import json

from benchrun.report import build_mini_report


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
    machine_root = out_root / "99" / "99" / "tiel" / "R1"
    _write_result(machine_root, "lcb", "problem-3", 1)
    journal = tmp_path / "journal-99.jsonl"
    journal.write_text(_journal_line("99/tiel/R1", "lcb", 1, 512, 12.5, 30.0) + "\n")

    report = build_mini_report(out_root, "99", journal, seed=42, cap=2048, generated_at="2026-09-24T10:00:00")

    assert "Graine de tirage : 42" in report
    assert "Plafond de reponse : 2048" in report
    assert "99/tiel/R1" in report
    assert "problem-3" in report
    assert "reussi" in report
    assert "512" in report
    assert "1 reussites sur 1" in report


def test_build_mini_report_sums_multi_request_items(tmp_path):
    # A BFCL multi-turn or agentic item takes several requests: the report
    # sums tokens and duration across every journal line for that one
    # run_id/suite/rep instead of showing only the last request.
    out_root = tmp_path / "mini"
    machine_root = out_root / "99" / "99" / "tiel" / "R1"
    _write_result(machine_root, "bfcl", "multi_turn_base_4", 0)
    journal = tmp_path / "journal-99.jsonl"
    lines = [
        _journal_line("99/tiel/R1", "bfcl", 1, 300, 5.0, 20.0),
        _journal_line("99/tiel/R1", "bfcl", 1, 200, 4.0, 25.0),
    ]
    journal.write_text("\n".join(lines) + "\n")

    report = build_mini_report(out_root, "99", journal, seed=1, cap=2048, generated_at="2026-09-24T10:00:00")

    assert "500" in report  # 300 + 200 completion tokens, summed
    assert "echoue" in report


def test_build_mini_report_flags_truncation_at_the_cap(tmp_path):
    out_root = tmp_path / "mini"
    machine_root = out_root / "99" / "99" / "tiel" / "R1"
    _write_result(machine_root, "aider", "python/some-exercise", 1)
    journal = tmp_path / "journal-99.jsonl"
    journal.write_text(_journal_line("99/tiel/R1", "aider", 1, 2048, 60.0, 15.0) + "\n")

    report = build_mini_report(out_root, "99", journal, seed=1, cap=2048, generated_at="2026-09-24T10:00:00")

    rows = [l for l in report.splitlines() if l.startswith("| aider")]
    assert len(rows) == 1 and "| oui |" in rows[0]


def test_build_mini_report_untruncated_item_says_non(tmp_path):
    out_root = tmp_path / "mini"
    machine_root = out_root / "99" / "99" / "tiel" / "R1"
    _write_result(machine_root, "aider", "python/some-exercise", 1)
    journal = tmp_path / "journal-99.jsonl"
    journal.write_text(_journal_line("99/tiel/R1", "aider", 1, 40, 3.0, 15.0) + "\n")

    report = build_mini_report(out_root, "99", journal, seed=1, cap=2048, generated_at="2026-09-24T10:00:00")

    rows = [l for l in report.splitlines() if l.startswith("| aider")]
    assert len(rows) == 1 and "| non |" in rows[0]


def test_build_mini_report_two_variants_get_their_own_section(tmp_path):
    out_root = tmp_path / "mini"
    for variant in ("R1", "R2"):
        variant_dir = out_root / "99" / "99" / "tiel" / variant
        _write_result(variant_dir, "lcb", "problem-1", 1)
    journal = tmp_path / "journal-99.jsonl"
    journal.write_text(
        _journal_line("99/tiel/R1", "lcb", 1, 100, 2.0, 20.0) + "\n"
        + _journal_line("99/tiel/R2", "lcb", 1, 150, 3.0, 25.0) + "\n"
    )

    report = build_mini_report(out_root, "99", journal, seed=1, cap=2048, generated_at="2026-09-24T10:00:00")

    assert "## 99/tiel/R1" in report and "## 99/tiel/R2" in report


def test_build_mini_report_shows_a_failed_suite_as_erreur(tmp_path):
    out_root = tmp_path / "mini"
    variant_dir = out_root / "99" / "99" / "tiel" / "R1"
    (variant_dir / "bfcl").mkdir(parents=True)
    (variant_dir / "bfcl" / "rep1.error").write_text(
        "StatisticsError: stdev requires at least two data points\n\nTraceback...\n", encoding="utf-8"
    )
    journal = tmp_path / "journal-99.jsonl"
    journal.write_text("")

    report = build_mini_report(out_root, "99", journal, seed=1, cap=2048, generated_at="2026-09-24T10:00:00")

    rows = [l for l in report.splitlines() if l.startswith("| bfcl")]
    assert len(rows) == 1
    assert "| erreur (StatisticsError) |" in rows[0]
    assert "0 reussites sur 1" in report


def test_build_mini_report_empty_machine_says_so(tmp_path):
    out_root = tmp_path / "mini"
    journal = tmp_path / "journal-99.jsonl"
    report = build_mini_report(out_root, "99", journal, seed=1, cap=2048, generated_at="2026-09-24T10:00:00")
    assert "Aucun profil trouve" in report
