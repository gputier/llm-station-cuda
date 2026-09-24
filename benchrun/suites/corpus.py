"""Shared filler-text corpus for suites that need a long, real prompt.

Real source code, never a repeated block, as aiguille.ps1 requires for its
own filler: a repeated block would let the model answer from a pattern
instead of paying the real ingestion cost. Read from this repository's own
source (.py and .ps1 files, the whole repo so 32768 tokens' worth of filler
is always reachable), sized by a fixed characters-per-token ratio (aiguille.ps1
uses the same 3 chars/token approximation) since an exact tokenizer count
cannot be had without relaying /tokenize.
"""
from __future__ import annotations

import pathlib

# bench/benchrun/suites/corpus.py -> suites -> benchrun -> bench -> repo root
DEFAULT_CORPUS = pathlib.Path(__file__).resolve().parents[3]
CHARS_PER_TOKEN = 3
FILLER_SUFFIXES = (".py", ".ps1")


def filler_text(corpus_dir: pathlib.Path, target_chars: int) -> str:
    parts = []
    total = 0
    for path in sorted(corpus_dir.rglob("*")):
        if path.suffix not in FILLER_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        parts.append(f"# ---- {path.name} ----\n{text}")
        total += len(parts[-1])
        if total >= target_chars:
            break
    text = "".join(parts)
    if len(text) < target_chars // 2:
        raise RuntimeError(f"corpus too short: {len(text)} characters for {target_chars} requested")
    return text[:target_chars]
