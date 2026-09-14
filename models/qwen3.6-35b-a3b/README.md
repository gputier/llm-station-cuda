# Qwen3.6-35B-A3B (Apache 2.0)

Sparse MoE from Qwen, architecture `qwen35moe`: 41 blocks counting the MTP
layer, 256 experts, 8 active per token, 2 KV heads of 256 dimensions on the ten
full-attention layers, one every four. Served on the **16 GB box only**, from
[../../llm-ctl-16gb.ps1](../../llm-ctl-16gb.ps1). It replaced the dense
Qwen3.8-27B there on 2026-09-14 and sits next to `tiel` on the same card.

```powershell
.\llm-ctl.ps1 -Action qwen36    # the 16 GB box's copy of llm-ctl-16gb.ps1
```

| | |
|---|---|
| Weights | `Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf`, 14,069,266,720 bytes, from `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` |
| Vision projector | `mmproj-BF16.gguf` on disk, not loaded: this box does text |
| Context | 262,144, the GGUF's declared window |
| KV cache | KVarN 3 for the target and for the MTP draft |
| Offload | `--n-cpu-moe 2` and `--load-mode none` |
| Speculation | MTP head inside the GGUF, `--spec-draft-n-max 2` |
| Build | BeeLlama v0.4.6, the one build on that box |

The header was read before any load on 2026-09-14: `nextn_predict_layers = 1`,
four `blk.40.nextn` tensors, `head_count_kv = 2`, `key_length = 256`,
`full_attention_interval = 4`.

## Why a 35B-A3B on a 16 GB card

About 3B of the 35B parameters work per token, so far fewer bytes are re-read
per token than on a dense 27B, and the attention cache is 3.2 times smaller per
token (10 layers x 2 heads against 16 x 4). Measured on 2026-09-14, same
6,018-token prompt, before any offload:

| | Decode | Prefill | VRAM |
|---|---|---|---|
| Qwen3.8-27B UD-IQ3_XXS, the model it replaced | 72.1 tok/s | 1,394 tok/s | 15,851 MiB |
| Qwen3.6-35B-A3B UD-IQ3_XXS | 137.1 tok/s | 2,684 tok/s | 15,861 MiB |

Quality on the 500 MMLU questions and 60 GSM8K problems of every campaign in
this repository, temperature 0, thinking disabled: **90.2% and 56/60**, no empty
answer, 27.2 minutes of bench. `tiel` on the same card scores 85.8% and 55/60 in
10.9 minutes. The 4.4-point gap sits under the 4.5 points that separate two
models on 500 questions, on a public set this repository already caught
flattering 9B models, so both stay. qwen36 writes much longer answers with
thinking disabled, which the bench time shows at equal decode speed.

## The two offload flags, and what they cost

In real use on 2026-09-14 decode fell in one step from 80 to 21 tok/s at 200k
tokens of context and stayed there until a restart, with the card full. No
bench reproduced it and the cause is not proven. The profile now keeps more
room on the card:

| At 196,613 tokens | Decode | Prefill | Card | Host RAM free |
|---|---|---|---|---|
| no offload | 79.2 tok/s | 2,007 tok/s | 15,890 MiB | not read |
| `--n-cpu-moe 2` | 68.9 | 1,788 | 15,428 | 5,645 MiB |
| `--n-cpu-moe 2 --load-mode none`, in service | 71.9 | 1,910 | 15,446 | 16,838 MiB |

Short-prompt decode in service: 132.8 tok/s. Three layers of experts and
`--fit on` with a 1 GB target were measured and set aside, see
[../../docs/tuning-log.md](../../docs/tuning-log.md), entry of 2026-09-14.

## Traps specific to this model

**Its embedded template needs no derivation.** It accepts a system message after
the first user turn, checked with a direct request and then through Claude
Code. Qwen3.8-27B's raised `System message must be at the beginning` on the
same input and needed a derived template.

**The wait before each prompt grows with the conversation.** On a long agent
session: 5.7 s at 93k tokens, 13.6 s at 150k, 26 s at 200k, before the first
512-token block, then about 1,400 tok/s. It did not move when decode dropped,
so it is a cost of depth, probably the KVarN cache unpacked at the start of each
prompt. Not proven. Compacting earlier shortens it.

**A stray `</think>` closed one answer out of two through Claude Code** on
2026-09-14. The OpenAI endpoint separates reasoning correctly (`content` "4",
reasoning in `reasoning_content`), so the fault came through the Anthropic
endpoint. Cause not investigated yet.

**`-Extra` cannot take `--n-cpu-moe` back.** `-Extra "--n-cpu-moe 0"` on this
profile leaves both values in the command line and the offload in force. To try
the model without offload, start the server without the flag.

Sampling from unsloth's page for the thinking mode: temperature 1.0, top-p
0.95, top-k 20, min-p 0. The page also lists `presence_penalty 1.5`, left unset
so that benches compare like with like.
