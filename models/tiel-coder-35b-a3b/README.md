# Tiel-Coder-35B-A3B (MIT)

Sparse MoE, architecture `qwen35moe`: 41 blocks, 256 experts, 8 active per
token, so roughly 3B of the 35B parameters do the work per token.
`general.name` in the GGUF reads `Ornith-1.5-35B`: this is a requantisation of
Ornith-1.5-35B-A3B with an MTP head added, not a separate model. The coding
model of this box.

```powershell
.\llm-ctl.ps1 -Action tiel
```

|                  |                                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------ |
| Weights          | `Tiel-Coder-35B-A3B-MTP-UD-Q4_K_XL.gguf`                                                         |
| Vision projector | `mmproj-BF16.gguf`                                                                               |
| Context          | 262,144, the GGUF's declared ceiling. Ran at 393,216 with `--override-kv` until 2026-09-26.      |
| KV cache         | `q4_0`                                                                                           |
| VRAM             | 29,198 MB, measured 2026-09-26 at the 262,144 window                                             |
| Slots            | 1. It ran 2 from 2026-09-06 to 2026-09-08, see below.                                            |
| Build            | `b10826` (2026-09-06), official release binary, no compilation. Only build serving this profile. |

## On the 16 GB box

The same model also runs on the RTX 4080 SUPER box since 2026-09-14, from
[../../llm-ctl-16gb.ps1](../../llm-ctl-16gb.ps1), in a smaller tier:
`Tiel-Coder-35B-A3B-MTP-UD-IQ3_XXS.gguf`, 13.6 GB, from
`peculiar-ragdoll/Tiel-Coder-35B-A3B-GGUF-MTP`. BeeLlama v0.4.6, KVarN 3 cache,
MTP depth 2, window 262,144, no vision projector, temperature 0.6 like here.

Measured that day on a 6,018-token prompt: 139.3 tok/s decode, 2,893 tok/s
prefill, 15,413 MiB of VRAM. MMLU 85.8% and GSM8K 55/60 on the set of the
2026-09-10 campaign, against 86.6% and 53/60 for the UD-Q4_K_XL build here: the
3-bit tier costs no measurable quality.

Two things are not measured there yet. Nothing past short prompts, where
`qwen36` on the same recipe lost three quarters of its decode at 200k tokens in
real use; and no run through Claude Code. The launcher `tiel` asks which box to
use. Details of both boxes: [../qwen3.6-35b-a3b/](../qwen3.6-35b-a3b/) and
[../../docs/tuning-log.md](../../docs/tuning-log.md).

## Why this model over `qwen`

Measured 2026-09-01 against the `qwen` profile, same 37,981-token prompt, seed
42, three runs, median, plus a fresh 500-question MMLU set spanning 25 subjects
and 60 GSM8K problems, temperature 0, both models on the identical set:

|                          | qwen         | tiel         |                                            |
| ------------------------ | ------------ | ------------ | ------------------------------------------ |
| Decode                   | 104.53 tok/s | 161.43 tok/s | +54.4%                                     |
| Prefill                  | 4,264 tok/s  | 8,616 tok/s  | x2.02                                      |
| VRAM                     | 30,952 MB    | 29,465 MB    |                                            |
| MMLU                     | 82.0%        | 82.2%        | one question in five hundred               |
| MMLU, re-run 2026-09-10  | 78.8%        | **86.6%**    | the old bench was lost, both figures moved |
| GSM8K, re-run 2026-09-10 | 57/60        | 53/60        | tiel's weak spot, `kat` scores 60/60       |
| GSM8K                    | 52/60        | 58/60        |                                            |

The gain is structural: far fewer bytes reread per token, which is exactly the
bandwidth ceiling this machine runs into.

**Acceptance rate does not predict throughput.** Tiel's MTP acceptance is
27.2%, worse than qwen's 38.7%, and it still wins by half again. Read the
acceptance percentage as a diagnostic, never as the criterion for a decision.

## `--spec-draft-n-max 2`, not 4

Swept on this model on 2026-09-03, production flags otherwise, one
38,000-token prompt of real llama.cpp sources, fixed seed, 3 runs, median
decode:

| n-max | Decode          | Acceptance                |
| ----- | --------------- | ------------------------- |
| **2** | **184.8 tok/s** | 46.9%                     |
| 3     | 181.9 tok/s     | 40.1%                     |
| 4     | 156.8 tok/s     | 27.2% (was in production) |
| 6     | 133.3 tok/s     | 18.4%                     |

Raising `--spec-draft-p-min` to 0.40 or 0.60 lifts acceptance to 50-66% and
halves throughput: acceptance is not the target, throughput is. This sweep was
run at short context only; the 2026-08-31 lesson on `qwen`, where the n-max
ranking inverts at full context, has not been replayed here.

A same-day bench (2026-09-08) later showed the sweep itself measures the wrong
regime for part of the workload: `bench.ps1` asks for a ten-line French
summary, the least predictable text a draft head can be handed. On generated
code, speculation is worth 30%; on French prose, it can cost 15%. Every n-max
decision taken on this bench has been taken on prose. A code-shaped
long-context bench is still missing.

## Two slots, tried for two days, then given up

Two workstations call this model, which is why `--parallel 2 --kv-unified` was
put in on 2026-09-06. It came out on 2026-09-08, and the reason is not
throughput.

**A shared pool forces the client to be told half a window.** With
`--kv-unified` the 393,216 tokens are ONE pool rather than 2 x 196,608, which is
the right way round: without the flag llama-server divides `-c` between slots
while `/props` still announces the total. But a pool is still shared, so an
honest client may fill only its share, and `CLAUDE_CODE_MAX_CONTEXT_TOKENS` had
to be halved to 180,000. That figure is what a client reads to decide when to
compact. Agents spawned inside a single session therefore compacted their
context away long before the model would have run out: work thrown out to
respect a ceiling that only exists because a second slot might want its turn.

Leaving the announcement at the full pool instead fails louder, and was seen the
same afternoon: a 196,000-token prefill at 1,546 tok/s against the 8,000 the
bench gives, the other slot generating at 1.40 tok/s, then `failed to find free
space in the KV cache` with the batch halved down to 16 and still failing.

One slot, the whole window, concurrent callers queue. Waiting costs time;
compacting costs work already done.

The two-slot measurements are kept here, because they stay true if the question
is ever reopened. Measured 2026-09-06 on b10826, 400 tokens forced, pure
generation:

| Streams | Per stream           | Total                |
| ------- | -------------------- | -------------------- |
| 1       | 260 tok/s            | 260                  |
| 2       | 202 + 188 tok/s      | 390                  |
| 4       | 97 to 104 tok/s each | 400 (card saturated) |

The cost is the prefill: while one slot reads a 40k prompt (4 to 5 s), the
other's generation drops to 15 to 50 tok/s. Four slots were rejected: no gain
over two for two callers, half the per-stream rate, and only 580 MB of VRAM
headroom under two-stream load.

## `--ctx-size 393216`, until 2026-09-26

The GGUF declares `context_length 262144`; llama.cpp caps the slot on that
value and ignores a larger `--ctx-size`, in a single log line. The lock used to
be `--override-kv qwen35moe.context_length=int:393216`, same mechanism as on
`muse` and `qwen`. The override was dropped on 2026-09-26: this profile now
runs at the GGUF's own 262,144 ceiling, and the `--override-kv` line is gone
from `llm-ctl.ps1`.

Recall verified 2026-09-01 by needle-in-a-haystack at 269,274 then 378,540
tokens, needle at 10/50/90% depth, 6 hits out of 6, at the 393,216 window that
ran then. VRAM at that window sat at 31,617 MB of 32,607: about 990 MB of
headroom, not yet exercised with an image on input while the projector is
loaded.

## The first request after a start is not a measurement

Every speculation run on this box collapses on its first request and recovers
on the next: 49 to 88 tok/s against 180 to 208 immediately after. A run without
speculation shows nothing of the sort. Tested by sending a second prompt built
from a different slice of the sources to a warm server:

|                 | prompt A, cold | prompt A, cached | prompt B, new context | prompt B, cached |
| --------------- | -------------- | ---------------- | --------------------- | ---------------- |
| speculation on  | 55.82 tok/s    | 200.20           | 190.10                | 191.83           |
| speculation off | 196.96         | 195.98           | 187.63                | 193.15           |

A new context costs nothing; only the first request after a start does, and
only under speculation. Discard run 1 of any speculative bench on this model.

## Temperature 0.6, back to the card

Ornith's model card recommends 0.6 for general use and reserves 1.0 for
reproducing its benchmarks. Claude Code sends no temperature (verified by
capturing a request: only `thinking`, `output_config.effort` and `max_tokens`
are sent, none of which llama-server maps to a reasoning budget), so the
profile's value is the one every session runs at. It went from 1.0 to 0.6 on
2026-09-06, then to 0.3 on 2026-09-10, both times by hand on the box as a trial
on real usage, and back to 0.6 on 2026-09-26 on both boxes, aligned with the
card again. No bench backs any of these steps: a 12-prompt strict-instruction
bench passed 36/36 at temperature 1.0, so it could not discriminate between
values.

Tiel embeds the Sharp chat template `qwen3.8-froggeric-v22.4.0` with a
force-appended terseness system prompt (`terse` kwarg, default true), thinking
on and reasoning effort `medium` by default.

## Build: b10826 only

`b10826` is the official release binary posted flat, no compilation. Against
the 2026-08-27 build on the same profile, 65,615-token prompt, 400 tokens,
seed 42, 3 runs: prefill 8,724 to 9,155 tok/s (+4.9%), decode 211.3 to 210.8
(identical), VRAM 31,707 to 31,550 MB. Two behaviour changes it brings, both
logged at startup: `preserve_reasoning` is on by default (`--no-reasoning-preserve`
turns it off if prompts grow), and it recommends `--image-min-tokens 1024` for
this vision model, which the profile does not currently carry.
