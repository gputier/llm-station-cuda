# Spark-X2.5-4B

Dense 4B, architecture `spark2_5`: hybrid attention, most layers on a 512-token
sliding window, 9 full-attention layers, with a per-head sigmoid attention gate.
Installed 2026-09-10 as an agentic candidate. Nothing below the header has been
measured on this box.

```powershell
.\llm-ctl.ps1 -Action spark
```

| | |
|---|---|
| Weights | `Spark-X2.5-4B-Q8_0.gguf`, 4.07 GiB |
| Vision projector | None published |
| Context | 262,144 served. The model card claims a million |
| KV cache | `q8_0` |
| VRAM | About 9 GiB, COMPUTED not measured: 4,170 MiB of weights, roughly 4,800 of cache at full window |
| Build | `b10883` (2026-09-09). **b10828 is the minimum**, see below |

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
