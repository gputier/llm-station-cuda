# Ornith-1.5-9B (MIT)

Dense 9B, the small sibling of the `tiel` backbone. Best capability-per-byte on
this box: 12,441 MB of VRAM, a third of the others, leaving room to run
something else alongside. A fast second-opinion and short-task model, not a
replacement for `tiel` or `qwen`.

```powershell
.\llm-ctl.ps1 -Action ornith
```

| | |
|---|---|
| Weights | `Ornith-1.5-9B-Q5_K_M.gguf` |
| Vision projector | `mmproj-Ornith-1.5-9B-BF16.gguf` |
| Context | 262,144 |
| KV cache | `q4_0` |
| VRAM | 15,234 MB in production. Bench figures below exclude the projector: 11,648 MB without speculation, 14,179 MB with. |
| Build | `b10826` (2026-09-06), moved here 2026-09-08. See below, the move bought no speed. |

## Equal reasoning, far less knowledge

Measured 2026-09-01, same protocol and question set as `tiel`:

167.73 tok/s decode, 10,497 tok/s prefill, 12,441 MB of VRAM with the projector
loaded, which is the figure the header quotes. On the quality set, against
`qwen`, a 27B model: MMLU 73.0% against 82.0%, GSM8K 53/60 against 52/60.

Read that pair the right way: equal reasoning, far less knowledge. It knows much
less than the 27B model and answers word problems just as well.

## The MTP head was already in the weights, and nobody had switched it on

Speculation went on 2026-09-08. The draft head is not a new file: these exact
weights carry `blk.32.nextn.*` tensors, and llama-server had been logging

```
W model has unused tensor blk.32.nextn.eh_proj.weight (size = 23068672 bytes) -- ignoring
```

and dropping them since the profile was created, because `--spec-type` was
never passed. Measured the day it was fixed, `bench.ps1`, 38,000-token prompt,
seed 42, 3 runs, median:

| | before (dropped) | after (`--spec-type draft-mtp --spec-draft-n-max 2`) |
|---|---|---|
| Decode | 167.18 tok/s | 179.64 tok/s (+7.5%, acceptance 58.4%) |
| Prefill | 10,721 tok/s | 7,715 tok/s (-28%) |
| VRAM | 11,648 MB | 14,179 MB (+2,531, projector excluded) |

Taken because this profile answers short questions, where the head earns its
memory, and gives up prefill it rarely uses.

**The downloaded file loses to the weights already on disk.**
`Ornith-1.5-9B-MTP-BF16-ASHQ1-6500`, fetched to answer the exact same
question, runs at 164.25 tok/s with 49.0% acceptance: slower than the
production weights with speculation on, and it was not kept.

This repository had already written the general rule, on 2026-08-31, about
`qwen`: "the `blk.*.nextn.*` tensors are already inside the quant; llama.cpp
loads them and ignores them unless you pass `--spec-type draft-mtp`." It was
never replayed against `ornith` until 2026-09-08. A finding about one model is
worth a pass over the others the same day.

## Build: b10826, moved for consistency and not for speed

`ornith` was the last profile owing itself to the 2026-08-27 NVFP4 build,
although its Q5_K_M weights never touched an NVFP4 kernel. Control against
`b10826` with the production argument list unchanged, only the binary
varying: 37,981-token prompt, seed 42, temperature 1.0, thinking off, 3 runs,
median decode:

| Build | Decode | Prefill | VRAM |
|---|---|---|---|
| 2026-08-27 | 166.32 tok/s | 10,682 tok/s | 12,696 MB |
| b10826 | 167.44 tok/s | 10,796 tok/s | 12,697 MB |

0.7% and 1.1%: noise on three runs. Moved anyway: seven build directories sit
on this disk and each has to earn its keep. The 2026-08-27 build now justifies
itself through `qwen` alone, which genuinely needs its NVFP4 kernels; b10826
is an official binary, unzipped, not a local compilation.
