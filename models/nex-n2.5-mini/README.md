# Nex-N2.5-mini

Sparse MoE, architecture `qwen3_5_moe`: 40 blocks, hybrid attention mixing Gated
DeltaNet linear layers with 10 full-attention layers. Installed and measured
2026-09-10.

**The best quality in the parc**, tied with `tiel` on knowledge and ahead of it
on reasoning, with no speculation of any kind and 37 answers still cut short.

```powershell
.\llm-ctl.ps1 -Action nex
```

| | |
|---|---|
| Weights | `Nex-N2.5-mini.i1-Q4_K_M.gguf`, 19.71 GiB |
| Vision projector | `mmproj-Nex-N2.5-mini-F16.gguf`, 0.84 GiB |
| Context | 262,144, declared by the GGUF itself |
| KV cache | `q8_0` |
| VRAM | **27,089 MiB measured**, against 24,000 computed. The formula underestimates by about 3 GiB, same gap and same direction as on Spark |
| MMLU | **433/500, 86.6 %**, and that is a floor: 37 answers were still truncated, so 433 of 463 finished, 93.5 % |
| GSM8K | **58/60** |
| Decode | **215.1 tok/s**, no speculation. `tiel` without its MTP head does 198.3 |
| Long-context recall | **6/6**, needle at 10/50/90 % depth, at 176,080 then 243,969 tokens |
| Build | `b10883` (2026-09-09) |
| Speculation | None. See below, this is the model's main cost |

## Where the weights come from, and why not from the obvious place

Two repositories publish this quantisation at the same size to the byte,
`abenzerps/Nex-N2.5-mini-GGUF` and `mradermacher/Nex-N2.5-mini-i1-GGUF`. The
second also publishes the importance matrix used to quantise, as
`Nex-N2.5-mini.imatrix.gguf`; the first only declares one in the GGUF header.
At equal size and equal quantisation, the verifiable chain was preferred.

The projector comes from `abenzerps`. It was fetched before that arbitration,
it is the same format at the same size, and mradermacher's static repository
carries an equivalent pair.

## The MTP head that is declared and not shipped

`config.json` declares `mtp_num_hidden_layers: 1`, which reads as a
multi-token-prediction head, the same trick that makes `tiel` and `kat` fast.
The safetensors index carries **zero** MTP tensors and exactly 40 layers, 0 to
39. Verified at the source on 2026-09-09 by reading
`model.safetensors.index.json` directly.

So there is no speculative decoding to be had here, and that is the real price
of preferring this model to `tiel`: same family, no drafter.

Should the loader trip on that declaration, the workaround is:

```
--override-kv qwen35moe.block_count=int:40,qwen35moe.nextn_predict_layers=int:0
```

It is not in the profile, because a workaround for a failure that has not
happened is a lie in the code.

## Two open defects to know about before trusting it

**Vision, llama.cpp issue 27931, open.** The server crashes, stack overflow or
access violation, on this family of hybrid recurrent models with a projector
loaded, when text and image turns **alternate** in one conversation. The log
fills with warnings about non-consecutive token positions first. A single image
in a single turn may well pass, which is precisely why the projector was fetched
rather than skipped: this gets settled by a trial, not by reading.

The proposed fix, pull request 28007, falls back to reprocessing the whole
prompt when the memory rollback fails. It is **open and not merged** as of
2026-09-10.

**Silent stop at long context, llama.cpp issue 27756.** Generation ends without
an end-of-sequence token past a certain depth. Not monotonic with model size,
and the cross-check is worth keeping: a 35B-A3B with 30 Gated DeltaNet layers
passes 243k while a 27B with 48 such layers fails. This model has exactly 30
linear layers, which puts it on the good side of that single data point. One
data point.

## What is not known

No third-party speed measurement exists on these weights. No
needle-in-a-haystack. No successful vision deployment under llama.cpp. No
reproducible comparison against anything. Every figure in circulation is
self-reported by the publisher. Whatever this box measures will be the first
public number on this model.
