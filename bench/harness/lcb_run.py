"""Launcher for the pinned LiveCodeBench runner against a served OpenAI-compatible alias.

The pinned harness only accepts models from its hardcoded registry
(lcb_runner/lm_styles.py), so this launcher registers the served alias at
LMStyle.OpenAIChat, then calls the unmodified lcb_runner.runner.main.main().
The OpenAI client reads OPENAI_BASE_URL; the harness reads its key from
OPENAI_KEY, not OPENAI_API_KEY.

It also replaces the pinned load_code_generation_dataset() where the
scenario router calls it. The pinned loader decompresses the private tests of
every problem of the release before filtering by date (7.3 GiB peak on
release_v6); the replacement filters on the contest_date column first, with
the same bounds, and builds the Hugging Face cache in batches of 10 examples
when it is missing (the default batch of 1000 is killed above 7.2 GiB).

The pinned CLI has no problem count, only date bounds. The launcher's own
"--max-problems N" (removed before the pinned CLI sees the arguments) keeps a
seeded sample of N problems from the date window, the same N for every model.

It also removes the retry-on-failure defect proven live on the 99,
2026-09-24: OpenAIRunner._run_single (lcb_runner/runner/oai_runner.py, not
ours to edit, see bench/harness/README.md rule 3) catches every API error
(including a timeout) and resends the same prompt up to 10 times, 30 s apart,
while the server may still be decoding the first attempt: the abandoned
request keeps occupying the single-slot server (--parallel 1) and every
resend queues up behind it, which is how a real answer got lost. The
launcher replaces _run_single with a version that makes exactly one request,
no resend; the client's own SDK retries are set to 0 for the same reason.
BENCH_REQUEST_TIMEOUT_S (set by bench/benchrun/suites/lcb.py from the shared
benchrun.suites.request_timeout_s) becomes the pinned CLI's own
--openai_timeout when the caller did not pass it explicitly.

A failed request becomes ONE failed problem (an empty completion, graded
wrong by the pinned evaluator), never a crashed run: with --multiprocess 1
(sequential, no per-item isolation in the pinned base_runner.py, unlike
BFCL's own multi_threaded_inference) an uncaught exception here would lose
every OTHER problem's real result along with the failed one, for a single
bad question. Two conditions still abort the whole run instead of recording
a failed problem, on the same model _bounded_process_batch in ruler_run.py
uses: a failure before this process has ever received a real response (the
endpoint itself looks unreachable, not just this one request was slow), and
MAX_CONSECUTIVE_FAILURES failures in a row (the endpoint went dead
mid-run). Either way, a dead station must fail the suite, not produce a
full run of empty answers silently scored as a real 0.

Usage: lcb_run.py <served-alias> [--max-problems N] [lcb_runner.runner.main args...]
The launcher injects "--model <served-alias>" itself; do not pass --model.
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime

from datasets import load_dataset
from lcb_runner.benchmarks.code_generation import CodeGenerationProblem
from lcb_runner.lm_styles import LanguageModel, LanguageModelList, LanguageModelStore, LMStyle
from lcb_runner.runner import scenario_router

SELECTION_SEED = 0
# Not a retry budget (module docstring): a persistent or dead endpoint
# aborts the whole run after this many CONSECUTIVE single-attempt problem
# failures, instead of scoring every remaining problem a silent 0.
MAX_CONSECUTIVE_FAILURES = 3

_ever_succeeded = False
_consecutive_failures = 0


def _no_retry_run_single(self, prompt):
    """Replacement for OpenAIRunner._run_single (module docstring): one
    request, no catch-and-resend. A real SDK error is recorded as one
    failed problem (module docstring) unless the fail-fast predicate below
    trips, in which case the whole run gives up instead."""
    global _ever_succeeded, _consecutive_failures
    assert isinstance(prompt, list)
    try:
        response = type(self).client.chat.completions.create(messages=prompt, **self.client_kwargs)
        result = [c.message.content for c in response.choices]
        _ever_succeeded = True
        _consecutive_failures = 0
        return result
    except Exception as exc:
        _consecutive_failures += 1
        print(f"lcb_run: request failed, no retry ({type(exc).__name__}: {exc}), recorded as a failed problem",
              file=sys.stderr)
        if not _ever_succeeded or _consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            reason = "no response received yet" if not _ever_succeeded else f"{_consecutive_failures} consecutive failures"
            raise RuntimeError(f"lcb_run: giving up, {reason} ({type(exc).__name__}: {exc})") from exc
        return [""] * self.args.n


def disable_harness_retries() -> None:
    from openai import OpenAI
    from lcb_runner.runner.oai_runner import OpenAIRunner

    # The pinned class builds its client once, at class-definition time
    # (class attribute), with the SDK's own default retries (2): replaced
    # here with a fresh client, same api_key, max_retries=0.
    OpenAIRunner.client = OpenAI(api_key=OpenAIRunner.client.api_key, max_retries=0)
    OpenAIRunner._run_single = _no_retry_run_single


def apply_request_timeout_env(rest: list[str]) -> list[str]:
    """Default --openai_timeout from BENCH_REQUEST_TIMEOUT_S when the caller
    did not pass it explicitly: the single env var every suite adapter now
    carries the client timeout through (bench/harness/README.md rule 3)."""
    if "--openai_timeout" in rest:
        return rest
    timeout_env = os.environ.get("BENCH_REQUEST_TIMEOUT_S")
    if timeout_env is None:
        return rest
    return [*rest, "--openai_timeout", timeout_env]


def register(alias: str) -> None:
    if alias in LanguageModelStore:
        return
    model = LanguageModel(alias, alias, LMStyle.OpenAIChat, datetime(2025, 1, 1), link=None)
    LanguageModelList.append(model)
    LanguageModelStore[alias] = model


def select_indices(keep: list[int], max_problems: int | None) -> list[int]:
    if max_problems is None or max_problems >= len(keep):
        return keep
    return sorted(random.Random(SELECTION_SEED).sample(keep, max_problems))


def split_max_problems(args: list[str]) -> tuple[int | None, list[str]]:
    if "--max-problems" not in args:
        return None, args
    i = args.index("--max-problems")
    return int(args[i + 1]), args[:i] + args[i + 2:]


def load_code_generation_dataset_by_date(release_version="release_v1", start_date=None, end_date=None,
                                         max_problems=None):
    # Same signature, rows and bounds as the pinned loader, plus the sample size.
    dataset = load_dataset(
        "livecodebench/code_generation_lite",
        split="test",
        version_tag=release_version,
        trust_remote_code=True,
        writer_batch_size=10,
    )
    low = datetime.strptime(start_date, "%Y-%m-%d") if start_date is not None else None
    high = datetime.strptime(end_date, "%Y-%m-%d") if end_date is not None else None
    keep = [
        i for i, raw in enumerate(dataset["contest_date"])
        if (low is None or low <= datetime.fromisoformat(raw))
        and (high is None or datetime.fromisoformat(raw) <= high)
    ]
    keep = select_indices(keep, max_problems)
    problems = [CodeGenerationProblem(**row) for row in dataset.select(keep)]
    print(f"Loaded {len(problems)} problems")
    return problems


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: lcb_run.py <served-alias> [lcb_runner.runner.main args...]")
    alias, rest = sys.argv[1], sys.argv[2:]
    if "--model" in rest:
        raise SystemExit("pass the served alias as the first argument, not --model")
    max_problems, rest = split_max_problems(rest)
    rest = apply_request_timeout_env(rest)
    register(alias)
    disable_harness_retries()
    scenario_router.load_code_generation_dataset = (
        lambda *a, **kw: load_code_generation_dataset_by_date(*a, max_problems=max_problems, **kw)
    )
    sys.argv = [sys.argv[0], "--model", alias, *rest]
    from lcb_runner.runner.main import main as run_main

    run_main()


if __name__ == "__main__":
    main()
