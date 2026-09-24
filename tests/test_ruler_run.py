"""Direct unit tests for bench/harness/ruler_run.py's failure-budget logic
(adversarial review, round 4, 2026-09-24): _consecutive_failures was a plain
int mutated from up to RULER_THREADS concurrent threads with no lock, and
reset by any success, so a flaky endpoint that fails one request in two
never accumulated 3 CONSECUTIVE failures and the pinned retry-forever loop
was never stopped.

Imported by file path (importlib), the same posture as test_longbench_run.py:
ruler_run.py's own module-level code only touches sys.path and stdlib, the
heavy import (client_wrappers, from the pinned repo, container-only) happens
lazily inside _bounded_process_batch, so it is faked here via sys.modules
rather than requiring the container.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import threading
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
    # Module globals mutated by _bounded_process_batch; reset before every
    # test so one test's failures cannot leak into the next.
    monkeypatch.setattr(ruler_run, "_consecutive_failures", 0)
    monkeypatch.setattr(ruler_run, "_total_failures", 0)
    monkeypatch.setattr(ruler_run, "_max_total_failures", ruler_run.MIN_TOTAL_FAILURES)
    monkeypatch.setattr(ruler_run.os, "_exit", lambda code: (_ for _ in ()).throw(_Exit(code)))
    yield


def _install_fake_client_wrappers(process_batch_fn):
    """Injects a fake client_wrappers module (the pinned repo's own module,
    container-only) so _bounded_process_batch's lazy `import client_wrappers`
    resolves to a controlled stub instead of needing the real pinned repo."""
    fake_client_cls = type("Client", (), {"process_batch": staticmethod(process_batch_fn)})
    fake_module = types.SimpleNamespace(Client=fake_client_cls)
    sys.modules["client_wrappers"] = fake_module
    return fake_module


def _call_like_get_output(n_calls):
    """Mimics call_api.py's own get_output() (unmodified, pinned): a plain
    `while True: try: process_batch(...) except Exception: retry` loop with
    no cap of its own. _bounded_process_batch is the only thing that can
    stop it (by calling os._exit, faked here as _Exit). Runs n_calls
    successful-or-retried logical batches, or stops early on _Exit."""
    done = 0
    while done < n_calls:
        try:
            ruler_run._bounded_process_batch(object(), ["prompt"])
        except _Exit:
            raise
        except Exception:
            continue  # the pinned loop retries the same batch forever
        done += 1


def test_consecutive_failures_trip_the_breaker(monkeypatch):
    """A persistent failure (every call fails) trips on the CONSECUTIVE
    budget well before the total one, unchanged behavior from round 3."""
    def always_fails(self, prompts, **kwargs):
        raise RuntimeError("stub always fails")

    _install_fake_client_wrappers(always_fails)
    monkeypatch.setattr(ruler_run, "_max_total_failures", 100)  # never reached first

    with pytest.raises(_Exit) as exc_info:
        _call_like_get_output(1)
    assert exc_info.value.code == 1
    assert ruler_run._consecutive_failures == ruler_run.MAX_CONSECUTIVE_FAILURES


def test_flaky_endpoint_never_reaches_three_consecutive_still_trips_total(monkeypatch):
    """The real bug: a stub that fails exactly one request in two (fail,
    succeed, fail, succeed, ...) never accumulates MAX_CONSECUTIVE_FAILURES
    (3) consecutive failures, since every other call resets the counter to
    0. Before the fix, this endpoint was never stopped: the pinned
    retry-forever loop kept calling it, one retry per flaky sample, for the
    whole run. The TOTAL failure budget must trip independently of the
    consecutive one."""
    call_count = {"n": 0}

    def fails_one_in_two(self, prompts, **kwargs):
        call_count["n"] += 1
        if call_count["n"] % 2 == 0:
            raise RuntimeError("stub flaky failure")
        return ["ok"]

    _install_fake_client_wrappers(fails_one_in_two)
    monkeypatch.setattr(ruler_run, "_max_total_failures", 4)

    # call_api.py's own get_output() (unmodified) retries the SAME logical
    # batch immediately on failure, so with a global counter incrementing on
    # every raw attempt (retries included), the raw call sequence strictly
    # alternates success/failure: consecutive can never exceed 1. The total
    # budget (4) must still fire, well before 20 logical batches complete.
    with pytest.raises(_Exit) as exc_info:
        _call_like_get_output(20)
    assert exc_info.value.code == 1
    assert ruler_run._consecutive_failures < ruler_run.MAX_CONSECUTIVE_FAILURES
    assert ruler_run._total_failures >= 4


def test_success_resets_consecutive_but_not_total(monkeypatch):
    def fails_then_succeeds(self, prompts, **kwargs):
        fails_then_succeeds.calls += 1
        if fails_then_succeeds.calls <= 2:
            raise RuntimeError("stub fails twice")
        return ["ok"]

    fails_then_succeeds.calls = 0
    _install_fake_client_wrappers(fails_then_succeeds)
    monkeypatch.setattr(ruler_run, "_max_total_failures", 100)

    for _ in range(2):
        with pytest.raises(RuntimeError):
            ruler_run._bounded_process_batch(object(), ["prompt"])
    assert ruler_run._consecutive_failures == 2
    assert ruler_run._total_failures == 2

    ruler_run._bounded_process_batch(object(), ["prompt"])  # succeeds
    assert ruler_run._consecutive_failures == 0
    assert ruler_run._total_failures == 2  # total is never reset


def test_failure_counters_are_thread_safe_under_the_lock(monkeypatch):
    """Round 4 review: an unlocked int mutated from up to RULER_THREADS (4)
    concurrent threads can lose an increment (read-increment-write race).
    Every real caller in call_api.py's get_output() runs on its own worker
    thread; simulate that with N threads all hitting a failing endpoint at
    once and assert every failure is counted, none lost."""
    def always_fails(self, prompts, **kwargs):
        raise RuntimeError("stub always fails")

    _install_fake_client_wrappers(always_fails)
    # A high consecutive budget too: this test is about the LOCK (no lost
    # increments under concurrent access), not about which budget trips
    # first. With every call failing, the real os._exit would fire the
    # first time either budget is reached; the fake _Exit only unwinds the
    # thread that raised it here, so several threads may independently
    # observe a trip, which does not affect the counter this test checks.
    monkeypatch.setattr(ruler_run, "_max_total_failures", 10_000)
    monkeypatch.setattr(ruler_run, "MAX_CONSECUTIVE_FAILURES", 10_000)

    n_threads = 20
    barrier = threading.Barrier(n_threads)
    exits = []

    def worker():
        barrier.wait()
        try:
            ruler_run._bounded_process_batch(object(), ["prompt"])
        except _Exit as exc:
            exits.append(exc)
        except Exception:
            pass  # expected: re-raised by _bounded_process_batch when not tripped

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not exits  # budget of 10_000 must not trip for 20 failures
    assert ruler_run._total_failures == n_threads
