"""LiveBench suite adapter: reasoning and math categories, ground-truth scoring.

Runs the pinned livebench/livebench CLI unmodified: an unregistered model
name falls back to a generic OpenAI-compatible chat.completions call against
--api-base (livebench/model/api_model_config.py, completions.py).

The release is pinned (RELEASE) and passed with --livebench-release-option;
without it the harness keeps questions already retired. Tasks are listed from
the live dataset, so a task retired before RELEASE yields no question at all
(web_of_lies_v2 at 2026-06-25): run() fails when a requested category grades
nothing. Volume at RELEASE, counted with the harness's own load_questions:
282 questions, reasoning 100 (spatial 50, zebra_puzzle 50) and math 182
(AMPS_Hard 100, math_comp 46, olympiad 36).

run_livebench.py calls its sibling scripts by bare name, which only resolves
from the inner livebench/livebench directory: run() works from there and
mounts the data directory at livebench/livebench/data, where one judgment
file per task is written.
"""
from __future__ import annotations

import json
import pathlib
import subprocess

from benchrun.config import served_alias
from benchrun.suites import HF_CACHE_VOLUME, NETWORK, SAFETY_FACTOR, per_request_seconds

IMAGE = "bench-livebench"
# Newest release in LIVE_BENCH_RELEASES at LIVEBENCH_SHA (pins.env), read
# from livebench/common.py: LIVE_BENCH_RELEASES literal set.
RELEASE = "2026-06-25"
# Question counts at RELEASE, measured with the harness's own load_questions
# (module docstring): reasoning 100 (spatial 50, zebra_puzzle 50), math 182
# (AMPS_Hard 100, math_comp 46, olympiad 36). A category not in this table
# (none exist today beyond these two) falls back to the larger of the two
# measured counts, kept as an explicit margin rather than guessed low.
ITEMS_PER_CATEGORY = {"reasoning": 100, "math": 182}
DEFAULT_ITEMS_PER_CATEGORY = max(ITEMS_PER_CATEGORY.values())
# LiveBench math/reasoning prompts run long (full problem statements, some
# with figures described in text); kept generous.
PROMPT_TOKENS_ESTIMATE = 3000
# Local ground-truth judging per question (no model call), plus the
# harness's own retry on a malformed answer.
JUDGE_OVERHEAD_S = 10
STARTUP_OVERHEAD_S = 60


def subprocess_timeout_s(categories: tuple[str, ...], max_tokens: int) -> int:
    per_request_s = per_request_seconds(PROMPT_TOKENS_ESTIMATE, max_tokens)
    n_items = sum(ITEMS_PER_CATEGORY.get(c, DEFAULT_ITEMS_PER_CATEGORY) for c in categories)
    return int(max(n_items, 1) * (per_request_s * SAFETY_FACTOR + JUDGE_OVERHEAD_S) + STARTUP_OVERHEAD_S)


def parse_livebench(judgment_jsonl: pathlib.Path) -> list[dict]:
    """Read one flat JSONL judgment file written by gen_ground_truth_judgment.py.

    One dict per graded question: question_id, task, model, score, category,
    and an optional eval_status/error_msg on a scoring failure. Reasoning and
    math tasks here are exact-match ground-truth checkers, so score is 0 or 1;
    passed rounds it to stay defensive against a float artifact.
    """
    rows = []
    for line in judgment_jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        passed = 1 if round(entry["score"]) >= 1 else 0
        rows.append({"item_id": entry["question_id"], "passed": passed, "detail": entry})
    return rows


class LiveBenchSuite:
    name = "livebench"

    def __init__(self, categories: tuple[str, ...] = ("reasoning", "math"), release: str = RELEASE):
        # common.get_categories_tasks keeps what precedes the first "_" of a
        # category name, so "data_analysis" would load dataset "data".
        for category in categories:
            if "_" in category:
                raise ValueError(f"livebench category {category} holds an underscore the pinned harness truncates")
        self.categories = categories
        self.release = release

    def run(self, ctx) -> list[dict]:
        model_alias = served_alias(ctx.cfg)
        result_name = f"rep{ctx.rep}-livebench"
        data_dir = ctx.out_dir / result_name / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        max_tokens = ctx.cfg.sampling.get("max_tokens", 0)
        subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", NETWORK,
                "-w", "/livebench/livebench",
                "-v", f"{data_dir}:/livebench/livebench/data",
                "-v", f"{HF_CACHE_VOLUME}:/root/.cache/huggingface",
                IMAGE,
                "python", "run_livebench.py",
                "--model", model_alias,
                "--mode", "single",
                "--bench-name", *[f"live_bench/{c}" for c in self.categories],
                "--question-source", "huggingface",
                "--api-base", f"{ctx.base_url}/v1",
                "--api-key", "x",
                "--livebench-release-option", self.release,
            ],
            check=True,
            timeout=subprocess_timeout_s(self.categories, max_tokens),
        )
        rows: list[dict] = []
        for category in self.categories:
            found = []
            for judgment_file in sorted(data_dir.glob(f"live_bench/{category}/*/model_judgment/ground_truth_judgment.jsonl")):
                found.extend(parse_livebench(judgment_file))
            if not found:
                raise RuntimeError(f"livebench category {category} graded no question at release {self.release}")
            rows.extend(found)
        return rows
