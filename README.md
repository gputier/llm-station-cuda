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
| GPU | NVIDIA GeForce RTX 5090, 32,607 MiB, driver 616.56 |
| CPU / RAM | Ryzen 9 9950X3D, 128 GB |
| OS | Windows 11 Pro, build 26200 |
| Backend | `llama.cpp` CUDA, native Windows build (not vLLM, not WSL) |
| Client | macOS, Claude Code over SSH |

Nothing here needs that exact hardware, but every number below was measured on
it. Throughput figures do not transpose to another card, and a couple of the
findings show that settings published for the *same* card and the *same* model
did not transpose either.

## Models served

All four share port 8080 and are mutually exclusive on the GPU: loading one
unloads the others.

| Action | Model | Context | Role |
|---|---|---|---|
| `muse` | Muse Glimmer 30B, UD-Q4_K_XL | 1,048,576 | Agentic multi-turn, vision, faithful OCR |
| `qwen` | Qwen3.8-27B, NVFP4 LOW | 262,144 | Reasoning and coding |
| `qwenu` | Qwen3.8-27B Uncensored, Q5_K_M | 262,144 | Used only when the aligned model refuses a legitimate task |
| `embed` | nomic-embed-text-v1.5, Q8_0 | 131,072 | 768-dimension embeddings |

Role split established by measurement, not preference: Qwen to reason and code,
Muse for agentic loops, for faithful OCR (Qwen distorts identifiers), and for
any context beyond 262k.

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
export LLM_HOST=your-server-hostname-or-ip
export LLM_SSH_USER=your-ssh-user

./clients/qwen        # loads Qwen if needed, then runs Claude Code against it
```

The three paths at the top of `llm-ctl.ps1` (`$RootDir`, `$ModelsDir`,
`$CudaRoot`) are the only installation-specific values. Everything else is
portable.

## Documentation

| File | What it covers |
|---|---|
| [docs/prerequisites.md](docs/prerequisites.md) | Everything that must be installed before a first build |
| [docs/building-llama-cpp.md](docs/building-llama-cpp.md) | The CUDA builds, why there are three, and the two build traps |
| [docs/claude-code-integration.md](docs/claude-code-integration.md) | How a local server replaces the Anthropic API, and what that costs |
| [docs/api-usage.md](docs/api-usage.md) | Calling the server directly: sampling per model, vision, embeddings |
| [docs/tuning-log.md](docs/tuning-log.md) | Every measurement campaign, including the ones that found nothing |
| [models/](models/) | One page per model: profile, measurements, model-specific traps |
| [clients/](clients/) | The launcher scripts and how they decide to reload |

## Five findings that cost the most to establish

Each is documented in full where it belongs; this is the short list for anyone
running llama.cpp on similar hardware.

**1. `--ctx-size` sizes buffers on what you ask for, not on what you get.**
The GGUF declares the real ceiling. Ask for more and llama.cpp caps the window
in one log line, then allocates buffers for the value you asked for anyway. We
paid the memory cost of a 512k window while only ever having 262k: 23% of decode
and 72% of prefill lost for nothing. This is one notch beyond the known capping
trap, which is about the window and not about memory.

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
