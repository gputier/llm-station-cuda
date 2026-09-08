# KAT-Coder-V2.5-Dev-35B-A3B-MTP

Abliterated, requantised with an MTP head by jakeroxs. Same `qwen35moe`
architecture and the same 3B-of-35B sparsity as `tiel`, which is why it
inherits that profile's launch arguments unchanged.

**On trial since 2026-09-08. It replaces nothing.** It is the only one of four
candidates benched that day to match `tiel` everywhere and beat it where this
box does its work, and the trial is what decides whether it takes over.

```powershell
.\llm-ctl.ps1 -Action kat
```

| | |
|---|---|
| Weights | `KAT-Philly-MTP-Q4_K_M.gguf` |
| Vision projector | none published for these weights: text only |
| Context | 393,216 (`--override-kv`, same mechanism as `tiel`) |
| KV cache | `q4_0` |
| VRAM | 29,702 MB in the 2026-09-08 bench |
| Build | `b10826`, same as `tiel` |

## Running it stops `tiel`

One model at a time on this card, and 29.7 GB leaves no room for a second.
This is an alternation, not a coexistence: switching to `kat` unloads `tiel`,
and vice versa.

## No projector: text only

No projector is published for these weights, unlike `tiel` which loads
`mmproj-BF16.gguf`. Anything sending an image must stay on
[tiel](../tiel-coder-35b-a3b/).

## The 2026-09-08 bench against `tiel`

Four files pulled the same morning were benched against the model in
production. Protocol: b10826, the production `tiel` argument list with only
the model path changing, no projector on either side including the control,
`bench.ps1`, 150,000 characters of real llama.cpp sources (about 38,000
tokens), 512 tokens, seed 42, three runs, median.

| | tiel (control) | kat-coder |
|---|---|---|
| Decode | 199.71 tok/s | 197.71 tok/s |
| Prefill | 8,758 tok/s | 8,072 tok/s |
| VRAM | 30,936 MB | 29,702 MB |
| MTP accepted | 56.2% | 52.8% |

On generated code specifically, `kat` wins: 295.1 tok/s writing PowerShell
against 284.3 for `tiel`.

On a four-question hand-scored quality gate (a C++ out-of-bounds loop, a
PowerShell function to write, the classic fly-between-trains problem, and
Python's mutable default argument), both score 4/4.

Two other candidates pulled the same day, `onyx compact` and `ice (MTPv2
23G)`, led parts of the decode table but failed the quality gate (3/4 each,
same PowerShell-function failure) and were dropped. `kat-coder` is the only
one of the four worth a real trial.

**Read the bench prompt with caution.** `bench.ps1` asks for a ten-line French
summary, the least predictable text a draft head can be handed, and it is
blind to the 30% speculation gains seen on generated code. Every number above
that comes from `bench.ps1` measures a regime this model rarely runs in
practice; the PowerShell figure is closer to real use.

## Two slots means each caller gets half the pool, not all of it

Inherited from `tiel` along with the rest: `--parallel 2 --kv-unified` holds ONE
shared pool of 393,216 tokens. `/props` announces that whole pool to every
caller, and a client that believes it hits a wall. Measured on the trial's first
afternoon, two agents of a single session: a 196,000-token prefill at 1,546
tok/s against the 8,000 the bench gives, the other slot generating at 1.40
tok/s, then `failed to find free space in the KV cache`.

The launcher now announces 180,000 to each caller, which is the pool over two
slots minus room for the output. See
[../../docs/claude-code-integration.md](../../docs/claude-code-integration.md).

## `--spec-draft-n-max 2` carried over, not re-swept

The n-max value is inherited from `tiel`'s 2026-09-03 sweep, run on different
weights. Re-run that sweep on this model's own weights before reading anything
into its acceptance rate; the value has not been validated here.
