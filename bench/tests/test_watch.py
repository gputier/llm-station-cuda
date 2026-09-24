import json

from benchrun.watch import _cells, _ranking


def _suite(cell, name, rep, passed, done=True):
    out = cell / name
    out.mkdir(parents=True, exist_ok=True)
    (out / f"rep{rep}.jsonl").write_text("".join(json.dumps({"passed": p}) + "\n" for p in passed))
    if done:
        (out / f"rep{rep}.done").write_text("")


def test_cells_found_at_the_runner_depth_and_unfinished_reps_ignored(tmp_path):
    cell = tmp_path / "99" / "bonsai" / "R1"
    _suite(cell, "lcb", 1, [1, 0, 1, 1])
    _suite(cell, "aider", 1, [1, 1], done=False)
    rows = {r["suite"]: r for r in _cells(tmp_path / "99")}
    assert rows["lcb"]["config"] == "bonsai/R1"
    assert (rows["lcb"]["passed"], rows["lcb"]["total"]) == (3, 4)
    assert rows["lcb"]["running"]  # no meta.json yet
    assert rows["aider"]["total"] == 0


def test_ranking_means_finished_suites_and_leaves_speed_out():
    cells = [
        {"config": "99/a/R1", "suite": "lcb", "passed": 1, "total": 4},
        {"config": "99/a/R1", "suite": "speed", "passed": 9, "total": 9},
        {"config": "97/b/R1", "suite": "lcb", "passed": 3, "total": 4},
        {"config": "97/b/R1", "suite": "aider", "passed": 1, "total": 4},
        {"config": "99/c/R1", "suite": "lcb", "passed": 0, "total": 0},
    ]
    ranking = _ranking(cells)
    # b: mean of 75 and 25; a: 25 alone, its speed row left out; c: nothing graded.
    assert [(r["config"], r["mean"]) for r in ranking] == [("97/b/R1", 50.0), ("99/a/R1", 25.0)]
    assert ranking[1]["suites"] == {"lcb": 25.0}
