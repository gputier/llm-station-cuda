import json
import pathlib

import pytest

from benchrun.tasks import swe_rebench
from benchrun.tasks.swe_rebench import (
    SweCredentialError,
    SweEnvironmentError,
    SweTaskInvalidError,
    validate_and_build,
)

ROW = {
    "instance_id": "acme__widget-42",
    "repo": "acme/widget",
    "docker_image": "swerebench/sweb.eval.x86_64.acme_1776_widget-42:latest",
    "base_commit": "deadbeef",
    "problem_statement": "The widget does not spin when cold.",
    "patch": "diff --git a/gold b/gold\n",
    "test_patch": "diff --git a/tests b/tests\n",
    "FAIL_TO_PASS": ["tests/test_widget.py::test_spins_when_cold"],
    "PASS_TO_PASS": ["tests/test_widget.py::test_spins_when_warm"],
    "install_config": {"test_cmd": "pytest -rA tests/test_widget.py"},
}


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _outcomes_stdout(outcomes: dict[str, str]) -> str:
    return "\n".join(f"{status} {node_id}" for node_id, status in outcomes.items())


def _fake_run_factory(base_outcomes, fix_outcomes, pull_ok=True, apply_ok=True):
    state = {"stage": None}

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "pull"]:
            return FakeCompleted(returncode=0 if pull_ok else 1, stderr="manifest unknown")
        if cmd[:2] == ["docker", "run"]:
            name = cmd[cmd.index("--name") + 1]
            state["stage"] = "base" if "verify-base" in name else "fix"
            return FakeCompleted()
        if cmd[:2] == ["docker", "cp"]:
            return FakeCompleted()
        if cmd[:2] == ["docker", "exec"]:
            joined = " ".join(cmd)
            if "git apply" in joined:
                return FakeCompleted(returncode=0 if apply_ok else 1, stderr="patch does not apply")
            if "uv --version" in joined:
                return FakeCompleted(returncode=0)
            outcomes = base_outcomes if state["stage"] == "base" else fix_outcomes
            return FakeCompleted(stdout=_outcomes_stdout(outcomes))
        return FakeCompleted()

    return fake_run


def test_validate_and_build_writes_task_json_without_the_gold_patch(tmp_path, monkeypatch):
    fake_run = _fake_run_factory(
        base_outcomes={"tests/test_widget.py::test_spins_when_cold": "FAILED"},
        fix_outcomes={
            "tests/test_widget.py::test_spins_when_cold": "PASSED",
            "tests/test_widget.py::test_spins_when_warm": "PASSED",
        },
    )
    monkeypatch.setattr(swe_rebench.subprocess, "run", fake_run)

    dest = validate_and_build(ROW, tmp_path)

    task = json.loads((dest / "task.json").read_text(encoding="utf-8"))
    assert task["id"] == "acme__widget-42"
    assert task["source"] == "swe"
    assert task["prompt"] == ROW["problem_statement"]
    assert task["test_patch"] == ROW["test_patch"]
    assert "patch" not in task
    assert task["fail_to_pass"] == ROW["FAIL_TO_PASS"]
    assert task["pass_to_pass"] == ROW["PASS_TO_PASS"]


def test_validate_and_build_rejects_a_fail_to_pass_test_that_already_passes_on_base(tmp_path, monkeypatch):
    fake_run = _fake_run_factory(
        base_outcomes={"tests/test_widget.py::test_spins_when_cold": "PASSED"},
        fix_outcomes={
            "tests/test_widget.py::test_spins_when_cold": "PASSED",
            "tests/test_widget.py::test_spins_when_warm": "PASSED",
        },
    )
    monkeypatch.setattr(swe_rebench.subprocess, "run", fake_run)

    with pytest.raises(SweTaskInvalidError, match="must fail"):
        validate_and_build(ROW, tmp_path)
    assert not (tmp_path / ROW["instance_id"]).exists()


def test_validate_and_build_rejects_a_pass_to_pass_regression_after_the_gold_patch(tmp_path, monkeypatch):
    fake_run = _fake_run_factory(
        base_outcomes={"tests/test_widget.py::test_spins_when_cold": "FAILED"},
        fix_outcomes={
            "tests/test_widget.py::test_spins_when_cold": "PASSED",
            "tests/test_widget.py::test_spins_when_warm": "FAILED",
        },
    )
    monkeypatch.setattr(swe_rebench.subprocess, "run", fake_run)

    with pytest.raises(SweTaskInvalidError, match="must stay passing"):
        validate_and_build(ROW, tmp_path)
    assert not (tmp_path / ROW["instance_id"]).exists()


def test_validate_and_build_raises_when_the_image_cannot_be_pulled(tmp_path, monkeypatch):
    fake_run = _fake_run_factory(base_outcomes={}, fix_outcomes={}, pull_ok=False)
    monkeypatch.setattr(swe_rebench.subprocess, "run", fake_run)

    with pytest.raises(SweEnvironmentError):
        validate_and_build(ROW, tmp_path)


def test_validate_and_build_raises_a_distinct_error_on_a_keychain_credential_failure(tmp_path, monkeypatch):
    # Real macOS keychain error text, not a made-up string.
    credential_stderr = (
        "error getting credentials - err: exit status 1, out: `One or more "
        "parameters passed to the function were not valid. (-50)`\n"
    )

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "pull"]:
            return FakeCompleted(returncode=1, stderr=credential_stderr)
        return FakeCompleted()

    monkeypatch.setattr(swe_rebench.subprocess, "run", fake_run)

    with pytest.raises(SweCredentialError):
        validate_and_build(ROW, tmp_path)


def test_validate_and_build_raises_when_test_patch_does_not_apply(tmp_path, monkeypatch):
    fake_run = _fake_run_factory(base_outcomes={}, fix_outcomes={}, apply_ok=False)
    monkeypatch.setattr(swe_rebench.subprocess, "run", fake_run)

    with pytest.raises(SweTaskInvalidError, match="did not apply cleanly"):
        validate_and_build(ROW, tmp_path)
