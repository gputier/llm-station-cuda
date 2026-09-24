"""Direct unit tests for bench/harness/longbench_run.py's pure selection
and truncation logic, imported by file path (importlib) since the harness
script is not part of the benchrun package and its module-level code has no
heavy imports (tiktoken/openai are imported inside main(), never at module
scope): a fake tokenizer is enough to exercise _select_items and
_truncate_to_budget on the host, without a container.

These tests were written for the adversarial review's round 4 findings
(2026-09-24): silent truncation after a context-only selection check, and
overlapping buckets. Both proven RED against the pre-fix logic conceptually
(the old functions are gone, replaced; RED is instead demonstrated by
asserting the exact behaviors the review measured as broken: a full prompt
larger than its context alone, and two adjacent bands sharing no item).
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from benchrun.suites.longbench import MAX_ATTEMPTS as SUITE_MAX_ATTEMPTS

_SPEC = importlib.util.spec_from_file_location(
    "longbench_run", pathlib.Path(__file__).parents[1] / "harness" / "longbench_run.py",
)
longbench_run = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(longbench_run)


def test_max_attempts_agree_with_the_suite_adapter_and_allow_no_retry():
    """longbench_run.py sizes its own single-attempt request loop;
    benchrun.suites.longbench sizes subprocess_timeout_s from the same
    number. The two files cannot import each other (this script runs
    container-side, without benchrun on its path), so this test is what
    keeps them from drifting apart. MAX_ATTEMPTS == 1 is itself the
    contract: no retry on a timeout or a disconnection (bench/harness/README.md)."""
    assert longbench_run.MAX_ATTEMPTS == SUITE_MAX_ATTEMPTS == 1


def test_give_up_reason_fires_before_any_success():
    """A failure before this run has ever recorded a real success means the
    endpoint itself looks unreachable: give up rather than spend the whole
    sample budget on empty answers silently scored as real zeros."""
    assert longbench_run._give_up_reason(ever_succeeded=False, consecutive_failures=1) is not None


def test_give_up_reason_tolerates_an_isolated_failure_after_a_success():
    reason = longbench_run._give_up_reason(ever_succeeded=True, consecutive_failures=1)
    assert reason is None
    reason = longbench_run._give_up_reason(
        ever_succeeded=True, consecutive_failures=longbench_run.MAX_CONSECUTIVE_FAILURES - 1)
    assert reason is None


def test_give_up_reason_fires_at_max_consecutive_failures():
    reason = longbench_run._give_up_reason(
        ever_succeeded=True, consecutive_failures=longbench_run.MAX_CONSECUTIVE_FAILURES)
    assert reason is not None


class FakeEncoding:
    """One fake token per whitespace-separated word, deterministic and
    good enough to test selection/truncation arithmetic without tiktoken."""

    def encode(self, text, disallowed_special=()):
        return text.split()

    def decode(self, ids):
        return " ".join(ids)


TEMPLATE = "HEADER $DOC$ QUESTION $Q$ A)$C_A$ B)$C_B$ C)$C_C$ D)$C_D$ FOOTER"


def _item(item_id, context_words, question="q", choices=("a", "b", "c", "d")):
    return {
        "_id": item_id,
        "context": " ".join(f"w{i}" for i in range(context_words)),
        "question": question,
        "choice_A": choices[0], "choice_B": choices[1], "choice_C": choices[2], "choice_D": choices[3],
        "answer": "A",
        "domain": "d", "sub_domain": "s", "difficulty": "hard", "length": "short",
    }


def test_select_items_uses_full_prompt_not_context_alone():
    # HEADER/QUESTION/labels/FOOTER add fixed words on top of the context:
    # an item whose context alone fits the budget but whose full prompt
    # does not must be rejected, not silently truncated later (the real
    # bug: 2 of 20 items cut with no trace, context-only check passed them
    # through).
    item = _item("only-context-fits", context_words=95)
    encoding = FakeEncoding()
    context_tokens = len(encoding.encode(item["context"]))
    full_prompt_tokens = len(encoding.encode(longbench_run._build_prompt(TEMPLATE, item)))
    assert full_prompt_tokens > context_tokens  # the template body is real overhead

    # window == context_tokens: a context-only check would pass this item
    # (it exactly fits), but the full prompt (context + template overhead)
    # does not.
    selected, skipped = longbench_run._select_items(
        [item], TEMPLATE, band_floor=0, band_ceiling=1000, ratio=1.0, window=context_tokens,
        max_tokens=0, num_samples=10, encoding=encoding,
    )
    assert selected == []
    assert skipped == ["only-context-fits"]


def test_select_items_accepts_when_full_prompt_fits():
    item = _item("fits", context_words=50)
    encoding = FakeEncoding()
    selected, skipped = longbench_run._select_items(
        [item], TEMPLATE, band_floor=0, band_ceiling=1000, ratio=1.0, window=100,
        max_tokens=0, num_samples=10, encoding=encoding,
    )
    assert [i["_id"] for i in selected] == ["fits"]
    assert skipped == []


def test_bands_are_disjoint_no_shared_item_across_two_invocations():
    # Real bug: the 131072 bucket reused 70 percent of the 32768 bucket's
    # items. Simulate the two suite invocations (band (0, 100] then
    # (100, 300]) over the same dataset and assert no _id is selected by
    # both, however large num_samples is.
    encoding = FakeEncoding()
    data = [_item(f"item-{i}", context_words=w) for i, w in enumerate([20, 60, 89, 90, 150, 200, 280])]
    selected_a, _ = longbench_run._select_items(
        data, TEMPLATE, band_floor=0, band_ceiling=100, ratio=1.0, window=10_000,
        max_tokens=0, num_samples=100, encoding=encoding,
    )
    selected_b, _ = longbench_run._select_items(
        data, TEMPLATE, band_floor=100, band_ceiling=300, ratio=1.0, window=10_000,
        max_tokens=0, num_samples=100, encoding=encoding,
    )
    ids_a = {i["_id"] for i in selected_a}
    ids_b = {i["_id"] for i in selected_b}
    assert ids_a and ids_b  # both bands actually drew items, not a vacuous pass
    assert ids_a.isdisjoint(ids_b)
    # every item in the dataset must appear (its full prompt fits somewhere
    # in [0, 300]) in exactly one of the two bands
    assert ids_a | ids_b == {item["_id"] for item in data}


def test_selection_is_seeded_deterministic_across_calls():
    encoding = FakeEncoding()
    data = [_item(f"item-{i}", context_words=10) for i in range(20)]
    first, _ = longbench_run._select_items(
        data, TEMPLATE, band_floor=0, band_ceiling=1000, ratio=1.0, window=10_000,
        max_tokens=0, num_samples=5, encoding=encoding,
    )
    second, _ = longbench_run._select_items(
        data, TEMPLATE, band_floor=0, band_ceiling=1000, ratio=1.0, window=10_000,
        max_tokens=0, num_samples=5, encoding=encoding,
    )
    assert [i["_id"] for i in first] == [i["_id"] for i in second]
    assert len(first) == 5


def test_selection_seed_does_not_always_pick_the_alphabetically_first_items():
    # A regression guard against silently reverting to "first N sorted by
    # _id": with a seed, at least one run out of a reasonably sized pool
    # should NOT be exactly the first num_samples ids.
    encoding = FakeEncoding()
    data = [_item(f"item-{i:03d}", context_words=10) for i in range(50)]
    selected, _ = longbench_run._select_items(
        data, TEMPLATE, band_floor=0, band_ceiling=1000, ratio=1.0, window=10_000,
        max_tokens=0, num_samples=5, encoding=encoding,
    )
    first_five_sorted = [f"item-{i:03d}" for i in range(5)]
    assert [i["_id"] for i in selected] != first_five_sorted


def test_truncate_to_budget_reports_when_it_fires():
    encoding = FakeEncoding()
    text, truncated, cut = longbench_run._truncate_to_budget("a b c d e f g h", encoding, budget_tokens=4)
    assert truncated is True
    assert cut == 4
    assert text == "a b g h"


def test_truncate_to_budget_reports_no_op_when_it_fits():
    encoding = FakeEncoding()
    text, truncated, cut = longbench_run._truncate_to_budget("a b c", encoding, budget_tokens=10)
    assert truncated is False
    assert cut == 0
    assert text == "a b c"


def test_no_eligible_item_in_this_band_is_reported_not_silent(capsys, monkeypatch):
    encoding = FakeEncoding()
    data = [_item("too-big", context_words=1000)]
    selected, skipped = longbench_run._select_items(
        data, TEMPLATE, band_floor=0, band_ceiling=2000, ratio=1.0, window=10, max_tokens=0,
        num_samples=5, encoding=encoding,
    )
    assert selected == []
    assert skipped == ["too-big"]
