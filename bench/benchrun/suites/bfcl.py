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
from benchrun.suites import NETWORK

IMAGE = "bench-bfcl"


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
        categories: tuple[str, ...] = ("simple_python", "multiple", "multi_turn_base"),
        handler: str = "OpenAICompletionsHandler",
    ):
        self.categories = categories
        self.handler = handler

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
        subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", NETWORK,
                *common_env,
                "-v", f"{rep_dir}:/out",
                IMAGE,
                "python", "/bfcl_run.py", model_alias, "generate",
                *category_flags,
                "--result-dir", "/out/result",
                "--allow-overwrite",
            ],
            check=True,
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
            ],
            check=True,
        )
        failing = {row["item_id"]: row for row in parse_bfcl(score_dir)}
        rows = []
        for item_id in _result_item_ids(result_dir):
            rows.append(failing.get(item_id) or {"item_id": item_id, "passed": 1, "detail": None})
        return rows
