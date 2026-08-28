# Tuning log

Every campaign run on this box, including the ones that found nothing. The
negative results are the more useful half: they tell you which hypotheses are
already spent.

Unless stated otherwise, all figures are at fixed seed, median of 3 runs, on the
hardware listed in [prerequisites.md](prerequisites.md).

---

## 2026-08-28: the NVFP4 migration and two settings that were wrong all along

### NVFP4 replaces Q5_K_XL

The model body moved to NVFP4, LOW tier, a format the RTX 5090 tensor cores
execute natively. Measured against the previous UD-Q5_K_XL, same prompt, same
protocol, **speculation disabled on both sides** to isolate the structural gain
from acceptance noise:

| | Q5_K_XL | NVFP4 LOW | |
|---|---|---|---|
| Decode | 58.84 tok/s | 70.64 tok/s | +20.1% |
| Prefill | 3,015 tok/s | 4,584 tok/s | +52.0% |
| VRAM | 28.5 GB | 24.4 GB | 4.1 GB freed |

With speculation on, median over 5 seeds: 98.46 to 122.79 tok/s (+24.7%). On a
real workload of 60 reasoning problems: 233.1 s to 188.6 s.

**The gain comes from size, which confirms the ceiling is memory bandwidth**:
fewer bytes to re-read per token. The operational corollary is uncomfortable but
clear: raising precision costs speed. A Q8 of the weights would be a net
regression, and it is also why the KV cache stays at q4_0.

**Quality was proven, not assumed.** MMLU over 500 stratified questions, fixed
seed, identical question set, 5-shot, temperature 0: 72.8% against 72.4%, two
questions out of five hundred, well inside the roughly 2-point uncertainty.
GSM8K over 60 problems in thinking mode: **56/60 for both, failing the same
problems**. Needle-in-a-haystack recall verified at 59,622 and 198,625 tokens,
two positions each, 4/4.

The MEDIUM tier was measured and **rejected**: 98.05 tok/s, heavier, MMLU 71.8%.
Dominated on every axis. The repository publishes nine tiers sharing an identical
NVFP4 body, differing only in head precision.

### `--ctx-size` was oversizing buffers on two profiles

Both profiles requested 524288 while the GGUF declares 262144. llama.cpp caps
the window at the declared value, in a single log line, **but still sizes its
buffers on what was requested**. We were paying the memory of a 512k window
without ever having it.

This is one notch beyond the capping trap already known, which concerns the
window and not the memory. Measured with only `--ctx-size` changing:

| Requested | Real window | VRAM | Decode | Prefill |
|---|---|---|---|---|
| 262144 | 262144 | 27.2 GB | **123.03** | **4,241** |
| 524288 | 262144 | 31.9 GB | 99.76 | 2,462 |

Twenty-three percent of decode and 72% of prefill lost for nothing. The defect
predated the NVFP4 migration, so it was already costing on the previous quant.

### The client-side ceiling has to match the server

Two sides, one number. Server: `--ctx-size`. Client:
`CLAUDE_CODE_MAX_CONTEXT_TOKENS`. The client value had been raised to 524288 and
was brought back to 262144 the same day: it promised the client twice what the
server serves, and a session crossing the real ceiling would have been truncated
with no warning.

The authoritative value is what `/props` returns in
`default_generation_settings.n_ctx`, never what you asked for.

### n-gram speculation, all modes rejected

The build exposes `ngram-simple`, `ngram-cache`, `ngram-map-k`, `ngram-mod`,
`draft-eagle3` and `draft-dflash` alongside `draft-mtp`. The four n-gram modes
need no draft file, so they were free to try.

On a synthetic benchmark, `ngram-simple` gave **176.52 tok/s** against 121.70 for
`draft-mtp`. On a real workload it took **358.1 s against 188.6 s**, almost
double.

The benchmark lied because its prompt is a repeated paragraph and because
`ignore_eos` prolongs generation into degenerate text. Two gifts to a method that
predicts by replaying the context. `ngram-cache` was worse still, 69.85 tok/s.

> A benchmark that does not resemble the workload can invert the ranking. This
> one did, by a factor of two, in the direction that looked like a win.

### A published setting for the same card and model did not transpose

A community benchmark recommended `--spec-draft-n-max 4` on this exact quant.
Measured here: **108.66 tok/s against 123.03 at n-max 3.**

---

## 2026-08-27: retuning, two hypotheses disproved

### A speculation sweep without a fixed seed measures nothing

An earlier sweep concluded `--spec-draft-n-max 2` beat 3. Re-run with a fixed
seed, the order inverts: 2 gives 114.90 tok/s, 3 gives 117.75.

The reason is that draft acceptance depends on the text being generated. Without
a seed, every run generates different text and the 2-3-4 neighbourhood sits
entirely inside that noise. With a seed, three runs of the same configuration
return rigorously identical speculation counters, which makes the A/B readable.

| n-max | Throughput | Acceptance |
|---|---|---|
| 2 | 114.90 tok/s | 64% (449/697) |
| **3** | **117.75 tok/s** | **54% (495/907)** |
| 4 | 113.53 tok/s | 45% (516/1128) |
| 6 | 97.76 tok/s | 33% (534/1575) |

### Removing the vision projector does NOT gain throughput

The hypothesis was that the vision encoder cost speed and constrained `-ub`.
Measured: 115.70 tok/s without it against 114.90 with, which is noise. It costs
1.3 GB of VRAM and nothing else.

And `-ub 512` was not imposed by it either. At `-ub 2048` the encoder stays
loaded with no penalty; the multimodal buffer only overflows at 4096, where the
card saturates at 31.5 GB and **both** metrics regress.

| `-ub` | Prefill | Decode | VRAM |
|---|---|---|---|
| 512 | 3,106 tok/s | 117.08 tok/s | 29.5 GB |
| **2048** | **3,252 tok/s** | **117.75 tok/s** | **30.7 GB** |
| 4096 | 2,582 tok/s | 111.81 tok/s | 31.5 GB |

### `--min-p 0`: the publisher's calibration, silently overridden

The model card calibrates thinking mode at `temperature 1.0 / top_p 0.95 /
top_k 20 / min_p 0.0`. **`min_p` is zero.**

llama.cpp imposes `min_p = 0.05` by default when nothing sets it, which clips the
tail of the distribution **on top of** the already-calibrated top-p and top-k,
with no message anywhere. `/props` confirmed it.

Same family of trap as a hard-coded `temperature=0.3` inherited from another
model, which had been quietly degrading a different profile for weeks.

### The build was not the limiting factor

Covered in [building-llama-cpp.md](building-llama-cpp.md). Two recompiles,
two null results, and a build kept for nothing that turned out to be
indispensable three weeks later.

### What the whole campaign was worth

**2.5% in generation and 4.7% in context reading.** The configuration from nine
days earlier was already good. Worth knowing before opening a tuning campaign:
on a bandwidth-bound workload, the remaining margin is structural. Neither a
setting nor a llama.cpp version will unlock it; only a smaller model in memory
would, at the cost of quality.

---

## A note on single-seed throughput numbers

With speculation active, the previous quant varied from **68.44 to 139.07 tok/s
on seed alone**, and one seed in five hit an end-of-sequence on the first token,
so a throughput of zero. A number quoted from a single seed is not wrong, it is
fragile.

Two valid protocols: speculation disabled, which returns a measurement stable to
0.3%, or a median over five seeds minimum. The NVFP4 quant turned out markedly
more regular than the old one, 104 to 141 against 68 to 139.
