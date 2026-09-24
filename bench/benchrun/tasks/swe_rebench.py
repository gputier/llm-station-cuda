"""Build a public agentic task from one nebius/SWE-rebench-leaderboard instance.

The environment is the instance's own pre-built docker_image (Nebius already
publishes one ready image per instance on Docker Hub: reuse it, never rebuild
one from scratch). All images are linux/amd64; this host pulls and runs them
under emulation (--platform linux/amd64).

A task is only valid if, in that same image:
  - test_patch alone (no fix) makes every FAIL_TO_PASS node id fail;
  - patch + test_patch (the gold fix) makes every FAIL_TO_PASS node id AND
    every PASS_TO_PASS node id pass.

This mirrors benchrun.tasks.builder's dual-check discipline (checking only the
base side would accept a broken environment as a valid task), adapted to
SWE-rebench's diff-based setup instead of a git-commit walk. The gold patch is
used only for this proof: it is never written into the shipped task.json, so
it can never reach the agent. test_patch IS shipped, but only for grading: the
agent phase never sees or applies it, and AgenticSuite applies it after the
agent phase, right before running test_cmd.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import tempfile

from benchrun.tasks import needs_uv, parse_outcomes

PLATFORM = "linux/amd64"


class SweTaskInvalidError(Exception):
    pass


class SweEnvironmentError(Exception):
    """The image itself is broken (won't pull, won't start, missing /testbed)."""


CREDENTIAL_ERROR_RE = re.compile(
    r"error getting credentials.*not valid\.\s*\(-50\)", re.DOTALL
)


class SweCredentialError(SweEnvironmentError):
    """The local Docker credential helper is broken (macOS keychain error -50).

    This is not a property of the instance: every pull will fail the same way
    until the keychain is fixed, so a caller looping over many instances must
    stop on this one instead of recording it as one more environment
    rejection.
    """


def _docker(*args, timeout=1800):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _pull(image: str) -> None:
    result = _docker("pull", "--platform", PLATFORM, image, timeout=1800)
    if result.returncode != 0:
        if CREDENTIAL_ERROR_RE.search(result.stderr):
            raise SweCredentialError(f"could not pull {image}: {result.stderr[-2000:]}")
        raise SweEnvironmentError(f"could not pull {image}: {result.stderr[-2000:]}")


def _start(image: str, name: str) -> None:
    _docker("rm", "-f", name)
    result = _docker(
        "run", "-d", "--name", name, "--platform", PLATFORM,
        "--entrypoint", "sleep", image, "infinity",
    )
    if result.returncode != 0:
        raise SweEnvironmentError(f"could not start {image} as {name}: {result.stderr[-2000:]}")


def _exec(name: str, cmd: str, timeout=1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", name, "bash", "-c", cmd],
        capture_output=True, text=True, timeout=timeout,
    )


def _apply_patch(name: str, diff_text: str, label: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as fh:
        fh.write(diff_text)
        diff_path = fh.name
    try:
        copy = subprocess.run(["docker", "cp", diff_path, f"{name}:/tmp/{label}.diff"],
                               capture_output=True, text=True)
        if copy.returncode != 0:
            raise SweEnvironmentError(f"could not copy {label} into {name}: {copy.stderr}")
        applied = _exec(name, f"cd /testbed && git apply --whitespace=nowarn /tmp/{label}.diff")
        if applied.returncode != 0:
            raise SweTaskInvalidError(f"{label} did not apply cleanly: {applied.stderr[-2000:]}")
    finally:
        pathlib.Path(diff_path).unlink(missing_ok=True)


def _ensure_uv(name: str) -> None:
    probe = _exec(name, "cd /testbed && uv --version")
    if probe.returncode == 0:
        return
    installed = _exec(name, "pip install -q uv", timeout=300)
    if installed.returncode != 0:
        raise SweEnvironmentError(f"could not install uv in {name}: {installed.stderr[-1000:]}")


def _run_test_cmd(name: str, test_cmd: str) -> dict[str, str]:
    if needs_uv(test_cmd):
        _ensure_uv(name)
    result = _exec(name, f"cd /testbed && {test_cmd}", timeout=1800)
    return parse_outcomes(result.stdout + "\n" + result.stderr)


def _require(outcomes: dict[str, str], node_ids: list[str], expected: str, label: str) -> None:
    missing = [n for n in node_ids if n not in outcomes]
    if missing:
        raise SweTaskInvalidError(f"{label}: no outcome recorded for {missing[:5]} (of {len(missing)})")
    wrong = [n for n in node_ids if outcomes[n] != expected]
    if wrong:
        raise SweTaskInvalidError(
            f"{label}: expected {expected} for {wrong[:5]} (of {len(wrong)}), got other outcomes"
        )


def validate_and_build(row: dict, out: pathlib.Path) -> pathlib.Path:
    """Pull the instance's own image, prove the task is valid in it, and write
    the shipped task (no repo.tar.gz: the environment IS the pinned image).
    Raises SweTaskInvalidError if the instance does not pass the dual check,
    SweEnvironmentError if the image itself is unusable (neither means the
    task is "half-built"; nothing is written to `out` unless validation
    passes).
    """
    instance_id = row["instance_id"]
    image = row["docker_image"]
    fail_to_pass = list(row["FAIL_TO_PASS"])
    pass_to_pass = list(row["PASS_TO_PASS"])
    test_cmd = row["install_config"]["test_cmd"]

    _pull(image)

    base_name = f"swe-verify-base-{instance_id}".replace("_", "-").lower()[:63]
    try:
        _start(image, base_name)
        _apply_patch(base_name, row["test_patch"], "test_patch")
        base_outcomes = _run_test_cmd(base_name, test_cmd)
        _require(base_outcomes, fail_to_pass, "FAILED", "base+test_patch (must fail)")
    finally:
        _docker("rm", "-f", base_name)

    fix_name = f"swe-verify-fix-{instance_id}".replace("_", "-").lower()[:63]
    try:
        _start(image, fix_name)
        _apply_patch(fix_name, row["test_patch"], "test_patch")
        _apply_patch(fix_name, row["patch"], "gold_patch")
        fix_outcomes = _run_test_cmd(fix_name, test_cmd)
        _require(fix_outcomes, fail_to_pass, "PASSED", "base+patch+test_patch (must pass)")
        _require(fix_outcomes, pass_to_pass, "PASSED", "base+patch+test_patch (must stay passing)")
    finally:
        _docker("rm", "-f", fix_name)

    dest = pathlib.Path(out) / instance_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "task.json").write_text(json.dumps({
        "id": instance_id,
        "source": "swe",
        "docker_image": image,
        "prompt": row["problem_statement"],
        "test_patch": row["test_patch"],
        "test_cmd": test_cmd,
        "fail_to_pass": fail_to_pass,
        "pass_to_pass": pass_to_pass,
        "repo": row["repo"],
        "base_commit": row["base_commit"],
    }, indent=2), encoding="utf-8")
    return dest
