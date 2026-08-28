# Qwen3.8-27B (NVFP4)

Hybrid dense 28B: 48 Gated DeltaNet layers plus 16 full-attention layers,
architecture `qwen35`, native vision. Apache 2.0. The reasoning and coding model
of this box.

```powershell
.\llm-ctl.ps1 -Action qwen
```

| | |
|---|---|
| Weights | `Qwen3.8-27B-NVFP4-MTP-LOW.gguf`, 14.5 GiB |
| Vision projector | `mmproj-BF16.gguf`, 0.87 GiB |
| Context | 262,144 (native, hard ceiling without an override) |
| KV cache | `q4_0` |
| VRAM | 26,453 MB of 32,607 |
| Throughput | 123.4 tok/s decode, 4,267 tok/s prefill |
| Build | **2026-08-27 only.** NVFP4 kernels do not exist in older builds. |

## Why NVFP4

The model body is NVFP4, a format the RTX 5090 tensor cores execute natively.
Measured against the previous `UD-Q5_K_XL`, same prompt, same protocol, with
speculation **disabled on both sides** to isolate the structural gain from
acceptance noise:

| | Q5_K_XL | NVFP4 LOW | |
|---|---|---|---|
| Decode | 58.84 tok/s | 70.64 tok/s | +20.1% |
| Prefill | 3,015 tok/s | 4,584 tok/s | +52.0% |
| VRAM | 28.5 GB | 24.4 GB | 4.1 GB freed |

**The gain comes from size, which is direct confirmation that the ceiling is
memory bandwidth**: fewer bytes to re-read per token. The operational corollary
is that raising precision costs speed. A Q8 of the weights would be a net
regression, and it is also why the cache stays at `q4_0`.

Quality was proven, not assumed: MMLU 72.8% against 72.4% over an identical
500-question set, GSM8K 56/60 for both quants **failing the same problems**,
recall verified 4/4 at 198,625 tokens. Full protocol in
[../../docs/tuning-log.md](../../docs/tuning-log.md).

The MEDIUM tier was measured and rejected: slower, heavier, marginally worse.
The repository publishes nine tiers sharing an identical NVFP4 body, differing
only in head precision.

## Four things that do not follow from the documentation

### 1. MTP speculation has no draft file

Unlike the DFlash drafter of [muse](../muse-glimmer-30b/), there is **no draft
model at all**. The `blk.*.nextn.*` tensors are already inside the quant;
llama.cpp loads them and **ignores them** unless you pass `--spec-type
draft-mtp`. One flag enables speculation, no second file to fetch.

`--spec-draft-n-max 3` is the retained depth. A community benchmark recommended
4 for this exact card and quant: measured here, 108.66 tok/s against 123.03 at
3. A setting published for the same hardware and the same model transposes no
better than any other.

### 2. The embedded chat template blocks agentic clients

This is a hard blocker, not an annoyance. The template raises `System message
must be at the beginning` as soon as a `system` message arrives after a `user`
message, which agentic clients do mid-session.

The symptom points the wrong way: `/v1/chat/completions` works perfectly in
every manual test while the agentic client fails with `API Error: 500` on the
very first turn.

Fix: a copy of the embedded template with **one line changed**, so a late system
message is rendered as an ordinary ChatML system turn instead of raising. ChatML
supports this natively; it was the template refusing, not the format. Load it
with `--chat-template-file`.

### 3. The reasoning switch is not the same key as on muse

| Model | Key |
|---|---|
| Muse Glimmer | `chat_template_kwargs.reasoning_strength` |
| Qwen3.8-27B | `enable_thinking` (boolean) or `reasoning_effort` |

Using the wrong key raises **no error**: the parameter is ignored and the model
reasons unbounded. Verified: 0 characters of reasoning with
`enable_thinking: false`, 112 at `low`, 162 at `high`, 227 at the default.

Same trap for sampling: `top_k` is **20** here and 64 on muse.

### 4. In vision, it reads numbers but distorts identifiers

On a meter reading, every numeric value came out exact, but `bond0.1000` was
read as `bond0 : 1000` and `BDX01` as `BDXX01`. Doubling the resolution **makes
it worse**, so the defect comes from the model and not from the image.

Use [muse](../muse-glimmer-30b/) for OCR.

## `--ctx-size 262144`, and why not more

The GGUF declares `context_length = 262144`. llama.cpp caps the window there, in
a single log line, **but still sizes its buffers on the value you requested**.
This profile used to ask for 524288 and paid the memory of a window it never had:

| Requested | Real window | VRAM | Decode | Prefill |
|---|---|---|---|---|
| **262144** | 262144 | **27.2 GB** | **123.03** | **4,241** |
| 524288 | 262144 | 31.9 GB | 99.76 | 2,462 |

Twenty-three percent of decode and 72% of prefill lost for nothing.

Raising it for real requires `--override-kv qwen35.context_length`, exactly like
muse's 1M, and recall must then be re-proven by measurement. Expect it to cost
most of the NVFP4 gain, since 512k-sized buffers put the card back into the
saturation regime.

## Why the KV cache is `q4_0` and not `q8_0`

This is a VRAM trade, not a quality trade, and it was measured. At 262k, `q8_0`
leaves 675 MB of headroom out of 32.6 GB and throughput **collapses**:

| Cache | Window | VRAM | Throughput |
|---|---|---|---|
| `q8_0` | 262k | 31,932 MB | 73.81 tok/s |
| **`q4_0`** | **262k** | **28,370 MB** | **100.32 tok/s** |
| `q8_0` | 131k | 26,846 MB | 99.00 tok/s |

Below ~29 GB throughput saturates around 100 tok/s: `q4_0` at 262k and `q8_0` at
131k run at the same speed, and the former gives twice the window.

The q4 cache does not cost recall: needle found at 207,067 tokens of prompt,
verified by execution.

## The ceiling is structural

At roughly 116 tok/s on 18.8 GiB of weights, the read bandwidth demanded
approaches the nominal bandwidth of the card. MTP is what allows that rate, by
verifying several tokens per pass instead of one.

Practical consequence: **the remaining margin is structural.** Neither a setting
nor a llama.cpp version will unlock it. A full day of tuning was worth 2 to 4%.
Only a smaller model in memory would move it, at the cost of quality, which is
exactly what the NVFP4 migration then did.

## Not yet exploited

- `presence_penalty 1.5` in instruct mode, while both profiles sit at 0.
- The 1M context advertised on the model card, against the 262,144 allocated
  natively here.
