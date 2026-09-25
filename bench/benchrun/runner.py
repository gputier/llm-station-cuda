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


class SuiteSkipped(Exception):
    """Raised by a suite that does not apply to this configuration (every
    requested context length above the served window, for example). The rep
    is recorded as skipped with the reason, never as an empty success nor as
    a failure of the whole configuration."""


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
        "total_mb": max(s["total_mb"] for s in samples),
        "shared_mb": max(s["shared_mb"] for s in samples),
        "samples": len(samples),
    }


# llama-server keeps gigabytes of pinned host buffers that Windows counts as
# the process's shared GPU memory even with the card half empty (measured
# 2026-09-25: bonsai R1 on the 99, 20530 of 32607 MiB used, 1611 MiB shared).
# A spill is the driver falling back to system memory, which only happens
# once the dedicated memory is full: both conditions are required.
SPILL_HEADROOM_MB = 512


def _is_spill(vram: dict | None, shared_limit_mb: int) -> bool:
    return (bool(vram) and vram["shared_mb"] > shared_limit_mb
            and vram["used_mb"] >= vram["total_mb"] - SPILL_HEADROOM_MB)


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

    def _set_context(self, cfg: BenchConfig, suite, rep: int) -> None:
        # A suite may pin sampling keys its measure depends on (the speed
        # suite pins max_tokens), laid over the configuration's own values.
        sampling = {**cfg.sampling, **getattr(suite, "sampling_override", {})}
        body = json.dumps({"run_id": f"{cfg.machine}/{cfg.model}/{cfg.variant}", "suite": suite.name,
                           "rep": rep, "sampling": sampling,
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
        meta = {"vram": vram, "spill": _is_spill(vram, self.spill_limit_mb),
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
                    suite_failures: list[str] = []
                    for suite, rep in todo:
                        out = cell / suite.name
                        out.mkdir(parents=True, exist_ok=True)
                        self._set_context(cfg, suite, rep)
                        ctx = SuiteContext(self.gateway_url, cfg, rep, out, self.private_root)
                        try:
                            results = suite.run(ctx)
                            if not results:
                                raise RuntimeError(f"suite {suite.name} graded no item (rep {rep})")
                        except SuiteSkipped as skip:
                            (out / f"rep{rep}.skipped").write_text(str(skip), encoding="utf-8")
                            (out / f"rep{rep}.done").write_text("")
                            continue
                        except Exception as exc:  # noqa: BLE001 - one suite/rep's own
                            # failure (a timed-out subprocess, a harness that
                            # crashed on this rep, ...) must not stop every
                            # OTHER suite of this profile from getting its own
                            # chance to run: recorded here, without a .done
                            # marker so it is retried on the next launch, and
                            # the loop moves on to the next (suite, rep). The
                            # cell as a whole is still marked failed below
                            # (suite_failures raises once every suite has had
                            # its turn), same as any other config failure.
                            (out / f"rep{rep}.error").write_text(
                                f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}", encoding="utf-8")
                            suite_failures.append(f"{suite.name} rep{rep}: {type(exc).__name__}: {exc}")
                            continue
                        with open(out / f"rep{rep}.jsonl", "w", encoding="utf-8") as fh:
                            for r in results:
                                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                        (out / f"rep{rep}.done").write_text("")
                        # Sampled after every pass, not only after warmup, so
                        # a spill that only appears mid-suite is not missed.
                        vram_samples.append(self.station.vram())
                    if suite_failures:
                        raise RuntimeError(f"{len(suite_failures)} suite/rep failure(s): " + "; ".join(suite_failures))
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
