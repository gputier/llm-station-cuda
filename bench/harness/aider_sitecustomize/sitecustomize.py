"""Auto-imported by the interpreter at process start (Python's own
sitecustomize convention: any module named sitecustomize on sys.path is
imported before the target script runs), placed on PYTHONPATH by
bench/harness/aider.Dockerfile. Fixes the client-side timeout/retry defect
proven live on LiveCodeBench, 2026-09-24, for the pinned Aider-AI/aider
harness (bench/harness/README.md rule 3: no fork of the pinned repo):
benchmark.py calls into aider's own Coder machinery in-process (no separate
venv or subprocess for the model call itself), so a patch applied here
before that machinery runs is enough; no runpy/monkeypatch-then-call
indirection is needed the way lcb_run.py/ruler_run.py need it for a
subprocess-driven pinned CLI.

aider/models.py (AIDER_SHA) hardcodes `request_timeout = 600` as a plain
module global, read fresh at every send (`if "timeout" not in kwargs:
kwargs["timeout"] = request_timeout`): reassigning the module attribute is
enough. aider/coders/base_coder.py's send loop
(`while True: try: yield from self.send(...) except
litellm_ex.exceptions_tuple() as err: ... should_retry = ex_info.retry ...`)
retries on litellm's own Timeout and APIConnectionError classes (both
retry=True in aider/exceptions.py's EXCEPTIONS table) with exponential
backoff up to RETRY_TIMEOUT (60 s) between tries: exactly the defect proven
live, a client that resends a request the server may still be decoding
abandons the first attempt in place and its resend queues up behind it.
LiteLLMExceptions.exception_info is a CLASS attribute (built once, shared by
every instance LiteLLMExceptions() creates, since `_load()` assigns from it
by reference rather than copying), so mutating every entry's `.retry` to
False once, here, at import time, is enough: no per-instance patch and no
need to intercept `_load()` itself.
"""
from __future__ import annotations

import os


def _patch_aider_timeout() -> None:
    timeout_env = os.environ.get("BENCH_REQUEST_TIMEOUT_S")
    if timeout_env is None:
        return
    try:
        import aider.models as models
    except ImportError:
        # Not every process on this image imports aider.models (e.g. a bare
        # "python -c" health check); nothing to patch then.
        return
    models.request_timeout = int(timeout_env)


def _patch_aider_no_retry() -> None:
    try:
        from aider.exceptions import LiteLLMExceptions
    except ImportError:
        return
    for info in LiteLLMExceptions.exception_info.values():
        info.retry = False


_patch_aider_timeout()
_patch_aider_no_retry()
