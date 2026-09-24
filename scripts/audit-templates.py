"""Phase 0, template audit (task 14, plan step 2).

For every R1 configuration of one machine: start it through the bench action
at a small context window, read the served chat template from /props, and
record whether it contains a tool-call opening tag glued to the function name
with no newline in between, a known trigger of llama.cpp issue #29295. The
window is forced to 4096 only for this probe, never for the configuration's
own context: a fresh copy of args is built per profile, the config file on
disk is never touched.

Usage: python scripts/audit-templates.py <machine> [--ssh SSH_TARGET]
Prints one Markdown table row per profile to stdout; the caller appends the
table to docs/phase0-2026-09.md.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import sys
import urllib.request

from benchrun.config import load_config
from benchrun.station import Station

ROOT = pathlib.Path(__file__).parents[1]
CTX_PROBE = "4096"
# The exact defect from llama.cpp issue #29295: the model opens a tool call
# with no newline between the tag and the function name, which the
# streaming parser misreads as plain text on some builds.
NO_NEWLINE_PATTERN = re.compile(r"<tool_call><function=")


def probe_args(args: list[str]) -> list[str]:
    out = list(args)
    for i, a in enumerate(out):
        if a == "--ctx-size" and i + 1 < len(out):
            out[i + 1] = CTX_PROBE
            return out
    return out + ["--ctx-size", CTX_PROBE]


def audit_one(station: Station, cfg) -> dict:
    probed = dataclasses.replace(cfg, args=probe_args(cfg.args))
    station.start(probed, timeout_s=900)
    try:
        with urllib.request.urlopen(station.base_url + "/props", timeout=30) as r:
            props = json.load(r)
    finally:
        station.stop()
    template = props.get("chat_template", "") or ""
    return {
        "model": cfg.model,
        "has_tool_call": "<tool_call>" in template,
        "has_function_tag": "<function=" in template,
        "has_parameter_tag": "<parameter=" in template,
        "no_newline_glued": bool(NO_NEWLINE_PATTERN.search(template)),
    }


def render_row(r: dict) -> str:
    def mark(v: bool) -> str:
        return "yes" if v else "no"
    return (f"| {r['model']} | {mark(r['has_tool_call'])} | {mark(r['has_function_tag'])} "
            f"| {mark(r['has_parameter_tag'])} | {mark(r['no_newline_glued'])} |")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("machine", choices=["99", "97"])
    parser.add_argument("--ssh", required=True, help="user@host of the station")
    parser.add_argument("--base-url", required=True, help="http://host:8080")
    parser.add_argument("--ctl-path", default=r"D:\LLM-Setup\llm-ctl.ps1")
    args = parser.parse_args(argv)

    station = Station(args.ssh, args.base_url, args.ctl_path)
    configs = sorted((ROOT / "configs" / args.machine).glob("*/R1.yaml"))
    if not configs:
        print(f"NO_CONFIGS for machine {args.machine}", file=sys.stderr)
        return 2

    print("| profil | tool_call | function= | parameter= | collé sans retour à la ligne |")
    print("| --- | --- | --- | --- | --- |")
    for path in configs:
        cfg = load_config(path)
        row = audit_one(station, cfg)
        print(render_row(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
