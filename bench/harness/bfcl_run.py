"""Launcher that registers the served alias in BFCL's model registry, then
calls the pinned bfcl_eval generation_main() and evaluation_main(), the
functions behind `bfcl generate` and `bfcl evaluate`.

The pinned CLI only accepts models listed in MODEL_CONFIG_MAPPING and has no
flag to add one. The alias is served through OpenAICompletionsHandler, the
only handler that speaks plain OpenAI chat completions with tools and needs
no Hugging Face tokenizer; BFCL-HANDLERS.md records why the family-specific
handlers do not fit.

register() also patches OpenAICompletionsHandler._build_client_kwargs
(bfcl_eval/model_handler/api_inference/openai_completion.py, not ours to
edit): the pinned version only ever reads api_key/base_url/default_headers
from the environment, leaving the OpenAI SDK's own defaults for the request
timeout (600 s) and max_retries (2) in place. BENCH_REQUEST_TIMEOUT_S (set
by bench/benchrun/suites/bfcl.py from the shared
benchrun.suites.request_timeout_s) becomes the client's own timeout, and
max_retries is forced to 0: the generate phase's own category/item loop
already accounts for a failed item, a silent SDK resend on a timeout it
never sees is what abandons the server-side request instead (module
docstring of bench/harness/lcb_run.py describes the same defect proven live
on the 99, 2026-09-24).

generate's own "--limit N" (optionally "--seed S") caps each requested
category to N items instead of running it in full, through the pinned
harness's own run-ids mechanism (selected_ids_by_category writes
TEST_IDS_TO_GENERATE_PATH, then generation_main runs with run_ids=True):
the first N ids in the dataset's own order without a seed, a seeded random
sample of N with one. evaluate's own "--partial-eval" (unchanged, already
present) must be passed alongside on the matching evaluate call whenever
generate used "--limit", since the result files then hold a strict subset of
each category and a full evaluation would otherwise count every missing
item as a hard failure. Neither flag existed before benchrun's mini/medium/
large bench levels (2026-09-24); a caller that never passes them (the "run"
subcommand's full campaign) gets exactly the previous behavior.
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path
from types import SimpleNamespace


def _build_client_kwargs_with_timeout(self) -> dict:
    kwargs = _ORIGINAL_BUILD_CLIENT_KWARGS(self)
    timeout_env = os.environ.get("BENCH_REQUEST_TIMEOUT_S")
    if timeout_env is not None:
        kwargs["timeout"] = float(timeout_env)
    kwargs["max_retries"] = 0
    return kwargs


_ORIGINAL_BUILD_CLIENT_KWARGS = None


def register(alias: str) -> None:
    from bfcl_eval.constants.model_config import MODEL_CONFIG_MAPPING, ModelConfig
    from bfcl_eval.model_handler.api_inference.openai_completion import (
        OpenAICompletionsHandler,
    )

    global _ORIGINAL_BUILD_CLIENT_KWARGS
    if _ORIGINAL_BUILD_CLIENT_KWARGS is None:
        _ORIGINAL_BUILD_CLIENT_KWARGS = OpenAICompletionsHandler._build_client_kwargs
        OpenAICompletionsHandler._build_client_kwargs = _build_client_kwargs_with_timeout

    MODEL_CONFIG_MAPPING[alias] = ModelConfig(
        model_name=alias,
        display_name=alias,
        url="",
        org="bench",
        license="n/a",
        model_handler=OpenAICompletionsHandler,
        input_price=None,
        output_price=None,
        is_fc_model=True,
        # OpenAICompletionsHandler sends "math.factorial" as "math_factorial"
        # (OpenAI tool names allow no dot); without this flag the checker
        # compares the dotted name and grades every such call wrong.
        underscore_to_dot=True,
    )


def _parse_categories(rest: list[str]) -> tuple[list[str], dict[str, str]]:
    categories: list[str] = []
    extra: dict[str, str] = {}
    args_iter = iter(rest)
    for token in args_iter:
        if token == "--test-category":
            categories.append(next(args_iter))
        elif token == "--result-dir":
            extra["result_dir"] = next(args_iter)
        elif token == "--score-dir":
            extra["score_dir"] = next(args_iter)
        elif token == "--allow-overwrite":
            extra["allow_overwrite"] = "1"
        elif token == "--run-ids":
            extra["run_ids"] = "1"
        elif token == "--partial-eval":
            extra["partial_eval"] = "1"
        elif token == "--limit":
            extra["limit"] = next(args_iter)
        elif token == "--seed":
            extra["seed"] = next(args_iter)
        else:
            raise SystemExit(f"unknown flag: {token}")
    return categories, extra


# Fixed path the pinned harness itself reads when generation_main() is called
# with run_ids=True (bfcl_eval._llm_response_generation.get_involved_test_entries
# -> load_test_entries_from_id_file(TEST_IDS_TO_GENERATE_PATH), not something
# this launcher can point elsewhere: the constant lives in the pinned repo,
# not ours to edit (bench/harness/README.md rule 3).
def selected_ids_by_category(categories: list[str], limit: int, seed: int | None) -> dict[str, list[str]]:
    """Native selection, not a reimplementation: bfcl_eval.utils.load_dataset_entry
    is the pinned harness's own dataset loader (the same one generation_main
    uses internally), read here only to pick which of its ids go into the
    run-ids file the harness already knows how to consume. Deterministic
    (first `limit` ids in the dataset's own order) when seed is None,
    seeded-random (one random.Random per category, so categories do not
    share a draw) otherwise, kept reproducible across the three profiles of
    one model within a single bench launch by reusing the same seed value."""
    from bfcl_eval.utils import load_dataset_entry

    selected: dict[str, list[str]] = {}
    for category in categories:
        ids = [entry["id"] for entry in load_dataset_entry(category)]
        if seed is None:
            selected[category] = ids[:limit]
        else:
            rng = random.Random(f"{seed}:{category}")
            selected[category] = rng.sample(ids, min(limit, len(ids)))
    return selected


def generate(alias: str, rest: list[str]) -> None:
    from bfcl_eval._llm_response_generation import main as generation_main
    from bfcl_eval.constants.eval_config import TEST_IDS_TO_GENERATE_PATH

    categories, extra = _parse_categories(rest)
    limit = extra.get("limit")
    run_ids = bool(extra.get("run_ids"))
    if limit is not None:
        seed = int(extra["seed"]) if "seed" in extra else None
        selection = selected_ids_by_category(categories, int(limit), seed)
        Path(TEST_IDS_TO_GENERATE_PATH).write_text(json.dumps(selection), encoding="utf-8")
        run_ids = True
    args = SimpleNamespace(
        model=[alias],
        test_category=categories,
        temperature=0.001,
        include_input_log=False,
        exclude_state_log=False,
        num_gpus=1,
        num_threads=None,
        gpu_memory_utilization=0.9,
        backend="sglang",
        skip_server_setup=True,
        local_model_path=None,
        result_dir=extra.get("result_dir"),
        allow_overwrite=bool(extra.get("allow_overwrite")),
        run_ids=run_ids,
        enable_lora=False,
        max_lora_rank=None,
        lora_modules=None,
    )
    generation_main(args)


def evaluate(alias: str, rest: list[str]) -> None:
    from bfcl_eval.eval_checker import eval_runner
    from bfcl_eval.eval_checker.eval_runner import main as evaluation_main

    # runner() (called by evaluation_main, eval_runner.py) writes every
    # category's *_result.json/*_score.json through evaluate_task() FIRST,
    # then always finishes with generate_leaderboard_csv(leaderboard_table,
    # score_dir): a single-item category (mini's own --limit 1, or a small
    # --limit generally) makes get_cost_latency_info (eval_runner_helper.py)
    # call statistics.stdev on that one data point, which the pinned stdlib
    # raises "requires at least two data points" for (proven live, 99/tiel/
    # R1/bfcl, 2026-09-24). The score files this launcher actually reads
    # (benchrun.suites.bfcl.parse_bfcl) are already on disk by the time this
    # fires; the leaderboard CSV itself is never read by anything in this
    # repo. Neutralized here (module attribute, not the pinned source file:
    # eval_runner.py imports it with "from ... import *", so this name lives
    # in eval_runner's own namespace, not eval_runner_helper's) rather than
    # wrapped in a try/except around evaluation_main, since a real error
    # inside evaluate_task itself (a missing result file, say) must still
    # raise and fail the suite, not be swallowed along with this one.
    eval_runner.generate_leaderboard_csv = lambda *args, **kwargs: None

    categories, extra = _parse_categories(rest)
    evaluation_main(
        [alias],
        categories,
        extra.get("result_dir"),
        extra.get("score_dir"),
        bool(extra.get("partial_eval")),
    )


def main() -> None:
    alias = sys.argv[1]
    command = sys.argv[2]
    rest = sys.argv[3:]
    register(alias)
    if command == "generate":
        generate(alias, rest)
    elif command == "evaluate":
        evaluate(alias, rest)
    else:
        raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
