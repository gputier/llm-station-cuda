# Muse Glimmer 30B

Dense 30B backbone with a ViT-G/14 perception encoder and a DFlash drafter.
Apache 2.0. The agentic and multimodal model of this box: reliable multi-turn
tool calling, and the only one here that reads an image faithfully.

```powershell
.\llm-ctl.ps1 -Action muse
```

| | |
|---|---|
| Weights | `Muse-Glimmer-30B-UD-Q4_K_XL.gguf`, 14.8 GB |
| Vision projector | `mmproj-kquant.gguf`, 1.30 GB |
| Drafter | `dflash-kquant.gguf`, 1.5 GB |
| Context | 1,048,576 (trained at 131,072) |
| VRAM | 29.0 GB of 32.6 |
| Throughput | 102 to 107 tok/s |
| Build | upstream 2026-08-11. The frozen fork does not know the `muse-glimmer` architecture. |

## Role

Use it for agentic loops, for OCR, and for any context beyond 262k. Use
[qwen3.8-27b](../qwen3.8-27b/) to reason and to code. That split was established
by measurement: Qwen reads numbers correctly but distorts identifiers, which
makes it the wrong tool for reading documents.

## The 1M context, and the flag that does not do it

llama.cpp caps the slot on the `context_length` written **in the GGUF**, not on
`--ctx-size`. Without an override, asking for 262144 or 786432 returns exactly
131072, silently, with a single log line:

```
the slot context (...) exceeds the training context of the model - capping
```

The one and only lock is:

```
--override-kv muse-glimmer.context_length=int:1048576
```

**The three YaRN flags were removed, they did nothing.** This profile used to
carry `--rope-scaling yarn --rope-scale 8 --yarn-orig-ctx 131072`, on the belief
that the 1M window came from an 8x YaRN stretch. That was false. Without them
the server allocates `n_ctx_slot = 1048576` with no capping line, and recall
holds at 556,390 tokens.

The architecture explains it: only the sliding-window layers use RoPE, the
global layers have no positional encoding at all, so there is no position to
stretch. **The 1M context did not work thanks to YaRN, it worked despite it.**

A window is only worth what the model still finds in it. Recall here was proven
by needle-in-a-haystack at 122,254, 256,696 and 556,390 tokens, not inferred
from the server accepting the prompt.

## `-ub 512` is the setting not to touch

With `--mmproj`, the prompt goes through the multimodal pipeline, whose compute
buffer explodes when the micro-batch is large. At `-ub 4096` that buffer weighed
**10 GB of VRAM** and produced two symptoms with no apparent connection:

1. `decode() failed: bad allocation` (HTTP 500) past 10k tokens of prompt, seen
   from an agentic client as `400 Failed to tokenize prompt`;
2. the DFlash drafter **silently disabled** past 96k of context for lack of
   memory. 83 tok/s instead of 128, with no error anywhere.

The second one was first attributed to the context window, and the window was
throttled to 98304 as a result. That was wrong. At `-ub 512`, 131072 fits in
21.4 GB and the drafter stays alive. No cost: generation unchanged, prompt
processing up from 1,376 to 3,000 tok/s.

**If you touch `-b`, `-ub` or `--ctx-size`, re-prove the drafter** by checking
that `draft_n` is present in the `timings` of a response. Without that check you
can lose a third of your throughput and never see a message.

## Do not set `--spec-draft-n-max 15`

The DFlash block is 16 tokens, 1 anchor plus 15 proposed, which makes 15 look
like the obvious value. It is a trap. Forcing the depth does not merely add
cost, it **collapses the acceptance rate**.

| Configuration | Throughput | Drafted / kept |
|---|---|---|
| no drafter | 82.24 tok/s | - |
| `--spec-draft-n-max 15` | 101.27 tok/s | 3,482 / 364 (10.2%) |
| **default (kept)** | **102 to 107 tok/s** | **818 / 326 (39.9%)** |

Nearly the same gain for 4.3x less wasted compute. On Metal the same flag pushes
the drafter **below** the no-drafter baseline (10.62 against 29.19 tok/s): the
direction transposes across backends, the magnitude does not.

Real drafter gain here is **1.23x to 1.31x**, not the 1.5x to 1.7x we first
recorded.

## Reasoning effort is a template argument, not a prompt

```json
{"chat_template_kwargs": {"reasoning_strength": "low"}}
```

Values: `low`, `medium`, `high`, `xhigh`.

Writing `Reasoning strength: low` in a system message does **nothing**. The
model then thinks without a bound and can consume the entire token budget in
reasoning, returning an empty `content` while the thinking went to
`reasoning_content`. Budget at least 2000 tokens for a response.

A recall test with `max_tokens=100` produces a **false failure** for the same
reason: the model does find the needle but gets cut mid-answer, and it reads
like a model error.

## Sampling

`temperature 1.0`, `top_p 0.95`, `top_k 64`. Publisher calibration. Do not lower
the temperature. A hard-coded `temperature=0.3` inherited from an unrelated model
was silently degrading this one for weeks.

Note `top_k 64` here against `top_k 20` on Qwen. The two are not interchangeable.

## The vision projector: lighter is more faithful

`mmproj-kquant.gguf` (1.30 GB) replaced the Q8_0 (1.91 GB), and it is **both
lighter and more accurate**, which was not the intuition.

Measured on a deliberately hard bank statement (11pt body, low contrast,
rotated, blurred), 15 lines compared field by field against ground truth over 3
runs: the kquant makes no value error, where the Q8_0 reads `-88.90` instead of
`-88.940`. The BF16 (3.58 GB) is ruled out, it pushed VRAM to 31.6 GB of 32.6.

## Large prompts die around 302 seconds

Past roughly 500,000 tokens in a **single** request, the connection dies during
prompt processing. The server logs `cancel task` and stays alive; the client
sees `Remote end closed connection without response`.

This is not a window limit: processing was progressing normally at 1,630 tok/s.
Two tests at 560k and 894k failed on this and were nearly misread as recall
failures.

Proven workaround: send growing prefixes that share the same beginning. The KV
cache keeps the work already done and each call stays under the bar. That is how
556,390 was validated.
