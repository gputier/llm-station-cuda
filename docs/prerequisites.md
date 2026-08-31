# Prerequisites

Everything below was installed on the machine these measurements come from. The
versions are the ones actually used, not the minimum theoretical ones.

## Server side, Windows

### Hardware

| | Used here | Notes |
|---|---|---|
| GPU | RTX 5090, 32,607 MiB VRAM | Compute capability 12.0, so `sm_120`. Any CUDA card works, but the NVFP4 path below needs Blackwell. |
| RAM | 128 GB | See the OOM trap at the bottom of this page: host RAM matters more than it looks. |
| Disk | Weights on a dedicated volume | Roughly 60 GB for the four models kept here. Plan for more, quant comparisons add up fast. |

### Toolchain

| Component | Version used | Why |
|---|---|---|
| Windows 11 Pro | build 26200 | |
| NVIDIA driver | 616.56 | |
| CUDA Toolkit | **12.8 and 13.3, both installed** | Different builds link against different versions. `llm-ctl.ps1` prepends the right `bin` directory to `PATH` per model. |
| Visual Studio 2022 Build Tools | with the C++ workload | `vcvarsall.bat amd64` must be callable. The full IDE is not needed. |
| CMake | 4.4.2 | |
| Ninja | any recent | Much faster than the MSBuild generator on this codebase. |

Two CUDA versions side by side is not an accident of history. The frozen fork
still serving the embedder was built against 12.8, and rebuilding it has no
benefit, so both stay installed.

### Model weights

GGUF format, from the usual public repositories. Exact files per model are
listed in each page under [models/](../models/). Nothing in this repository
downloads weights for you.

## Client side, macOS

- Claude Code
- An SSH key authorised on the Windows box, non-interactive. The launchers call
  `ssh` without a TTY, so a passphrase prompt will hang them.
- `curl`, present by default.

The SSH warning about post-quantum key exchange on recent OpenSSH clients is
cosmetic here and can be ignored.

## Two traps worth knowing before the first run

### A CUDA OOM can come from host RAM, not from VRAM

Models are loaded through host RAM (`--load-mode mlock`) before populating VRAM.
If the host is saturated, the failure surfaces as `cudaMalloc failed: out of
memory` **while `nvidia-smi` shows VRAM free**. Read available host RAM before
concluding anything about the GPU. This cost us an afternoon when a WSL instance
had been allowed to reserve 120 GB.

Counter-intuitively, `--load-mode mlock` **preserves** host RAM rather than
consuming it: with those flags the process falls back to about 1.1 GB of working
set once the weights are copied to VRAM. Without them, the GGUF stays in the
file cache inside the working set, 16.3 GB measured. Do not remove them believing
you are freeing memory, it is the other way round.

### `/health` tells you nothing about which model is loaded

It answers `ok` before the model is servable, and it answers `ok` whatever model
is loaded. To know which model actually occupies the port, read `model_path` in
`/props`. Every launcher in [clients/](../clients/) does exactly that, and it is
why they can detect that the wrong model is loaded and offer to swap it.

Related: `/v1/embeddings` returns 501 when the loaded model is not the embedder.
Sequence your work, do not expect embeddings and generation at the same time on
a single-slot server.
