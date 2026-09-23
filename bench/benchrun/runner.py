"""Campaign runner: configs x suites x reps, idempotent and resumable.

A configuration whose station start or suites fail is recorded in its own
meta.json and the campaign continues with the next configuration; the
failures are only raised, as a single CampaignError, once every configuration
has had its turn.

An operator interrupt (KeyboardInterrupt) or SystemExit also gets its cell's
meta.json written, marked with "interrupted", but is never counted as a
CampaignError failure: it stops the whole campaign immediately instead.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import time
import traceback
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

    def _record_cell(self, cell, cfg, started, vram_samples, error, error_tb,
                      stop_error, stop_error_tb, interrupted, interrupted_tb):
        # Writes meta.json (and error.txt, if there is anything to report) for
        # one cell. Called on both the normal and the interrupted exit path.
        # Returns the short failure summary for CampaignError, or None.
        vram = _peak_vram(vram_samples)
        meta = {"vram": vram, "spill": bool(vram) and vram["shared_mb"] > self.spill_limit_mb,
                "config": dataclasses.asdict(cfg), "started": started, "ended": time.time()}
        failure_parts = []
        tracebacks = []
        if error is not None:
            meta["error"] = f"{type(error).__name__}: {error}"
            failure_parts.append(meta["error"])
            tracebacks.append(f"--- error ---\n{error_tb}")
        if stop_error is not None:
            meta["stop_error"] = f"{type(stop_error).__name__}: {stop_error}"
            failure_parts.append(f"stop also failed: {meta['stop_error']}")
            tracebacks.append(f"--- stop_error ---\n{stop_error_tb}")
        if interrupted is not None:
            meta["interrupted"] = interrupted
            tracebacks.append(f"--- interrupted ---\n{interrupted_tb}")
        # meta["error"]/meta["stop_error"] stay short (class + message) so
        # meta.json is readable at a glance; full tracebacks go to error.txt.
        if tracebacks:
            (cell / "error.txt").write_text("\n".join(tracebacks), encoding="utf-8")
        (cell / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        return "; ".join(failure_parts) if failure_parts else None

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
            error_tb = None
            stop_error = None
            stop_error_tb = None
            try:
                try:
                    # A failed start() can still have launched the process:
                    # stop() below always runs regardless, so nothing is left
                    # running because of a start that failed on our end.
                    self.station.start(cfg)
                    self.station.warmup()
                    vram_samples.append(self.station.vram())
                    for suite, rep in todo:
                        out = cell / suite.name
                        out.mkdir(parents=True, exist_ok=True)
                        self._set_context(cfg, suite.name, rep)
                        ctx = SuiteContext(self.gateway_url, cfg, rep, out, self.private_root)
                        results = suite.run(ctx)
                        if not results:
                            raise RuntimeError(f"suite {suite.name} graded no item (rep {rep})")
                        with open(out / f"rep{rep}.jsonl", "w", encoding="utf-8") as fh:
                            for r in results:
                                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                        (out / f"rep{rep}.done").write_text("")
                        # Sampled after every pass, not only after warmup, so
                        # a spill that only appears mid-suite is not missed.
                        vram_samples.append(self.station.vram())
                except Exception as exc:  # noqa: BLE001 - recorded per config, campaign continues
                    error = exc
                    error_tb = traceback.format_exc()
                finally:
                    # Runs on every exit, Exception or BaseException alike, so
                    # the station is never left with a model loaded. Guarded
                    # on its own so a stop() failure never replaces an error
                    # already caught above.
                    try:
                        self.station.stop()
                    except Exception as exc:  # noqa: BLE001 - recorded per config, campaign continues
                        stop_error = exc
                        stop_error_tb = traceback.format_exc()
            except BaseException as exc:
                # KeyboardInterrupt / SystemExit: record this cell too, then
                # re-raise unchanged so the campaign stops right here instead
                # of continuing to the next configuration.
                self._record_cell(cell, cfg, started, vram_samples, error, error_tb,
                                   stop_error, stop_error_tb, type(exc).__name__,
                                   traceback.format_exc())
                raise
            # Reps that did not reach their .done marker stay pending and are
            # redone on the next run; this write does not affect that.
            failure = self._record_cell(cell, cfg, started, vram_samples, error, error_tb,
                                         stop_error, stop_error_tb, None, None)
            if failure is not None:
                failures.append((f"{cfg.machine}/{cfg.model}/{cfg.variant}", failure))
        if failures:
            detail = "; ".join(f"{cell}: {err}" for cell, err in failures)
            raise CampaignError(f"{len(failures)} configuration(s) failed: {detail}")
