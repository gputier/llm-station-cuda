#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -q -t benchrun:dev -f harness/benchrun.Dockerfile . >/dev/null
# No bytecode or pytest cache written into the bind-mounted working tree.
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD":/bench -w /bench benchrun:dev \
  pytest -q -p no:cacheprovider "$@"
