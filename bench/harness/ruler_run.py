"""Launcher for the pinned NVIDIA/RULER harness against an OpenAI-compatible
endpoint (bench/harness/README.md, RULER section, rule 3: no fork of the
pinned repo).

client_wrappers.OpenAIClient.__init__ looks up the requested model name in a
small hardcoded dict of OpenAI/Azure model names and raises KeyError for any
other name, our served alias included. This launcher replaces only that
constructor at runtime, before handing off to the unmodified
pred/call_api.py::main() through runpy, the same posture as lcb_run.py's
registration of a served alias for LiveCodeBench.

OpenAIClient also does not inherit from the Client(abc.ABC) base class and
defines no process_batch, which call_api.py's get_output() calls
unconditionally (llm.process_batch(prompts=...)); the pinned repo's own
--server_type openai path is unusable as shipped without a patch.

OpenAIClient.__call__ swallows every real exception (its except branch falls
through to `return response` with response never assigned, raising
UnboundLocalError instead), and get_output() (pinned, unmodified: `while
True: try: llm.process_batch(...); break except Exception: continue`)
retries the SAME batch on any exception with no cap at all: proven live on
LiveCodeBench, 2026-09-24, a client that resends a request the server may
still be decoding abandons the first attempt in place (--parallel 1) and the
resend queues up behind it, losing the real answer. Two fixes, both applied
here rather than in the pinned files:

1. OpenAIClient.__call__ is replaced with a corrected version that performs
   the same request (same messages, same generation kwargs, same
   token-budget trim, client-side timeout from BENCH_REQUEST_TIMEOUT_S,
   SDK max_retries=0), but instead of letting a real exception propagate
   into get_output()'s retry-forever loop, it catches every openai SDK error
   (timeout, disconnection, HTTP error) and returns an empty prediction:
   get_output()'s try always succeeds on the first pass, so the pinned loop
   never retries, and the failed request is recorded as a failed item
   instead of silently resent. A non-SDK exception (a bug in this launcher,
   not a request failure) is NOT caught here and is left to reach fix 2.
   finish_reason and completion_tokens (the pinned __call__ only ever
   returns response.choices[0].message.content, discarding both; "error" and
   None for a failed request) do not survive into <task>.jsonl (call_api.py's
   own outputs_parallel dict only ever holds
   index/pred/input/outputs/others/truncation/length), so this launcher
   appends one line per real request to a sibling <save_dir>/<task>.meta.jsonl,
   keyed by the sha256 of the exact prompt text sent (which call_api.py DOES
   write back verbatim as the "input" field of <task>.jsonl), so
   benchrun.suites.ruler.parse_ruler can join the two files back together
   without needing the sample's index (never passed down to __call__ by the
   pinned code).
2. OpenAIClient.process_batch is replaced by a thin wrapper that still calls
   the pinned Client.process_batch unmodified, but if that ever raises
   anyway (fix 1 made every real API/network failure a non-raising, recorded
   result, so this can only be an unexpected bug, not a slow or unreachable
   endpoint), it gives up immediately instead of letting get_output() retry
   the same batch forever: os._exit(1), not sys.exit(1), terminates the
   whole process from whichever thread calls it (get_output() runs on its
   own worker Thread, and SystemExit raised there only terminates that
   thread, leaving the container silently exiting 0 with an incomplete
   prediction file).

Two steps per (task, length): data/prepare.py (unmodified, run as a real
subprocess: it shells out to the task generator itself and never imports the
nemo stub) writes the synthetic dataset, then pred/call_api.py (patched
in-process) calls the endpoint and writes one prediction per line to
<save_dir>/<task>.jsonl.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import runpy
import subprocess
import sys
import threading

SCRIPTS_DIR = pathlib.Path("/ruler/scripts")
sys.path.insert(0, str(SCRIPTS_DIR / "pred"))

# Generous ceiling for the openai-compatible path: prepare.py already sizes
# the haystack to --max_seq_length, so the real prompt never approaches this.
MAX_LENGTH = 400000
# Not a retry budget (module docstring, fix 1): a persistent or dead
# endpoint aborts the whole run after this many CONSECUTIVE single-attempt
# request failures, instead of scoring every remaining sample a silent 0.
MAX_CONSECUTIVE_FAILURES = 3

_META_LOCK = threading.Lock()
_META_PATH: pathlib.Path | None = None
# Read and written under _STATE_LOCK: _patched_call runs concurrently from
# up to RULER_THREADS worker threads (Client.process_batch's own
# ThreadPoolExecutor), so a plain int/bool would lose updates to a race.
_STATE_LOCK = threading.Lock()
_ever_succeeded = False
_consecutive_failures = 0


def _patched_openai_init(self, model_name, **generation_kwargs):
    import tiktoken

    self.openai_api_key = os.environ.get("OPENAI_API_KEY", "x")
    self.azure_api_id = os.environ.get("AZURE_API_ID", "")
    self.azure_api_secret = os.environ.get("AZURE_API_SECRET", "")
    self.azure_api_endpoint = os.environ.get("AZURE_API_ENDPOINT", "")
    self.model_name = model_name
    self.encoding = tiktoken.get_encoding("cl100k_base")
    self.max_length = MAX_LENGTH
    self.generation_kwargs = generation_kwargs
    self._create_client()


def _patched_create_client(self):
    """The pinned _create_client (client_wrappers.Client, unmodified) builds
    `OpenAI(api_key=...)` with the SDK's own default timeout (600 s) and
    retry (max_retries=2). BENCH_REQUEST_TIMEOUT_S (set by
    bench/benchrun/suites/ruler.py from the shared
    benchrun.suites.request_timeout_s) becomes the client's own timeout, and
    max_retries=0 makes one _patched_call attempt map to exactly one real
    HTTP request: the SDK never silently resends on its own before
    _patched_call's own error handling (fix 1, module docstring) ever sees
    the failure.
    """
    from openai import OpenAI

    timeout_env = os.environ.get("BENCH_REQUEST_TIMEOUT_S")
    kwargs = {"api_key": self.openai_api_key, "max_retries": 0}
    if timeout_env is not None:
        kwargs["timeout"] = float(timeout_env)
    self.client = OpenAI(**kwargs)


def _patched_call(self, prompt: str, **_kwargs):
    """Same request as the pinned OpenAIClient.__call__, minus the swallowed
    exception and the in-place mutation of self.generation_kwargs (the
    pinned code reused the same dict across every call and shrank
    tokens_to_generate on it permanently after the first long prompt); plus
    recording finish_reason and completion_tokens to _META_PATH, keyed by
    the exact prompt text sent.

    A real SDK error (timeout, disconnection, HTTP error) is caught here,
    not let through to get_output()'s retry-forever loop (module docstring,
    fix 1): the pinned harness's own resend-on-any-exception is exactly the
    defect proven live on LiveCodeBench, so this returns a recorded, empty
    prediction instead of raising, making get_output()'s try succeed on the
    first and only attempt every time, EXCEPT when the fail-fast predicate
    below trips (no response received yet in this run, or
    MAX_CONSECUTIVE_FAILURES in a row): a dead endpoint must fail the whole
    run, not silently produce every remaining sample as a scored 0.
    """
    import openai

    global _ever_succeeded, _consecutive_failures

    system_msg = []
    user_assistant_msgs = [{"role": "user", "content": prompt}]
    msgs = system_msg + user_assistant_msgs
    openai_length = self._count_tokens(msgs)
    request = dict(self.generation_kwargs)
    tokens_to_generate = request["tokens_to_generate"]
    tokens_to_generate_new = self.max_length - openai_length
    if tokens_to_generate_new < tokens_to_generate:
        tokens_to_generate = tokens_to_generate_new

    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    try:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=msgs,
            max_tokens=tokens_to_generate,
            temperature=request["temperature"],
            seed=request["random_seed"],
            top_p=request["top_p"],
            stop=request["stop"],
        )
        choice = response.choices[0]
        finish_reason = choice.finish_reason
        completion_tokens = response.usage.completion_tokens if response.usage else None
        pred_text = choice.message.content or ""
        with _STATE_LOCK:
            _ever_succeeded = True
            _consecutive_failures = 0
    except openai.OpenAIError as exc:
        with _STATE_LOCK:
            _consecutive_failures += 1
            fail_fast = (not _ever_succeeded) or (_consecutive_failures >= MAX_CONSECUTIVE_FAILURES)
            reason = "no response received yet" if not _ever_succeeded else f"{_consecutive_failures} consecutive failures"
        print(f"ruler_run: request failed (prompt_sha256={prompt_sha256}), no retry "
              f"({type(exc).__name__}: {exc}), recorded as a failed item", file=sys.stderr)
        if fail_fast:
            print(f"ruler_run: giving up, {reason} ({type(exc).__name__}: {exc})", file=sys.stderr)
            sys.stderr.flush()
            # sys.exit here would only terminate this worker thread: get_output()
            # runs its process_batch call from a dedicated worker Thread, and
            # _patched_call itself runs inside Client.process_batch's own
            # ThreadPoolExecutor workers. os._exit terminates the whole
            # process from any thread, which is what "give up" must mean.
            os._exit(1)
        finish_reason = "error"
        completion_tokens = None
        pred_text = ""

    meta = {
        "prompt_sha256": prompt_sha256,
        "finish_reason": finish_reason,
        "completion_tokens": completion_tokens,
    }
    if _META_PATH is not None:
        with _META_LOCK:
            with open(_META_PATH, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(meta) + "\n")
    return {"text": [pred_text]}


def _bounded_process_batch(self, prompts, **kwargs):
    """Thin passthrough to the pinned Client.process_batch: every real
    API/network failure is already caught and recorded by _patched_call
    (fix 1, module docstring), so this only ever sees an unexpected bug, not
    a slow or unreachable endpoint. Giving up immediately (fix 2, module
    docstring) is what "zero retries" must mean for that case too, instead
    of letting get_output() retry the same batch forever.
    """
    import client_wrappers

    try:
        return client_wrappers.Client.process_batch(self, prompts, **kwargs)
    except Exception as exc:
        print(f"ruler_run: process_batch failed unexpectedly ({exc!r}), giving up (zero retries)",
              file=sys.stderr)
        sys.stderr.flush()
        # sys.exit here would only terminate this worker thread (module
        # docstring, fix 2): os._exit terminates the whole process from any
        # thread, which is what "give up" must mean here.
        os._exit(1)


def main() -> None:
    global _META_PATH

    parser = argparse.ArgumentParser()
    parser.add_argument("model_alias")
    parser.add_argument("--task", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--save-dir", required=True)
    parser.add_argument("--max-seq-length", type=int, required=True)
    parser.add_argument("--num-samples", type=int, required=True)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()

    os.environ["OPENAI_BASE_URL"] = args.base_url
    os.environ.setdefault("OPENAI_API_KEY", "x")
    os.environ.setdefault("AZURE_API_ID", "")
    os.environ.setdefault("AZURE_API_SECRET", "")
    os.environ.setdefault("AZURE_API_ENDPOINT", "")

    save_dir = pathlib.Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    _META_PATH = save_dir / f"{args.task}.meta.jsonl"
    if _META_PATH.exists():
        _META_PATH.unlink()

    subprocess.run(
        [
            "python", str(SCRIPTS_DIR / "data" / "prepare.py"),
            "--save_dir", args.data_dir,
            "--benchmark", "synthetic",
            "--task", args.task,
            "--tokenizer_path", "cl100k_base",
            "--tokenizer_type", "openai",
            "--max_seq_length", str(args.max_seq_length),
            "--model_template_type", "base",
            "--num_samples", str(args.num_samples),
        ],
        check=True,
        cwd=str(SCRIPTS_DIR / "data"),
    )

    import client_wrappers

    client_wrappers.OpenAIClient.__init__ = _patched_openai_init
    client_wrappers.OpenAIClient._create_client = _patched_create_client
    client_wrappers.OpenAIClient.__call__ = _patched_call
    client_wrappers.OpenAIClient.process_batch = _bounded_process_batch

    sys.argv = [
        "call_api.py",
        "--data_dir", args.data_dir,
        "--save_dir", args.save_dir,
        "--benchmark", "synthetic",
        "--task", args.task,
        "--server_type", "openai",
        "--model_name_or_path", args.model_alias,
        "--temperature", "0.0",
        "--top_k", "32",
        "--top_p", "1.0",
        "--batch_size", "1",
    ]
    runpy.run_path(str(SCRIPTS_DIR / "pred" / "call_api.py"), run_name="__main__")


if __name__ == "__main__":
    main()
