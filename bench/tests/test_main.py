"""Entry point: argument parsing and suite construction, campaign ("run") and
the mini/medium/large bench levels ("bench", 2026-09-24) alike.

No real subprocess or network call happens here: build_station, campaign_suites,
bench_suites and bench_configs only ever construct objects or read local
files. The real bench and campaign runs themselves (docker compose, both
stations) are proven live, not by a unit test.
"""
import pathlib

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


def test_first_n_aider_exercises_file_keeps_only_the_first_five(tmp_path):
    root = _make_private_root(tmp_path)
    out = pathlib.Path(entry._first_n_aider_exercises_file(root, 5))
    assert out.is_absolute()
    lines = [line for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines == ["exercise-0", "exercise-1", "exercise-2", "exercise-3", "exercise-4"]


def test_first_n_agentic_tasks_dir_symlinks_only_the_first_five(tmp_path):
    root = _make_private_root(tmp_path)
    out_dir = entry._first_n_agentic_tasks_dir(root, 5)
    names = sorted(p.name for p in out_dir.iterdir())
    assert names == ["task-0", "task-1", "task-2", "task-3", "task-4"]
    for name in names:
        assert (out_dir / name / "task.json").is_symlink() is False
        assert (out_dir / name).is_symlink()


def test_random_aider_exercises_file_is_deterministic_for_one_seed(tmp_path):
    import random
    root = _make_private_root(tmp_path)
    out1 = pathlib.Path(entry._random_aider_exercises_file(root, 1, random.Random(42)))
    out2 = pathlib.Path(entry._random_aider_exercises_file(root, 1, random.Random(42)))
    assert out1.read_text() == out2.read_text()


def test_random_agentic_tasks_dir_is_deterministic_for_one_seed(tmp_path):
    import random
    root = _make_private_root(tmp_path)
    dir1 = entry._random_agentic_tasks_dir(root, 1, random.Random(42))
    dir2 = entry._random_agentic_tasks_dir(root, 1, random.Random(42))
    names1 = sorted(p.name for p in dir1.iterdir())
    names2 = sorted(p.name for p in dir2.iterdir())
    assert names1 == names2 and len(names1) == 1


def test_sized_suites_medium_sizes(tmp_path):
    root = _make_private_root(tmp_path)
    by_name = {s.name: s for s in entry.sized_suites("medium", root, "99")}
    assert by_name["lcb"].n_problems == 10
    lines = [l for l in pathlib.Path(by_name["aider"].exercises_file).read_text().splitlines() if l.strip()]
    assert len(lines) == 5
    assert by_name["bfcl"].limit == 5 and by_name["bfcl"].seed is None
    assert by_name["livebench"].limit == 5 and by_name["livebench"].seed is None
    assert by_name["ruler"].lengths == [32768] and by_name["ruler"].per_task == 1
    assert by_name["longbench_v2"].lengths == [32768] and by_name["longbench_v2"].samples_per_length == 2
    assert len(list(pathlib.Path(by_name["agentic"].tasks_dir).iterdir())) == 2
    assert by_name["speed"].prompt_tokens == (512, 8192, 32768) and by_name["speed"].reps == 3


def test_sized_suites_large_sizes(tmp_path):
    root = _make_private_root(tmp_path)
    by_name = {s.name: s for s in entry.sized_suites("large", root, "99")}
    assert by_name["lcb"].n_problems == 30
    lines = [l for l in pathlib.Path(by_name["aider"].exercises_file).read_text().splitlines() if l.strip()]
    assert len(lines) == 10  # only 10 fixture exercise lines exist under _make_private_root, large asks for 20
    assert by_name["bfcl"].limit == 30
    assert by_name["livebench"].limit == 25
    assert by_name["ruler"].lengths == [32768, 131072] and by_name["ruler"].per_task == 3
    assert by_name["longbench_v2"].lengths == [32768, 131072] and by_name["longbench_v2"].samples_per_length == 5
    assert len(list(pathlib.Path(by_name["agentic"].tasks_dir).iterdir())) == 8


def test_mini_suites_sizes_are_all_one_and_response_capped(tmp_path):
    root = _make_private_root(tmp_path)
    by_name = {s.name: s for s in entry.mini_suites(root, "99", seed=42)}
    assert by_name["lcb"]._inner.n_problems == 1 and by_name["lcb"]._inner.selection_seed == 42
    lines = [l for l in pathlib.Path(by_name["aider"]._inner.exercises_file).read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    assert len(by_name["bfcl"]._inner.categories) == 1 and by_name["bfcl"]._inner.limit == 1
    assert by_name["bfcl"]._inner.categories[0] in ("simple_python", "multiple", "multi_turn_base")
    assert len(by_name["livebench"]._inner.categories) == 1 and by_name["livebench"]._inner.limit == 1
    assert by_name["livebench"]._inner.categories[0] in ("reasoning", "math")
    assert len(by_name["ruler"]._inner.tasks) == 1 and by_name["ruler"]._inner.per_task == 1
    assert by_name["ruler"]._inner.lengths == [32768]
    assert by_name["longbench_v2"]._inner.samples_per_length == 1 and by_name["longbench_v2"]._inner.lengths == [32768]
    assert len(list(pathlib.Path(by_name["agentic"]._inner.tasks_dir).iterdir())) == 1
    for name in ("lcb", "aider", "bfcl", "livebench", "ruler", "longbench_v2", "agentic"):
        assert by_name[name].sampling_override == {"max_tokens": entry.MINI_MAX_TOKENS}
    assert not hasattr(by_name["speed"], "sampling_override") or by_name["speed"].sampling_override != {"max_tokens": entry.MINI_MAX_TOKENS}
    assert by_name["speed"].prompt_tokens == (512,) and by_name["speed"].reps == 1


def test_mini_suites_same_seed_draws_the_same_selection(tmp_path):
    root = _make_private_root(tmp_path)
    a = {s.name: s for s in entry.mini_suites(root, "99", seed=7)}
    b = {s.name: s for s in entry.mini_suites(root, "99", seed=7)}
    assert a["bfcl"]._inner.categories == b["bfcl"]._inner.categories
    assert a["livebench"]._inner.categories == b["livebench"]._inner.categories
    assert a["ruler"]._inner.tasks == b["ruler"]._inner.tasks


def test_bench_suites_rejects_an_unknown_preset(tmp_path):
    with pytest.raises(SystemExit, match="unknown preset"):
        entry.bench_suites("full", tmp_path, "99")


def test_bench_suites_mini_needs_a_seed(tmp_path):
    with pytest.raises(ValueError):
        entry.bench_suites("mini", tmp_path, "99")


def test_capped_suite_forwards_run_and_name():
    inner = _FakeInner()
    capped = entry._CappedSuite(inner, 2048)
    assert capped.name == "fake" and capped.sampling_override == {"max_tokens": 2048}
    assert capped.run(ctx=object()) == [{"item_id": "x", "passed": 1}]
    assert inner.calls


class _FakeInner:
    name = "fake"

    def __init__(self):
        self.calls = []

    def run(self, ctx):
        self.calls.append(ctx)
        return [{"item_id": "x", "passed": 1}]


def test_bench_configs_picks_every_variant_of_the_requested_model():
    configs = entry.bench_configs("99", ["tiel"])
    assert [c.variant for c in configs] == ["R1", "R2", "R3"]
    assert all(c.model == "tiel" and c.machine == "99" for c in configs)


def test_bench_configs_defaults_to_every_model_when_none_requested():
    configs = entry.bench_configs("99", None)
    assert any(c.model == "tiel" for c in configs)
    assert len(configs) >= 3


def test_run_bench_full_lcb_argv_unchanged_by_the_new_selection_seed_param(tmp_path, monkeypatch):
    # A default-constructed LiveCodeBenchSuite/BfclSuite/LongBenchV2Suite
    # (what campaign_suites, "run"'s own builder, still constructs) must
    # never emit the new --selection-seed/--limit/--seed flags: proves the
    # "run" subcommand's own docker argv is unaffected by this change.
    assert LiveCodeBenchSuite().selection_seed is None
    assert BfclSuite().limit is None and BfclSuite().seed is None
    assert LongBenchV2Suite().selection_seed is None
    assert LiveBenchSuite().limit is None and LiveBenchSuite().seed is None


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


def test_main_bench_dispatches_with_preset_and_seed(tmp_path, monkeypatch):
    env_path = _write_env(tmp_path)
    captured = {}

    def fake_run_bench(args, env):
        captured["args"] = args

    monkeypatch.setattr(entry, "run_bench", fake_run_bench)
    entry.main([
        "bench", "--machine", "97", "--env", str(env_path), "--preset", "mini",
        "--models", "tiel,qwen", "--out", "/private/runs/bench", "--seed", "123",
    ])
    args = captured["args"]
    assert args.machine == "97" and args.preset == "mini"
    assert args.models == "tiel,qwen" and args.out == "/private/runs/bench" and args.seed == 123


def test_main_rejects_an_unknown_machine(tmp_path):
    env_path = _write_env(tmp_path)
    with pytest.raises(SystemExit):
        entry.main(["bench", "--machine", "98", "--env", str(env_path), "--preset", "mini", "--out", "/x"])
