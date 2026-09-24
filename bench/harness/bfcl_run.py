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
"""
from __future__ import annotations

import os
import sys
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
        else:
            raise SystemExit(f"unknown flag: {token}")
    return categories, extra


def generate(alias: str, rest: list[str]) -> None:
    from bfcl_eval._llm_response_generation import main as generation_main

    categories, extra = _parse_categories(rest)
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
        run_ids=bool(extra.get("run_ids")),
        enable_lora=False,
        max_lora_rank=None,
        lora_modules=None,
    )
    generation_main(args)


def evaluate(alias: str, rest: list[str]) -> None:
    from bfcl_eval.eval_checker.eval_runner import main as evaluation_main

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
