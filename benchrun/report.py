"""Human-readable report for the mini bench level (Guillaume, 2026-09-24):
what mini actually measured, one profile at a time, built once at the end of
a mini launch from the results each suite already wrote to disk and the
gateway's own journal (run_id/suite/rep, read the same way gateway.py writes
them and watch.py already reads them back). No model call of its own.
"""
from __future__ import annotations

import json
import pathlib

# One short phrase per suite: what it actually grades, shown verbatim in the
# report's per-test rows (bfcl and ruler append the category/task name).
SUITE_DESCRIPTIONS = {
    "lcb": "resout un probleme de programmation, code juge par execution de tests",
    "aider": "edite un fichier existant pour satisfaire un exercice, juge par sa suite de tests",
    "bfcl": "appelle la bonne fonction avec les bons arguments",
    "livebench": "repond a une question, comparee a une reponse de reference",
    "ruler": "retrouve une information inseree dans un contexte long",
    "longbench_v2": "choisit la bonne reponse a choix multiple sur un contexte long",
    "agentic": "realise une tache de developpement avec Claude Code, jugee par les tests du depot",
    "speed": "mesure le debit de decodage, ce n'est pas une reussite",
}


def _suite_description(suite: str, item_id: str) -> str:
    base = SUITE_DESCRIPTIONS.get(suite, suite)
    if suite == "bfcl" and "_" in item_id:
        return f"{base} (categorie {item_id.rsplit('_', 1)[0]})"
    if suite == "ruler" and "@" in item_id:
        return f"{base} (tache {item_id.split('@', 1)[0].split('#', 1)[0]})"
    return base


def _journal_rows(journal_lines: list[dict], run_id: str, suite: str, rep: int) -> list[dict]:
    return [
        rec for rec in journal_lines
        if rec.get("run_id") == run_id and rec.get("suite") == suite and rec.get("rep") == rep
    ]


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


def _aggregate(rows: list[dict], cap: int) -> dict:
    """Sums tokens and duration across every journal line that matches one
    run_id/suite/rep: with the mini preset's one item per test, that is
    every request the item took, one for a single-turn test, several for a
    multi-turn one (BFCL's multi_turn_base category, the agentic suite)."""
    completion_tokens = 0
    duration_s = 0.0
    decode_tps_values = []
    truncated = False
    for rec in rows:
        usage = rec.get("usage") or {}
        tokens = usage.get("completion_tokens")
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


def _profile_rows(variant_dir: pathlib.Path, run_id: str, journal_lines: list[dict], cap: int) -> tuple[list[dict], dict]:
    rows = []
    passed_count = 0
    total_count = 0
    token_values = []
    truncated_count = 0
    duration_total = 0.0
    for suite_dir in sorted(p for p in variant_dir.iterdir() if p.is_dir()):
        suite = suite_dir.name
        rep_file = suite_dir / "rep1.jsonl"
        if not rep_file.exists():
            continue
        for line in rep_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            item_id = item.get("item_id") or "-"
            passed = bool(item.get("passed"))
            agg = _aggregate(_journal_rows(journal_lines, run_id, suite, 1), cap)
            total_count += 1
            passed_count += 1 if passed else 0
            token_values.append(agg["completion_tokens"])
            duration_total += agg["duration_s"]
            truncated_count += 1 if agg["truncated"] else 0
            rows.append({
                "suite": suite,
                "description": _suite_description(suite, item_id),
                "item_id": item_id,
                "passed": passed,
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
        result="reussi" if row["passed"] else "echoue",
        tokens=row["completion_tokens"], cut="oui" if row["truncated"] else "non",
        duration=row["duration_s"], tps=tps,
    )


def build_mini_report(out_root: pathlib.Path, machine: str, journal_path: pathlib.Path,
                       seed: int, cap: int, generated_at: str) -> str:
    """out_root is DIR/mini (the mini preset's own --out), one level above
    the machine directory: mirrors Campaign._cell's own machine/model/variant
    nesting, so the result tree on disk already says which profiles this
    launch touched, nothing extra to track."""
    machine_root = out_root / machine / machine
    journal_lines = _read_journal(journal_path)
    lines = [
        f"# Rapport Mini, machine {machine}",
        "",
        f"Genere le {generated_at}. Graine de tirage : {seed}. Plafond de reponse : {cap} tokens.",
        "",
        "Ces notes mesurent le modele sous une contrainte de reponse a 2048 tokens, pas ses "
        "capacites completes : elles ne se comparent pas a une campagne Full. Avec une seule "
        "question par epreuve, chaque note vaut 0 % ou 100 %, jamais une valeur intermediaire.",
        "",
    ]
    if not machine_root.exists() or not any(machine_root.iterdir()):
        lines.append("Aucun profil trouve pour cette machine.")
        return "\n".join(lines) + "\n"
    for model_dir in sorted(p for p in machine_root.iterdir() if p.is_dir()):
        for variant_dir in sorted(p for p in model_dir.iterdir() if p.is_dir()):
            run_id = f"{machine}/{model_dir.name}/{variant_dir.name}"
            rows, summary = _profile_rows(variant_dir, run_id, journal_lines, cap)
            lines.append(f"## {run_id}")
            lines.append("")
            lines.append("| epreuve | juge | question | resultat | tokens | coupee | duree (s) | decode (tok/s) |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
            lines.extend(_format_row(row) for row in rows)
            lines.append("")
            lines.append(
                "Synthese : {passed} reussites sur {total}, {tokens:.0f} tokens en moyenne, "
                "{truncated} reponses coupees, duree totale {duration:.1f} s.".format(
                    passed=summary["passed"], total=summary["total"], tokens=summary["mean_tokens"],
                    truncated=summary["truncated"], duration=summary["duration_s"],
                )
            )
            lines.append("")
    return "\n".join(lines) + "\n"
