import json
from aiohttp import web
from benchrun.gateway import make_app


async def fake_upstream(request):
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
