import dataclasses
import json
import pathlib
import subprocess
import tarfile

import pytest

from benchrun.config import load_config
from benchrun.runner import SuiteContext, SuiteSkipped
from benchrun.suites import agentic
from benchrun.suites.agentic import (
    AgenticSuite,
    CLAUDE_CODE_MIN_WINDOW_TOKENS,
    GatewayUnreachableError,
    IsolationError,
    ReposRootNotConfiguredError,
    SidecarError,
    parse_claude_json,
)
from benchrun.tasks.builder import build_task

# Real stdout of `claude -p ... --output-format json --max-turns 40
# --permission-mode bypassPermissions`, captured against a live gateway pass.
# session_id is a random UUID, not private. Everything else is exactly what
# the CLI printed.
FIX = pathlib.Path(__file__).parent / "fixtures" / "claude_stdout_sample.json"
CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")


def test_parse_claude_json_reads_the_real_output():
    parsed = parse_claude_json(FIX.read_text(encoding="utf-8"))
    assert parsed["type"] == "result"
    assert parsed["subtype"] == "success"
    assert parsed["is_error"] is False
    assert parsed["num_turns"] == 6
    assert parsed["total_cost_usd"] > 0


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _make_task_dir(tmp_path, name, prepare_cmd, test_cmd, sidecars=None):
    tasks_dir = tmp_path / "tasks" / name
    tasks_dir.mkdir(parents=True)
    tree_id = f"tree-{name}"
    task = {
        "id": name, "prompt": "fix the bug", "prepare_cmd": prepare_cmd,
        "test_cmd": test_cmd, "fail_before": True,
        # Rebuild fields (benchrun.tasks.builder.build_task's real contract):
        # unused by these tests since the cache is always pre-populated
        # below, a cache hit that never calls rebuild_task.
        "repo": "placeholder/repo", "fix_commit": "deadbeef", "parent_commit": "beadfeed",
        "subdir": None, "tree_id": tree_id,
    }
    if sidecars is not None:
        task["sidecars"] = sidecars
    tasks_dir.joinpath("task.json").write_text(json.dumps(task))
    src = tmp_path / f"{name}-src.txt"
    src.write_text("placeholder")
    # The tarball is a derived artifact: it lives in the cache directory
    # sibling to tasks_dir, named after the recorded tree id, never inside
    # the task's own directory.
    cache_dir = tmp_path / ".cache" / "tasks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(cache_dir / f"{tree_id}.tar.gz", "w:gz") as tf:
        tf.add(src, arcname="calc.js")
    return tasks_dir.parent


_REAL_SUBPROCESS_RUN = subprocess.run


def _default_fake_run(cmd, **kwargs):
    """Baseline double for every docker call this suite makes that is not
    the phase itself: network create/inspect/connect/disconnect, docker
    run/rm, and the gateway health curl. A test overrides only what it
    needs to check, on top of this baseline, so a new call this module adds
    does not silently break every other test.

    agentic.subprocess IS the real subprocess module (not a copy), so
    patching agentic.subprocess.run also reaches the grading-integrity
    helpers' own real git calls (_snapshot_base_commit,
    _capture_and_reset_diff, _apply_filtered_diff): only docker commands
    are faked here, git runs for real against the fixture's own extracted
    tarball (which is not itself a git repo, so these calls exercise the
    "init a fresh base commit" fallback path).
    """
    if cmd[0] != "docker":
        return _REAL_SUBPROCESS_RUN(cmd, **kwargs)
    if cmd[:3] == ["docker", "network", "inspect"]:
        return FakeCompleted(stdout="true\n")
    return FakeCompleted()


def _fake_run_factory(calls, test_exit_code, agent_stdout=None):
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "exec"] and "claude" in cmd:
            return FakeCompleted(stdout=agent_stdout if agent_stdout is not None else FIX.read_text(encoding="utf-8"))
        if cmd[:2] == ["docker", "exec"] and "prepare-the-deps" in cmd:
            return FakeCompleted()
        if cmd[:2] == ["docker", "exec"] and "node --test" in cmd:
            return FakeCompleted(returncode=test_exit_code)
        return _default_fake_run(cmd, **kwargs)
    return fake_run


def test_run_installs_then_isolates_then_grades(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_task_dir(tmp_path, "toy1", "prepare-the-deps", "node --test")
    calls = []
    monkeypatch.setattr(agentic.subprocess, "run", _fake_run_factory(calls, test_exit_code=0))
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)

    assert rows == [{
        "item_id": "private/toy1",
        "passed": 1,
        "detail": {
            "turns": 6,
            "duration_s": rows[0]["detail"]["duration_s"],
            "prepare_exit_code": 0,
            "agent_exit_code": 0,
            "agent_timed_out": False,
            "reason": None,
            "test_exit_code": 0,
            "grade_timed_out": False,
            "test_diff_dropped": False,
            "cost_usd": rows[0]["detail"]["cost_usd"],
            "is_error": False,
        },
    }]

    # Network isolation created per task+rep, checked internal, and the
    # gateway joined it once.
    isolated_network = agentic._isolated_network_name("toy1", 1)
    assert ["docker", "network", "create", "--internal", isolated_network] in calls
    assert ["docker", "network", "inspect", isolated_network, "--format", "{{.Internal}}"] in calls
    assert ["docker", "network", "connect", isolated_network, "gw-t12"] in calls

    # Same container for all three phases: the one docker run -d call, then
    # exec calls against that same name.
    run_calls = [c for c in calls if c[:3] == ["docker", "run", "-d"]]
    assert len(run_calls) == 1
    container = run_calls[0][run_calls[0].index("--name") + 1]
    assert "--network" in run_calls[0]
    assert run_calls[0][run_calls[0].index("--network") + 1] == "bridge"

    prepare_idx = calls.index(["docker", "exec", container, "bash", "-c", "prepare-the-deps"])
    disconnect_bridge_idx = calls.index(["docker", "network", "disconnect", "bridge", container])
    connect_isolated_idx = calls.index(["docker", "network", "connect", isolated_network, container])
    health_idx = next(
        i for i, c in enumerate(calls)
        if c[:2] == ["docker", "exec"] and container in c and "curl" in c
    )
    claude_idx = next(i for i, c in enumerate(calls) if "claude" in c)
    disconnect_isolated_idx = calls.index(["docker", "network", "disconnect", isolated_network, container])
    grade_idx = calls.index(["docker", "exec", container, "bash", "-c", "node --test"])
    rm_calls = [i for i, c in enumerate(calls) if c[:3] == ["docker", "rm", "-f"] and container in c]

    # Order proves the phases: prepare (network access) happens first, the
    # container is switched to the isolated network, the gateway is checked
    # reachable before the agent call, then off it again before grading,
    # and the container is removed last.
    assert prepare_idx < disconnect_bridge_idx < connect_isolated_idx < health_idx < claude_idx
    assert claude_idx < disconnect_isolated_idx < grade_idx < rm_calls[-1]

    claude_cmd = calls[claude_idx]
    assert "ANTHROPIC_BASE_URL=http://gw-t12:8081" in claude_cmd
    assert "ANTHROPIC_API_KEY=dummy" in claude_cmd
    assert "CLAUDE_CODE_ATTRIBUTION_HEADER=0" in claude_cmd
    assert "bypassPermissions" in claude_cmd


def test_run_fails_the_item_when_tests_still_fail(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_task_dir(tmp_path, "toy2", "prepare-the-deps", "node --test")
    calls = []
    monkeypatch.setattr(agentic.subprocess, "run", _fake_run_factory(calls, test_exit_code=1))
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)
    assert rows[0]["passed"] == 0
    assert rows[0]["detail"]["test_exit_code"] == 1


def test_run_raises_when_prepare_cmd_fails(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_task_dir(tmp_path, "toy3", "prepare-the-deps", "node --test")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "exec"] and "prepare-the-deps" in cmd:
            return FakeCompleted(returncode=1, stderr="dependency not found")
        return _default_fake_run(cmd, **kwargs)

    monkeypatch.setattr(agentic.subprocess, "run", fake_run)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    with pytest.raises(RuntimeError, match="prepare_cmd failed"):
        AgenticSuite(tasks_dir).run(ctx)

    # The container is still removed even though prepare_cmd failed.
    assert any(c[:3] == ["docker", "rm", "-f"] for c in calls)


def test_run_records_a_model_failure_and_continues_the_loop(tmp_path, monkeypatch):
    # A second task exists after the failing one: an agent-side failure
    # (here, a non-zero exit) must not stop the suite from grading the rest
    # of the set, unlike a prepare_cmd failure.
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_task_dir(tmp_path, "toyA", "prepare-the-deps", "node --test")
    _make_task_dir(tmp_path, "toyB", "prepare-the-deps", "node --test")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "exec"] and "claude" in cmd:
            return FakeCompleted(stdout="not json", returncode=1)
        if cmd[:2] == ["docker", "exec"] and "prepare-the-deps" in cmd:
            return FakeCompleted()
        if cmd[:2] == ["docker", "exec"] and "node --test" in cmd:
            return FakeCompleted(returncode=1)
        return _default_fake_run(cmd, **kwargs)

    monkeypatch.setattr(agentic.subprocess, "run", fake_run)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)

    assert len(rows) == 2
    for row in rows:
        assert row["passed"] == 0
        assert row["detail"]["reason"] == "agent_exit_nonzero"
        assert row["detail"]["agent_exit_code"] == 1
        # Grading still ran (test_exit_code is present, not skipped).
        assert row["detail"]["test_exit_code"] == 1


def test_run_records_a_timeout_as_a_model_result(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_task_dir(tmp_path, "toyTimeout", "prepare-the-deps", "node --test")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "exec"] and "claude" in cmd:
            import subprocess
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))
        if cmd[:2] == ["docker", "exec"] and "prepare-the-deps" in cmd:
            return FakeCompleted()
        if cmd[:2] == ["docker", "exec"] and "node --test" in cmd:
            return FakeCompleted(returncode=1)
        return _default_fake_run(cmd, **kwargs)

    monkeypatch.setattr(agentic.subprocess, "run", fake_run)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)
    assert rows[0]["passed"] == 0
    assert rows[0]["detail"]["agent_timed_out"] is True
    assert rows[0]["detail"]["reason"] == "timeout"
    # Grading still ran despite the timeout.
    assert rows[0]["detail"]["test_exit_code"] == 1


def test_run_raises_when_gateway_is_unreachable(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_task_dir(tmp_path, "toyGw", "prepare-the-deps", "node --test")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "exec"] and "prepare-the-deps" in cmd:
            return FakeCompleted()
        if "curl" in cmd:
            return FakeCompleted(returncode=7, stderr="connection refused")
        return _default_fake_run(cmd, **kwargs)

    monkeypatch.setattr(agentic.subprocess, "run", fake_run)
    monkeypatch.setattr(agentic, "GATEWAY_HEALTH_INTERVAL_S", 0)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    with pytest.raises(GatewayUnreachableError):
        AgenticSuite(tasks_dir).run(ctx)

    # claude was never called: the health check ran first.
    assert not any("claude" in c for c in calls)


def test_run_raises_when_the_isolated_network_is_not_internal(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_task_dir(tmp_path, "toyNet", "prepare-the-deps", "node --test")

    def fake_run(cmd, **kwargs):
        if cmd[0] != "docker":
            return _REAL_SUBPROCESS_RUN(cmd, **kwargs)
        if cmd[:3] == ["docker", "network", "inspect"]:
            return FakeCompleted(stdout="false\n")
        return FakeCompleted()

    monkeypatch.setattr(agentic.subprocess, "run", fake_run)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    with pytest.raises(IsolationError):
        AgenticSuite(tasks_dir).run(ctx)


def test_run_starts_sidecars_and_never_gives_the_agent_phase_their_network(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    sidecars = [{"name": "postgres", "image": "postgres@sha256:deadbeef",
                 "env": {"POSTGRES_PASSWORD": "test"}, "ready_cmd": "pg_isready"}]
    tasks_dir = _make_task_dir(tmp_path, "toySidecar", "prepare-the-deps", "node --test", sidecars=sidecars)
    calls = []
    monkeypatch.setattr(agentic.subprocess, "run", _fake_run_factory(calls, test_exit_code=0))
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)
    assert rows[0]["passed"] == 1

    task_network = agentic._task_network_name("toySidecar", 1)
    sidecar_container = "task-toySidecar-r1-postgres"

    assert ["docker", "network", "create", "--internal", task_network] in calls
    assert any(c[:3] == ["docker", "run", "-d"] and sidecar_container in c for c in calls)
    assert any(c[:4] == ["docker", "exec", sidecar_container, "pg_isready"] for c in calls)

    main_container = "agent-1-toySidecar"
    run_calls = [c for c in calls if c[:3] == ["docker", "run", "-d"] and main_container in c]
    assert "POSTGRES_HOST=postgres" in run_calls[0]

    connect_task_net_calls = [
        i for i, c in enumerate(calls)
        if c == ["docker", "network", "connect", task_network, main_container]
    ]
    disconnect_task_net_calls = [
        i for i, c in enumerate(calls)
        if c == ["docker", "network", "disconnect", task_network, main_container]
    ]
    isolated_network = agentic._isolated_network_name("toySidecar", 1)
    connect_isolated_idx = calls.index(["docker", "network", "connect", isolated_network, main_container])
    disconnect_isolated_idx = calls.index(["docker", "network", "disconnect", isolated_network, main_container])

    # Connected to the task network before the agent phase starts (prepare
    # can reach the database), disconnected before switching to the
    # isolated network (the agent phase gets neither the internet nor the
    # sidecar), and reconnected after leaving the isolated network (grading
    # gets the database back, never the internet).
    assert connect_task_net_calls[0] < disconnect_task_net_calls[0] < connect_isolated_idx
    assert disconnect_isolated_idx < connect_task_net_calls[1]

    # Cleanup: sidecar container and its network both removed.
    assert any(c[:3] == ["docker", "rm", "-f"] and sidecar_container in c for c in calls)
    assert ["docker", "network", "rm", task_network] in calls


def test_run_raises_when_a_sidecar_never_becomes_ready(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    sidecars = [{"name": "redis", "image": "redis@sha256:deadbeef", "ready_cmd": "redis-cli ping"}]
    tasks_dir = _make_task_dir(tmp_path, "toyBadSidecar", "prepare-the-deps", "node --test", sidecars=sidecars)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "exec"] and "redis-cli" in cmd:
            return FakeCompleted(returncode=1)
        return _default_fake_run(cmd, **kwargs)

    monkeypatch.setattr(agentic.subprocess, "run", fake_run)
    monkeypatch.setattr(agentic, "SIDECAR_READY_RETRIES", 1)
    monkeypatch.setattr(agentic, "SIDECAR_READY_INTERVAL_S", 0)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    with pytest.raises(SidecarError):
        AgenticSuite(tasks_dir).run(ctx)


def _make_swe_task_dir(tmp_path, instance_id):
    tasks_dir = tmp_path / "tasks" / instance_id
    tasks_dir.mkdir(parents=True)
    task = {
        "id": instance_id,
        "source": "swe",
        "docker_image": "swerebench/sweb.eval.x86_64.acme_1776_widget-42:latest",
        "prompt": "The widget does not spin when cold.",
        "test_patch": "diff --git a/tests b/tests\n",
        "test_cmd": "pytest -rA tests/test_widget.py",
        "fail_to_pass": ["tests/test_widget.py::test_spins_when_cold"],
        "pass_to_pass": ["tests/test_widget.py::test_spins_when_warm"],
        "repo": "acme/widget",
        "base_commit": "deadbeef",
    }
    tasks_dir.joinpath("task.json").write_text(json.dumps(task))
    return tasks_dir.parent


def _fake_run_factory_swe(calls, outcomes_stdout, patch_ok=True):
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "exec"] and "claude" in cmd:
            return FakeCompleted(stdout=FIX.read_text(encoding="utf-8"))
        if cmd[:2] == ["docker", "cp"]:
            return FakeCompleted(returncode=0 if patch_ok else 1)
        if cmd[:2] == ["docker", "exec"] and "git apply" in " ".join(cmd):
            return FakeCompleted(returncode=0 if patch_ok else 1)
        if cmd[:2] == ["docker", "exec"] and "pytest" in " ".join(cmd):
            return FakeCompleted(stdout=outcomes_stdout)
        return _default_fake_run(cmd, **kwargs)
    return fake_run


def test_run_a_swe_task_prefixes_item_id_and_grades_on_fail_to_pass_and_pass_to_pass(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_swe_task_dir(tmp_path, "acme__widget-42")
    calls = []
    outcomes = (
        "PASSED tests/test_widget.py::test_spins_when_cold\n"
        "PASSED tests/test_widget.py::test_spins_when_warm\n"
    )
    monkeypatch.setattr(agentic.subprocess, "run", _fake_run_factory_swe(calls, outcomes))
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)

    assert rows[0]["item_id"] == "swe/acme__widget-42"
    assert rows[0]["passed"] == 1

    swe_container = "swe-env-1-acme__widget-42"
    agent_container = "agent-1-acme__widget-42"
    run_calls = [c for c in calls if c[:3] == ["docker", "run", "-d"]]
    # Claude Code's Bun runtime aborts under QEMU emulation inside the SWE
    # image itself (see the module docstring): the agent phase runs in a
    # SECOND, native container from bench-agent, never inside the SWE image.
    assert len(run_calls) == 2

    swe_run = next(c for c in run_calls if swe_container in c)
    assert "--platform" in swe_run and "linux/amd64" in swe_run
    assert "swerebench/sweb.eval.x86_64.acme_1776_widget-42:latest" in swe_run

    agent_run = next(c for c in run_calls if agent_container in c)
    assert "bench-agent" in agent_run
    assert "--user" in agent_run and agent_run[agent_run.index("--user") + 1] == "agent"

    # the agent call itself runs against the native container, not the SWE
    # image (Claude Code is baked into bench-agent at build time, never
    # installed at runtime).
    agent_calls = [c for c in calls if c[:2] == ["docker", "exec"] and "claude" in c]
    assert agent_calls and agent_container in agent_calls[0]

    # the test_patch diff is copied into the SWE container and applied, and
    # pytest runs against /testbed there, not a bind-mounted /repo (there is
    # none: the image IS the environment). The agent's edits are copied back
    # from the native container's /repo before grading.
    cp_calls = [c for c in calls if c[:2] == ["docker", "cp"]]
    assert any(f"{swe_container}:/testbed/." in c for c in cp_calls)
    assert any(c[-1] == f"{swe_container}:/testbed/" for c in cp_calls)
    exec_on_swe = [c for c in calls if c[:3] == ["docker", "exec", swe_container]]
    assert any("cd /testbed" in " ".join(c) for c in exec_on_swe)


def test_run_a_swe_task_fails_when_a_pass_to_pass_test_regresses(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_swe_task_dir(tmp_path, "acme__widget-42")
    calls = []
    outcomes = (
        "PASSED tests/test_widget.py::test_spins_when_cold\n"
        "FAILED tests/test_widget.py::test_spins_when_warm\n"
    )
    monkeypatch.setattr(agentic.subprocess, "run", _fake_run_factory_swe(calls, outcomes))
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)

    assert rows[0]["passed"] == 0


def test_run_a_swe_task_fails_when_the_test_patch_does_not_apply(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_swe_task_dir(tmp_path, "acme__widget-42")
    calls = []
    outcomes = (
        "PASSED tests/test_widget.py::test_spins_when_cold\n"
        "PASSED tests/test_widget.py::test_spins_when_warm\n"
    )
    monkeypatch.setattr(agentic.subprocess, "run", _fake_run_factory_swe(calls, outcomes, patch_ok=False))
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)

    assert rows[0]["passed"] == 0


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def _make_real_private_repo_task(tmp_path):
    """A real git repo built with build_task's real contract (not the
    _make_task_dir fake): used to prove the cache actually rebuilds a
    tarball from task.json's recorded fields, not just reads a pre-seeded
    fixture.
    """
    src = tmp_path / "src_repo"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    _git(src, "config", "user.email", "t@t")
    _git(src, "config", "user.name", "t")
    (src / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (src / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "init")
    (src / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git(src, "commit", "-qam", "fix: add was subtracting")
    fix = _git(src, "rev-parse", "HEAD").strip()
    tasks_dir = tmp_path / "tasks"
    task_dir = build_task(src, fix, "python -m pytest -q", tasks_dir, repo_name="src_repo")
    return tasks_dir, task_dir


def test_run_rebuilds_a_missing_private_tarball_from_recorded_fields(tmp_path, monkeypatch):
    tasks_dir, task_dir = _make_real_private_repo_task(tmp_path)
    task = json.loads((task_dir / "task.json").read_text())
    cache_dir = tasks_dir.parent / ".cache" / "tasks"
    cached_tarball = cache_dir / f"{task['tree_id']}.tar.gz"
    assert cached_tarball.exists()
    cached_tarball.unlink()  # simulate a pruned/missing cache

    cfg = dataclasses.replace(CFG, model="qwen")
    calls = []
    docker_fake = _fake_run_factory(calls, test_exit_code=0)
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        # agentic.subprocess IS the real subprocess module (not a copy), so
        # patching agentic.subprocess.run also reaches builder.py's own git
        # calls inside rebuild_task: only docker commands are faked here,
        # everything else (git, from the real rebuild this test proves)
        # runs for real.
        if cmd and cmd[0] == "docker":
            return docker_fake(cmd, **kwargs)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(agentic.subprocess, "run", fake_run)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir, repos_root=tmp_path).run(ctx)

    assert rows[0]["passed"] == 1
    # rebuild_task wrote the tarball back under the SAME tree id: a second
    # run would now hit the cache instead of rebuilding again.
    assert cached_tarball.exists()
    with tarfile.open(cached_tarball, "r:*") as tf:
        calc = tf.extractfile("calc.py").read().decode()
    assert "return a - b" in calc


def test_run_raises_suite_skipped_when_the_window_fits_no_task(tmp_path, monkeypatch):
    long_prompt = "x" * (CLAUDE_CODE_MIN_WINDOW_TOKENS * 10)  # guaranteed to overflow any tiny window
    tasks_dir = tmp_path / "tasks" / "toyBigPrompt"
    tasks_dir.mkdir(parents=True)
    tasks_dir.joinpath("task.json").write_text(json.dumps({
        "id": "toyBigPrompt", "prompt": long_prompt, "prepare_cmd": "prepare-the-deps",
        "test_cmd": "node --test", "fail_before": True,
        "repo": "placeholder/repo", "fix_commit": "deadbeef", "parent_commit": "beadfeed",
        "subdir": None, "tree_id": "tree-toyBigPrompt",
    }))
    cfg = dataclasses.replace(CFG, model="qwen", ctx_proven=8192)
    monkeypatch.setattr(agentic.subprocess, "run", _default_fake_run)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tasks_dir.parent)

    with pytest.raises(SuiteSkipped):
        AgenticSuite(tasks_dir.parent).run(ctx)


def test_run_skips_only_the_oversized_task_and_still_grades_the_rest(tmp_path, monkeypatch):
    long_prompt = "x" * (CLAUDE_CODE_MIN_WINDOW_TOKENS * 10)
    tasks_dir = _make_task_dir(tmp_path, "toyFits", "prepare-the-deps", "node --test")
    big_dir = tmp_path / "tasks" / "toyTooBig"
    big_dir.mkdir(parents=True)
    big_dir.joinpath("task.json").write_text(json.dumps({
        "id": "toyTooBig", "prompt": long_prompt, "prepare_cmd": "prepare-the-deps",
        "test_cmd": "node --test", "fail_before": True,
        "repo": "placeholder/repo", "fix_commit": "deadbeef", "parent_commit": "beadfeed",
        "subdir": None, "tree_id": "tree-toyTooBig",
    }))
    calls = []
    monkeypatch.setattr(agentic.subprocess, "run", _fake_run_factory(calls, test_exit_code=0))
    # A window above the overhead alone (so "toyFits", a tiny prompt, fits)
    # but far below what the huge prompt needs (so "toyTooBig" does not).
    window = CLAUDE_CODE_MIN_WINDOW_TOKENS + 1000
    cfg = dataclasses.replace(CFG, model="qwen", ctx_proven=window)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)

    assert [row["item_id"] for row in rows] == ["private/toyFits"]
    skip_report = json.loads((tmp_path / "out" / "agentic_skipped.json").read_text())
    assert skip_report["skipped_task_ids"] == ["toyTooBig"]
    assert skip_report["window"] == window


# --- Grading cannot be gamed: the agent has write access to the whole
# tree, so grading must never run on the agent's raw, unfiltered edits.

def _make_private_task_with_a_test_file(tmp_path, name):
    """Like _make_task_dir, but the shipped tarball has a real test file
    (test_calc.py) next to the source, so a test can simulate the agent
    deleting it and check that it comes back before grading.
    """
    tasks_dir = tmp_path / "tasks" / name
    tasks_dir.mkdir(parents=True)
    tree_id = f"tree-{name}"
    task = {
        "id": name, "prompt": "fix the bug", "prepare_cmd": "prepare-the-deps",
        "test_cmd": "node --test", "fail_before": True,
        "repo": "placeholder/repo", "fix_commit": "deadbeef", "parent_commit": "beadfeed",
        "subdir": None, "tree_id": tree_id,
    }
    tasks_dir.joinpath("task.json").write_text(json.dumps(task))
    src_dir = tmp_path / f"{name}-src"
    src_dir.mkdir()
    (src_dir / "calc.js").write_text("function add(a, b) { return a - b; }\n")
    (src_dir / "test_calc.py").write_text("def test_add():\n    assert add(2, 2) == 4\n")
    cache_dir = tmp_path / ".cache" / "tasks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(cache_dir / f"{tree_id}.tar.gz", "w:gz") as tf:
        tf.add(src_dir / "calc.js", arcname="calc.js")
        tf.add(src_dir / "test_calc.py", arcname="test_calc.py")
    return tasks_dir.parent


def test_run_restores_a_test_file_the_agent_deleted_before_grading(tmp_path, monkeypatch):
    # Real exploit this closes: the agent has write access to the whole
    # tree and could delete the failing test so test_cmd trivially exits 0.
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_private_task_with_a_test_file(tmp_path, "toyDeleteTest")
    work_dir = tmp_path / "out" / "rep1-toyDeleteTest"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "exec"] and "claude" in cmd:
            # Simulate the agent's own edit: delete the failing test file
            # (work is a real host directory, bind-mounted into the
            # container in the real code; here the test plays the agent's
            # part directly on that same host directory).
            (work_dir / "test_calc.py").unlink()
            return FakeCompleted(stdout=FIX.read_text(encoding="utf-8"))
        if cmd[:2] == ["docker", "exec"] and "prepare-the-deps" in cmd:
            return FakeCompleted()
        if cmd[:2] == ["docker", "exec"] and "node --test" in cmd:
            # The restored test still correctly fails: the agent made no
            # real fix, only tried to delete the evidence.
            return FakeCompleted(returncode=1)
        return _default_fake_run(cmd, **kwargs)

    monkeypatch.setattr(agentic.subprocess, "run", fake_run)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)

    assert (work_dir / "test_calc.py").exists(), "the deleted test file must be restored before grading"
    assert (work_dir / "test_calc.py").read_text() == "def test_add():\n    assert add(2, 2) == 4\n"
    assert rows[0]["passed"] == 0
    assert rows[0]["detail"]["test_diff_dropped"] is True


def test_run_a_swe_task_drops_a_fake_conftest_and_a_pass_to_pass_edit_before_grading(tmp_path, monkeypatch):
    # Real exploits this closes on the swe/ path: the agent adds a
    # conftest.py that prints fake "PASSED <node id>" lines, or edits an
    # existing PASS_TO_PASS test file directly, either way fabricating a
    # pass the stdout-regex grader would otherwise trust.
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_swe_task_dir(tmp_path, "acme__widget-42")
    work_dir = tmp_path / "out" / "rep1-acme__widget-42"
    outcomes = (
        "PASSED tests/test_widget.py::test_spins_when_cold\n"
        "PASSED tests/test_widget.py::test_spins_when_warm\n"
    )

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "cp"] and cmd[2].endswith(":/testbed/."):
            # Simulate the real extraction docker cp would have done: a
            # pre-existing PASS_TO_PASS test file lands in work.
            test_dir = work_dir / "tests"
            test_dir.mkdir(parents=True, exist_ok=True)
            (test_dir / "test_widget.py").write_text(
                "def test_spins_when_cold(): ...\ndef test_spins_when_warm(): ...\n")
            return FakeCompleted()
        if cmd[:2] == ["docker", "exec"] and "claude" in cmd:
            # The agent's own edits: a brand new conftest.py faking a pass,
            # AND a direct edit to the existing PASS_TO_PASS test file.
            (work_dir / "conftest.py").write_text(
                "print('PASSED tests/test_widget.py::test_spins_when_cold')\n")
            (work_dir / "tests" / "test_widget.py").write_text("def test_spins_when_warm(): assert True\n")
            return FakeCompleted(stdout=FIX.read_text(encoding="utf-8"))
        if cmd[:2] == ["docker", "cp"]:
            return FakeCompleted()
        if cmd[:2] == ["docker", "exec"] and "git apply" in " ".join(cmd):
            return FakeCompleted()
        if cmd[:2] == ["docker", "exec"] and "pytest" in " ".join(cmd):
            return FakeCompleted(stdout=outcomes)
        return _default_fake_run(cmd, **kwargs)

    monkeypatch.setattr(agentic.subprocess, "run", fake_run)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)

    assert not (work_dir / "conftest.py").exists(), "a brand new conftest.py must never reach grading"
    assert (work_dir / "tests" / "test_widget.py").read_text() == (
        "def test_spins_when_cold(): ...\ndef test_spins_when_warm(): ...\n"
    ), "an edit to an existing test file must be reverted before grading"
    assert rows[0]["detail"]["test_diff_dropped"] is True


def test_swe_grade_passed_rejects_an_exit_code_that_is_not_a_real_completed_run():
    outcomes = {"a": "PASSED", "b": "PASSED"}
    # Every required node id claims PASSED, but the process itself did not
    # complete normally (5 = pytest's own "no tests collected", the shape a
    # gamed run that never really executed the suite would produce): must
    # not be trusted just because the outcome lines look right.
    assert agentic._swe_grade_passed(outcomes, ["a"], ["b"], test_exit_code=5) is False
    assert agentic._swe_grade_passed(outcomes, ["a"], ["b"], test_exit_code=0) is True
    assert agentic._swe_grade_passed(outcomes, ["a"], ["b"], test_exit_code=1) is True


def test_run_never_executes_a_malicious_git_config_or_filter_the_agent_writes(tmp_path, monkeypatch):
    # Real exploit this closes: work is a host directory bind-mounted into
    # the agent's container, so the agent (untrusted model output) can
    # write work/.git/config directly. Before this fix, the host ran
    # `git -C work ...`, which discovers and READS work/.git as the repo:
    # an agent-written core.fsmonitor (a command git runs on every
    # add/status) or a filter.<name>.clean driver (selected by a
    # .gitattributes the agent also adds) would execute arbitrary commands
    # on this Mac. The fix keeps the real repo metadata in a host-private
    # temp directory the agent never sees, named explicitly with
    # --git-dir/--work-tree on every host git call, and discards whatever
    # work/.git the agent leaves before touching work at all.
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_task_dir(tmp_path, "toyEvilGit", "prepare-the-deps", "node --test")
    work_dir = tmp_path / "out" / "rep1-toyEvilGit"
    marker_fsmonitor = tmp_path / "PWNED_FSMONITOR"
    marker_filter = tmp_path / "PWNED_FILTER"

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "exec"] and "claude" in cmd:
            # The agent's own write, simulated directly on the real host
            # directory (a bind mount in the real code, not something a
            # fake docker exec can touch, so the test plays the agent's
            # part here exactly like the other exploit tests above do).
            evil_git = work_dir / ".git"
            evil_git.mkdir(parents=True, exist_ok=True)
            (evil_git / "config").write_text(
                "[core]\n"
                f"\tfsmonitor = touch {marker_fsmonitor}\n"
                "[filter \"evil\"]\n"
                f"\tclean = touch {marker_filter}\n"
            )
            (work_dir / ".gitattributes").write_text("calc.js filter=evil\n")
            return FakeCompleted(stdout=FIX.read_text(encoding="utf-8"))
        if cmd[:2] == ["docker", "exec"] and "prepare-the-deps" in cmd:
            return FakeCompleted()
        if cmd[:2] == ["docker", "exec"] and "node --test" in cmd:
            return FakeCompleted(returncode=0)
        return _default_fake_run(cmd, **kwargs)

    monkeypatch.setattr(agentic.subprocess, "run", fake_run)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)

    assert not marker_fsmonitor.exists(), "the agent's fsmonitor command must never run on the host"
    assert not marker_filter.exists(), "the agent's filter clean command must never run on the host"
    # Grading still works: the malicious .git is discarded, not fatal.
    assert rows[0]["passed"] == 1
    assert rows[0]["detail"]["test_exit_code"] == 0


# --- Timeouts: prepare and grade need their own handling, not a bare
# TimeoutExpired that would abort every remaining task in the run.

def test_run_raises_a_clear_error_when_prepare_times_out(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_task_dir(tmp_path, "toyPrepareTimeout", "prepare-the-deps", "node --test")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "exec"] and "prepare-the-deps" in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))
        return _default_fake_run(cmd, **kwargs)

    monkeypatch.setattr(agentic.subprocess, "run", fake_run)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    with pytest.raises(RuntimeError, match="did not finish within"):
        AgenticSuite(tasks_dir, prepare_timeout_s=5).run(ctx)


def test_run_records_a_grade_timeout_as_a_model_result_and_continues_the_loop(tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, model="qwen")
    tasks_dir = _make_task_dir(tmp_path, "toyGradeTimeoutA", "prepare-the-deps", "node --test")
    _make_task_dir(tmp_path, "toyGradeTimeoutB", "prepare-the-deps", "node --test")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "exec"] and "node --test" in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))
        if cmd[:2] == ["docker", "exec"] and "prepare-the-deps" in cmd:
            return FakeCompleted()
        if cmd[:2] == ["docker", "exec"] and "claude" in cmd:
            return FakeCompleted(stdout=FIX.read_text(encoding="utf-8"))
        return _default_fake_run(cmd, **kwargs)

    monkeypatch.setattr(agentic.subprocess, "run", fake_run)
    ctx = SuiteContext("http://gw-t12:8081", cfg, 1, tmp_path / "out", tmp_path)

    rows = AgenticSuite(tasks_dir).run(ctx)

    assert len(rows) == 2
    for row in rows:
        assert row["passed"] == 0
        assert row["detail"]["grade_timed_out"] is True
        assert row["detail"]["reason"] == "grade_timeout"


# --- BENCH_REPOS_ROOT: fails clearly when unset, only when actually needed
# (a real cache miss), not for every run.

def test_resolve_private_tarball_raises_a_clear_error_when_bench_repos_root_is_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(agentic.BENCH_REPOS_ROOT_ENV, raising=False)
    suite = AgenticSuite(tmp_path / "tasks")
    task = {"id": "toy", "tree_id": "deadbeef" * 5, "repo": "acme/widget",
            "fix_commit": "a", "parent_commit": "b", "subdir": None}

    with pytest.raises(ReposRootNotConfiguredError, match="BENCH_REPOS_ROOT"):
        suite._resolve_private_tarball(task)
