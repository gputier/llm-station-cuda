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

Usage: lcb_run.py <served-alias> [lcb_runner.runner.main args...]
The launcher injects "--model <served-alias>" itself; do not pass --model.
"""
from __future__ import annotations

import sys
from datetime import datetime

from datasets import load_dataset
from lcb_runner.benchmarks.code_generation import CodeGenerationProblem
from lcb_runner.lm_styles import LanguageModel, LanguageModelList, LanguageModelStore, LMStyle
from lcb_runner.runner import scenario_router


def register(alias: str) -> None:
    if alias in LanguageModelStore:
        return
    model = LanguageModel(alias, alias, LMStyle.OpenAIChat, datetime(2025, 1, 1), link=None)
    LanguageModelList.append(model)
    LanguageModelStore[alias] = model


def load_code_generation_dataset_by_date(release_version="release_v1", start_date=None, end_date=None):
    # Same signature, rows and bounds as the pinned loader.
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
    problems = [CodeGenerationProblem(**row) for row in dataset.select(keep)]
    print(f"Loaded {len(problems)} problems")
    return problems


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: lcb_run.py <served-alias> [lcb_runner.runner.main args...]")
    alias, rest = sys.argv[1], sys.argv[2:]
    if "--model" in rest:
        raise SystemExit("pass the served alias as the first argument, not --model")
    register(alias)
    scenario_router.load_code_generation_dataset = load_code_generation_dataset_by_date
    sys.argv = [sys.argv[0], "--model", alias, *rest]
    from lcb_runner.runner.main import main as run_main

    run_main()


if __name__ == "__main__":
    main()
