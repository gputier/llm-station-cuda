# Tuning log

Every campaign run on this box, including the ones that found nothing. The
negative results are the more useful half: they tell you which hypotheses are
already spent.

Unless stated otherwise, all figures are at fixed seed, median of 3 runs, on the
hardware listed in [prerequisites.md](prerequisites.md).

---

## 2026-09-08: 65,536 tokens of extra window cost a factor of fifty on decode

A session sitting at 372,738 tokens of a 393,216 window could no longer compact: the client kept
answering `summarization produced empty response`. The reading was that no room was left for the
summary to be written, since `n_ctx` counts input and output in one pool. So the window went up to
458,752 on `kat`, and the server took it: 30,527 MiB of 32,607, one gigabyte for the extra 65,536
tokens.

It made things worse, and the log says by how much. Same model, same 372,000-token context, same
single slot:

| Window | Prefill | Decode |
|---|---|---|
| 393,216 | cached | **97.31 tok/s** |
| 458,752 | 371,799 tokens in 111 s | **1.92 tok/s** |

Four minutes for 259 tokens, after which the client gave up. The empty response was a client
timeout, not a server refusal.

**The cliff is one gigabyte wide.** At 29,389 MiB the card has 3 GB spare and decodes at a hundred
tokens per second; at 30,527 MiB it has 2 GB and decodes at two. This repository already carried
the warning, in the `qwen` entry: the VRAM cost of a window is not linear, 384k was free on this
card and 512k was not. It was read before the change and tried anyway.

**What the incident does not explain.** Why the compaction failed at 393,216 in the first place,
where decode was measured at 97 tok/s and 20,478 tokens of room remained, is still unknown. The
window went back to 393,216 and the compaction then succeeded, so the working hypothesis is that
the earlier failures were the two-slot configuration in place at that moment, not the window size.
Not proven.

---

## 2026-09-08: four candidate models benched, and the bench itself turns out to measure the wrong thing

Four files pulled the same morning were put against the models in service: `Ornith-1.5-9B-MTP-BF16-ASHQ1-6500`,
`Ornith-1.5-35B-A3B-TIEL_Calibrated-MTPv2-23G-ICE`, `Ornith-1.5-35B-A3B-ONYX-compact`, and
`KAT-Philly-MTP-Q4_K_M` from KAT-Coder-V2.5-Dev-35B-A3B. All four declare `nextn_predict_layers`,
so all four were run with speculation on.

Protocol: b10826, the production `tiel` argument list with only the model path changing, no
projector on any of them including the control, `bench.ps1`, 150,000 characters of real llama.cpp
sources (about 38,000 tokens), 512 tokens, seed 42, three runs, median.

| Model | Decode | Prefill | VRAM | MTP accepted |
|---|---|---|---|---|
| **tiel** (control) | 199.71 tok/s | 8,758 tok/s | 30,936 MiB | 56.2% |
| onyx compact | **207.19** | 8,275 | **25,621** | 60.1% |
| kat-coder | 197.71 | 8,072 | 29,702 | 52.8% |
| ice (MTPv2 23G) | 190.97 | 8,411 | 30,530 | 52.2% |

And the two 9B, same protocol at the production `ornith` window of 262,144:

| Model | Decode | Prefill | VRAM | MTP accepted |
|---|---|---|---|---|
| ornith Q5_K_M, as in production | 167.18 tok/s | **10,721** | **11,648 MiB** | none |
| ornith Q5_K_M, speculation on | **179.64** | 7,715 | 14,179 | 58.4% |
| ashq1 (the downloaded file) | 164.25 | 7,743 | 14,357 | 49.0% |

### The 9B already had the MTP head, and nobody had switched it on

The interesting line above is the middle one, and it is not a new file: it is the model that has
been serving `ornith` all along. Starting it without `--spec-type` prints four warnings that are
easy to walk past:

```
W model has unused tensor blk.32.nextn.eh_proj.weight (size = 23068672 bytes) -- ignoring
```

The production weights carry a draft head, llama-server drops it when no speculation is asked for,
and switching it on is worth 7.5% of decode for 2,531 MB of VRAM and a quarter of the prefill. The
file downloaded to answer that same question, ASHQ1, is slower than the one already on the disk.

**And this repository has said so since 2026-08-31**, in
[../models/qwen3.8-27b/README.md](../models/qwen3.8-27b/README.md): "the `blk.*.nextn.*` tensors
are already inside the quant; llama.cpp loads them and ignores them unless you pass `--spec-type
draft-mtp`. One flag enables speculation, no second file to fetch." Written about `qwen`, true of
every quant that ships those tensors, and never replayed against the other profiles. That is the
actual lesson, and it is worse than the one about checking the disk before downloading: the fact
was already written down, in this repository, by us. A finding about one model is worth a pass over
the others the same day.

### The first request after a start is not a measurement

Every speculation run collapsed on its first request and recovered on the next: 49, 57, 69, 86 and
88 tok/s against 180 to 208 immediately after. Runs without speculation showed nothing of the sort.
Two explanations fit, a cold server or a context the draft head has never seen, and they lead to
opposite conclusions, so a second prompt of the same size was built from a different slice of the
sources and sent to a warm server:

| tiel | prompt A, cold | prompt A, cached | prompt B, new context | prompt B, cached |
|---|---|---|---|---|
| speculation on | 55.82 tok/s | 200.20 | 190.10 | 191.83 |
| speculation off | 196.96 | 195.98 | 187.63 | 193.15 |

A new context costs nothing. Only the first request after a start does, and only under speculation.
**Discard run 1 of any speculative bench**, and read the header of this file accordingly: "median
of 3 runs" has always meant one cold run plus two that hit the prompt cache, `-cram` being on in
every profile. The median lands on the cached pair, which is the right regime to read since Claude
Code reuses its prefix, but it is not what the phrase says.

### The bench prompt is the worst possible case for speculation

Compared like for like on prompt B, warm, speculation buys nothing at all: 190.10 against 187.63,
then 191.83 against 193.15, for 3,710 MB of VRAM and 14% of the prefill. That reading would have
sent the MTP head to the bin. It would have been wrong, and the reason is the prompt: `bench.ps1`
asks for a ten-line summary in French, which is the least predictable text a draft head can be
handed. Four short tasks at temperature 0, same model, same day:

| Task | tiel, speculation on | tiel, speculation off |
|---|---|---|
| write a PowerShell function | **284.3 tok/s** | 218.2 |
| read a Python snippet | **226.4** | 190.3 |
| reason in French | 204.8 | **240.1** |

Thirty percent on generated code, nineteen on reading it, fifteen lost on French prose. The head
earns its VRAM on exactly the work this box exists for, and `bench.ps1` is blind to it. Every MTP
decision taken on this bench since 2026-09-03, the `n-max` sweep included, was taken on prose.
A code-shaped long-context bench is missing from this repository.

### Quality, four exercises, temperature 0

Same four tasks scored by hand: a C++ out-of-bounds loop, a PowerShell function to write, a
classic fly-between-trains problem, and Python's mutable default argument.

- **tiel** 4/4, **kat-coder** 4/4
- **onyx** 3/4, **ice** 3/4, both failing the same one, the PowerShell function they write does
  not return files

Four questions rank nothing. They are a gate: onyx and ice do not pass it, and their throughput
advantage is not worth reopening.

### What moves

Nothing yet, and `tiel` stayed in production throughout. Onyx leads the decode table by 3.7% and
saves 5.3 GB, which is real, but it fails a four-question gate that the incumbent passes, and the
table it leads measures the wrong regime. Kat-coder matches tiel everywhere and beats it on
generated code, 295.1 tok/s against 284.3, which makes it the only candidate worth a real trial.
Ice is out on both counts. The 9B question is answered without a download: the head is already
there.

---

## 2026-09-08: Ornith moves to b10826, which buys nothing, and a launcher trap bites twice

### The build change is neutral on Ornith, and the profile moved anyway

`ornith` was the last profile owing itself to `llama-cpp-20260827`, the NVFP4 build, although its
weights are Q5_K_M and never touched an NVFP4 kernel. Control against `llama-cpp-b10826` with the
production argument list unchanged, only the binary varying: `bench.ps1`, 37,981-token prompt of
real llama.cpp sources, 512 tokens, seed 42, temperature 1.0, thinking off, 3 runs, median decode.

| Build | Decode | Prefill | VRAM | Window |
|---|---|---|---|---|
| 2026-08-27 | 166.32 tok/s | 10,682 tok/s | 12,696 MiB | 262,144 |
| b10826 | 167.44 tok/s | 10,796 tok/s | 12,697 MiB | 262,144 |

0.7% and 1.1%: noise on three runs. `draft_n` is absent on both, as expected, no MTP file exists
for this model. The vision projector loads on both. A reasoning control (two trains, relative
speed) answers correctly in French on b10826, so the chat template travels fine.

**Moved regardless, and the reason is not speed.** Seven build directories sit on the disk, four
of them in service (see [building-llama-cpp.md](building-llama-cpp.md)), and each has to earn its
keep. `llama-cpp-20260827` now justifies itself through `qwen` alone, which
genuinely needs the NVFP4 kernels; b10826 is an official binary, unzipped, not a local
compilation. One fewer profile depending on something we compiled ourselves.

### The profile-to-build pairing now lives in one table

Six switch branches each quoted their binary by hand at the `Start-LLM` call site, while the same
script already resolved the port through a `$ports` table. The pairing moves far more often than
the port does: `tiel` on 2026-09-06, `ornith` on 2026-09-08. A `$builds` table now holds it, one
row per profile, and `Start-LLM` reads it when no binary is passed explicitly. Bench launchers
still pass one, which is how a profile is run against another build without editing this file.

Proved by execution, not by reading: each of the four distinct binaries was started through the
table and the running process path checked. `embed` came up on the frozen turboquant build,
`qwenu` on upstream, `qwen` on the 2026-08-27 build, `ornith` and `tiel` on b10826.

### `set "PATH=..." && ...` inside `cmd /c` swallows the whole command line, again

`bench-launch.ps1` still carried the pattern that `Start-LLM` documents against since 2026-09-01:
`cmd /c` strips the outer quotes, `set PATH=<value> && <rest>` then absorbs everything after it
into the variable's value. Nothing runs, no log file is created, and `Win32_Process.Create` still
returns 0: the launch reports success and the server never exists. It cost one run here before
the empty 2-byte log gave it away. Both bench launchers now set `$env:PATH` on the PowerShell
process and let the child inherit it, like production does.

### The repository copy of `llm-ctl.ps1` had drifted from the machine

Comparing the two before deploying showed the repository copy was **behind on code and ahead on
comments**. It was missing the 2026-09-05 fix that removes `--load-mode` from `embed` (the frozen
turboquant build dies on that flag) and the dynamic profile list in the `NO_INSTANCE` message,
while carrying better-written comment blocks for b10826 and for Tiel's draft depth. Deploying the
repository file as-is would have broken the embedder. Reconciled in both directions on 2026-09-08:
the machine keeps the code, the repository's comments were merged in, and the two files are now
byte-identical modulo line endings. Worth a check before any future deploy from the repository.

---

## 2026-09-06: Tiel on the official b10826 binary, two slots, and a strict-instruction bench

### The official b10826 binary is neutral in decode and +5% in prefill

Posted flat into `llama-cpp-b10826` from the release zip plus its cudart, no compilation, the
same way as b10740 on 2026-09-01. Control on the `tiel` profile, 65,615-token synthetic code
prompt, 400 tokens forced, seed 42, temperature 0.6, prompt cache off, 3 runs, median:

| Build | Prefill | Decode | VRAM | MTP counters |
|---|---|---|---|---|
| 2026-08-27 (b10643) | 8,724 tok/s | 211.3 tok/s | 31,707 MiB | 339 / 229 |
| **b10826** | **9,155 tok/s** | 210.8 tok/s | 31,550 MiB | 341 / 228 |

Confirmed in real use by the owner: a 41,264-token opening turn read at 9,950 tok/s against
9,496 the day before. Two startup notices to know: `preserve_reasoning` is on by default since
b10763 (may lengthen prompts, `--no-reasoning-preserve` turns it off), and the server recommends
`--image-min-tokens 1024` for this vision model, which the profile does not carry yet.

### Two slots are worth it for two callers, four are not

`--parallel N --kv-unified` on b10826. Without `--kv-unified` the window is split between slots
while `/props` still announces the total (measured 2026-09-02 on the Vulkan box). Pure generation,
tiny prompts, 400 tokens forced per stream, one process with one thread per stream:

| Streams | Per stream | Total |
|---|---|---|
| 1 | 260 tok/s | 260 |
| 2 | 202 + 188 tok/s | 390 |
| 4 | 97 to 104 tok/s each | 400 |

With 40,000-token prompts arriving together the picture changes: two streams finish in 11.8 s,
exactly the time of two sequential requests, and each stream drops to 15 to 50 tok/s while the
other reads its prompt. Four streams finish in 30.9 s against 23 s queued: slower than no
parallelism at all. A single stream on four idle slots loses 3% (205 against 211).

Retained: `--parallel 2 --kv-unified`. VRAM 31,538 MiB at rest, 32,028 MiB under two-stream
load (580 MiB headroom). Four slots rejected.

### Temperature 0.6 instead of 1.0, trial

Ornith's model card recommends 0.6 for general use and reserves 1.0 for reproducing its
benchmarks. Claude Code sends no temperature (verified by capturing a request: only `thinking`,
`output_config.effort` and `max_tokens` are sent, none of which llama-server maps to a
reasoning budget), so the server value is what every session runs at. Set on 2026-09-06 as a
trial on real usage; the strict-instruction bench below could not discriminate because it passed
at 1.0.

### A strict-instruction bench passes at 1.0, so the reported misbehaviour is elsewhere

12 strict-format prompts (single word, exact JSON, four-line list, code without comments, banned
word) on `/v1/chat/completions`, seed 42: 36/36. 8 file-editing tasks through Claude Code (rename
a variable, create a file, replace a word, answer with one word): 8/8 in a fresh session, 7/8
after a forced 55,000-token read placed before the instruction, the miss being an ambiguous
prompt. The only "failure" seen came from this workstation's own UserPromptSubmit hook, which
demands an acknowledgment line and contradicts "answer OK only": the model obeyed the hook.

Read from the GGUF: Tiel embeds the Sharp chat template `qwen3.8-froggeric-v22.4.0` with a
force-appended terseness system prompt (`terse` kwarg, default true), thinking on and reasoning
effort `medium` by default. Ornith's own template raises on a late system message like Qwen's;
a one-line derivative is staged next to the weights for a later A/B, unused.

---

## 2026-09-01: the context ceiling was lifted to 384k, and 512k was rejected

### `--override-kv` is what actually raises the window

The 2026-08-28 entry below closed on a rule: only raise `--ctx-size` with an
`--override-kv` that genuinely extends the window. That is what was done here.
`--override-kv qwen35.context_length=int:393216` lifts the value the GGUF
declares, `--ctx-size 393216` then sizes both the slot and the buffers on it.
`/props` returns `default_generation_settings.n_ctx = 393216` and the log prints
`n_ctx_slot = 393216` with no capping line.

### The cost of window is not linear, and that is the finding

Same 50,480-token prompt of real prose, 800 tokens forced, fixed seed, cold
prefill on a fresh process each time, override in place:

| Window | VRAM | Decode | Prefill |
|---|---|---|---|
| 262,144 | 27,110 MB | 123.6 tok/s | 4,007 tok/s |
| **393,216** | **31,291 MB** | **122.5 tok/s** | **4,035 tok/s** |
| 524,288 | 31,858 MB | 94.2 tok/s | 2,308 tok/s |

Half again as much window costs **4.2 GB of VRAM and nothing else**, both
throughput figures inside the noise. Doubling it costs a quarter of the decode
and 43% of the prefill.

At 524,288 throughput also stops being **reproducible**, which is its own signal.
Six cold runs spread from 71.9 to 94.9 tok/s decode and 1,716 to 2,333 tok/s
prefill; 262,144 and 393,216 each held within 1% across three runs. A profile
whose numbers will not repeat is a profile sitting on a wall.

### The wall is between 31.3 and 31.9 GB, not at 29

The `q4_0` cache entry had put the throttling threshold around 29 GB. That was
the point where a heavier KV cache started costing, not a hard edge: 31,291 MB
runs at full speed here. The edge is narrower and higher than we thought, and it
is worth knowing because it leaves 1,316 MB free. This profile now has no room
for another GPU tenant.

### What is NOT proven

Recall past 262,144. A window the server accepts says nothing about what the
model still finds in it, and 262,144 is where the model was trained. A
needle-in-a-haystack run above 300k was attempted the same day and abandoned when
the client dropped the connection at 58% of the prefill; the server logged a
clean task cancellation and stayed up. Two things were learned from the attempt
anyway: prefill decays badly on very long prompts, from 1,203 tok/s at 143k down
to 796 tok/s at 233k, and a 400k prompt therefore needs a client that will hold a
connection for ten minutes. Treat the top third of the window as unproven.

### The client value moved in the same commit

`CLAUDE_CODE_MAX_CONTEXT_TOKENS` is now 393216 in the launcher. It has to move
with the server value, always: a client promised more than the server serves is
truncated server-side with no warning.

---

## 2026-08-31: the n-max sweep was measured on short prompts only

### Every sweep before this one used a single, short prompt

The 2026-08-27 entry below settled on `--spec-draft-n-max 3`, and the 2026-08-18
entry settled on 2. Both were measured on one prompt of 10,608 tokens. Neither
asked what happens at the context length this box actually serves.

Re-swept at **both** empty and full (150k) context, on two distinct workloads,
3 seeds, median decode tok/s:

| n-max | reasoning / empty | reasoning / 150k | code / empty | code / 150k |
|---|---|---|---|---|
| 2 | . | 71.01 | . | 65.06 |
| 3 | 152.50 | 76.90 | **139.24** | 66.19 |
| **4** | **169.83** | **83.03** | 129.88 | **71.21** |
| 5 | 165.28 | 79.81 | 125.47 | 73.82 |
| 6 | 159.20 | 79.38 | 118.33 | 61.05 |
| 8 | 125.22 | 69.10 | 100.58 | 60.92 |

`n-max 4` wins three cases out of four, by 7.6 to 11.4%, and loses only on code
at empty context. That is the least representative case here: under an agentic
client the context is never empty, the system prompt alone exceeds ten thousand
tokens on the first turn. Applied to the `qwen` profile. `qwenu` was left at 3,
it has not been re-swept.

Why the law inverts: at full context, decoding **one** token costs far more,
since attention sweeps the whole context. Verifying several tokens in a single
pass therefore amortises a longer draft, whereas at short context the draft
dominates the cost.

> Acceptance rate falls monotonically as n-max rises, so it is not the criterion.
> Only throughput is. At 63.3% acceptance, n-max 3 yields less than n-max 4 at
> 55.1%.

### `iq4_nl` on the KV cache: same size, 150x slower prefill

`iq4_nl` occupies exactly as much as `q4_0`, 4.5 bits per element, with a
non-linear table and therefore better fidelity on paper. It was applied to both
Qwen profiles and caught mid-benchmark:

| KV cache type | Prefill at 150k |
|---|---|
| `q4_0` | ~4,000 tok/s |
| `iq4_nl` | **25.9 tok/s** |

The Flash Attention CUDA kernels do not cover this type and the engine falls
back to a slow path. Reverted the same session.

> Cache size tells you nothing about cache speed. Only the compiled kernel set
> decides. Check FA support before trading one quant type for another.

### `--reasoning-preserve` does nothing here

The server suggests it at load time: `chat template supports preserving
reasoning`. Tested across all four combinations, same 3-turn conversation, same
seed:

| Client resends reasoning | Server flag | Prompt at turn 3 |
|---|---|---|
| yes | off | 2,846 |
| yes | on | 2,846 |
| no | on | 219 |
| no | off | 219 |

The flag changes nothing in either direction. What carries the reasoning across
turns is the client resending `reasoning_content`, not the server. Not retained.

### Reference figures that were missing: context length dominates everything

| Workload | Empty context | 150k context |
|---|---|---|
| reasoning | 169.83 tok/s | 83.03 tok/s |
| code | 129.88 tok/s | 71.21 tok/s |

Prompt length roughly halves throughput, far beyond what any flag returns. The
counterpart is that the prompt cache earns its keep: switching workload on the
same 150k context re-prefilled **2,046 tokens instead of 149,706**, about 70
seconds saved.

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

That holds for the short prompt it was measured on. At full context the ranking
reverses and n-max 4 wins; see the 2026-08-31 entry above.

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

Measured on a single 10,608-token prompt. Superseded for the `qwen` profile by
the 2026-08-31 sweep above, which found the ranking inverts at full context.

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
