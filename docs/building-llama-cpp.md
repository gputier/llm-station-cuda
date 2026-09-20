# Building llama.cpp for CUDA on Windows

Seven builds coexist on this machine, on purpose. They are **not
interchangeable**, and picking the wrong one fails silently rather than loudly.
Five are official llama.cpp releases. The sixth is a fork, and the seventh was
compiled here from a pull request; the last two sections say why each had to be.

| Build                      | Date              | CUDA | Serves                            | Why it exists                                                                                                                                                                                                                                                                                                                                                 |
| -------------------------- | ----------------- | ---- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `llama-cpp-turboquant-win` | frozen 2026-04-07 | 12.8 | `embed`                           | A custom fork kept for a cache-quant feature of a model since removed. It has no remaining technical justification and could be retired once the embedder is validated on upstream.                                                                                                                                                                           |
| `llama-cpp-upstream`       | 2026-08-11        | 13.3 | `muse`, `qwenu`, `qwenf`, `qwent` | Official build. The only one of the first two that knows the `muse-glimmer` architecture.                                                                                                                                                                                                                                                                     |
| `llama-cpp-20260827`       | 2026-08-27        | 13.3 | `qwen`                            | The only build with NVFP4 CUDA kernels. See below. It also served `ornith` until 2026-09-08; that profile is Q5_K_M and never needed those kernels.                                                                                                                                                                                                           |
| `llama-cpp-b10826`         | 2026-09-06        | 13.3 | `tiel`, `ornith`, `kat`           | The official release zip and its cudart, unzipped flat, no compilation. Neutral in decode and +5% in prefill on Tiel against the 2026-08-27 build; strictly neutral on Ornith, which moved here on 2026-09-08 to stop owing a profile to the NVFP4 build. See [tuning-log.md](tuning-log.md). `kat` was added here on 2026-09-08 and never ran anywhere else. |
| `llama-cpp-b10883`         | 2026-09-09        | 13.3 | `nex`, `spark`, `bonsai`          | Same recipe as b10826, official zip plus cudart unzipped flat. Installed 2026-09-10 for three candidate models and serving only those. See below.                                                                                                                                                                                                             |
| `llama-cpp-prism-b10685`   | 2026-09-15        | 13.3 | `bonsai2`                         | **Not upstream.** PrismML's fork, release `prism-b10685-7dffb15`, `win-cuda-13.3-x64` archive unpacked flat. The only engine that reads Bonsai 2's rotated weights. Installed 2026-09-18. See below.                                                                                                                                                          |
| `llama-cpp-xing-pr29012`   | 2026-09-19        | 13.3 | `xing`                            | **Not a release.** Compiled on this box from llama.cpp pull request #29012, commit `63c16fb`. The only engine that knows Xing4.0's architecture. See the last section.                                                                                                                                                                                        |

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
and a driver of the same major version serves it; testing 13.4 _kernels_ on x64
would mean compiling, which is a different exercise.

## The build command

`build/build-llama.bat` is the script actually used. It takes three optional
arguments, the source tree, the CUDA toolkit directory name (`v13.3`) and the
log path, and reads `FORCE_CUBLAS` from the environment. The essentials:

```bat
call "...\VC\Auxiliary\Build\vcvarsall.bat" amd64
set PATH=%NINJA_DIR%;%CUDA_PATH%\bin;%PATH%
cmake .. -G "Ninja" ^
  -DBUILD_SHARED_LIBS=OFF ^
  -DGGML_CUDA=ON ^
  -DCMAKE_CUDA_ARCHITECTURES=120a ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DCUDAToolkit_ROOT="%CUDA_PATH%" ^
  -DCMAKE_CUDA_COMPILER="%CUDA_PATH%\bin\nvcc.exe" ^
  -DGGML_CUDA_FORCE_CUBLAS=%FORCE_CUBLAS%
cmake --build . --config Release -j 16
```

Pin the toolkit. Three are installed (12.8, 13.3, 13.4) and the machine
`CUDA_PATH` follows the newest, while every engine here ships cudart 13.3.
Without the second argument a build lands on 13.4 and needs a runtime no
profile provides.

**A double quote in the PATH breaks `vcvarsall.bat`.** On 2026-09-19 the build
died right after the Visual Studio banner with `\Microsoft était inattendu`
("was unexpected at this time"). The cause was the machine PATH entry
`C:\Program Files\Kryolys"`, with one stray quote: it flips cmd's quoting state,
and the `(x86)` of a later entry then closes a parenthesised block inside
vcvarsall. The quote was removed from the registry value (a copy of the old
value is in `D:\LLM-Setup\logs\machine-path-backup-20260919.txt`). If this
error comes back, look for a quote in the PATH first.

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

## The fork, and the rule it did not break

Bonsai 2 stores its weights in a Hadamard-rotated basis and expects the runtime
to apply the matching transform to activations. Mainline llama.cpp has no such
transform, so it refuses `PQ2_0` and `PTQ1_0` as unknown types, and their
authors keep a third band, `Q2_0`, in a separate repository precisely because
that one loads on a stock build and answers gibberish without a warning.

There was no third option, and no `Q2_g64` file as the first generation shipped:
the fork or nothing. What made it acceptable is that PrismML publishes release
archives per platform. `win-cuda-13.3-x64` was unzipped flat like every other
engine here, and the rule that this box runs prebuilt binaries held. It no
longer holds without exception: see the next section.

Two things to know before touching it. The release picked matters: the newer
`prism-b10687` of 2026-09-17 ships only the cudart and no executables, so
`prism-b10685-7dffb15` of 2026-09-15 is the one that works. And the fork tracks
upstream at its own pace, `b10685` against `b10883` next door, which is another
reason no other profile was moved onto it.

Its `--spec-type` accepts values mainline does not: `draft-simple`,
`draft-eagle3`, `draft-mtp`, `draft-dflash`, `draft-dspark`, `ngram-simple`,
`ngram-map-k`, `ngram-map-k4v`, `ngram-mod`, `ngram-cache`, and `none`. It also
ships `gguf-dspark-to-dflash` in its `gguf-py`, which repacks a pre-migration
dspark drafter into a format current binaries load. That converter is what made
it possible to test the first generation's drafter against the second, and to
close the question: see [tuning-log.md](tuning-log.md).

## The pull request build: the first engine compiled here

Xing4.0-29B-A4B (China Telecom, 2026-09-16) combines MLA attention, a MoE and
an op of its own called mHC. No release knows that combination, upstream or
fork. Its authors produced the official GGUF with llama.cpp pull request
#29012, branch `xing4_0-port` of `shuxiaoqiong/llama.cpp`, which adds the CPU
and CUDA backends for it. Their prebuilt Windows package, built for an RTX 3090
(sm_86) and shipping its own CUDA 13 DLLs, was set aside for a build targeting
this card's sm_120a.

The rule that nothing is compiled on this box therefore gave way, for this
engine only. Built on 2026-09-19 from a depth-1 clone of commit
`63c16fb9797d00f13d70b5a618b4deb08953aef3` into
`D:\LLM-Setup\llama-cpp-xing-pr29012`:

```bat
set FORCE_CUBLAS=OFF
build-llama.bat D:\LLM-Setup\llama-cpp-xing-pr29012 v13.3 D:\LLM-Setup\logs\build-xing-pr29012.txt
```

560 steps in about five minutes, exit code 0. The result is one static
`llama-server.exe` of 64 MB in `build-win\bin`, with no DLL split, reporting
`version: 0.4.1-dev (build 1, commit 63c16fb)`. It loads cudart 13.3 from the
toolkit directory through `CudaBin`, like every other engine.

`FORCE_CUBLAS` is off here: the flag was carried over from the frozen fork's
original build and was never measured on anything else.

The GGUF carries an MTP head (`blk.40.nextn.*`, about 540 MB). This build
ignores it and says so at load, so there is no speculative decoding on this
profile. Retire the directory once the pull request is merged and a release
carries the architecture.

The model it was built for was rejected the same evening, see
[tuning-log.md](tuning-log.md), and its weights deleted. The engine stays so the
profile can be re-run after a new download.
