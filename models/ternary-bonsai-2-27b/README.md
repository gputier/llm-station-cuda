# Bonsai 2 27B

A ternary quantisation of Qwen3.8-27B by PrismML, and the successor to
`ternary-bonsai-27b`, which quantised Qwen3.6-27B. Weights in {-1, 0, +1} with
one FP16 scale per group of 128, 1.72 bits each counting everything.
Twenty-seven billion parameters in 6.71 GiB. Installed and measured 2026-09-18.

**Ingestion is sixty times what the first generation managed**, 3,347 tok/s
against 56, measured on the same prompt by the same bench on the same day.
Generation was a wash that day, 97.2 against 102.6.

Speculation, on since 2026-09-20, moves both numbers: generation to 141.4 tok/s
and ingestion down to 2,879, which leaves the first generation about fifty times
behind rather than sixty. The two ingestion figures come from bench runs two days
apart, so read that ratio as an order of magnitude and not to the unit. For a
client that re-reads a long conversation at every turn, ingestion still decides
between the two profiles.

```powershell
.\llm-ctl.ps1 -Action bonsai2
```

|                  |                                                                                                                  |
| ---------------- | ---------------------------------------------------------------------------------------------------------------- |
| Weights          | `Ternary-Bonsai-2-27B-PQ2_0.gguf`, 6.71 GiB                                                                      |
| Vision projector | `Ternary-Bonsai-2-27B-mmproj-BF16.gguf`, 0.87 GiB                                                                |
| Speculation      | **DFlash2 drafter, on since 2026-09-20.** 55.5% acceptance, +37% decode, see below                               |
| Context          | 262,144                                                                                                          |
| KV cache         | `q8_0`                                                                                                           |
| VRAM             | 25,463 MiB, projector and drafter included                                                                       |
| Decode           | **141.4 tok/s** (median of 3, with the drafter; 102.9 without)                                                   |
| Prefill          | **2,879 tok/s** (median of 3, with the drafter; 3,259 without)                                                   |
| Build            | built here 2026-09-20 from PrismML's fork carrying upstream DFlash2, see below                                   |
| Quality          | Not measured here yet. Its authors publish 84.78 across 14 thinking-mode benchmarks, 98.2 % of the FP16 baseline |

## Two engines, and the one this runs on was built here

Bonsai 2 stores its weights in a Hadamard-rotated basis and the runtime applies
the matching transform to activations. That transform is not in mainline
llama.cpp, so `PQ2_0` and `PTQ1_0` are refused outright as unknown types. The
authors warn of something worse than a refusal: their `Q2_0` band loads on a
stock build without a warning and produces gibberish, which is why they keep it
in a separate repository.

There was no third option. The first generation shipped a `Q2_g64` file meant
for upstream builds and this one does not, so the fork is the price of running
the model at all.

The fork publishes release archives, so it was first unpacked like every other
engine here, `prism-b10685-7dffb15`. That is no longer what this profile runs.

The drafter above is a DFlash2 file, and both published fork archives refuse it
at load: `wrong number of tensors; expected 81, got 58`, because they do not know
23 of its tensor names. The upstream commit that adds DFlash2, `4a6ad487a` of
2026-08-27, does not port onto the fork as a patch: 36 of its 38 hunks were
rejected, the two trees having diverged too far on these files. The drafter's
author had already done that merge and publishes the exact tree alongside the
weights, so that is what is built here, into
`D:\LLM-Setup\llama-cpp-prism-dflash2`. See
[docs/building-llama-cpp.md](../../docs/building-llama-cpp.md).

Retire that directory once the fork merges upstream DFlash2 and ships an archive
with it. No other profile was moved onto either engine.

## PQ2_0 and not PTQ1_0, on this card and no other

The two packs hold the same weights. `PTQ1_0` packs trits densely, 1.75 bits and
5.95 GB; `PQ2_0` gives each trit a 2-bit slot, 2.13 bits and 7.21 GB, and its
unpacking is cheaper.

Neither is uniformly faster, and the authors measure both on an RTX 5090: 129.9
tok/s decode against 120.5, and 3,893 tok/s prompt processing against 1,805.
Dense packing wins on the Ada parts and the L4, where memory bandwidth is the
binding constraint. It is not the constraint here, so the larger file is the
right one and costs 1.26 GB.

## The drafter question, reopened and settled the other way

On 2026-09-18 this profile ran without speculation and the README said no
drafter existed. Both halves of that turned out to be wrong, and the correction
is worth 37% of decode.

A DFlash2 head trained against this exact target was published on 2026-09-17,
2.06 GB, `Bonsai-2-27B-DFlash2-Q8_0.gguf`. It carries no embedding table of its
own and borrows the target's, which is what keeps it small. Measured here on
2026-09-20, same bench prompt, three runs, median:

|                           | Without     | With the DFlash2 drafter |
| ------------------------- | ----------- | ------------------------ |
| Decode                    | 102.9 tok/s | **141.4 tok/s**          |
| Prefill                   | 3,259 tok/s | 2,879 tok/s              |
| Acceptance                | -           | **426 of 768, 55.5 %**   |
| VRAM                      | 20,297 MiB  | 25,463 MiB               |
| Spill at the end of a run | 1,508 MiB   | 4,132 MiB                |

Prefill pays about 12% for it. On this box's mix that still wins, but a workload
that only ever ingests should not take this.

The z-lab `Qwen3.8-27B-DFlash2-Q4_K_M.gguf` was measured beside it and loses by a
hair, 139.9 tok/s and 53.8% acceptance, while saving 872 MiB. Take it on a card
short of memory, not here.

On the protocol of the validation posted to PrismML pull request 210, same three
prompts at temperature 0 with seed 42 and a 32,768 window, this box returns 231.2
tok/s and 86.0% acceptance on arithmetic, 256.7 and 90.9% on code, 125.5 and
31.1% on prose, against 134.5, 134.4 and 132.9 with no drafter at all. Output is
byte-identical to the undrafted run on arithmetic and on code; prose differs,
which the published validation reports as well and attributes to batching, not to
speculation.

**Do not add pull request 210 to the build this runs on.** That patch is real and
fixes drafters borrowing a Hadamard-folded target's embeddings, but the tree built
here already applies the inverse transform right after the token lookup, in
`src/llama-graph.cpp`. Applying it a second time is measurable: acceptance falls
to 6-10% and decode to 72-81 tok/s, on both drafters. The figures the pull request
reports as its gain are what this build produces without it.

### What the 2026-09-18 measurement actually proved

That day Bonsai 1's published bf16 drafter was converted with
`gguf-dspark-to-dflash` against this model as tokenizer donor. It loaded, and
drafted nothing: 6 tokens accepted out of 2,028, decode halved to 44.9 tok/s from
98.3, 10,780 MiB spilled. The conclusion drawn was that drafters never transfer
between generations.

The measurement stands. The conclusion does not: the engine of that day did not
know the DFlash2 format and could not have used a correct drafter either. What
was proved is that _that_ converted file was useless, not that the idea was.

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
