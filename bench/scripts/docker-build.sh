#!/usr/bin/env bash
# Guarded docker build: refuses to run below a free-disk floor on the data
# volume, so a build never repeats the disk-fill incident that stopped a
# previous session. Every image in the bench harness is built only through
# this script, one build at a time (never run two of these in parallel).
set -euo pipefail

MIN_FREE_KB=$((20 * 1024 * 1024))  # 20 GB floor, in 1K blocks

# The floor applies to the volume that holds Docker Desktop's disk image, which
# can live outside the system disk (DataFolder in its settings store).
SETTINGS="$HOME/Library/Group Containers/group.com.docker/settings-store.json"
docker_volume=/System/Volumes/Data
if [ -f "$SETTINGS" ]; then
    data_folder=$(plutil -extract DataFolder raw -o - "$SETTINGS" 2>/dev/null || true)
    if [ -n "$data_folder" ] && [ -d "$data_folder" ]; then
        docker_volume="$data_folder"
    fi
fi

usage() {
    echo "usage: $(basename "$0") <tag> <dockerfile> <context> [--build-arg KEY=VAL ...]" >&2
}

if [ "$#" -lt 3 ]; then
    usage
    exit 3
fi

tag="$1"
dockerfile="$2"
context="$3"
shift 3

free_kb=$(df -k "$docker_volume" | awk 'NR==2 {print $4}')
if [ "$free_kb" -lt "$MIN_FREE_KB" ]; then
    free_gb=$(awk -v k="$free_kb" 'BEGIN { printf "%.1f", k/1024/1024 }')
    echo "BLOCKED: only ${free_gb} GB free on ${docker_volume}, need at least 20 GB. Refusing to build ${tag}." >&2
    exit 3
fi

docker build -t "$tag" -f "$dockerfile" "$context" "$@"

size=$(docker image inspect "$tag" --format '{{.Size}}' | awk '{printf "%.1f MB", $1/1024/1024}')
free_after_kb=$(df -k "$docker_volume" | awk 'NR==2 {print $4}')
free_after_gb=$(awk -v k="$free_after_kb" 'BEGIN { printf "%.1f", k/1024/1024 }')
echo "BUILT ${tag}: size=${size}, free_after=${free_after_gb} GB on ${docker_volume}"
