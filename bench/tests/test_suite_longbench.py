import dataclasses
import json
import pathlib
import shutil

import pytest

from benchrun.config import load_config
from benchrun.runner import SuiteSkipped
from benchrun.suites import longbench, request_timeout_s
from benchrun.suites.longbench import LongBenchV2Suite, parse_longbench_v2, subprocess_timeout_s
from _helpers import cfg_with_ctx_size, suite_ctx

CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")

# Real pass (nex on the 99, 2026-09-24, through gw-t11, context_length
# 32768, 3 samples). No context/question/choice text in the output file
# (bench/harness/longbench_run.py never writes them back), nothing
# third-party to blank.
FIX_LB = pathlib.Path(__file__).parent / "fixtures" / "longbench_v2_pred_sample.jsonl"


def _ctx(tmp_path, cfg=None):
    return suite_ctx(tmp_path, cfg or dataclasses.replace(CFG, model="nex"))


def test_parse_longbench_v2_real_pass():
    # Real values in the fixture (station 99, nex, through gw-t11,
    # 2026-09-24, round 4 fixture recapture after the selection/bands fix:
    # every row now also carries input_truncated/input_tokens_cut, both
    # false/0 on the three full-length requests below): items 1-3 judge
    # true, finish_reason stop (max_tokens 16384, one real request per
    # item, band-floor 0, context-length 32768); item 4 is a second real
    # request for the SAME item at max_tokens 300, finish_reason length
    # (the model was cut off before stating an answer letter, pred is null
    # even though the harness's own judge field there is False already;
    # this proves the truncation rule is not a no-op on its own, see the
    # dedicated test below).
    rows = parse_longbench_v2(FIX_LB)
    assert len(rows) == 4
    assert [r["passed"] for r in rows] == [1, 1, 1, 0]
    assert all(r["detail"]["input_truncated"] is False for r in rows)


def test_truncated_by_length_forces_passed_zero_even_if_judge_were_true(tmp_path):
    pred_path = tmp_path / "longbench_v2_32768.jsonl"
    entry = {
        "_id": "x", "domain": "d", "sub_domain": "s", "difficulty": "hard",
        "length": "short", "answer": "A", "response": "The correct answer is (A)",
        "pred": "A", "judge": True, "finish_reason": "length", "completion_tokens": 4000,
    }
    pred_path.write_text(json.dumps(entry) + "\n")
    rows = parse_longbench_v2(pred_path)
    assert rows[0]["passed"] == 0
    assert rows[0]["detail"]["truncated_by_length"] is True


def test_subprocess_timeout_scales_with_samples_and_max_tokens():
    small = subprocess_timeout_s(samples_per_length=3, max_tokens=300, context_length=32768)
    large = subprocess_timeout_s(samples_per_length=20, max_tokens=16384, context_length=32768)
    assert large > small
    assert small > 0


def test_subprocess_timeout_scales_with_context_length():
    small = subprocess_timeout_s(samples_per_length=3, max_tokens=300, context_length=32768)
    large = subprocess_timeout_s(samples_per_length=3, max_tokens=300, context_length=131072)
    assert large > small


def test_run_raises_suite_skipped_when_every_length_exceeds_window(tmp_path):
    cfg = dataclasses.replace(cfg_with_ctx_size(CFG, 4096), model="nex")
    ctx = _ctx(tmp_path, cfg)
    with pytest.raises(SuiteSkipped):
        LongBenchV2Suite(lengths=[32768], samples_per_length=3).run(ctx)
    skipped = json.loads((tmp_path / "rep1-longbench_v2" / "longbench_v2_skipped.json").read_text())
    assert skipped == {"skipped_lengths": [32768]}


def _fake_harness(tmp_path, calls):
    def fake_run(cmd, check, timeout=None):
        calls.append(cmd)
        length = cmd[cmd.index("--context-length") + 1]
        save_dir = pathlib.Path(cmd[cmd.index("--save-dir") + 1].replace("/out", str(tmp_path / "rep1-longbench_v2" / length)))
        save_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX_LB, save_dir / f"longbench_v2_{length}.jsonl")
    return fake_run


def test_run_reads_the_file_the_harness_writes(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    calls = []
    monkeypatch.setattr(longbench.subprocess, "run", _fake_harness(tmp_path, calls))
    rows = LongBenchV2Suite(lengths=[32768], samples_per_length=3).run(ctx)
    assert len(rows) == 4  # fixture holds 4 real rows (round 4 recapture, see FIX_LB)
    assert len(calls) == 1
    assert "--base-url" in calls[0]
    assert calls[0][calls[0].index("--base-url") + 1] == "http://gw:8081/v1"
    assert all(r["item_id"].endswith("@32768") for r in rows)
    cfg = dataclasses.replace(CFG, model="nex")
    expected_timeout = request_timeout_s(cfg.sampling.get("max_tokens", 0), 32768 * longbench.MEASURED_TOKEN_RATIO)
    assert f"BENCH_REQUEST_TIMEOUT_S={expected_timeout}" in calls[0]


def test_run_passes_a_positive_timeout(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    seen_timeouts = []

    def fake_run(cmd, check, timeout=None):
        seen_timeouts.append(timeout)
        length = cmd[cmd.index("--context-length") + 1]
        save_dir = pathlib.Path(cmd[cmd.index("--save-dir") + 1].replace("/out", str(tmp_path / "rep1-longbench_v2" / length)))
        save_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX_LB, save_dir / f"longbench_v2_{length}.jsonl")

    monkeypatch.setattr(longbench.subprocess, "run", fake_run)
    LongBenchV2Suite(lengths=[32768], samples_per_length=3).run(ctx)
    assert seen_timeouts == [subprocess_timeout_s(3, CFG.sampling.get("max_tokens", 0), 32768)]


def test_run_fails_when_a_length_grades_nothing(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)

    def fake_run(cmd, check, timeout=None):
        pass  # writes no output file

    monkeypatch.setattr(longbench.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="graded nothing"):
        LongBenchV2Suite(lengths=[32768], samples_per_length=3).run(ctx)


def test_lengths_above_window_are_skipped_and_recorded(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    calls = []
    monkeypatch.setattr(longbench.subprocess, "run", _fake_harness(tmp_path, calls))
    LongBenchV2Suite(lengths=[32768, 524288], samples_per_length=3).run(ctx)
    assert len(calls) == 1
    skipped = json.loads((tmp_path / "rep1-longbench_v2" / "longbench_v2_skipped.json").read_text())
    assert skipped == {"skipped_lengths": [524288]}
