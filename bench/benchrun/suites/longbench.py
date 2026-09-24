"""LongBench v2 suite adapter: real-world long-context multiple choice.

Runs the pinned zai-org/LongBench-v2 dataset (Apache-2.0) against
bench/harness/longbench_run.py (bench/harness/README.md, LongBench v2
section, rule 3: no fork of the official THUDM/LongBench harness). Replaces
NoLiMa: NoLiMa's Adobe license is noncommercial research use only.

Length policy is shared with RULER (benchrun.suites.lengths_to_run): a
requested length above the model's proven or configured context window is
skipped, not silently dropped, recorded to <out_dir>/longbench_v2_skipped.json,
and raises SuiteSkipped when every requested length is skipped. The same
margin (measured tokenizer mismatch against the served model, plus the
config's max_tokens) applies to any suite that sizes its prompt with
cl100k_base instead of the served model's own tokenizer.

Bucket bands: each length in self.lengths gets a band (band_floor,
band_ceiling] of FULL PROMPT token counts (band_floor is the previous length
in the sorted list, 0 for the first), passed to the launcher as
--band-floor/--context-length: an item's full prompt (template + context +
question + choices, not the context alone) must land in exactly one band, so
no item_id can appear in two buckets by construction. This corresponds
loosely to the dataset's own "length" field (short items mostly land in the
first band, medium in the second, long never fits any band this suite uses,
see bench/harness/longbench_run.py's docstring for the measured word-count
correlation), but the band boundaries, not the field, are what the launcher
actually enforces.
"""
from __future__ import annotations

import json
import subprocess

from benchrun.config import served_alias
from benchrun.suites import (
    NETWORK,
    SAFETY_FACTOR,
    lengths_to_run,
    max_ctx,
    passed_unless_truncated,
    per_request_seconds,
    request_timeout_s,
    write_skip_report,
)

IMAGE = "bench-longbench"
# LongBench v2 contexts are real-world prose/code/dialogue, closer to book
# prose than to RULER's needle-dense synthetic text under cl100k_base vs the
# served model's own tokenizer. Kept at 1.05 with margin.
MEASURED_TOKEN_RATIO = 1.05
# longbench_run.py makes exactly one request per item: a timeout or a
# disconnection is recorded as a failed item, never resent (proven live on
# LiveCodeBench, 2026-09-24, a resend abandons the first attempt in place
# and queues up behind it, losing the real answer). Kept in sync with
# bench/harness/longbench_run.py's own MAX_ATTEMPTS (container-side, cannot
# import benchrun): bench/tests/test_longbench_run.py asserts the two agree.
MAX_ATTEMPTS = 1
# Container start/stop plus loading the 465 MB baked dataset once per run;
# measured well under 10 s in every real and stub pass.
STARTUP_OVERHEAD_S = 60


def subprocess_timeout_s(samples_per_length: int, max_tokens: int, context_length: int) -> int:
    """Timeout for one LongBenchV2Suite docker run call at the given
    context_length bucket, sized from the measured floor rates
    (benchrun.suites): longbench_run.py processes samples sequentially (one
    item, one request, no concurrency, no retry), so the total is
    samples_per_length times one item's single request cost, not divided
    into concurrent waves the way RULER's is. Prefill cost scales with
    context_length (the band's own ceiling, close to the real prompt size
    selected items must fit under).
    """
    per_request_s = per_request_seconds(context_length * MEASURED_TOKEN_RATIO, max_tokens)
    return int(max(samples_per_length, 1) * per_request_s * SAFETY_FACTOR + STARTUP_OVERHEAD_S)


def parse_longbench_v2(pred_jsonl_path) -> list[dict]:
    """Read one output file written by bench/harness/longbench_run.py.

    One JSON object per line: _id, domain, sub_domain, difficulty, length,
    answer, response, pred, judge, finish_reason, completion_tokens,
    input_truncated, input_tokens_cut. judge is the official
    THUDM/LongBench result.py scoring rule (exact letter match, no partial
    credit, ported verbatim in the launcher). passed = 1 only when judge is
    true AND finish_reason is not "length": a reply cut off before it could
    state an answer letter proves nothing about the model's actual answer,
    the same rule RULER's parse_ruler applies.
    """
    rows = []
    with open(pred_jsonl_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            truncated = entry.get("finish_reason") == "length"
            passed = passed_unless_truncated(bool(entry.get("judge")), truncated)
            detail = dict(entry)
            detail["truncated_by_length"] = truncated
            rows.append({"item_id": str(entry["_id"]), "passed": passed, "detail": detail})
    return rows


class LongBenchV2Suite:
    name = "longbench_v2"

    def __init__(self, lengths: list[int] | None = None, samples_per_length: int = 20,
                 selection_seed: int | None = None):
        self.lengths = list(lengths) if lengths is not None else [32768, 131072]
        self.samples_per_length = samples_per_length
        # Overrides the launcher's own fixed SELECTION_SEED (longbench_run.py)
        # for this run only; left None, the launcher's own default applies
        # and the docker run argv is byte-identical to before this parameter
        # existed.
        self.selection_seed = selection_seed

    def run(self, ctx) -> list[dict]:
        model_alias = served_alias(ctx.cfg)
        run_lengths, skipped = lengths_to_run(self.lengths, ctx.cfg, ratio=MEASURED_TOKEN_RATIO)
        rep_dir = ctx.out_dir / f"rep{ctx.rep}-longbench_v2"
        write_skip_report(rep_dir, "longbench_v2", run_lengths, skipped,
                           f"longbench_v2: every requested length {self.lengths} exceeds the served window")

        window = ctx.cfg.ctx_proven or max_ctx(ctx.cfg)
        max_tokens = ctx.cfg.sampling.get("max_tokens", 0)

        # Band floors: the previous entry of the FULL, nominal self.lengths
        # list (not just run_lengths), so band definitions stay stable
        # across configs with different served windows. 0 for the smallest
        # bucket.
        sorted_lengths = sorted(self.lengths)
        band_floor_by_length = {}
        floor = 0
        for target in sorted_lengths:
            band_floor_by_length[target] = floor
            floor = target

        rows: list[dict] = []
        seed_args = ["--seed", str(self.selection_seed)] if self.selection_seed is not None else []
        for length in run_lengths:
            timeout = subprocess_timeout_s(self.samples_per_length, max_tokens, length)
            cell_dir = rep_dir / str(length)
            cell_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--network", NETWORK,
                    "-v", f"{cell_dir}:/out",
                    "-e", f"BENCH_REQUEST_TIMEOUT_S={request_timeout_s(max_tokens, length * MEASURED_TOKEN_RATIO)}",
                    IMAGE,
                    "python", "/longbench_run.py", model_alias,
                    "--base-url", f"{ctx.base_url}/v1",
                    "--context-length", str(length),
                    "--band-floor", str(band_floor_by_length[length]),
                    "--window", str(window),
                    "--ratio", str(MEASURED_TOKEN_RATIO),
                    "--max-tokens", str(max_tokens),
                    "--num-samples", str(self.samples_per_length),
                    "--save-dir", "/out",
                    *seed_args,
                ],
                check=True,
                timeout=timeout,
            )
            pred_file = cell_dir / f"longbench_v2_{length}.jsonl"
            length_rows = parse_longbench_v2(pred_file) if pred_file.exists() else []
            if not length_rows:
                raise RuntimeError(f"longbench_v2 at length {length} graded nothing")
            for row in length_rows:
                row["item_id"] = f"{row['item_id']}@{length}"
                rows.append(row)
        if not rows:
            raise RuntimeError("longbench_v2 graded nothing: no length was requested")
        return rows
