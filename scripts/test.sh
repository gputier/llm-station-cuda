#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/docker-build.sh benchrun:dev harness/benchrun.Dockerfile .
# No bytecode or pytest cache written into the bind-mounted working tree.
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD":/bench -w /bench benchrun:dev \
  pytest -q -p no:cacheprovider "$@"
