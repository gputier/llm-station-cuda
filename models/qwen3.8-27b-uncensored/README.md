# Qwen3.8-27B Uncensored

An abliterated variant of [qwen3.8-27b](../qwen3.8-27b/), deployed **alongside**
the aligned model and not in its place. The aligned profile stays the reasoning
and coding model; this one is only loaded when the aligned version refuses a
legitimate task.

```powershell
.\llm-ctl.ps1 -Action qwenu
```

| | |
|---|---|
| Weights | `Qwen3.8-27B-Uncensored-Q5_K_M.gguf`, 18.19 GiB |
| Vision projector | `mmproj-Qwen3.8-27B-Uncensored-F16.gguf`, 0.86 GiB |
| Context | 262,144 |
| VRAM | 29,019 MB of 32,607 |
| Throughput | 125.21 tok/s decode, 3,354 tok/s prefill |
| Build | upstream 2026-08-11 |

## The vision projector was renamed upstream, and the local file follows

Checked on 2026-08-31. On 2026-08-28 at 20:04 the repository renamed
`Qwen3.8-27B-Uncensored-vision-f16.gguf` to
`mmproj-Qwen3.8-27B-Uncensored-F16.gguf`, so that llama.cpp discovers it from
the `mmproj-` prefix. Same content, 927,606,912 bytes, and the table above
carries the current upstream name.

The local weights were renamed to match on 2026-08-31, size verified identical
to the byte, and `llm-ctl.ps1` now points at the new name. A fresh download and
an existing install therefore land on the same filename.

**`llm-ctl.ps1` still passes the old name to `--mmproj`.** That is deliberate
here, because the weights on this box were downloaded before the rename and the
path is explicit. **If you are setting this up now, you will download the new
name and the action will fail on a missing file**: either rename your local copy
or edit the `--mmproj` line. Nothing else in the profile depends on it.

The three commits of 2026-08-29 on that repository touch the README only, so
checksums taken before it are still valid.

## The launch profile is copied verbatim, on purpose

MTP `n-max 3`, `q4_0` cache, `-ub 2048`, `top_k 20`, `--min-p 0`. Every
measurement that justifies these lives in [../qwen3.8-27b/](../qwen3.8-27b/).
Do not re-derive them here.

One value now differs, and deliberately: `qwen` moved to `n-max 4` on
2026-08-31 after a sweep at full context. This profile stays at 3 because it was
not re-swept, and it is the fallback rather than the daily driver. Re-sweep it
before assuming 4 transposes.

Retuning was nonetheless **measured separately rather than transposed**, and it
gave 120.90 to 125.21 tok/s decode (+3.6%) and 3,210 to 3,354 tok/s prefill
(+4.5%). Same direction as the aligned profile, different magnitude.

## Two file-level differences, and they are the only ones

**Standard Q5_K_M (18.19 GiB) rather than a Dynamic quant**, because no
abliterated repository publishes one. Slightly smaller, so the VRAM budget holds
as is. Q6_K (20.89 GiB) would push the total to 30.4 GB, above the ~29 GB where
throughput collapses on this card: rejected without further testing.

**The quant ships with the MTP head.** This repository was chosen because it was
the only one of four abliterated candidates to publish the `-noMTP-` and MTP
sets side by side, which is evidence the speculation head was handled rather
than stripped by accident. The `-noMTP-` variants are 0.28 GiB smaller, so a
size check tells you which one you have.

Re-prove speculation on any change by checking for `draft_n` in the `timings` of
a response.

## The chat template is shared with the aligned model

The two GGUFs do **not** embed the same template. The aligned build carries a
9,993-character version that merges multiple system messages and knows the
`developer` role; this repository carries an earlier 8,952-character one that
knows neither.

Both raise the same `System message must be at the beginning` as soon as a
system message follows a user message, so both are unusable by an agentic client
as shipped. This profile therefore loads the derivative of the **more recent**
one, already proven in production.

**Consequence worth knowing: deleting the aligned model's directory breaks this
action**, because that is where the shared template file lives.

## What was verified at deployment

Abliteration proven on 3 requests the aligned model typically refuses, zero
refusals. Vision proven. MTP active (`draft_n` 810, 394 accepted, ~49%).
Non-regression of the aligned path verified by reloading it afterwards.
