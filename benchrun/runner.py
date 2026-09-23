"""Campaign runner: configs x suites x reps, idempotent and resumable.

A configuration whose station start or suites fail is recorded in its own
meta.json and the campaign continues with the next configuration; the
failures are only raised, as a single CampaignError, once every configuration
has had its turn.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import time
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .config import BenchConfig


class CampaignError(Exception):
    """Raised after a full pass over all configurations if any of them failed.

    Each configuration that failed already has its error recorded in its own
    meta.json; this exception only surfaces the summary to the caller once
    nothing is left to run.
    """


@dataclass
class SuiteContext:
    base_url: str
    cfg: BenchConfig
    rep: int
    out_dir: pathlib.Path
    private_root: pathlib.Path


class Suite(Protocol):
    name: str

    def run(self, ctx: SuiteContext) -> list[dict]: ...


def _peak_vram(samples: list[dict]) -> dict | None:
    if not samples:
        return None
    return {
        "used_mb": max(s["used_mb"] for s in samples),
        "shared_mb": max(s["shared_mb"] for s in samples),
        "samples": len(samples),
    }


class Campaign:
    def __init__(self, station, gateway_url: str, suites: list, out_root: pathlib.Path,
                 reps: int = 3, spill_limit_mb: int = 256, private_root: pathlib.Path | None = None):
        self.station, self.gateway_url, self.suites = station, gateway_url.rstrip("/"), suites
        self.out_root, self.reps, self.spill_limit_mb = pathlib.Path(out_root), reps, spill_limit_mb
        self.private_root = pathlib.Path(private_root or out_root)

    def _cell(self, cfg: BenchConfig) -> pathlib.Path:
        return self.out_root / cfg.machine / cfg.model / cfg.variant

    def _pending(self, cfg: BenchConfig) -> list[tuple]:
        todo = []
        for suite in self.suites:
            for rep in range(1, self.reps + 1):
                if not (self._cell(cfg) / suite.name / f"rep{rep}.done").exists():
                    todo.append((suite, rep))
        return todo

    def _set_context(self, cfg: BenchConfig, suite_name: str, rep: int) -> None:
        body = json.dumps({"run_id": f"{cfg.machine}/{cfg.model}/{cfg.variant}", "suite": suite_name,
                           "rep": rep, "sampling": cfg.sampling,
                           "chat_template_kwargs": cfg.chat_template_kwargs}).encode()
        req = urllib.request.Request(self.gateway_url + "/_bench/context", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10).read()

    def run(self, configs: list[BenchConfig]) -> None:
        failures = []
        for cfg in configs:
            todo = self._pending(cfg)
            if not todo:
                continue
            cell = self._cell(cfg)
            cell.mkdir(parents=True, exist_ok=True)
            started = time.time()
            vram_samples: list[dict] = []
            error = None
            try:
                # A failed start() can still have launched the process on the
                # station before raising (e.g. a health-check timeout): stop()
                # runs in the finally below regardless, so nothing is ever
                # left running because of a start that failed on our end.
                self.station.start(cfg)
                self.station.warmup()
                vram_samples.append(self.station.vram())
                for suite, rep in todo:
                    out = cell / suite.name
                    out.mkdir(parents=True, exist_ok=True)
                    self._set_context(cfg, suite.name, rep)
                    ctx = SuiteContext(self.gateway_url, cfg, rep, out, self.private_root)
                    results = suite.run(ctx)
                    with open(out / f"rep{rep}.jsonl", "w", encoding="utf-8") as fh:
                        for r in results:
                            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                    (out / f"rep{rep}.done").write_text("")
                    # Sampled again after every pass, not only after warmup:
                    # a spill that only shows up once the context has grown
                    # during a suite would otherwise never be seen.
                    vram_samples.append(self.station.vram())
            except Exception as exc:  # noqa: BLE001 - recorded per config, campaign continues
                error = exc
            finally:
                self.station.stop()
            # meta.json is written whether the pass succeeded or failed: the
            # station is already stopped above, and a failed pass still needs
            # its vram reading and timing on disk for the post-mortem. Reps
            # that did not reach their .done marker stay pending and are
            # redone on the next run, unaffected by this write.
            vram = _peak_vram(vram_samples)
            meta = {"vram": vram, "spill": bool(vram) and vram["shared_mb"] > self.spill_limit_mb,
                    "config": dataclasses.asdict(cfg), "started": started, "ended": time.time()}
            if error is not None:
                meta["error"] = f"{type(error).__name__}: {error}"
                failures.append((f"{cfg.machine}/{cfg.model}/{cfg.variant}", meta["error"]))
            (cell / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        if failures:
            detail = "; ".join(f"{cell}: {err}" for cell, err in failures)
            raise CampaignError(f"{len(failures)} configuration(s) failed: {detail}")
