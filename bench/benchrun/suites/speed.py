"""Throughput suite adapter, ported from vitesse.ps1.

Sends the request vitesse.ps1 sends (/v1/chat/completions, cache_prompt
false, seed 42) through the gateway, which applies the configuration's own
sampling: draft acceptance depends on it, so speed is measured as the
configuration answers. Only max_tokens is pinned to gen_tokens, through
sampling_override, which the runner lays over the configuration's sampling
for this suite. Reads decode_tps, prefill_tps, draft_n and
draft_n_accepted straight from the response body's "timings" field, exactly
as vitesse.ps1 reads $r.timings.predicted_per_second. Unlike vitesse.ps1 this
suite also reports ttft_s, which llama-server never puts in the response
body: the gateway alone measures it (benchrun.gateway.relay_post) and writes
it to its journal, so ttft_s is read back from there, matched to the journal
line the gateway appended for the very request just sent (client-side wall
time is informational only over the current wifi hop, so this suite never
times its own requests as a substitute).

The filler prompt (benchrun.suites.corpus) is real source code, never a
repeated block.

Length policy, same rule as RulerSuite (benchrun.suites.lengths_to_run): a
length whose prompt plus gen_tokens exceeds the served window is skipped, not
silently dropped. lengths_to_run() here reuses benchrun.suites.max_ctx to
read --ctx-size from the configuration; it cannot reuse the shared
lengths_to_run itself, whose output margin is the configuration's max_tokens,
while a speed pass pins its own gen_tokens (sampling_override). run() writes
the skipped ones to <out_dir>/speed_skipped.json, exactly as RulerSuite.run()
does.
"""
from __future__ import annotations

import json
import pathlib
import urllib.request

from benchrun.suites import max_ctx, write_skip_report
from benchrun.suites.corpus import CHARS_PER_TOKEN, DEFAULT_CORPUS, filler_text as _filler_text

QUESTION = "\n\nResume ce code en une phrase."
# vitesse.ps1's own protocol: fixed seed, prompt cache off so every run pays
# for ingestion instead of only the first one.
SEED = 42
DETAIL_KEYS = ("decode_tps", "prefill_tps", "draft_n", "draft_n_accepted", "ttft_s")


def lengths_to_run(prompt_tokens: tuple[int, ...], gen_tokens: int, cfg) -> tuple[list[int], list[int]]:
    """Split prompt_tokens into what fits the served window with room for
    gen_tokens of output, and what does not. Reuses max_ctx to read
    --ctx-size; the +gen_tokens margin is speed-specific (a speed pass needs
    room for the generated tokens in the same window as the prompt)."""
    limit = cfg.ctx_proven or max_ctx(cfg)
    run = [n for n in prompt_tokens if n + gen_tokens <= limit]
    skipped = [n for n in prompt_tokens if n + gen_tokens > limit]
    return run, skipped


def median_row(rows: list[dict], key: str) -> float:
    """Median of one field across passes, ported from vitesse.ps1's Mediane:
    the lower of the two middle values on an even count, never their
    average, so the aggregate is always a value one of the passes actually
    produced."""
    values = sorted(r[key] for r in rows)
    return values[len(values) // 2]


def _read_new_ttft(journal_path: pathlib.Path, offset: int) -> tuple[float | None, int]:
    """Read the journal lines appended since offset and return the ttft_s of
    the last /v1/chat/completions entry among them, plus the new offset. The
    gateway writes one line per relayed request, line-buffered, so the entry
    for the request just answered is always the newest one on disk by the
    time the HTTP call above has returned."""
    with open(journal_path, "rb") as fh:
        fh.seek(offset)
        new_bytes = fh.read()
        new_offset = fh.tell()
    ttft = None
    for line in new_bytes.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("path") == "/v1/chat/completions":
            ttft = rec.get("ttft_s")
    return ttft, new_offset


def _one_pass(base_url: str, journal_path: pathlib.Path, filler: str, gen_tokens: int) -> dict:
    body = json.dumps({
        "messages": [{"role": "user", "content": filler + QUESTION}],
        "max_tokens": gen_tokens,
        "seed": SEED,
        "cache_prompt": False,
    }).encode("utf-8")
    offset = journal_path.stat().st_size if journal_path.exists() else 0
    req = urllib.request.Request(
        base_url + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        payload = json.loads(resp.read())
    timings = payload.get("timings")
    # decode_tps and prefill_tps are the whole point of this suite: a
    # response with no usable timings is a real failure, not a 0.0 tok/s
    # measurement. draft_n/draft_n_accepted are the one legitimate silent
    # default: llama-server omits both entirely when there is no drafter
    # (vitesse.ps1's own comment), so their absence IS the true value 0.
    if not timings or timings.get("predicted_per_second") is None or timings.get("prompt_per_second") is None:
        raise RuntimeError(f"response carries no usable timings: {payload}")
    ttft, _ = _read_new_ttft(journal_path, offset)
    if ttft is None:
        raise RuntimeError("gateway journal has no ttft_s line for the request just sent")
    return {
        "decode_tps": float(timings["predicted_per_second"]),
        "prefill_tps": float(timings["prompt_per_second"]),
        "draft_n": int(timings.get("draft_n") or 0),
        "draft_n_accepted": int(timings.get("draft_n_accepted") or 0),
        "ttft_s": float(ttft),
    }


class SpeedSuite:
    name = "speed"

    def __init__(
        self,
        prompt_tokens: tuple[int, ...] = (512, 8192, 32768),
        gen_tokens: int = 256,
        reps: int = 3,
        journal_path: str | pathlib.Path | None = None,
        corpus_dir: str | pathlib.Path | None = None,
    ):
        # journal_path is required at run() time: without it ttft_s cannot be
        # read back (see module docstring). Kept as a constructor default of
        # None, not a required positional, so the plan's documented call
        # SpeedSuite(prompt_tokens=..., gen_tokens=...) still type-checks;
        # run() raises immediately if it was never set.
        self.prompt_tokens = tuple(prompt_tokens)
        self.gen_tokens = gen_tokens
        self.sampling_override = {"max_tokens": gen_tokens}
        self.reps = reps
        self.journal_path = pathlib.Path(journal_path) if journal_path else None
        self.corpus_dir = pathlib.Path(corpus_dir) if corpus_dir else DEFAULT_CORPUS

    def run(self, ctx) -> list[dict]:
        if self.journal_path is None:
            raise RuntimeError("SpeedSuite needs journal_path to read ttft_s back from the gateway")
        run_lengths, skipped = lengths_to_run(self.prompt_tokens, self.gen_tokens, ctx.cfg)
        if not run_lengths and not skipped:
            raise RuntimeError("speed graded nothing and skipped nothing: no length was requested")
        write_skip_report(ctx.out_dir, "speed", run_lengths, skipped,
                           f"every prompt length {skipped} plus {self.gen_tokens} output tokens exceeds the served window")

        rows = []
        for n in run_lengths:
            filler = _filler_text(self.corpus_dir, n * CHARS_PER_TOKEN)
            passes = [_one_pass(ctx.base_url, self.journal_path, filler, self.gen_tokens)
                      for _ in range(self.reps)]
            detail = {key: median_row(passes, key) for key in DETAIL_KEYS}
            rows.append({"item_id": f"pp{n}", "passed": 1, "detail": detail})
        return rows
