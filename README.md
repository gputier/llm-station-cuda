# llm-station-cuda

A self-hosted LLM box that serves **Claude Code** from local weights, with no
translation proxy in between.

A Windows machine with an RTX 5090 runs `llama-server`. A macOS laptop runs
Claude Code pointed at it. The launcher scripts load the right model over SSH,
wait for it, and hand over to `claude`. That is the whole system.

What makes this repository worth reading is not the scripts, it is the
**measurements attached to every flag**. Each setting in `llm-ctl.ps1` carries
the numbers that justify it and, where it applies, the hypothesis that was
disproved. Several of these traps are silent: they cost throughput or context
without ever printing an error.

## Hardware and software this was proven on

| | |
|---|---|
| GPU | NVIDIA GeForce RTX 5090, 32,607 MiB, driver 616.92 |
| CPU / RAM | Ryzen 9 9950X3D, 128 GB |
| OS | Windows 11 Pro, build 26200 |
| Backend | `llama.cpp` CUDA, native Windows build (not vLLM, not WSL) |
| Client | macOS, Claude Code over SSH |

Nothing here needs that exact hardware, but every number below was measured on
it. Throughput figures do not transpose to another card, and a couple of the
findings show that settings published for the *same* card and the *same* model
did not transpose either.

## Models served

All ten share port 8080 and are mutually exclusive on the GPU: loading one
unloads the others.

| Action | Model | Context | Role |
|---|---|---|---|
| `tiel` | Tiel-Coder-35B-A3B, MTP UD-Q4_K_XL | 393,216 | In service. Coding and reasoning, vision |
| `kat` | KAT-Coder-V2.5-Dev-35B-A3B, MTP Q4_K_M | 393,216 | On trial since 2026-09-08. Text only, no projector |
| `ornith` | Ornith-1.5-9B, Q5_K_M | 262,144 | Fast second opinion and short tasks, a third of the VRAM |
| `muse` | Muse Glimmer 30B, UD-Q4_K_XL | 262,144 | Agentic multi-turn, vision, faithful OCR |
| `qwen` | Qwen3.8-27B, NVFP4 LOW | 393,216 | Reasoning and coding, superseded by `tiel` |
| `qwenu` | Qwen3.8-27B Uncensored, Q5_K_M | 262,144 | Used only when the aligned model refuses a legitimate task |
| `embed` | nomic-embed-text-v1.5, Q8_0 | 131,072 | 768-dimension embeddings |
| `nex` | Nex-N2.5-mini, i1-Q4_K_M | 262,144 | Candidate since 2026-09-10. Vision, no speculation |
| `spark` | Spark-X2.5-4B, Q8_0 | 262,144 | Candidate since 2026-09-10. Agentic, text only |
| `bonsai` | Ternary-Bonsai-27B, Q2_g64 | 262,144 | Candidate since 2026-09-10. Ternary weights, vision |

The last three were installed on 2026-09-10 and measured the same day. They run
on their own engine, `b10883`, which serves nothing else, and each carries a
known defect written at the top of its page under [models/](models/).

**Every quality figure in this repository comes from the campaign of
2026-09-10**, where all nine chat models went through the same 500-question MMLU
set and the same 60 GSM8K problems. It is written up in
[docs/campagne-mesures-2026-09-10.md](docs/campagne-mesures-2026-09-10.md), and
it replaced the figures published before it: the previous bench had been lost,
its method was unknown, and the re-run showed it wrong in both directions at
once. `ornith` does not score 73.0 % but 83.4 %, and `qwen` not 82.0 % but 78.8 %.

Ranking, MMLU then GSM8K: `nex` and `tiel` 86.6 %, `muse` 85.0 %, `kat` 84.6 %,
`bonsai` 83.6 %, `ornith` 83.4 %, `qwen` 78.8 %, `qwenu` 77.0 %, `spark` 73.0 %.
`kat` alone scores 60/60 on GSM8K, where `tiel` manages 53.

Which one to reach for, per use, with what is measured kept separate from what
is inferred:
[docs/quel-modele-pour-quel-usage.md](docs/quel-modele-pour-quel-usage.md).

Muse used to be the answer to anything beyond 262k, and it no longer is: its
window was brought down from 1,048,576 to 262,144 on 2026-08-31 because a
million tokens cost more than they returned. The model still reaches a million,
recall proven at 556,390 tokens; this box just does not serve it there.

## Quick start

On the Windows box:

```powershell
# Start a model. Any running model is unloaded first.
.\llm-ctl.ps1 -Action qwen

# What is loaded right now?
.\llm-ctl.ps1 -Action status

# Follow the live log of the running instance.
.\llm-ctl.ps1 -Action logs

# Free the GPU.
.\llm-ctl.ps1 -Action stop
```

From the macOS client:

```bash
export LLM_HOST=your-32gb-box
export LLM_HOST_16GB=your-16gb-box   # only for tiel and qwen, see clients/README.md
export LLM_SSH_USER=your-ssh-user

./clients/tiel        # asks which box, loads Tiel there if needed, then runs Claude Code
```

There is one launcher per model family, eight in all, listed with what each is
good at in [clients/README.md](clients/README.md). `tiel` and `qwen` open with a
menu because their models are served on both boxes: `qwen` covers the aligned
and the uncensored Qwen3.8 here, and Qwen3.6 on the 16 GB box. `embed` has none
on purpose: it serves embeddings, not a chat endpoint.

The three paths at the top of `llm-ctl.ps1` (`$RootDir`, `$ModelsDir`,
`$CudaRoot`) are the only installation-specific values. Everything else is
portable.

## Documentation

| File | What it covers |
|---|---|
| [docs/prerequisites.md](docs/prerequisites.md) | Everything that must be installed before a first build |
| [docs/building-llama-cpp.md](docs/building-llama-cpp.md) | The CUDA builds, why there are five, and the two build traps |
| [docs/claude-code-integration.md](docs/claude-code-integration.md) | How a local server replaces the Anthropic API, and what that costs |
| [docs/api-usage.md](docs/api-usage.md) | Calling the server directly: sampling per model, vision, embeddings |
| [docs/tuning-log.md](docs/tuning-log.md) | Every measurement campaign, including the ones that found nothing |
| [docs/campagne-mesures-2026-09-10.md](docs/campagne-mesures-2026-09-10.md) | Nine models through one bench in one day. **Supersedes every quality figure published before it.** |
| [docs/quel-modele-pour-quel-usage.md](docs/quel-modele-pour-quel-usage.md) | Which model to reach for, per use, and what is measured against what is inferred |
| [models/](models/) | One page per model: profile, measurements, model-specific traps |
| [clients/](clients/) | The launcher scripts and how they decide to reload |

## Seven findings that cost the most to establish

Each is documented in full where it belongs; this is the short list for anyone
running llama.cpp on similar hardware.

**1. `--ctx-size` sizes buffers on what you ask for, not on what you get.**
The GGUF declares the real ceiling. Ask for more and llama.cpp caps the window
in one log line, then allocates buffers for the value you asked for anyway. We
paid the memory cost of a 512k window while only ever having 262k: 23% of decode
and 72% of prefill lost for nothing. This is one notch beyond the known capping
trap, which is about the window and not about memory. Lifting the ceiling for
real takes `--override-kv`, and then the cost is not linear: 384k is free on this
card and 512k is not.

**2. Forcing speculative depth collapses acceptance.** The DFlash block is 16
tokens, so `--spec-draft-n-max 15` looks like the obvious setting. It gives
101.27 tok/s at 10.2% acceptance, against 107.33 tok/s at 39.9% for the default:
slightly worse, for 4.3x more wasted draft compute.

**3. A speculation sweep without a fixed seed measures nothing.** Draft
acceptance depends on the text being generated. Without a seed, the 2-3-4
neighbourhood sits entirely inside the noise, and our first sweep concluded the
opposite of the re-run.

**4. `-ub` carries failures that look unrelated to it.** At `-ub 4096` with a
vision projector, the multimodal compute buffer reached 10 GB and produced two
symptoms with no apparent connection: `decode() failed: bad allocation` past 10k
tokens, and a speculative drafter **silently disabled** past 96k of context.
We first blamed the context window. That was wrong.

**5. A dead end is only dead under the assumptions you tested it with.** A build
was measured, found to gain nothing, and written off. Three weeks later the model
format changed to NVFP4 and that same build became the only one able to serve it.

**6. "The model is running in RAM" is almost always the wrong diagnosis, and the
test that settles it is throughput on a SHORT prompt.** A box reported as slow
showed 25,707 MB of dedicated VRAM and 2,940 MB of *shared* memory, host RAM
presented as graphics memory, while 5.7 GB of VRAM sat free. That reads exactly
like a spill. It was not one: throughput was nominal, a 118.35 tok/s median over
five seeds against a 123.4 baseline, and the split reproduced to within one
percent across a clean restart. Weights genuinely served from host RAM collapse
*every* request; here only the long ones were slow, and they were slow for an
ordinary reason, attention cost. Two things generalise. Under WDDM `nvidia-smi`
reports `[N/A]` per process and cannot see this at all, so the Windows
`GPU Process Memory` counters are the only instrument. And a fixed sleep between
killing one instance and starting the next is unsound whatever the cause, since
Windows frees device memory asynchronously; this repository waits for usage to
settle instead, which costs 1.69 s against the 2 s it replaces.

**7. The prompt cache in host RAM is capped at 8 GB, and two interleaved
conversations are enough to lose a third.** `--cache-ram` defaults to 8192 MB and
nothing here was setting it. A 140k-token context weighs 2,461 MB in that cache,
so three of them do not fit. Replaying the same three disjoint ~150k contexts as
A, A, B, C, A: returning to A cost **139,810 re-prefilled tokens and 62.6 s**
with the default, and **4 tokens and 0.3 s** at a raised `-cram`. Everything else
was identical. The cost is host RAM only, 11,732 to 16,946 MB for the process out
of 128 GB, VRAM untouched and decode throughput unchanged. The A/B was run at
`-cram 49152`; the ceiling in `llm-ctl.ps1` is now 24576 MB, which still holds
about ten 140k contexts. Two things generalise.
This flag, not the slot count, is what decides whether returning to a conversation
is free: a single-slot server keeps several contexts alive as long as they fit the
budget. And **a cache test whose contexts all fit in the budget does not measure
the cache, it measures that nothing had to be evicted**: our first attempt used
8k contexts and wrongly concluded that interleaving was free.

## Security

The server binds `0.0.0.0` and, in this repository, runs without `--api-key`.
That is safe only on a trusted network segment. A firewall does not protect you
from the browser case: any web page open on any machine of your LAN can reach a
LAN-bound server behind its user's back. If your network is not fully trusted,
set `--api-key` and `--cors-origins ""`, as the sibling
[llm-station-vulkan](../llm-station-vulkan) repository does.

## License

MIT. `llama.cpp` is MIT; model weights carry their own licenses, Apache 2.0 for
the Qwen and Muse Glimmer families.
