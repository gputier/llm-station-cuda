"""In-house agentic suite: Claude Code driven against the gateway on a private
task set built by benchrun.tasks.builder.

Each task runs in three phases inside the SAME container (same filesystem
throughout, so whatever a language's dependency installer writes outside the
task tree itself, RubyGems' GEM_HOME, cargo's ~/.cargo/registry, go's module
cache, carries over to the phase that needs it, not just whatever a single
bind mount happens to cover):

1. prepare: the task's own prepare_cmd (dependency install, migrations),
   with normal network access AND the task's own sidecars (see below) if it
   declares any. A prepare failure is an infrastructure fault (broken image,
   missing dependency), not a model result: it raises and aborts the whole
   suite run, same as a missing result file in the other suites.
2. agent: claude -p against the gateway only. The container is switched onto
   an isolated network of its own, one per task AND rep
   (bench-agent-isolated-<task id>-r<rep>, internal, no route to the
   internet, no sidecars either, created before this phase and removed
   right after it): two concurrent AgenticSuite runs never share one, unlike
   a single fixed network name would. bench-net itself is NOT internal
   (other tasks' gateways still need it to reach the station over the LAN),
   so isolation needs a network of its own; the gateway container joins this
   task's isolated network in addition to bench-net, only for the duration
   of this one task's agent phase. A timeout, a non-zero exit or hitting
   max_turns here is a MODEL result, not an infrastructure fault: the task
   is recorded passed=0 with the reason in detail, grading still runs on
   whatever (filtered, see below) tree state exists, and the loop continues
   to the next task. Only a prepare failure or an unreachable gateway stops
   the whole run.
3. grade: the task's own test_cmd, with the container off the internet and
   its isolated network, but back on the task's own sidecar network if it
   has one. Before this phase runs, the agent's diff against the pristine
   base tree is computed, every file that looks like a test file, a
   conftest.py or test configuration is dropped from it (the agent has
   write access to the whole tree: it could otherwise delete or gut a
   failing test, or add a conftest.py / edit a PASS_TO_PASS test to
   fabricate a pass), and only the filtered diff is applied to a tree reset
   back to that pristine base. A grade-phase timeout (the agent may have
   written an infinite loop) is a MODEL result too, passed=0 with a reason,
   never an exception that aborts the rest of the task set.

passed = 1 if the grade phase's test command exits zero (private) or, for
swe/, if the test command's exit code is a real completed run (0 or 1, not
an interrupted/crashed/no-tests-collected code) AND every required node id
is PASSED.

Sidecars (task.json's optional "sidecars" list): a task whose grading needs a
real database or cache (RecyNetwork's AdonisJS backend needs Redis at boot,
for instance) declares them there, e.g.
    "sidecars": [{"name": "postgres", "image": "postgres@sha256:<digest>",
                  "env": {"POSTGRES_PASSWORD": "test"}, "ready_cmd": "pg_isready"}]
Each sidecar gets its own container on a network private to THIS task and
rep (bench-task-<id>-r<rep>, internal, removed at the end), reachable by the
main container under `name` as hostname during prepare and grade, never
during the agent phase. The image is pinned by digest, read once and
recorded next to the task in SOURCES.md, the same convention pins.env uses
for the other harnesses' commits.

Public sub-score (task.json's "source": "swe"): a nebius/SWE-rebench-leaderboard
instance, built by benchrun.tasks.swe_rebench.validate_and_build. There is no
repo.tar.gz: the dependency environment IS the instance's own pinned
docker_image (Nebius already ships one ready image per instance; this
harness reuses it rather than rebuilding one, per its own design). That
image is linux/amd64 and runs under QEMU emulation on this arm64 host, which
is fine for prepare and grade (plain Python/pytest), but Claude Code's
runtime (a Bun-compiled standalone executable) reliably aborts inside it:
`claude --version` prints "ASSERTION FAILED: MemoryExhaustion" from Bun's
JSC heap allocator and aborts (SIGABRT, exit 134) with abundant free memory
available, a QEMU/Bun interaction bug, not a resource limit. So the agent
phase never runs inside the SWE image at all:
  - a second container, from the SAME bench-agent image private tasks use
    (native arm64, Claude Code baked in at build time, already proven), runs
    the agent phase only, on a copy of /testbed extracted onto the host with
    `docker cp` before the agent phase and written back with `docker cp`
    after it;
  - the SWE container itself only ever runs prepare (nothing, its
    dependencies are already installed) and grade (apply task["test_patch"],
    never shown to the agent, then run test_cmd against the agent's edits);
  - grading checks that every id in task["fail_to_pass"] and
    task["pass_to_pass"] comes back PASSED, not just the test command's exit
    code, since a SWE task's test_cmd legitimately runs tests outside those
    two sets too.
item_id is reported as "<source>/<id>" ("private/..." or "swe/...") so the
two populations can be scored separately.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import urlparse

from benchrun.runner import SuiteSkipped
from benchrun.suites import max_ctx
from benchrun.suites.corpus import CHARS_PER_TOKEN
from benchrun.tasks import GIT_IDENTITY_ARGS, needs_uv, parse_outcomes
from benchrun.tasks.builder import rebuild_task

IMAGE = "bench-agent"
SWE_PLATFORM = "linux/amd64"
# llama-server's /v1/messages response never reports a prompt/input token
# count, only output_tokens: the only real prompt-token figure available is
# what llama-server itself computes and puts in its own 400 error body for an
# over-window request, e.g. "request (18132 tokens) exceeds the available
# context size (8192 tokens)" for a real, unmodified 2713-character task
# prompt. MEASURED_MIN_WINDOW_TOKENS is that number as-is (not decomposed
# into "overhead" via an estimated ratio: the whole figure is the real
# minimum for that prompt); MIN_WINDOW_MARGIN is a 20% safety margin for
# extrapolating from a single data point.
MEASURED_MIN_WINDOW_TOKENS = 18132
MEASURED_MIN_WINDOW_PROMPT_CHARS = 2713
MIN_WINDOW_MARGIN = 1.2
CLAUDE_CODE_MIN_WINDOW_TOKENS = int(MEASURED_MIN_WINDOW_TOKENS * MIN_WINDOW_MARGIN)
# Default location a private task's cache-rebuilt tarball is read from and
# written to when the tarball is not already there: sibling of tasks_dir
# (Bench-LLM/.cache/tasks next to Bench-LLM/tasks), gitignored, never
# committed. The tarball is a derived artifact: task.json's own repo,
# fix_commit, parent_commit, subdir and tree_id are what is pinned and
# committed (see benchrun.tasks.builder), not tarball bytes.
BENCH_REPOS_ROOT_ENV = "BENCH_REPOS_ROOT"
# Bounded retry for the gateway health check from inside the isolated
# network: claude -p does not fail fast when the gateway is unreachable
# (120+ s of silence before Claude Code itself gives up), so this check runs
# BEFORE the agent call, not as a substitute for a timeout.
GATEWAY_HEALTH_RETRIES = 5
GATEWAY_HEALTH_INTERVAL_S = 2
# Bounded retry for a sidecar's own ready_cmd (e.g. pg_isready, redis-cli
# ping): a task must never race a cold database.
SIDECAR_READY_RETRIES = 30
SIDECAR_READY_INTERVAL_S = 1
# Prepare (dependency install) needs its own, much longer timeout than the
# agent/grade one: a real Ruby task's grpc C++ core compile was measured
# over 90 minutes once under heavy concurrent Docker load (SOURCES.md).
# Sized with margin over that, not shared with the agent/grade budget so a
# slow cold-cache install cannot masquerade as a hung agent or vice versa.
PREPARE_TIMEOUT_S = 7200
# Every other subprocess.run this suite starts is a short docker/git admin
# call (network create/rm/inspect, container rm/cp, "docker run -d ... sleep
# infinity" to start a long-lived container, a git plumbing command): none of
# these talk to a model, so a flat, generous ceiling is enough. Without one a
# stuck docker daemon or git process would hang this cell (and, since
# Campaign.run is sequential, the whole rest of the campaign) forever
# (Task 16 memo, due 2026-09-30: give every suite subprocess a timeout).
DOCKER_ADMIN_TIMEOUT_S = 120


class GatewayUnreachableError(Exception):
    pass


class IsolationError(Exception):
    pass


class SidecarError(Exception):
    pass


class ReposRootNotConfiguredError(Exception):
    pass


def _min_window_for_task(task: dict) -> int:
    """The smallest served window this task can run in at all: the real
    measured minimum (see CLAUDE_CODE_MIN_WINDOW_TOKENS above) plus, for a
    task whose own prompt is LONGER than the one actually measured, an
    estimate (this project's own CHARS_PER_TOKEN, benchrun.suites.corpus,
    tuned for English prose, an approximation here) of the EXCESS length
    only. A window under this is not "slow", it is a request Claude Code
    cannot even send."""
    excess_chars = max(0, len(task["prompt"]) - MEASURED_MIN_WINDOW_PROMPT_CHARS)
    excess_tokens = excess_chars // CHARS_PER_TOKEN
    return CLAUDE_CODE_MIN_WINDOW_TOKENS + excess_tokens


def _default_repos_root() -> pathlib.Path:
    value = os.environ.get(BENCH_REPOS_ROOT_ENV)
    if not value:
        raise ReposRootNotConfiguredError(
            f"{BENCH_REPOS_ROOT_ENV} is not set: a private task's tarball cache miss cannot be "
            "rebuilt without knowing where the source repositories live. Set it (see "
            "bench/.env.example) or pass repos_root explicitly to AgenticSuite."
        )
    return pathlib.Path(value)


def _task_network_name(task_id: str, rep: int) -> str:
    return f"bench-task-{task_id}-r{rep}"


def _isolated_network_name(task_id: str, rep: int) -> str:
    # Per task and rep, like the sidecar network above: a single fixed
    # ISOLATED_NETWORK name was shared by every concurrent run (two agents
    # running AgenticSuite at once would each switch containers onto the
    # SAME network, seeing each other), removed here per-task instead of
    # created once for the whole suite run.
    return f"bench-agent-isolated-{task_id}-r{rep}"


def _start_sidecars(task_id: str, rep: int, sidecars: list[dict]) -> tuple[str | None, list[str]]:
    if not sidecars:
        return None, []
    network = _task_network_name(task_id, rep)
    subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
    create = subprocess.run(["docker", "network", "create", "--internal", network],
                             capture_output=True, text=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
    if create.returncode != 0:
        raise SidecarError(f"could not create {network}: {create.stderr}")
    names = []
    try:
        for sidecar in sidecars:
            name = f"task-{task_id}-r{rep}-{sidecar['name']}"
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
            cmd = ["docker", "run", "-d", "--name", name, "--network", network,
                   "--network-alias", sidecar["name"]]
            for key, value in (sidecar.get("env") or {}).items():
                cmd += ["-e", f"{key}={value}"]
            cmd.append(sidecar["image"])
            started = subprocess.run(cmd, capture_output=True, text=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
            if started.returncode != 0:
                raise SidecarError(f"could not start sidecar {name}: {started.stderr}")
            names.append(name)
            ready_cmd = sidecar.get("ready_cmd")
            if ready_cmd:
                for _ in range(SIDECAR_READY_RETRIES):
                    probe = subprocess.run(["docker", "exec", name, *shlex.split(ready_cmd)],
                                            capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
                    if probe.returncode == 0:
                        break
                    time.sleep(SIDECAR_READY_INTERVAL_S)
                else:
                    raise SidecarError(f"sidecar {name} never became ready ({ready_cmd})")
    except SidecarError:
        for name in names:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
        subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
        raise
    return network, names


def _stop_sidecars(network: str | None, names: list[str]) -> None:
    for name in names:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
    if network:
        subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)


def parse_claude_json(stdout: str) -> dict:
    """Parse the final JSON object Claude Code prints with --output-format
    json. That mode emits exactly one JSON object on stdout once the run
    finishes (streaming events, if any, go to stderr): json.loads is enough,
    no line splitting.
    """
    return json.loads(stdout)


def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _ensure_isolated_network(network: str) -> None:
    # Idempotent: an already-existing network is not an error worth failing
    # the suite over, but its Internal flag is checked every time regardless
    # of whether this call created it or found it already there. A
    # pre-existing bridge network under this name (created by hand, or left
    # over from a crashed prior run) would silently give the agent phase
    # real internet access: this is the isolation guarantee, not a
    # formality. `network` is per task and rep (see _isolated_network_name):
    # two concurrent AgenticSuite runs never share one.
    subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
    create = subprocess.run(["docker", "network", "create", "--internal", network],
                             capture_output=True, text=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
    if create.returncode != 0:
        raise IsolationError(f"could not create {network}: {create.stderr}")
    inspect = subprocess.run(
        ["docker", "network", "inspect", network, "--format", "{{.Internal}}"],
        capture_output=True, text=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
    if inspect.stdout.strip() != "true":
        raise IsolationError(
            f"{network} exists but is not internal: refusing to use it for the agent phase")


def _remove_isolated_network(network: str, gw_host: str) -> None:
    subprocess.run(["docker", "network", "disconnect", network, gw_host], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
    subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)


def _check_gateway_reachable(container: str, base_url: str, network: str) -> None:
    last_error = ""
    for _ in range(GATEWAY_HEALTH_RETRIES):
        probe = subprocess.run(
            ["docker", "exec", container, "curl", "-s", "-m", "5", "-o", "/dev/null",
             "-w", "%{http_code}", base_url],
            capture_output=True, text=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
        if probe.returncode == 0:
            return
        last_error = probe.stderr.strip()
        time.sleep(GATEWAY_HEALTH_INTERVAL_S)
    raise GatewayUnreachableError(
        f"gateway at {base_url} did not answer from inside {network}: {last_error}")


def _apply_diff(container: str, diff_text: str, dest: str) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as fh:
        fh.write(diff_text)
        diff_path = fh.name
    try:
        subprocess.run(["docker", "cp", diff_path, f"{container}:{dest}"],
                        check=True, capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
    finally:
        pathlib.Path(diff_path).unlink(missing_ok=True)
    return subprocess.run(
        ["docker", "exec", container, "bash", "-c", f"cd /testbed && git apply --whitespace=nowarn {dest}"],
        capture_output=True, text=True, timeout=DOCKER_ADMIN_TIMEOUT_S)


def _swe_grade_passed(outcomes: dict[str, str], fail_to_pass: list[str], pass_to_pass: list[str],
                       test_exit_code: int | None) -> bool:
    required = list(fail_to_pass) + list(pass_to_pass)
    # test_exit_code in (0, 1): a real, completed pytest run, whether every
    # requested node id passed (0) or something in the wider test_cmd (which
    # legitimately covers tests outside fail_to_pass/pass_to_pass) failed
    # (1). Any other code (2 interrupted, 3 internal error, 4 usage error, 5
    # no tests collected, or a signal) means the run itself did not happen
    # the way it was supposed to and the outcome lines cannot be trusted,
    # regardless of what they claim: this is the "exit code must agree"
    # check, sized to NOT reject the legitimate case (other tests in the
    # same test_cmd failing) that made this suite avoid `== 0` in the first
    # place.
    exit_code_ok = test_exit_code in (0, 1)
    return exit_code_ok and bool(required) and all(outcomes.get(node_id) == "PASSED" for node_id in required)


# --- Grading integrity: the agent has write access to the whole tree, so a
# task's own test files (or a fresh conftest.py) are never applied to the
# tree grading actually runs on. Real exploits closed by this: (private) the
# agent deletes or guts the failing test, test_cmd then trivially exits 0;
# (swe/) the agent adds a conftest.py or edits a PASS_TO_PASS test file to
# fabricate a "PASSED <node id>" line the stdout regex trusts. Both are
# closed the same way, at whole-file granularity, on both paths: compute the
# agent's diff against the pristine base tree, drop every file the diff
# touches that looks like a test file, conftest.py or test configuration
# (never a hunk-level filter: mixing a real fix into a test file's own diff
# would still leak it through a hunk-level rule), apply only what remains to
# a tree reset back to that same pristine base, and grade THAT.
_TEST_FILENAME_RE = re.compile(
    r"(^|/)("
    r"conftest\.py"
    r"|test_[^/]+\.py"
    r"|[^/]+_test\.py"
    r"|[^/]+_test\.go"
    r"|[^/]+\.(test|spec)\.[jt]sx?"
    r"|[^/]+_spec\.rb"
    r"|spec_helper\.rb"
    r"|pytest\.ini"
    r"|setup\.cfg"
    r"|tox\.ini"
    r"|\.rspec"
    r"|jest\.config\.[cm]?[jt]s"
    r"|vitest\.config\.[cm]?[jt]s"
    r")$",
    re.IGNORECASE,
)
_TEST_DIR_RE = re.compile(r"(^|/)(tests?|spec)/", re.IGNORECASE)


def _is_test_path(path: str) -> bool:
    return bool(_TEST_FILENAME_RE.search(path)) or bool(_TEST_DIR_RE.search(path))


def _diff_target_path(diff_git_line: str) -> str | None:
    # "diff --git a/<path> b/<path>": the b/ side is what to classify (a
    # rename or a brand new file both show up there; a/dev/null on the a
    # side for a new file is not a real path).
    parts = diff_git_line.split()
    if len(parts) < 4:
        return None
    b = parts[3]
    return b[2:] if b.startswith("b/") else b


def _filter_diff_dropping_test_files(diff_text: str) -> str:
    kept: list[str] = []
    skipping = False
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            path = _diff_target_path(line)
            skipping = bool(path) and _is_test_path(path)
        if not skipping:
            kept.append(line)
    return "".join(kept)


# --- Host-side git never trusts work/.git. `work` is a HOST directory
# bind-mounted into the agent container: the agent (untrusted model output,
# not our own code) has full write access to it, including any .git inside
# it. A host `git -C work ...` call lets git discover and READ work/.git's
# own config, which the agent could set to execute arbitrary commands on
# this Mac (core.fsmonitor = <command>, core.hooksPath pointing at a
# hook the agent wrote, a filter.<name>.clean/smudge driver selected by a
# .gitattributes the agent adds): the model under test is untrusted input,
# and "the host runs git inside a directory it wrote to" is code execution
# on the host, not a sandboxed grading step. Fixed by never letting the
# host's git discover a repo from `work` at all: the pristine repo's real
# metadata lives in a host-private temp directory, created once per task
# before the agent ever runs and never mounted into any container, and
# every host-side git call names it explicitly with --git-dir/--work-tree,
# never `-C work` (which lets git walk up from work and find work/.git on
# its own). Both hardening flags (-c core.hooksPath=/dev/null, -c
# core.fsmonitor=false) are set on every call too, defense in depth: with
# --git-dir explicit, git already never reads work/.git's config for
# anything, but these two are cheap and close the same class of risk one
# more way. No filter.*.clean/smudge is ever defined in this private
# config (only core.* keys are ever set below): a .gitattributes the agent
# adds inside work can NAME a filter, but naming one with no matching
# [filter "<name>"] section in the config that is actually consulted (this
# private one) runs no command at all.


def _git_priv(git_dir: pathlib.Path, work: pathlib.Path, *args, check: bool = True) -> subprocess.CompletedProcess:
    # cwd=work matters specifically for `apply`: unlike most git porcelain,
    # `git apply` resolves a patch's target paths and its "does this file
    # already exist" pre-check against the PROCESS's current working
    # directory, not against --work-tree (a real, measured git quirk: with
    # cwd left at this project's own checkout, `apply` reported a conflict
    # against this repo's own bench/.gitattributes, a file that has nothing
    # to do with the task being graded). Harmless for the other calls,
    # which are cwd-independent once --git-dir/--work-tree are explicit.
    return subprocess.run(
        ["git", f"--git-dir={git_dir}", f"--work-tree={work}",
         "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
        cwd=str(work), capture_output=True, text=True, check=check, timeout=DOCKER_ADMIN_TIMEOUT_S)


def _discard_stray_git(work: pathlib.Path) -> None:
    """Remove whatever work/.git exists right now, unconditionally, without
    ever opening or reading it: after _snapshot_base_commit runs, work
    should have none (the pristine one was moved out); if the agent created
    one anyway (or wrote into a leftover), it is attacker-controlled and is
    discarded, never inspected, never used as a repo.
    """
    stray = work / ".git"
    if stray.exists() or stray.is_symlink():
        shutil.rmtree(stray, ignore_errors=True)


def _snapshot_base_commit(work: pathlib.Path, private_git_dir: pathlib.Path) -> str:
    """Move the pristine repo's real metadata OUT of `work` into
    private_git_dir (a host-private temp directory, never mounted into any
    container, created by the caller before this runs) and return the base
    commit's sha. A private task's extracted tarball already ships a
    single-commit .git (benchrun.tasks.builder): that exact directory is
    RELOCATED, not re-hashed, so this is still the same history, just moved
    before the agent ever gets a chance to touch it. A swe/ task's
    copied-out /testbed may not carry one from the source repository, so
    one is created directly at private_git_dir via GIT_DIR/GIT_WORK_TREE
    (never `git init` inside work itself). Either way, work has no `.git`
    of its own once this returns: only private_git_dir does.
    """
    _discard_stray_git(work)
    existing_git = work / ".git"
    if existing_git.exists():
        shutil.move(str(existing_git), str(private_git_dir))
    else:
        private_git_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, GIT_DIR=str(private_git_dir), GIT_WORK_TREE=str(work))
        subprocess.run(["git", "init", "-q", "-b", "bench-base"], cwd=str(work), env=env, check=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
        _git_priv(private_git_dir, work, "config", "core.autocrlf", "false")
        _git_priv(private_git_dir, work, "-c", "core.excludesFile=", "add", "-A")
        _git_priv(private_git_dir, work, *GIT_IDENTITY_ARGS, "commit", "-q", "--allow-empty", "-m", "base")
    return _git_priv(private_git_dir, work, "rev-parse", "HEAD").stdout.strip()


def _capture_and_reset_diff(work: pathlib.Path, private_git_dir: pathlib.Path, base_sha: str) -> str:
    """Stage every change the agent made (new, modified, deleted files),
    capture the full diff against base_sha, then hard-reset work back to
    that exact pristine state (`clean -fdx` also removes gitignored paths,
    since the goal is BYTE identity with the original tree). Any work/.git
    the agent left is discarded first, unread: see the module note above.
    """
    _discard_stray_git(work)
    _git_priv(private_git_dir, work, "add", "-A")
    diff = _git_priv(private_git_dir, work, "diff", "--cached", base_sha).stdout
    _git_priv(private_git_dir, work, "reset", "--hard", base_sha)
    _git_priv(private_git_dir, work, "clean", "-fdx", "-e", ".git")
    return diff


def _apply_filtered_diff(work: pathlib.Path, private_git_dir: pathlib.Path, diff_text: str) -> None:
    if not diff_text.strip():
        return
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as fh:
        fh.write(diff_text)
        diff_path = fh.name
    try:
        applied = _git_priv(private_git_dir, work, "apply", "--whitespace=nowarn", diff_path, check=False)
        if applied.returncode != 0:
            raise RuntimeError(f"could not apply the agent's own (filtered) diff: {applied.stderr[-2000:]}")
    finally:
        pathlib.Path(diff_path).unlink(missing_ok=True)


class AgenticSuite:
    name = "agentic"

    def __init__(self, tasks_dir: pathlib.Path, max_turns: int = 40, timeout_s: int = 1800,
                 prepare_timeout_s: int = PREPARE_TIMEOUT_S,
                 repos_root: pathlib.Path | None = None, cache_dir: pathlib.Path | None = None):
        self.tasks_dir = pathlib.Path(tasks_dir)
        self.max_turns = max_turns
        self.timeout_s = timeout_s
        self.prepare_timeout_s = prepare_timeout_s
        # Resolved lazily (see _resolve_private_tarball): most runs never
        # hit a cache miss, and BENCH_REPOS_ROOT (bench/.env.example) should
        # only be required of the runs that actually need to rebuild a
        # tarball, not of every test or swe/*-only run.
        self._repos_root_arg = pathlib.Path(repos_root) if repos_root else None
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else self.tasks_dir.parent / ".cache" / "tasks"

    def _resolve_private_tarball(self, task: dict) -> pathlib.Path:
        cached = self.cache_dir / f"{task['tree_id']}.tar.gz"
        if cached.exists():
            return cached
        repos_root = self._repos_root_arg or _default_repos_root()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return rebuild_task(task, repos_root, self.cache_dir)

    def run(self, ctx) -> list[dict]:
        task_dirs = sorted(p for p in self.tasks_dir.iterdir() if (p / "task.json").exists())
        all_tasks = [(d, json.loads((d / "task.json").read_text(encoding="utf-8"))) for d in task_dirs]

        # A served window too small for even Claude Code's own first request
        # plus the task's prompt is not a slow config, it is one that cannot
        # run this task at all (see CLAUDE_CODE_MIN_WINDOW_TOKENS above): such
        # tasks are skipped and recorded, never silently sent.
        window = ctx.cfg.ctx_proven or max_ctx(ctx.cfg)
        fits = [(d, t) for d, t in all_tasks if window >= _min_window_for_task(t)]
        skipped = [t["id"] for d, t in all_tasks if window < _min_window_for_task(t)]
        if skipped:
            ctx.out_dir.mkdir(parents=True, exist_ok=True)
            (ctx.out_dir / "agentic_skipped.json").write_text(json.dumps(
                {"skipped_task_ids": skipped, "window": window,
                 "claude_code_min_window_tokens": CLAUDE_CODE_MIN_WINDOW_TOKENS}))
        if not fits:
            raise SuiteSkipped(
                f"served window {window} tokens is below the measured Claude Code + "
                f"task-prompt minimum ({CLAUDE_CODE_MIN_WINDOW_TOKENS} tokens) for every "
                "task in this run"
            )

        gw_host = urlparse(ctx.base_url).hostname

        rows = []
        for _task_dir, task in fits:
            source = task.get("source", "private")
            is_swe = source == "swe"
            isolated_network = _isolated_network_name(task["id"], ctx.rep)
            container = f"agent-{ctx.rep}-{task['id']}"
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
            swe_container = f"swe-env-{ctx.rep}-{task['id']}" if is_swe else None
            if swe_container:
                subprocess.run(["docker", "rm", "-f", swe_container], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)

            work = ctx.out_dir / f"rep{ctx.rep}-{task['id']}"
            work.mkdir(parents=True)
            # Host-private, never mounted into any container: the pristine
            # repo's real git metadata lives here once _snapshot_base_commit
            # runs, never inside work itself (see the note above
            # _git_priv). Cleaned up in the finally block below.
            base_git_scratch = pathlib.Path(tempfile.mkdtemp(prefix="bench-basegit-"))
            private_git_dir = base_git_scratch / "gitdir"
            if is_swe:
                # The SWE image is linux/amd64 (emulated on this arm64 host)
                # and only ever runs prepare/grade (plain Python). Claude
                # Code's Bun runtime aborts under that emulation (measured
                # live, see module docstring), so the agent phase runs in a
                # SECOND, native container from the same bench-agent image
                # private tasks use, on a copy of /testbed extracted here.
                subprocess.run(
                    ["docker", "run", "-d", "--name", swe_container, "--platform", SWE_PLATFORM,
                     "--network", "bridge", "--entrypoint", "sleep", task["docker_image"], "infinity"],
                    check=True, capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
                subprocess.run(["docker", "cp", f"{swe_container}:/testbed/.", str(work)],
                                check=True, capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
                base_sha = _snapshot_base_commit(work, private_git_dir)
                task_network, sidecar_names = None, []
                subprocess.run(
                    ["docker", "run", "-d", "--name", container, "--network", "bridge",
                     "--user", "agent", "-e", "HOME=/home/agent",
                     "-v", f"{work}:/repo", "-w", "/repo",
                     "--entrypoint", "sleep", IMAGE, "infinity"],
                    check=True, capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
            else:
                tar_path = self._resolve_private_tarball(task)
                with tarfile.open(tar_path, "r:*") as tf:
                    tf.extractall(work, filter="data")
                base_sha = _snapshot_base_commit(work, private_git_dir)
                sidecars_cfg = task.get("sidecars") or []
                task_network, sidecar_names = _start_sidecars(task["id"], ctx.rep, sidecars_cfg)
                sidecar_env = []
                for sidecar in sidecars_cfg:
                    sidecar_env += ["-e", f"{sidecar['name'].upper()}_HOST={sidecar['name']}"]
                subprocess.run(
                    ["docker", "run", "-d", "--name", container, "--network", "bridge",
                     "--user", "agent", "-e", "HOME=/home/agent", *sidecar_env,
                     "-v", f"{work}:/repo", "-w", "/repo",
                     "--entrypoint", "sleep", IMAGE, "infinity"],
                    check=True, capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
                if task_network:
                    subprocess.run(["docker", "network", "connect", task_network, container],
                                    check=True, capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
            try:
                # prepare: infrastructure phase. A failure here means the
                # environment cannot even attempt this task and is not a
                # model result: it raises, aborting the whole suite run, the
                # same way a missing result file aborts bfcl/lcb. Sidecars
                # (if any) are already reachable here, for migrations/seeds.
                # A SWE task's own prepare_cmd (none currently used) would
                # run against swe_container, not the native agent container.
                # prepare's own timeout, separate from the agent/grade
                # budget (self.timeout_s): a real Ruby task's dependency
                # compile was measured over 90 minutes once (SOURCES.md), a
                # cold-cache install is legitimately slow, not hung. A
                # TimeoutExpired here is still an infrastructure fault (the
                # environment could not even finish preparing), so it is
                # turned into a clear RuntimeError, never left to propagate
                # as a bare TimeoutExpired with a less informative message.
                prepare_exit_code = None
                prepare_cmd = None if is_swe else task.get("prepare_cmd")
                try:
                    if is_swe and needs_uv(task["test_cmd"]):
                        uv_proc = _run(["docker", "exec", swe_container, "bash", "-c", "pip install -q uv"],
                                        self.prepare_timeout_s)
                        prepare_exit_code = uv_proc.returncode
                        if prepare_exit_code != 0:
                            raise RuntimeError(
                                f"could not install uv for swe task {task['id']}: {uv_proc.stderr[-2000:]}")
                    elif prepare_cmd:
                        prepare_proc = _run(
                            ["docker", "exec", container, "bash", "-c", prepare_cmd],
                            self.prepare_timeout_s)
                        prepare_exit_code = prepare_proc.returncode
                        if prepare_exit_code != 0:
                            raise RuntimeError(
                                f"prepare_cmd failed for task {task['id']}: {prepare_proc.stderr[-2000:]}")
                except subprocess.TimeoutExpired:
                    raise RuntimeError(
                        f"prepare_cmd for task {task['id']} did not finish within "
                        f"{self.prepare_timeout_s}s: infrastructure fault, not a model result"
                    ) from None

                _ensure_isolated_network(isolated_network)
                subprocess.run(["docker", "network", "connect", isolated_network, gw_host],
                                capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
                subprocess.run(["docker", "network", "disconnect", "bridge", container],
                                check=True, capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
                if task_network:
                    # Sidecars never give the agent phase egress either: the
                    # main container leaves the task's own network too,
                    # exactly like it leaves the internet, and only rejoins
                    # it for grading.
                    subprocess.run(["docker", "network", "disconnect", task_network, container],
                                    check=True, capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
                subprocess.run(["docker", "network", "connect", isolated_network, container],
                                check=True, capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
                # The gateway must answer before the agent call: claude -p
                # hangs silently for 120+ s otherwise, which would look
                # identical to a slow but working model instead of an
                # infrastructure problem.
                _check_gateway_reachable(container, ctx.base_url, isolated_network)

                # agent: model phase. A timeout, a non-zero exit or hitting
                # max_turns is a result to record, not a reason to abort the
                # rest of the task set.
                agent_exit_code = None
                agent_timed_out = False
                parsed: dict = {}
                t0 = time.monotonic()
                try:
                    agent_proc = _run(
                        ["docker", "exec",
                         "-e", f"ANTHROPIC_BASE_URL={ctx.base_url}",
                         "-e", "ANTHROPIC_API_KEY=dummy",
                         "-e", "CLAUDE_CODE_ATTRIBUTION_HEADER=0",
                         container, "claude", "-p", task["prompt"],
                         "--output-format", "json", "--max-turns", str(self.max_turns),
                         "--permission-mode", "bypassPermissions"],
                        self.timeout_s)
                    agent_exit_code = agent_proc.returncode
                    try:
                        parsed = parse_claude_json(agent_proc.stdout)
                    except ValueError:
                        parsed = {}
                except subprocess.TimeoutExpired:
                    agent_timed_out = True
                duration_s = time.monotonic() - t0

                hit_max_turns = parsed.get("num_turns") is not None and parsed["num_turns"] >= self.max_turns
                if agent_timed_out:
                    reason = "timeout"
                elif agent_exit_code not in (None, 0):
                    reason = "agent_exit_nonzero"
                elif hit_max_turns:
                    reason = "max_turns"
                elif parsed.get("is_error"):
                    reason = "agent_error"
                else:
                    reason = None

                subprocess.run(["docker", "network", "disconnect", isolated_network, container],
                                check=True, capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
                _remove_isolated_network(isolated_network, gw_host)
                if task_network:
                    # Grading gets the sidecars back (a real database/cache
                    # is what makes the test suite meaningful), but never
                    # the internet: only task_network is reconnected, not
                    # bridge.
                    subprocess.run(["docker", "network", "connect", task_network, container],
                                    check=True, capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)

                # Grading integrity: the agent had write access to the whole
                # tree (private: could delete/gut the failing test; swe/:
                # could add a conftest.py or edit a PASS_TO_PASS test to
                # fabricate a pass). Compute the agent's diff against the
                # pristine base captured before the agent ran, drop every
                # file that looks like a test file/conftest.py/test config,
                # reset `work` back to pristine and apply only what remains.
                # Grading below runs on THAT tree, never on the agent's raw
                # edits. `work` is a host directory bind-mounted into
                # `container` for both paths, so this happens once, host
                # side, for private and swe/ alike.
                agent_raw_diff = _capture_and_reset_diff(work, private_git_dir, base_sha)
                graded_diff = _filter_diff_dropping_test_files(agent_raw_diff)
                _apply_filtered_diff(work, private_git_dir, graded_diff)
                test_diff_dropped = graded_diff != agent_raw_diff

                # grade: always runs, even after a model-side failure above,
                # on whatever (filtered) tree state the agent left. A task
                # that timed out mid-edit almost always still fails grading
                # for real, which is the correct, informative result, not a
                # reason to skip it. A SWE task's test_patch is applied
                # here, never before: the agent phase must never see it,
                # and grading itself runs back in swe_container (the real
                # dependency environment), against the filtered edits
                # copied back in. A grade-phase timeout (the agent may have
                # written an infinite loop) is a MODEL result, passed=0
                # with a reason, never an uncaught exception that would
                # abort every remaining task in the run.
                test_exit_code = None
                grade_outcomes: dict[str, str] = {}
                grade_timed_out = False
                if is_swe:
                    subprocess.run(["docker", "cp", f"{work}/.", f"{swe_container}:/testbed/"],
                                    check=True, capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
                    patch_applied = _apply_diff(swe_container, task["test_patch"], "/tmp/test_patch.diff")
                    test_cmd = task["test_cmd"]
                    try:
                        test_proc = _run(["docker", "exec", swe_container, "bash", "-c",
                                           f"cd /testbed && {test_cmd}"], self.timeout_s)
                        test_exit_code = test_proc.returncode
                        grade_outcomes = parse_outcomes(test_proc.stdout + "\n" + test_proc.stderr)
                    except subprocess.TimeoutExpired:
                        grade_timed_out = True
                    swe_passed = (
                        not grade_timed_out
                        and patch_applied.returncode == 0
                        and _swe_grade_passed(grade_outcomes, task["fail_to_pass"], task["pass_to_pass"],
                                               test_exit_code)
                    )
                else:
                    try:
                        test_proc = _run(["docker", "exec", container, "bash", "-c", task["test_cmd"]],
                                          self.timeout_s)
                        test_exit_code = test_proc.returncode
                    except subprocess.TimeoutExpired:
                        grade_timed_out = True
            finally:
                subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
                if swe_container:
                    subprocess.run(["docker", "rm", "-f", swe_container], capture_output=True, timeout=DOCKER_ADMIN_TIMEOUT_S)
                _stop_sidecars(task_network, sidecar_names)
                shutil.rmtree(base_git_scratch, ignore_errors=True)

            if grade_timed_out:
                passed = False
            else:
                passed = swe_passed if is_swe else (test_exit_code == 0)
            rows.append({
                "item_id": f"{source}/{task['id']}",
                "passed": 1 if passed else 0,
                "detail": {
                    "turns": parsed.get("num_turns"),
                    "duration_s": round(duration_s, 3),
                    "prepare_exit_code": prepare_exit_code,
                    "agent_exit_code": agent_exit_code,
                    "agent_timed_out": agent_timed_out,
                    "reason": "grade_timeout" if grade_timed_out else reason,
                    "test_exit_code": test_exit_code,
                    "grade_timed_out": grade_timed_out,
                    "test_diff_dropped": test_diff_dropped,
                    "cost_usd": parsed.get("total_cost_usd"),
                    "is_error": parsed.get("is_error"),
                },
            })
        return rows
