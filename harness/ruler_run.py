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
UnboundLocalError instead) and get_output()'s own retry-forever loop (pinned,
unmodified) never stops, so a persistent endpoint error would spin the
container indefinitely. Two fixes, both applied here rather than in the
pinned files:

1. OpenAIClient.__call__ is replaced with a corrected version that performs
   the same request (same messages, same generation kwargs, same
   token-budget trim) but lets a real exception propagate, and additionally
   captures finish_reason and completion_tokens (the pinned __call__ only
   ever returns response.choices[0].message.content, discarding both).
   Neither field survives into <task>.jsonl (call_api.py's own
   outputs_parallel dict only ever holds
   index/pred/input/outputs/others/truncation/length), so this launcher
   appends one line per real request to a sibling <save_dir>/<task>.meta.jsonl,
   keyed by the sha256 of the exact prompt text sent (which call_api.py DOES
   write back verbatim as the "input" field of <task>.jsonl), so
   benchrun.suites.ruler.parse_ruler can join the two files back together
   without needing the sample's index (never passed down to __call__ by the
   pinned code).
2. OpenAIClient.process_batch is replaced by a version that counts failures
   across calls (get_output calls process_batch again, unmodified, on every
   failure) and aborts the whole process once either a CONSECUTIVE or a
   TOTAL budget is exceeded, instead of reusing Client.process_batch
   unboundedly. Both counters are read and written under _FAILURE_LOCK (a
   plain int mutated from up to RULER_THREADS concurrent threads with no
   lock would lose increments, and a flaky endpoint that never fails 3 times
   in a row would never trip a consecutive-only counter), and the TOTAL
   budget is set from args.num_samples in main(): a persistent or flaky
   endpoint that fails at least as many times as the number of samples
   requested has clearly not delivered value proportional to its cost, even
   if none of those failures were consecutive. os._exit(1), not sys.exit(1),
   terminates the whole process from whichever thread calls it: get_output()
   runs on its own worker Thread, and SystemExit raised there only
   terminates that thread, leaving the container silently exiting 0 with an
   incomplete prediction file.

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
# A persistent error (wrong port, endpoint returning 500 on every request,
# and so on) is not worth retrying beyond a handful of tries: the pinned
# call_api.py retries forever with no cap otherwise (see module docstring).
MAX_CONSECUTIVE_FAILURES = 3
# Minimum total-failure budget regardless of num_samples, so a tiny sample
# count still gets a meaningful number of tries before giving up.
MIN_TOTAL_FAILURES = 10

_META_LOCK = threading.Lock()
_META_PATH: pathlib.Path | None = None
_FAILURE_LOCK = threading.Lock()
_consecutive_failures = 0
_total_failures = 0
_max_total_failures = MIN_TOTAL_FAILURES


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
    `OpenAI(api_key=...)` with the SDK's own default retry (max_retries=2):
    a single _patched_call attempt can silently absorb up to 3 real HTTP
    failures before ever raising to _bounded_process_batch, undercounting
    real failures by up to 3x and delaying the total-failure budget (fix
    below) exactly when it matters most, a genuinely unreliable endpoint.
    max_retries=0 makes one _patched_call attempt map to exactly one real
    HTTP request, so _bounded_process_batch's counters reflect reality.
    """
    from openai import OpenAI

    self.client = OpenAI(api_key=self.openai_api_key, max_retries=0)


def _patched_call(self, prompt: str, **_kwargs):
    """Same request as the pinned OpenAIClient.__call__, minus the swallowed
    exception (module docstring, fix 1) and the in-place mutation of
    self.generation_kwargs (the pinned code reused the same dict across
    every call and shrank tokens_to_generate on it permanently after the
    first long prompt); plus recording finish_reason and completion_tokens
    to _META_PATH, keyed by the exact prompt text sent.
    """
    system_msg = []
    user_assistant_msgs = [{"role": "user", "content": prompt}]
    msgs = system_msg + user_assistant_msgs
    openai_length = self._count_tokens(msgs)
    request = dict(self.generation_kwargs)
    tokens_to_generate = request["tokens_to_generate"]
    tokens_to_generate_new = self.max_length - openai_length
    if tokens_to_generate_new < tokens_to_generate:
        tokens_to_generate = tokens_to_generate_new

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
    meta = {
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "finish_reason": choice.finish_reason,
        "completion_tokens": response.usage.completion_tokens if response.usage else None,
    }
    if _META_PATH is not None:
        with _META_LOCK:
            with open(_META_PATH, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(meta) + "\n")
    return {"text": [choice.message.content or ""]}


def _bounded_process_batch(self, prompts, **kwargs):
    import client_wrappers

    try:
        result = client_wrappers.Client.process_batch(self, prompts, **kwargs)
    except Exception as exc:
        global _consecutive_failures, _total_failures
        with _FAILURE_LOCK:
            _consecutive_failures += 1
            _total_failures += 1
            consecutive, total = _consecutive_failures, _total_failures
        if consecutive >= MAX_CONSECUTIVE_FAILURES or total >= _max_total_failures:
            reason = (
                f"{consecutive} consecutive failures" if consecutive >= MAX_CONSECUTIVE_FAILURES
                else f"{total} total failures (budget {_max_total_failures}, catches a flaky endpoint "
                     "that never fails 3 times in a row but never delivers either)"
            )
            print(
                f"ruler_run: process_batch failed, {reason} ({exc!r}); the pinned "
                "call_api.py retries forever otherwise, giving up instead",
                file=sys.stderr,
            )
            sys.stderr.flush()
            # sys.exit here would only terminate this worker thread (module
            # docstring, fix 2): os._exit terminates the whole process from
            # any thread, which is what "give up" must mean for a persistent
            # endpoint error.
            os._exit(1)
        raise
    with _FAILURE_LOCK:
        _consecutive_failures = 0
    return result


def main() -> None:
    global _META_PATH, _max_total_failures

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
    _max_total_failures = max(MIN_TOTAL_FAILURES, args.num_samples)

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
