"""Launcher for LongBench v2 (zai-org/LongBench-v2 on Hugging Face,
Apache-2.0) against an OpenAI-compatible endpoint (bench/harness/README.md,
LongBench v2 section, rule 3: no fork of a pinned repo).

The official evaluation harness is THUDM/LongBench's root pred.py
(LONGBENCH_SHA, MIT license): it hardcodes `URL = "http://127.0.0.1:8000/v1"`
and, to pick a tokenizer for prompt truncation, does a strict dict lookup
(`model_map[model]`/`maxlen_map[model]`, loaded from
config/model2path.json and config/model2maxlen.json at MODULE IMPORT TIME,
before any code of ours can run) that raises KeyError for any model name it
does not already know, our served alias included. Unlike RULER
(ruler_run.py), this cannot be fixed by monkeypatching before a runpy call:
the KeyError-raising lookups execute as soon as the module is imported, not
inside a function we could replace first.

What this launcher reuses from the pinned repo, unmodified, by reading the
file directly rather than importing the module: prompts/0shot.txt (the
0-shot multiple-choice template, byte for byte). What it re-implements,
because the source file's own top-level code has unrelated side effects that
would run before any patch point exists (pred.py's model_map/maxlen_map file
loads; result.py's `os.listdir('results')`): pred.py's extract_answer()
(answer-letter regex, ported verbatim below as _extract_answer, comment
marks the one-line difference) and result.py's `judge = pred == answer`
scoring rule (ported verbatim as the "judge" field of every output line).
Tokenizer: cl100k_base via tiktoken for every model, the same choice
ruler_run.py makes and for the same reason (RULER_SHA's own OpenAIClient
takes this branch for any "gpt"/"o1" model name; our aliases take neither
branch of the pinned model_map lookup, so cl100k_base is the harness's own
fallback made explicit rather than a new choice).

Bounded retries: a single-threaded loop (get_pred() in the pinned harness
uses one thread per shard; this launcher processes samples sequentially, so
os._exit's thread pitfall documented in ruler_run.py does not apply here),
up to MAX_ATTEMPTS per sample with a short backoff, and a hard stop
(sys.exit(1), safe here because the whole process is a single thread) after
MAX_CONSECUTIVE_FAILURES consecutive sample failures, so a persistent
endpoint error fails fast and loud instead of burning through every
remaining sample one by one.

Selection: _select_items builds each candidate's full prompt (_build_prompt,
template plus context plus question plus choices) and measures ITS token
count, the same quantity actually sent to the model, so a selected item is
proven to fit before being sent (measuring the context field alone would
miss the template and question's own token cost). _truncate_to_budget is
kept as a documented safety net (a tokenizer version drift, a rounding
difference between selection time and send time) and returns whether it
fired and how many tokens it cut, recorded in the output line
(input_truncated, input_tokens_cut) rather than silently.

Bands: the suite's target lengths (32768, 131072) must not select
overlapping items, since a model's score at a larger bucket would otherwise
partly re-test the same items as a smaller one. --band-floor (passed by
benchrun.suites.longbench.LongBenchV2Suite, computed from the full, sorted
target lengths list) restricts this launcher to items whose FULL PROMPT
token count is in (band_floor, context_length], a strict partition of every
possible token count across the suite's target lengths: an item's real size
places it in exactly one band, so no item_id can be selected by two
different context_length invocations. This corresponds loosely to the
dataset's own "length" field (measured on the real dataset: "short" items
are 8205-32173 words, "medium" items are 33139-129194 words, which is why
the 32768 target mostly draws "short" items and 131072 mostly draws "medium"
ones), but the band boundary the launcher enforces is the real full-prompt
token count, not the field, since a field value close to a boundary could
fall on either side of it once the template and question are added.

Selection is seeded (random.Random(SELECTION_SEED).sample) rather than
"first N sorted by _id": a fixed, deterministic seed keeps a given band's
selection reproducible across reps and reruns without always drawing the
same alphabetically-first subset of a large candidate pool.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import sys
import time

DATA_PATH = pathlib.Path("/longbench_v2_data.json")
TEMPLATE_PATH = pathlib.Path("/longbench/prompts/0shot.txt")

# Kept in sync with benchrun.suites.longbench's own MAX_ATTEMPTS / backoff
# (this script runs container-side and cannot import benchrun):
# bench/tests/test_longbench_run.py asserts the two agree.
MAX_ATTEMPTS = 3
MAX_CONSECUTIVE_FAILURES = 3
# Fixed for reproducibility across reps and reruns (see module docstring);
# not derived from the model, config or rep number, so a given band's
# candidate pool is sampled the same way regardless of who is running it.
SELECTION_SEED = 20260924


def _extract_answer(response: str) -> str | None:
    """Ported verbatim from pred.py's extract_answer() (LONGBENCH_SHA): same
    two-pattern regex, not imported (module docstring explains why)."""
    response = response.replace("*", "")
    match = re.search(r"The correct answer is \(([A-D])\)", response)
    if match:
        return match.group(1)
    match = re.search(r"The correct answer is ([A-D])", response)
    if match:
        return match.group(1)
    return None


def _build_prompt(template: str, item: dict) -> str:
    return (
        template.replace("$DOC$", item["context"].strip())
        .replace("$Q$", item["question"].strip())
        .replace("$C_A$", item["choice_A"].strip())
        .replace("$C_B$", item["choice_B"].strip())
        .replace("$C_C$", item["choice_C"].strip())
        .replace("$C_D$", item["choice_D"].strip())
    )


def _truncate_to_budget(prompt: str, encoding, budget_tokens: int) -> tuple[str, bool, int]:
    """Ported from pred.py's query_llm() truncation (LONGBENCH_SHA, the
    else branch: cl100k_base, disallowed_special=()): keep the first and
    last half of the token budget, drop the middle. _select_items now
    selects on the exact same full-prompt token count this function would
    check, so this should be a no-op in practice; it stays as the harness's
    own documented safety net, and reports whether it fired instead of
    cutting silently.
    """
    ids = encoding.encode(prompt, disallowed_special=())
    if len(ids) <= budget_tokens:
        return prompt, False, 0
    half = budget_tokens // 2
    kept = ids[:half] + ids[-half:]
    return encoding.decode(kept), True, len(ids) - len(kept)


def _select_items(
    data: list[dict],
    template: str,
    band_floor: int,
    band_ceiling: int,
    ratio: float,
    window: int,
    max_tokens: int,
    num_samples: int,
    encoding,
    seed: int = SELECTION_SEED,
) -> tuple[list[dict], list[str]]:
    """Select up to num_samples items whose FULL PROMPT (not context alone)
    both falls in this launcher's own band
    (band_floor, band_ceiling] of real token counts, and fits the config's
    window under the measured ratio margin. Disjoint bands across the
    suite's target lengths guarantee no item is selected by two different
    invocations (see module docstring). Selection within the eligible pool
    is seeded (random.Random(seed).sample), not "first N sorted by _id".
    """
    budget_tokens = int((window - max_tokens) / ratio) if ratio > 0 else 0
    banded: list[tuple[dict, int]] = []
    for item in data:
        prompt_tokens = len(encoding.encode(_build_prompt(template, item), disallowed_special=()))
        if band_floor < prompt_tokens <= band_ceiling:
            banded.append((item, prompt_tokens))
    banded.sort(key=lambda pair: pair[0]["_id"])  # deterministic before seeding

    eligible = [pair for pair in banded if pair[1] <= budget_tokens]
    skipped_ids = [pair[0]["_id"] for pair in banded if pair[1] > budget_tokens]

    rng = random.Random(seed)
    chosen = eligible if len(eligible) <= num_samples else rng.sample(eligible, num_samples)
    chosen.sort(key=lambda pair: pair[0]["_id"])  # stable output order
    return [item for item, _tokens in chosen], skipped_ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_alias")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--context-length", type=int, required=True)
    parser.add_argument("--band-floor", type=int, required=True)
    parser.add_argument("--window", type=int, required=True)
    parser.add_argument("--ratio", type=float, required=True)
    parser.add_argument("--max-tokens", type=int, required=True)
    parser.add_argument("--num-samples", type=int, required=True)
    parser.add_argument("--save-dir", required=True)
    args = parser.parse_args()

    import tiktoken
    from openai import OpenAI

    encoding = tiktoken.get_encoding("cl100k_base")
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    selected, skipped_ids = _select_items(
        data, template, args.band_floor, args.context_length, args.ratio, args.window,
        args.max_tokens, args.num_samples, encoding,
    )

    save_dir = pathlib.Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / "longbench_v2_selection_skipped.json").write_text(json.dumps({"skipped_ids": skipped_ids}))

    if not selected:
        print(
            f"longbench_run: no item with a full prompt in ({args.band_floor}, {args.context_length}] "
            f"fits window {args.window} with ratio {args.ratio}",
            file=sys.stderr,
        )
        sys.exit(1)

    client = OpenAI(base_url=args.base_url, api_key="x")
    out_path = save_dir / f"longbench_v2_{args.context_length}.jsonl"
    consecutive_failures = 0

    with open(out_path, "w", encoding="utf-8") as handle:
        for item in selected:
            prompt = _build_prompt(template, item)
            budget_tokens = int((args.window - args.max_tokens) / args.ratio) if args.ratio > 0 else 0
            prompt, input_truncated, input_tokens_cut = _truncate_to_budget(prompt, encoding, budget_tokens)
            if input_truncated:
                # _select_items checks this exact full-prompt token count
                # before choosing the item, so this should never fire; if
                # it does (tokenizer drift, a rounding difference), it must
                # be visible, not silent.
                print(
                    f"longbench_run: item {item['_id']} truncated by {input_tokens_cut} tokens "
                    "despite passing selection, this should not happen",
                    file=sys.stderr,
                )

            record = None
            last_exc = None
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    response = client.chat.completions.create(
                        model=args.model_alias,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=args.max_tokens,
                    )
                    choice = response.choices[0]
                    content = choice.message.content or ""
                    pred = _extract_answer(content)
                    record = {
                        "_id": item["_id"],
                        "domain": item["domain"],
                        "sub_domain": item["sub_domain"],
                        "difficulty": item["difficulty"],
                        "length": item["length"],
                        "answer": item["answer"],
                        "response": content,
                        "pred": pred,
                        # result.py's own scoring rule (LONGBENCH_SHA),
                        # ported verbatim: exact letter match, no partial
                        # credit and no compensation for an unparseable
                        # response.
                        "judge": pred == item["answer"],
                        "finish_reason": choice.finish_reason,
                        "completion_tokens": response.usage.completion_tokens if response.usage else None,
                        "input_truncated": input_truncated,
                        "input_tokens_cut": input_tokens_cut,
                    }
                    break
                except Exception as exc:  # noqa: BLE001 - real API/network errors, retried below
                    last_exc = exc
                    if attempt < MAX_ATTEMPTS:
                        time.sleep(2 * attempt)

            if record is None:
                consecutive_failures += 1
                print(f"longbench_run: item {item['_id']} failed after {MAX_ATTEMPTS} attempts ({last_exc!r})", file=sys.stderr)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(
                        f"longbench_run: {consecutive_failures} consecutive item failures, giving up",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                continue

            consecutive_failures = 0
            handle.write(json.dumps(record) + "\n")
            handle.flush()


if __name__ == "__main__":
    main()
