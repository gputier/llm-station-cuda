"""Entry point (Task 16): argument parsing and suite construction.

No real subprocess or network call happens here: build_station, campaign_suites
and pilot_suites only ever construct objects, and _record_pilot_result only
touches a local file. The real pilot run itself (docker compose, both
stations) is proven live, not by a unit test.
"""
import json
import pathlib
import subprocess

import pytest

from benchrun import __main__ as entry
from benchrun.suites.agentic import AgenticSuite
from benchrun.suites.aider import AiderSuite
from benchrun.suites.bfcl import BfclSuite
from benchrun.suites.lcb import LiveCodeBenchSuite
from benchrun.suites.livebench import LiveBenchSuite
from benchrun.suites.longbench import LongBenchV2Suite
from benchrun.suites.ruler import RulerSuite
from benchrun.suites.speed import SpeedSuite


def _write_env(path: pathlib.Path, **overrides) -> pathlib.Path:
    values = {
        "STATION_99_SSH": "u@h99",
        "STATION_99_URL": "http://h99:8080",
        "STATION_97_SSH": "u@h97",
        "STATION_97_URL": "http://h97:8080",
        "BENCH_PRIVATE": "/private",
    }
    values.update(overrides)
    env_path = path / "stations.env"
    env_path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")
    return env_path


def test_load_env_file_skips_blank_and_comment_lines(tmp_path):
    p = tmp_path / "e.env"
    p.write_text("# comment\n\nSTATION_99_SSH=u@h\n", encoding="utf-8")
    assert entry.load_env_file(str(p)) == {"STATION_99_SSH": "u@h"}


def test_resolve_env_file_value_used_when_not_already_in_process_env(tmp_path, monkeypatch):
    monkeypatch.delenv("STATION_99_SSH", raising=False)
    env_path = _write_env(tmp_path)
    env = entry.resolve_env(str(env_path))
    assert env["STATION_99_SSH"] == "u@h99"


def test_resolve_env_process_env_wins_over_file(tmp_path, monkeypatch):
    env_path = _write_env(tmp_path)
    monkeypatch.setenv("STATION_99_SSH", "overridden@host")
    env = entry.resolve_env(str(env_path))
    assert env["STATION_99_SSH"] == "overridden@host"


def test_resolve_env_reads_bench_env_var_when_no_flag(tmp_path, monkeypatch):
    env_path = _write_env(tmp_path)
    monkeypatch.setenv("BENCH_ENV", str(env_path))
    env = entry.resolve_env(None)
    assert env["STATION_97_URL"] == "http://h97:8080"


def test_build_station_reads_the_right_machine(tmp_path):
    env = entry.load_env_file(str(_write_env(tmp_path)))
    st = entry.build_station("97", env)
    assert st.ssh_target == "u@h97"
    assert st.base_url == "http://h97:8080"
    assert st.ctl_path == entry.CTL_PATH


def test_build_station_missing_value_raises(tmp_path):
    env_path = _write_env(tmp_path, STATION_99_SSH="")
    env = entry.load_env_file(str(env_path))
    with pytest.raises(SystemExit, match="STATION_99_SSH"):
        entry.build_station("99", env)


def test_gateway_url_uses_ruling_z_service_name():
    assert entry.gateway_url("99") == "http://gateway-99:8081"
    assert entry.gateway_url("97") == "http://gateway-97:8081"


def test_journal_path_under_bench_private_runs():
    p = entry.journal_path(pathlib.Path("/private"), "99")
    assert p == pathlib.Path("/private/runs/journal-99.jsonl")


def test_campaign_suites_builds_every_requested_suite_at_campaign_size(tmp_path):
    names = ["lcb", "aider", "bfcl", "livebench", "ruler", "longbench_v2", "agentic", "speed"]
    suites = entry.campaign_suites(names, tmp_path, "99")
    by_name = {s.name: s for s in suites}
    assert isinstance(by_name["lcb"], LiveCodeBenchSuite) and by_name["lcb"].n_problems == 100
    assert isinstance(by_name["aider"], AiderSuite)
    assert isinstance(by_name["bfcl"], BfclSuite)
    assert by_name["bfcl"].categories == ("simple_python", "multiple", "multi_turn_base")
    assert isinstance(by_name["livebench"], LiveBenchSuite)
    assert by_name["livebench"].categories == ("reasoning", "math")
    assert isinstance(by_name["ruler"], RulerSuite) and by_name["ruler"].lengths == [32768, 131072]
    assert isinstance(by_name["longbench_v2"], LongBenchV2Suite)
    assert by_name["longbench_v2"].lengths == [32768, 131072]
    assert isinstance(by_name["agentic"], AgenticSuite)
    assert by_name["agentic"].tasks_dir == tmp_path / "tasks"
    assert isinstance(by_name["speed"], SpeedSuite)
    assert by_name["speed"].journal_path == tmp_path / "runs" / "journal-99.jsonl"


def test_campaign_suites_rejects_an_unknown_name(tmp_path):
    with pytest.raises(SystemExit, match="unknown"):
        entry.campaign_suites(["not-a-suite"], tmp_path, "99")


def _make_private_root(tmp_path) -> pathlib.Path:
    root = tmp_path / "private"
    (root / "sets").mkdir(parents=True)
    (root / "sets" / "aider-subset-60.txt").write_text(
        "\n".join(f"exercise-{i}" for i in range(10)) + "\n", encoding="utf-8"
    )
    tasks_dir = root / "tasks"
    tasks_dir.mkdir()
    for i in range(8):
        task_dir = tasks_dir / f"task-{i}"
        task_dir.mkdir()
        (task_dir / "task.json").write_text("{}", encoding="utf-8")
    return root


def test_pilot_aider_exercises_file_keeps_only_the_first_five(tmp_path):
    root = _make_private_root(tmp_path)
    out = pathlib.Path(entry._pilot_aider_exercises_file(root, 5))
    assert out.is_absolute()
    lines = [line for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines == ["exercise-0", "exercise-1", "exercise-2", "exercise-3", "exercise-4"]


def test_pilot_agentic_tasks_dir_symlinks_only_the_first_five(tmp_path):
    root = _make_private_root(tmp_path)
    pilot_dir = entry._pilot_agentic_tasks_dir(root, 5)
    names = sorted(p.name for p in pilot_dir.iterdir())
    assert names == ["task-0", "task-1", "task-2", "task-3", "task-4"]
    for name in names:
        assert (pilot_dir / name / "task.json").is_symlink() is False
        assert (pilot_dir / name).is_symlink()


def test_pilot_suites_are_sized_to_the_spec_minimum(tmp_path):
    root = _make_private_root(tmp_path)
    suites = entry.pilot_suites(root, "99")
    by_name = {s.name: s for s in suites}
    assert by_name["lcb"].n_problems == 10
    lines = [l for l in pathlib.Path(by_name["aider"].exercises_file).read_text().splitlines() if l.strip()]
    assert len(lines) == 5
    assert by_name["bfcl"].categories == ("simple_python",)
    assert by_name["livebench"].categories == ("reasoning",)
    assert by_name["ruler"].lengths == [32768] and by_name["ruler"].per_task == 5
    assert by_name["longbench_v2"].lengths == [32768] and by_name["longbench_v2"].samples_per_length == 5
    assert len(list(pathlib.Path(by_name["agentic"].tasks_dir).iterdir())) == 5
    assert by_name["speed"].prompt_tokens == (512,) and by_name["speed"].gen_tokens == 64 and by_name["speed"].reps == 1


class _FakeInner:
    name = "fake"

    def __init__(self):
        self.calls = []

    def run(self, ctx):
        self.calls.append(ctx)
        return [{"item_id": "x", "passed": 1}]


def test_timed_suite_records_duration_and_forwards_result():
    inner = _FakeInner()
    timings: dict = {}
    timed = entry._TimedSuite(inner, timings)
    assert timed.name == "fake"
    result = timed.run(ctx=object())
    assert result == [{"item_id": "x", "passed": 1}]
    assert timings["fake"] >= 0.0


def test_timed_suite_carries_sampling_override():
    speed = SpeedSuite(gen_tokens=64, journal_path=pathlib.Path("/tmp/j.jsonl"))
    timed = entry._TimedSuite(speed, {})
    assert timed.sampling_override == {"max_tokens": 64}


class _FakeStation:
    def start(self, cfg, timeout_s=900):
        pass

    def warmup(self):
        pass

    def stop(self):
        pass

    def vram(self):
        return {"used_mb": 1, "shared_mb": 0}


def test_timed_station_records_start_and_warmup_separately():
    timings: dict = {}
    timed = entry._TimedStation(_FakeStation(), timings)
    timed.start(cfg=None)
    timed.warmup()
    assert "start_s" in timings and "warmup_s" in timings


def test_record_pilot_result_writes_json_per_machine(tmp_path):
    entry._record_pilot_result("99", tmp_path, {"start_s": 1.5}, {"lcb": 2.0}, None)
    data = json.loads(entry.pilot_result_path(tmp_path, "99").read_text(encoding="utf-8"))
    assert data["machine"] == "99" and data["suites"]["lcb"] == 2.0 and data["station"]["start_s"] == 1.5


def test_record_pilot_result_keeps_machines_in_separate_files(tmp_path):
    entry._record_pilot_result("99", tmp_path, {"start_s": 1.0}, {"lcb": 1.0}, None)
    entry._record_pilot_result("97", tmp_path, {"start_s": 2.0}, {"lcb": 2.0}, None)
    d99 = json.loads(entry.pilot_result_path(tmp_path, "99").read_text(encoding="utf-8"))
    d97 = json.loads(entry.pilot_result_path(tmp_path, "97").read_text(encoding="utf-8"))
    assert d99["suites"]["lcb"] == 1.0 and d97["suites"]["lcb"] == 2.0


def test_main_run_dispatches_with_parsed_args(tmp_path, monkeypatch):
    env_path = _write_env(tmp_path)
    captured = {}

    def fake_run_campaign(args, env):
        captured["args"] = args
        captured["env"] = env

    monkeypatch.setattr(entry, "run_campaign", fake_run_campaign)
    entry.main([
        "run", "--machine", "99", "--env", str(env_path),
        "--configs", "configs/99/*/*.yaml", "--suites", "lcb,aider", "--reps", "2",
        "--out", "/private/runs/campaign",
    ])
    args = captured["args"]
    assert args.machine == "99" and args.configs == "configs/99/*/*.yaml"
    assert args.suites == "lcb,aider" and args.reps == 2 and args.out == "/private/runs/campaign"
    assert captured["env"]["STATION_99_SSH"] == "u@h99"


def test_main_pilot_dispatches_with_machine(tmp_path, monkeypatch):
    env_path = _write_env(tmp_path)
    captured = {}

    def fake_run_pilot(args, env):
        captured["args"] = args

    monkeypatch.setattr(entry, "run_pilot", fake_run_pilot)
    entry.main(["pilot", "--machine", "97", "--env", str(env_path)])
    assert captured["args"].machine == "97"


def test_main_rejects_an_unknown_machine(tmp_path):
    env_path = _write_env(tmp_path)
    with pytest.raises(SystemExit):
        entry.main(["pilot", "--machine", "98", "--env", str(env_path)])
