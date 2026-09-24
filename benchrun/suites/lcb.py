"""LiveCodeBench suite adapter.

Runs the pinned livecodebench/livecodebench harness (bench/harness/lcb.Dockerfile)
through the launcher bench/harness/lcb_run.py (ruling V, task 9 brief): the
pinned commit only accepts models from its hardcoded registry and its OpenAI
runner takes no base_url of its own, so the launcher registers one
LanguageModel entry for the served alias at LMStyle.OpenAIChat before handing
off to the unmodified lcb_runner.runner.main.main().

Proven by two real runs on 2026-09-23 (spark on the 99, release_v6), whose
eval_all file is cut into tests/fixtures/lcb_eval_sample.json. Memory: the
launcher's date-first loader keeps the load under 1 GiB on release_v6 after
2025-01-01, where the pinned one peaks at 7.3 GiB (see lcb_run.py).

Output length: the harness sends max_tokens 2000 by default, which cut every
answer of a reasoning model before any code (four empty answers in the first
run). The gateway overwrites max_tokens like any sampling key, so every
config must set it in its sampling block.
"""
from __future__ import annotations

import json
import subprocess

from benchrun.config import served_alias
from benchrun.suites import HF_CACHE_VOLUME, NETWORK, SAFETY_FACTOR, per_request_seconds, request_timeout_s

IMAGE = "bench-lcb"
SCENARIO = "codegeneration"
# One sample per problem. Temperature and top_p only shape the output file
# name: the gateway overwrites sampling on every request.
N_SAMPLES = 1
PLACEHOLDER_TEMPERATURE = 0.6
PLACEHOLDER_TOP_P = 0.95
# LiveCodeBench problem statements plus a solution skeleton, a generous
# margin over what real passes have sent (Task 9's real 12-problem pass, no
# statement seen over a few hundred tokens). No timing/tokenizer to measure
# this from directly, unlike RULER's cl100k ratio: kept generous on purpose.
PROMPT_TOKENS_ESTIMATE = 2000
# Sandboxed test execution per problem (the pinned harness's own --evaluate
# step), which the request/decode floors above do not cover at all.
EVAL_OVERHEAD_S = 30
# Container start plus the HF-cached dataset filter (bench-hf-cache volume,
# already warm after the first run); measured well under this in every real
# pass so far (Task 9, Ruling AA).
STARTUP_OVERHEAD_S = 120


def subprocess_timeout_s(n_problems: int, max_tokens: int) -> int:
    """Timeout for the one docker run call this suite makes (--multiprocess 1,
    strictly sequential across problems): n_problems times one request's
    prefill+decode cost (the shared per_request_seconds floors) plus its own
    evaluation overhead, plus a fixed startup margin."""
    per_request_s = per_request_seconds(PROMPT_TOKENS_ESTIMATE, max_tokens)
    return int(max(n_problems, 1) * (per_request_s * SAFETY_FACTOR + EVAL_OVERHEAD_S) + STARTUP_OVERHEAD_S)


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
        # The pinned CLI only filters by release and dates (182 problems for
        # release_v6 after 2025-01-01, measured); the launcher's own
        # --max-problems keeps a seeded sample of n_problems of them.
        self.n_problems = n_problems
        self.release = release
        self.after_date = after_date

    def run(self, ctx) -> list[dict]:
        model_alias = served_alias(ctx.cfg)
        result_name = f"rep{ctx.rep}-output"
        output_dir = ctx.out_dir / result_name
        max_tokens = ctx.cfg.sampling.get("max_tokens", 0)
        subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", NETWORK,
                "-e", f"OPENAI_BASE_URL={ctx.base_url}/v1",
                "-e", "OPENAI_KEY=x",
                # The pinned client default is 90 s, then up to 10 resends
                # 30 s apart: a longer answer is dropped and resent while the
                # server still decodes it (seen live on the 99, 2026-09-24).
                # lcb_run.py reads this to set the pinned CLI's own
                # --openai_timeout and to remove the pinned harness's resend
                # loop entirely (bench/harness/README.md).
                "-e", f"BENCH_REQUEST_TIMEOUT_S={request_timeout_s(max_tokens, PROMPT_TOKENS_ESTIMATE)}",
                "-v", f"{output_dir}:/lcb/output",
                "-v", f"{HF_CACHE_VOLUME}:/root/.cache/huggingface",
                IMAGE,
                "python", "/lcb_run.py", model_alias,
                "--max-problems", str(self.n_problems),
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
            timeout=subprocess_timeout_s(self.n_problems, max_tokens),
        )
        # The pinned get_output_path() formats the Scenario enum member itself,
        # which renders as "Scenario.codegeneration" (seen in the real run).
        eval_all_path = (
            output_dir / model_alias
            / f"Scenario.{SCENARIO}_{N_SAMPLES}_{PLACEHOLDER_TEMPERATURE}_eval_all.json"
        )
        eval_all = json.loads(eval_all_path.read_text(encoding="utf-8"))
        return parse_lcb(eval_all)
