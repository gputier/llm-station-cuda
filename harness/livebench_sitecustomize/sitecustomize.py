"""Auto-imported by the interpreter at process start (Python's own
sitecustomize convention: any module named sitecustomize on sys.path is
imported before the target script runs), placed on PYTHONPATH by
bench/harness/livebench.Dockerfile. Fixes the client-side timeout defect
proven live on LiveCodeBench, 2026-09-24, for the pinned livebench harness
(bench/harness/README.md rule 3: no fork of the pinned repo).

livebench.model.completions.chat_completion_openai (LIVEBENCH_SHA) builds
its OpenAI client with `timeout=httpx.Timeout(timeout=TIMEOUT, connect=10.0)`
when an api_dict is given (our --api-base/--api-key path, gen_api_answer.py
setup_model), where TIMEOUT is a plain module-level int (1800) read fresh at
every call: reassigning the module attribute is enough, no need to replace
the function itself. The pinned harness's own retry decorator
(API_MAX_RETRY = 1, tenacity stop_after_attempt(1)) is already zero retries
on the harness side; the SDK's own default (max_retries=2) is not, so
openai.OpenAI.__init__ is wrapped once to force max_retries=0 unless a
caller passes one explicitly. This runs for every OpenAI client built
anywhere in the process, not just the livebench.model.completions one:
every call site in this dedicated single-purpose image goes to the same
served endpoint under the same benchrun timeout contract.
"""
from __future__ import annotations

import os


def _patch_livebench_timeout() -> None:
    timeout_env = os.environ.get("BENCH_REQUEST_TIMEOUT_S")
    if timeout_env is None:
        return
    try:
        from livebench.model import completions
    except ImportError:
        # Not every process on this image imports livebench.model.completions
        # (e.g. a bare "python -c" health check); nothing to patch then.
        return
    completions.TIMEOUT = int(timeout_env)


def _patch_openai_max_retries() -> None:
    try:
        from openai import OpenAI
    except ImportError:
        return
    if getattr(OpenAI.__init__, "_bench_no_retry_patch", False):
        return
    original_init = OpenAI.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("max_retries", 0)
        original_init(self, *args, **kwargs)

    patched_init._bench_no_retry_patch = True
    OpenAI.__init__ = patched_init


_patch_livebench_timeout()
_patch_openai_max_retries()
