import json
import subprocess
import tarfile
import pathlib
import pytest
from benchrun.tasks.builder import build_task, rebuild_task, TaskLeakError, PrepareError, TaskRebuildError


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def _open_tarball(tasks_dir: pathlib.Path, task_dir: pathlib.Path) -> tarfile.TarFile:
    # The tarball is a derived artifact, never inside task_dir: it lives in
    # the cache directory sibling to tasks_dir, named after task.json's own
    # recorded tree_id.
    task = json.loads((task_dir / "task.json").read_text())
    cache_dir = tasks_dir.parent / ".cache" / "tasks"
    return tarfile.open(cache_dir / f"{task['tree_id']}.tar.gz", "r:*")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    (r / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (r / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "init")
    (r / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git(r, "commit", "-qam", "fix: add was subtracting")
    return r


def test_build_task_produces_parent_tree_without_fix(repo, tmp_path):
    fix = git(repo, "rev-parse", "HEAD").strip()
    tasks_dir = tmp_path / "tasks"
    out = build_task(repo, fix, "python -m pytest -q", tasks_dir)
    with _open_tarball(tasks_dir, out) as tf:
        names = tf.getnames()
        calc = tf.extractfile("calc.py").read().decode()
    assert "return a - b" in calc
    assert not any("ORIG_HEAD" in n or "logs/" in n for n in names)


def test_build_task_records_the_rebuild_fields_not_the_tarball_in_the_task_dir(repo, tmp_path):
    fix = git(repo, "rev-parse", "HEAD").strip()
    parent = git(repo, "rev-parse", f"{fix}^").strip()
    tasks_dir = tmp_path / "tasks"
    out = build_task(repo, fix, "python -m pytest -q", tasks_dir, repo_name="acme/calc")
    task = json.loads((out / "task.json").read_text())
    assert task["repo"] == "acme/calc"
    assert task["fix_commit"] == fix
    assert task["parent_commit"] == parent
    assert task["subdir"] is None
    assert len(task["tree_id"]) == 40  # a real git tree sha
    # A private task's directory holds task.json only: the tarball is a
    # derived artifact and lives in the cache directory, never here.
    assert list(out.iterdir()) == [out / "task.json"]


def test_task_builder_rejects_leaking_history(repo, tmp_path, monkeypatch):
    fix = git(repo, "rev-parse", "HEAD").strip()
    import benchrun.tasks.builder as b
    monkeypatch.setattr(b, "_reinit_history", lambda root: None)
    with pytest.raises(TaskLeakError):
        build_task(repo, fix, "python -m pytest -q", tmp_path / "tasks")


def test_build_task_requires_failing_tests_before_fix(repo, tmp_path):
    fix = git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(ValueError, match="fail"):
        build_task(repo, fix, "true", tmp_path / "tasks")


@pytest.fixture
def monorepo(tmp_path):
    r = tmp_path / "mono"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    (r / "unrelated.txt").write_text("not part of the task\n")
    sub = r / "services" / "calc"
    sub.mkdir(parents=True)
    (sub / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (sub / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "init")
    (sub / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git(r, "commit", "-qam", "fix: add was subtracting")
    return r


def test_build_task_subdir_scopes_the_tarball_to_the_service(monorepo, tmp_path):
    fix = git(monorepo, "rev-parse", "HEAD").strip()
    tasks_dir = tmp_path / "tasks"
    out = build_task(monorepo, fix, "python -m pytest -q", tasks_dir, subdir="services/calc")
    with _open_tarball(tasks_dir, out) as tf:
        names = tf.getnames()
        calc = tf.extractfile("calc.py").read().decode()
    assert "return a - b" in calc
    assert not any("unrelated.txt" in n or n.startswith("services/") for n in names)
    task = json.loads((out / "task.json").read_text())
    assert task["subdir"] == "services/calc"


def test_build_task_runs_prepare_cmd_before_checking_the_test_fails(repo, tmp_path):
    fix = git(repo, "rev-parse", "HEAD").strip()
    marker = tmp_path / "prepared.marker"
    out = build_task(
        repo, fix, "python -m pytest -q", tmp_path / "tasks",
        prepare_cmd=f"python -c \"open(r'{marker}', 'w').close()\"",
    )
    assert marker.exists()
    task = json.loads((out / "task.json").read_text())
    assert task["prepare_cmd"].startswith("python -c")


def test_build_task_raises_when_prepare_cmd_fails(repo, tmp_path):
    fix = git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(PrepareError):
        build_task(repo, fix, "python -m pytest -q", tmp_path / "tasks", prepare_cmd="false")


def test_build_task_never_tars_what_prepare_cmd_wrote_into_the_tree(repo, tmp_path):
    # A real prepare_cmd (bundle/pnpm/cargo/go) writes its dependency tree
    # INSIDE the task's own directory, not to some external path: without
    # verifying on a throwaway copy, a task's tarball can balloon with
    # installed dependencies.
    fix = git(repo, "rev-parse", "HEAD").strip()
    tasks_dir = tmp_path / "tasks"
    out = build_task(
        repo, fix, "python -m pytest -q", tasks_dir,
        prepare_cmd="mkdir -p node_modules && touch node_modules/some_dependency.js",
    )
    with _open_tarball(tasks_dir, out) as tf:
        names = tf.getnames()
    assert not any("node_modules" in n for n in names)


def test_build_task_rejects_a_test_command_that_fails_on_both_sides(repo, tmp_path):
    # Reproduces the real bug: a test_cmd naming a file that does not exist
    # returns a non-zero exit code on the parent, which build_task's old,
    # exit-code-only check accepted as "the test fails before the fix". It
    # also fails, for the same reason, on the fix commit: a real task must
    # pass on the fix, and this one never can.
    fix = git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(ValueError, match="pass after the fix"):
        build_task(repo, fix, "python -m pytest nonexistent_test.py", tmp_path / "tasks")


def test_build_task_prompt_never_names_the_fix(repo, tmp_path):
    # The commit subject describes the fix, not the symptom: baking it into
    # the prompt gives the agent the answer. The prompt is built from the
    # real failing test output captured on the parent tree instead.
    fix = git(repo, "rev-parse", "HEAD").strip()
    out = build_task(repo, fix, "python -m pytest -q", tmp_path / "tasks")
    task = json.loads((out / "task.json").read_text())
    assert "subtracting" not in task["prompt"]
    assert "add was" not in task["prompt"]
    # The real assertion failure IS present: this is what makes the prompt
    # useful, not a description of the fix.
    assert "test_add" in task["prompt"]
    assert "assert" in task["prompt"]


def test_rebuild_task_reproduces_the_same_tree_id(repo, tmp_path):
    fix = git(repo, "rev-parse", "HEAD").strip()
    tasks_dir = tmp_path / "tasks"
    out = build_task(repo, fix, "python -m pytest -q", tasks_dir, repo_name="r")
    task = json.loads((out / "task.json").read_text())
    cache_dir = tasks_dir.parent / ".cache" / "tasks"
    cached_tarball = cache_dir / f"{task['tree_id']}.tar.gz"
    assert cached_tarball.exists()
    cached_tarball.unlink()  # simulate a missing/pruned cache

    rebuilt = rebuild_task(task, repos_root=tmp_path, cache_dir=cache_dir)

    assert rebuilt == cached_tarball
    assert cached_tarball.exists()
    with tarfile.open(rebuilt, "r:*") as tf:
        calc = tf.extractfile("calc.py").read().decode()
    assert "return a - b" in calc


def test_rebuild_task_raises_when_the_source_no_longer_matches(repo, tmp_path):
    fix = git(repo, "rev-parse", "HEAD").strip()
    tasks_dir = tmp_path / "tasks"
    out = build_task(repo, fix, "python -m pytest -q", tasks_dir, repo_name="r")
    task = json.loads((out / "task.json").read_text())
    task["tree_id"] = "0" * 40  # a tree id that cannot match anything real

    with pytest.raises(TaskRebuildError):
        rebuild_task(task, repos_root=tmp_path, cache_dir=tasks_dir.parent / ".cache" / "tasks")
