import dataclasses
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
