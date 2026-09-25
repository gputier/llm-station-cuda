"""Generate day-to-day launch profiles from the bench's own configs.

A bench config never bakes sampling into its args: the bench's gateway
(benchrun.gateway) imposes sampling and chat_template_kwargs on every request,
per BenchConfig.sampling and BenchConfig.chat_template_kwargs (config.py). Day
to day there is no gateway in front of llama-server, so whatever the gateway
would have sent has to become server flags instead, or the server falls back
to its own defaults (llama-server's own, not the model card's, see config.py).

One profile file per (machine, model, variant) config, written as the same
JSON shape as a bench launch spec (config.render_launch_spec): name, exe,
workDir, cudaBin, args, env. The name is "<model>-r<N>" rather than the bench's
"bench-<model>-<variant>", so llm-ctl.ps1's existing --alias injection (already
done for every profile in Start-LLM) serves it under that short name instead.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import BenchConfig, ConfigError, load_config, render_launch_spec

VARIANT_ALIAS = {"R1": "r1", "R2": "r2", "R3": "r3"}

# Sampling key (as a bench config writes it) -> llama-server sampling flag.
# Every flag on the right is one of config.SAMPLING_FLAGS, read from
# llama-server --help at build b10826 on the 99 on 2026-09-23 (see there).
# 'repeat_penalty' and not 'repetition_penalty': config.load_config already
# rejects the latter (SERVER_SAMPLING_NAMES), so a config can only ever carry
# the flag's own name here.
SAMPLING_TO_FLAG = {
    "temperature": "--temp",
    "top_p": "--top-p",
    "top_k": "--top-k",
    "min_p": "--min-p",
    "presence_penalty": "--presence-penalty",
    "repeat_penalty": "--repeat-penalty",
    "frequency_penalty": "--frequency-penalty",
}

# max_tokens is required in every config's sampling (config.REQUIRED_SAMPLING),
# but is not itself a sampling flag: it is the number of tokens the gateway's
# caller (a bench suite) asks for per request, and with no gateway in front of
# the server it becomes the SERVER's own default when a client's request sets
# no max_tokens at all, "-n, --predict, --n-predict N" in llama-server's CLI
# (common/common.cpp). Not re-verified against a live --help for this change:
# no station was touched, see the report for that gap.
MAX_TOKENS_FLAG = "-n"

CHAT_TEMPLATE_KWARGS_FLAG = "--chat-template-kwargs"


def profile_name(cfg: BenchConfig) -> str:
    return f"{cfg.model}-{VARIANT_ALIAS[cfg.variant]}"


def sampling_flags(cfg: BenchConfig) -> list[str]:
    """Sampling and chat_template_kwargs as llama-server flags, in a fixed,
    deterministic order (dict order of SAMPLING_TO_FLAG, then max_tokens, then
    chat_template_kwargs) so the same config always renders the same args."""
    flags: list[str] = []
    seen: set[str] = set()
    sampling = dict(cfg.sampling)
    max_tokens = sampling.pop("max_tokens")
    for key, flag in SAMPLING_TO_FLAG.items():
        if key not in sampling:
            continue
        if flag in seen:
            raise ConfigError(f"{cfg.model}/{cfg.variant}: sampling flag {flag} generated twice")
        seen.add(flag)
        flags += [flag, str(sampling.pop(key))]
    if sampling:
        raise ConfigError(f"{cfg.model}/{cfg.variant}: no server flag known for sampling key(s) {sorted(sampling)}")
    flags += [MAX_TOKENS_FLAG, str(max_tokens)]
    if cfg.chat_template_kwargs:
        flags += [CHAT_TEMPLATE_KWARGS_FLAG, json.dumps(cfg.chat_template_kwargs, separators=(",", ":"))]
    return flags


def build_profile(cfg: BenchConfig) -> dict:
    """A launch spec (config.render_launch_spec's shape) named for daily use,
    its args extended with the flags the bench's gateway would otherwise have
    sent per request."""
    spec = render_launch_spec(cfg)
    spec["name"] = profile_name(cfg)
    spec["args"] = list(cfg.args) + sampling_flags(cfg)
    return spec


def iter_configs(machine: str, configs_dir: Path):
    machine_dir = configs_dir / machine
    if not machine_dir.is_dir():
        raise ConfigError(f"no configs directory for machine {machine!r}: {machine_dir}")
    for model_dir in sorted(p for p in machine_dir.iterdir() if p.is_dir()):
        for variant_file in sorted(model_dir.glob("R*.yaml")):
            yield load_config(variant_file)


def write_profiles(machine: str, out_dir: Path, configs_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    seen_names: set[str] = set()
    for cfg in iter_configs(machine, configs_dir):
        spec = build_profile(cfg)
        if spec["name"] in seen_names:
            raise ConfigError(f"duplicate profile name {spec['name']!r} for machine {machine!r}")
        seen_names.add(spec["name"])
        path = out_dir / f"{spec['name']}.json"
        path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


def add_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("profiles", help="write one launch-profile JSON per bench config of one machine")
    p.add_argument("--machine", required=True, choices=("99", "97"))
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--configs", default=None, type=Path,
                    help="configs root, default the bench/configs next to this module")


def run(args: argparse.Namespace) -> None:
    configs_dir = args.configs or (Path(__file__).resolve().parents[1] / "configs")
    for path in write_profiles(args.machine, args.out, configs_dir):
        print(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m benchrun profiles")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_subparser(sub)
    args = ap.parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
