import dataclasses
import json
import pathlib
import shutil

from benchrun.config import load_config
from benchrun.runner import SuiteContext
from benchrun.suites import aider
from benchrun.suites.aider import AiderSuite, parse_aider

FIX = pathlib.Path(__file__).parent / "fixtures" / "aider_results_sample"
CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")


def test_parse_aider_one_row_per_exercise():
    rows = parse_aider(FIX)
    assert len(rows) == 3
    assert all(r["passed"] in (0, 1) and r["item_id"] for r in rows)


def test_parse_aider_reads_real_outcomes():
    # Values read by hand in the fixture: all three exercises have
    # tests_outcomes [false, false] in the real spark pass on the 99.
    rows = parse_aider(FIX)
    assert [(r["item_id"], r["passed"]) for r in rows] == [
        ("cpp/complex-numbers", 0),
        ("javascript/grep", 0),
        ("python/zebra-puzzle", 0),
    ]


def test_parse_aider_passes_on_last_try(tmp_path):
    # The real pass has no success, so the passing path is proven on a copy of
    # a real result file where only tests_outcomes differs.
    shutil.copytree(FIX, tmp_path / "res")
    target = tmp_path / "res" / "python" / "exercises" / "practice" / "zebra-puzzle" / ".aider.results.json"
    data = json.loads(target.read_text())
    data["tests_outcomes"] = [False, True]
    target.write_text(json.dumps(data))
    rows = {r["item_id"]: r["passed"] for r in parse_aider(tmp_path / "res")}
    assert rows["python/zebra-puzzle"] == 1
    assert rows["cpp/complex-numbers"] == 0


def test_run_passes_a_positive_timeout_sized_from_the_keyword_count(tmp_path, monkeypatch):
    private_root = tmp_path / "private"
    (private_root / "sets").mkdir(parents=True)
    (private_root / "sets" / "aider-subset-60.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    cfg = dataclasses.replace(CFG, model="spark")
    ctx = SuiteContext("http://gw:8081", cfg, 1, tmp_path, private_root)
    calls, seen_timeouts = [], []

    def fake_run(cmd, check, **kwargs):
        calls.append(cmd)
        seen_timeouts.append(kwargs.get("timeout"))
        results_dir = tmp_path / "rep1-results"
        target = results_dir / "python" / "exercises" / "practice" / "one" / ".aider.results.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"tests_outcomes": [True]}))

    monkeypatch.setattr(aider.subprocess, "run", fake_run)
    rows = AiderSuite().run(ctx)
    assert len(rows) == 1
    cmd = calls[0]
    assert cmd[cmd.index("--keywords") + 1] == "one,two,three"
    assert seen_timeouts[0] is not None and seen_timeouts[0] > 0
    assert seen_timeouts[0] == aider.subprocess_timeout_s(3, cfg.sampling.get("max_tokens", 0))
