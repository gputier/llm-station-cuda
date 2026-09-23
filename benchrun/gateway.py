"""Measurement gateway between the harnesses and llama-server.

Every harness request goes through here. The gateway overwrites sampling with
the values of the configuration under test (harnesses ship their own defaults,
often temperature 0), relays the answer byte for byte, and journals timings.
"""
from __future__ import annotations

import asyncio
import json
import time

import aiohttp
from aiohttp import web

RELAYED_POSTS = ("/v1/chat/completions", "/v1/completions", "/v1/messages")

# Hop-by-hop headers are per-connection, not per-request: forwarding them lets
# the harness's "Connection: close" tear down the gateway's pooled connection
# to upstream, folding a fresh TCP connect into ttft_s and total_s. Content
# length changes once sampling is overwritten, and accept-encoding is dropped
# so upstream never compresses a body the gateway re-emits uncompressed.
STRIPPED_HEADERS = {
    "host", "content-length", "connection", "keep-alive", "transfer-encoding",
    "upgrade", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "accept-encoding",
}


def _apply(body: dict, ctx: dict, path: str) -> dict:
    body = dict(body)
    for key, value in ctx["sampling"].items():
        body[key] = value
    if ctx.get("chat_template_kwargs") and path != "/v1/messages":
        merged = dict(body.get("chat_template_kwargs") or {})
        merged.update(ctx["chat_template_kwargs"])
        body["chat_template_kwargs"] = merged
    if body.get("stream") and path != "/v1/messages":
        opts = dict(body.get("stream_options") or {})
        opts["include_usage"] = True
        body["stream_options"] = opts
    return body


def _reasoning_chars(payload: dict) -> int:
    try:
        return len(payload["choices"][0]["message"].get("reasoning_content") or "")
    except (KeyError, IndexError, TypeError):
        return 0


def _scan_sse(buffer: bytes, state: dict) -> None:
    for line in buffer.split(b"\n"):
        if not line.startswith(b"data: ") or line == b"data: [DONE]":
            continue
        try:
            evt = json.loads(line[6:])
        except ValueError:
            continue
        if "timings" in evt:
            state["timings"] = evt["timings"]
        if evt.get("usage"):
            state["usage"] = evt["usage"]
        for choice in evt.get("choices") or []:
            state["reasoning_chars"] += len((choice.get("delta") or {}).get("reasoning_content") or "")


# The bench context is a mutable holder stored before startup: handlers change
# its content, never the Application mapping, which aiohttp freezes once started.
BENCH_CTX = web.AppKey("bench_ctx", dict)
SESSION = web.AppKey("session", aiohttp.ClientSession)
JOURNAL = web.AppKey("journal", object)


def make_app(upstream: str, journal_path: str) -> web.Application:
    app = web.Application(client_max_size=256 * 1024 * 1024)
    app[BENCH_CTX] = {"ctx": None}

    async def resources(app: web.Application):
        # One pooled session for the gateway's lifetime: a session per request
        # would fold a TCP connect into every measured ttft_s and total_s.
        timeout = aiohttp.ClientTimeout(total=None, sock_read=3600)
        app[SESSION] = aiohttp.ClientSession(timeout=timeout)
        app[JOURNAL] = open(journal_path, "a", encoding="utf-8", buffering=1)
        yield
        await app[SESSION].close()
        app[JOURNAL].close()

    app.cleanup_ctx.append(resources)

    async def set_ctx(request):
        request.app[BENCH_CTX]["ctx"] = await request.json()
        return web.json_response({"ok": True})

    async def clear_ctx(request):
        request.app[BENCH_CTX]["ctx"] = None
        return web.json_response({"ok": True})

    async def relay_post(request):
        ctx = request.app[BENCH_CTX]["ctx"]
        if ctx is None:
            return web.json_response({"error": "no bench context set"}, status=409)
        path = request.path
        body = _apply(await request.json(), ctx, path)
        rec = {"run_id": ctx["run_id"], "suite": ctx["suite"], "rep": ctx["rep"], "path": path,
               "t_start": time.time(), "ttft_s": None, "total_s": None, "status": None,
               "request_sampling": {k: body.get(k) for k in ctx["sampling"]},
               # llama-server's /v1/messages (Anthropic shape) returns no
               # "timings" field: for Anthropic-shaped passes this stays null
               # and decode rate is read from the server's own log instead.
               "timings": None, "usage": None, "reasoning_chars": 0}
        headers = {k: v for k, v in request.headers.items() if k.lower() not in STRIPPED_HEADERS}
        t0 = time.monotonic()
        try:
            async with request.app[SESSION].post(upstream + path, json=body, headers=headers) as up:
                rec["status"] = up.status
                if body.get("stream"):
                    resp = web.StreamResponse(status=up.status, headers={
                        "Content-Type": up.headers.get("Content-Type", "text/event-stream")})
                    await resp.prepare(request)
                    tail = b""
                    async for chunk in up.content.iter_any():
                        if rec["ttft_s"] is None:
                            rec["ttft_s"] = time.monotonic() - t0
                        await resp.write(chunk)
                        tail += chunk
                        cut = tail.rfind(b"\n\n")
                        if cut >= 0:
                            _scan_sse(tail[:cut], rec)
                            tail = tail[cut + 2:]
                    _scan_sse(tail, rec)
                    await resp.write_eof()
                else:
                    raw = await up.read()
                    rec["ttft_s"] = time.monotonic() - t0
                    try:
                        payload = json.loads(raw)
                        rec["timings"] = payload.get("timings")
                        rec["usage"] = payload.get("usage")
                        rec["reasoning_chars"] = _reasoning_chars(payload)
                    except ValueError:
                        pass
                    resp = web.Response(status=up.status, body=raw, content_type=up.content_type)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"
            rec["total_s"] = time.monotonic() - t0
            request.app[JOURNAL].write(json.dumps(rec, ensure_ascii=False) + "\n")
            return web.json_response({"error": rec["error"]}, status=502)
        rec["total_s"] = time.monotonic() - t0
        request.app[JOURNAL].write(json.dumps(rec, ensure_ascii=False) + "\n")
        return resp

    async def relay_get(request):
        try:
            async with request.app[SESSION].get(upstream + request.path_qs) as up:
                return web.Response(status=up.status, body=await up.read(), content_type=up.content_type)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            return web.json_response({"error": f"{type(exc).__name__}: {exc}"}, status=502)

    app.router.add_post("/_bench/context", set_ctx)
    app.router.add_delete("/_bench/context", clear_ctx)
    for p in RELAYED_POSTS:
        app.router.add_post(p, relay_post)
    app.router.add_get("/{tail:.*}", relay_get)
    return app


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--journal", required=True)
    ap.add_argument("--port", type=int, default=8081)
    a = ap.parse_args()
    web.run_app(make_app(a.upstream.rstrip("/"), a.journal), port=a.port)


if __name__ == "__main__":
    main()
