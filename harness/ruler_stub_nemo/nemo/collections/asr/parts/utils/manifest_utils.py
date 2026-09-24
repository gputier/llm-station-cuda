# Stub of nemo.collections.asr.parts.utils.manifest_utils (see
# bench/harness/README.md, RULER section): pred/call_api.py and eval/evaluate.py
# import only these two functions from the nemo-toolkit, which would otherwise
# pull torch and a CUDA stack never touched on the OpenAI-endpoint path. Body
# copied from the pinned repo's OWN scripts/data/manifest_utils.py (read at
# RULER_SHA), which reimplements the same two functions for the same reason
# on the data-prep side.
import json


def read_manifest(manifest_path):
    data = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data


def write_manifest(output_path, target_manifest, ensure_ascii: bool = True):
    with open(output_path, "w", encoding="utf-8") as outfile:
        for tgt in target_manifest:
            json.dump(tgt, outfile, ensure_ascii=ensure_ascii)
            outfile.write("\n")
