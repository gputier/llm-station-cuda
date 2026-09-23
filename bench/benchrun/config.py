"""Bench configuration: one YAML file per model x variant x machine."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

VARIANTS = {"R1", "R2", "R3"}
# machine, model and variant end up as path segments (Campaign._cell) and as
# literal values interpolated into PowerShell single-quoted strings (Station).
# Restricting them to a safe character set closes both doors at once, rather
# than escaping at every point of use. No underscore: BFCL reads "_" in a
# model name as an escaped "/" (eval_runner.runner, ast_checker), so a served
# alias holding one fails its registry lookup at evaluation.
FIELD_NAME_RE = re.compile(r"^[A-Za-z0-9.-]+$")
# Without both flags llama-server binds to loopback only, which the gateway
# (running elsewhere, or in another container) then cannot reach.
REQUIRED_ARG_FLAGS = ("--host", "--port")
# Sampling is enforced per request by the gateway, never baked into the server
# command line: a flag here would silently disagree with what the gateway sends.
# Every flag and alias of the "sampling params" section of llama-server --help
# at b10826, read on the 99 on 2026-09-23.
SAMPLING_FLAGS = {
    "--samplers", "-s", "--seed", "--sampler-seq", "--sampling-seq", "--ignore-eos",
    "--temp", "--temperature", "--top-k", "--top-p", "--min-p", "--top-nsigma",
    "--top-n-sigma", "--xtc-probability", "--xtc-threshold", "--typical", "--typical-p",
    "--repeat-last-n", "--repeat-penalty", "--presence-penalty", "--frequency-penalty",
    "--dry-multiplier", "--dry-base", "--dry-allowed-length", "--dry-penalty-last-n",
    "--dry-sequence-breaker", "--adaptive-target", "--adaptive-decay", "--dynatemp-range",
    "--dynatemp-exp", "--mirostat", "--mirostat-lr", "--mirostat-ent", "-l", "--logit-bias",
    "--grammar", "--grammar-file", "-j", "--json-schema", "-jf", "--json-schema-file",
    "-bs", "--backend-sampling",
}
REQUIRED = ("machine", "model", "variant", "label", "exe", "workdir", "args", "sampling", "sources")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class BenchConfig:
    machine: str
    model: str
    variant: str
    label: str
    exe: str
    workdir: str
    args: list[str]
    sampling: dict
    sources: dict
    cudabin: str | None = None
    env: dict = field(default_factory=dict)
    chat_template_kwargs: dict = field(default_factory=dict)
    ctx_proven: int | None = None


def load_config(path) -> BenchConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise ConfigError(f"{path}: missing keys {missing}")
    for field_name in ("machine", "model", "variant"):
        value = str(data[field_name])
        if not FIELD_NAME_RE.match(value):
            raise ConfigError(f"{path}: {field_name} must match {FIELD_NAME_RE.pattern!r}, got {value!r}")
    if data["variant"] not in VARIANTS:
        raise ConfigError(f"{path}: variant must be one of {sorted(VARIANTS)}")
    for flag in REQUIRED_ARG_FLAGS:
        if flag not in data["args"]:
            raise ConfigError(f"{path}: args must include '{flag}' or llama-server binds to loopback only")
    for flag in data["args"]:
        if flag in SAMPLING_FLAGS:
            raise ConfigError(f"{path}: {flag} belongs in 'sampling', not in 'args'")
    for key in list(data["sampling"]) + list(data.get("chat_template_kwargs") or {}):
        if not data["sources"].get(key):
            raise ConfigError(f"{path}: setting '{key}' has no source")
    return BenchConfig(
        machine=str(data["machine"]), model=data["model"], variant=data["variant"],
        label=data["label"], exe=data["exe"], workdir=data["workdir"],
        args=[str(a) for a in data["args"]], sampling=data["sampling"],
        sources=data["sources"], cudabin=data.get("cudabin"), env=data.get("env") or {},
        chat_template_kwargs=data.get("chat_template_kwargs") or {},
        ctx_proven=data.get("ctx_proven"),
    )


def served_alias(cfg: BenchConfig) -> str:
    # Name of the launch spec on the station, and model name the harnesses send.
    return f"bench-{cfg.model}-{cfg.variant}"


def render_launch_spec(cfg: BenchConfig) -> dict:
    return {
        "name": served_alias(cfg),
        "exe": cfg.exe,
        "workDir": cfg.workdir,
        "cudaBin": cfg.cudabin,
        "args": list(cfg.args),
        "env": dict(cfg.env),
    }
