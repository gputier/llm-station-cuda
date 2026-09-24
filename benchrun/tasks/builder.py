"""Build a private agentic task from a fix commit of one of our repositories.

The agent gets the PARENT tree with a fresh one-commit history: if the fix were
reachable (log, reflog, packs), the agent could read the answer instead of
solving the bug.

A task is only valid if the SAME prepare_cmd + test_cmd fails on the parent
commit and passes on the fix commit, in the same environment. Checking the
parent alone is not enough: a missing dependency or a collection error also
returns a non-zero exit code and looks like a valid task while measuring
nothing (reproduced live with pytest absent from the image). Checking the fix
side catches that, because the same missing dependency also fails there.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shlex
import shutil
import subprocess
import tarfile
import tempfile

from benchrun.tasks import GIT_IDENTITY_ARGS

# Bound on how much of the parent tree's failing test output goes into the
# prompt: enough to show which assertion failed, never the fix. The tail is
# kept, not the head, because RSpec, cargo test, go test and vitest all print
# their failure summary at the end of the run.
MAX_PROMPT_OUTPUT_CHARS = 4000


class TaskLeakError(Exception):
    pass


class PrepareError(Exception):
    """Raised when prepare_cmd itself fails, on either the parent or the fix
    tree. This is an infrastructure fault (missing dependency, broken image),
    never counted as "the test fails before the fix": a task must fail for a
    reason the fix under test actually addresses.
    """


class TaskRebuildError(Exception):
    """Raised when a rebuilt tree does not match the tree id task.json
    recorded: the source repository's content at the recorded commits is not
    what it was when the task was built, so the tarball cannot be
    reconstructed identically.
    """


def _copytree_retrying(src: pathlib.Path, dst: pathlib.Path, attempts: int = 3) -> None:
    """shutil.copytree of a directory holding a freshly created .git can
    raise a transient FileNotFoundError walking .git/objects (macOS, Python
    3.14): not a git content problem (the "vanished" object is present on
    the next attempt), a real filesystem flake worth a bounded retry, not
    silenced: the last attempt's exception propagates if every retry fails.
    """
    last_error: OSError | None = None
    for _ in range(attempts):
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        try:
            shutil.copytree(src, dst)
            return
        except OSError as exc:
            last_error = exc
    raise last_error


def _git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], check=check, capture_output=True, text=True)


def _run(cmd: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(shlex.split(cmd), cwd=cwd, capture_output=True, text=True)


def _reinit_history(root: pathlib.Path) -> None:
    # A pruned subdir copy carries no .git of its own (the source tree's
    # .git lives at the monorepo root, above the subdir): only remove it
    # when present.
    shutil.rmtree(root / ".git", ignore_errors=True)
    _git(root, "init", "-q", "-b", "main")
    # core.autocrlf=false, set again here: `git init` above throws away the
    # OLD .git (where _checkout_copy had already pinned core.autocrlf=false)
    # and creates a brand new one with no config of its own, so `add -A`
    # right after falls back to the operator's ambient global config. With an
    # ambient autocrlf setting, `add -A` can change a checked-out file's blob
    # hash from what it hashed as right after checkout. Pinning it again
    # here, not just at clone time, is what actually matters: this is the
    # git invocation that recomputes blobs from the working tree.
    _git(root, "config", "core.autocrlf", "false")
    # core.ignorecase=false, core.precomposeunicode=false: both default to
    # true on macOS (APFS is case-insensitive and normalizes filenames to
    # NFD), and `git init` bakes the DETECTED default into the fresh
    # .git/config. A shipped task built on macOS would carry a config
    # subtly different from one built on Linux CI, even though the actual
    # tree content (what matters, what tree_id hashes) does not depend on
    # it: pinning both off keeps .git/config itself identical regardless of
    # which machine builds or rebuilds the task.
    _git(root, "config", "core.ignorecase", "false")
    _git(root, "config", "core.precomposeunicode", "false")
    # gc.auto=0: "commit" and "reflog expire" can both trigger a background
    # `git gc --auto` that repacks loose objects and deletes their loose
    # copies. That races with the caller's own shutil.copytree of this same
    # .git right after this function returns: a loose object file present
    # when copytree lists the directory can be gone by the time copytree
    # reaches it (FileNotFoundError). Auto-gc has nothing useful to do on a
    # single-commit repository anyway.
    #
    # core.excludesFile="": a fresh `git init` still inherits the operator's
    # own GLOBAL gitignore (~/.gitconfig's core.excludesFile), which is
    # personal machine config, not a property of the source repository. A
    # file genuinely tracked at the built commit but matched by that global
    # ignore would silently drop from `add -A` on this brand new,
    # not-yet-tracked-anywhere repo, making the shipped tree depend on
    # whoever's machine built it. Blanking core.excludesFile makes `add -A`
    # see only the checked-out commit's own tracked content (plus its own
    # committed .gitignore, if any), which is what a task's tree must be.
    _git(root, "-c", "gc.auto=0", "-c", "core.excludesFile=",
         *GIT_IDENTITY_ARGS, "add", "-A")
    _git(root, "-c", "gc.auto=0", *GIT_IDENTITY_ARGS, "commit", "-qm", "snapshot")
    # A fresh commit still writes .git/logs/HEAD (reflog on by default): wipe
    # it and expire any reflog entry, or the tarball would carry a log trail
    # even though rev-list --all --count is 1.
    shutil.rmtree(root / ".git" / "logs", ignore_errors=True)
    _git(root, "-c", "gc.auto=0", "reflog", "expire", "--expire=now", "--all")


def _assert_no_leak(root: pathlib.Path, fix_commit: str) -> None:
    if _git(root, "cat-file", "-e", fix_commit, check=False).returncode == 0:
        raise TaskLeakError(f"fix commit {fix_commit} reachable in task tree")
    count = _git(root, "rev-list", "--all", "--count").stdout.strip()
    if count != "1":
        raise TaskLeakError(f"task history has {count} commits, expected 1")


def _checkout_copy(repo: pathlib.Path, commit: str, dest: pathlib.Path, restore_from: str | None = None) -> None:
    """Clone repo, check out commit into dest. If restore_from is given, also
    restore every path touched between commit and restore_from that looks
    like a test, from restore_from's blob: used to bring the fix commit's
    test changes onto the parent tree, so the parent is graded by the same
    tests the fix is graded by.
    """
    subprocess.run(["git", "clone", "-q", "--no-local", "--no-checkout", str(repo), str(dest)], check=True)
    # Pin checkout normalization before the first checkout: the host's
    # ambient core.autocrlf (global git config, not this repository's own)
    # can change a text-attributed file's blob content on checkout between
    # two runs on the same machine, breaking tree-id reproducibility.
    # Pinning it here makes checkout deterministic regardless of ambient
    # config.
    _git(dest, "config", "core.autocrlf", "false")
    _git(dest, "checkout", "-q", commit)
    if restore_from:
        changed = _git(repo, "diff", "--name-only", commit, restore_from).stdout.split()
        for path in changed:
            if "test" in path.lower():
                blob = _git(repo, "show", f"{restore_from}:{path}", check=False)
                if blob.returncode == 0:
                    (dest / path).parent.mkdir(parents=True, exist_ok=True)
                    (dest / path).write_text(blob.stdout)


def _prepare_and_test(root: pathlib.Path, subdir: str | None, prepare_cmd: str | None,
                       test_cmd: str, label: str) -> subprocess.CompletedProcess:
    target = (root / subdir) if subdir else root
    if prepare_cmd:
        prep = _run(prepare_cmd, target)
        if prep.returncode != 0:
            raise PrepareError(f"prepare_cmd failed on the {label} tree: {prep.stderr[-2000:]}")
    return _run(test_cmd, target)


def _build_prompt(test_cmd: str, parent_result: subprocess.CompletedProcess) -> str:
    output = (parent_result.stdout + "\n" + parent_result.stderr).strip()
    trimmed = output[-MAX_PROMPT_OUTPUT_CHARS:]
    return ("A test in this repository fails. Find the cause and fix the code so the whole "
            f"test suite passes with: {test_cmd}. Do not modify tests. Failing test output "
            f"captured on the current tree:\n{trimmed}")


def _default_cache_dir(out: pathlib.Path) -> pathlib.Path:
    # Sibling of the tasks directory, e.g. Bench-LLM/.cache/tasks next to
    # Bench-LLM/tasks: gitignored, never committed, safe to delete entirely
    # (rebuild_task recreates any tarball it is missing from task.json's own
    # recorded fields).
    return pathlib.Path(out).parent / ".cache" / "tasks"


def _prune_to_subdir(work: pathlib.Path, subdir: str | None, tmp: str) -> pathlib.Path:
    """Copy subdir's own content out into a fresh directory under tmp and
    return it; returns work unchanged if subdir is not set. A monorepo's
    other services must never end up in a task's shipped tree."""
    if not subdir:
        return work
    pruned = pathlib.Path(tmp) / "pruned"
    shutil.copytree(work / subdir, pruned)
    return pruned


def _build_tree(repo: pathlib.Path, parent_commit: str, fix_commit: str,
                 subdir: str | None, dest: pathlib.Path) -> str:
    """Produce the exact tree a task ships (parent commit, fix commit's test
    files restored onto it, pruned to subdir if given, history reinitialised
    to one leak-free commit) at dest, and return that commit's tree id
    (`git rev-parse HEAD^{tree}`), which is deterministic for the same
    (repo content at parent_commit and fix_commit, subdir): the same inputs
    always produce the same tree id, which is what makes a tarball a derived,
    rebuildable artifact instead of something that has to live in git.
    """
    # Manual mkdtemp + finally instead of the TemporaryDirectory context
    # manager: its own __exit__ cleanup can raise "Directory not empty"
    # removing a freshly created .git, after the real work below has already
    # succeeded. The real result must not be lost to a flaky cleanup of a
    # directory nothing needs anymore.
    tmp = tempfile.mkdtemp()
    try:
        work = pathlib.Path(tmp) / "repo"
        _checkout_copy(repo, parent_commit, work, restore_from=fix_commit)
        work = _prune_to_subdir(work, subdir, tmp)
        _reinit_history(work)
        _assert_no_leak(work, fix_commit)
        _copytree_retrying(work, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _git(dest, "rev-parse", "HEAD^{tree}").stdout.strip()


def _tar_tree(tree_dir: pathlib.Path, tar_path: pathlib.Path) -> None:
    tar_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "w:gz") as tf:
        for p in sorted(tree_dir.rglob("*")):
            tf.add(p, arcname=str(p.relative_to(tree_dir)), recursive=False)


def build_task(
    repo: pathlib.Path,
    fix_commit: str,
    test_cmd: str,
    out: pathlib.Path,
    prepare_cmd: str | None = None,
    subdir: str | None = None,
    repo_name: str | None = None,
    cache_dir: pathlib.Path | None = None,
) -> pathlib.Path:
    parent_commit = _git(repo, "rev-parse", f"{fix_commit}^").stdout.strip()
    task_id = hashlib.sha256(f"{repo.name}:{fix_commit}".encode()).hexdigest()[:12]
    dest = pathlib.Path(out) / task_id
    dest.mkdir(parents=True, exist_ok=True)
    cache_dir = pathlib.Path(cache_dir) if cache_dir else _default_cache_dir(out)
    # See _build_tree's own comment: manual mkdtemp + finally, not the
    # TemporaryDirectory context manager, so a flaky cleanup of a freshly
    # created .git cannot lose a build that already succeeded.
    tmp = tempfile.mkdtemp()
    try:
        work = pathlib.Path(tmp) / "repo"
        _checkout_copy(repo, parent_commit, work, restore_from=fix_commit)

        # Verification runs on THROWAWAY copies, never on the tree that gets
        # tarred: prepare_cmd (bundle install, pnpm install, cargo fetch, go
        # mod download) writes dependency trees (node_modules, vendor/bundle,
        # target/) into its cwd, and those must never end up in the private
        # tarball: without this copy, a single task's tarball can balloon by
        # hundreds of megabytes of installed dependencies.
        verify_parent = pathlib.Path(tmp) / "verify_parent"
        shutil.copytree(work, verify_parent)
        parent_result = _prepare_and_test(verify_parent, subdir, prepare_cmd, test_cmd, "parent")
        if parent_result.returncode == 0:
            raise ValueError("tests must fail before the fix, or the task measures nothing")

        # The fix side is checked out fresh (not restored: it already has its
        # own tests as committed) and must pass the SAME prepare_cmd and
        # test_cmd, in the same environment. A candidate that fails on the
        # parent only because a dependency or the test file itself is
        # missing also fails here, for the same reason, and is rejected: a
        # real fix commit resolves what test_cmd checks, proven both ways.
        fix_tree = pathlib.Path(tmp) / "fix_tree"
        _checkout_copy(repo, fix_commit, fix_tree)
        fix_result = _prepare_and_test(fix_tree, subdir, prepare_cmd, test_cmd, "fix")
        if fix_result.returncode != 0:
            raise ValueError(
                "tests must pass after the fix, or the parent's failure is not what this "
                f"fix addresses: {fix_result.stdout[-500:]}{fix_result.stderr[-500:]}"
            )

        prompt = _build_prompt(test_cmd, parent_result)

        work = _prune_to_subdir(work, subdir, tmp)
        _reinit_history(work)
        _assert_no_leak(work, fix_commit)
        tree_id = _git(work, "rev-parse", "HEAD^{tree}").stdout.strip()
        _tar_tree(work, cache_dir / f"{tree_id}.tar.gz")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # The tarball itself is a derived artifact and lives only in cache_dir,
    # outside git (gitignored in Bench-LLM): task.json carries everything
    # needed to reproduce it byte-for-byte (repo_name, the two commits,
    # subdir) plus the tree id to check a rebuild against, so SETS.sha256
    # can pin task.json and tree ids without ever hashing tarball bytes.
    (dest / "task.json").write_text(json.dumps(
        {"id": task_id, "prompt": prompt, "prepare_cmd": prepare_cmd, "test_cmd": test_cmd,
         "fail_before": True, "repo": repo_name or repo.name, "fix_commit": fix_commit,
         "parent_commit": parent_commit, "subdir": subdir, "tree_id": tree_id}, indent=2))
    return dest


def rebuild_task(task: dict, repos_root: pathlib.Path, cache_dir: pathlib.Path) -> pathlib.Path:
    """Rebuild a private task's tarball into cache_dir from task.json's own
    recorded fields (repo, fix_commit, parent_commit, subdir), without
    re-running the dual-validity check (already proven when the task was
    built: only the tree-building steps are needed, and those are
    deterministic). Raises TaskRebuildError if the rebuilt tree id does not
    match the one task.json recorded. Returns the path to the tarball in
    cache_dir, named after the tree id, so a later lookup by the same id is
    a cache hit without asking this function anything.
    """
    repo = pathlib.Path(repos_root) / task["repo"]
    expected_tree_id = task["tree_id"]
    cache_dir = pathlib.Path(cache_dir)
    tmp = tempfile.mkdtemp()
    try:
        work = pathlib.Path(tmp) / "tree"
        tree_id = _build_tree(repo, task["parent_commit"], task["fix_commit"], task.get("subdir"), work)
        if tree_id != expected_tree_id:
            raise TaskRebuildError(
                f"rebuilt tree {tree_id} for task {task['id']} does not match the recorded "
                f"{expected_tree_id}: {task['repo']} at {task['parent_commit']}/{task['fix_commit']} "
                "no longer reproduces the tarball this task was built from"
            )
        tar_path = cache_dir / f"{tree_id}.tar.gz"
        _tar_tree(work, tar_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return tar_path
