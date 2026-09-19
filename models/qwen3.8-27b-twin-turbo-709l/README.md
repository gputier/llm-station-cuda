# Qwen3.8-27B TWIN-TURBO Fable Cold-Fusion 709-L (candidate)

A candidate to replace [qwen3.8-27b-uncensored](../qwen3.8-27b-uncensored/),
fetched and benched on 2026-09-19 after [qwenf](../qwen3.8-27b-turbo-fcf/),
its sibling. It scores best of the three on both sets, but nothing in service
points at it yet: its speed at long context has not been measured, and it loads
above the VRAM line where this card slows down.

## Result of 2026-09-19

Same day, same build, same bench scripts as `qwenf` and `qwenu`.

On the public set (thinking off), `qwent` scored 460/500 MMLU and 56/60 GSM8K
in 15.3 min, against 461/500 and 56/60 in 13.5 min for `qwenf` and 450/500 and
57/60 in 33.7 min for `qwenu`.

On the unpublished set (thinking on), `qwent` scored 225/235 (95.7%) with 5
empty answers and no decoy, in 25.3 min. `qwenf` and `qwenu` both scored
221/235. It lost points mainly on the rewritten-rule family (19/24) and got the
units family right to the last item (48/48).

The margin over `qwenu` is 4 items out of 235, one run each, at temperature 0.
It is enough to rank the three, not enough to call the gap large.

VRAM read at load: 31,597 MiB, above the ~29 GB where throughput collapses on
this card. The bench prompts are short, so this figure says nothing yet about a
long conversation. Measure speed at long context before swapping it into
service.

```powershell
.\llm-ctl.ps1 -Action qwent
```

| | |
|---|---|
| Repository | `DavidAU/Qwen3.8-27B-TWIN-TURBO-Fable-Cold-Fusion-709-L-Uncensored-NM-DAU-NEO-MTP-GGUF` |
| Weights | `Qwen3.8-27B-TTURBO-Fable-C-Fusion-709-L-Uncen-NM-DAU-NEO-MAX-MTP-Q5_K_M.gguf`, 21,182,281,216 bytes |
| SHA-256 | `6a5ee76203b8fa12cbfeff7f4dc686d2086f8dce66effc9dc213b3e851073daa` |
| Vision projector | `mmproj-F16.gguf`, 927,607,008 bytes |
| SHA-256 | `cd20ce309b744aab7e3baab5c91cddbadf6daef61626820ae213f734ea3cd81f` |
| Context | 262,144 |
| Build | upstream 2026-08-11, same as `qwenu` and `qwenf` |

Both checksums were computed on the station before the bench and match the ones
Hugging Face publishes.

## How it differs from qwenf

The same merge, retrained once more for shorter thinking (the card claims down
to a twentieth of the tokens) and five instruct modes. The author puts its
ARC-C at 0.709 against 0.735 for `qwenf`: on our sets it does better, not
worse. "L" is the lighter de-censoring; the ULTRA-HERETIC sibling loses another
point on ARC-C and was left out.

The calibration is the generic NEO, not the CODER one `qwenf` uses. Same
layout otherwise: output tensor in 16 bit, MTP tensors in Q8_0.

## Template

The profile loads the template `qwenu` and `qwenf` use, so the bench compares
weights only. The author's template adds `{REASON:xxx}` switches typed in the
message, which the shared template ignores: the bench measured the default
modes. The author's templates sit next to the weights on the station
(`chat_template.jinja`, `chat_template-tturbo-v2.jinja`).
