"""Entry point: campaign runner and the mini/medium/large bench levels
(2026-09-24, replaces Task 16's "pilot" subcommand entirely).

Two subcommands:
- "run" builds every requested suite at the campaign size (spec, phase 1),
  loads every config matching --configs and hands them to Campaign.run. The
  Full campaign always uses this subcommand; its argv, suite sizes and
  output paths are unchanged by everything below (see campaign_suites).
- "bench" runs one of three fixed levels (--preset mini|medium|large) on
  every R1/R2/R3 configuration of the requested models (one machine, one
  pass, reps 1): mini draws one random question per suite (a fresh,
  replayable seed, see mini_suites), medium and large take a fixed, larger
  count per suite, deterministically (see DETERMINISTIC_PRESET_SIZES). Each
  level writes under its own DIR/<preset>/<machine> subtree, so mini,
  medium, large and a Full campaign's own --out never collide. mini also
  writes seed.txt (the seed drawn, replayable with --seed) and rapport.md
  (benchrun.report.build_mini_report) next to it.

Station and the gateway URL both come from an environment file: this
repository's write hook refuses any file named ".env*" (see bench/stations.example),
so the file lives outside the convention and is named on the command line
(--env) or through BENCH_ENV. Real values may also arrive as process
environment variables (compose.yaml sets them from the same file via
--env-file): those take precedence over the file, so a value docker compose
already substituted is never second-guessed by a stale copy on disk.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import os
import pathlib
import random
import tempfile

from . import profiles as profiles_cmd
from .config import load_config
from .report import build_mini_report
from .runner import Campaign
from .station import Station
from .suites.agentic import AgenticSuite
from .suites.aider import AiderSuite
from .suites.bfcl import DEFAULT_CATEGORIES as BFCL_DEFAULT_CATEGORIES
from .suites.bfcl import BfclSuite
from .suites.lcb import LiveCodeBenchSuite
from .suites.livebench import LiveBenchSuite
from .suites.longbench import LongBenchV2Suite
from .suites.ruler import DEFAULT_TASKS as RULER_DEFAULT_TASKS
from .suites.ruler import RulerSuite
from .suites.speed import SpeedSuite

# llm-ctl.ps1's fixed path on both stations (parallel-rules.md, Task 4/6/8).
CTL_PATH = r"D:\LLM-Setup\llm-ctl.ps1"
MACHINES = ("99", "97")
# The repo root's own bench/ directory, host or container alike: the runner
# and gateway containers only ever mount bench/ at /bench (Ruling J), never
# the whole repo, so this entry point (bench/benchrun/__main__.py) can only
# reach configs/ (a bench/ sibling of benchrun/) this way, never docs/ (a
# repo-root sibling of bench/, outside the mount).
BENCH_ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_env_file(path: str) -> dict:
    values: dict[str, str] = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def resolve_env(env_arg: str | None) -> dict:
    """File values first, then the real process environment on top: a value
    docker compose already injected (from the same file, via --env-file)
    always wins over a second, possibly stale, read of the file itself."""
    path = env_arg or os.environ.get("BENCH_ENV")
    merged = load_env_file(path) if path else {}
    merged.update(os.environ)
    return merged


def build_station(machine: str, env: dict) -> Station:
    ssh_key, url_key = f"STATION_{machine}_SSH", f"STATION_{machine}_URL"
    missing = [k for k in (ssh_key, url_key, "BENCH_PRIVATE") if not env.get(k)]
    if missing:
        raise SystemExit(
            f"missing environment values {missing}: pass --env <file> or set BENCH_ENV "
            "(see bench/stations.example)"
        )
    return Station(env[ssh_key], env[url_key], CTL_PATH)


def gateway_url(machine: str) -> str:
    # Ruling Z: gateway and runner both sit on the external network bench-net
    # (harness/compose.yaml), reachable from the runner and from the harness
    # containers a suite spawns alike by the gateway's compose service name.
    return f"http://gateway-{machine}:8081"


def journal_path(private_root: pathlib.Path, machine: str) -> pathlib.Path:
    return private_root / "runs" / f"journal-{machine}.jsonl"


# Campaign suite sizes (spec, phase 1 table). ruler's third target,
# "maximum servi", is not a fixed number: a served window narrower than
# 131072 already makes RulerSuite skip that length on its own
# (benchrun.suites.lengths_to_run), so the two nominal targets below cover
# it without a third, per-config value this entry point cannot express in one
# shared suite list.
def campaign_suites(names: list[str], private_root: pathlib.Path, machine: str) -> list:
    journal = journal_path(private_root, machine)
    factories = {
        "lcb": lambda: LiveCodeBenchSuite(n_problems=100),
        "aider": lambda: AiderSuite(),
        "bfcl": lambda: BfclSuite(),
        "livebench": lambda: LiveBenchSuite(),
        "ruler": lambda: RulerSuite(lengths=[32768, 131072]),
        "longbench_v2": lambda: LongBenchV2Suite(),
        "agentic": lambda: AgenticSuite(tasks_dir=private_root / "tasks"),
        "speed": lambda: SpeedSuite(journal_path=journal),
    }
    unknown = [n for n in names if n not in factories]
    if unknown:
        raise SystemExit(f"unknown suite(s) {unknown}, known: {sorted(factories)}")
    return [factories[n]() for n in names]


def _first_n_aider_exercises_file(private_root: pathlib.Path, n: int) -> str:
    """First n lines of the frozen 60-exercise subset, written to a fresh
    temp file. Returned as an ABSOLUTE path string: AiderSuite.run() joins
    private_root / exercises_file with pathlib's own "/" operator, which
    discards the left side when the right side is already absolute, so this
    works regardless of where private_root points. Used by the medium and
    large bench levels (deterministic, fixed seed as far as this file goes:
    there is nothing to seed, the same lines every time)."""
    full = (private_root / "sets" / "aider-subset-60.txt").read_text(encoding="utf-8").splitlines()
    keep = [line for line in full if line.strip()][:n]
    return _write_exercises_file(keep, prefix="bench-aider-")


def _random_aider_exercises_file(private_root: pathlib.Path, n: int, rng: random.Random) -> str:
    """Same file shape as _first_n_aider_exercises_file, n lines drawn at
    random from the full subset instead of the first n: used by the mini
    bench level, whose rng is seeded once per launch (mini_suites) so every
    profile of every model in that launch sees the same draw."""
    full = (private_root / "sets" / "aider-subset-60.txt").read_text(encoding="utf-8").splitlines()
    lines = [line for line in full if line.strip()]
    keep = rng.sample(lines, min(n, len(lines)))
    return _write_exercises_file(keep, prefix="mini-aider-")


def _write_exercises_file(keep: list[str], prefix: str) -> str:
    fh = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, prefix=prefix)
    fh.write("\n".join(keep) + "\n")
    fh.close()
    return fh.name


def _first_n_agentic_tasks_dir(private_root: pathlib.Path, n: int) -> pathlib.Path:
    """A temp directory holding symlinks to the first n task directories
    (sorted by id, deterministic), so AgenticSuite.run's own directory scan
    (sorted iterdir over tasks_dir) sees exactly n tasks without this entry
    point needing its own size knob on AgenticSuite itself. Used by the
    medium and large bench levels."""
    all_tasks = _sorted_agentic_tasks(private_root)
    return _symlink_tasks_dir(all_tasks[:n], prefix="bench-agentic-")


def _random_agentic_tasks_dir(private_root: pathlib.Path, n: int, rng: random.Random) -> pathlib.Path:
    """Same shape as _first_n_agentic_tasks_dir, n tasks drawn at random
    instead of the first n: used by the mini bench level."""
    all_tasks = _sorted_agentic_tasks(private_root)
    return _symlink_tasks_dir(rng.sample(all_tasks, min(n, len(all_tasks))), prefix="mini-agentic-")


def _sorted_agentic_tasks(private_root: pathlib.Path) -> list[pathlib.Path]:
    tasks_dir = private_root / "tasks"
    return sorted(p for p in tasks_dir.iterdir() if (p / "task.json").exists())


def _symlink_tasks_dir(chosen: list[pathlib.Path], prefix: str) -> pathlib.Path:
    out_dir = pathlib.Path(tempfile.mkdtemp(prefix=prefix))
    for task_dir in chosen:
        (out_dir / task_dir.name).symlink_to(task_dir, target_is_directory=True)
    return out_dir


# mini/medium/large's own suite sizes (Guillaume, 2026-09-24). Medium and
# large are deterministic (first N, or the harness's own fixed selection
# seed): this table is their entire sizing, nothing else varies between the
# two. mini is built separately by mini_suites, below: every count there is
# 1, drawn at random from a shared per-launch seed, which this table cannot
# express.
DETERMINISTIC_PRESET_SIZES = {
    "medium": {
        "lcb_n_problems": 10, "aider_n": 5, "bfcl_limit": 5, "livebench_limit": 5,
        "ruler_lengths": [32768], "ruler_per_task": 1,
        "longbench_lengths": [32768], "longbench_samples_per_length": 2,
        "agentic_n": 2,
    },
    "large": {
        "lcb_n_problems": 30, "aider_n": 20, "bfcl_limit": 30, "livebench_limit": 25,
        "ruler_lengths": [32768, 131072], "ruler_per_task": 3,
        "longbench_lengths": [32768, 131072], "longbench_samples_per_length": 5,
        "agentic_n": 8,
    },
}
# Every response a mini suite grades is capped here (sampling_override, the
# same mechanism SpeedSuite already relies on): keeps a mini pass close to
# its target duration per model regardless of a model card's own max_tokens.
# Per-suite, not one flat number (Guillaume, 2026-09-25): measured live on a
# real mini launch that almost every LiveCodeBench answer hit the 2048 floor
# and scored 0 %, a real reasoning solution routinely needs more room than a
# short factual answer does. lcb/livebench/aider get 16384; every other
# graded suite (bfcl, ruler, longbench_v2, agentic) keeps the original 2048
# default (short, close-ended answers: a tool call, a needle, a multiple-
# choice letter, a code diff already bounded by the task itself).
MINI_MAX_TOKENS_DEFAULT = 2048
MINI_MAX_TOKENS_BY_SUITE = {
    "lcb": 16384,
    "livebench": 16384,
    "aider": 16384,
}


class _CappedSuite:
    """Wraps a suite so Campaign._set_context applies sampling_override on
    top of the configuration's own sampling, without changing anything else
    about how the suite runs (name, run() both pass straight through). Used
    only by the mini bench level."""

    def __init__(self, inner, max_tokens: int):
        self.name = inner.name
        self._inner = inner
        self.sampling_override = {"max_tokens": max_tokens}

    def run(self, ctx) -> list[dict]:
        return self._inner.run(ctx)


def mini_suites(private_root: pathlib.Path, machine: str, seed: int) -> list:
    """One random item per suite (Guillaume, 2026-09-24, final): the same
    seed value drives every draw below, but each draw uses its OWN
    random.Random(f"{seed}:<purpose>") rather than one shared generator, so
    adding or reordering a draw here can never shift any other draw's
    result. bfcl and livebench each first draw which ONE of their categories
    to use (both suites are normally per-category; mini keeps them to
    exactly one item total, like every other suite), then draw the one item
    inside it (bfcl_run.py / livebench_select_ids.py, both seeded the same
    way). Every graded suite is wrapped in _CappedSuite; speed is not (its
    own prompt/gen_tokens sizing is untouched by the mini level)."""
    journal = journal_path(private_root, machine)
    bfcl_category = random.Random(f"{seed}:bfcl-category").choice(BFCL_DEFAULT_CATEGORIES)
    livebench_category = random.Random(f"{seed}:livebench-category").choice(("reasoning", "math"))
    ruler_task = random.Random(f"{seed}:ruler-task").choice(RULER_DEFAULT_TASKS)
    aider_file = _random_aider_exercises_file(private_root, 1, random.Random(f"{seed}:aider"))
    agentic_dir = _random_agentic_tasks_dir(private_root, 1, random.Random(f"{seed}:agentic"))

    graded = [
        LiveCodeBenchSuite(n_problems=1, selection_seed=seed),
        AiderSuite(exercises_file=aider_file),
        BfclSuite(categories=(bfcl_category,), limit=1, seed=seed),
        LiveBenchSuite(categories=(livebench_category,), limit=1, seed=seed),
        RulerSuite(lengths=[32768], tasks=[ruler_task], per_task=1),
        LongBenchV2Suite(lengths=[32768], samples_per_length=1, selection_seed=seed),
        AgenticSuite(tasks_dir=agentic_dir, cache_dir=private_root / ".cache" / "tasks"),
    ]
    return [
        _CappedSuite(suite, MINI_MAX_TOKENS_BY_SUITE.get(suite.name, MINI_MAX_TOKENS_DEFAULT))
        for suite in graded
    ] + [
        SpeedSuite(prompt_tokens=(512,), reps=1, journal_path=journal),
    ]


def sized_suites(level: str, private_root: pathlib.Path, machine: str) -> list:
    sizes = DETERMINISTIC_PRESET_SIZES[level]
    journal = journal_path(private_root, machine)
    return [
        LiveCodeBenchSuite(n_problems=sizes["lcb_n_problems"]),
        AiderSuite(exercises_file=_first_n_aider_exercises_file(private_root, sizes["aider_n"])),
        BfclSuite(limit=sizes["bfcl_limit"]),
        LiveBenchSuite(limit=sizes["livebench_limit"]),
        RulerSuite(lengths=sizes["ruler_lengths"], per_task=sizes["ruler_per_task"]),
        LongBenchV2Suite(lengths=sizes["longbench_lengths"],
                          samples_per_length=sizes["longbench_samples_per_length"]),
        AgenticSuite(tasks_dir=_first_n_agentic_tasks_dir(private_root, sizes["agentic_n"]),
                      cache_dir=private_root / ".cache" / "tasks"),
        SpeedSuite(journal_path=journal),
    ]


def bench_suites(preset: str, private_root: pathlib.Path, machine: str, seed: int | None = None) -> list:
    if preset == "mini":
        if seed is None:
            raise ValueError("mini needs a seed")
        return mini_suites(private_root, machine, seed)
    if preset in DETERMINISTIC_PRESET_SIZES:
        return sized_suites(preset, private_root, machine)
    raise SystemExit(f"unknown preset {preset!r}, known: mini, medium, large")


def bench_configs(machine: str, models: list[str] | None) -> list:
    """Every R1/R2/R3 configuration of the requested models (every model
    with a config for this machine when models is None), sorted so a given
    model's three profiles stay adjacent: that is what lets mini_suites'
    single shared seed give the three profiles of one model the exact same
    draw, campaign after campaign."""
    if models:
        paths = []
        for model in models:
            paths += sorted(glob.glob(str(BENCH_ROOT / "configs" / machine / model / "R*.yaml")))
    else:
        paths = sorted(glob.glob(str(BENCH_ROOT / "configs" / machine / "*" / "R*.yaml")))
    return [load_config(pathlib.Path(p)) for p in paths]


def run_campaign(args: argparse.Namespace, env: dict) -> None:
    private_root = pathlib.Path(env["BENCH_PRIVATE"])
    station = build_station(args.machine, env)
    configs = [load_config(pathlib.Path(p)) for p in sorted(glob.glob(args.configs))]
    if not configs:
        raise SystemExit(f"no config matched {args.configs!r}")
    suites = campaign_suites(args.suites.split(","), private_root, args.machine)
    campaign = Campaign(station, gateway_url(args.machine), suites, out_root=args.out,
                         reps=args.reps, private_root=private_root)
    campaign.run(configs)


def run_bench(args: argparse.Namespace, env: dict) -> None:
    private_root = pathlib.Path(env["BENCH_PRIVATE"])
    station = build_station(args.machine, env)
    models = args.models.split(",") if args.models else None
    configs = bench_configs(args.machine, models)
    if not configs:
        raise SystemExit(f"no config matched machine {args.machine!r} models {models!r}")
    out_root = pathlib.Path(args.out) / args.preset
    seed = None
    if args.preset == "mini":
        seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2**32)
    suites = bench_suites(args.preset, private_root, args.machine, seed=seed)
    campaign = Campaign(station, gateway_url(args.machine), suites, out_root=out_root,
                         reps=1, private_root=private_root)
    try:
        campaign.run(configs)
    finally:
        if args.preset == "mini":
            level_dir = out_root / args.machine
            level_dir.mkdir(parents=True, exist_ok=True)
            (level_dir / "seed.txt").write_text(f"{seed}\n", encoding="utf-8")
            report = build_mini_report(
                out_root, args.machine, journal_path(private_root, args.machine),
                seed, MINI_MAX_TOKENS_BY_SUITE, MINI_MAX_TOKENS_DEFAULT,
                datetime.datetime.now().isoformat(timespec="seconds"),
            )
            (level_dir / "rapport.md").write_text(report, encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m benchrun")
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--machine", required=True, choices=MACHINES)
    common.add_argument("--env", default=None, help="path to the real values file (bench/stations.example template)")

    run_p = sub.add_parser("run", parents=[common])
    run_p.add_argument("--configs", required=True, help="glob, e.g. 'configs/99/*/*.yaml'")
    run_p.add_argument("--suites", required=True,
                        help="comma-separated: lcb,aider,bfcl,livebench,ruler,longbench_v2,agentic,speed")
    run_p.add_argument("--reps", type=int, default=3)
    run_p.add_argument("--out", required=True)

    bench_p = sub.add_parser("bench", parents=[common])
    bench_p.add_argument("--preset", required=True, choices=("mini", "medium", "large"))
    bench_p.add_argument("--models", default=None,
                          help="comma-separated model names; default every model with a config for --machine")
    bench_p.add_argument("--out", required=True)
    bench_p.add_argument("--seed", type=int, default=None,
                          help="mini only: replay a previous mini launch's random draw")

    profiles_cmd.add_subparser(sub)

    args = ap.parse_args(argv)
    if args.cmd == "profiles":
        profiles_cmd.run(args)
        return
    env = resolve_env(args.env)
    if args.cmd == "run":
        run_campaign(args, env)
    else:
        run_bench(args, env)


if __name__ == "__main__":
    main()
