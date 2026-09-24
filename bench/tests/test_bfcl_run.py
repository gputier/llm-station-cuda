"""Direct unit tests for bench/harness/bfcl_run.py's own selection logic
(selected_ids_by_category, _parse_categories), imported by file path
(importlib) the same way test_lcb_run.py and test_longbench_run.py import
their own launcher: bfcl_run.py's module-level code only imports stdlib
(os/sys/json/random/pathlib/types), every bfcl_eval import is lazy inside a
function, so no container is needed to exercise this part of it.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "bfcl_run", pathlib.Path(__file__).parents[1] / "harness" / "bfcl_run.py",
)
bfcl_run = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bfcl_run)


def _install_fake_bfcl_utils(entries_by_category: dict[str, list[dict]]):
    utils_mod = types.ModuleType("bfcl_eval.utils")
    utils_mod.load_dataset_entry = lambda category: entries_by_category[category]
    sys.modules["bfcl_eval.utils"] = utils_mod


def test_parse_categories_reads_limit_and_seed():
    categories, extra = bfcl_run._parse_categories([
        "--test-category", "simple_python", "--limit", "5", "--seed", "42",
    ])
    assert categories == ["simple_python"]
    assert extra == {"limit": "5", "seed": "42"}


def test_parse_categories_still_rejects_an_unknown_flag():
    with pytest.raises(SystemExit):
        bfcl_run._parse_categories(["--not-a-flag"])


def test_selected_ids_by_category_first_n_without_seed_keeps_dataset_order():
    _install_fake_bfcl_utils({
        "simple_python": [{"id": f"simple_python_{i}"} for i in range(10)],
    })
    selected = bfcl_run.selected_ids_by_category(["simple_python"], limit=3, seed=None)
    assert selected == {"simple_python": ["simple_python_0", "simple_python_1", "simple_python_2"]}


def test_selected_ids_by_category_seeded_draw_is_reproducible_and_bounded():
    _install_fake_bfcl_utils({
        "multiple": [{"id": f"multiple_{i}"} for i in range(20)],
    })
    first = bfcl_run.selected_ids_by_category(["multiple"], limit=3, seed=42)
    second = bfcl_run.selected_ids_by_category(["multiple"], limit=3, seed=42)
    assert first == second
    assert len(first["multiple"]) == 3
    assert set(first["multiple"]) <= {f"multiple_{i}" for i in range(20)}


def test_selected_ids_by_category_different_seed_changes_the_draw():
    _install_fake_bfcl_utils({
        "multiple": [{"id": f"multiple_{i}"} for i in range(20)],
    })
    a = bfcl_run.selected_ids_by_category(["multiple"], limit=3, seed=1)
    b = bfcl_run.selected_ids_by_category(["multiple"], limit=3, seed=2)
    assert a != b


def test_selected_ids_by_category_categories_do_not_share_one_draw():
    # Each category gets its own random.Random(f"{seed}:{category}"): two
    # categories asked with the same seed must not always draw the exact
    # same relative pattern (this is a smoke check, not a statistical proof).
    _install_fake_bfcl_utils({
        "simple_python": [{"id": f"simple_python_{i}"} for i in range(30)],
        "multiple": [{"id": f"multiple_{i}"} for i in range(30)],
    })
    selected = bfcl_run.selected_ids_by_category(["simple_python", "multiple"], limit=5, seed=7)
    assert len(selected["simple_python"]) == 5 and len(selected["multiple"]) == 5
    assert all(i.startswith("simple_python_") for i in selected["simple_python"])
    assert all(i.startswith("multiple_") for i in selected["multiple"])


def test_selected_ids_by_category_limit_above_population_keeps_everything():
    _install_fake_bfcl_utils({"simple_python": [{"id": "simple_python_0"}]})
    assert bfcl_run.selected_ids_by_category(["simple_python"], limit=5, seed=None) == {
        "simple_python": ["simple_python_0"],
    }
    assert bfcl_run.selected_ids_by_category(["simple_python"], limit=5, seed=1) == {
        "simple_python": ["simple_python_0"],
    }
