# Ternary-Bonsai-27B

A ternary quantisation of Qwen3.6-27B by PrismML: weights in {-1, 0, +1} with
group-wise FP16 scaling, about 1.71 bits each. Twenty-seven billion parameters
in 7.06 GiB. Installed and measured 2026-09-10.

**The best quality-per-byte in the parc, by a wide margin.** Three points under
the best model of the day for a third of its weight, and the only candidate whose
footprint leaves room for a second model on the card.

```powershell
.\llm-ctl.ps1 -Action bonsai
```

| | |
|---|---|
| Weights | `Ternary-Bonsai-27B-Q2_g64.gguf`, 7.06 GiB |
| Vision projector | `Ternary-Bonsai-27B-mmproj-BF16.gguf`, 0.87 GiB |
| Speculation | **None.** Its own drafter prevents the model from loading at all, see below |
| Context | 262,144 |
| KV cache | `q8_0` |
| VRAM | Weights and projector, the drafter having been dropped |
| MMLU | **418/500, 83.6 %**, zero empty answers |
| GSM8K | **59/60**, second only to kat's perfect score |
| Decode | **102.6 tok/s**. Half of tiel, which is expected: 27B dense works every parameter per token where the MoE models activate three billion of thirty-five |
| Build | `b10883` (2026-09-09) |

## Q2_g64 and not PQ2_0

The repository publishes both, and they are the same model packed two ways.
`PQ2_0` is a group-128 pack for PrismML's own fork of llama.cpp. `Q2_g64` is the
variant meant for upstream builds, and `GGML_TYPE_Q2_0` is type 42 in the
upstream master, verified in `ggml.h`.

Taking PQ2_0 would mean compiling and maintaining a fifth engine on this box,
against the standing rule that it runs official release binaries. It was not
taken.

## The drafter is expected to fail

DSpark is the model's own semi-autoregressive drafter, a DFlash path plus a
Markov head, announced by its authors at 1.34x. The upstream flags exist and
were verified in `common/arg.cpp` and `common/speculative.cpp`:
`--spec-type draft-dspark` with `-md`.

llama.cpp issue **26337** reports it refusing to load:

```
tensor 'dspark.fc.weight' has offset 337718592, expected 357584192
failed to read tensor data
```

Open, labelled bug-unconfirmed and stale, no maintainer answer, no linked pull
request, no workaround. Reported against b10197 and untested on b10883.

If it fails at startup, drop `--spec-type`, `--spec-draft-n-max` and `-md` from
the profile. The model still serves; only the speed claim goes.

**It did fail, on 2026-09-10.** The profile was loaded during the bench campaign
and never answered: 420 seconds, no health, the runner moved on to the next
model. So the defect reported against b10197 in February is still there in
b10883, seven months and several hundred releases later.

### There is a replacement, and it does not come from the model's author

Bonsai is a quantisation of Qwen3.6-27B, and that base has drafters published by
other people, already converted to GGUF: `spiritbuun/Qwen3.6-27B-DFlash-GGUF`
and `Ardenzard/Qwen3.6-27B-DFlash-GGUF`, plus an EAGLE3 at
`wimmmm/Ex0bit-Qwen3.6-27B-PRISM-EAGLE3-GGUF`. They are usable here because the
whole Qwen3.5 and 3.6 family shares one 248,320-token vocabulary, which is the
condition that makes a drafter transferable at all.

The caveat is the same one that applies to Nex: these are trained on the **base**
model, not on this ternary derivative. A drafter that predicts what the original
would say, where the derivative says something else, gets rejected. Only the
decode rate settles it.

## Sampling

Temperature 0.7, top-p 0.95, top-k 20, straight from the model card. Unlike the
Qwen profiles there is no second recommended value for general use: 0.7 is what
the authors used for their own benchmark runs and it is all they publish.

## Two other reported failures, for context

Issue 25727, "Ternary Bonsai 27B don't run", is open but carries a single
sentence, no log and no reproduction: noise, not evidence. Issue 26073 reports
the Q2 quantisation failing on Metal, which does not apply to a CUDA box.

## What is not known

Whether ternary weights hold long-range recall. Quality lost to quantisation
tends to surface first on recall at depth, and a 27B model compressed to 1.71
bits per weight is the most aggressive compression on this machine by a wide
margin. Treat long sessions with suspicion until a needle-in-a-haystack says
otherwise.
