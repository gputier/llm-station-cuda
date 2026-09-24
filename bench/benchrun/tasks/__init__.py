"""Shared building blocks for the task builders (private repo tasks and the
SWE-rebench public subset) and the suite that runs them.
"""
from __future__ import annotations

import re

# Test-runner outcome line, shared by every source that greps a raw test
# command's output for per-node-id PASSED/FAILED/ERROR results (pytest -rA
# style reporting) rather than trusting only the command's own exit code.
OUTCOME_RE = re.compile(r"^(PASSED|FAILED|ERROR)\s+(\S+)")

# Git identity for every commit this project writes on the agent's behalf
# (task tree snapshots, base commits for grading diffs): never a real
# person, so a task's history never looks authored.
GIT_IDENTITY_ARGS = ("-c", "user.email=bench@local", "-c", "user.name=bench")


def parse_outcomes(output: str) -> dict[str, str]:
    """Map each PASSED/FAILED/ERROR node id in a test command's raw output
    (stdout and/or stderr) to its outcome."""
    outcomes: dict[str, str] = {}
    for line in output.splitlines():
        match = OUTCOME_RE.match(line.strip())
        if match:
            outcomes[match.group(2)] = match.group(1)
    return outcomes


def needs_uv(test_cmd: str) -> bool:
    """Whether test_cmd itself invokes uv: SWE-rebench's own install_config
    sometimes assumes it is already on PATH, and the published image does
    not always have it."""
    return "uv run" in test_cmd or test_cmd.strip().startswith("uv ")
