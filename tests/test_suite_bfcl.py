import dataclasses
import pathlib
import shutil

import pytest

from benchrun.config import load_config
from benchrun.runner import SuiteContext
from benchrun.suites import bfcl
from benchrun.suites.bfcl import BfclSuite, parse_bfcl

# Cut from a real BFCL pass (spark on the 99, 2026-09-23, --run-ids: 8
# simple_python, 8 multiple, 2 multi_turn_base cases, underscore_to_dot on).
# Nothing to blank: BFCL's data ships in the pinned repo under its own
# Apache license, not a third-party dataset we cannot redistribute.
FIX = pathlib.Path(__file__).parent / "fixtures" / "bfcl_score_sample"
CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")


def test_parse_bfcl_one_row_per_failing_item():
    rows = parse_bfcl(FIX / "score")
    # Real quirk (proven live): eval_runner_helper.save_eval_results only
    # writes FAILING entries to the score file; a passing entry is counted
    # in the header's correct_count but never appears as a row here.
    assert len(rows) == 1
    assert all(r["passed"] == 0 and r["item_id"] for r in rows)


def test_parse_bfcl_reads_real_grades():
    # Values read by hand in the fixture: multiple 7 of 8 correct (failing
    # id multiple_7), simple_python 8 of 8, multi_turn_base 2 of 2.
    rows = parse_bfcl(FIX / "score")
    assert [r["item_id"] for r in rows] == ["multiple_7"]


def _ctx(tmp_path):
    return SuiteContext("http://gw:8081", dataclasses.replace(CFG, model="spark"), 1, tmp_path, tmp_path)


def _fake_harness(tmp_path, calls, drop=None):
    def fake_run(cmd, check):
        calls.append(cmd)
        rep_dir = tmp_path / "rep1-bfcl"
        if "generate" in cmd:
            shutil.copytree(FIX / "result", rep_dir / "result")
        elif "evaluate" in cmd:
            shutil.copytree(FIX / "score", rep_dir / "score")
            if drop:
                (rep_dir / "score" / drop).unlink()
    return fake_run


def test_run_reads_the_files_the_harness_writes(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    calls = []
    monkeypatch.setattr(bfcl.subprocess, "run", _fake_harness(tmp_path, calls))
    rows = BfclSuite().run(ctx)
    # 18 items across the three categories (the result files' own universe),
    # one failed (in the multiple score file), 17 passed.
    assert len(rows) == 18
    assert [r["item_id"] for r in rows if r["passed"] == 0] == ["multiple_7"]
    assert {r["item_id"].rsplit("_", 1)[0] for r in rows} == {"simple_python", "multiple", "multi_turn_base"}
    assert len(calls) == 2
    assert "OPENAI_BASE_URL=http://gw:8081/v1" in calls[0]
    assert "OPENAI_API_KEY=x" in calls[0]


def test_run_fails_when_a_category_has_no_score_file(tmp_path, monkeypatch):
    # A category whose evaluation never ran would otherwise count every one
    # of its items as passed, since score files only list failures.
    ctx = _ctx(tmp_path)
    drop = "bench-spark-R1/multi_turn/BFCL_v4_multi_turn_base_score.json"
    monkeypatch.setattr(bfcl.subprocess, "run", _fake_harness(tmp_path, [], drop))
    with pytest.raises(RuntimeError, match="multi_turn_base"):
        BfclSuite().run(ctx)
