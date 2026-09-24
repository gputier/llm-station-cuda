"""BFCL v4 suite adapter: agentic tool-calling, native function-calling (FC) mode.

Runs the pinned harness through bench/harness/bfcl_run.py. The design spec's
"simple" category is "simple_python" at BFCL_SHA; handler choice and category
naming are recorded in bench/harness/BFCL-HANDLERS.md.
"""
from __future__ import annotations

import json
import pathlib
import subprocess

from benchrun.config import served_alias
from benchrun.suites import NETWORK, SAFETY_FACTOR, per_request_seconds, request_timeout_s

IMAGE = "bench-bfcl"
# BfclSuite's own default categories, read by benchrun.__main__'s mini
# preset (mini_suites draws one of these at random) rather than duplicated
# there.
DEFAULT_CATEGORIES = ("simple_python", "multiple", "multi_turn_base")
# BFCL prompts (tool schema plus turn history) stay modest; kept generous.
PROMPT_TOKENS_ESTIMATE = 1500
# Conservative upper bound on items in any one category (categories vary;
# no fixed count published at BFCL_SHA), so the timeout never undercounts a
# category larger than the ones already run live (Task 10: simple_python 8,
# multiple 8, multi_turn_base 2).
MAX_ITEMS_PER_CATEGORY = 400
STARTUP_OVERHEAD_S = 60
# evaluate() makes no model call at all (pure local grading against the
# generate phase's own result files), a flat per-category budget is enough.
EVALUATE_OVERHEAD_PER_CATEGORY_S = 60


def generate_timeout_s(n_categories: int, max_tokens: int) -> int:
    per_request_s = per_request_seconds(PROMPT_TOKENS_ESTIMATE, max_tokens)
    return int(max(n_categories, 1) * MAX_ITEMS_PER_CATEGORY * per_request_s * SAFETY_FACTOR
               + STARTUP_OVERHEAD_S)


def evaluate_timeout_s(n_categories: int) -> int:
    return int(max(n_categories, 1) * EVALUATE_OVERHEAD_PER_CATEGORY_S + STARTUP_OVERHEAD_S)


def parse_bfcl(score_dir: pathlib.Path) -> list[dict]:
    """Read every BFCL_v4_<category>_score.json file under score_dir.

    Each file is JSONL: a header line (accuracy, correct_count, total_count,
    no "id"), then one line per FAILING item. Passing items are only counted
    in the header, so this returns failures only; BfclSuite.run() gets the
    full item list from the harness's result files.
    """
    rows = []
    for score_file in sorted(score_dir.glob("**/*_score.json")):
        for line in score_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if "id" not in entry:
                continue
            rows.append({"item_id": entry["id"], "passed": 0, "detail": entry})
    return rows


def _result_item_ids(result_dir: pathlib.Path) -> list[str]:
    """Read every id from the harness's own *_result.json files (one row
    per requested item, regardless of pass/fail): the universe parse_bfcl's
    failure-only score files cannot provide on their own."""
    ids = []
    for result_file in sorted(result_dir.glob("**/*_result.json")):
        for line in result_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            ids.append(json.loads(line)["id"])
    return ids


class BfclSuite:
    name = "bfcl"

    def __init__(
        self,
        categories: tuple[str, ...] = DEFAULT_CATEGORIES,
        handler: str = "OpenAICompletionsHandler",
        limit: int | None = None,
        seed: int | None = None,
    ):
        # limit caps each category to its first `limit` items (dataset
        # order) or, with seed set, a seeded random sample of `limit`:
        # bfcl_run.py's own --limit/--seed (see its module docstring). None
        # (the default) runs every category in full, the "run" subcommand's
        # own behavior, unchanged.
        self.categories = categories
        self.handler = handler
        self.limit = limit
        self.seed = seed

    def run(self, ctx) -> list[dict]:
        model_alias = served_alias(ctx.cfg)
        rep_dir = ctx.out_dir / f"rep{ctx.rep}-bfcl"
        result_dir = rep_dir / "result"
        score_dir = rep_dir / "score"
        category_flags = []
        for category in self.categories:
            category_flags += ["--test-category", category]

        common_env = [
            "-e", f"OPENAI_BASE_URL={ctx.base_url}/v1",
            "-e", "OPENAI_API_KEY=x",
        ]
        max_tokens = ctx.cfg.sampling.get("max_tokens", 0)
        n_categories = len(self.categories)
        # The generate phase is the only one that calls the model
        # (evaluate() grades local result files, no model call at all):
        # bfcl_run.py reads this to size the OpenAI client's own timeout and
        # to set the SDK's max_retries to 0 (bench/harness/README.md).
        generate_env = [*common_env, "-e", f"BENCH_REQUEST_TIMEOUT_S={request_timeout_s(max_tokens, PROMPT_TOKENS_ESTIMATE)}"]
        limit_flags = ["--limit", str(self.limit)] if self.limit is not None else []
        if self.limit is not None and self.seed is not None:
            limit_flags += ["--seed", str(self.seed)]
        partial_eval_flags = ["--partial-eval"] if self.limit is not None else []
        subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", NETWORK,
                *generate_env,
                "-v", f"{rep_dir}:/out",
                IMAGE,
                "python", "/bfcl_run.py", model_alias, "generate",
                *category_flags,
                "--result-dir", "/out/result",
                "--allow-overwrite",
                *limit_flags,
            ],
            check=True,
            timeout=generate_timeout_s(n_categories, max_tokens),
        )
        subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", NETWORK,
                *common_env,
                "-v", f"{rep_dir}:/out",
                IMAGE,
                "python", "/bfcl_run.py", model_alias, "evaluate",
                *category_flags,
                "--result-dir", "/out/result",
                "--score-dir", "/out/score",
                *partial_eval_flags,
            ],
            check=True,
            timeout=evaluate_timeout_s(n_categories),
        )
        # Score files list failures only: a category the harness skipped
        # would otherwise count every one of its items as passed.
        written = {path.name for path in rep_dir.glob("**/BFCL_v4_*.json")}
        for category in self.categories:
            for kind in ("result", "score"):
                if f"BFCL_v4_{category}_{kind}.json" not in written:
                    raise RuntimeError(f"bfcl category {category} wrote no {kind} file")
        failing = {row["item_id"]: row for row in parse_bfcl(score_dir)}
        rows = []
        for item_id in _result_item_ids(result_dir):
            rows.append(failing.get(item_id) or {"item_id": item_id, "passed": 1, "detail": None})
        return rows
