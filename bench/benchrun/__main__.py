"""Entry point: campaign runner and pilot (Task 16).

Two subcommands:
- "run" builds every requested suite at the campaign size (spec, phase 1),
  loads every config matching --configs and hands them to Campaign.run.
- "pilot" runs one model (tiel), one configuration (R1), every suite at the
  minimum size (spec, phase 2), on a single machine, and records loading time
  and each suite's wall time to a JSON file under BENCH_PRIVATE (see
  pilot_result_path): the runner and gateway containers only ever mount
  bench/ (Ruling J), not docs/, so merging that JSON into
  docs/pilote-2026-09.md so the campaign's duration can be projected is a
  separate, host-side step.

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
import glob
import json
import os
import pathlib
import tempfile
import time

from .config import load_config
from .runner import Campaign, SuiteContext
from .station import Station
from .suites.agentic import AgenticSuite
from .suites.aider import AiderSuite
from .suites.bfcl import BfclSuite
from .suites.lcb import LiveCodeBenchSuite
from .suites.livebench import LiveBenchSuite
from .suites.longbench import LongBenchV2Suite
from .suites.ruler import RulerSuite
from .suites.speed import SpeedSuite

# llm-ctl.ps1's fixed path on both stations (parallel-rules.md, Task 4/6/8).
CTL_PATH = r"D:\LLM-Setup\llm-ctl.ps1"
MACHINES = ("99", "97")
# The repo root's own bench/ directory, host or container alike: the runner
# and gateway containers only ever mount bench/ at /bench (Ruling J), never
# the whole repo, so this entry point (bench/benchrun/__main__.py) can only
# reach configs/ (a bench/ sibling of benchrun/) this way, never docs/ (a
# repo-root sibling of bench/, outside the mount). The pilot therefore
# records its measurements under BENCH_PRIVATE (also mounted, see
# run_pilot/_record_pilot_result) instead of writing docs/pilote-2026-09.md
# directly; merging that JSON into the doc is a step run on the host, after
# both machines' pilot containers have exited.
BENCH_ROOT = pathlib.Path(__file__).resolve().parents[1]
# Model and variant the pilot always uses (spec, phase 2): tiel is the one
# model present on both stations, R1 its first configuration.
PILOT_MODEL, PILOT_VARIANT = "tiel", "R1"


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


def _pilot_aider_exercises_file(private_root: pathlib.Path, n: int = 5) -> str:
    """First n lines of the frozen 60-exercise subset, written to a fresh
    temp file. Returned as an ABSOLUTE path string: AiderSuite.run() joins
    private_root / exercises_file with pathlib's own "/" operator, which
    discards the left side when the right side is already absolute, so this
    works regardless of where private_root points."""
    full = (private_root / "sets" / "aider-subset-60.txt").read_text(encoding="utf-8").splitlines()
    keep = [line for line in full if line.strip()][:n]
    fh = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, prefix="pilot-aider-")
    fh.write("\n".join(keep) + "\n")
    fh.close()
    return fh.name


def _pilot_agentic_tasks_dir(private_root: pathlib.Path, n: int = 5) -> pathlib.Path:
    """A temp directory holding symlinks to the first n task directories
    (sorted by id, deterministic), so AgenticSuite.run's own directory scan
    (sorted iterdir over tasks_dir) sees exactly n tasks without this entry
    point needing its own size knob on AgenticSuite itself."""
    tasks_dir = private_root / "tasks"
    all_tasks = sorted(p for p in tasks_dir.iterdir() if (p / "task.json").exists())
    pilot_dir = pathlib.Path(tempfile.mkdtemp(prefix="pilot-agentic-"))
    for task_dir in all_tasks[:n]:
        (pilot_dir / task_dir.name).symlink_to(task_dir, target_is_directory=True)
    return pilot_dir


def pilot_suites(private_root: pathlib.Path, machine: str) -> list:
    """Every suite at the spec's minimum pilot size (phase 2): LiveCodeBench
    10, Aider 5, one BFCL category, RULER 32k, 5 maison tasks are named
    explicitly in the spec; livebench, longbench_v2 and speed are not, so
    they are cut to the smallest unit each suite's own constructor exposes
    (one category, one length, one prompt length), the same "minimal size"
    pattern the spec states for the rest. Judgment call, not sourced from the
    spec text itself.
    """
    journal = journal_path(private_root, machine)
    return [
        LiveCodeBenchSuite(n_problems=10),
        AiderSuite(exercises_file=_pilot_aider_exercises_file(private_root, 5)),
        BfclSuite(categories=("simple_python",)),
        LiveBenchSuite(categories=("reasoning",)),
        RulerSuite(lengths=[32768], per_task=5),
        LongBenchV2Suite(lengths=[32768], samples_per_length=5),
        AgenticSuite(tasks_dir=_pilot_agentic_tasks_dir(private_root, 5),
                      cache_dir=private_root / ".cache" / "tasks"),
        SpeedSuite(prompt_tokens=(512,), gen_tokens=64, reps=1, journal_path=journal),
    ]


class _TimedSuite:
    """Wraps a suite to record its wall time into a shared dict, without
    changing anything about how Campaign drives it (name, sampling_override,
    SuiteSkipped all pass through unchanged)."""

    def __init__(self, inner, timings: dict):
        self.name = inner.name
        self._inner = inner
        self._timings = timings
        if hasattr(inner, "sampling_override"):
            self.sampling_override = inner.sampling_override

    def run(self, ctx: SuiteContext) -> list[dict]:
        t0 = time.monotonic()
        try:
            return self._inner.run(ctx)
        finally:
            self._timings[self.name] = time.monotonic() - t0


class _TimedStation:
    """Wraps a Station to record start() and warmup() durations separately
    from the suites' own time (spec, phase 2: "chronomètre séparément le
    chargement et l'évaluation"), while delegating every call unchanged."""

    def __init__(self, inner: Station, timings: dict):
        self._inner = inner
        self._timings = timings

    def start(self, cfg, timeout_s: int = 900) -> None:
        t0 = time.monotonic()
        self._inner.start(cfg, timeout_s=timeout_s)
        self._timings["start_s"] = time.monotonic() - t0

    def warmup(self) -> None:
        t0 = time.monotonic()
        self._inner.warmup()
        self._timings["warmup_s"] = time.monotonic() - t0

    def stop(self) -> None:
        self._inner.stop()

    def vram(self) -> dict:
        return self._inner.vram()


def pilot_result_path(private_root: pathlib.Path, machine: str) -> pathlib.Path:
    return private_root / "runs" / "pilot" / f"{machine}.json"


def _record_pilot_result(machine: str, private_root: pathlib.Path, station_timings: dict,
                          suite_timings: dict, error: str | None) -> None:
    """One JSON file per machine, under BENCH_PRIVATE (mounted in both
    containers, but each machine only ever writes its own file): both
    machines' pilot runs are launched in parallel (plan step 3), and giving
    each one a distinct path is what keeps them from ever touching the same
    bytes, no lock needed. Merging both files into docs/pilote-2026-09.md
    (which the containers cannot reach, see BENCH_ROOT) is a separate, host-
    side step.
    """
    out_path = pilot_result_path(private_root, machine)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "machine": machine,
        "error": error,
        "station": station_timings,
        "suites": suite_timings,
    }, indent=2), encoding="utf-8")


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


def run_pilot(args: argparse.Namespace, env: dict) -> None:
    private_root = pathlib.Path(env["BENCH_PRIVATE"])
    station_timings: dict = {}
    suite_timings: dict = {}
    station = _TimedStation(build_station(args.machine, env), station_timings)
    suites = [_TimedSuite(s, suite_timings) for s in pilot_suites(private_root, args.machine)]
    cfg_path = BENCH_ROOT / "configs" / args.machine / PILOT_MODEL / f"{PILOT_VARIANT}.yaml"
    cfg = load_config(cfg_path)
    out_root = private_root / "runs" / "pilot" / args.machine
    campaign = Campaign(station, gateway_url(args.machine), suites, out_root=out_root,
                         reps=1, private_root=private_root)
    error = None
    try:
        campaign.run([cfg])
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _record_pilot_result(args.machine, private_root, station_timings, suite_timings, error)


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

    sub.add_parser("pilot", parents=[common])

    args = ap.parse_args(argv)
    env = resolve_env(args.env)
    if args.cmd == "run":
        run_campaign(args, env)
    else:
        run_pilot(args, env)


if __name__ == "__main__":
    main()
