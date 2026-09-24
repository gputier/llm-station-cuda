"""Direct unit tests for bench/harness/lcb_run.py's zero-retry and fail-fast
fix (the coordinator's correction, 2026-09-24, on top of the client-side
timeout fix proven live on LiveCodeBench the same day): a request that
fails must become ONE failed problem, not a crashed run (LiveCodeBench runs
--multiprocess 1, so an uncaught exception here would lose every OTHER
problem's real result along with the failed one), except when the failure
means the endpoint itself is unreachable rather than one bad question: no
response received yet in this run, or MAX_CONSECUTIVE_FAILURES in a row.

Imported by file path (importlib), the same posture as test_ruler_run.py and
test_longbench_run.py. lcb_run.py's own module-level code imports `datasets`
and `lcb_runner` (the pinned harness and its dataset loader, container-only),
unlike ruler_run.py/longbench_run.py which keep those imports lazy inside
main(): both are faked here via sys.modules before the module is executed,
enough to satisfy the import statements without needing the container.
_no_retry_run_single itself never touches either fake beyond what is stubbed
(it only calls type(self).client.chat.completions.create and reads
self.args.n), so the stubs' bodies do not need to do anything real.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

import pytest


def _install_fake_lcb_runner_package():
    datasets_mod = types.ModuleType("datasets")
    datasets_mod.load_dataset = lambda *a, **kw: None
    sys.modules["datasets"] = datasets_mod

    lcb_runner_mod = types.ModuleType("lcb_runner")
    sys.modules["lcb_runner"] = lcb_runner_mod

    benchmarks_mod = types.ModuleType("lcb_runner.benchmarks")
    sys.modules["lcb_runner.benchmarks"] = benchmarks_mod
    code_generation_mod = types.ModuleType("lcb_runner.benchmarks.code_generation")
    code_generation_mod.CodeGenerationProblem = type("CodeGenerationProblem", (), {})
    sys.modules["lcb_runner.benchmarks.code_generation"] = code_generation_mod

    lm_styles_mod = types.ModuleType("lcb_runner.lm_styles")
    lm_styles_mod.LanguageModel = type("LanguageModel", (), {"__init__": lambda self, *a, **kw: None})
    lm_styles_mod.LanguageModelList = []
    lm_styles_mod.LanguageModelStore = {}
    lm_styles_mod.LMStyle = types.SimpleNamespace(OpenAIChat="openai_chat")
    sys.modules["lcb_runner.lm_styles"] = lm_styles_mod

    runner_mod = types.ModuleType("lcb_runner.runner")
    scenario_router_mod = types.ModuleType("lcb_runner.runner.scenario_router")
    runner_mod.scenario_router = scenario_router_mod
    sys.modules["lcb_runner.runner"] = runner_mod
    sys.modules["lcb_runner.runner.scenario_router"] = scenario_router_mod


_install_fake_lcb_runner_package()

_SPEC = importlib.util.spec_from_file_location(
    "lcb_run", pathlib.Path(__file__).parents[1] / "harness" / "lcb_run.py",
)
lcb_run = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lcb_run)


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    monkeypatch.setattr(lcb_run, "_ever_succeeded", False)
    monkeypatch.setattr(lcb_run, "_consecutive_failures", 0)
    yield


class _FakeChoice:
    def __init__(self, content):
        self.message = types.SimpleNamespace(content=content)


class _FakeResponse:
    def __init__(self, contents):
        self.choices = [_FakeChoice(c) for c in contents]


class _FakeCompletions:
    def __init__(self, fn):
        self._fn = fn

    def create(self, **kwargs):
        return self._fn(**kwargs)


class _FakeClient:
    def __init__(self, fn):
        self.chat = types.SimpleNamespace(completions=_FakeCompletions(fn))


def _self_with_client(fn, n=1):
    fake_runner_cls = type("FakeRunner", (), {"client": _FakeClient(fn)})
    self = fake_runner_cls()
    self.args = types.SimpleNamespace(n=n)
    self.client_kwargs = {}
    return self


def test_run_single_returns_the_real_completions_on_success():
    def fake_create(**kwargs):
        return _FakeResponse(["def solve(): pass"])

    self = _self_with_client(fake_create)
    result = lcb_run._no_retry_run_single(self, [{"role": "user", "content": "solve this"}])
    assert result == ["def solve(): pass"]
    assert lcb_run._ever_succeeded is True
    assert lcb_run._consecutive_failures == 0


def test_run_single_fails_fast_when_no_response_received_yet():
    """The first exception, before this process has ever received a real
    response, means the endpoint itself looks unreachable: the whole run
    must give up rather than silently score every remaining problem 0 from
    an empty completion."""
    def fake_create(**kwargs):
        raise ConnectionError("connection refused")

    self = _self_with_client(fake_create)
    with pytest.raises(RuntimeError, match="no response received yet"):
        lcb_run._no_retry_run_single(self, [{"role": "user", "content": "solve this"}])


def test_run_single_records_a_single_failure_after_a_real_success():
    """Once the endpoint has proven it works, one isolated failure (well
    under the consecutive budget) becomes one failed problem, not a reason
    to lose every other problem's real result: exactly one attempt, no
    resend, no exception."""
    calls = {"n": 0}

    def fake_create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(["ok"])
        raise TimeoutError("timed out")

    self = _self_with_client(fake_create)
    lcb_run._no_retry_run_single(self, [{"role": "user", "content": "warm-up"}])  # establishes success

    result = lcb_run._no_retry_run_single(self, [{"role": "user", "content": "solve this"}])
    assert result == [""]  # one failed problem, matches self.args.n
    assert calls["n"] == 2  # exactly one attempt for the failing call, no retry


def test_run_single_fails_fast_after_max_consecutive_failures():
    """A server that answered once, then goes dead, must not be allowed to
    silently turn every remaining problem into a scored 0."""
    calls = {"n": 0}

    def fake_create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(["ok"])
        raise ConnectionResetError("connection reset")

    self = _self_with_client(fake_create)
    lcb_run._no_retry_run_single(self, [{"role": "user", "content": "warm-up"}])

    for _ in range(lcb_run.MAX_CONSECUTIVE_FAILURES - 1):
        result = lcb_run._no_retry_run_single(self, [{"role": "user", "content": "solve this"}])
        assert result == [""]  # recorded, not yet fatal

    with pytest.raises(RuntimeError, match="consecutive failures"):
        lcb_run._no_retry_run_single(self, [{"role": "user", "content": "solve this"}])


def test_run_single_success_resets_the_consecutive_counter():
    """A flaky-but-working endpoint must never trip the circuit breaker
    just from accumulated non-consecutive failures."""
    calls = {"n": 0}

    def fake_create(**kwargs):
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            return _FakeResponse(["ok"])
        raise TimeoutError("flaky")

    self = _self_with_client(fake_create)
    for _ in range(6):
        lcb_run._no_retry_run_single(self, [{"role": "user", "content": "x"}])  # must never raise
    assert calls["n"] == 6


def test_disable_harness_retries_sets_zero_sdk_retries(monkeypatch):
    seen = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    fake_client = types.SimpleNamespace(api_key="x")
    fake_oai_runner_cls = type("OpenAIRunner", (), {"client": fake_client})
    fake_oai_runner_mod = types.ModuleType("lcb_runner.runner.oai_runner")
    fake_oai_runner_mod.OpenAIRunner = fake_oai_runner_cls
    sys.modules["lcb_runner.runner.oai_runner"] = fake_oai_runner_mod
    sys.modules["openai"] = types.SimpleNamespace(OpenAI=_FakeOpenAI)

    lcb_run.disable_harness_retries()
    assert seen["max_retries"] == 0
    assert seen["api_key"] == "x"
    assert fake_oai_runner_cls._run_single is lcb_run._no_retry_run_single


def test_apply_request_timeout_env_defaults_openai_timeout(monkeypatch):
    monkeypatch.setenv("BENCH_REQUEST_TIMEOUT_S", "321")
    assert lcb_run.apply_request_timeout_env(["--n", "1"]) == ["--n", "1", "--openai_timeout", "321"]


def test_apply_request_timeout_env_never_overrides_an_explicit_flag(monkeypatch):
    monkeypatch.setenv("BENCH_REQUEST_TIMEOUT_S", "321")
    rest = ["--openai_timeout", "999"]
    assert lcb_run.apply_request_timeout_env(rest) == rest
