"""Aider Polyglot benchmark suite adapter.

Runs the pinned Aider-AI/aider benchmark harness (bench/harness/aider.Dockerfile)
against the frozen 60-exercise subset (Bench-LLM/sets/aider-subset-60.txt),
pointed at the gateway through aider's documented OpenAI-compatible path
(model name prefixed "openai/", OPENAI_API_BASE set to the gateway).
"""
from __future__ import annotations

import json
import pathlib
import subprocess

from benchrun.config import served_alias
from benchrun.suites import NETWORK

IMAGE = "bench-aider"


def parse_aider(results_dir: pathlib.Path) -> list[dict]:
    """Read one .aider.results.json per exercise under results_dir.

    item_id is "<lang>/<exercise>": exercise names repeat across languages.
    tests_outcomes holds one boolean per try and stops at the first pass, so
    the exercise passed when its last entry is true; an empty list (aider
    raised before running the tests) counts as failed.
    """
    rows = []
    for result_file in sorted(results_dir.glob("*/exercises/practice/*/.aider.results.json")):
        data = json.loads(result_file.read_text())
        exercise_dir = result_file.parent
        lang = exercise_dir.parents[2].name
        item_id = f"{lang}/{exercise_dir.name}"
        outcomes = data.get("tests_outcomes") or []
        passed = 1 if outcomes and outcomes[-1] else 0
        rows.append({"item_id": item_id, "passed": passed, "detail": data})
    return rows


class AiderSuite:
    name = "aider"

    def __init__(self, exercises_file: str = "sets/aider-subset-60.txt", edit_format: str = "whole"):
        self.exercises_file = exercises_file
        self.edit_format = edit_format

    def run(self, ctx) -> list[dict]:
        model_alias = served_alias(ctx.cfg)
        exercises_path = ctx.private_root / self.exercises_file
        keywords = ",".join(
            line.strip() for line in exercises_path.read_text(encoding="utf-8").splitlines() if line.strip()
        )
        # benchmark.py only does its initial copytree from the polyglot tree
        # into the results dirname when that dirname does not exist yet. A
        # bind-mounted directory always exists (docker creates the mount
        # point even for a host path that did not exist), so ctx.out_dir
        # itself is mounted as the *parent* and the results dirname passed
        # to benchmark.py is a subpath under it that has never been created:
        # that is what makes benchmark.py take the "not dirname.exists()"
        # branch and populate it.
        result_name = f"rep{ctx.rep}-results"
        results_dir = ctx.out_dir / result_name
        subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", NETWORK,
                "-e", "AIDER_DOCKER=1",
                "-e", f"OPENAI_API_BASE={ctx.base_url}/v1",
                "-e", "OPENAI_API_KEY=x",
                "-e", "AIDER_BENCHMARK_DIR=/",
                "-v", f"{ctx.out_dir}:/output",
                IMAGE,
                "python", "benchmark/benchmark.py", f"/output/{result_name}",
                "--model", f"openai/{model_alias}",
                "--edit-format", self.edit_format,
                "--keywords", keywords,
                "--exercises-dir", "polyglot",
                "--threads", "1",
            ],
            check=True,
        )
        return parse_aider(results_dir)
