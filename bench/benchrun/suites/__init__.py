"""Suite adapters. Each module exposes one class implementing benchrun.runner.Suite."""
from __future__ import annotations

import json
import pathlib

from benchrun.runner import SuiteSkipped

# User-defined docker network shared by the gateway and the harness
# containers, created once by whoever starts the gateway.
NETWORK = "bench-net"
# Named volume holding the Hugging Face cache of the harness datasets (about
# 9 GB for LiveCodeBench release_v6), filled on first use and reused by every
# run.
HF_CACHE_VOLUME = "bench-hf-cache"

# Measured directly from the "timings" field every llama-server response
# carries, scanned across every real gateway journal on disk (every suite,
# every model run so far): the slowest real decode is 135.98 tokens/second
# and the slowest real prefill is 98.96 tokens/second (both on nex). Both
# floors are set well under that as an explicit margin for a slower
# configuration never measured; SAFETY_FACTOR adds a small margin on top of
# the floor estimate itself so a slow-but-progressing run is not killed by
# the timeout alone.
PREFILL_FLOOR_TOKENS_PER_SECOND = 50.0
DECODE_FLOOR_TOKENS_PER_SECOND = 50.0
SAFETY_FACTOR = 1.3


def per_request_seconds(prompt_tokens: float, max_tokens: int) -> float:
    """Estimated wall time for one request: prefill cost for the prompt plus
    decode cost for the generation budget, computed separately since they pay
    for different work."""
    return prompt_tokens / PREFILL_FLOOR_TOKENS_PER_SECOND + max(max_tokens, 1) / DECODE_FLOOR_TOKENS_PER_SECOND


def request_timeout_s(max_tokens: int, prompt_tokens: float) -> int:
    """Client-side timeout for ONE request against the served model: the
    full prefill+decode budget for the given prompt and generation length,
    with the shared safety margin. Every suite adapter (docker run/exec
    -e BENCH_REQUEST_TIMEOUT_S) and every harness launcher or sitecustomize
    patch that configures a model client's own timeout computes it from this
    single function, never a local copy: a client-side delay shorter than
    this abandons a request the server is still legitimately working on,
    which is exactly the defect this function exists to close (proven live
    on LiveCodeBench, 2026-09-24)."""
    return int(per_request_seconds(prompt_tokens, max_tokens) * SAFETY_FACTOR)


def max_ctx(cfg) -> int:
    try:
        idx = cfg.args.index("--ctx-size")
    except ValueError as exc:
        raise ValueError("config args have no --ctx-size") from exc
    return int(cfg.args[idx + 1])


def lengths_to_run(lengths: list[int], cfg, ratio: float) -> tuple[list[int], list[int]]:
    """Split requested lengths into what fits the served window (the length
    times ratio, plus the config's max_tokens) and what does not: a length
    above the window is skipped, not silently sent and truncated."""
    limit = cfg.ctx_proven or max_ctx(cfg)
    max_tokens = cfg.sampling.get("max_tokens", 0)
    run = [length for length in lengths if length * ratio + max_tokens <= limit]
    skipped = [length for length in lengths if length * ratio + max_tokens > limit]
    return run, skipped


def write_skip_report(out_dir: pathlib.Path, name: str, run_lengths: list, skipped: list, message: str) -> None:
    """Write <out_dir>/<name>_skipped.json ({"skipped_lengths": [...]}), then
    raise SuiteSkipped(message) if nothing is left to run: a config whose
    window fits no requested length is not applicable to this suite, not a
    failed pass."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}_skipped.json").write_text(json.dumps({"skipped_lengths": skipped}))
    if not run_lengths:
        raise SuiteSkipped(message)


def passed_unless_truncated(base_passed: bool, truncated: bool) -> int:
    """A response cut off before finishing (finish_reason == "length") proves
    nothing about the model's actual answer, so it forces passed to 0
    regardless of the harness's own score or judge field."""
    return 0 if truncated else (1 if base_passed else 0)
