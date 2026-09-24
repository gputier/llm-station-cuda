import dataclasses
import json
import pathlib
import shutil

import pytest

from benchrun.config import load_config
from benchrun.runner import SuiteContext
from benchrun.suites import livebench, request_timeout_s
from benchrun.suites.livebench import LiveBenchSuite, parse_livebench

# Cut from a real run (spark on the 99, live_bench/reasoning/zebra_puzzle,
# release 2026-06-25, 3 questions). No problem statements or model answers
# in this file to blank: the judgment schema only carries question_id,
# task, model, score and error metadata.
FIX = pathlib.Path(__file__).parent / "fixtures" / "livebench_judgment_sample.jsonl"
CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")


def test_parse_livebench_one_row_per_question():
    rows = parse_livebench(FIX)
    assert len(rows) == 3
    assert all(r["passed"] in (0, 1) and r["item_id"] for r in rows)


def test_parse_livebench_reads_real_scores():
    # Values read by hand in the fixture: score 1.0, 0, 1.0. The middle
    # question hit spark's 7000-token sampling ceiling mid-reasoning
    # (finish_reason "length", no content), which the harness itself
    # records as eval_status "api_error" and scores 0.
    rows = parse_livebench(FIX)
    assert [r["passed"] for r in rows] == [1, 0, 1]


def test_run_reads_the_file_the_harness_writes(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="spark")
    ctx = SuiteContext("http://gw:8081", cfg, 1, tmp_path, tmp_path)
    calls = []
    seen_timeouts = []

    def fake_run(cmd, check, **kwargs):
        calls.append(cmd)
        seen_timeouts.append(kwargs.get("timeout"))
        # Path observed in the real run: nested under the harness's own
        # inner livebench/livebench package directory, not the repo root.
        target = (
            tmp_path / "rep1-livebench" / "data"
            / "live_bench" / "reasoning" / "zebra_puzzle" / "model_judgment"
            / "ground_truth_judgment.jsonl"
        )
        target.parent.mkdir(parents=True)
        shutil.copy(FIX, target)

    monkeypatch.setattr(livebench.subprocess, "run", fake_run)
    rows = LiveBenchSuite(categories=("reasoning",)).run(ctx)
    assert [r["passed"] for r in rows] == [1, 0, 1]
    cmd = calls[0]
    assert cmd[cmd.index("--api-base") + 1] == "http://gw:8081/v1"
    assert cmd[cmd.index("--livebench-release-option") + 1] == "2026-06-25"
    assert f"{livebench.HF_CACHE_VOLUME}:/root/.cache/huggingface" in cmd
    assert seen_timeouts[0] is not None and seen_timeouts[0] > 0
    assert seen_timeouts[0] == livebench.subprocess_timeout_s(("reasoning",), cfg.sampling.get("max_tokens", 0))
    expected_timeout = request_timeout_s(cfg.sampling.get("max_tokens", 0), livebench.PROMPT_TOKENS_ESTIMATE)
    assert f"BENCH_REQUEST_TIMEOUT_S={expected_timeout}" in cmd


def test_run_fails_when_a_category_grades_nothing(tmp_path, monkeypatch):
    # Reasoning has a judgment file, math has none: a retired task or a
    # wrong release must not pass as a smaller score.
    cfg = dataclasses.replace(CFG, model="spark")
    ctx = SuiteContext("http://gw:8081", cfg, 1, tmp_path, tmp_path)

    def fake_run(cmd, check, **kwargs):
        target = (
            tmp_path / "rep1-livebench" / "data"
            / "live_bench" / "reasoning" / "zebra_puzzle" / "model_judgment"
            / "ground_truth_judgment.jsonl"
        )
        target.parent.mkdir(parents=True)
        shutil.copy(FIX, target)

    monkeypatch.setattr(livebench.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="category math graded no question"):
        LiveBenchSuite(categories=("reasoning", "math")).run(ctx)


def test_category_with_underscore_is_rejected():
    with pytest.raises(ValueError, match="data_analysis"):
        LiveBenchSuite(categories=("reasoning", "data_analysis"))


def test_select_question_ids_calls_the_mounted_listing_script(monkeypatch):
    suite = LiveBenchSuite(categories=("reasoning", "math"), limit=2, seed=42)
    calls = []

    class _FakeCompletedProcess:
        stdout = json.dumps({"reasoning": ["reasoning_q1", "reasoning_q2"], "math": ["math_q1", "math_q2"]})

    def fake_run(cmd, check, timeout, capture_output, text):
        calls.append(cmd)
        return _FakeCompletedProcess()

    monkeypatch.setattr(livebench.subprocess, "run", fake_run)
    ids = suite._select_question_ids()
    assert ids == ["reasoning_q1", "reasoning_q2", "math_q1", "math_q2"]
    cmd = calls[0]
    assert cmd[cmd.index("--limit") + 1] == "2"
    assert cmd[cmd.index("--seed") + 1] == "42"
    assert str(livebench.SELECT_IDS_SCRIPT) in " ".join(cmd)
    assert cmd[-2:] == ["reasoning", "math"]


def test_run_with_limit_passes_question_id_to_the_main_call(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="spark")
    ctx = SuiteContext("http://gw:8081", cfg, 1, tmp_path, tmp_path)
    calls = []

    class _FakeListingResult:
        stdout = json.dumps({"reasoning": ["zebra_puzzle_3"]})

    def fake_run(cmd, check, **kwargs):
        calls.append(cmd)
        if "capture_output" in kwargs:
            return _FakeListingResult()
        target = (
            tmp_path / "rep1-livebench" / "data"
            / "live_bench" / "reasoning" / "zebra_puzzle" / "model_judgment"
            / "ground_truth_judgment.jsonl"
        )
        target.parent.mkdir(parents=True)
        shutil.copy(FIX, target)

    monkeypatch.setattr(livebench.subprocess, "run", fake_run)
    rows = LiveBenchSuite(categories=("reasoning",), limit=1, seed=1).run(ctx)
    assert [r["passed"] for r in rows] == [1, 0, 1]
    assert len(calls) == 2  # the listing call, then the real run
    main_call = calls[1]
    assert main_call[main_call.index("--question-id") + 1] == "zebra_puzzle_3"


def test_run_without_limit_makes_a_single_call_unchanged(tmp_path, monkeypatch):
    # No limit set (the "run" subcommand's own default): no listing call,
    # no --question-id, exactly the pre-existing single-call behavior.
    cfg = dataclasses.replace(CFG, model="spark")
    ctx = SuiteContext("http://gw:8081", cfg, 1, tmp_path, tmp_path)
    calls = []

    def fake_run(cmd, check, **kwargs):
        calls.append(cmd)
        target = (
            tmp_path / "rep1-livebench" / "data"
            / "live_bench" / "reasoning" / "zebra_puzzle" / "model_judgment"
            / "ground_truth_judgment.jsonl"
        )
        target.parent.mkdir(parents=True)
        shutil.copy(FIX, target)

    monkeypatch.setattr(livebench.subprocess, "run", fake_run)
    LiveBenchSuite(categories=("reasoning",)).run(ctx)
    assert len(calls) == 1
    assert "--question-id" not in calls[0]
