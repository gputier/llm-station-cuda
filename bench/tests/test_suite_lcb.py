import dataclasses
import json
import pathlib
import shutil

from benchrun.config import load_config
from benchrun.runner import SuiteContext
from benchrun.suites import lcb, request_timeout_s
from benchrun.suites.lcb import LiveCodeBenchSuite, parse_lcb

# Cut from a real eval_all file (spark on the 99, release_v6, contests of
# 2025-04-05 and 2025-04-06). Only question_content was blanked, the problem
# statements are not ours to redistribute.
FIX = pathlib.Path(__file__).parent / "fixtures" / "lcb_eval_sample.json"
CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")


def test_parse_lcb_one_row_per_problem():
    rows = parse_lcb(json.loads(FIX.read_text()))
    assert len(rows) == 3
    assert all(r["passed"] in (0, 1) and r["item_id"] for r in rows)


def test_parse_lcb_reads_real_grades():
    # Values read by hand in the fixture: graded_list [true], [false], [false].
    rows = parse_lcb(json.loads(FIX.read_text()))
    assert [(r["item_id"], r["passed"]) for r in rows] == [
        ("abc400_a", 1),
        ("abc400_c", 0),
        ("3773", 0),
    ]


def test_run_reads_the_file_the_harness_writes(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="spark")
    ctx = SuiteContext("http://gw:8081", cfg, 1, tmp_path, tmp_path)
    calls = []
    seen_timeouts = []

    def fake_run(cmd, check, **kwargs):
        calls.append(cmd)
        seen_timeouts.append(kwargs.get("timeout"))
        # Name observed in the real run: the pinned harness formats the
        # Scenario enum member, hence the "Scenario." prefix.
        target = tmp_path / "rep1-output" / "bench-spark-R1" / "Scenario.codegeneration_1_0.6_eval_all.json"
        target.parent.mkdir(parents=True)
        shutil.copy(FIX, target)

    monkeypatch.setattr(lcb.subprocess, "run", fake_run)
    rows = LiveCodeBenchSuite(release="release_v6", after_date="2025-04-05").run(ctx)
    assert [r["passed"] for r in rows] == [1, 0, 0]
    cmd = calls[0]
    assert f"{lcb.HF_CACHE_VOLUME}:/root/.cache/huggingface" in cmd
    assert cmd[cmd.index("--start_date") + 1] == "2025-04-05"
    assert cmd[cmd.index("--max-problems") + 1] == "100"
    expected_timeout = request_timeout_s(cfg.sampling["max_tokens"], lcb.PROMPT_TOKENS_ESTIMATE)
    assert expected_timeout > 90
    assert f"BENCH_REQUEST_TIMEOUT_S={expected_timeout}" in cmd
    assert "OPENAI_BASE_URL=http://gw:8081/v1" in cmd
    assert seen_timeouts[0] is not None and seen_timeouts[0] > 0
    assert seen_timeouts[0] == lcb.subprocess_timeout_s(100, cfg.sampling.get("max_tokens", 0))
