"""Human-readable report for the mini bench level (Guillaume, 2026-09-24):
what mini actually measured, one profile at a time, built once at the end of
a mini launch from the results each suite already wrote to disk and the
gateway's own journal (run_id/suite/rep, read the same way gateway.py writes
them and watch.py already reads them back). No model call of its own.

A suite that raised (Campaign.run's per-suite isolation, runner.py: no
rep1.jsonl, a rep1.error instead) shows up as its own row, result "erreur
(<ExceptionType>)", read from rep1.error's own first line: it still counts
toward the profile's total, never toward its passes.

Text is French, for Guillaume, written with its own accents (proper UTF-8,
never the ASCII-stripped placeholders an early draft of this file shipped
with, 2026-09-24 second real mini run).
"""
from __future__ import annotations

import json
import pathlib

# One short phrase per suite: what it actually grades, shown verbatim in the
# report's per-test rows (bfcl and ruler append the category/task name).
SUITE_DESCRIPTIONS = {
    "lcb": "résout un problème de programmation, code jugé par exécution de tests",
    "aider": "édite un fichier existant pour satisfaire un exercice, jugé par sa suite de tests",
    "bfcl": "appelle la bonne fonction avec les bons arguments",
    "livebench": "répond à une question, comparée à une réponse de référence",
    "ruler": "retrouve une information insérée dans un contexte long",
    "longbench_v2": "choisit la bonne réponse à choix multiple sur un contexte long",
    "agentic": "réalise une tâche de développement avec Claude Code, jugée par les tests du dépôt",
    "speed": "mesure le débit de décodage, ce n'est pas une réussite",
}

# speed measures throughput, not a pass/fail test: excluded from the
# "réussites sur N" summary (N counts the 7 graded suites, not all 8), same
# convention benchrun.watch's own ranking already applies to it.
UNRANKED_SUITES = {"speed"}


def _suite_description(suite: str, item_id: str) -> str:
    base = SUITE_DESCRIPTIONS.get(suite, suite)
    if suite == "bfcl" and "_" in item_id:
        return f"{base} (catégorie {item_id.rsplit('_', 1)[0]})"
    if suite == "ruler" and "@" in item_id:
        return f"{base} (tâche {item_id.split('@', 1)[0].split('#', 1)[0]})"
    return base


def _journal_rows(journal_lines: list[dict], run_id: str, suite: str, rep: int) -> list[dict]:
    """The journal rows of the last pass of one rep. The journal is appended
    to by every launch, so a rep replayed after a failure has the failed
    attempt's rows too; only the latest pass_id is the one its result file
    describes. Rows written before pass_id existed form a single group."""
    rows = [
        rec for rec in journal_lines
        if rec.get("run_id") == run_id and rec.get("suite") == suite and rec.get("rep") == rep
    ]
    if not rows:
        return rows
    last_pass = max(rows, key=lambda rec: rec.get("t_start") or 0).get("pass_id")
    return [rec for rec in rows if rec.get("pass_id") == last_pass]


def _read_journal(journal_path: pathlib.Path) -> list[dict]:
    if not journal_path.exists():
        return []
    rows = []
    for line in journal_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _cap_for(suite: str, caps_by_suite: dict, default_cap: int) -> int:
    return caps_by_suite.get(suite, default_cap)


def _aggregate(rows: list[dict], cap: int) -> dict:
    """Sums tokens and duration across every journal line that matches one
    run_id/suite/rep: with the mini preset's one item per test, that is
    every request the item took, one for a single-turn test, several for a
    multi-turn one (BFCL's multi_turn_base category, the agentic suite).

    Token count: gateway.py journals the response body's own "usage" object
    verbatim, whatever shape the relayed path sent (rec["path"] says which).
    /v1/chat/completions and /v1/completions (OpenAI shape) put it in
    usage.completion_tokens; /v1/messages (Anthropic shape, the agentic
    suite's own path) puts it in usage.output_tokens instead (proven live,
    2026-09-24: agentic's own rows showed 0 tokens before this, since only
    completion_tokens was ever read). Truncation: the gateway journal does
    not carry finish_reason/stop_reason at all (relay_post only ever stores
    timings/usage/reasoning_chars), so both shapes are flagged the same way
    already in place for OpenAI's own finish_reason == "length": the token
    count landing on the configured cap. That is exactly what Anthropic's
    own stop_reason == "max_tokens" would mean too (output_tokens hits
    max_tokens precisely when the response was cut there), so no per-format
    branch is needed beyond reading the right token field.
    """
    completion_tokens = 0
    duration_s = 0.0
    decode_tps_values = []
    truncated = False
    for rec in rows:
        usage = rec.get("usage") or {}
        tokens = usage.get("completion_tokens")
        if tokens is None:
            tokens = usage.get("output_tokens")
        if tokens is None:
            tokens = (rec.get("timings") or {}).get("predicted_n")
        if tokens:
            completion_tokens += tokens
            if tokens >= cap:
                truncated = True
        if isinstance(rec.get("total_s"), (int, float)):
            duration_s += rec["total_s"]
        tps = (rec.get("timings") or {}).get("predicted_per_second")
        if tps:
            decode_tps_values.append(tps)
    decode_tps = sum(decode_tps_values) / len(decode_tps_values) if decode_tps_values else None
    return {
        "completion_tokens": completion_tokens,
        "duration_s": duration_s,
        "decode_tps": decode_tps,
        "truncated": truncated,
    }


def _error_type(error_file: pathlib.Path) -> str:
    """rep<N>.error's own first line (Campaign.run's per-suite handler):
    "<ExceptionType>: <message>", the exception type alone is what the
    report shows next to "erreur"."""
    first_line = error_file.read_text(encoding="utf-8").splitlines()[0]
    return first_line.split(":", 1)[0].strip()


def _profile_rows(variant_dir: pathlib.Path, run_id: str, journal_lines: list[dict],
                   caps_by_suite: dict, default_cap: int) -> tuple[list[dict], dict]:
    rows = []
    passed_count = 0
    total_count = 0
    token_values = []
    truncated_count = 0
    duration_total = 0.0
    for suite_dir in sorted(p for p in variant_dir.iterdir() if p.is_dir()):
        suite = suite_dir.name
        rep_file = suite_dir / "rep1.jsonl"
        error_file = suite_dir / "rep1.error"
        if not rep_file.exists():
            if error_file.exists():
                # A failed speed run still gets its row, it only stays out of
                # the "réussites sur N" count, as in the success branch below.
                if suite not in UNRANKED_SUITES:
                    total_count += 1
                rows.append({
                    "suite": suite,
                    "description": _suite_description(suite, ""),
                    "item_id": "-",
                    "result": f"erreur ({_error_type(error_file)})",
                    "completion_tokens": 0,
                    "truncated": False,
                    "duration_s": 0.0,
                    "decode_tps": None,
                })
            continue
        for line in rep_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            item_id = item.get("item_id") or "-"
            passed = bool(item.get("passed"))
            cap = _cap_for(suite, caps_by_suite, default_cap)
            agg = _aggregate(_journal_rows(journal_lines, run_id, suite, 1), cap)
            # speed measures throughput, not a pass/fail test (same
            # exclusion watch.py's own ranking already makes, UNRANKED_
            # SUITES): it gets its own row but never counts toward the
            # profile's "réussites sur N" summary.
            if suite not in UNRANKED_SUITES:
                total_count += 1
                passed_count += 1 if passed else 0
                token_values.append(agg["completion_tokens"])
                duration_total += agg["duration_s"]
                truncated_count += 1 if agg["truncated"] else 0
            rows.append({
                "suite": suite,
                "description": _suite_description(suite, item_id),
                "item_id": item_id,
                "result": "mesure" if suite in UNRANKED_SUITES else ("réussi" if passed else "échoué"),
                "completion_tokens": agg["completion_tokens"],
                "truncated": agg["truncated"],
                "duration_s": agg["duration_s"],
                "decode_tps": agg["decode_tps"],
            })
    summary = {
        "passed": passed_count,
        "total": total_count,
        "mean_tokens": (sum(token_values) / len(token_values)) if token_values else 0.0,
        "truncated": truncated_count,
        "duration_s": duration_total,
    }
    return rows, summary


def _format_row(row: dict) -> str:
    tps = f"{row['decode_tps']:.1f}" if row["decode_tps"] else "-"
    return "| {suite} | {description} | {item_id} | {result} | {tokens} | {cut} | {duration:.1f} | {tps} |".format(
        suite=row["suite"], description=row["description"], item_id=row["item_id"],
        result=row["result"],
        tokens=row["completion_tokens"], cut="oui" if row["truncated"] else "non",
        duration=row["duration_s"], tps=tps,
    )


def _caps_header_phrase(caps_by_suite: dict, default_cap: int) -> str:
    """One phrase listing every per-suite response cap, default first: e.g.
    "2048 tokens par défaut (16384 pour aider, lcb, livebench)." Built from
    the same table mini_suites uses to wrap each suite, so the header can
    never drift from what actually ran (Guillaume, 2026-09-25: LiveCodeBench,
    LiveBench and Aider need more room than a flat 2048 cap gives, measured
    live: almost every LCB answer hit 2048 and scored 0 %)."""
    if not caps_by_suite:
        return f"{default_cap} tokens."
    overrides = ", ".join(f"{suite}" for suite in sorted(caps_by_suite))
    override_values = sorted(set(caps_by_suite.values()))
    values_phrase = " ou ".join(str(v) for v in override_values)
    return f"{default_cap} tokens par défaut ({values_phrase} pour {overrides})."


def build_mini_report(out_root: pathlib.Path, machine: str, journal_path: pathlib.Path,
                       seed: int, caps_by_suite: dict, default_cap: int, generated_at: str) -> str:
    """out_root is DIR/mini (the mini preset's own --out): Campaign._cell
    nests exactly machine/model/variant under it (run_bench passes
    out_root=DIR/mini straight to Campaign, which appends cfg.machine
    itself, ONCE, not DIR/mini/<machine> again), so the profile tree to
    read is out_root/machine/model/variant, not a doubled machine level (a
    real second mini run on 2026-09-24 proved this: rapport.md said "Aucun
    profil trouvé" over an existing out_root/97/tiel/R1/.../rep1.jsonl tree,
    because this function used to look one level too deep).

    caps_by_suite/default_cap mirror benchrun.__main__.MINI_MAX_TOKENS_BY_
    SUITE/MINI_MAX_TOKENS_DEFAULT exactly (passed in by run_bench, not
    imported here, so this module stays free of a benchrun.__main__ import):
    each suite's own truncation check uses its own cap, not one flat number
    for every suite (Guillaume, 2026-09-25)."""
    machine_root = out_root / machine
    journal_lines = _read_journal(journal_path)
    lines = [
        f"# Rapport Mini, machine {machine}",
        "",
        f"Généré le {generated_at}. Graine de tirage : {seed}. "
        f"Plafond de réponse : {_caps_header_phrase(caps_by_suite, default_cap)}",
        "",
        "Ces notes mesurent le modèle sous contrainte de plafond de réponse, pas ses "
        "capacités complètes : elles ne se comparent pas à une campagne Full. Avec une seule "
        "question par épreuve, chaque note vaut 0 % ou 100 %, jamais une valeur intermédiaire.",
        "",
    ]
    if not machine_root.exists() or not any(machine_root.iterdir()):
        lines.append("Aucun profil trouvé pour cette machine.")
        return "\n".join(lines) + "\n"
    for model_dir in sorted(p for p in machine_root.iterdir() if p.is_dir()):
        for variant_dir in sorted(p for p in model_dir.iterdir() if p.is_dir()):
            run_id = f"{machine}/{model_dir.name}/{variant_dir.name}"
            rows, summary = _profile_rows(variant_dir, run_id, journal_lines, caps_by_suite, default_cap)
            lines.append(f"## {run_id}")
            lines.append("")
            lines.append("| épreuve | juge | question | résultat | tokens | coupée | durée (s) | décodage (tok/s) |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
            lines.extend(_format_row(row) for row in rows)
            lines.append("")
            lines.append(
                "Synthèse : {passed} réussites sur {total}, {tokens:.0f} tokens en moyenne, "
                "{truncated} réponses coupées, durée totale {duration:.1f} s.".format(
                    passed=summary["passed"], total=summary["total"], tokens=summary["mean_tokens"],
                    truncated=summary["truncated"], duration=summary["duration_s"],
                )
            )
            lines.append("")
    return "\n".join(lines) + "\n"
