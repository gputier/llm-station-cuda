"""Campaign runner: configs x suites x reps, idempotent and resumable."""
from __future__ import annotations

import dataclasses
import json
import pathlib
import time
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .config import BenchConfig


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
        for cfg in configs:
            todo = self._pending(cfg)
            if not todo:
                continue
            cell = self._cell(cfg)
            cell.mkdir(parents=True, exist_ok=True)
            started = time.time()
            self.station.start(cfg)
            vram = None
            error = None
            try:
                self.station.warmup()
                vram = self.station.vram()
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
            except Exception as exc:  # noqa: BLE001 - recorded in meta.json, then re-raised
                error = exc
            finally:
                self.station.stop()
            # meta.json is written whether the pass succeeded or a suite raised:
            # the station is already stopped above, and a failed pass still
            # needs its vram reading and timing on disk for the post-mortem.
            # Reps that did not reach their .done marker stay pending and are
            # redone on the next run, unaffected by this write.
            meta = {"vram": vram, "spill": bool(vram) and vram["shared_mb"] > self.spill_limit_mb,
                    "config": dataclasses.asdict(cfg), "started": started, "ended": time.time()}
            if error is not None:
                meta["error"] = f"{type(error).__name__}: {error}"
            (cell / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            if error is not None:
                raise error
