"""Phase 0, reproduction of llama.cpp issue #29295 (task 14, plan step 3).

Sends the same tool-call request to 'qwen' on the 99, five times with a fresh
server restart between each pass (cold cache) and five times against one
already-warm server (warm cache, cache_prompt left at its default, on). Counts
the responses that come back without a tool_calls entry despite
tool_choice: "required". If the warm run fails more often than the cold one,
this is evidence for the issue, and every configuration's sampling block
needs "cache_prompt": false, sourced "issue llama.cpp #29295", enforced by
the gateway.

Usage: python scripts/repro-29295.py --ssh SSH_TARGET --base-url URL
Prints a summary line and one row per pass to stdout.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

from benchrun.config import load_config
from benchrun.station import Station

ROOT = pathlib.Path(__file__).parents[1]

TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}

# An edge case on purpose: a question answerable without the tool, so a model
# that avoids the tool call has an easy way out, which is exactly what makes
# a missing tool_calls entry meaningful rather than a forced artifact.
MESSAGES = [
    {"role": "user", "content": "What is the weather like in Paris right now? "
                                 "If you are unsure, call the weather tool."},
]


def chat_request(base_url: str, cache_prompt: bool) -> dict:
    body = json.dumps({
        "messages": MESSAGES,
        "tools": [TOOL],
        "tool_choice": "required",
        "temperature": 0.0,
        "max_tokens": 256,
        "cache_prompt": cache_prompt,
    }).encode()
    req = urllib.request.Request(
        base_url + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def has_tool_call(resp: dict) -> bool:
    try:
        msg = resp["choices"][0]["message"]
    except (KeyError, IndexError):
        return False
    return bool(msg.get("tool_calls"))


def run_cold(station: Station, cfg, n: int) -> list[bool]:
    results = []
    for i in range(n):
        station.start(cfg, timeout_s=900)
        try:
            resp = chat_request(station.base_url, cache_prompt=True)
            ok = has_tool_call(resp)
        finally:
            station.stop()
        results.append(ok)
        print(f"cold pass {i + 1}/{n}: tool_calls={'yes' if ok else 'NO'}")
    return results


def run_warm(station: Station, cfg, n: int) -> list[bool]:
    station.start(cfg, timeout_s=900)
    try:
        results = []
        for i in range(n):
            resp = chat_request(station.base_url, cache_prompt=True)
            ok = has_tool_call(resp)
            results.append(ok)
            print(f"warm pass {i + 1}/{n}: tool_calls={'yes' if ok else 'NO'}")
        return results
    finally:
        station.stop()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ctl-path", default=r"D:\LLM-Setup\llm-ctl.ps1")
    parser.add_argument("--passes", type=int, default=5)
    args = parser.parse_args(argv)

    cfg = load_config(ROOT / "configs" / "99" / "qwen" / "R1.yaml")
    station = Station(args.ssh, args.base_url, args.ctl_path)

    cold = run_cold(station, cfg, args.passes)
    warm = run_warm(station, cfg, args.passes)

    cold_fail = cold.count(False)
    warm_fail = warm.count(False)
    print(f"SUMMARY cold_fail={cold_fail}/{len(cold)} warm_fail={warm_fail}/{len(warm)}")
    if warm_fail > cold_fail:
        print("VERDICT warm cache fails more often: cache_prompt:false required, "
              "source 'issue llama.cpp #29295'")
    else:
        print("VERDICT no evidence that warm cache fails more often than cold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
