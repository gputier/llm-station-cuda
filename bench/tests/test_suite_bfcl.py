import dataclasses
import pathlib
import shutil

from benchrun.config import load_config
from benchrun.runner import SuiteContext
from benchrun.suites import bfcl
from benchrun.suites.bfcl import BfclSuite, parse_bfcl

# Cut from a real BFCL pass (spark on the 99, 5 simple_python cases,
# --run-ids). Nothing to blank: BFCL's data ships in the pinned repo under
# its own Apache license, not a third-party dataset we cannot redistribute.
FIX = pathlib.Path(__file__).parent / "fixtures" / "bfcl_score_sample"
CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")


def test_parse_bfcl_one_row_per_failing_item():
    rows = parse_bfcl(FIX / "score")
    # Real quirk (proven live): eval_runner_helper.save_eval_results only
    # writes FAILING entries to the score file; a passing entry is counted
    # in the header's correct_count but never appears as a row here.
    assert len(rows) == 3
    assert all(r["passed"] == 0 and r["item_id"] for r in rows)


def test_parse_bfcl_reads_real_grades():
    # Values read by hand in the fixture: correct_count 2, total_count 5,
    # failing ids simple_python_1, simple_python_2, simple_python_3.
    rows = parse_bfcl(FIX / "score")
    assert [r["item_id"] for r in rows] == ["simple_python_1", "simple_python_2", "simple_python_3"]


def test_run_reads_the_files_the_harness_writes(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="spark")
    ctx = SuiteContext("http://gw:8081", cfg, 1, tmp_path, tmp_path)
    calls = []

    def fake_run(cmd, check):
        calls.append(cmd)
        rep_dir = tmp_path / "rep1-bfcl"
        if "generate" in cmd:
            shutil.copytree(FIX / "result", rep_dir / "result")
        elif "evaluate" in cmd:
            shutil.copytree(FIX / "score", rep_dir / "score")

    monkeypatch.setattr(bfcl.subprocess, "run", fake_run)
    rows = BfclSuite().run(ctx)
    # 5 items total (the result file's own universe), 2 passed (not in the
    # failing score file), 3 failed (in it): matches the real 40% accuracy.
    assert len(rows) == 5
    assert sorted(r["item_id"] for r in rows if r["passed"] == 1) == ["simple_python_0", "simple_python_4"]
    assert sorted(r["item_id"] for r in rows if r["passed"] == 0) == [
        "simple_python_1", "simple_python_2", "simple_python_3",
    ]
    assert len(calls) == 2
    assert "OPENAI_BASE_URL=http://gw:8081/v1" in calls[0]
    assert "OPENAI_API_KEY=x" in calls[0]
