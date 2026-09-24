"""RULER suite adapter: synthetic long-context recall and tracking tasks.

Runs the pinned NVIDIA/RULER harness through bench/harness/ruler_run.py
(bench/harness/README.md, rule 3, no fork of the pinned repo): the client's
model2length lookup is patched at the launcher only, and OPENAI_BASE_URL
carries the gateway address since OpenAIClient never takes a base_url kwarg.

Length policy: a requested length above the model's proven or configured
context window is skipped, not silently dropped. lengths_to_run() returns
both lists; run() writes the skipped ones to <out_dir>/ruler_skipped.json for
the record. When every requested length is skipped, run() raises
SuiteSkipped: a config whose window never fits any requested length is not
applicable to this suite, not a failed pass.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess

from benchrun.config import served_alias
from benchrun.suites import (
    NETWORK,
    SAFETY_FACTOR,
    lengths_to_run as _lengths_to_run,
    max_ctx,
    passed_unless_truncated,
    per_request_seconds,
    write_skip_report,
)

IMAGE = "bench-ruler"
# The 13 canonical RULER synthetic tasks (scripts/config_tasks.sh, RULER_SHA).
# The essay haystack (niah_single_2, niah_single_3, niah_multikey_1,
# niah_multivalue, niah_multiquery) and the two QA datasets (qa_1: squad,
# qa_2: hotpotqa) are baked into bench-ruler at build time, pinned by URL and
# sha256 in pins.env (bench/harness/README.md).
DEFAULT_TASKS = (
    "niah_single_1", "niah_single_2", "niah_single_3",
    "niah_multikey_1", "niah_multikey_2", "niah_multikey_3",
    "niah_multivalue", "niah_multiquery",
    "vt", "cwe", "fwe", "qa_1", "qa_2",
)
# Tasks scored by the pinned harness's string_match_part metric (ANY
# reference literally present grades the full sample). Every other task uses
# string_match_all (fraction of references present). Read at RULER_SHA,
# scripts/eval/synthetic/constants.py TASKS: 'qa': {'metric_fn':
# string_match_part}, every other category (niah, variable_tracking,
# common_words_extraction, freq_words_extraction) uses string_match_all. The
# task-to-category mapping is scripts/synthetic.yaml: qa_1 and qa_2 are the
# only two entries with task: qa.
QA_TASKS = frozenset({"qa_1", "qa_2"})
# RULER sizes the prompt with cl100k_base (ruler_run.py), not the served
# model's own tokenizer, so the actual request is larger than the
# cl100k-counted length. Measured on real passes: the largest observed ratio
# (needle-dense haystacks compress worse than plain noise) is kept, rounded
# up, since a haystack type not yet measured could compress worse still and
# an unnoticed overflow would silently truncate the needle out of the prompt.
MEASURED_TOKEN_RATIO = 1.3
# call_api.py runs up to RULER_THREADS (its own --threads default, 4)
# requests concurrently, so one docker run call issues
# ceil(per_task / RULER_THREADS) sequential waves; each wave's wall time is
# bounded by its single slowest request, not the sum of all of them.
RULER_THREADS = 4
# data/prepare.py (dataset generation) plus container start/stop; measured
# well under 20 s for every task at 32768-131072, kept generous for the
# essay/QA tasks' heavier assembly at large lengths.
PREPARE_OVERHEAD_S = 120


def subprocess_timeout_s(per_task: int, max_tokens: int, length: int) -> int:
    """Timeout for one RulerSuite docker run call at the given RULER
    --max-seq-length, sized from the measured floor rates (benchrun.suites):
    it accounts for both the prefill cost of the requested length and the
    decode cost of max_tokens, times how many sequential waves per_task
    requires. Not the launcher's own bounded failure budget (ruler_run.py,
    MAX_CONSECUTIVE_FAILURES / MIN_TOTAL_FAILURES), which fails fast on real
    errors well before this timeout could ever fire.
    """
    waves = -(-max(per_task, 1) // RULER_THREADS)  # ceil division, no import
    per_request_s = per_request_seconds(length * MEASURED_TOKEN_RATIO, max_tokens)
    return int(waves * per_request_s * SAFETY_FACTOR + PREPARE_OVERHEAD_S)


def lengths_to_run(lengths: list[int], cfg, ratio: float = MEASURED_TOKEN_RATIO) -> tuple[list[int], list[int]]:
    return _lengths_to_run(lengths, cfg, ratio)


def _score_all(pred: str, refs: list) -> float:
    """string_match_all (RULER_SHA constants.py): fraction of references
    literally present in the prediction, case-insensitive. Used by niah,
    variable_tracking, common_words_extraction and freq_words_extraction."""
    if not refs:
        return 0.0
    pred_lower = pred.lower()
    return sum(1.0 for ref in refs if str(ref).lower() in pred_lower) / len(refs)


def _score_part(pred: str, refs: list) -> float:
    """string_match_part (RULER_SHA constants.py): 1.0 if ANY reference is
    literally present in the prediction, else 0.0. Used by qa_1 and qa_2."""
    if not refs:
        return 0.0
    pred_lower = pred.lower()
    return 1.0 if any(str(ref).lower() in pred_lower for ref in refs) else 0.0


def parse_ruler(pred_jsonl_path, task: str) -> list[dict]:
    """Read one prediction file written by the pinned pred/call_api.py.

    One JSON object per line: index, pred, input, outputs (list of
    references), others, truncation, length. Both of the pinned harness's own
    per-sample metrics (scripts/eval/synthetic/constants.py) are ported here
    exactly (_score_all, _score_part, selected by QA_TASKS): the pinned
    harness only ever averages them over a whole dataset run, never per item.

    Every row carries the harness score (float, 0 to 1) in detail["score"];
    passed is 1 only when score == 1 (a partial niah/vt/cwe/fwe match is
    recorded, not credited). detail also carries finish_reason and
    completion_tokens, joined from the sibling <task>.meta.jsonl file
    ruler_run.py writes (the pinned call_api.py keeps neither field); a
    sample whose finish_reason is "length" (the model was cut off) is forced
    to passed = 0 regardless of the harness score, since a truncated answer
    proves nothing about capability.
    """
    pred_path = pathlib.Path(pred_jsonl_path)
    meta_path = pred_path.with_name(f"{pred_path.stem}.meta.jsonl")
    meta_by_hash: dict[str, dict] = {}
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                meta_by_hash[entry["prompt_sha256"]] = entry

    score_fn = _score_part if task in QA_TASKS else _score_all
    rows = []
    with open(pred_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            pred = entry.get("pred") or ""
            refs = entry.get("outputs") or []
            score = score_fn(pred, refs)
            prompt_hash = hashlib.sha256(str(entry.get("input", "")).encode("utf-8")).hexdigest()
            meta = meta_by_hash.get(prompt_hash, {})
            finish_reason = meta.get("finish_reason")
            truncated = finish_reason == "length"
            passed = passed_unless_truncated(score >= 1.0, truncated)
            detail = dict(entry)
            detail["score"] = score
            detail["finish_reason"] = finish_reason
            detail["completion_tokens"] = meta.get("completion_tokens")
            detail["truncated_by_length"] = truncated
            rows.append({"item_id": f"{entry['index']}", "passed": passed, "detail": detail})
    return rows


class RulerSuite:
    name = "ruler"

    def __init__(self, lengths: list[int], tasks: list[str] | None = None, per_task: int = 20):
        self.lengths = lengths
        self.tasks = list(tasks) if tasks is not None else list(DEFAULT_TASKS)
        self.per_task = per_task

    def run(self, ctx) -> list[dict]:
        model_alias = served_alias(ctx.cfg)
        run_lengths, skipped = lengths_to_run(self.lengths, ctx.cfg)
        rep_dir = ctx.out_dir / f"rep{ctx.rep}-ruler"
        write_skip_report(rep_dir, "ruler", run_lengths, skipped,
                           f"ruler: every requested length {self.lengths} exceeds the served window")

        max_tokens = ctx.cfg.sampling.get("max_tokens", 0)
        rows: list[dict] = []
        for length in run_lengths:
            # Timeout is computed per length, not once for the whole suite:
            # prefill cost scales with the requested length (see
            # subprocess_timeout_s), so a 131072 pass legitimately needs much
            # more time than a 32768 one.
            timeout = subprocess_timeout_s(self.per_task, max_tokens, length)
            for task in self.tasks:
                cell_dir = rep_dir / str(length)
                cell_dir.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        "docker", "run", "--rm",
                        "--network", NETWORK,
                        "-v", f"{cell_dir}:/out",
                        IMAGE,
                        "python", "/ruler_run.py", model_alias,
                        "--task", task,
                        "--data-dir", "/out/data",
                        "--save-dir", "/out/pred",
                        "--max-seq-length", str(length),
                        "--num-samples", str(self.per_task),
                        "--base-url", f"{ctx.base_url}/v1",
                    ],
                    check=True,
                    timeout=timeout,
                )
                pred_file = cell_dir / "pred" / f"{task}.jsonl"
                task_rows = parse_ruler(pred_file, task) if pred_file.exists() else []
                if not task_rows:
                    raise RuntimeError(f"ruler task {task} at length {length} graded nothing")
                for row in task_rows:
                    row["item_id"] = f"{task}@{length}#{row['item_id']}"
                rows.extend(task_rows)
        if not rows:
            raise RuntimeError("ruler graded nothing: no task was requested")
        return rows
