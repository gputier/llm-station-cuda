"""Live campaign page: reads the gateway journals and the runner's cells.

Read-only over BENCH_PRIVATE/runs, never touches a station. Serves one page
that polls /state every few seconds.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

from aiohttp import web

TAIL_LINES = 400

PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Banc LLM en direct</title>
<style>
body{font:14px system-ui,sans-serif;margin:16px;background:#111;color:#ddd}
h2{margin:18px 0 6px}table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #333;padding:4px 8px;text-align:left}
.ok{color:#6c6}.ko{color:#e66}.dim{color:#888}
</style></head><body>
<h1>Banc LLM en direct</h1><div id="app" class="dim">chargement...</div>
<script>
function row(c){return "<td>"+c.join("</td><td>")+"</td>"}
async function tick(){
 const s=await (await fetch("state")).json(); let h="<h2>Classement</h2>";
 if(!s.ranking.length) h+="<p class=dim>aucune épreuve terminée</p>";
 else{h+="<p class=dim>moyenne simple des taux de réussite des épreuves terminées, vitesse exclue ; une configuration n'est comparable aux autres qu'une fois toutes ses épreuves finies</p><table><tr><th>rang</th><th>configuration</th><th>moyenne</th><th>épreuves</th></tr>";
  s.ranking.forEach((r,i)=>{h+="<tr>"+row([i+1,r.config,r.mean+" %",Object.entries(r.suites).map(([k,v])=>k+" "+v+" %").join(", ")])+"</tr>"});
  h+="</table>";}
 for(const m of s.machines){
  h+="<h2>Station "+m.machine+"</h2>";
  if(m.current){const c=m.current;
   h+="<p>En cours : <b>"+c.run_id+"</b>, épreuve <b>"+c.suite+"</b>, passe "+c.rep+
      ", dernière requête il y a "+c.age_s+" s, "+(c.decode_tps??"?")+" tokens/s, "+
      c.requests+" requêtes dans cette épreuve, durée moyenne "+c.mean_s+" s"+
      (c.errors?", <span class=ko>"+c.errors+" erreurs</span>":"")+"</p>";}
  else h+="<p class=dim>aucune requête</p>";
  h+="<table><tr><th>configuration</th><th>épreuve</th><th>passes finies</th><th>réussite</th><th>état</th></tr>";
  for(const r of m.cells) h+="<tr>"+row([r.config,r.suite,r.done,r.pass_rate,
     r.running?"en cours":r.skipped?"<span class=dim>sautée</span>":(r.error?"<span class=ko>erreur</span>":(r.spill?"<span class=ko>débordement VRAM</span>":"<span class=ok>ok</span>"))])+"</tr>";
  h+="</table>";}
 document.getElementById("app").innerHTML=h+"<p class=dim>mis à jour "+new Date().toLocaleTimeString()+"</p>";}
tick();setInterval(tick,5000);
</script></body></html>"""


def _tail(path: pathlib.Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _current(records: list[dict]) -> dict | None:
    if not records:
        return None
    last = records[-1]
    same = [r for r in records if (r.get("run_id"), r.get("suite"), r.get("rep")) ==
            (last.get("run_id"), last.get("suite"), last.get("rep"))]
    totals = [r["total_s"] for r in same if isinstance(r.get("total_s"), (int, float))]
    tps = (last.get("timings") or {}).get("predicted_per_second")
    return {
        "run_id": last.get("run_id"), "suite": last.get("suite"), "rep": last.get("rep"),
        "age_s": int(time.time() - last.get("t_start", time.time()) - (last.get("total_s") or 0)),
        "decode_tps": round(tps, 1) if tps else None,
        "requests": len(same),
        "mean_s": round(sum(totals) / len(totals), 1) if totals else None,
        "errors": sum(1 for r in same if r.get("error") or (r.get("status") or 200) >= 400),
    }


def _suite_score(suite_dir: pathlib.Path) -> tuple[int, int]:
    """Passed and graded items over the finished reps only (rep<N>.done)."""
    passed = total = 0
    for done in suite_dir.glob("rep*.done"):
        rep_file = done.with_suffix(".jsonl")
        if not rep_file.exists():
            continue
        for line in rep_file.read_text(encoding="utf-8").splitlines():
            try:
                passed += int(json.loads(line).get("passed") or 0)
                total += 1
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
    return passed, total


def _cells(machine_root: pathlib.Path) -> list[dict]:
    rows = []
    # A cell is <model>/<variant>; the runner nests it under the machine at a
    # depth that depends on --out, so it is found by its variant directory.
    for cell in sorted(p for p in machine_root.glob("**/R[0-9]*") if p.is_dir() and p.parent != machine_root):
        meta = cell / "meta.json"
        info = {}
        if meta.exists():
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        for suite_dir in sorted(p for p in cell.iterdir() if p.is_dir()):
            passed, total = _suite_score(suite_dir)
            # meta.json is written when a cell ends: no meta, or a suite
            # touched after it, means a run of this cell still going.
            newest = max(p.stat().st_mtime for p in [suite_dir, *suite_dir.rglob("*")])
            running = not meta.exists() or newest > meta.stat().st_mtime
            rows.append({
                "config": f"{cell.parent.name}/{cell.name}", "suite": suite_dir.name,
                "done": len(list(suite_dir.glob("rep*.done"))),
                "skipped": any(suite_dir.glob("rep*.skipped")),
                "passed": passed, "total": total,
                "pass_rate": f"{100 * passed / total:.1f} % sur {total}" if total else "-",
                "running": running,
                "error": bool(info.get("error")) and not running, "spill": bool(info.get("spill")),
            })
    return rows


# speed measures throughput, not a pass rate: it never enters the ranking.
UNRANKED_SUITES = {"speed"}


def _ranking(cells: list[dict]) -> list[dict]:
    """One row per configuration: the unweighted mean of its finished suites'
    pass rates, and how many suites that mean covers so far."""
    by_config: dict[str, dict[str, float]] = {}
    for row in cells:
        if row["suite"] in UNRANKED_SUITES or not row["total"]:
            continue
        by_config.setdefault(row["config"], {})[row["suite"]] = 100 * row["passed"] / row["total"]
    ranked = [
        {"config": config, "mean": round(sum(scores.values()) / len(scores), 1),
         "suites": {name: round(rate, 1) for name, rate in sorted(scores.items())}}
        for config, scores in by_config.items()
    ]
    return sorted(ranked, key=lambda r: r["mean"], reverse=True)


def make_app(runs: pathlib.Path, campaign: str) -> web.Application:
    async def page(_request):
        return web.Response(text=PAGE, content_type="text/html")

    async def state(_request):
        machines = []
        all_cells = []
        for machine in ("99", "97"):
            cells = _cells(runs / campaign / machine)
            all_cells += [{**row, "config": f"{machine}/{row['config']}"} for row in cells]
            machines.append({
                "machine": machine,
                "current": _current(_tail(runs / f"journal-{machine}.jsonl", TAIL_LINES)),
                "cells": cells,
            })
        return web.json_response({"machines": machines, "ranking": _ranking(all_cells)})

    app = web.Application()
    app.router.add_get("/", page)
    app.router.add_get("/state", state)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", required=True, type=pathlib.Path)
    parser.add_argument("--campaign", default="pilot")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    web.run_app(make_app(args.runs, args.campaign), port=args.port)


if __name__ == "__main__":
    main()
