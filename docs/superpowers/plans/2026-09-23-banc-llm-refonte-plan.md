# Refonte du banc LLM, plan d'exécution

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repasser chaque LLM de la 97 et de la 99 au banc, avec trois configurations par modèle, sur un banc reconstruit (épreuves publiques + épreuve maison agentique), puis publier un tableau comparatif, une convention d'entrée et une doc à jour.

**Architecture:** Un orchestrateur Python (`benchrun`) tourne en conteneur sur le Mac. Il pilote chaque station par SSH via une nouvelle action `bench` de `llm-ctl.ps1`, qui lance llama-server depuis une spec JSON complète. Toutes les requêtes des harnais passent par une passerelle de mesure (`gateway`) qui impose le sampling de la configuration testée et journalise chaque échange. Les harnais publics tournent chacun dans leur conteneur épinglé ; les jeux privés et les journaux vivent dans le dépôt privé `Bench-LLM`.

**Tech Stack:** Python 3.12, aiohttp, PyYAML, pytest ; Docker Compose ; PowerShell 7 (stations Windows) ; llama.cpp llama-server ; harnais LiveCodeBench, Aider, bfcl-eval, LiveBench, RULER, NoLiMa ; Claude Code en mode `-p`.

**Spec:** `/private/tmp/claude-501/-Users-gputier-git-projects-Tools-LLM/38fc6ad7-18df-488c-88c0-1945de9c3a25/scratchpad/2026-09-23-banc-llm-refonte-design.md` (à copier dans `llm-station-cuda/docs/superpowers/specs/` à la tâche 1).

## Global Constraints

- Rien ne s'installe sur le Mac : tout outil, test ou harnais tourne dans Docker.
- Code, commentaires et docstrings en anglais ; doc et messages de commit en français.
- Aucun tiret cadratin ni demi-cadratin, nulle part. Contrôle : `grep -rlE $'\xe2\x80\x94|\xe2\x80\x93' <fichiers>` doit sortir en code 1.
- Aucune mention d'outil d'IA dans un commit. Aucun `git push` sans accord explicite de Guillaume.
- Les questions et tâches maison ne sont jamais publiées. Elles vivent dans `~/git_projects/Tools/Bench-LLM` (distant privé `git.shpv.work/gputier/bench-llm`). Le public ne garde que leur empreinte SHA-256.
- Aucune IP interne ni nom de machine dans ce qui est publié. Les IP vivent dans `bench/.env` (ignoré par git).
- 3 passes par cellule (modèle × configuration × épreuve). Aucune mesure sur la première requête après chargement.
- Arbre figé pendant une campagne : aucune modification du dépôt, des scripts de contrôle ni des GGUF entre le début et la fin d'une campagne.
- Sampling de la configuration imposé à chaque requête par la passerelle, quel que soit le harnais.
- Configurations : R1 = fiche éditeur fidèle ; R2 et R3 = paris, chaque réglage avec `source:` (URL + date) ou `source: "raisonnement, non sourcé"`.
- La 99 et la 97 tournent en parallèle, chacune sur ses modèles. ComfyUI est arrêté sur la 99 pendant la campagne.
- Toute intervention sur une machine se consigne dans sa fiche de vie du second brain (`sources/<propriétaire>/serveurs/<hôte>.md`), via /claude-memory.
- Mise en service d'une configuration gagnante : seulement avec le go de Guillaume.

## Review Focus

1. Harnais qui envoie ses propres paramètres de sampling (température 0 par défaut) : la passerelle doit les écraser. Test : `test_gateway_overrides_harness_sampling` (tâche 5).
2. Réponse en flux (SSE) coupée ou modifiée par la passerelle : le client doit recevoir les octets à l'identique et la passerelle doit tout de même relever `timings`. Test : `test_gateway_streams_bytes_unchanged` (tâche 5).
3. Campagne interrompue (coupure réseau, machine qui redémarre) : la reprise ne doit ni refaire une passe complète, ni en compter une deux fois. Test : `test_runner_resume_skips_complete_and_redoes_partial` (tâche 8).
4. Configuration qui fait déborder la VRAM en mémoire partagée Windows : la passe doit être marquée `spill` et exclue du classement de débit, pas classée silencieusement. Test : `test_runner_marks_spill` (tâche 8).
5. Tâche maison dont le clone expose encore le correctif (`git log`, reflog, packs) : la tâche doit être refusée à la construction. Test : `test_task_builder_rejects_leaking_history` (tâche 12).

---

## Partie A. Socle

### Task 1: Dépôt privé Bench-LLM et copie de la spec

**Files:**
- Create: `~/git_projects/Tools/Bench-LLM/README.md`
- Create: `~/git_projects/Tools/Bench-LLM/.gitignore`
- Create: `~/git_projects/Tools/Bench-LLM/sets/.gitkeep`, `tasks/.gitkeep`, `runs/.gitkeep`
- Create: `llm-station-cuda/docs/superpowers/specs/2026-09-23-banc-llm-refonte-design.md` (copie de la spec)
- Create: `llm-station-cuda/docs/superpowers/plans/2026-09-23-banc-llm-refonte-plan.md` (copie de ce plan)

**Interfaces:**
- Produces: variable `BENCH_PRIVATE=~/git_projects/Tools/Bench-LLM` lue par toutes les tâches suivantes ; sous-dossiers `sets/`, `tasks/`, `runs/`.

- [ ] **Step 1: Créer le dépôt local**

```bash
mkdir -p ~/git_projects/Tools/Bench-LLM/{sets,tasks,runs}
cd ~/git_projects/Tools/Bench-LLM
git init -b main
touch sets/.gitkeep tasks/.gitkeep runs/.gitkeep
```

- [ ] **Step 2: Écrire le README et le .gitignore**

`README.md` :

```markdown
# Bench-LLM (privé)

Jeux de questions maison, tâches agentiques et journaux bruts du banc LLM.
Ce dépôt ne doit jamais devenir public : un jeu publié finit dans les données
d'entraînement et ne mesure plus rien.

- `sets/` : jeux de questions, un fichier JSONL par jeu, versionnés.
- `tasks/` : tâches agentiques (une par dossier, `task.json` + `repo.tar.gz`).
- `runs/` : journaux JSONL des passes, une arborescence par campagne.

Le code du banc est public : `llm-station-cuda/bench/`. Il lit ce dépôt par la
variable `BENCH_PRIVATE`.
```

`.gitignore` :

```
.DS_Store
*.tmp
```

- [ ] **Step 3: Créer le distant privé et vérifier sa visibilité**

Lire `~/.claude/references/credentials.md` avant tout appel `glab`.

```bash
glab api --hostname git.shpv.work -X POST projects \
  -f name=bench-llm -f path=bench-llm -f visibility=private
glab api --hostname git.shpv.work "projects/gputier%2Fbench-llm" | python3 -c "import json,sys; print(json.load(sys.stdin)['visibility'])"
```

Expected: `private`

- [ ] **Step 4: Brancher le distant et committer en local**

```bash
cd ~/git_projects/Tools/Bench-LLM
git remote add origin git@git.shpv.work:gputier/bench-llm.git
git add -A && git commit -m "chore: initialiser le dépôt privé du banc"
```

Pas de push : il attend l'accord de Guillaume.

- [ ] **Step 5: Copier la spec et le plan dans le dépôt public, puis committer**

```bash
cd ~/git_projects/Tools/LLM/llm-station-cuda
mkdir -p docs/superpowers/specs docs/superpowers/plans
cp "<scratchpad>/2026-09-23-banc-llm-refonte-design.md" docs/superpowers/specs/
cp "<scratchpad>/2026-09-23-banc-llm-refonte-plan.md" docs/superpowers/plans/
grep -rlE $'\xe2\x80\x94|\xe2\x80\x93' docs/superpowers; echo "code=$?"
git add docs/superpowers && git commit -m "docs: spec et plan de la refonte du banc"
```

Expected: `code=1`

### Task 2: Image de travail benchrun et harnais de tests

**Files:**
- Create: `llm-station-cuda/bench/pyproject.toml`
- Create: `llm-station-cuda/bench/harness/benchrun.Dockerfile`
- Create: `llm-station-cuda/bench/scripts/test.sh`
- Create: `llm-station-cuda/bench/benchrun/__init__.py`
- Create: `llm-station-cuda/bench/tests/test_smoke.py`
- Modify: `llm-station-cuda/.gitignore` (ajouter `bench/.env`, `bench/runs/`)

**Interfaces:**
- Produces: `bench/scripts/test.sh [pytest args]` qui lance pytest dans l'image `benchrun:dev`.

- [ ] **Step 1: Écrire le test de fumée**

`bench/tests/test_smoke.py` :

```python
import benchrun


def test_package_version():
    assert benchrun.__version__ == "0.1.0"
```

- [ ] **Step 2: Écrire pyproject, Dockerfile et script de test**

`bench/pyproject.toml` :

```toml
[project]
name = "benchrun"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["aiohttp==3.10.10", "PyYAML==6.0.2"]

[project.optional-dependencies]
dev = ["pytest==8.3.3", "pytest-asyncio==0.24.0", "pytest-aiohttp==1.0.5"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`bench/harness/benchrun.Dockerfile` :

```dockerfile
FROM python:3.12.7-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /bench
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" || true
COPY . .
RUN pip install --no-cache-dir -e ".[dev]"
```

`bench/scripts/test.sh` :

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -q -t benchrun:dev -f harness/benchrun.Dockerfile . >/dev/null
docker run --rm -v "$PWD":/bench -w /bench benchrun:dev pytest -q "$@"
```

`bench/benchrun/__init__.py` :

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: Lancer le test**

Run: `chmod +x bench/scripts/test.sh && bench/scripts/test.sh`
Expected: `1 passed`

- [ ] **Step 4: Ignorer les fichiers locaux et committer**

Ajouter à `.gitignore` :

```
# Bench: station addresses and run journals stay local.
bench/.env
bench/runs/
```

```bash
git add bench/pyproject.toml bench/harness/benchrun.Dockerfile bench/scripts/test.sh bench/benchrun/__init__.py bench/tests/test_smoke.py .gitignore
git commit -m "chore(bench): image de travail et harnais de tests"
```

### Task 3: Schéma de configuration et rendu de la spec de lancement

**Files:**
- Create: `bench/benchrun/config.py`
- Create: `bench/tests/test_config.py`
- Create: `bench/tests/fixtures/config_ok.yaml`

**Interfaces:**
- Produces:
  - `load_config(path: str) -> BenchConfig`
  - `BenchConfig` (dataclass) : `machine: str`, `model: str`, `variant: str` (`R1|R2|R3`), `label: str`, `exe: str`, `workdir: str`, `cudabin: str | None`, `args: list[str]`, `env: dict[str, str]`, `sampling: dict[str, float | int]`, `chat_template_kwargs: dict`, `sources: dict[str, str]`, `ctx_proven: int | None`
  - `render_launch_spec(cfg: BenchConfig) -> dict` : `{"name", "exe", "workDir", "cudaBin", "args", "env"}`, le JSON que lit l'action `bench` de `llm-ctl.ps1`
  - `ConfigError(Exception)`

- [ ] **Step 1: Écrire les tests**

`bench/tests/fixtures/config_ok.yaml` :

```yaml
machine: "99"
model: tiel
variant: R1
label: "fiche éditeur, T0.6"
exe: 'D:\LLM-Setup\llama-cpp-b10826\llama-server.exe'
workdir: 'D:\LLM-Setup\llama-cpp-b10826'
cudabin: null
args: ["-m", 'D:\models\tiel\Tiel.gguf', "--ctx-size", "393216"]
env: {}
sampling: {temperature: 0.6, top_p: 0.95, top_k: 20, min_p: 0.0}
chat_template_kwargs: {}
sources:
  temperature: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B (lu le 2026-09-23)"
  top_p: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B (lu le 2026-09-23)"
  top_k: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B (lu le 2026-09-23)"
  min_p: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B (lu le 2026-09-23)"
ctx_proven: null
```

`bench/tests/test_config.py` :

```python
import pathlib
import pytest
import yaml
from benchrun.config import load_config, render_launch_spec, ConfigError

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_load_valid_config():
    cfg = load_config(FIX / "config_ok.yaml")
    assert cfg.model == "tiel" and cfg.variant == "R1"
    assert cfg.sampling["temperature"] == 0.6


def test_every_sampling_key_needs_a_source(tmp_path):
    data = yaml.safe_load((FIX / "config_ok.yaml").read_text())
    del data["sources"]["top_k"]
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="top_k"):
        load_config(p)


def test_variant_must_be_r1_r2_r3(tmp_path):
    data = yaml.safe_load((FIX / "config_ok.yaml").read_text())
    data["variant"] = "R4"
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="variant"):
        load_config(p)


def test_sampling_flags_forbidden_in_args(tmp_path):
    data = yaml.safe_load((FIX / "config_ok.yaml").read_text())
    data["args"] += ["--temp", "0.3"]
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="--temp"):
        load_config(p)


def test_render_launch_spec_names_instance_after_model_and_variant():
    spec = render_launch_spec(load_config(FIX / "config_ok.yaml"))
    assert spec["name"] == "bench-tiel-R1"
    assert spec["args"][:2] == ["-m", "D:\\models\\tiel\\Tiel.gguf"]
    assert set(spec) == {"name", "exe", "workDir", "cudaBin", "args", "env"}
```

- [ ] **Step 2: Vérifier l'échec**

Run: `bench/scripts/test.sh tests/test_config.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'benchrun.config'`

- [ ] **Step 3: Implémenter**

`bench/benchrun/config.py` :

```python
"""Bench configuration: one YAML file per model x variant x machine."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

VARIANTS = {"R1", "R2", "R3"}
# Sampling is enforced per request by the gateway, never baked into the server
# command line: a flag here would silently disagree with what the gateway sends.
SAMPLING_FLAGS = {
    "--temp", "--top-p", "--top-k", "--min-p", "--presence-penalty",
    "--repeat-penalty", "--frequency-penalty",
}
REQUIRED = ("machine", "model", "variant", "label", "exe", "workdir", "args", "sampling", "sources")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class BenchConfig:
    machine: str
    model: str
    variant: str
    label: str
    exe: str
    workdir: str
    args: list[str]
    sampling: dict
    sources: dict
    cudabin: str | None = None
    env: dict = field(default_factory=dict)
    chat_template_kwargs: dict = field(default_factory=dict)
    ctx_proven: int | None = None


def load_config(path) -> BenchConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise ConfigError(f"{path}: missing keys {missing}")
    if data["variant"] not in VARIANTS:
        raise ConfigError(f"{path}: variant must be one of {sorted(VARIANTS)}")
    for flag in data["args"]:
        if flag in SAMPLING_FLAGS:
            raise ConfigError(f"{path}: {flag} belongs in 'sampling', not in 'args'")
    for key in list(data["sampling"]) + list(data.get("chat_template_kwargs") or {}):
        if not data["sources"].get(key):
            raise ConfigError(f"{path}: setting '{key}' has no source")
    return BenchConfig(
        machine=str(data["machine"]), model=data["model"], variant=data["variant"],
        label=data["label"], exe=data["exe"], workdir=data["workdir"],
        args=[str(a) for a in data["args"]], sampling=data["sampling"],
        sources=data["sources"], cudabin=data.get("cudabin"), env=data.get("env") or {},
        chat_template_kwargs=data.get("chat_template_kwargs") or {},
        ctx_proven=data.get("ctx_proven"),
    )


def render_launch_spec(cfg: BenchConfig) -> dict:
    return {
        "name": f"bench-{cfg.model}-{cfg.variant}",
        "exe": cfg.exe,
        "workDir": cfg.workdir,
        "cudaBin": cfg.cudabin,
        "args": list(cfg.args),
        "env": dict(cfg.env),
    }
```

- [ ] **Step 4: Vérifier le passage**

Run: `bench/scripts/test.sh tests/test_config.py`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add bench/benchrun/config.py bench/tests/test_config.py bench/tests/fixtures/config_ok.yaml
git commit -m "feat(bench): schéma de configuration et spec de lancement"
```

### Task 4: Action `bench` dans les deux scripts de contrôle

**Files:**
- Modify: `llm-station-cuda/llm-ctl.ps1` (bloc `param` l.1-21, `switch ($Action)` l.447)
- Modify: `llm-station-cuda/llm-ctl-16gb.ps1` (bloc `param`, `switch ($Action)`)
- Create: `bench/tests/fixtures/launch_spec_99.json`, `launch_spec_97.json`
- Create: `bench/scripts/check-ctl-bench.sh`

**Interfaces:**
- Consumes: JSON de `render_launch_spec` (tâche 3).
- Produces: `llm-ctl.ps1 -Action bench -Spec <chemin.json> [-DryRun]`. Avec `-DryRun`, la commande n'exécute rien et écrit une ligne `DRYRUN <exe> <arg1> <arg2> ...`. Sans `-DryRun`, elle lance l'instance nommée `spec.name` via `Start-LLM`.

- [ ] **Step 1: Écrire le contrôle distant (le test)**

`bench/tests/fixtures/launch_spec_99.json` :

```json
{"name":"bench-ornith-R1","exe":"D:\\LLM-Setup\\llama-cpp-b10826\\llama-server.exe","workDir":"D:\\LLM-Setup\\llama-cpp-b10826","cudaBin":null,"args":["-m","D:\\models\\ornith-1.5-9b\\Ornith-1.5-9B-Q5_K_M.gguf","--ctx-size","8192"],"env":{}}
```

`launch_spec_97.json` : même contenu avec `"name":"bench-tiel-R1"`, `exe` et `workDir` pointant le build BeeLlama de la 97, `-m` pointant le GGUF de tiel sur la 97 (chemins relevés dans `llm-ctl-16gb.ps1`), et `"env":{"GGML_KVARN_WINDOW_CHUNK":"16384"}`.

`bench/scripts/check-ctl-bench.sh` :

```bash
#!/usr/bin/env bash
# Usage: check-ctl-bench.sh <ssh-target> <local-spec.json>
set -euo pipefail
target="$1"; spec="$2"
scp -q "$spec" "$target:D:/LLM-Setup/bench-specs/check.json"
out=$(ssh "$target" 'pwsh -NoProfile -File D:\LLM-Setup\llm-ctl.ps1 -Action bench -Spec D:\LLM-Setup\bench-specs\check.json -DryRun')
echo "$out"
exe=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['exe'])" "$spec")
grep -qF "DRYRUN $exe --alias" <<<"$out"
grep -qF -- "--ctx-size 8192" <<<"$out"
echo OK
```

- [ ] **Step 2: Vérifier l'échec sur la 99**

Lire la fiche `powershell-via-ssh-lancement-et-quoting` du second brain avant le premier appel.

Run: `ssh $STATION_99_SSH 'mkdir D:\LLM-Setup\bench-specs' ; bench/scripts/check-ctl-bench.sh $STATION_99_SSH bench/tests/fixtures/launch_spec_99.json`
Expected: échec, pas de ligne `DRYRUN` (action inconnue).

- [ ] **Step 3: Implémenter dans `llm-ctl.ps1`**

Dans `param(...)`, après `[switch]$NoSpec` :

```powershell
  # Path to a bench launch spec (JSON: name, exe, workDir, cudaBin, args, env).
  # Written by benchrun, never by hand. See bench/benchrun/config.py.
  [string]$Spec = '',
  # With -Action bench: print the command line and start nothing.
  [switch]$DryRun
```

Dans `switch ($Action)`, après `'logs'` :

```powershell
  'bench' {
    if (-not $Spec -or -not (Test-Path $Spec)) { Write-Output "ERROR spec not found: $Spec"; exit 2 }
    $s = Get-Content -Raw $Spec | ConvertFrom-Json
    $benchArgs = @($s.args)
    if ($DryRun) {
      Write-Output ("DRYRUN " + $s.exe + " " + ((@('--alias', $s.name) + $benchArgs) -join ' '))
      break
    }
    foreach ($p in $s.env.PSObject.Properties) { Set-Item -Path "Env:$($p.Name)" -Value $p.Value }
    Start-LLM $s.name $benchArgs $null $s.exe $s.workDir $s.cudaBin
    break
  }
```

- [ ] **Step 4: Implémenter dans `llm-ctl-16gb.ps1`**

Mêmes ajouts dans `param`. Dans le `switch`, la signature diffère (`Start-LLM($name, $modelArgs, $exePath, $workDirPath, $envVars)`) :

```powershell
  'bench' {
    if (-not $Spec -or -not (Test-Path $Spec)) { Write-Output "ERROR spec not found: $Spec"; exit 2 }
    $s = Get-Content -Raw $Spec | ConvertFrom-Json
    $benchArgs = @($s.args)
    if ($DryRun) {
      Write-Output ("DRYRUN " + $s.exe + " " + ((@('--alias', $s.name) + $benchArgs) -join ' '))
      break
    }
    $envVars = @{}
    foreach ($p in $s.env.PSObject.Properties) { $envVars[$p.Name] = $p.Value }
    Start-LLM $s.name $benchArgs $s.exe $s.workDir $envVars
    break
  }
```

Lire d'abord la fonction `Start-LLM` de `llm-ctl-16gb.ps1` (l.150) pour vérifier qu'elle ajoute elle-même `--alias $name`. Si ce n'est pas le cas, retirer `--alias` de la ligne `DRYRUN` pour que le contrôle reflète ce qui est réellement lancé.

- [ ] **Step 5: Déployer, puis vérifier sur les deux machines**

```bash
scp llm-ctl.ps1 $STATION_99_SSH:D:/LLM-Setup/llm-ctl.ps1
scp llm-ctl-16gb.ps1 <cible-97>:D:/LLM-Setup/llm-ctl.ps1
bench/scripts/check-ctl-bench.sh $STATION_99_SSH bench/tests/fixtures/launch_spec_99.json
bench/scripts/check-ctl-bench.sh <cible-97> bench/tests/fixtures/launch_spec_97.json
```

Expected: `OK` deux fois. `<cible-97>` est l'utilisateur et l'hôte relevés dans la fiche de vie de PC-GUILLAUME-3.

- [ ] **Step 6: Lancement réel minimal, puis arrêt**

```bash
ssh $STATION_99_SSH 'pwsh -NoProfile -File D:\LLM-Setup\llm-ctl.ps1 -Action bench -Spec D:\LLM-Setup\bench-specs\check.json'
curl -sf $STATION_99_URL/health
curl -sf $STATION_99_URL/v1/models | python3 -c "import json,sys; print(json.load(sys.stdin)['data'][0]['id'])"
ssh $STATION_99_SSH 'pwsh -NoProfile -File D:\LLM-Setup\llm-ctl.ps1 -Action stop'
```

Expected: `{"status":"ok"}`, puis `bench-ornith-R1`.

- [ ] **Step 7: Vérifier que les profils existants n'ont pas bougé**

Run: `ssh $STATION_99_SSH 'pwsh -NoProfile -File D:\LLM-Setup\llm-ctl.ps1 -Action ornith -Extra "--ctx-size 8192"'`, puis `/health`, puis `-Action stop`.
Expected: `ok`. Le profil `ornith` démarre comme avant.

- [ ] **Step 8: Commit**

```bash
git add llm-ctl.ps1 llm-ctl-16gb.ps1 bench/tests/fixtures/launch_spec_9*.json bench/scripts/check-ctl-bench.sh
git commit -m "feat(ctl): action bench, lancement depuis une spec JSON"
```

Consigner le déploiement dans les deux fiches de vie via /claude-memory.

### Task 5: Passerelle de mesure

**Files:**
- Create: `bench/benchrun/gateway.py`
- Create: `bench/tests/test_gateway.py`

**Interfaces:**
- Consumes: `BenchConfig.sampling`, `BenchConfig.chat_template_kwargs` (tâche 3).
- Produces:
  - `make_app(upstream: str, journal_path: str) -> aiohttp.web.Application`
  - Contrôle : `POST /_bench/context` avec JSON `{"run_id","suite","rep","sampling","chat_template_kwargs"}` ; `DELETE /_bench/context`.
  - Relais : `POST /v1/chat/completions`, `/v1/completions`, `/v1/messages` ; `GET` relayé tel quel pour tout autre chemin.
  - Journal JSONL, une ligne par requête : `{"run_id","suite","rep","path","t_start","ttft_s","total_s","status","request_sampling","timings","usage","reasoning_chars"}`.

- [ ] **Step 1: Écrire les tests**

`bench/tests/test_gateway.py` :

```python
import json
from aiohttp import web
from benchrun.gateway import make_app


async def fake_upstream(request):
    body = await request.json()
    request.app["seen"].append(body)
    if body.get("stream"):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
        await resp.write(b'data: {"choices":[{"delta":{}}],"timings":{"predicted_per_second":42.0,"draft_n":10,"draft_n_accepted":7}}\n\n')
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp
    return web.json_response({
        "choices": [{"message": {"content": "ok", "reasoning_content": "abc"}}],
        "usage": {"completion_tokens": 3},
        "timings": {"predicted_per_second": 50.0, "prompt_per_second": 900.0},
    })


async def start(aiohttp_client, tmp_path):
    up = web.Application()
    up["seen"] = []
    up.router.add_post("/v1/chat/completions", fake_upstream)
    up_client = await aiohttp_client(up)
    journal = tmp_path / "journal.jsonl"
    gw = await aiohttp_client(make_app(str(up_client.make_url("")).rstrip("/"), str(journal)))
    ctx = {"run_id": "r1", "suite": "lcb", "rep": 1,
           "sampling": {"temperature": 0.6, "top_k": 20}, "chat_template_kwargs": {"reasoning_effort": "medium"}}
    assert (await gw.post("/_bench/context", json=ctx)).status == 200
    return gw, up, journal


async def test_gateway_overrides_harness_sampling(aiohttp_client, tmp_path):
    gw, up, _ = await start(aiohttp_client, tmp_path)
    await gw.post("/v1/chat/completions", json={"messages": [], "temperature": 0.0, "top_k": 1})
    sent = up["seen"][-1]
    assert sent["temperature"] == 0.6 and sent["top_k"] == 20
    assert sent["chat_template_kwargs"]["reasoning_effort"] == "medium"


async def test_gateway_journals_timings_and_reasoning(aiohttp_client, tmp_path):
    gw, _, journal = await start(aiohttp_client, tmp_path)
    r = await gw.post("/v1/chat/completions", json={"messages": []})
    assert (await r.json())["choices"][0]["message"]["content"] == "ok"
    rec = json.loads(journal.read_text().splitlines()[-1])
    assert rec["run_id"] == "r1" and rec["rep"] == 1
    assert rec["timings"]["predicted_per_second"] == 50.0
    assert rec["reasoning_chars"] == 3


async def test_gateway_streams_bytes_unchanged(aiohttp_client, tmp_path):
    gw, _, journal = await start(aiohttp_client, tmp_path)
    r = await gw.post("/v1/chat/completions", json={"messages": [], "stream": True})
    raw = await r.read()
    assert raw.endswith(b"data: [DONE]\n\n") and b'"content":"hi"' in raw
    rec = json.loads(journal.read_text().splitlines()[-1])
    assert rec["timings"]["draft_n_accepted"] == 7
    assert rec["ttft_s"] is not None


async def test_gateway_refuses_without_context(aiohttp_client, tmp_path):
    gw, _, _ = await start(aiohttp_client, tmp_path)
    await gw.delete("/_bench/context")
    r = await gw.post("/v1/chat/completions", json={"messages": []})
    assert r.status == 409
```

- [ ] **Step 2: Vérifier l'échec**

Run: `bench/scripts/test.sh tests/test_gateway.py`
Expected: FAIL, `No module named 'benchrun.gateway'`

- [ ] **Step 3: Implémenter**

`bench/benchrun/gateway.py` :

```python
"""Measurement gateway between the harnesses and llama-server.

Every harness request goes through here. The gateway overwrites sampling with
the values of the configuration under test (harnesses ship their own defaults,
often temperature 0), relays the answer byte for byte, and journals timings.
"""
from __future__ import annotations

import json
import time

import aiohttp
from aiohttp import web

RELAYED_POSTS = ("/v1/chat/completions", "/v1/completions", "/v1/messages")


def _apply(body: dict, ctx: dict, path: str) -> dict:
    body = dict(body)
    for key, value in ctx["sampling"].items():
        body[key] = value
    if ctx.get("chat_template_kwargs") and path != "/v1/messages":
        merged = dict(body.get("chat_template_kwargs") or {})
        merged.update(ctx["chat_template_kwargs"])
        body["chat_template_kwargs"] = merged
    if body.get("stream") and path != "/v1/messages":
        opts = dict(body.get("stream_options") or {})
        opts["include_usage"] = True
        body["stream_options"] = opts
    return body


def _reasoning_chars(payload: dict) -> int:
    try:
        return len(payload["choices"][0]["message"].get("reasoning_content") or "")
    except (KeyError, IndexError, TypeError):
        return 0


def _scan_sse(buffer: bytes, state: dict) -> None:
    for line in buffer.split(b"\n"):
        if not line.startswith(b"data: ") or line == b"data: [DONE]":
            continue
        try:
            evt = json.loads(line[6:])
        except ValueError:
            continue
        if "timings" in evt:
            state["timings"] = evt["timings"]
        if evt.get("usage"):
            state["usage"] = evt["usage"]
        for choice in evt.get("choices") or []:
            state["reasoning_chars"] += len((choice.get("delta") or {}).get("reasoning_content") or "")


def make_app(upstream: str, journal_path: str) -> web.Application:
    app = web.Application(client_max_size=256 * 1024 * 1024)
    app["ctx"] = None

    async def set_ctx(request):
        app["ctx"] = await request.json()
        return web.json_response({"ok": True})

    async def clear_ctx(request):
        app["ctx"] = None
        return web.json_response({"ok": True})

    def journal(rec: dict) -> None:
        with open(journal_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    async def relay_post(request):
        ctx = app["ctx"]
        if ctx is None:
            return web.json_response({"error": "no bench context set"}, status=409)
        path = request.path
        body = _apply(await request.json(), ctx, path)
        rec = {"run_id": ctx["run_id"], "suite": ctx["suite"], "rep": ctx["rep"], "path": path,
               "t_start": time.time(), "ttft_s": None, "total_s": None, "status": None,
               "request_sampling": {k: body.get(k) for k in ctx["sampling"]},
               "timings": None, "usage": None, "reasoning_chars": 0}
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        t0 = time.monotonic()
        timeout = aiohttp.ClientTimeout(total=None, sock_read=3600)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(upstream + path, json=body, headers=headers) as up:
                rec["status"] = up.status
                if body.get("stream"):
                    resp = web.StreamResponse(status=up.status, headers={
                        "Content-Type": up.headers.get("Content-Type", "text/event-stream")})
                    await resp.prepare(request)
                    state = {"timings": None, "usage": None, "reasoning_chars": 0}
                    tail = b""
                    async for chunk in up.content.iter_any():
                        if rec["ttft_s"] is None:
                            rec["ttft_s"] = time.monotonic() - t0
                        await resp.write(chunk)
                        tail += chunk
                        cut = tail.rfind(b"\n\n")
                        if cut >= 0:
                            _scan_sse(tail[:cut], state)
                            tail = tail[cut + 2:]
                    _scan_sse(tail, state)
                    await resp.write_eof()
                    rec.update(state)
                else:
                    raw = await up.read()
                    rec["ttft_s"] = time.monotonic() - t0
                    try:
                        payload = json.loads(raw)
                        rec["timings"] = payload.get("timings")
                        rec["usage"] = payload.get("usage")
                        rec["reasoning_chars"] = _reasoning_chars(payload)
                    except ValueError:
                        pass
                    resp = web.Response(status=up.status, body=raw,
                                        content_type=up.headers.get("Content-Type", "application/json").split(";")[0])
        rec["total_s"] = time.monotonic() - t0
        journal(rec)
        return resp

    async def relay_get(request):
        async with aiohttp.ClientSession() as s:
            async with s.get(upstream + request.path_qs) as up:
                return web.Response(status=up.status, body=await up.read(),
                                    content_type=up.headers.get("Content-Type", "application/json").split(";")[0])

    app.router.add_post("/_bench/context", set_ctx)
    app.router.add_delete("/_bench/context", clear_ctx)
    for p in RELAYED_POSTS:
        app.router.add_post(p, relay_post)
    app.router.add_get("/{tail:.*}", relay_get)
    return app


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--journal", required=True)
    ap.add_argument("--port", type=int, default=8081)
    a = ap.parse_args()
    web.run_app(make_app(a.upstream.rstrip("/"), a.journal), port=a.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Vérifier le passage**

Run: `bench/scripts/test.sh tests/test_gateway.py`
Expected: `4 passed`

- [ ] **Step 5: Vérifier contre un vrai llama-server (Anthropic et OpenAI)**

Lancer `ornith` en contexte réduit sur la 99 par l'action `bench` (tâche 4), puis :

```bash
docker run --rm -d --name gw -p 8081:8081 -v "$PWD/bench":/bench -v /tmp/gw:/j benchrun:dev \
  python -m benchrun.gateway --upstream $STATION_99_URL --journal /j/j.jsonl
curl -s -XPOST localhost:8081/_bench/context -d '{"run_id":"t","suite":"s","rep":1,"sampling":{"temperature":0.6},"chat_template_kwargs":{}}'
curl -s localhost:8081/v1/chat/completions -d '{"messages":[{"role":"user","content":"2+2?"}],"max_tokens":64}' >/dev/null
curl -s localhost:8081/v1/messages -H 'anthropic-version: 2023-06-01' -d '{"model":"x","max_tokens":64,"messages":[{"role":"user","content":"2+2?"}]}' >/dev/null
tail -2 /tmp/gw/j.jsonl
docker stop gw
```

Expected: deux lignes de journal avec `status` 200 et `timings` non nuls. Si `/v1/messages` ne renvoie pas `timings`, le noter dans la doc de la passerelle : le débit des passes Claude Code se lit alors dans le journal du serveur (`-Action logs`).

- [ ] **Step 6: Commit**

```bash
git add bench/benchrun/gateway.py bench/tests/test_gateway.py
git commit -m "feat(bench): passerelle de mesure et d'imposition du sampling"
```

### Task 6: Pilotage des stations

**Files:**
- Create: `bench/benchrun/station.py`
- Create: `bench/tests/test_station.py`

**Interfaces:**
- Consumes: `render_launch_spec` (tâche 3), action `bench` (tâche 4).
- Produces:
  - `Station(ssh_target: str, base_url: str, ctl_path: str, runner=subprocess.run)`
  - `Station.start(cfg: BenchConfig, timeout_s: int = 900) -> None` : dépose la spec, lance `-Action bench`, attend `/health` = ok.
  - `Station.stop() -> None`
  - `Station.warmup() -> None` : une requête de 16 jetons jetée (pas de mesure sur la première requête).
  - `Station.vram() -> dict` : `{"used_mb": int, "shared_mb": int}` via `nvidia-smi` et `Get-Counter '\GPU Process Memory(*)\Shared Usage'`.
  - `StationError(Exception)`

- [ ] **Step 1: Écrire les tests (runner simulé)**

`bench/tests/test_station.py` :

```python
import subprocess
import pytest
from benchrun.station import Station, StationError, parse_vram
from benchrun.config import load_config
import pathlib

CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_start_uploads_spec_then_launches(monkeypatch):
    run = FakeRun()
    st = Station("u@h", "http://h:8080", r"D:\LLM-Setup\llm-ctl.ps1", runner=run)
    monkeypatch.setattr(st, "_health_ok", lambda: True)
    st.start(CFG, timeout_s=1)
    assert run.calls[0][0] == "scp"
    assert "-Action bench" in run.calls[1][-1]
    assert "bench-tiel-R1.json" in run.calls[1][-1]


def test_start_times_out_when_health_never_ok(monkeypatch):
    st = Station("u@h", "http://h:8080", r"D:\x.ps1", runner=FakeRun())
    monkeypatch.setattr(st, "_health_ok", lambda: False)
    with pytest.raises(StationError, match="health"):
        st.start(CFG, timeout_s=0)


def test_parse_vram():
    assert parse_vram("24576\n", "123456789\n") == {"used_mb": 24576, "shared_mb": 117}
```

- [ ] **Step 2: Vérifier l'échec**

Run: `bench/scripts/test.sh tests/test_station.py`
Expected: FAIL, module absent.

- [ ] **Step 3: Implémenter**

`bench/benchrun/station.py` :

```python
"""Drive one llama-server station over SSH through llm-ctl.ps1 -Action bench."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
import urllib.request

from .config import BenchConfig, render_launch_spec

SPEC_DIR = r"D:\LLM-Setup\bench-specs"


class StationError(Exception):
    pass


def parse_vram(nvidia_out: str, shared_bytes_out: str) -> dict:
    used = int(nvidia_out.strip().splitlines()[0])
    shared = int(float(shared_bytes_out.strip() or 0)) // (1024 * 1024)
    return {"used_mb": used, "shared_mb": shared}


class Station:
    def __init__(self, ssh_target: str, base_url: str, ctl_path: str, runner=subprocess.run):
        self.ssh_target, self.base_url, self.ctl_path, self.run = ssh_target, base_url.rstrip("/"), ctl_path, runner

    def _ps(self, command: str) -> subprocess.CompletedProcess:
        return self.run(["ssh", self.ssh_target, f"pwsh -NoProfile -Command \"{command}\""],
                        capture_output=True, text=True, check=True)

    def _health_ok(self) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/health", timeout=5) as r:
                return json.load(r).get("status") == "ok"
        except OSError:
            return False

    def start(self, cfg: BenchConfig, timeout_s: int = 900) -> None:
        spec = render_launch_spec(cfg)
        remote = f"{SPEC_DIR}\\{spec['name']}.json"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(spec, fh)
        self.run(["scp", "-q", fh.name, f"{self.ssh_target}:{remote.replace(chr(92), '/')}"], check=True)
        self._ps(f"& '{self.ctl_path}' -Action bench -Spec '{remote}'")
        deadline = time.monotonic() + timeout_s
        while True:
            if self._health_ok():
                return
            if time.monotonic() >= deadline:
                raise StationError(f"health never ok for {spec['name']} within {timeout_s}s")
            time.sleep(5)

    def stop(self) -> None:
        self._ps(f"& '{self.ctl_path}' -Action stop")

    def warmup(self) -> None:
        body = json.dumps({"messages": [{"role": "user", "content": "Say ok."}], "max_tokens": 16}).encode()
        req = urllib.request.Request(self.base_url + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=600).read()

    def vram(self) -> dict:
        used = self._ps("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits").stdout
        shared = self._ps("(Get-Counter '\\GPU Process Memory(*)\\Shared Usage').CounterSamples | "
                          "Measure-Object CookedValue -Sum | ForEach-Object Sum").stdout
        return parse_vram(used, shared)
```

- [ ] **Step 4: Vérifier le passage**

Run: `bench/scripts/test.sh tests/test_station.py`
Expected: `3 passed`

- [ ] **Step 5: Essai réel sur la 99 et la 97**

Écrire un script jetable dans le scratchpad (hors dépôt) qui appelle `Station.start`, `warmup`, `vram`, puis `stop` avec la fixture de la tâche 4, exécuté dans l'image `benchrun:dev` avec `-v ~/.ssh:/root/.ssh:ro`. Expected : `used_mb` > 0 et `shared_mb` petit, sans erreur.

- [ ] **Step 6: Commit**

```bash
git add bench/benchrun/station.py bench/tests/test_station.py
git commit -m "feat(bench): pilotage des stations par l'action bench"
```

### Task 7: Statistiques

**Files:**
- Create: `bench/benchrun/stats.py`
- Create: `bench/tests/test_stats.py`

**Interfaces:**
- Produces:
  - `item_scores(results: list[dict]) -> dict[str, float]` : pour chaque `item_id`, moyenne de `passed` (0/1) sur les passes.
  - `bootstrap_ci(scores: dict[str, float], b: int = 10000, seed: int = 0) -> tuple[float, float, float]` : (moyenne, borne basse, borne haute), en rééchantillonnant les items.
  - `paired_diff_ci(a: dict, b: dict, b_iter: int = 10000, seed: int = 0) -> tuple[float, float, float]` : sur les items communs.
  - `mcnemar_exact(a: dict, b: dict) -> float` : p-value exacte bilatérale, un item réussi si sa moyenne est > 0.5.
  - `min_detectable_gap(n_items: int, p: float = 0.6) -> float` : écart minimal détectable (alpha 0,05, puissance 0,8), en points.

- [ ] **Step 1: Écrire les tests**

`bench/tests/test_stats.py` :

```python
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
```

- [ ] **Step 2: Vérifier l'échec**

Run: `bench/scripts/test.sh tests/test_stats.py`
Expected: FAIL, module absent.

- [ ] **Step 3: Implémenter**

`bench/benchrun/stats.py` :

```python
"""Statistics for the bench: paired item bootstrap and exact McNemar.

Items are resampled, never individual passes: the three passes of an item are
correlated, and resampling them would shrink the interval dishonestly.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict


def item_scores(results: list[dict]) -> dict[str, float]:
    acc = defaultdict(list)
    for r in results:
        acc[r["item_id"]].append(float(r["passed"]))
    return {k: sum(v) / len(v) for k, v in acc.items()}


def _percentiles(values: list[float]) -> tuple[float, float]:
    values = sorted(values)
    lo = values[int(0.025 * (len(values) - 1))]
    hi = values[int(math.ceil(0.975 * (len(values) - 1)))]
    return lo, hi


def bootstrap_ci(scores: dict[str, float], b: int = 10000, seed: int = 0) -> tuple[float, float, float]:
    vals = list(scores.values())
    rng = random.Random(seed)
    n = len(vals)
    means = [sum(rng.choice(vals) for _ in range(n)) / n for _ in range(b)]
    lo, hi = _percentiles(means)
    return sum(vals) / n, lo, hi


def paired_diff_ci(a: dict, b: dict, b_iter: int = 10000, seed: int = 0) -> tuple[float, float, float]:
    common = sorted(set(a) & set(b))
    diffs = {k: a[k] - b[k] for k in common}
    return bootstrap_ci(diffs, b=b_iter, seed=seed)


def mcnemar_exact(a: dict, b: dict) -> float:
    common = set(a) & set(b)
    only_a = sum(1 for k in common if a[k] > 0.5 and b[k] <= 0.5)
    only_b = sum(1 for k in common if b[k] > 0.5 and a[k] <= 0.5)
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def min_detectable_gap(n_items: int, p: float = 0.6) -> float:
    z_alpha, z_beta = 1.959964, 0.841621
    return 100 * (z_alpha + z_beta) * math.sqrt(2 * p * (1 - p) / n_items)
```

- [ ] **Step 4: Vérifier le passage**

Run: `bench/scripts/test.sh tests/test_stats.py`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add bench/benchrun/stats.py bench/tests/test_stats.py
git commit -m "feat(bench): bootstrap apparié et McNemar exact"
```

### Task 8: Orchestrateur de campagne

**Files:**
- Create: `bench/benchrun/runner.py`
- Create: `bench/benchrun/suites/__init__.py`
- Create: `bench/tests/test_runner.py`

**Interfaces:**
- Consumes: `load_config` (3), `Station` (6), passerelle (5).
- Produces:
  - `Suite` (protocole) : `name: str`, `run(ctx: SuiteContext) -> list[dict]`. Chaque dict : `{"item_id": str, "passed": 0|1, "detail": dict}`.
  - `SuiteContext` (dataclass) : `base_url: str` (URL de la passerelle), `cfg: BenchConfig`, `rep: int`, `out_dir: pathlib.Path`, `private_root: pathlib.Path`.
  - `Campaign(station, gateway_url: str, suites: list[Suite], out_root: pathlib.Path, reps: int = 3, spill_limit_mb: int = 256)`
  - `Campaign.run(configs: list[BenchConfig]) -> None`
  - Résultat sur disque : `out_root/<machine>/<model>/<variant>/<suite>/rep<k>.jsonl`, puis un marqueur `rep<k>.done` une fois la passe complète. `out_root/<machine>/<model>/<variant>/meta.json` : `{"vram": {...}, "spill": bool, "config": <yaml brut>, "started", "ended"}`.

- [ ] **Step 1: Écrire les tests**

`bench/tests/test_runner.py` :

```python
import json
import pathlib
from benchrun.runner import Campaign
from benchrun.config import load_config

CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")


class FakeStation:
    def __init__(self, shared_mb=0):
        self.log, self.shared_mb = [], shared_mb

    def start(self, cfg, timeout_s=900): self.log.append("start")
    def warmup(self): self.log.append("warmup")
    def stop(self): self.log.append("stop")
    def vram(self): return {"used_mb": 20000, "shared_mb": self.shared_mb}


class FakeSuite:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        return [{"item_id": "i1", "passed": 1, "detail": {}}]


def make(tmp_path, station=None, suite=None):
    return Campaign(station or FakeStation(), "http://gw", [suite or FakeSuite()], tmp_path, reps=3), suite


def test_runner_runs_three_reps_after_warmup(tmp_path, monkeypatch):
    suite = FakeSuite()
    camp, _ = make(tmp_path, suite=suite)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    camp.run([CFG])
    assert suite.calls == 3
    assert camp.station.log == ["start", "warmup", "stop"]
    base = tmp_path / "99" / "tiel" / "R1" / "fake"
    assert all((base / f"rep{k}.done").exists() for k in (1, 2, 3))


def test_runner_resume_skips_complete_and_redoes_partial(tmp_path, monkeypatch):
    base = tmp_path / "99" / "tiel" / "R1" / "fake"
    base.mkdir(parents=True)
    (base / "rep1.jsonl").write_text('{"item_id":"i1","passed":1,"detail":{}}\n')
    (base / "rep1.done").write_text("")
    (base / "rep2.jsonl").write_text('{"item_id":"i1","passed":0,"detail":{}}\n')
    suite = FakeSuite()
    camp, _ = make(tmp_path, suite=suite)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    camp.run([CFG])
    assert suite.calls == 2
    assert len((base / "rep2.jsonl").read_text().splitlines()) == 1


def test_runner_skips_station_when_everything_done(tmp_path, monkeypatch):
    base = tmp_path / "99" / "tiel" / "R1" / "fake"
    base.mkdir(parents=True)
    for k in (1, 2, 3):
        (base / f"rep{k}.jsonl").write_text("")
        (base / f"rep{k}.done").write_text("")
    camp, _ = make(tmp_path)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    camp.run([CFG])
    assert camp.station.log == []


def test_runner_marks_spill(tmp_path, monkeypatch):
    camp, _ = make(tmp_path, station=FakeStation(shared_mb=900))
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    camp.run([CFG])
    meta = json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())
    assert meta["spill"] is True
```

- [ ] **Step 2: Vérifier l'échec**

Run: `bench/scripts/test.sh tests/test_runner.py`
Expected: FAIL, module absent.

- [ ] **Step 3: Implémenter**

`bench/benchrun/suites/__init__.py` :

```python
"""Suite adapters. Each module exposes one class implementing benchrun.runner.Suite."""
```

`bench/benchrun/runner.py` :

```python
"""Campaign runner: configs x suites x reps, idempotent and resumable."""
from __future__ import annotations

import dataclasses
import json
import pathlib
import time
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .config import BenchConfig


@dataclass
class SuiteContext:
    base_url: str
    cfg: BenchConfig
    rep: int
    out_dir: pathlib.Path
    private_root: pathlib.Path


class Suite(Protocol):
    name: str

    def run(self, ctx: SuiteContext) -> list[dict]: ...


class Campaign:
    def __init__(self, station, gateway_url: str, suites: list, out_root: pathlib.Path,
                 reps: int = 3, spill_limit_mb: int = 256, private_root: pathlib.Path | None = None):
        self.station, self.gateway_url, self.suites = station, gateway_url.rstrip("/"), suites
        self.out_root, self.reps, self.spill_limit_mb = pathlib.Path(out_root), reps, spill_limit_mb
        self.private_root = pathlib.Path(private_root or out_root)

    def _cell(self, cfg: BenchConfig) -> pathlib.Path:
        return self.out_root / cfg.machine / cfg.model / cfg.variant

    def _pending(self, cfg: BenchConfig) -> list[tuple]:
        todo = []
        for suite in self.suites:
            for rep in range(1, self.reps + 1):
                if not (self._cell(cfg) / suite.name / f"rep{rep}.done").exists():
                    todo.append((suite, rep))
        return todo

    def _set_context(self, cfg: BenchConfig, suite_name: str, rep: int) -> None:
        body = json.dumps({"run_id": f"{cfg.machine}/{cfg.model}/{cfg.variant}", "suite": suite_name,
                           "rep": rep, "sampling": cfg.sampling,
                           "chat_template_kwargs": cfg.chat_template_kwargs}).encode()
        req = urllib.request.Request(self.gateway_url + "/_bench/context", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10).read()

    def run(self, configs: list[BenchConfig]) -> None:
        for cfg in configs:
            todo = self._pending(cfg)
            if not todo:
                continue
            cell = self._cell(cfg)
            cell.mkdir(parents=True, exist_ok=True)
            started = time.time()
            self.station.start(cfg)
            try:
                self.station.warmup()
                vram = self.station.vram()
                for suite, rep in todo:
                    out = cell / suite.name
                    out.mkdir(parents=True, exist_ok=True)
                    self._set_context(cfg, suite.name, rep)
                    ctx = SuiteContext(self.gateway_url, cfg, rep, out, self.private_root)
                    results = suite.run(ctx)
                    with open(out / f"rep{rep}.jsonl", "w", encoding="utf-8") as fh:
                        for r in results:
                            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                    (out / f"rep{rep}.done").write_text("")
            finally:
                self.station.stop()
            meta = {"vram": vram, "spill": vram["shared_mb"] > self.spill_limit_mb,
                    "config": dataclasses.asdict(cfg), "started": started, "ended": time.time()}
            (cell / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
```

- [ ] **Step 4: Vérifier le passage**

Run: `bench/scripts/test.sh tests/test_runner.py`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add bench/benchrun/runner.py bench/benchrun/suites/__init__.py bench/tests/test_runner.py
git commit -m "feat(bench): orchestrateur de campagne reprenable"
```

## Partie B. Épreuves

Règle commune aux tâches 9 à 12 : chaque harnais est épinglé par commit dans son Dockerfile. Le test d'analyse des résultats s'écrit sur une sortie RÉELLE du harnais, capturée lors d'une passe minimale contre `ornith` en contexte réduit, puis déposée comme fixture. On n'écrit jamais un parseur sur un format supposé (fiche `test-qui-recopie-sa-reference-ne-mesure-rien`).

### Task 9: LiveCodeBench et Aider Polyglot

**Files:**
- Create: `bench/harness/lcb.Dockerfile`, `bench/harness/aider.Dockerfile`
- Create: `bench/benchrun/suites/lcb.py`, `bench/benchrun/suites/aider.py`
- Create: `bench/tests/fixtures/lcb_eval_sample.json`, `bench/tests/fixtures/aider_results_sample/` (sorties réelles)
- Create: `bench/tests/test_suite_lcb.py`, `bench/tests/test_suite_aider.py`
- Create: `Bench-LLM/sets/aider-subset-60.txt` (liste figée des exercices)

**Interfaces:**
- Consumes: `SuiteContext` (8).
- Produces: `LiveCodeBenchSuite(n_problems=100, release="release_v6", after_date: str)` et `AiderSuite(exercises_file: str)`, qui implémentent `Suite` avec `name` = `"lcb"` et `"aider"`. Fonctions pures testées : `parse_lcb(eval_json: dict) -> list[dict]` et `parse_aider(results_dir: pathlib.Path) -> list[dict]`.

- [ ] **Step 1: Épingler les harnais**

Relever le dernier commit de `livecodebench/livecodebench` et de `Aider-AI/aider` (`gh api repos/<r>/commits/main --jq .sha`), puis écrire les Dockerfiles :

`bench/harness/lcb.Dockerfile` :

```dockerfile
FROM python:3.11.10-slim
ARG LCB_SHA
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/livecodebench/livecodebench /lcb && cd /lcb && git checkout ${LCB_SHA} && pip install --no-cache-dir -e .
WORKDIR /lcb
```

`bench/harness/aider.Dockerfile` :

```dockerfile
FROM python:3.11.10-slim
ARG AIDER_SHA
ARG POLYGLOT_SHA
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential golang default-jdk nodejs npm rustc cargo cmake && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/Aider-AI/aider /aider && cd /aider && git checkout ${AIDER_SHA} && pip install --no-cache-dir -e . -r requirements/requirements-dev.txt
RUN git clone https://github.com/Aider-AI/polyglot-benchmark /polyglot && cd /polyglot && git checkout ${POLYGLOT_SHA}
WORKDIR /aider
```

Inscrire les SHA dans `bench/harness/pins.env` (`LCB_SHA=...`, `AIDER_SHA=...`, `POLYGLOT_SHA=...`).

- [ ] **Step 2: Passe minimale réelle et capture des sorties**

Lancer `ornith` sur la 99 (tâche 4) et la passerelle (tâche 5), puis :

```bash
docker build -t bench-lcb --build-arg LCB_SHA=$LCB_SHA -f bench/harness/lcb.Dockerfile bench/harness
docker run --rm --network host -e OPENAI_BASE_URL=http://localhost:8081/v1 -e OPENAI_API_KEY=x -v /tmp/lcb:/lcb/output bench-lcb \
  python -m lcb_runner.runner.main --model bench-ornith-R1 --scenario codegeneration --evaluate --release_version release_v6 --start_date 2025-01-01 --n 1
ls /tmp/lcb
```

Lire la documentation du dépôt épinglé pour les options exactes. Si `--start_date` ou `--n` n'existent pas sous ce nom, prendre l'option équivalente qu'il documente. Copier le fichier d'évaluation produit dans `bench/tests/fixtures/lcb_eval_sample.json`, réduit à 3 problèmes. Faire de même pour Aider avec `--num-tests 3 --edit-format whole`, et copier le dossier de résultats dans `bench/tests/fixtures/aider_results_sample/`.

- [ ] **Step 3: Écrire les tests sur les fixtures réelles**

`bench/tests/test_suite_lcb.py` :

```python
import json
import pathlib
from benchrun.suites.lcb import parse_lcb

FIX = pathlib.Path(__file__).parent / "fixtures" / "lcb_eval_sample.json"


def test_parse_lcb_one_row_per_problem():
    rows = parse_lcb(json.loads(FIX.read_text()))
    assert len(rows) == 3
    assert all(r["passed"] in (0, 1) and r["item_id"] for r in rows)
```

Ajouter une assertion sur la valeur exacte de `passed` du premier problème, relevée à la main dans la fixture. `test_suite_aider.py` suit le même modèle sur `aider_results_sample/`.

- [ ] **Step 4: Vérifier l'échec, implémenter les parseurs et les classes**

`bench/benchrun/suites/lcb.py` : `parse_lcb` lit la structure réellement observée à l'étape 2 (liste de problèmes avec identifiant et booléen ou `pass@1` par problème ; `passed = 1` si la génération passe tous les tests). `LiveCodeBenchSuite.run(ctx)` lance le conteneur `bench-lcb` avec `subprocess.run([...], check=True)`, puis applique `parse_lcb` à la sortie. Le modèle est désigné par `bench-<model>-<variant>`, et l'URL est `ctx.base_url + "/v1"`. `bench/benchrun/suites/aider.py` : même structure, avec `--keywords` construit depuis `Bench-LLM/sets/aider-subset-60.txt`.

Run: `bench/scripts/test.sh tests/test_suite_lcb.py tests/test_suite_aider.py`
Expected: FAIL avant l'implémentation, PASS après.

- [ ] **Step 5: Figer le sous-ensemble Aider**

Tirer une fois 60 exercices répartis sur les 6 langages (10 par langage, graine 20260923), les écrire dans `Bench-LLM/sets/aider-subset-60.txt`, committer dans Bench-LLM, et reporter l'empreinte SHA-256 dans `bench/SETS.sha256` (public).

- [ ] **Step 6: Commit**

```bash
git add bench/harness/lcb.Dockerfile bench/harness/aider.Dockerfile bench/harness/pins.env bench/benchrun/suites/lcb.py bench/benchrun/suites/aider.py bench/tests/test_suite_lcb.py bench/tests/test_suite_aider.py bench/tests/fixtures/lcb_eval_sample.json bench/tests/fixtures/aider_results_sample bench/SETS.sha256
git commit -m "feat(bench): épreuves LiveCodeBench et Aider Polyglot"
```

### Task 10: BFCL v4 et LiveBench

**Files:**
- Create: `bench/harness/bfcl.Dockerfile`, `bench/harness/livebench.Dockerfile`
- Create: `bench/benchrun/suites/bfcl.py`, `bench/benchrun/suites/livebench.py`
- Create: fixtures réelles `bench/tests/fixtures/bfcl_score_sample/`, `bench/tests/fixtures/livebench_judgment_sample.jsonl`
- Create: `bench/tests/test_suite_bfcl.py`, `bench/tests/test_suite_livebench.py`

**Interfaces:**
- Produces: `BfclSuite(categories=("simple","multiple","multi_turn_base"), handler: str)` et `LiveBenchSuite(categories=("reasoning","math"), release: str)`, avec `name` = `"bfcl"` et `"livebench"`. Fonctions pures `parse_bfcl(score_dir)` et `parse_livebench(judgment_jsonl)`.

- [ ] **Step 1: Épingler et vérifier le mode d'appel natif de BFCL**

Épingler `ShishirPatil/gorilla` (sous-dossier `berkeley-function-call-leaderboard`) et `livebench/livebench`. Lire dans le dépôt épinglé la liste des handlers FC. Pour chaque famille de modèles (Qwen3.x, Ornith, KAT, Nex, Muse, Spark), choisir le handler OpenAI-compatible en mode FC. Si aucun ne convient à une famille, l'écrire dans `bench/harness/BFCL-HANDLERS.md` : ce modèle passe BFCL en mode Prompt, et le tableau le signale.

- [ ] **Step 2: Passe minimale réelle et capture**

```bash
docker run --rm --network host -e REMOTE_OPENAI_BASE_URL=http://localhost:8081/v1 -e REMOTE_OPENAI_API_KEY=x -v /tmp/bfcl:/out bench-bfcl \
  sh -c "bfcl generate --model <handler> --test-category simple --skip-server-setup --run-ids && bfcl evaluate --model <handler> --test-category simple"
```

Utiliser les noms d'options du dépôt épinglé. Réduire la passe à 5 cas par `--run-ids` (ou l'option équivalente), et copier le dossier de scores en fixture. Faire de même pour LiveBench (`run_livebench.py --api-base http://localhost:8081/v1 --bench-name live_bench/reasoning --question-source huggingface`, limité à 3 questions), et copier le jugement en fixture.

- [ ] **Step 3: Tests sur les fixtures réelles**

Même modèle qu'à la tâche 9 : nombre de lignes égal au nombre de cas capturés, `passed` dans {0, 1}, et la valeur exacte du premier cas relevée à la main.

- [ ] **Step 4: Implémenter, faire passer, committer**

Run: `bench/scripts/test.sh tests/test_suite_bfcl.py tests/test_suite_livebench.py`
Expected: PASS

```bash
git add bench/harness/bfcl.Dockerfile bench/harness/livebench.Dockerfile bench/harness/BFCL-HANDLERS.md bench/harness/pins.env bench/benchrun/suites/bfcl.py bench/benchrun/suites/livebench.py bench/tests/test_suite_bfcl.py bench/tests/test_suite_livebench.py bench/tests/fixtures/bfcl_score_sample bench/tests/fixtures/livebench_judgment_sample.jsonl
git commit -m "feat(bench): épreuves BFCL v4 et LiveBench"
```

### Task 11: Long contexte, RULER et NoLiMa

**Files:**
- Create: `bench/harness/ruler.Dockerfile`, `bench/harness/nolima.Dockerfile`
- Create: `bench/benchrun/suites/ruler.py`, `bench/benchrun/suites/nolima.py`
- Create: fixtures réelles, `bench/tests/test_suite_ruler.py`, `bench/tests/test_suite_nolima.py`

**Interfaces:**
- Produces: `RulerSuite(lengths: list[int], tasks: list[str] | None = None, per_task: int = 20)`, `NolimaSuite(lengths=(32768, 131072), per_length: int = 50)`, avec `name` = `"ruler"` et `"nolima"`. Les longueurs supérieures à `cfg.ctx_proven or max_ctx(cfg)` sont sautées et journalisées `skipped`. `max_ctx(cfg: BenchConfig) -> int` lit la valeur de `--ctx-size` dans `cfg.args`.

- [ ] **Step 1: Vérifier que RULER parle à un endpoint OpenAI**

Lire les scripts de configuration de `NVIDIA/RULER` à la version épinglée. S'il n'existe pas de client OpenAI générique, utiliser l'implémentation RULER d'`inspect_evals` (épinglée). Consigner le choix dans `bench/harness/README.md`.

- [ ] **Step 2: Test de `max_ctx` et du saut des longueurs**

```python
from benchrun.suites.ruler import max_ctx, lengths_to_run
from benchrun.config import load_config
import pathlib

CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")


def test_max_ctx_reads_ctx_size():
    assert max_ctx(CFG) == 393216


def test_lengths_above_window_are_skipped():
    run, skipped = lengths_to_run([32768, 131072, 524288], CFG)
    assert run == [32768, 131072] and skipped == [524288]
```

- [ ] **Step 3: Passe minimale réelle, capture, parseurs, tests**

Même méthode qu'aux tâches 9 et 10 : passe réelle à 32k sur 2 tâches × 2 échantillons, capture en fixture, parseur écrit sur la sortie observée.

- [ ] **Step 4: Faire passer et committer**

Run: `bench/scripts/test.sh tests/test_suite_ruler.py tests/test_suite_nolima.py`
Expected: PASS

```bash
git add bench/harness/ruler.Dockerfile bench/harness/nolima.Dockerfile bench/harness/README.md bench/harness/pins.env bench/benchrun/suites/ruler.py bench/benchrun/suites/nolima.py bench/tests/test_suite_ruler.py bench/tests/test_suite_nolima.py bench/tests/fixtures/ruler_* bench/tests/fixtures/nolima_*
git commit -m "feat(bench): épreuves long contexte RULER et NoLiMa"
```

### Task 12: Épreuve maison agentique

**Files:**
- Create: `bench/benchrun/tasks/builder.py`
- Create: `bench/benchrun/suites/agentic.py`
- Create: `bench/harness/agent.Dockerfile`
- Create: `bench/tests/test_task_builder.py`, `bench/tests/test_suite_agentic.py`
- Create: `Bench-LLM/tasks/<id>/task.json` + `repo.tar.gz` (40 tâches)

**Interfaces:**
- Produces:
  - `build_task(repo: pathlib.Path, fix_commit: str, test_cmd: str, out: pathlib.Path) -> pathlib.Path` : écrit `out/<id>/task.json` (`{"id","prompt","test_cmd","fail_before":true}`) et `out/<id>/repo.tar.gz`, qui contient l'arbre du commit parent. Le fichier `.git` y est réinitialisé avec un seul commit, et aucune trace du correctif ne subsiste.
  - `TaskLeakError(Exception)`
  - `AgenticSuite(tasks_dir: pathlib.Path, max_turns: int = 40, timeout_s: int = 1800)`, avec `name = "agentic"`. Par tâche : extraction dans un conteneur `bench-agent`, puis `claude -p "<prompt>" --output-format json --max-turns N --permission-mode bypassPermissions` avec `ANTHROPIC_BASE_URL=<passerelle>`, `ANTHROPIC_API_KEY=dummy` et `CLAUDE_CODE_ATTRIBUTION_HEADER=0`, puis `test_cmd`. `passed = 1` si les tests passent.

- [ ] **Step 1: Tests du constructeur**

`bench/tests/test_task_builder.py` :

```python
import subprocess
import tarfile
import pathlib
import pytest
from benchrun.tasks.builder import build_task, TaskLeakError


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


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
    out = build_task(repo, fix, "python -m pytest -q", tmp_path / "tasks")
    with tarfile.open(out / "repo.tar.gz", "r:*") as tf:
        names = tf.getnames()
        calc = tf.extractfile("calc.py").read().decode()
    assert "return a - b" in calc
    assert not any("ORIG_HEAD" in n or "logs/" in n for n in names)


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
```

- [ ] **Step 2: Vérifier l'échec**

Run: `bench/scripts/test.sh tests/test_task_builder.py`
Expected: FAIL, module absent.

- [ ] **Step 3: Implémenter le constructeur**

`bench/benchrun/tasks/__init__.py` : vide. `bench/benchrun/tasks/builder.py` :

```python
"""Build a private agentic task from a fix commit of one of our repositories.

The agent gets the PARENT tree with a fresh one-commit history: if the fix were
reachable (log, reflog, packs), the agent could read the answer instead of
solving the bug.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shlex
import shutil
import subprocess
import tarfile
import tempfile


class TaskLeakError(Exception):
    pass


def _git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], check=check, capture_output=True, text=True)


def _reinit_history(root: pathlib.Path) -> None:
    shutil.rmtree(root / ".git")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "-c", "user.email=bench@local", "-c", "user.name=bench", "add", "-A")
    _git(root, "-c", "user.email=bench@local", "-c", "user.name=bench", "commit", "-qm", "snapshot")


def _assert_no_leak(root: pathlib.Path, fix_commit: str) -> None:
    if _git(root, "cat-file", "-e", fix_commit, check=False).returncode == 0:
        raise TaskLeakError(f"fix commit {fix_commit} reachable in task tree")
    count = _git(root, "rev-list", "--all", "--count").stdout.strip()
    if count != "1":
        raise TaskLeakError(f"task history has {count} commits, expected 1")


def build_task(repo: pathlib.Path, fix_commit: str, test_cmd: str, out: pathlib.Path) -> pathlib.Path:
    subject = _git(repo, "log", "-1", "--format=%s%n%n%b", fix_commit).stdout.strip()
    parent = _git(repo, "rev-parse", f"{fix_commit}^").stdout.strip()
    task_id = hashlib.sha256(f"{repo.name}:{fix_commit}".encode()).hexdigest()[:12]
    dest = pathlib.Path(out) / task_id
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp) / "repo"
        subprocess.run(["git", "clone", "-q", "--no-local", str(repo), str(work)], check=True)
        _git(work, "checkout", "-q", parent)
        tests_fixed = _git(repo, "diff", "--name-only", parent, fix_commit).stdout.split()
        for path in tests_fixed:
            if "test" in path.lower():
                blob = _git(repo, "show", f"{fix_commit}:{path}", check=False)
                if blob.returncode == 0:
                    (work / path).parent.mkdir(parents=True, exist_ok=True)
                    (work / path).write_text(blob.stdout)
        if subprocess.run(shlex.split(test_cmd), cwd=work, capture_output=True).returncode == 0:
            raise ValueError("tests must fail before the fix, or the task measures nothing")
        _reinit_history(work)
        _assert_no_leak(work, fix_commit)
        with tarfile.open(dest / "repo.tar.gz", "w:gz") as tf:
            for p in sorted(work.rglob("*")):
                tf.add(p, arcname=str(p.relative_to(work)), recursive=False)
    prompt = ("A test in this repository fails. Find the cause and fix the code so the whole "
              f"test suite passes with: {test_cmd}. Do not modify tests. Context from the bug "
              f"report: {subject.splitlines()[0]}")
    (dest / "task.json").write_text(json.dumps(
        {"id": task_id, "prompt": prompt, "test_cmd": test_cmd, "fail_before": True}, indent=2))
    return dest
```


- [ ] **Step 4: Faire passer**

Run: `bench/scripts/test.sh tests/test_task_builder.py`
Expected: `3 passed`

- [ ] **Step 5: Image agent et suite agentique**

`bench/harness/agent.Dockerfile` : image Node 22 + Python 3.12 + outils de test des dépôts ciblés, avec `npm install -g @anthropic-ai/claude-code@<version épinglée>`. `bench/benchrun/suites/agentic.py` : pour chaque tâche, extraction dans un volume temporaire, lancement du conteneur sans réseau sortant sauf vers la passerelle (réseau Docker dédié), `claude -p`, puis `test_cmd`. `detail` enregistre les tours, la durée et le code de sortie. Test unitaire : `parse_claude_json(stdout) -> dict`, écrit sur une sortie réelle capturée lors d'une passe sur une tâche jouet.

- [ ] **Step 6: Constituer les 40 tâches (dans Bench-LLM)**

Candidats : commits `fix:` accompagnés de tests, dans les dépôts de `~/git_projects` qui ont une suite de tests lançable en conteneur. Lire chaque candidat avant de le retenir. Viser 40 tâches réparties sur au moins 4 langages. Écrire la liste des commits retenus dans `Bench-LLM/tasks/SOURCES.md` (privé). Vérifier chaque tâche avec `build_task`, qui échoue si les tests passent déjà.

- [ ] **Step 7: Commits (public puis privé)**

```bash
git add bench/benchrun/tasks bench/benchrun/suites/agentic.py bench/harness/agent.Dockerfile bench/tests/test_task_builder.py bench/tests/test_suite_agentic.py bench/tests/fixtures/claude_*
git commit -m "feat(bench): épreuve maison agentique"
cd ~/git_projects/Tools/Bench-LLM && git add tasks && git commit -m "feat: quarante tâches agentiques"
```

Reporter l'empreinte de l'ensemble des tâches dans `bench/SETS.sha256` (public) et committer.

### Task 13: Débit et conformité de l'embedder

**Files:**
- Create: `bench/benchrun/suites/speed.py`, `bench/benchrun/embed_check.py`
- Create: `bench/tests/test_suite_speed.py`, `bench/tests/test_embed_check.py`

**Interfaces:**
- Produces:
  - `SpeedSuite(prompt_tokens=(512, 8192, 32768), gen_tokens=256)`, `name = "speed"`. Chaque item a pour `item_id` `pp<n>`, `passed = 1`, et `detail = {"decode_tps","prefill_tps","draft_n","draft_n_accepted","ttft_s"}` relus dans la passerelle.
  - `embed_check(base_url: str) -> dict` : `{"prefix_gain": float, "matryoshka": {768: float, 512: float, 256: float}, "max_ctx_ok": int}`.

- [ ] **Step 1: Test de l'agrégat de débit (médiane sur 3 passes)**

```python
from benchrun.suites.speed import median_row


def test_median_row():
    rows = [{"decode_tps": 100}, {"decode_tps": 120}, {"decode_tps": 90}]
    assert median_row(rows, "decode_tps") == 100
```

- [ ] **Step 2: Implémenter la suite de débit à partir de `vitesse.ps1`**

Reprendre la logique de `vitesse.ps1` : `/v1/chat/completions`, lecture de `timings` et des champs `draft_n` et `draft_n_accepted`. Le prompt de remplissage est tiré du code source réel, comme dans `aiguille.ps1`.

- [ ] **Step 3: Conformité de l'embedder**

Test sur 20 paires question/document du jeu privé `Bench-LLM/sets/embed-pairs.jsonl`. On compare le rappel avec et sans les préfixes `search_query`/`search_document`, la troncature Matryoshka (768, 512, 256) et un document de 8 192 puis de 32 768 jetons. `max_ctx_ok` est la plus grande longueur qui renvoie un vecteur sans erreur. Fixture réelle et test comme aux tâches 9 à 11.

- [ ] **Step 4: Faire passer et committer**

```bash
bench/scripts/test.sh tests/test_suite_speed.py tests/test_embed_check.py
git add bench/benchrun/suites/speed.py bench/benchrun/embed_check.py bench/tests/test_suite_speed.py bench/tests/test_embed_check.py bench/tests/fixtures/embed_*
git commit -m "feat(bench): débit et conformité de l'embedder"
```

## Partie C. Fiabilité, configurations, pilote, campagne

### Task 14: Phase 0, fiabilité des stations

**Files:**
- Create: `bench/scripts/audit-templates.py`
- Create: `bench/scripts/repro-29295.py`
- Create: `docs/phase0-2026-09.md` (constats)
- Modify: `llm-ctl.ps1` (nouveau build dans les variables d'exe, sans toucher aux affectations de `$builds`)

**Interfaces:**
- Produces: `D:\LLM-Setup\llama-cpp-<tag>` (build postérieur au 22/09/2026) sur la 99, et l'équivalent sur la 97 ; rapport `docs/phase0-2026-09.md`, qui liste pour chaque profil : template servi, présence de `<tool_call><function=` sans retour à la ligne, résultat du contrôle de fumée.

- [ ] **Step 1: Nouveau build llama.cpp**

Relever la dernière release `ggml-org/llama.cpp` postérieure au 22/09/2026 (`gh api repos/ggml-org/llama.cpp/releases/latest --jq .tag_name`). Vérifier que le commit `bfd73a876` en est ancêtre : `gh api repos/ggml-org/llama.cpp/compare/bfd73a876...<tag> --jq .status`. Attendu : `ahead` ou `identical`. Poser le binaire CUDA officiel à plat dans `D:\LLM-Setup\llama-cpp-<tag>` (méthode de `docs/building-llama-cpp.md`). Sur la 97, vérifier les releases BeeLlama. Si aucune ne dérive d'une base postérieure au 22/09, poser le même binaire upstream à côté de BeeLlama. Contrôle : `llama-server.exe --version` affiche le tag.

- [ ] **Step 2: Audit des templates**

`bench/scripts/audit-templates.py` : pour chaque profil, lancer l'action `bench` avec `--ctx-size 4096`, lire `/props` (`chat_template`), puis écrire une ligne avec : profil, présence de `<tool_call>`, `<function=` et `<parameter=`, et présence du motif sans retour à la ligne `'<tool_call><function=' ~`. Le résultat va dans `docs/phase0-2026-09.md`.

- [ ] **Step 3: Reproduction de #29295**

`bench/scripts/repro-29295.py` : sur `qwen`, même requête avec un outil et `tool_choice: "required"` et un cas limite (question à laquelle on peut répondre sans outil), 5 passes cache froid (redémarrage entre deux passes) puis 5 passes cache chaud. On note le nombre de réponses sans `tool_calls`. Si le chaud échoue plus souvent que le froid, BFCL et l'épreuve agentique tournent avec `"cache_prompt": false`, imposé par la passerelle (ajouter la clé au `sampling` de chaque configuration, avec `source: "issue llama.cpp #29295"`). Le constat va dans `docs/phase0-2026-09.md`.

- [ ] **Step 4: Côté client, poser l'en-tête d'attribution**

Les lanceurs `~/.claude/bin/*` sont hors du dépôt. Demander l'accord de Guillaume par AskUserQuestion avant d'y écrire. Après accord : ajouter `export CLAUDE_CODE_ATTRIBUTION_HEADER=0` à chaque lanceur de modèle local. Contrôle : une session courte, deux tours, puis dans `-Action logs`, la ligne `restored context checkpoint` au second tour.

- [ ] **Step 5: Nettoyer l'instance périmée de la 97 et arrêter ComfyUI sur la 99**

```bash
ssh <cible-97> 'pwsh -NoProfile -File D:\LLM-Setup\llm-ctl.ps1 -Action stop'
ssh <cible-97> 'pwsh -NoProfile -File D:\LLM-Setup\llm-ctl.ps1 -Action status'
```

Expected: `NOT_RUNNING`.

Pour ComfyUI : relever son mode de lancement (service, tâche planifiée ou processus manuel) avant de l'arrêter, pour pouvoir le relancer à l'identique en fin de campagne. Arrêter, puis vérifier que `nvidia-smi` affiche moins de 1 Go. Consigner les deux gestes dans les fiches de vie via /claude-memory.

- [ ] **Step 6: Commit**

```bash
git add bench/scripts/audit-templates.py bench/scripts/repro-29295.py docs/phase0-2026-09.md llm-ctl.ps1 llm-ctl-16gb.ps1
git commit -m "chore(bench): phase 0, build récent, audit des templates, bug 29295"
```

### Task 15: Les 42 configurations

**Files:**
- Create: `bench/configs/99/<model>/R{1,2,3}.yaml` pour muse, qwen, qwenu, qwenf, qwent, tiel, ornith, kat, nex, spark, bonsai, bonsai2
- Create: `bench/configs/97/{tiel,qwen36}/R{1,2,3}.yaml`
- Create: `bench/tests/test_all_configs.py`
- Create: `bench/scripts/fetch-models.sh` (téléchargements de la section 5.3 de la spec)

**Interfaces:**
- Consumes: `load_config` (3), section 5 de la spec (contenu de chaque configuration).
- Produces: 42 fichiers valides et un contrôle qui les charge tous.

- [ ] **Step 1: Test de complétude**

```python
import pathlib
from benchrun.config import load_config

ROOT = pathlib.Path(__file__).parents[1] / "configs"
EXPECTED = {
    "99": ["muse", "qwen", "qwenu", "qwenf", "qwent", "tiel", "ornith", "kat", "nex", "spark", "bonsai", "bonsai2"],
    "97": ["tiel", "qwen36"],
}


def test_every_model_has_three_valid_configs():
    for machine, models in EXPECTED.items():
        for m in models:
            for v in ("R1", "R2", "R3"):
                cfg = load_config(ROOT / machine / m / f"{v}.yaml")
                assert (cfg.machine, cfg.model, cfg.variant) == (machine, m, v)


def test_r1_cites_the_model_card():
    for p in ROOT.glob("*/*/R1.yaml"):
        cfg = load_config(p)
        assert any("huggingface.co" in s for s in cfg.sources.values()), p
```

- [ ] **Step 2: Vérifier l'échec**

Run: `bench/scripts/test.sh tests/test_all_configs.py`
Expected: FAIL, fichiers absents.

- [ ] **Step 3: Écrire les configurations**

Partir, pour chaque modèle, de la branche du profil dans `llm-ctl.ps1` (ou `llm-ctl-16gb.ps1`) : binaire tiré de `$builds`, arguments du profil sans les drapeaux de sampling. Appliquer ensuite exactement les écarts de la section 5 de la spec. Chaque réglage de `sampling` et de `chat_template_kwargs` porte sa source, et `label` résume la configuration en quelques mots. Avant d'écrire une configuration, relire la fiche HF qu'elle cite (règle « rien de mémoire ») et reporter la date de lecture dans `sources`. Exemple complet pour `bench/configs/99/tiel/R1.yaml` :

```yaml
machine: "99"
model: tiel
variant: R1
label: "fiche éditeur, T0.6"
exe: 'D:\LLM-Setup\llama-cpp-b10826\llama-server.exe'
workdir: 'D:\LLM-Setup\llama-cpp-b10826'
cudabin: null
args: [<arguments du profil tiel de llm-ctl.ps1, sans --temp/--top-p/--top-k/--min-p>]
env: {}
sampling: {temperature: 0.6, top_p: 0.95, top_k: 20, min_p: 0.0}
chat_template_kwargs: {}
sources:
  temperature: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B (lu le <date>)"
  top_p: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B (lu le <date>)"
  top_k: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B (lu le <date>)"
  min_p: "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B (lu le <date>)"
ctx_proven: 393216
```

Les chevrons de cet exemple désignent des valeurs à recopier depuis la source nommée, pas des trous : l'implémenteur les remplit en lisant le fichier désigné.

- [ ] **Step 4: Télécharger les fichiers nouveaux**

`bench/scripts/fetch-models.sh` télécharge sur la station concernée (via `ssh` et `huggingface-cli` déjà présent, sinon `curl`) les fichiers de la section 5.3 de la spec, vérifie le SHA-256 publié par le dépôt HF, et refuse de continuer en cas d'écart. KAT APEX-MTP : lire d'abord la fiche (accès 401 le 23/09). Si le gain annoncé n'est pas documenté, la configuration kat R2 passe sur le fichier existant, avec le template R3 comme second pari, et la spec est mise à jour.

- [ ] **Step 5: Faire passer, puis contrôle de fumée de chaque configuration**

Run: `bench/scripts/test.sh tests/test_all_configs.py`
Expected: `2 passed`

Puis, pour chaque configuration : lancement par `Station.start`, une requête avec un appel d'outil réel, et `draft_n` présent si la configuration pose une spéculation. Une configuration qui échoue est écartée, avec son motif dans `docs/phase0-2026-09.md`. On ne la corrige pas en cours de campagne.

- [ ] **Step 6: Commit**

```bash
git add bench/configs bench/tests/test_all_configs.py bench/scripts/fetch-models.sh docs/phase0-2026-09.md
git commit -m "feat(bench): trois configurations par modèle"
```

### Task 16: Point d'entrée, pilote et dimensionnement

**Files:**
- Create: `bench/benchrun/__main__.py`
- Create: `bench/harness/compose.yaml`
- Create: `bench/.env.example`
- Create: `docs/pilote-2026-09.md`

**Interfaces:**
- Produces: `python -m benchrun run --machine 99|97 --configs <glob> --suites lcb,aider,bfcl,livebench,ruler,nolima,agentic,speed --reps 3 --out $BENCH_PRIVATE/runs/<campagne>` ; `python -m benchrun pilot --machine 99|97`.

- [ ] **Step 1: `.env.example` et compose**

`bench/.env.example` :

```
STATION_99_SSH=user@host
STATION_99_URL=http://host:8080
STATION_97_SSH=user@host
STATION_97_URL=http://host:8080
BENCH_PRIVATE=/private
```

`bench/harness/compose.yaml` : deux services passerelle (`gateway-99` sur 8081, `gateway-97` sur 8082, image `benchrun:dev`, journal dans `${BENCH_PRIVATE}/runs`) et deux services orchestrateurs (`runner-99`, `runner-97`), avec `~/.ssh` monté en lecture seule, `BENCH_PRIVATE` monté et la socket Docker montée (pour lancer les conteneurs de harnais).

- [ ] **Step 2: Point d'entrée**

`bench/benchrun/__main__.py` : lecture des arguments, construction de `Station` depuis `.env`, chargement des configurations par glob, instanciation des suites demandées avec leurs tailles de campagne (spec, section phase 1), puis `Campaign.run`. Le mode `pilot` utilise les tailles minimales de la spec (phase 2) sur `configs/<machine>/tiel/R1.yaml`, et écrit la durée de chargement et la durée de chaque épreuve dans `docs/pilote-2026-09.md`.

- [ ] **Step 3: Lancer le pilote sur les deux machines en parallèle**

```bash
cd bench/harness && docker compose run --rm runner-99 python -m benchrun pilot --machine 99 &
docker compose run --rm runner-97 python -m benchrun pilot --machine 97 &
wait
```

Expected: `docs/pilote-2026-09.md` rempli pour les deux machines, sans erreur de harnais.

- [ ] **Step 4: Dimensionner**

Durée projetée = (durée par item de chaque épreuve × taille de campagne × 3 passes + chargement) × nombre de configurations de la machine. L'écrire dans `docs/pilote-2026-09.md` avec l'écart détectable (`min_detectable_gap`) de chaque épreuve. Si la durée n'est pas acceptable, demander à Guillaume, par AskUserQuestion, quelles épreuves réduire. Chaque option doit indiquer l'écart détectable qui en résulte.

- [ ] **Step 5: Commit**

```bash
git add bench/benchrun/__main__.py bench/harness/compose.yaml bench/.env.example docs/pilote-2026-09.md
git commit -m "feat(bench): point d'entrée, pilote et dimensionnement"
```

### Task 17: Campagne

**Files:**
- Aucun fichier du dépôt (arbre figé). Écrit uniquement dans `Bench-LLM/runs/2026-09-campagne/`.

- [ ] **Step 1: Figer l'arbre**

```bash
git -C ~/git_projects/Tools/LLM/llm-station-cuda status --porcelain
```

Expected : sortie vide. Noter le commit dans `Bench-LLM/runs/2026-09-campagne/TREE`. Vérifier que les deux `llm-ctl.ps1` déployés ont le même SHA-256 que le dépôt.

- [ ] **Step 2: Lancer les deux campagnes en parallèle, en tâche de fond**

```bash
cd bench/harness
docker compose run -d --name camp-99 runner-99 python -m benchrun run --machine 99 --configs 'configs/99/*/*.yaml' --suites lcb,aider,bfcl,livebench,ruler,nolima,agentic,speed --reps 3 --out /private/runs/2026-09-campagne
docker compose run -d --name camp-97 runner-97 python -m benchrun run --machine 97 --configs 'configs/97/*/*.yaml' --suites lcb,aider,bfcl,livebench,ruler,nolima,agentic,speed --reps 3 --out /private/runs/2026-09-campagne
```

Surveiller avec Monitor sur la fin des deux conteneurs, sans sonder en boucle. En cas de coupure, relancer la même commande : la reprise saute ce qui est marqué `.done`.

- [ ] **Step 3: Contrôle d'intégrité**

Chaque cellule attendue a ses 3 marqueurs `.done` par épreuve, sauf les longueurs sautées, journalisées. Les `meta.json` marqués `spill` sont listés. Tout manque est relancé avant l'analyse.

- [ ] **Step 4: Relancer ComfyUI sur la 99**

Relance à l'identique du mode relevé à la tâche 14. Contrôle : le port 8188 répond. Consigner le geste dans la fiche de vie.

- [ ] **Step 5: Commit privé**

```bash
cd ~/git_projects/Tools/Bench-LLM && git add runs/2026-09-campagne && git commit -m "feat: campagne de septembre 2026"
```

## Partie D. Rapport, publication, convention, doc

### Task 18: Agrégateur et tableau publiable

**Files:**
- Create: `bench/report/aggregate.py`
- Create: `bench/tests/test_aggregate.py`
- Create: `bench/report/RESULTS-2026-09.md`, `bench/report/results-2026-09.csv`

**Interfaces:**
- Consumes: arborescence de résultats (8), `stats` (7).
- Produces: `aggregate(runs_root: pathlib.Path) -> list[dict]`, une ligne par machine × modèle × configuration, avec les colonnes de la section 6 de la spec ; `render_markdown(rows) -> str` ; `render_csv(rows) -> str`.

- [ ] **Step 1: Tests sur une arborescence minimale**

```python
import json
import pathlib
from report.aggregate import aggregate, render_markdown


def make_tree(root: pathlib.Path):
    cell = root / "99" / "tiel" / "R1"
    for suite in ("lcb", "speed"):
        d = cell / suite
        d.mkdir(parents=True)
        for k in (1, 2, 3):
            rows = [{"item_id": f"i{j}", "passed": int(j % 2 == 0), "detail": {"decode_tps": 100 + k}} for j in range(10)]
            (d / f"rep{k}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            (d / f"rep{k}.done").write_text("")
    (cell / "meta.json").write_text(json.dumps({"vram": {"used_mb": 29500, "shared_mb": 0}, "spill": False,
                                                "config": {"label": "fiche éditeur", "args": ["--ctx-size", "393216"]}}))


def test_aggregate_one_row_with_ci(tmp_path):
    make_tree(tmp_path)
    rows = aggregate(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["model"] == "tiel" and r["variant"] == "R1"
    assert abs(r["lcb"]["mean"] - 0.5) < 1e-9 and r["lcb"]["lo"] <= 0.5 <= r["lcb"]["hi"]


def test_markdown_has_no_internal_address(tmp_path):
    make_tree(tmp_path)
    md = render_markdown(aggregate(tmp_path))
    assert "192.168." not in md and "PC-GUILLAUME" not in md
```

- [ ] **Step 2: Vérifier l'échec, implémenter, faire passer**

`bench/report/aggregate.py` parcourt `<machine>/<model>/<variant>`, applique `item_scores` puis `bootstrap_ci` à chaque épreuve de qualité, prend la médiane pour `speed`, et lit `meta.json` (VRAM, spill, label). La colonne GPU vient d'une table fixe `{"99": "RTX 5090 32 Go", "97": "RTX 4080 SUPER 16 Go"}`, jamais du nom d'hôte. La colonne « contexte prouvé » vient de RULER : c'est la plus grande longueur dont le score reste au-dessus de 0,8 × le score à 32k. Écrire ce critère dans l'en-tête du tableau.

Run: `bench/scripts/test.sh tests/test_aggregate.py`
Expected: `2 passed`

- [ ] **Step 3: Générer le tableau réel et les comparaisons**

Produire `RESULTS-2026-09.md` et le CSV. Pour chaque modèle, ajouter la comparaison appariée R1/R2/R3 (`paired_diff_ci`, `mcnemar_exact`) et l'écart détectable de chaque épreuve. L'épreuve maison est présentée comme « épreuve interne, construite sur notre propre usage », sans aucun détail sur les tâches. Pour chaque modèle, contrôler la licence (fiche HF) avant publication de sa ligne.

- [ ] **Step 4: Contrôles avant commit**

```bash
grep -nE "192\.168\.|PC-GUILLAUME|guill@" bench/report/*; echo "code=$?"
grep -rlE $'\xe2\x80\x94|\xe2\x80\x93' bench/report; echo "code=$?"
```

Expected: `code=1` deux fois.

- [ ] **Step 5: Commit**

```bash
git add bench/report bench/tests/test_aggregate.py
git commit -m "feat(bench): agrégateur et tableau de septembre 2026"
```

### Task 19: Convention d'entrée d'un nouveau modèle

**Files:**
- Create: `docs/convention-nouveau-modele.md`
- Create: `bench/configs/_template/R1.yaml`, `R2.yaml`, `R3.yaml`
- Create: `models/_template/README.md`
- Create: `bench/scripts/new-model.sh`

**Interfaces:**
- Produces: `bench/scripts/new-model.sh <machine> <model>` crée `bench/configs/<machine>/<model>/R{1,2,3}.yaml` depuis le gabarit, puis `models/<model>/README.md`. Il échoue si le dossier existe déjà.

- [ ] **Step 1: Test du script**

`bench/tests/test_new_model.sh` (lancé dans l'image) : créer un modèle fictif dans un clone temporaire, vérifier la présence des 4 fichiers, relancer, puis vérifier le code de sortie non nul.

- [ ] **Step 2: Rédiger la convention (doc-copywriter, anti-ai-patterns)**

Contenu fixé par la section 8 de la spec : fiche d'identité sourcée, trois configurations, porte d'entrée (template lu, appel d'outil réel, `draft_n`, rappel à la fenêtre servie), banc standard en 3 passes, ligne au tableau, et règle de mise en service (battre le modèle en place au-delà de la marge, sur son rôle).

- [ ] **Step 3: Faire passer et committer**

```bash
git add docs/convention-nouveau-modele.md bench/configs/_template models/_template bench/scripts/new-model.sh bench/tests/test_new_model.sh
git commit -m "docs(bench): convention d'entrée d'un nouveau modèle"
```

### Task 20: Nettoyage du dépôt et doc

**Files:**
- Delete: `bench/banc.ps1`, `bench/banc-tous.ps1`, `bench/banc-sampling.ps1`, `bench/quality.ps1`, `bench/speculation-bonsai2.ps1` (conclusions archivées d'abord dans `docs/historique-bancs.md`)
- Keep: `bench/vitesse.ps1`, `bench/aiguille.ps1`, `bench/banc-inedit.ps1`, `bench/comparer-inedit.ps1`, `bench/tools/*.py` (liés depuis la nouvelle doc)
- Modify: `README.md` (sous-dépôt et racine), `docs/INDEX.md`, `docs/quel-modele-pour-quel-usage.md`, `models/*/README.md` (chiffres MMLU/GSM8K retirés ou marqués caducs)
- Create: `docs/banc.md`
- Create: wiki `$WIKI_PATH/content/docs/llm/banc.mdx` (chemin exact relevé dans le wiki)

- [ ] **Step 1: Archiver avant de retirer**

Écrire `docs/historique-bancs.md` : ce que chaque ancien script mesurait, pourquoi ses chiffres ne sont plus retenus (section 2.1 de la spec) et le commit où il est lisible. Seulement ensuite, retirer les scripts avec `git rm`.

- [ ] **Step 2: Réécrire la doc (doc-copywriter), deux cibles**

`docs/banc.md` : méthode, épreuves, statistique, séparation public/privé, lancement d'une campagne, lecture du tableau. Wiki : même contenu, pour quelqu'un qui reprend le projet. Corriger ou retirer dans la même passe tout passage devenu faux (README, INDEX, fiches modèles).

- [ ] **Step 3: Contrôles**

```bash
grep -rnE "MMLU|GSM8K" README.md docs models | grep -v -i "caduc\|historique"; echo "code=$?"
grep -rlE $'\xe2\x80\x94|\xe2\x80\x93' README.md docs models bench; echo "code=$?"
bash ../scripts/security-scan.sh
```

Expected: `code=1` deux fois ; scan de sécurité propre.

- [ ] **Step 4: Revue indépendante et commit**

Lancer l'agent `atomic-review` (Agent, `subagent_type: atomic-review`, `model: sonnet`) sur la branche. Si le périmètre dépasse 40 fichiers ou 25 commits, signaler la dépense à Guillaume avant. Corriger ses constats vérifiés, passer /simplify sur les fichiers modifiés, puis :

```bash
git add -A && git commit -m "docs(bench): nouvelle doc du banc, retrait des anciens scripts"
```

Committer le wiki en local. Pas de push sans accord.

### Task 21: Mémoire et mise en service

- [ ] **Step 1: Mémoire**

Via /claude-memory : fiche de référence sur le banc (méthode, emplacement des jeux privés, commandes), constats de la phase 0 (#29257 et #29295, templates), et une entrée dans les fiches de vie des deux machines.

- [ ] **Step 2: Arbitrage de mise en service**

Un seul appel AskUserQuestion, avec un arbitrage par modèle dont une configuration R2 ou R3 bat R1 et la configuration en service au-delà de la marge. Chaque option indique l'écart mesuré et son intervalle. Rien n'est modifié dans `llm-ctl.ps1` avant la réponse.

- [ ] **Step 3: Publication**

Le texte d'accompagnement du tableau (LinkedIn ou autre) est rédigé dans un fichier `.md`, passé par anti-ai-patterns et linkedin-writing, puis remis à Guillaume. Il n'est publié par personne d'autre que lui.
