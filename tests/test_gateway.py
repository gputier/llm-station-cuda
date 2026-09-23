import asyncio
import json
from aiohttp import web
from benchrun.gateway import make_app


async def fake_upstream(request):
    # Append-only: assigning into request.app after the app has started is
    # deprecated in aiohttp, so headers and bodies seen are collected into
    # lists set up before start instead of written as fresh app keys.
    request.app["headers_seen"].append(dict(request.headers))
    body = await request.json()
    request.app["seen"].append(body)
    if body.get("stream"):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
        await resp.write(b'data: {"choices":[{"delta":{}}],"timings":{"predicted_per_second":42.0,"draft_n":10,"draft_n_accepted":7}}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp
    return web.json_response({
        "choices": [{"message": {"content": "ok", "reasoning_content": "abc"}}],
        "usage": {"completion_tokens": 3},
        "timings": {"predicted_per_second": 50.0, "prompt_per_second": 900.0},
    })


async def start(aiohttp_client, tmp_path):
    up = web.Application()
    up["seen"] = []
    up["headers_seen"] = []
    up.router.add_post("/v1/chat/completions", fake_upstream)
    up_client = await aiohttp_client(up)
    journal = tmp_path / "journal.jsonl"
    gw = await aiohttp_client(make_app(str(up_client.make_url("")).rstrip("/"), str(journal)))
    ctx = {"run_id": "r1", "suite": "lcb", "rep": 1,
           "sampling": {"temperature": 0.6, "top_k": 20}, "chat_template_kwargs": {"reasoning_effort": "medium"}}
    assert (await gw.post("/_bench/context", json=ctx)).status == 200
    return gw, up, journal


async def test_gateway_overrides_harness_sampling(aiohttp_client, tmp_path):
    gw, up, _ = await start(aiohttp_client, tmp_path)
    await gw.post("/v1/chat/completions", json={"messages": [], "temperature": 0.0, "top_k": 1})
    sent = up["seen"][-1]
    assert sent["temperature"] == 0.6 and sent["top_k"] == 20
    assert sent["chat_template_kwargs"]["reasoning_effort"] == "medium"


async def test_gateway_journals_timings_and_reasoning(aiohttp_client, tmp_path):
    gw, _, journal = await start(aiohttp_client, tmp_path)
    r = await gw.post("/v1/chat/completions", json={"messages": []})
    assert (await r.json())["choices"][0]["message"]["content"] == "ok"
    rec = json.loads(journal.read_text().splitlines()[-1])
    assert rec["run_id"] == "r1" and rec["rep"] == 1
    assert rec["timings"]["predicted_per_second"] == 50.0
    assert rec["reasoning_chars"] == 3


async def test_gateway_streams_bytes_unchanged(aiohttp_client, tmp_path):
    gw, _, journal = await start(aiohttp_client, tmp_path)
    r = await gw.post("/v1/chat/completions", json={"messages": [], "stream": True})
    raw = await r.read()
    assert raw.endswith(b"data: [DONE]\n\n") and b'"content":"hi"' in raw
    rec = json.loads(journal.read_text().splitlines()[-1])
    assert rec["timings"]["draft_n_accepted"] == 7
    assert rec["ttft_s"] is not None


async def test_gateway_refuses_without_context(aiohttp_client, tmp_path):
    gw, _, _ = await start(aiohttp_client, tmp_path)
    await gw.delete("/_bench/context")
    r = await gw.post("/v1/chat/completions", json={"messages": []})
    assert r.status == 409


async def test_gateway_strips_hop_by_hop_and_encoding_headers(aiohttp_client, tmp_path):
    gw, up, _ = await start(aiohttp_client, tmp_path)
    await gw.post("/v1/chat/completions", json={"messages": []},
                   headers={"Connection": "close", "Accept-Encoding": "gzip"})
    seen = {k.lower(): v for k, v in up["headers_seen"][-1].items()}
    assert "connection" not in seen
    # aiohttp's own client session sets its own Accept-Encoding for its
    # auto-decompression: what must not happen is the client's raw value
    # ("gzip" alone, sent to the gateway above) reaching upstream unchanged.
    assert seen.get("accept-encoding") != "gzip"


async def test_gateway_returns_502_and_journals_when_upstream_unreachable(aiohttp_client, tmp_path):
    journal = tmp_path / "journal.jsonl"
    # Nothing listens on this loopback port: the connection is refused before
    # any response is ever received, exercising the unreachable-upstream path.
    gw = await aiohttp_client(make_app("http://127.0.0.1:1", str(journal)))
    ctx = {"run_id": "r1", "suite": "lcb", "rep": 1,
           "sampling": {"temperature": 0.6}, "chat_template_kwargs": {}}
    assert (await gw.post("/_bench/context", json=ctx)).status == 200
    r = await gw.post("/v1/chat/completions", json={"messages": []})
    assert r.status == 502
    assert "error" in await r.json()
    rec = json.loads(journal.read_text().splitlines()[-1])
    assert rec["run_id"] == "r1" and rec["status"] is None
    assert rec["total_s"] is not None
    assert "error" in rec


async def test_gateway_relay_get_returns_502_when_upstream_unreachable(aiohttp_client, tmp_path):
    journal = tmp_path / "journal.jsonl"
    gw = await aiohttp_client(make_app("http://127.0.0.1:1", str(journal)))
    r = await gw.get("/health")
    assert r.status == 502
    assert "error" in await r.json()


async def fake_upstream_drops(request):
    # Sends a status line and one SSE chunk, like a real stream in progress,
    # then breaks the connection instead of finishing the response cleanly.
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    await resp.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
    raise ConnectionResetError("simulated upstream drop")


async def test_gateway_closes_stream_instead_of_hanging_when_upstream_drops_mid_response(aiohttp_client, tmp_path):
    up = web.Application()
    up.router.add_post("/v1/chat/completions", fake_upstream_drops)
    up_client = await aiohttp_client(up)
    journal = tmp_path / "journal.jsonl"
    gw = await aiohttp_client(make_app(str(up_client.make_url("")).rstrip("/"), str(journal)))
    ctx = {"run_id": "r1", "suite": "lcb", "rep": 1,
           "sampling": {"temperature": 0.6}, "chat_template_kwargs": {}}
    assert (await gw.post("/_bench/context", json=ctx)).status == 200
    # The bug this guards against is a hang: the client never gets a
    # terminating chunk and blocks on read() forever. Bound the read so a
    # regression fails the test instead of freezing the suite.
    r = await gw.post("/v1/chat/completions", json={"messages": [], "stream": True})
    raw = await asyncio.wait_for(r.read(), timeout=5)
    assert b'"content":"hi"' in raw
    rec = json.loads(journal.read_text().splitlines()[-1])
    assert rec["run_id"] == "r1" and "error" in rec
