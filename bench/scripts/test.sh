#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source harness/pins.env
./scripts/docker-build.sh benchrun:dev harness/benchrun.Dockerfile . \
    --build-arg DOCKER_CLI_URL="$DOCKER_CLI_URL" --build-arg DOCKER_CLI_SHA256="$DOCKER_CLI_SHA256"
# No bytecode or pytest cache written into the bind-mounted working tree.
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD":/bench -w /bench benchrun:dev \
  pytest -q -p no:cacheprovider "$@"
