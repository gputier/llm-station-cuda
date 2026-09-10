# Spark-X2.5-4B

Dense 4B, architecture `spark2_5`: hybrid attention, most layers on a 512-token
sliding window, 9 full-attention layers, with a per-head sigmoid attention gate.
Installed and measured 2026-09-10.

```powershell
.\llm-ctl.ps1 -Action spark
```

| | |
|---|---|
| Weights | `Spark-X2.5-4B-Q8_0.gguf`, 4.07 GiB |
| Vision projector | None published |
| Context | 262,144, confirmed by `/props` |
| KV cache | `q8_0` |
| VRAM | **13,156 MiB measured**, against 9,000 computed. See below |
| Decode | 226 to 231 tok/s |
| MMLU | **334/500, 66.8%** |
| GSM8K | **46/60** |
| Build | `b10883` (2026-09-09). **b10828 is the minimum**, see below |

## Last on both measures, including the one it was meant to win

Measured 2026-09-10, temperature 0, seed 42, the same 500 MMLU and 60 GSM8K set
that ranked the production models:

| Model | Parameters | MMLU | GSM8K |
|---|---|---|---|
| `tiel` | 35B-A3B | 82.2% | 58/60 |
| `qwen` | 27B | 82.0% | 52/60 |
| `ornith` | 9B | 73.0% | 53/60 |
| `spark` | 4B | 66.8% | 46/60 |

Fifteen points under the production models on knowledge was expected at this
size. Last on GSM8K was not: Ornith had shown that a small model can reason as
well as one three times its size, and the hope was that Spark would do the same
one rung down. It does not. Reasoning does not survive the drop to 4B the way
knowledge-free arithmetic sometimes does.

It is also not fast for its size: 226 tok/s in decode, against 210 for `tiel`,
a 35B-A3B model with speculation. Eight times fewer parameters buy 8% of decode.

## It is a reasoning model, and that has a practical cost

The answer arrives in `reasoning_content`, separate from `content`. Ask for 200
output tokens and you get 200 tokens of reasoning and an **empty** `content`,
which reads as a broken model and is nothing of the sort. Budget the output
generously, or disable thinking through `chat_template_kwargs` as the bench
does.

## The measured VRAM is 3 GiB above the computed budget

13,156 MiB against the 9,000 computed from the architecture. The same gap in the
same direction appeared on Nex the same day, so the error is in the method, not
in the model: the per-token cache arithmetic ignores something systematic,
probably the compute buffers sized from `-b 4096 -ub 2048`. Trust the
measurement, not the formula, and do not size a cohabitation on the formula.

## Do not compile the fork

The model card tells you to build `XHToken/llama.cpp`. Do not. Support landed
upstream in pull request 27868, and that fork is now obsolete: 15 commits ahead,
all of them merged upstream, and 385 behind.

The minimum official build was found by comparing commits: b10826 is two commits
ahead of the merge, b10827 one, **b10828 is the first release that carries it**.
The production build on this box is b10826, which is why this model needed a new
engine at all. An engine that does not know an architecture does not say so
clearly, it fails at load.

## The 4B model costs more cache per token than the 35B one

Worth stating plainly because it is the opposite of the intuition. Nine
full-attention layers with 4 KV heads at head_dim 256 cost about 18 KiB per
token in `q8_0`. Nex, ten times the parameters, has ten full layers with 2 KV
heads and costs about 10 KiB. The sliding-window layers add a fixed 57 MiB and
then stop growing, which is exactly what makes the rest affordable.

Consequence: **never** pass `--swa-full` to this profile. It would hold every
layer at full width and throw away the only reason the cache fits.

## Why 262,144 and not the advertised million

A window the server accepts says nothing about what the model still finds inside
it. The profile serves 262,144 until recall is measured on these weights, the
same discipline that caught `muse` promising a million it no longer served.

## Vulkan

Studied but not installed. The other station, an RX 5700 XT on Vulkan, is RDNA1
and has no `VK_KHR_cooperative_matrix`, so matrix operations fall back to the
scalar path. At 4.07 GiB the weights would fit in its 8 GiB, but the cache would
not leave room for a useful window on top. Nothing was attempted there.
