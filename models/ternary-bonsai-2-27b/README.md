# Bonsai 2 27B

A ternary quantisation of Qwen3.8-27B by PrismML, and the successor to
`ternary-bonsai-27b`, which quantised Qwen3.6-27B. Weights in {-1, 0, +1} with
one FP16 scale per group of 128, 1.72 bits each counting everything.
Twenty-seven billion parameters in 6.71 GiB. Installed and measured 2026-09-18.

**Ingestion is sixty times what the first generation managed**, 3,347 tok/s
against 56, measured on the same prompt by the same bench on the same day.
Generation is a wash, 97.2 against 102.6. For a client that re-reads a long
conversation at every turn, that one figure decides between the two.

```powershell
.\llm-ctl.ps1 -Action bonsai2
```

| | |
|---|---|
| Weights | `Ternary-Bonsai-2-27B-PQ2_0.gguf`, 6.71 GiB |
| Vision projector | `Ternary-Bonsai-2-27B-mmproj-BF16.gguf`, 0.87 GiB |
| Speculation | **None available.** The repository publishes no drafter, and the first generation's does not transfer, see below |
| Context | 262,144 |
| KV cache | `q8_0` |
| VRAM | 20,282 MiB, projector included |
| Decode | **97.2 tok/s** (median of 3) |
| Prefill | **3,347 tok/s** (median of 3) |
| Build | `prism-b10685-7dffb15` (2026-09-15), PrismML's fork |
| Quality | Not measured here yet. Its authors publish 84.78 across 14 thinking-mode benchmarks, 98.2 % of the FP16 baseline |

## The one binary on this box that upstream did not build

Bonsai 2 stores its weights in a Hadamard-rotated basis and the runtime applies
the matching transform to activations. That transform is not in mainline
llama.cpp, so `PQ2_0` and `PTQ1_0` are refused outright as unknown types. The
authors warn of something worse than a refusal: their `Q2_0` band loads on a
stock build without a warning and produces gibberish, which is why they keep it
in a separate repository.

There was no third option. The first generation shipped a `Q2_g64` file meant
for upstream builds and this one does not, so the fork is the price of running
the model at all.

What the standing rule protected is preserved: the fork publishes release
archives, so this was unpacked like every other engine here and nothing is
compiled on this machine. The cost is that the fork tracks upstream at its own
pace, `b10685` where the neighbouring profiles run `b10883`. No other profile
was moved onto it.

## PQ2_0 and not PTQ1_0, on this card and no other

The two packs hold the same weights. `PTQ1_0` packs trits densely, 1.75 bits and
5.95 GB; `PQ2_0` gives each trit a 2-bit slot, 2.13 bits and 7.21 GB, and its
unpacking is cheaper.

Neither is uniformly faster, and the authors measure both on an RTX 5090: 129.9
tok/s decode against 120.5, and 3,893 tok/s prompt processing against 1,805.
Dense packing wins on the Ada parts and the L4, where memory bandwidth is the
binding constraint. It is not the constraint here, so the larger file is the
right one and costs 1.26 GB.

## The drafter question, settled by measurement

The upstream demo repository states that drafters are target-specific. That was
tested rather than believed, on 2026-09-18.

Bonsai 1 publishes a dspark drafter; Bonsai 2 publishes none. The published
`dspark-Q4_1` file is a pre-migration packing that no current binary loads, which
is the real cause of llama.cpp issue 26337 and of this profile's twin failing on
2026-09-10. The fork ships the converter that fixes it, `gguf-dspark-to-dflash`,
and it takes the target model as tokenizer donor, so the first generation's
drafter can be converted against this one.

It converts, it quantises to 592 MiB, the server loads it and speculation
engages. Then:

| | Without | With the converted drafter |
|---|---|---|
| Decode | 98.3 tok/s | **44.9 tok/s** |
| Prefill | 3,349 tok/s | 2,901 tok/s |
| Acceptance | - | **6 of 2,028, 0.3 %** |
| VRAM | 16,184 MiB | 24,741 MiB |
| Spill | 1,508 MiB | 10,780 MiB |

Both runs used a `q4_0` cache, which is why the first column differs from the
profile. The drafter guesses nothing and is paid for every time. The claim was
correct.

## `q8_0` and not the demo's `BONSAI_KV4`

The upstream demo offers `BONSAI_KV4=1` for long contexts, which passes
`--cache-type-k q4_0 --cache-type-v q4_0`. Measured here the same day: 98.3
tok/s against 97.2 and identical ingestion, both inside the noise, for 4,098 MiB
of VRAM freed.

It was not taken. The memory buys nothing on this box, 12 GB being free either
way and 262,144 already the model's own ceiling, and the demo's own page says
the K cache loses a little accuracy at 4 bits unless a calibration bias is built
with `llama-kv-mean-center` and kept in step with the weights. Paying in quality
and in maintenance for headroom nobody needs is the wrong trade. On a 16 GB box
it would be the right one.

## L'écart Blackwell : 97 contre 130 tok/s, expliqué et non corrigé

La fiche PrismML annonce 129,9 tok/s en décodage sur RTX 5090 pour `PQ2_0`,
contre 97,2 tok/s mesurés ici, un écart de 25 %. Deux questions se posaient :
le moteur porte-t-il des noyaux CUDA natifs pour Blackwell (`sm_120`), et d'où
vient l'écart. Investigation menée le 2026-09-18, rien n'a été changé au
profil.

`cuobjdump --list-elf` sur `ggml-cuda.dll` du build `prism-b10685-7dffb15`
liste, pour chaque noyau, des variantes `sm_86`, `sm_89`, `sm_120a` et
`sm_121a`. La carte annonce `compute_cap 12.0` par `nvidia-smi`. Les noyaux
natifs pour cette architecture sont donc déjà présents et utilisés ; rien à
remplacer côté binaire.

L'écart tient à la profondeur de contexte, pas à un réglage manquant. La fiche
PrismML précise que ses 129,9 tok/s viennent de `llama-bench` en « batch size 1
and depth 0, no vision tower », un décodage mesuré depuis un contexte quasiment
vide. Ce profil sert 262 144 tokens de contexte avec le projecteur de vision
chargé, et le banc de ce dépôt mesure le décodage après ingestion d'une invite
réelle d'environ 56 000 caractères, où l'attention complète coûte bien plus
cher par token. Preuve : la même invite tronquée à 500 tokens environ a rendu
130,4 tok/s, quasiment le chiffre publié ; un appel `/v1/chat/completions` réel
à 105 tokens de prompt a mesuré 129,26 tok/s indépendamment. L'écart est donc
structurel, pas un défaut de ce profil.

Trois réglages ont été essayés sur l'invite complète et rejetés, aucun gain de
décodage hors bruit de mesure : `--no-cont-batching` (102,6 tok/s), `-ub 4096`
(102,8 tok/s, au prix de 1 416 MiB de VRAM et 1 104 MiB de débordement
supplémentaires) et `--no-mmproj-offload` (102,6 tok/s, ne libère que la VRAM
du projecteur sans toucher au décodage). Détail chiffré dans
[docs/tuning-log.md](../../docs/tuning-log.md).

## The chat template had to be patched, as it did twice before

Claude Code puts system turns in the middle of a conversation. This model's
template raises `System message must be at the beginning.` and the server
answers 500 before generating anything, measured 2026-09-18 on the first
request.

`chat-template-system-anywhere.jinja`, next to the weights, is this model's own
template with one line changed: where it raised, it emits a system block. The
`qwen3.8-27b` file two profiles up does almost the same thing and was not reused
although Bonsai 2 derives from that base: it is Unsloth's rework and carries a
developer role, tool-call argument validation and a `high` to `xhigh` mapping
that this model never declared.

The same defect was found on the first generation the same day and fixed the
same way. It had been there since 2026-09-10 without anyone noticing, because
the bench campaign drove the raw endpoint and never a client.

## Sampling

Temperature 1.0, top-p 0.95, top-k 20, min-p 0. These are the thinking-mode
values from the model card, and thinking mode is what produced its published
figures. The card gives a second set for instruct mode, temperature 0.7 and
top-p 0.80, which this profile does not use.

The model thinks by default at `xhigh` effort. Its authors state that `low` is
not supported and behaves close to `xhigh` when asked for; `medium` is the one
real alternative.

## What is not known

Its quality on this bench. The campaign of 2026-09-10 is what every published
figure in this repository rests on, and this model has not been through it: the
MMLU and GSM8K rows are missing on purpose rather than filled from the authors'
own numbers, which were measured elsewhere on another protocol.

Long-range recall, the same caveat the first generation carries. Compression
this aggressive tends to show first on what a model still finds deep in its
window, and nothing here has tested it.
