import dataclasses
import hashlib
import json
import pathlib
import shutil

import pytest

from benchrun.config import load_config
from benchrun.runner import SuiteContext, SuiteSkipped
from benchrun.suites import ruler, request_timeout_s
from benchrun.suites.ruler import (
    RulerSuite,
    max_ctx,
    lengths_to_run,
    parse_ruler,
    subprocess_timeout_s,
)
from _helpers import cfg_with_ctx_size, suite_ctx

CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")

# Cut from real passes (nex on the 99, through gw-t11). Pure RULER synthetic
# data (generated filler sentences and needle values) for niah_single_1 and
# niah_multikey_2 (2026-09-23), nothing third-party to blank. niah_multivalue
# and qa_1 (2026-09-24) use the essay corpus and SQuAD as their haystack:
# their "input" field is replaced by "<omitted: not redistributed>" and the
# sibling .meta.jsonl's prompt_sha256 is recomputed against that same
# placeholder, so parse_ruler's join still succeeds on the committed fixture.
FIX_NIAH_SINGLE = pathlib.Path(__file__).parent / "fixtures" / "ruler_niah_single_1_pred_sample.jsonl"
FIX_NIAH_MULTIKEY = pathlib.Path(__file__).parent / "fixtures" / "ruler_niah_multikey_2_pred_sample.jsonl"
FIX_NIAH_MULTIVALUE = pathlib.Path(__file__).parent / "fixtures" / "ruler_niah_multivalue_pred_sample.jsonl"
FIX_QA_1 = pathlib.Path(__file__).parent / "fixtures" / "ruler_qa_1_pred_sample.jsonl"


def test_max_ctx_reads_ctx_size():
    assert max_ctx(CFG) == 393216


def test_lengths_above_window_are_skipped():
    run, skipped = lengths_to_run([32768, 131072, 524288], CFG)
    assert run == [32768, 131072] and skipped == [524288]


def test_ratio_margin_rejects_a_length_that_only_fits_the_raw_count():
    # 32768 fits a 40000 window raw, but RULER's cl100k-sized prompt is
    # measured to land about 1.3x higher on the served model's own tokenizer
    # (niah_multikey_2, see MEASURED_TOKEN_RATIO): 32768 * 1.3 = 42598 > 40000.
    cfg = cfg_with_ctx_size(CFG, 40000)
    run, skipped = lengths_to_run([32768], cfg)
    assert run == [] and skipped == [32768]


def test_ratio_margin_accounts_for_max_tokens():
    cfg = cfg_with_ctx_size(CFG, 50000, max_tokens=8000)
    # 32768 * 1.3 + 8000 = 50598.4 > 50000: skipped once generation room counts.
    run, skipped = lengths_to_run([32768], cfg)
    assert run == [] and skipped == [32768]


def test_run_raises_suite_skipped_when_every_length_exceeds_window(tmp_path):
    cfg = dataclasses.replace(cfg_with_ctx_size(CFG, 4096), model="nex")
    ctx = SuiteContext("http://gw:8081", cfg, 1, tmp_path, tmp_path)
    with pytest.raises(SuiteSkipped):
        RulerSuite(lengths=[32768], tasks=["niah_single_1"], per_task=2).run(ctx)
    skipped = json.loads((tmp_path / "rep1-ruler" / "ruler_skipped.json").read_text())
    assert skipped == {"skipped_lengths": [32768]}


def test_parse_ruler_one_row_per_sample():
    rows = parse_ruler(FIX_NIAH_SINGLE, "niah_single_1")
    assert len(rows) == 2
    assert all(r["passed"] in (0, 1) and r["item_id"] for r in rows)


def test_parse_ruler_reads_real_grades():
    # Values read by hand in the fixture: index 12967 pred "4823498" against
    # outputs ["4226067"] (miss), index 53467 pred "4823498" against
    # ["4823498"] (hit). Single-reference samples: string_match_all and
    # string_match_part agree here, this only proves the base read path.
    rows = parse_ruler(FIX_NIAH_SINGLE, "niah_single_1")
    assert [r["passed"] for r in rows] == [0, 1]


def test_qa_uses_string_match_part_any_reference_grades_full_credit():
    # Real bug this fixes (adversarial review, 2026-09-24): qa_1 sample
    # index 1, real SQuAD references
    # ["10th and 11th centuries", "in the 10th and 11th centuries",
    #  "10th and 11th centuries", "10th and 11th centuries"], real model
    # pred "10th and 11th centuries" (nex on the 99, 2026-09-24). The old
    # parser applied all() (string_match_all): ref[1] ("in the 10th and
    # 11th centuries") is not a substring of the shorter pred, so all()
    # was False and this correct answer graded as a miss. string_match_part
    # (ANY reference found) is qa's real metric (RULER_SHA constants.py):
    # ref[0]/[2]/[3] match, so this sample must pass.
    rows = parse_ruler(FIX_QA_1, "qa_1")
    by_id = {r["item_id"]: r for r in rows}
    assert by_id["1"]["passed"] == 1
    assert by_id["1"]["detail"]["score"] == 1.0
    # index 2: real references are all "Denmark, Iceland and Norway" (no
    # comma), real pred inserts a comma ("Denmark, Iceland, and Norway"):
    # a genuine miss under either metric, not a demonstration of the bug,
    # kept to show a real false case is still graded false.
    assert by_id["2"]["passed"] == 0
    assert by_id["2"]["detail"]["score"] == 0.0


def test_niah_uses_string_match_all_fraction_of_references():
    # Real reference set (niah_multivalue, RULER's own data/prepare.py,
    # num_needle_v: 4, 2026-09-24), pred hand-set here to isolate the
    # fraction logic: 2 of 4 references present must score 0.5 and not
    # pass, since string_match_all is a fraction, not string_match_part's
    # any-hit-passes rule qa_1 uses.
    rows = parse_ruler(FIX_NIAH_MULTIVALUE, "niah_multivalue")
    refs = rows[0]["detail"]["outputs"]
    assert len(refs) == 4
    two_of_four_pred = f"{refs[0]} and {refs[1]} were mentioned"
    assert ruler._score_all(two_of_four_pred, refs) == 0.5
    assert ruler._score_part(two_of_four_pred, refs) == 1.0


def test_niah_multivalue_real_full_hit():
    # Real capture: all 3 samples got all 4 needles, real full-credit rows.
    rows = parse_ruler(FIX_NIAH_MULTIVALUE, "niah_multivalue")
    assert len(rows) == 3
    assert all(r["passed"] == 1 and r["detail"]["score"] == 1.0 for r in rows)


def test_finish_reason_and_completion_tokens_joined_from_meta():
    rows = parse_ruler(FIX_QA_1, "qa_1")
    for row in rows:
        assert row["detail"]["finish_reason"] == "stop"
        assert isinstance(row["detail"]["completion_tokens"], int)
        assert row["detail"]["truncated_by_length"] is False


def test_truncated_by_length_forces_passed_zero(tmp_path):
    pred_path = tmp_path / "qa_1.jsonl"
    meta_path = tmp_path / "qa_1.meta.jsonl"
    entry = {"index": 0, "pred": "France", "input": "some prompt", "outputs": ["France"], "others": {}, "truncation": -1, "length": 100}
    pred_path.write_text(json.dumps(entry) + "\n")
    prompt_hash = hashlib.sha256(b"some prompt").hexdigest()
    meta_path.write_text(json.dumps({"prompt_sha256": prompt_hash, "finish_reason": "length", "completion_tokens": 300}) + "\n")
    rows = parse_ruler(pred_path, "qa_1")
    assert rows[0]["passed"] == 0
    assert rows[0]["detail"]["score"] == 1.0  # the harness score itself is unaffected
    assert rows[0]["detail"]["truncated_by_length"] is True


def test_subprocess_timeout_scales_with_samples_and_max_tokens():
    small = subprocess_timeout_s(per_task=4, max_tokens=300, length=32768)
    large = subprocess_timeout_s(per_task=20, max_tokens=16384, length=32768)
    assert large > small
    assert small > 0


def test_subprocess_timeout_scales_with_length():
    # Round 4 fix: prefill cost scales with the requested length, a 131072
    # pass must get more time than a 32768 one at the same per_task/max_tokens.
    small = subprocess_timeout_s(per_task=4, max_tokens=300, length=32768)
    large = subprocess_timeout_s(per_task=4, max_tokens=300, length=131072)
    assert large > small


def _ctx(tmp_path):
    return suite_ctx(tmp_path, dataclasses.replace(CFG, model="nex"))


def _fake_harness(tmp_path, calls):
    def fake_run(cmd, check, timeout=None):
        calls.append(cmd)
        task = cmd[cmd.index("--task") + 1]
        save_dir = pathlib.Path(cmd[cmd.index("--save-dir") + 1].replace("/out", str(tmp_path / "rep1-ruler" / "32768")))
        save_dir.mkdir(parents=True, exist_ok=True)
        fixture = FIX_NIAH_SINGLE if task == "niah_single_1" else FIX_NIAH_MULTIKEY
        shutil.copy(fixture, save_dir / f"{task}.jsonl")
    return fake_run


def test_run_reads_the_files_the_harness_writes(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    calls = []
    monkeypatch.setattr(ruler.subprocess, "run", _fake_harness(tmp_path, calls))
    rows = RulerSuite(lengths=[32768], tasks=["niah_single_1", "niah_multikey_2"], per_task=2).run(ctx)
    assert len(rows) == 4
    assert len(calls) == 2
    assert "--base-url" in calls[0]
    assert calls[0][calls[0].index("--base-url") + 1] == "http://gw:8081/v1"
    expected_timeout = request_timeout_s(CFG.sampling.get("max_tokens", 0), 32768 * ruler.MEASURED_TOKEN_RATIO)
    assert f"BENCH_REQUEST_TIMEOUT_S={expected_timeout}" in calls[0]


def test_run_passes_a_positive_timeout_to_every_docker_run_call(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    seen_timeouts = []

    def fake_run(cmd, check, timeout=None):
        seen_timeouts.append(timeout)
        task = cmd[cmd.index("--task") + 1]
        save_dir = pathlib.Path(cmd[cmd.index("--save-dir") + 1].replace("/out", str(tmp_path / "rep1-ruler" / "32768")))
        save_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX_NIAH_SINGLE, save_dir / f"{task}.jsonl")

    monkeypatch.setattr(ruler.subprocess, "run", fake_run)
    RulerSuite(lengths=[32768], tasks=["niah_single_1"], per_task=2).run(ctx)
    assert seen_timeouts == [subprocess_timeout_s(2, CFG.sampling.get("max_tokens", 0), 32768)]
    assert seen_timeouts[0] > 0


def test_run_fails_when_a_task_grades_nothing(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)

    def fake_run(cmd, check, timeout=None):
        pass  # writes no prediction file

    monkeypatch.setattr(ruler.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="graded nothing"):
        RulerSuite(lengths=[32768], tasks=["niah_single_1"], per_task=2).run(ctx)


def test_lengths_above_window_are_skipped_and_recorded(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    calls = []
    monkeypatch.setattr(ruler.subprocess, "run", _fake_harness(tmp_path, calls))
    RulerSuite(lengths=[32768, 524288], tasks=["niah_single_1"], per_task=2).run(ctx)
    assert len(calls) == 1
    skipped = json.loads((tmp_path / "rep1-ruler" / "ruler_skipped.json").read_text())
    assert skipped == {"skipped_lengths": [524288]}
