import json
import pathlib
import shutil

from benchrun.suites.aider import parse_aider

FIX = pathlib.Path(__file__).parent / "fixtures" / "aider_results_sample"


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
