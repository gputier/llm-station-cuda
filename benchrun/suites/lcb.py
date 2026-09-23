"""LiveCodeBench suite adapter.

Runs the pinned livecodebench/livecodebench harness (bench/harness/lcb.Dockerfile)
through the launcher bench/harness/lcb_run.py (ruling V, task 9 brief): the
pinned commit only accepts models from its hardcoded registry and its OpenAI
runner takes no base_url of its own, so the launcher registers one
LanguageModel entry for the served alias at LMStyle.OpenAIChat before handing
off to the unmodified lcb_runner.runner.main.main().

Proven by two real runs on 2026-09-23 (spark on the 99, release_v6), whose
eval_all file is cut into tests/fixtures/lcb_eval_sample.json. Memory: the
pinned load_code_generation_dataset() decompresses the test cases of the
whole split before filtering by date, and peaks at 7.3 GiB of resident
memory on release_v6 even when the Hugging Face cache is already built. The
Docker VM needs at least 8 GiB (7.75 GiB usable here, enough with nothing
else running in it).

Output length: the harness sends max_tokens 2000 by default, which cut every
answer of a reasoning model before any code (four empty answers in the first
run). The gateway overwrites max_tokens like any sampling key, so every
config must set it in its sampling block.
"""
from __future__ import annotations

import json
import subprocess

from benchrun.config import served_alias
from benchrun.suites import NETWORK

IMAGE = "bench-lcb"
# Named volume holding the Hugging Face cache of the dataset (about 9 GB for
# release_v6), built once by bench/harness/lcb_run.py and reused by every run.
HF_CACHE_VOLUME = "bench-hf-cache"
SCENARIO = "codegeneration"
# One sample per problem. Temperature and top_p only shape the output file
# name: the gateway overwrites sampling on every request.
N_SAMPLES = 1
PLACEHOLDER_TEMPERATURE = 0.6
PLACEHOLDER_TOP_P = 0.95


def parse_lcb(eval_all: list[dict]) -> list[dict]:
    """Read the eval_all file the pinned harness writes with --evaluate.

    One dict per problem with "question_id" and "graded_list" (one boolean
    per sample). A problem passes when every sample passed, which equals the
    harness's own pass@1 for the single sample used here.
    """
    rows = []
    for instance in eval_all:
        graded_list = instance.get("graded_list") or []
        passed = 1 if graded_list and all(graded_list) else 0
        rows.append({"item_id": instance["question_id"], "passed": passed, "detail": instance})
    return rows


class LiveCodeBenchSuite:
    name = "lcb"

    def __init__(self, n_problems: int = 100, release: str = "release_v6", after_date: str = "2025-01-01"):
        # n_problems is not enforced: the pinned CLI only filters by release
        # and dates, so it documents the expected size of the selection
        # (182 problems for release_v6 after 2025-01-01, measured).
        self.n_problems = n_problems
        self.release = release
        self.after_date = after_date

    def run(self, ctx) -> list[dict]:
        model_alias = served_alias(ctx.cfg)
        result_name = f"rep{ctx.rep}-output"
        output_dir = ctx.out_dir / result_name
        subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", NETWORK,
                "-e", f"OPENAI_BASE_URL={ctx.base_url}/v1",
                "-e", "OPENAI_KEY=x",
                "-v", f"{output_dir}:/lcb/output",
                "-v", f"{HF_CACHE_VOLUME}:/root/.cache/huggingface",
                IMAGE,
                "python", "/lcb_run.py", model_alias,
                "--scenario", SCENARIO,
                "--evaluate",
                "--release_version", self.release,
                "--start_date", self.after_date,
                "--n", str(N_SAMPLES),
                "--temperature", str(PLACEHOLDER_TEMPERATURE),
                "--top_p", str(PLACEHOLDER_TOP_P),
                "--multiprocess", "1",
            ],
            check=True,
        )
        # The pinned get_output_path() formats the Scenario enum member itself,
        # which renders as "Scenario.codegeneration" (seen in the real run).
        eval_all_path = (
            output_dir / model_alias
            / f"Scenario.{SCENARIO}_{N_SAMPLES}_{PLACEHOLDER_TEMPERATURE}_eval_all.json"
        )
        eval_all = json.loads(eval_all_path.read_text(encoding="utf-8"))
        return parse_lcb(eval_all)
