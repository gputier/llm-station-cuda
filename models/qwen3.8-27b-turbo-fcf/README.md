# Qwen3.8-27B TURBO Fable Cold-Fusion Heretic (candidate)

A candidate to replace [qwen3.8-27b-uncensored](../qwen3.8-27b-uncensored/),
fetched on 2026-09-19 and **not yet benched**. Nothing in service points at it.

```powershell
.\llm-ctl.ps1 -Action qwenf
```

| | |
|---|---|
| Repository | `DavidAU/Qwen3.8-27B-TURBO-Fable-Cold-Fusion-735-882-Heretic-Uncensored-NEO-CODER-MAX-MTP-GGUF` |
| Weights | `Qwen3.8-27B-TurboFCFusion-735-882-Here-Uncen-NEO-CODER-MAX-MTP-Q5_K_M.gguf`, 21,182,273,056 bytes |
| SHA-256 | `7408f59414a436b1ac5dcf33cdc663596fc66a84786f59f835641374b658b87d` |
| Vision projector | `mmproj-F16.gguf`, 927,606,976 bytes |
| SHA-256 | `82e620db8cb83267e9775e5aad3e8d8aa5af7ace825e94c52d3d730eb35af88a` |
| Architecture | `qwen35`, 65 blocks, one MTP layer (`nextn_predict_layers 1`) |
| Context | 262,144 |
| Build | upstream 2026-08-11, same as `qwenu` |

Both checksums were computed on the station after the download and match the
ones Hugging Face publishes.

## What the card claims, and why none of it is taken as given

A merge of two fine-tunes: a Qwen3.6-27B lineage (Fable Fusion 711 Heretic) and
a retrained Qwen3.8-27B (Cold-Fusion GAIN), de-censored by ablation. The card
reports 11 refusals out of 100 against 86 for the original, at a divergence of
0.0025, and ARC-C 0.735 against 0.591 for the base model.

"TURBO" claims half to a tenth of the thinking tokens for the same output. That
is the only claim that matters here: `qwenu` took 25.9 min on the 2026-09-10
bench for 77.0% MMLU, and the fastest model of the parc took 3.5.

The quoted scores are multiple-choice log-likelihood tasks. None measures code,
despite "CODER" in the name, and a gain of fourteen points on ARC is the shape a
contamination produces. The unpublished set of 2026-09-11 exists for exactly
this case.

## Two things found in the file itself

`general.name` reads `Qwen3.8 27B Brainwaves NM HERETIC BR LOA1`, not the
repository name. Harmless for serving, worth knowing before trusting a
provenance chain.

The embedded chat template is the author's, 8,953 characters, and it knows both
`enable_thinking` and `reasoning_effort`. The card says its default effort is
`xhigh`. The profile does NOT load it: it loads the template `qwenu` uses, so
that the bench compares weights and not templates. A copy of the embedded one
sits next to the weights on the station as `chat-template-embedded-author.jinja`.

## The quant, and the one number to check first

Q5_K_M with MTP, the same quant as `qwenu`. Q6_K was rejected for the reason
`qwenu` rejected it: it crosses the ~29 GB above which throughput collapses on
this card. This file is still 1.5 GiB heavier than the one `qwenu` loads, so
read VRAM at load before trusting any speed figure.

The author warns that below 50% MTP acceptance the plain quant is faster. Read
`draft_n` and `draft_n_accepted` in the timings, but decide on throughput: on
this box a higher acceptance has already come with a lower speed.

## The bench it is waiting for

Run with the station free, since each step reloads the port:

```powershell
# public set, same protocol as 2026-09-10, both profiles in one pass
.\bench\banc-tous.ps1 -Profils qwenf,qwenu
# unpublished set, reasoning on, one profile loaded at a time
.\llm-ctl.ps1 -Action qwenf ; .\bench\banc-inedit.ps1 -Label qwenf -Reflexion
.\llm-ctl.ps1 -Action qwenu ; .\bench\banc-inedit.ps1 -Label qwenu -Reflexion
```

It replaces `qwenu` only if it beats it on the unpublished set. A win on the
public set alone decides nothing.
