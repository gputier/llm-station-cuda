# Building llama.cpp for CUDA on Windows

Five builds coexist on this machine, on purpose. They are **not
interchangeable**, and picking the wrong one fails silently rather than loudly.

| Build | Date | CUDA | Serves | Why it exists |
|---|---|---|---|---|
| `llama-cpp-turboquant-win` | frozen 2026-04-07 | 12.8 | `embed` | A custom fork kept for a cache-quant feature of a model since removed. It has no remaining technical justification and could be retired once the embedder is validated on upstream. |
| `llama-cpp-upstream` | 2026-08-11 | 13.3 | `muse`, `qwenu` | Official build. The only one of the first two that knows the `muse-glimmer` architecture. |
| `llama-cpp-20260827` | 2026-08-27 | 13.3 | `qwen` | The only build with NVFP4 CUDA kernels. See below. It also served `ornith` until 2026-09-08; that profile is Q5_K_M and never needed those kernels. |
| `llama-cpp-b10826` | 2026-09-06 | 13.3 | `tiel`, `ornith`, `kat` | The official release zip and its cudart, unzipped flat, no compilation. Neutral in decode and +5% in prefill on Tiel against the 2026-08-27 build; strictly neutral on Ornith, which moved here on 2026-09-08 to stop owing a profile to the NVFP4 build. See [tuning-log.md](tuning-log.md). `kat` was added here on 2026-09-08 and never ran anywhere else. |
| `llama-cpp-b10883` | 2026-09-09 | 13.3 | `nex`, `spark`, `bonsai` | Same recipe as b10826, official zip plus cudart unzipped flat. Installed 2026-09-10 for three candidate models and serving only those. See below. |

## b10883: taken for one architecture, not for speed

It exists because of a single hard requirement. b10826 does not know the
`spark2_5` architecture, whose support landed in **b10828**, and an engine that
does not know an architecture fails at load rather than saying so. b10883, the
newest release at install time, was taken instead of the barely-sufficient
b10828 so the exercise would not have to be repeated a week later.

It has **not** been benchmarked against b10826 on the production models, and no
production profile was moved onto it. Do not move `tiel`, `ornith` or `kat` here
on the assumption that newer is faster: the 2026-08-27 build taught that lesson
at the price of two full compilations for a gain of exactly nothing.

The official x64 release only ships against CUDA 12.4 and 13.3. There is a 13.4
asset, but arm64 only. Running this box on a 13.4 toolkit does not require a
matching build, since each build carries its own cudart flat in its directory
and a driver of the same major version serves it; testing 13.4 *kernels* on x64
would mean compiling, which is a different exercise.

## The build command

`build/build-llama.bat` is the script actually used. The essentials:

```bat
call "...\VC\Auxiliary\Build\vcvarsall.bat" amd64
set PATH=%NINJA_DIR%;%PATH%
cmake .. -G "Ninja" ^
  -DBUILD_SHARED_LIBS=OFF ^
  -DGGML_CUDA=ON ^
  -DCMAKE_CUDA_ARCHITECTURES=120a ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DGGML_CUDA_FORCE_CUBLAS=ON
cmake --build . --config Release -j 16
```

**`CMAKE_CUDA_ARCHITECTURES=120a`, not `120`.** CMake rewrites `120` to `120a`
on its own, and says so in the configure output:

```
-- Replacing 120 in CMAKE_CUDA_ARCHITECTURES with 120a
-- Using CMAKE_CUDA_ARCHITECTURES=120a CMAKE_CUDA_ARCHITECTURES_NATIVE=120a-real
```

The `a` suffix means architecture-specific features, which is what unlocks the
Blackwell tensor-core paths. Passing the plain `120` works because of that
rewrite, but writing `120a` makes the intent explicit.

The configure step also reports the CPU backend variant it picked, `/arch:AVX512`
here. Worth checking on a different CPU: a mismatch is a silent performance loss.

`ccache` is not installed and CMake warns about it. Installing it is worth the
trouble if you plan to rebuild often.

## NVFP4: the reason the third build exists

NVFP4 is **ggml type 40**. Its CUDA kernels exist only in recent builds, and the
binaries from 2026-08-11 do not contain them at all, verified by inspection. A
model quantised to NVFP4 simply cannot be served by an older build.

The history of this build is the most useful thing about it.

It was compiled on 2026-08-27 to test whether a forty-version-old llama.cpp was
holding throughput back. Measured against the 2026-08-11 build at fixed seed,
with identical speculation counters (495/907, so a clean comparison):
**118.49 tok/s versus 118.32, prefill 3,278 versus 3,252.** Nothing.

A second hypothesis was that `GGML_CUDA_FA_ALL_QUANTS=OFF` was depriving the
`q4_0` cache of a dedicated flash-attention kernel. Recompiled with the option
`ON`: **118.49 tok/s and 3,275 prefill**, exactly the same result. Hypothesis
dead.

The build was therefore kept, unused, and classified as a dead end. Three weeks
later the model moved to NVFP4 and this build became the only one able to serve
it.

> A dead end is only dead under the assumptions you tested it with. The measurement
> was correct; the conclusion drawn from it was correct at constant model, and
> false the moment the model format changed.

## Two traps specific to recent builds

**The server is split into DLLs.** `llama-server.exe` is now a ~10 KB launcher;
the code lives in `llama-server-impl.dll` and `ggml-cuda.dll`. A 10 KB binary is
**not** the sign of a failed build, which is what we assumed on the first
attempt. Consequence: the binary must be launched from its own directory, which
`llm-ctl.ps1` handles via the `workDirPath` parameter of `Start-LLM`.

**Linking fails if a server is still running on that build.** You get `LNK1104`,
cannot open `ggml-cuda.dll`. Stop the server before recompiling.

## Flags that changed between versions

The 2026-08-11 upstream build **removed** `--draft` and `--draft-min` in favour
of `--spec-draft-n-max` and `--spec-type`. Do not assume two llama.cpp binaries
accept the same command line, even a few weeks apart. `llm-ctl.ps1` routes the
executable per model through the `exePath` / `workDirPath` / `cudaBinPath`
parameters of `Start-LLM` precisely because of this.
