"""Question-id selector for LiveBench, run inside the pinned bench-livebench
image (bind-mounted at runtime by benchrun.suites.livebench, never baked into
the image: no Dockerfile change, no rebuild needed for this mechanism).

Reuses the pinned harness's own listing functions unmodified
(livebench.common.get_categories_tasks and load_questions, the exact
functions run_livebench.py's own gen_api_answer.py calls to build its
question list): this script only decides WHICH of the ids they return go
into the run, through --limit (first N in the harness's own dataset order)
and, with --seed, a seeded random sample of N per category instead. It never
touches question content, prompts or scoring: that stays entirely inside the
pinned run_livebench.py / gen_api_answer.py / gen_ground_truth_judgment.py
path, called separately with the resulting ids as --question-id.

Must run with cwd /livebench/livebench (same requirement as run_livebench.py,
module docstring of benchrun.suites.livebench): common.py resolves its own
data paths relative to that directory.

Usage: livebench_select_ids.py --release R --limit N [--seed S] CATEGORY [CATEGORY ...]
Prints one JSON object to stdout: {"<category>": ["<question_id>", ...], ...}.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

# "python /select_ids.py" (this script is bind-mounted at the container
# root, never inside /livebench/livebench) puts the script's OWN directory
# on sys.path[0], not the current working directory: common.py (cwd)
# would otherwise be unimportable even with -w /livebench/livebench set.
sys.path.insert(0, os.getcwd())


def list_category_ids(category_name: str, release_set: set, release: str) -> list[str]:
    """Every question id for one category, in the exact order and under the
    exact release filter run_livebench.py's own gen_api_answer.py uses to
    build its question list (its own loop over get_categories_tasks/tasks,
    no --question-begin/--question-end slicing here since that mechanism
    slices per TASK, not per category, see benchrun.suites.livebench)."""
    from common import get_categories_tasks, load_questions

    categories, tasks = get_categories_tasks(f"live_bench/{category_name}")
    ids: list[str] = []
    for task_name in tasks.get(category_name, []):
        for question in load_questions(categories[category_name], release_set, release, task_name, None):
            ids.append(question["question_id"])
    return ids


def select_ids(categories: list[str], limit: int, seed: int | None, release: str) -> dict[str, list[str]]:
    from common import LIVE_BENCH_RELEASES

    release_set = {r for r in LIVE_BENCH_RELEASES if r <= release}
    selected: dict[str, list[str]] = {}
    for category_name in categories:
        ids = list_category_ids(category_name, release_set, release)
        if seed is None:
            selected[category_name] = ids[:limit]
        else:
            rng = random.Random(f"{seed}:{category_name}")
            selected[category_name] = rng.sample(ids, min(limit, len(ids)))
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("categories", nargs="+")
    args = parser.parse_args()

    selected = select_ids(args.categories, args.limit, args.seed, args.release)
    for category_name, ids in selected.items():
        if not ids:
            raise SystemExit(f"livebench_select_ids: category {category_name} listed no question id")
    json.dump(selected, sys.stdout)


if __name__ == "__main__":
    main()
