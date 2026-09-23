#!/usr/bin/env bash
# Usage: check-ctl-bench.sh <ssh-target> <local-spec.json>
set -euo pipefail
target="$1"; spec="$2"
scp -q "$spec" "$target:D:/LLM-Setup/bench-specs/check.json"
out=$(ssh "$target" 'powershell -NoProfile -ExecutionPolicy Bypass -File D:\LLM-Setup\llm-ctl.ps1 -Action bench -Spec D:\LLM-Setup\bench-specs\check.json -DryRun')
echo "$out"
exe=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['exe'])" "$spec")
grep -qF "DRYRUN $exe --alias" <<<"$out"
grep -qF -- "--ctx-size 8192" <<<"$out"
echo OK
