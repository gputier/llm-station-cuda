import json
import shutil

from benchrun.report import build_mini_report

# A real mini out-root, one machine, left by a real launch (Bench-LLM/runs
# stays outside this repo and outside benchrun's own mount, so its absolute
# host path is fixed here rather than derived): used read-only, never
# written to, by test_build_mini_report_reads_a_real_result_tree below.
REAL_MINI_99 = "/Volumes/Git Ext./git_projects/Tools/Bench-LLM/runs/mini/99"


def _write_result(variant_dir, suite, item_id, passed):
    suite_dir = variant_dir / suite
    suite_dir.mkdir(parents=True, exist_ok=True)
    (suite_dir / "rep1.jsonl").write_text(json.dumps({"item_id": item_id, "passed": passed}) + "\n")


def _journal_line(run_id, suite, rep, tokens, total_s, decode_tps):
    return json.dumps({
        "run_id": run_id, "suite": suite, "rep": rep,
        "usage": {"completion_tokens": tokens}, "total_s": total_s,
        "timings": {"predicted_per_second": decode_tps},
    })


def test_build_mini_report_one_profile_one_test(tmp_path):
    out_root = tmp_path / "mini"
    variant_dir = out_root / "99" / "tiel" / "R1"
    _write_result(variant_dir, "lcb", "problem-3", 1)
    journal = tmp_path / "journal-99.jsonl"
    journal.write_text(_journal_line("99/tiel/R1", "lcb", 1, 512, 12.5, 30.0) + "\n")

    report = build_mini_report(out_root, "99", journal, seed=42, caps_by_suite={}, default_cap=2048, generated_at="2026-09-24T10:00:00")

    assert "Graine de tirage : 42" in report
    assert "Plafond de réponse : 2048" in report
    assert "99/tiel/R1" in report
    assert "problem-3" in report
    assert "réussi" in report
    assert "512" in report
    assert "1 réussites sur 1" in report


def test_build_mini_report_sums_multi_request_items(tmp_path):
    # A BFCL multi-turn or agentic item takes several requests: the report
    # sums tokens and duration across every journal line for that one
    # run_id/suite/rep instead of showing only the last request.
    out_root = tmp_path / "mini"
    variant_dir = out_root / "99" / "tiel" / "R1"
    _write_result(variant_dir, "bfcl", "multi_turn_base_4", 0)
    journal = tmp_path / "journal-99.jsonl"
    lines = [
        _journal_line("99/tiel/R1", "bfcl", 1, 300, 5.0, 20.0),
        _journal_line("99/tiel/R1", "bfcl", 1, 200, 4.0, 25.0),
    ]
    journal.write_text("\n".join(lines) + "\n")

    report = build_mini_report(out_root, "99", journal, seed=1, caps_by_suite={}, default_cap=2048, generated_at="2026-09-24T10:00:00")

    assert "500" in report  # 300 + 200 completion tokens, summed
    assert "échoué" in report


def test_build_mini_report_flags_truncation_at_the_cap(tmp_path):
    out_root = tmp_path / "mini"
    variant_dir = out_root / "99" / "tiel" / "R1"
    _write_result(variant_dir, "aider", "python/some-exercise", 1)
    journal = tmp_path / "journal-99.jsonl"
    journal.write_text(_journal_line("99/tiel/R1", "aider", 1, 2048, 60.0, 15.0) + "\n")

    report = build_mini_report(out_root, "99", journal, seed=1, caps_by_suite={}, default_cap=2048, generated_at="2026-09-24T10:00:00")

    rows = [l for l in report.splitlines() if l.startswith("| aider")]
    assert len(rows) == 1 and "| oui |" in rows[0]


def test_build_mini_report_uses_the_per_suite_cap_not_a_flat_one(tmp_path):
    # Guillaume, 2026-09-25: lcb/livebench/aider get 16384, everything else
    # keeps 2048. 2048 tokens on aider (its own real cap is 16384) must NOT
    # be flagged truncated; the same 2048 tokens on bfcl (default cap) must.
    out_root = tmp_path / "mini"
    variant_dir = out_root / "99" / "tiel" / "R1"
    _write_result(variant_dir, "aider", "python/some-exercise", 1)
    _write_result(variant_dir, "bfcl", "multiple_1", 1)
    journal = tmp_path / "journal-99.jsonl"
    journal.write_text(
        _journal_line("99/tiel/R1", "aider", 1, 2048, 10.0, 15.0) + "\n"
        + _journal_line("99/tiel/R1", "bfcl", 1, 2048, 1.0, 20.0) + "\n"
    )

    report = build_mini_report(
        out_root, "99", journal, seed=1,
        caps_by_suite={"lcb": 16384, "livebench": 16384, "aider": 16384},
        default_cap=2048, generated_at="2026-09-24T10:00:00",
    )

    aider_row = next(l for l in report.splitlines() if l.startswith("| aider"))
    bfcl_row = next(l for l in report.splitlines() if l.startswith("| bfcl"))
    assert "| non |" in aider_row  # 2048 tokens, own cap 16384: not truncated
    assert "| oui |" in bfcl_row  # 2048 tokens, default cap 2048: truncated


def test_build_mini_report_header_lists_the_per_suite_caps(tmp_path):
    out_root = tmp_path / "mini"
    journal = tmp_path / "journal-99.jsonl"

    report = build_mini_report(
        out_root, "99", journal, seed=1,
        caps_by_suite={"lcb": 16384, "livebench": 16384, "aider": 16384},
        default_cap=2048, generated_at="2026-09-24T10:00:00",
    )

    header = next(l for l in report.splitlines() if l.startswith("Généré le"))
    assert "2048 tokens par défaut" in header
    assert "16384" in header
    assert "aider" in header and "lcb" in header and "livebench" in header


def test_build_mini_report_untruncated_item_says_non(tmp_path):
    out_root = tmp_path / "mini"
    variant_dir = out_root / "99" / "tiel" / "R1"
    _write_result(variant_dir, "aider", "python/some-exercise", 1)
    journal = tmp_path / "journal-99.jsonl"
    journal.write_text(_journal_line("99/tiel/R1", "aider", 1, 40, 3.0, 15.0) + "\n")

    report = build_mini_report(out_root, "99", journal, seed=1, caps_by_suite={}, default_cap=2048, generated_at="2026-09-24T10:00:00")

    rows = [l for l in report.splitlines() if l.startswith("| aider")]
    assert len(rows) == 1 and "| non |" in rows[0]


def test_build_mini_report_two_variants_get_their_own_section(tmp_path):
    out_root = tmp_path / "mini"
    for variant in ("R1", "R2"):
        variant_dir = out_root / "99" / "tiel" / variant
        _write_result(variant_dir, "lcb", "problem-1", 1)
    journal = tmp_path / "journal-99.jsonl"
    journal.write_text(
        _journal_line("99/tiel/R1", "lcb", 1, 100, 2.0, 20.0) + "\n"
        + _journal_line("99/tiel/R2", "lcb", 1, 150, 3.0, 25.0) + "\n"
    )

    report = build_mini_report(out_root, "99", journal, seed=1, caps_by_suite={}, default_cap=2048, generated_at="2026-09-24T10:00:00")

    assert "## 99/tiel/R1" in report and "## 99/tiel/R2" in report


def _journal_line_anthropic(run_id, suite, rep, output_tokens, total_s):
    # /v1/messages (agentic's own path): usage.output_tokens, no "timings"
    # field at all (llama-server never reports one for this shape,
    # benchrun.gateway's own module docstring).
    return json.dumps({
        "run_id": run_id, "suite": suite, "rep": rep,
        "usage": {"output_tokens": output_tokens}, "total_s": total_s,
        "timings": None,
    })


def test_build_mini_report_reads_anthropic_shaped_usage_for_agentic(tmp_path):
    # Proven live, 2026-09-24: agentic showed 0 tokens because only
    # usage.completion_tokens (OpenAI shape) was ever read.
    out_root = tmp_path / "mini"
    variant_dir = out_root / "99" / "tiel" / "R1"
    _write_result(variant_dir, "agentic", "swe/some-task", 0)
    journal = tmp_path / "journal-99.jsonl"
    lines = [_journal_line_anthropic("99/tiel/R1", "agentic", 1, 2048, t) for t in (3.0, 4.0)]
    journal.write_text("\n".join(lines) + "\n")

    report = build_mini_report(out_root, "99", journal, seed=1, caps_by_suite={}, default_cap=2048, generated_at="2026-09-24T10:00:00")

    rows = [l for l in report.splitlines() if l.startswith("| agentic")]
    assert len(rows) == 1
    assert "| 4096 |" in rows[0]  # 2048 + 2048, summed across the two requests
    assert "| oui |" in rows[0]  # each request hit the 2048 cap: truncated


def test_build_mini_report_excludes_speed_from_the_pass_count(tmp_path):
    out_root = tmp_path / "mini"
    variant_dir = out_root / "99" / "tiel" / "R1"
    _write_result(variant_dir, "lcb", "problem-1", 1)
    _write_result(variant_dir, "speed", "pp512", 1)
    journal = tmp_path / "journal-99.jsonl"
    journal.write_text(_journal_line("99/tiel/R1", "lcb", 1, 100, 1.0, 20.0) + "\n")

    report = build_mini_report(out_root, "99", journal, seed=1, caps_by_suite={}, default_cap=2048, generated_at="2026-09-24T10:00:00")

    assert "1 réussites sur 1" in report  # speed does not add to the denominator
    speed_rows = [l for l in report.splitlines() if l.startswith("| speed")]
    assert len(speed_rows) == 1 and "| mesure |" in speed_rows[0]
    assert "| réussi |" not in speed_rows[0] and "| échoué |" not in speed_rows[0]


def test_build_mini_report_shows_a_failed_suite_as_erreur(tmp_path):
    out_root = tmp_path / "mini"
    variant_dir = out_root / "99" / "tiel" / "R1"
    (variant_dir / "bfcl").mkdir(parents=True)
    (variant_dir / "bfcl" / "rep1.error").write_text(
        "StatisticsError: stdev requires at least two data points\n\nTraceback...\n", encoding="utf-8"
    )
    journal = tmp_path / "journal-99.jsonl"
    journal.write_text("")

    report = build_mini_report(out_root, "99", journal, seed=1, caps_by_suite={}, default_cap=2048, generated_at="2026-09-24T10:00:00")

    rows = [l for l in report.splitlines() if l.startswith("| bfcl")]
    assert len(rows) == 1
    assert "| erreur (StatisticsError) |" in rows[0]
    assert "0 réussites sur 1" in report


def test_build_mini_report_empty_machine_says_so(tmp_path):
    out_root = tmp_path / "mini"
    journal = tmp_path / "journal-99.jsonl"
    report = build_mini_report(out_root, "99", journal, seed=1, caps_by_suite={}, default_cap=2048, generated_at="2026-09-24T10:00:00")
    assert "Aucun profil trouvé" in report


def test_build_mini_report_reads_a_real_result_tree(tmp_path):
    """Regression test for the second real mini launch (2026-09-24):
    build_mini_report used to look for out_root/<machine>/<machine>/... (one
    machine level too many, a leftover from the old pilot layout) and said
    "Aucun profil trouvé" over a tree that had real rep1.jsonl files all
    along. A real mini/99 output tree (Bench-LLM/runs, outside this repo, so
    never written to: copied into tmp_path first, the source stays read-
    only for the whole test) now proves the fix against actual layout,
    filenames and content, not a hand-built fixture that could encode the
    same wrong assumption as the bug it is meant to catch."""
    import os
    import stat
    if not os.path.isdir(REAL_MINI_99):
        import pytest
        pytest.skip(f"no real mini output tree at {REAL_MINI_99}")

    out_root = tmp_path / "mini"
    dest = out_root / "99"
    shutil.copytree(REAL_MINI_99, dest)
    # Read-only on the COPY only, to prove build_mini_report never writes
    # anything back; the source tree under Bench-LLM/runs is never touched
    # at all (copytree only ever reads from it).
    for path in [dest, *[p for p in dest.rglob("*")]]:
        current = os.stat(path).st_mode
        os.chmod(path, current & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)

    try:
        journal = tmp_path / "journal-99.jsonl"  # no real journal copied: aggregates read as zero, that is fine here
        journal.write_text("")
        report = build_mini_report(out_root, "99", journal, seed=1, caps_by_suite={}, default_cap=2048, generated_at="2026-09-24T10:00:00")
    finally:
        for path in [*[p for p in dest.rglob("*")], dest]:
            try:
                os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
            except FileNotFoundError:
                pass

    assert "Aucun profil trouvé" not in report
    assert "## 99/tiel/R1" in report
    # At least one real suite row must show up, read straight from the real
    # rep1.jsonl/rep1.error files on disk.
    assert any(line.startswith("| lcb") or line.startswith("| aider") for line in report.splitlines())
