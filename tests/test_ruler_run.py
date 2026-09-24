"""Direct unit tests for bench/harness/ruler_run.py's client-side timeout and
zero-retry fix (proven live on LiveCodeBench, 2026-09-24): a client whose own
timeout is shorter than the server's real per-request budget abandons the
request in place and its resend queues up behind it, losing the real answer.

_patched_call catches every real SDK error and returns an empty, recorded
prediction on the first and only attempt (no resend), so call_api.py's own
pinned `while True: try: process_batch() except Exception: continue` never
retries a batch (its try always succeeds). Two conditions still make it
raise instead of recording a failed item (the coordinator's correction,
2026-09-24: a bad question must not crash the whole run for every OTHER
question, but a dead endpoint must not silently score a full run of empty
answers as real zeros either): a failure before this process has ever
received a real response, and MAX_CONSECUTIVE_FAILURES failures in a row.
_bounded_process_batch is a thin passthrough that only ever sees a
genuinely unexpected exception (a bug, not a request failure: those are
handled by _patched_call itself) and gives up immediately instead of
retrying that either.

Imported by file path (importlib), the same posture as test_longbench_run.py:
ruler_run.py's own module-level code only touches sys.path and stdlib, the
heavy imports (client_wrappers, openai: the pinned repo and the real SDK,
container-only) happen lazily inside the patched functions, so both are
faked here via sys.modules rather than requiring the container.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import types

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ruler_run", pathlib.Path(__file__).parents[1] / "harness" / "ruler_run.py",
)
ruler_run = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ruler_run)


class _Exit(BaseException):
    """Stands in for os._exit(1): raised instead of actually killing the
    test process, real exit code carried on the exception."""

    def __init__(self, code):
        self.code = code


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    monkeypatch.setattr(ruler_run.os, "_exit", lambda code: (_ for _ in ()).throw(_Exit(code)))
    monkeypatch.setattr(ruler_run, "_ever_succeeded", False)
    monkeypatch.setattr(ruler_run, "_consecutive_failures", 0)
    yield


class _FakeOpenAIError(Exception):
    pass


def _install_fake_openai_errors():
    fake_module = types.SimpleNamespace(OpenAIError=_FakeOpenAIError)
    sys.modules["openai"] = fake_module
    return fake_module


class _FakeChoice:
    def __init__(self, content, finish_reason):
        self.finish_reason = finish_reason
        self.message = types.SimpleNamespace(content=content)


class _FakeUsage:
    def __init__(self, completion_tokens):
        self.completion_tokens = completion_tokens


class _FakeResponse:
    def __init__(self, content="hello", finish_reason="stop", completion_tokens=3):
        self.choices = [_FakeChoice(content, finish_reason)]
        self.usage = _FakeUsage(completion_tokens)


class _FakeCompletions:
    def __init__(self, fn):
        self._fn = fn

    def create(self, **kwargs):
        return self._fn(**kwargs)


class _FakeChat:
    def __init__(self, fn):
        self.completions = _FakeCompletions(fn)


class _FakeClient:
    def __init__(self, fn):
        self.chat = _FakeChat(fn)


def _self_with_client(fn, tokens_to_generate=100):
    self = types.SimpleNamespace()
    self.client = _FakeClient(fn)
    self.model_name = "bench-model"
    self.max_length = ruler_run.MAX_LENGTH
    self.generation_kwargs = {
        "tokens_to_generate": tokens_to_generate,
        "temperature": 0.0,
        "random_seed": 0,
        "top_p": 1.0,
        "stop": [],
    }
    self._count_tokens = lambda msgs: 10
    return self


def test_patched_call_returns_the_real_response_on_success(tmp_path, monkeypatch):
    _install_fake_openai_errors()
    monkeypatch.setattr(ruler_run, "_META_PATH", tmp_path / "task.meta.jsonl")

    def fake_create(**kwargs):
        return _FakeResponse(content="42", finish_reason="stop", completion_tokens=7)

    self = _self_with_client(fake_create)
    result = ruler_run._patched_call(self, "some prompt")
    assert result == {"text": ["42"]}
    meta = json.loads(ruler_run._META_PATH.read_text().strip())
    assert meta["finish_reason"] == "stop"
    assert meta["completion_tokens"] == 7
    assert ruler_run._ever_succeeded is True
    assert ruler_run._consecutive_failures == 0


def test_patched_call_fails_fast_when_no_response_received_yet(tmp_path, monkeypatch):
    """The first exception, before this process has ever received a real
    response, means the endpoint itself looks unreachable: the whole run
    must give up, not spend the rest of its sample budget on empty answers
    silently scored as real zeros."""
    _install_fake_openai_errors()
    monkeypatch.setattr(ruler_run, "_META_PATH", tmp_path / "task.meta.jsonl")

    def fake_create(**kwargs):
        raise sys.modules["openai"].OpenAIError("connection refused")

    self = _self_with_client(fake_create)
    with pytest.raises(_Exit) as exc_info:
        ruler_run._patched_call(self, "some prompt")
    assert exc_info.value.code == 1


def test_patched_call_records_a_single_failure_after_a_real_success(tmp_path, monkeypatch):
    """Once the endpoint has proven it works, one isolated failure (well
    under the consecutive budget) is recorded as one failed item, not a
    reason to abort the run: exactly one attempt, no resend."""
    _install_fake_openai_errors()
    monkeypatch.setattr(ruler_run, "_META_PATH", tmp_path / "task.meta.jsonl")
    calls = {"n": 0}

    def fake_create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(content="ok", finish_reason="stop", completion_tokens=1)
        raise sys.modules["openai"].OpenAIError("timed out")

    self = _self_with_client(fake_create)
    ruler_run._patched_call(self, "warm-up prompt")  # establishes _ever_succeeded

    result = ruler_run._patched_call(self, "some prompt")
    assert result == {"text": [""]}
    assert calls["n"] == 2  # exactly one attempt for the failing call, no retry
    meta_lines = ruler_run._META_PATH.read_text().strip().splitlines()
    assert json.loads(meta_lines[-1])["finish_reason"] == "error"
    assert json.loads(meta_lines[-1])["completion_tokens"] is None


def test_patched_call_fails_fast_after_max_consecutive_failures(tmp_path, monkeypatch):
    """A server that answered once, then goes dead, must not be allowed to
    silently turn every remaining sample into a scored 0: MAX_CONSECUTIVE_FAILURES
    failures in a row aborts the whole run instead."""
    _install_fake_openai_errors()
    monkeypatch.setattr(ruler_run, "_META_PATH", tmp_path / "task.meta.jsonl")
    calls = {"n": 0}

    def fake_create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(content="ok", finish_reason="stop", completion_tokens=1)
        raise sys.modules["openai"].OpenAIError("connection reset")

    self = _self_with_client(fake_create)
    ruler_run._patched_call(self, "warm-up prompt")  # establishes _ever_succeeded

    for _ in range(ruler_run.MAX_CONSECUTIVE_FAILURES - 1):
        result = ruler_run._patched_call(self, "some prompt")
        assert result == {"text": [""]}  # recorded, not yet fatal

    with pytest.raises(_Exit) as exc_info:
        ruler_run._patched_call(self, "some prompt")
    assert exc_info.value.code == 1


def test_patched_call_success_resets_the_consecutive_counter(tmp_path, monkeypatch):
    """A flaky-but-working endpoint (fails, then succeeds again) must never
    trip the circuit breaker just from accumulated non-consecutive failures."""
    _install_fake_openai_errors()
    monkeypatch.setattr(ruler_run, "_META_PATH", tmp_path / "task.meta.jsonl")
    calls = {"n": 0}

    def fake_create(**kwargs):
        calls["n"] += 1
        # succeed, fail, succeed, fail, succeed, fail, succeed: never two
        # failures in a row, well past MAX_CONSECUTIVE_FAILURES total fails.
        if calls["n"] % 2 == 1:
            return _FakeResponse(content="ok", finish_reason="stop", completion_tokens=1)
        raise sys.modules["openai"].OpenAIError("flaky")

    self = _self_with_client(fake_create)
    for _ in range(6):
        ruler_run._patched_call(self, "some prompt")  # must never raise
    assert calls["n"] == 6


def test_patched_create_client_sets_timeout_and_zero_retries(monkeypatch):
    seen = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    sys.modules["openai"] = types.SimpleNamespace(OpenAI=_FakeOpenAI)
    monkeypatch.setenv("BENCH_REQUEST_TIMEOUT_S", "222")

    self = types.SimpleNamespace(openai_api_key="x")
    ruler_run._patched_create_client(self)
    assert seen["max_retries"] == 0
    assert seen["timeout"] == 222.0


def test_patched_create_client_omits_timeout_when_env_is_unset(monkeypatch):
    seen = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    sys.modules["openai"] = types.SimpleNamespace(OpenAI=_FakeOpenAI)
    monkeypatch.delenv("BENCH_REQUEST_TIMEOUT_S", raising=False)

    self = types.SimpleNamespace(openai_api_key="x")
    ruler_run._patched_create_client(self)
    assert seen["max_retries"] == 0
    assert "timeout" not in seen


def _install_fake_client_wrappers(process_batch_fn):
    """Injects a fake client_wrappers module (the pinned repo's own module,
    container-only) so _bounded_process_batch's lazy `import client_wrappers`
    resolves to a controlled stub instead of needing the real pinned repo."""
    fake_client_cls = type("Client", (), {"process_batch": staticmethod(process_batch_fn)})
    fake_module = types.SimpleNamespace(Client=fake_client_cls)
    sys.modules["client_wrappers"] = fake_module
    return fake_module


def test_bounded_process_batch_passes_through_on_success():
    _install_fake_client_wrappers(lambda self, prompts, **kwargs: ["ok"])
    assert ruler_run._bounded_process_batch(object(), ["prompt"]) == ["ok"]


def test_bounded_process_batch_gives_up_immediately_with_zero_retries():
    """An unexpected exception (not a real API failure: _patched_call never
    lets one of those propagate any more) is not retried even once: the
    pinned get_output() loop must never get a chance to resend."""
    calls = {"n": 0}

    def always_raises(self, prompts, **kwargs):
        calls["n"] += 1
        raise RuntimeError("unexpected bug, not a request failure")

    _install_fake_client_wrappers(always_raises)
    with pytest.raises(_Exit) as exc_info:
        ruler_run._bounded_process_batch(object(), ["prompt"])
    assert exc_info.value.code == 1
    assert calls["n"] == 1  # zero retries
