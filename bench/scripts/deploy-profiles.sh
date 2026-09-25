#!/usr/bin/env bash
# Deploy one machine's bench-derived launch profiles and its llm-ctl script.
#
# DO NOT RUN THIS AGAINST A STATION WHILE A BENCH CAMPAIGN IS USING IT. Both
# stations run a llm-ctl script that every live campaign calls through
# bench/benchrun/station.py, and replacing it mid-run can break that run.
# Deploy only once both stations are free.
#
# What it does, in order:
#   1. Builds benchrun:dev if needed (same image bench/scripts/test.sh uses)
#      and runs "python -m benchrun profiles --machine <M> --out <tmp>"
#      inside it, writing one JSON profile per bench/configs/<M>/*/R*.yaml
#      (bench/benchrun/profiles.py).
#   2. scp's every profile to D:\LLM-Setup\profiles\ on that station.
#   3. scp's this machine's llm-ctl script (llm-ctl.ps1 for 99,
#      llm-ctl-16gb.ps1 for 97, this repo's own names) to
#      D:\LLM-Setup\llm-ctl.ps1 on that station: both stations run a script
#      under that SAME remote name (CTL_PATH, bench/benchrun/__main__.py),
#      only their local, repository-side name differs.
#
# Usage:
#   bench/scripts/deploy-profiles.sh --machine 99|97 --env <path>
# <path> is a real-values file shaped like bench/stations.example (never
# committed: the repository's write hook refuses any ".env*" name on purpose).
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
  echo "usage: $0 --machine 99|97 --env <path to a stations.example-shaped file>" >&2
  exit 2
}

machine=""
env_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --machine) machine="${2:-}"; shift 2 ;;
    --env) env_file="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
[[ "$machine" == "99" || "$machine" == "97" ]] || usage
[[ -n "$env_file" && -f "$env_file" ]] || usage

# The file is a compose env file, not shell: values are literal and unquoted
# (BENCH_PRIVATE holds a path with a space), so sourcing it breaks. Only the
# one key this script needs is read, last assignment wins as in compose.
ssh_var="STATION_${machine}_SSH"
ssh_target="$(sed -n "s/^${ssh_var}=//p" "$env_file" | tail -n 1)"
[[ -n "$ssh_target" ]] || { echo "missing ${ssh_var} in ${env_file}" >&2; exit 2; }

# The llm-ctl scripts live at the repository root, one level above the bench/
# directory this script cd's into.
if [[ "$machine" == "99" ]]; then
  ctl_script="../llm-ctl.ps1"
else
  ctl_script="../llm-ctl-16gb.ps1"
fi

out_dir="$(mktemp -d)"
trap 'rm -rf "$out_dir"' EXIT

# shellcheck disable=SC1091
source harness/pins.env
./scripts/docker-build.sh benchrun:dev harness/benchrun.Dockerfile . \
    --build-arg DOCKER_CLI_URL="$DOCKER_CLI_URL" --build-arg DOCKER_CLI_SHA256="$DOCKER_CLI_SHA256"

docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD":/bench -v "$out_dir":/out -w /bench benchrun:dev \
  python -m benchrun profiles --machine "$machine" --out /out

ssh_opts=(-o ConnectTimeout=10)
ssh "${ssh_opts[@]}" "$ssh_target" \
  "powershell -NoProfile -Command \"New-Item -ItemType Directory -Force -Path 'D:\\LLM-Setup\\profiles' | Out-Null\""

echo "Deploying $(ls "$out_dir"/*.json | wc -l | tr -d ' ') profiles to ${ssh_target}:D:/LLM-Setup/profiles/"
scp -q "${ssh_opts[@]}" "$out_dir"/*.json "${ssh_target}:D:/LLM-Setup/profiles/"

echo "Deploying ${ctl_script} to ${ssh_target}:D:/LLM-Setup/llm-ctl.ps1"
scp -q "${ssh_opts[@]}" "${ctl_script}" "${ssh_target}:D:/LLM-Setup/llm-ctl.ps1"

echo "Done: machine ${machine}, $(ls "$out_dir"/*.json | wc -l | tr -d ' ') profiles, ${ctl_script} -> llm-ctl.ps1"
