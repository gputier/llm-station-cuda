"""Launcher for the pinned LiveCodeBench runner against a served OpenAI-compatible alias.

The pinned harness only accepts models from its hardcoded registry
(lcb_runner/lm_styles.py), so this launcher registers the served alias at
LMStyle.OpenAIChat, then calls the unmodified lcb_runner.runner.main.main().
The OpenAI client reads OPENAI_BASE_URL; the harness reads its key from
OPENAI_KEY, not OPENAI_API_KEY.

It also builds the Hugging Face cache of the dataset first: with datasets'
default writer batch of 1000 examples the release_v6 build is killed above
7.2 GiB, with a batch of 10 it peaks at 1.5 GiB.

Usage: lcb_run.py <served-alias> [lcb_runner.runner.main args...]
The launcher injects "--model <served-alias>" itself; do not pass --model.
"""
from __future__ import annotations

import sys
from datetime import datetime

from datasets import load_dataset
from lcb_runner.lm_styles import LanguageModel, LanguageModelList, LanguageModelStore, LMStyle


def register(alias: str) -> None:
    if alias in LanguageModelStore:
        return
    model = LanguageModel(alias, alias, LMStyle.OpenAIChat, datetime(2025, 1, 1), link=None)
    LanguageModelList.append(model)
    LanguageModelStore[alias] = model


def prepare_dataset(args: list[str]) -> None:
    # Same dataset, split and version tag as the pinned
    # load_code_generation_dataset(), so the cache it writes is the one the
    # pinned loader reads. Default release mirrors the pinned CLI default.
    release = args[args.index("--release_version") + 1] if "--release_version" in args else "release_latest"
    load_dataset(
        "livecodebench/code_generation_lite",
        split="test",
        version_tag=release,
        trust_remote_code=True,
        writer_batch_size=10,
    )


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: lcb_run.py <served-alias> [lcb_runner.runner.main args...]")
    alias, rest = sys.argv[1], sys.argv[2:]
    if "--model" in rest:
        raise SystemExit("pass the served alias as the first argument, not --model")
    register(alias)
    if not {"-h", "--help"} & set(rest):
        prepare_dataset(rest)
    sys.argv = [sys.argv[0], "--model", alias, *rest]
    from lcb_runner.runner.main import main as run_main

    run_main()


if __name__ == "__main__":
    main()
