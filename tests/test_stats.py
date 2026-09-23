from benchrun.stats import item_scores, bootstrap_ci, paired_diff_ci, mcnemar_exact, min_detectable_gap


def test_item_scores_average_over_reps():
    rs = [{"item_id": "a", "passed": 1}, {"item_id": "a", "passed": 0}, {"item_id": "a", "passed": 1},
          {"item_id": "b", "passed": 0}]
    assert item_scores(rs) == {"a": 2 / 3, "b": 0.0}


def test_bootstrap_ci_contains_mean_and_is_ordered():
    scores = {str(i): (1.0 if i % 2 else 0.0) for i in range(200)}
    m, lo, hi = bootstrap_ci(scores, b=2000)
    assert lo < m < hi and abs(m - 0.5) < 1e-9 and hi - lo < 0.2


def test_bootstrap_ci_is_reproducible():
    scores = {str(i): float(i % 3 == 0) for i in range(100)}
    assert bootstrap_ci(scores, b=500, seed=1) == bootstrap_ci(scores, b=500, seed=1)


def test_paired_diff_uses_common_items_only():
    a = {"x": 1.0, "y": 1.0, "z": 0.0}
    b = {"x": 0.0, "y": 1.0}
    m, lo, hi = paired_diff_ci(a, b, b_iter=500)
    assert abs(m - 0.5) < 1e-9


def test_mcnemar_identical_is_one():
    a = {str(i): 1.0 for i in range(30)}
    assert mcnemar_exact(a, dict(a)) == 1.0


def test_mcnemar_strong_difference_is_small():
    a = {str(i): 1.0 for i in range(30)}
    b = {str(i): 0.0 for i in range(30)}
    assert mcnemar_exact(a, b) < 1e-6


def test_min_detectable_gap_shrinks_with_n():
    assert min_detectable_gap(40) > min_detectable_gap(400) > 0
