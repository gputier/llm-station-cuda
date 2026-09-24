# Harness choices

One section per suite whose harness was not an obvious pinned repository, or
whose call convention needed checking before commit (plan step "verify before
implement").

## Long context: RULER and LongBench v2 (Task 11)

Read at the pinned commits (`RULER_SHA`, `LONGBENCH_SHA` in `pins.env`), 2026-09-23/24.

### RULER (`NVIDIA/RULER`)

`scripts/pred/client_wrappers.py::OpenAIClient` does NOT take a `base_url`
argument. Sure (read the constructor at the pinned commit): it builds
`openai.OpenAI(api_key=self.openai_api_key)` with no `base_url` kwarg. The
`openai` Python SDK falls back to the `OPENAI_BASE_URL` environment variable
when `base_url` is not passed (confirmed by reading
`openai/openai-python` `src/openai/_client.py`, `elif base_url is None:
base_url = os.environ.get("OPENAI_BASE_URL")`), so RULER's `--server_type
openai` DOES reach a generic OpenAI-compatible endpoint, without touching the
pinned file, by setting `OPENAI_BASE_URL` on the container.

Two blockers remained, both worked around from the launcher side (rule 3, no
fork of the pinned repo):

1. `OpenAIClient.__init__` looks up the requested model name in a small
   hardcoded `model2length` dict (OpenAI and Azure model names only) and
   raises `KeyError` for any other name, our served alias included.
   `bench/harness/ruler_run.py` replaces only `OpenAIClient.__init__` at
   runtime (a launcher-side monkeypatch, same posture as `lcb_run.py`'s
   registration of a served alias for LiveCodeBench, ruling V) with a version
   that drops the dict lookup and always uses a generous ceiling, then hands
   off to the unmodified `pred/call_api.py::main()` via `runpy`.
2. `pred/call_api.py` and `eval/evaluate.py` import
   `nemo.collections.asr.parts.utils.manifest_utils` for two functions
   (`read_manifest`, `write_manifest`) that are a five-line JSONL read/write
   in the pinned repo itself
   (`scripts/data/manifest_utils.py`, read at the same commit). Installing
   `nemo-toolkit[all]` for two functions would pull torch, pytorch-lightning
   and a CUDA stack never imported on the OpenAI-endpoint path, the same shape
   of problem as LiveCodeBench's torch dependency (Task 9). A stub package
   (`bench/harness/ruler_stub_nemo/`) shadows only that import path with the
   same two functions, ahead of the real `nemo` on `PYTHONPATH`; nothing else
   under `nemo.*` is touched or importable.

Tokenizer: `--tokenizer_type openai --tokenizer_path cl100k_base` (tiktoken),
which `scripts/data/tokenizer.py` supports directly at the pinned commit;
this sizes the haystack for the prompt-generation step only, it is not the
tokenizer the served model actually uses (see the token-margin note below).

A third blocker, found live rather than by reading: `OpenAIClient` does not
inherit from `client_wrappers.Client` and has no `process_batch`, which
`call_api.py`'s `get_output()` calls unconditionally; the pinned
`--server_type openai` path is unusable as shipped (an unguarded `while True`
retry loop spins forever on the resulting `AttributeError`, no request ever
sent). `ruler_run.py` also assigns
`OpenAIClient.process_batch = Client.process_batch`, reusing the base
class's own method unchanged rather than reimplementing it.

Token margin: RULER sizes the prompt with `cl100k_base`, not the served
model's own tokenizer, so the request actually sent is larger than the
requested length. Measured 2026-09-23/24 on real passes (nex on the 99):
`niah_single_1` (noise haystack) 33989 model tokens against a 32752-token
cl100k count, ratio 1.038; `niah_multikey_2` (needle haystack, numeric/needle
text) 41655 against 32685, ratio 1.274. `benchrun/suites/ruler.py`'s
`lengths_to_run()` multiplies every requested length by the largest measured
ratio (1.3, with margin) plus the config's `max_tokens`, before comparing to
the served window, so a request is skipped rather than silently overflowing
the model's own context.

### RULER's 13 canonical tasks

All 13 tasks in `scripts/config_tasks.sh` are wired into `bench-ruler`
(`benchrun/suites/ruler.py` `DEFAULT_TASKS`), proven by generating real data
for each at `--max_seq_length 32768` (no model needed) and by one real
request each on an essay task (`niah_single_2`) and a QA task (`qa_1`),
2026-09-24. Five tasks need the Paul Graham essay corpus
(`niah_single_2`, `niah_single_3`, `niah_multikey_1`, `niah_multivalue`,
`niah_multiquery`), two need a QA dataset (`qa_1`: SQuAD, `qa_2`: HotpotQA);
all three are baked into the image at build time (`ruler.Dockerfile`), no
download at suite run time:

- Essay corpus: the pinned repo's own `download_paulgraham_essay.py`, run
  unmodified (rule 3), assembles it from 218 URLs listed in the pinned
  repo's own `scripts/data/synthetic/json/PaulGrahamEssays_URLs.txt` (fixed
  by `RULER_SHA`). There is no single URL for the assembled result, so its
  sha256 is verified against `RULER_ESSAY_CORPUS_SHA256` in `pins.env`
  instead (measured 2026-09-24); the build fails if any of the 218 pages now
  serve different bytes.
- SQuAD and HotpotQA: single pinned URL and sha256 each
  (`RULER_SQUAD_URL`/`_SHA256`, `RULER_HOTPOTQA_URL`/`_SHA256` in
  `pins.env`), read from the pinned repo's own
  `download_qa_dataset.sh`. HotpotQA's primary URL
  (`curtis.ml.cmu.edu`) carries no version pin, so the image uses the same
  commit-pinned Hugging Face mirror the pinned script itself falls back to.

**The day the essay corpus sha256 gate breaks** (`bench-ruler`'s image build
fails on the `sha256sum -c` check against `RULER_ESSAY_CORPUS_SHA256`): this
means at least one of the 218 URLs in the pinned repo's own
`scripts/data/synthetic/json/PaulGrahamEssays_URLs.txt` now serves different
bytes than on 2026-09-24 (an edited essay, a changed footer/analytics
snippet, a 404 turned into a redirect page, and so on); `RULER_SHA` pins the
URL list itself, not the live pages it points to, so this is an external
drift the pin cannot catch in advance. Do, in order:

1. Do NOT loosen or drop the check: an unverified essay corpus can silently
   change every niah/multivalue/multiquery haystack task's difficulty and
   invalidate every RULER score across every model already benched against
   the old corpus.
2. Rebuild `bench-ruler` with a shell open in the failed build layer (or
   `docker run --rm bench-ruler:<previous tag> ...` against a fresh
   `download_paulgraham_essay.py` run) and diff the new assembled corpus
   against a saved copy of the old one (keep one in the image's build cache
   or re-download once and archive it outside the repo) to see exactly which
   essay(s) changed and by how much.
3. If the drift is cosmetic (a byte-for-byte irrelevant change, e.g. a
   tracking script insert) and the corpus is otherwise the same essays: bump
   `RULER_ESSAY_CORPUS_SHA256` in `pins.env` to the new measured value,
   record the date and the diff summary in this file, and rebuild.
4. If the drift is substantive (an essay removed, replaced, or edited in a
   way that changes its content meaningfully): do not just re-pin silently;
   note it here with the date, since old fixtures/results captured against
   the previous corpus are no longer reproducible against a freshly built
   image, and re-run the affected RULER tasks if a campaign is in flight.
5. `shasum -a 256` on the freshly assembled corpus (same command the build
   uses) gives the new value directly; do not guess it from the failing
   build's own error message alone, always re-measure.

A fourth, unrelated real bug found while proving the 13 tasks:
`json/english_words.json` (the `cwe` task's word list) is a git-lfs pointer
file in the pinned repo; a plain `git clone` leaves the 132-byte pointer text
in place instead of the real 8.5 MB file, and `cwe`'s data generation fails
with a `JSONDecodeError` on it. `ruler.Dockerfile` installs `git-lfs` and
runs `git lfs pull` after checkout.

### RULER's metric: string_match_all vs string_match_part (round 3, 2026-09-24)

`scripts/eval/synthetic/constants.py` (RULER_SHA) scores every task with one
of two per-sample functions before averaging: `niah`, `variable_tracking`,
`common_words_extraction` and `freq_words_extraction` use
`string_match_all` (the FRACTION of references literally found in the
prediction); `qa` uses `string_match_part` (1.0 if ANY reference is found,
0 otherwise). The prior version of `parse_ruler` applied `all()` to every
task, the `string_match_all` behavior only: `qa_1`/`qa_2` (SQuAD/HotpotQA,
each sample carrying several paraphrased reference answers, for example
`["10th and 11th centuries", "in the 10th and 11th centuries", ...]`) failed
about 40 percent of items whatever the model answered, since a correct
answer rarely repeats every paraphrase verbatim. `benchrun/suites/ruler.py`
now ports both metrics exactly (`_score_all`, `_score_part`, selected by
`QA_TASKS`), records the harness score (0 to 1, `detail["score"]`) on every
row, and sets `passed = 1` only when the score is 1 (see `parse_ruler`'s
docstring). Proven on real reference sets generated by the pinned repo's own
`data/prepare.py` (`niah_multivalue`, 4 references per sample; `qa_1`, 4
paraphrased references per sample; no model needed for this proof) and on
one real request pass (below).

### Retry cap and subprocess timeout (round 3, 2026-09-24)

Two independent bugs, found on adversarial review and confirmed by reading
`client_wrappers.py` at RULER_SHA: `OpenAIClient._send_request` (a) is
wrapped in `tenacity`'s `@retry(stop_after_attempt(3))` but (b) its
`except Exception` branch prints the error and falls through to
`return response` without `response` ever having been assigned, so every
failed attempt raises `UnboundLocalError` instead of the real error, and
after 3 attempts tenacity raises `RetryError`; `call_api.py`'s own
`get_output()` (`while True: try: ... except Exception: traceback.print_exc()`,
unmodified) then retries that `RetryError` forever with no cap. A
persistent endpoint error (wrong port, an endpoint answering 500 on every
request) spun the container indefinitely instead of failing.

Fixed from `ruler_run.py` (rule 3, no fork): `OpenAIClient.__call__` is
replaced with a corrected version that performs the same request but lets a
real exception propagate, and `OpenAIClient.process_batch` is replaced with
a version that counts consecutive failures and gives up after
`MAX_CONSECUTIVE_FAILURES` (3). Proven live against a stub endpoint that
always answers HTTP 500 (`python:3.11.10-slim` running a five-line
`http.server` handler, on `bench-net`): the first attempt used
`sys.exit(1)`, which only terminated the failing worker thread (`get_output`
runs on its own `threading.Thread`, one of `args.threads`, not the main
thread) and left the container exiting 0 with an incomplete prediction
file, a bug in the fix itself, found by checking the exit code rather than
trusting the log line. Replaced with `os._exit(1)`, which terminates the
whole process from any thread; the same stub proof (2026-09-24) now exits 1
in 15 s.

`benchrun/suites/ruler.py`'s `RulerSuite.run()` now also passes a timeout to
every `docker run` call, computed by `subprocess_timeout_s(per_task,
max_tokens, length)`: it scales with how many samples a wave must generate
(the config's own `max_tokens`), how many sequential waves `per_task`
requires (`call_api.py`'s own 4-way thread concurrency), and the requested
`length` (prefill cost). A persistent failure now fails through the retry
cap above long before this timeout would ever fire (proven live, exit 1 in
15 s against a stub that always answers 500); the timeout is the safety net
for a hang the retry cap does not cover (a connection that never responds at
all). `LongBenchV2Suite` does the same with its own `subprocess_timeout_s`,
scaled differently because `longbench_run.py` processes samples one at a
time rather than in concurrent waves.

#### Timeout floors and the circuit breaker's total-failure budget (round 4, 2026-09-24)

Two more findings on adversarial review:

- The round-3 timeout used a single flat rate (`MEASURED_TOKENS_PER_SECOND`,
  30 tokens/second, an aggregate wall-clock figure from one fast model, nex,
  divided across concurrent requests, not a per-request rate) for every
  config. A slower model, or one that spills out of VRAM, would progress
  more slowly than that guess and get killed mid-run, failing its whole
  configuration even though it was making real progress. Fixed: the timeout
  now reads two floors directly from the real per-request `timings` every
  llama-server response carries (`predicted_per_second` for decode,
  `prompt_per_second` for prefill), scanned across every gateway journal on
  disk this session (Tasks 9-15's own real passes): the slowest real decode
  measured anywhere is 135.98 tokens/second and the slowest real prefill is
  98.96 tokens/second, both on `nex`, both from this task's own `qa_1` and
  `niah_multivalue` passes (`ruler.py`'s `PREFILL_FLOOR_TOKENS_PER_SECOND`
  and `DECODE_FLOOR_TOKENS_PER_SECOND`, both set to 50.0, well under either
  measurement). No CPU-offloaded or spilling configuration has a real
  per-request timing captured in any surviving journal on this machine as of
  this task, so the floors are an explicit margin below the slowest real
  measurement, not the measurement itself: a genuinely slower config found
  later may still need the floors lowered again, at which point this
  paragraph should be updated with the new measurement and date.
  `LongBenchV2Suite` shares the same two floors and reasoning (its own
  `subprocess_timeout_s` in `benchrun/suites/longbench.py`), since it runs
  on the same station against the same models.
- `ruler_run.py`'s `_consecutive_failures` counter was a plain `int`,
  mutated from up to `RULER_THREADS` (4) concurrent worker threads with no
  lock, and reset to 0 by any single success. Two real bugs: (a) a data
  race, an unlocked read-increment-write from concurrent threads can lose an
  increment; (b) a flaky endpoint that fails about one request in two never
  accumulates 3 CONSECUTIVE failures (a success resets the counter before
  three failures can stack up), so it never tripped the breaker at all, and
  the pinned retry-forever loop kept calling it, one retry per flaky sample,
  for the whole run. Fixed: both counters (consecutive and total) are read
  and written under `_FAILURE_LOCK`, and a TOTAL failure budget
  (`_max_total_failures`, set from `args.num_samples` in `main()`, floor
  `MIN_TOTAL_FAILURES` = 10) trips independently of the consecutive count.
  Proven live against a stub that fails every other request
  (`stub_openai_flaky.py`, a thread-safe global counter, `_COUNT % 2 == 0`
  fails) and by a dedicated unit test file, `bench/tests/test_ruler_run.py`
  (4 tests, `_bounded_process_batch` imported directly and exercised against
  a fake `client_wrappers` module, no container needed): a strictly
  alternating fail/succeed pattern that never reaches 3 consecutive still
  trips the total budget; 20 concurrent threads all failing at once are all
  counted, none lost, proving the lock. RED then GREEN: reverted to the old
  unlocked, consecutive-only, reset-by-any-success logic, 3 of the 4 new
  tests failed (the plain persistent-failure test, unchanged behavior,
  stayed green), restored, 4 passed.

### Truncated answers (round 3, 2026-09-24)

`parse_ruler` now joins each prediction to a sibling
`<save_dir>/<task>.meta.jsonl`, written by `ruler_run.py`'s patched
`__call__` (keyed by the sha256 of the exact prompt text, since
`call_api.py`'s pinned code never passes a sample's index down that far):
`finish_reason` and `completion_tokens`, neither field the pinned
`call_api.py` keeps on its own (`outputs_parallel` only ever holds
`pred`/`input`/`outputs`/`others`/`truncation`/`length`). A sample whose
`finish_reason` is `"length"` (the model was cut off) is now forced to
`passed = 0` regardless of the harness score: a truncated answer proves
nothing about the model's actual answer.

### LongBench v2 (`zai-org/LongBench-v2`, replaces NoLiMa, ruling AC)

NoLiMa (`adobe-research/NoLiMa`) was removed 2026-09-24: its license
(Adobe Research) is noncommercial research use only, incompatible with this
benchmark's use. Every NoLiMa file was removed with `trash`
(`benchrun/suites/nolima.py`, `harness/nolima.Dockerfile`,
`harness/nolima_run.py`, `harness/nolima_stub/` and its 11 files,
`tests/test_suite_nolima.py`, `tests/fixtures/nolima_result_sample.json`,
its `pins.env` entry and this section, 16 files total); `NOLIMA_SHA` no
longer appears anywhere in the tree.

Replacement, chosen and confirmed 2026-09-24: `zai-org/LongBench-v2` on
Hugging Face, dataset card read directly
(`huggingface.co/datasets/zai-org/LongBench-v2`, revision
`LONGBENCH_V2_DATA_REVISION` in `pins.env`), license `apache-2.0` (the
`cardData.license` field and the repo tag both say so). The dataset is 503
real multiple-choice questions (`_id`, `domain`, `sub_domain`, `difficulty`,
`length`, `question`, `choice_A..D`, `answer`, `context`) with contexts from
real-world documents (papers, books, code repositories, dialogues,
structured data) collected from about 100 contributors per the dataset
card; the card does not carry a separate license for the underlying source
documents (probable, not sure: the card is silent on this, the same posture
as most long-context benchmark releases built from mixed real-world text
under a research/fair-use framing). Measured directly on the downloaded
`data.json` (465490535 bytes, sha256 in `pins.env`), 2026-09-24: 503 items,
`length` field breaks down to short 180 / medium 215 / long 108, word counts
short 8205-32173 (median 14116), medium 33139-129194 (median 71579), long
131841-1380741 (median 204090).

Official harness: `THUDM/LongBench` (root of the repo, MIT license,
`LONGBENCH_SHA`), `pred.py`/`result.py`. Read at the pinned commit: `pred.py`
hardcodes `URL = "http://127.0.0.1:8000/v1"` and, to pick a tokenizer for
truncation, does a strict dict lookup (`model_map`/`maxlen_map`, loaded from
`config/model2path.json`/`config/model2maxlen.json`) that raises `KeyError`
for any model name it does not already know (our served alias included), and
this lookup executes as MODULE IMPORT-TIME code (`model_map =
json.loads(open(...))` at the top of the file), before any monkeypatch of
ours could run, unlike RULER's `call_api.py` where the failing lookup is
safely inside a function. `bench/harness/longbench_run.py` therefore does
not import `pred.py`/`result.py` as modules (rule 3, no fork): it reads
`prompts/0shot.txt` directly from the cloned, pinned repo (the 0-shot
multiple-choice template, byte for byte, unmodified) and re-implements
`pred.py`'s `extract_answer()` (the answer-letter regex) and `result.py`'s
`judge = pred == answer` scoring rule verbatim in the launcher, with the
launcher's own docstring citing exactly which pinned lines each piece comes
from. Tokenizer: `cl100k_base` via `tiktoken` for every model, same choice
and same reasoning as `ruler_run.py` (our aliases take neither branch of the
pinned `model_map` lookup, so `cl100k_base` is the harness's fallback path
made explicit).

Length policy, same coarse skip as RULER (`benchrun/suites/ruler.py`'s
`lengths_to_run()`, reused directly by `LongBenchV2Suite`): two target
context sizes, 32768 and 131072, a whole bucket skipped (recorded, not
silently dropped) when the config's window cannot fit it at all;
`SuiteSkipped` is raised when every bucket is skipped.

#### Selection and bands (round 4 fix, 2026-09-24)

Two real bugs, found on adversarial review, both in `longbench_run.py`'s own
item selection, upstream of anything RULER shares:

- **Silent truncation after selection.** The first version selected
  candidates by measuring the dataset's `context` field ALONE against the
  token budget, then built the full prompt (template plus context plus
  question plus the four choices) separately and let `_truncate_to_budget`
  cut it down with no trace if it did not fit; measured, 2 of 20 items at
  `max_tokens` 16384 were cut silently, since the template and question text
  add real tokens the context-only check never saw. Fixed: `_select_items`
  now builds each candidate's FULL prompt first and measures that exact
  quantity against the budget, so a selected item is proven to fit before
  being sent. `_truncate_to_budget` stays as a documented safety net (a
  tokenizer version drift, a rounding difference) but now returns whether it
  fired and how many tokens it cut, both recorded per item
  (`input_truncated`, `input_tokens_cut`) instead of silently, and the
  launcher logs to stderr if it ever fires despite passing selection.
- **Overlapping buckets.** The 32768 and 131072 buckets used to select
  independently, each drawing candidates from the dataset's own `length`
  field and taking the first N sorted by `_id`, with no exclusion between
  them; measured, 70 percent of the 131072 bucket's items were also in the
  32768 bucket, so a model's 131072 score was mostly re-testing the same
  items as its 32768 score. Fixed: `LongBenchV2Suite` computes a
  `(band_floor, band_ceiling]` of real, full-prompt token counts per target
  length (the previous length in the sorted target list is the floor, 0 for
  the smallest), passed to the launcher as `--band-floor`/`--context-length`;
  an item's actual full-prompt size places it in exactly one band by
  construction, so no `item_id` can be selected by two different
  invocations. This corresponds loosely to the dataset's own `length` field
  (word counts per length above, "Measured directly on the downloaded
  `data.json`"), but the band boundary the launcher enforces is the real
  token count, not the field, since a field value near a boundary could land
  on either side once the template and question are added.
- Selection within a band's eligible pool is seeded
  (`random.Random(SELECTION_SEED).sample`), not "first N sorted by `_id`",
  so a large candidate pool is not always reduced to its alphabetically
  first slice.

Proven by a dedicated unit test file, `bench/tests/test_longbench_run.py` (8
tests, `_select_items`/`_truncate_to_budget` imported directly with a fake
tokenizer, no container needed): a full prompt larger than its context alone
is rejected even when the context alone would fit; two adjacent bands over
the same dataset never share an `_id` and together cover every item; the
seeded selection is deterministic across calls and not always the
alphabetically first slice. RED then GREEN: reverted to the old
context-only, no-band, first-N-sorted logic, 4 of the 8 tests failed exactly
the ones targeting this bug, restored, 8 passed.

Within an eligible band, items are still checked (in `_select_items`)
against the config's real window with the measured-ratio margin RULER uses,
skipping any individual item that does not fit (recorded to
`longbench_v2_selection_skipped.json`, not silently dropped).

Bounded retries: `longbench_run.py` processes samples on a single thread
(unlike RULER's threaded `get_output`), so `sys.exit(1)` on a persistent
failure is safe here (RULER's `os._exit` thread pitfall does not apply):
`MAX_ATTEMPTS` per item with a short backoff, then a hard stop after
`MAX_CONSECUTIVE_FAILURES` (3) consecutive item failures.

Truncated answers: every output line carries `finish_reason` and
`completion_tokens` (from `response.usage`, discarded by the pinned
`pred.py` the same way RULER's `call_api.py` discards them); a sample whose
`finish_reason` is `"length"` is forced to `passed = 0` in
`parse_longbench_v2`, same rule as RULER.

Memory, harness container alone, no model (`docker stats --no-stream` in a
bounded loop, endpoint unreachable so no request ever completes): peak
2.252 GiB at the 131072 bucket, 2026-09-24. Higher than every RULER task
(about 1.1 GiB at its own 131072 peak, `RULER's 13 canonical tasks` above)
because `longbench_run.py` parses the whole baked 465 MB `data.json` into
memory before filtering by bucket, unlike RULER's per-task, per-length
synthetic generation; still comfortably inside the 7.75 GiB VM budget given
this harness rule's own "one container at a time" discipline, so left as
measured rather than optimized (a streaming JSON parser would need a new
dependency for a cost that already fits).

## Agentic suite: public sub-score (Task 12)

The agentic suite (`benchrun/suites/agentic.py`) scores two populations,
reported separately by their `item_id` prefix:

- `private/*`: 9 tasks built from real fix commits in repositories under
  the machine's own checkout root (`BENCH_REPOS_ROOT`, see below; the
  README used to assume `~/git_projects`, stale on a machine where that
  root lives elsewhere, e.g. `/Volumes/Git Ext./git_projects`; see
  `Bench-LLM/tasks/SOURCES.md` in the companion private repository for
  provenance). A private task's `task.json` carries
  everything needed to reproduce its tarball byte-for-byte (`repo`,
  `fix_commit`, `parent_commit`, `subdir`, `tree_id`, `benchrun.tasks.builder`);
  the tarball itself is a derived artifact, cached at `Bench-LLM/.cache/tasks/<tree_id>.tar.gz`
  (gitignored, never committed) and rebuilt on demand by `AgenticSuite` if
  missing, its tree id checked against the recorded one before use.
  `BENCH_REPOS_ROOT` (environment variable) must be set to that rebuild's
  own checkout root for the one case that needs it, a cache miss;
  `AgenticSuite` never requires it otherwise and raises a clear
  `ReposRootNotConfiguredError` naming the variable if a rebuild is needed
  and it is unset. `bench/.env.example` normally documents this, but this
  repository's own write policy blocks a file matching `.env*`, so it is
  documented here instead: copy the line below into your own shell profile
  or `.env`, pointing at wherever your checkout root actually is:
  `BENCH_REPOS_ROOT=/Volumes/Git Ext./git_projects`.
- `swe/*`: a seeded subset of public tasks drawn from
  [nebius/SWE-rebench-leaderboard](https://huggingface.co/datasets/nebius/SWE-rebench-leaderboard)
  on Hugging Face, licensed CC-BY-4.0. Dataset pinned by revision sha
  (`SWE_REBENCH_SHA` in `pins.env`); selected instance ids, draw seed and
  selection rule recorded in `Bench-LLM/sets/swe-rebench-subset.txt`
  (script: `Bench-LLM/sets/draw_swe_rebench_subset.py`).

Attribution (CC-BY-4.0): SWE-rebench-leaderboard is published by Nebius.
Citation:

```bibtex
@misc{badertdinov2025swerebenchautomatedpipelinetask,
      title={SWE-rebench: An Automated Pipeline for Task Collection and Decontaminated Evaluation of Software Engineering Agents},
      author={Ibragim Badertdinov and Alexander Golubev and Maksim Nekrashevich and Anton Shevtsov and Simon Karasik and Andrei Andriushchenko and Maria Trofimova and Daria Litvintseva and Boris Yangel},
      year={2025},
      eprint={2505.20411},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2505.20411}
}
```

Each `swe/*` task reuses the instance's own published docker_image (Docker
Hub, `swerebench/*`) rather than rebuilding an environment from scratch: the
dataset ships one ready image per instance, and the harness's own design rule
is to reuse a tool's native mechanism instead of reinventing it. Every image
seen so far is `linux/amd64`; this host is `arm64` and runs them under QEMU
emulation (`--platform linux/amd64`), which is markedly slower than a native
pull/run.

Real finding, corrects an earlier draft of this section: Claude Code's
runtime (a Bun-compiled standalone executable) does not survive that
emulation. Measured live: `claude --version` inside the SWE image prints
`ASSERTION FAILED: MemoryExhaustion` from Bun's JSC heap allocator and aborts
(`SIGABRT`, exit 134) with abundant free memory available, a QEMU/Bun
interaction bug, not a resource limit. So the agent phase never runs inside
the SWE image at all: `AgenticSuite.run` starts a SECOND, native (arm64)
container from the same `bench-agent` image the private tasks use, with
Claude Code already baked in at build time (never installed at prepare
time, `agent.Dockerfile`'s `CLAUDE_CODE_VERSION` build arg, same
`pins.env` entry the private-task image reads). The SWE container's
`/testbed` is copied onto the host with `docker cp` before the agent phase
and copied back after it; the SWE container itself only ever runs prepare
(nothing: its dependencies are already installed in the published image)
and grade (apply `test_patch`, never shown to the agent, then run
`test_cmd` against the agent's edits). Grading checks that every id in
`fail_to_pass` and `pass_to_pass` comes back `PASSED`, not just the test
command's exit code, since a SWE task's `test_cmd` legitimately runs tests
outside those two sets too.

## Orchestration: compose.yaml, docker CLI and ssh (Task 16)

Two real findings from the first live pilot run of `harness/compose.yaml`,
neither of which showed up in any unit test (both need the real host/Docker
setup):

1. **Docker-outside-of-Docker path translation.** `runner-99`/`runner-97`
   are themselves containers that call `docker run` over the mounted host
   socket (`/var/run/docker.sock`) to start each suite's harness container.
   That call is resolved by the HOST daemon, not by the runner's own
   filesystem view: a `-v <path>:...` the runner builds from `ctx.out_dir`
   must already be a real HOST path, or the daemon either cannot find it or
   resolves it against something else entirely (a container-only mount
   point named `/private` collided with macOS's own real `/private`
   directory, `mkdir: permission denied`). Fixed by mounting `BENCH_PRIVATE`
   at the identical path on both sides (`${BENCH_PRIVATE}:${BENCH_PRIVATE}`)
   instead of a container-only alias, so every suite's bind-mount argument
   is valid from wherever it is issued. `bench/` itself stays mounted at the
   virtual `/bench` since no suite ever passes a `/bench`-rooted path to a
   nested `docker run`.
2. **SSH to the stations needs the host's own ssh-agent.** Neither station's
   key authenticates as a bare file under `~/.ssh` (measured live: `ssh -vvv`
   inside a throwaway container tried every default identity name and got
   `Permission denied`); the real key material is Keychain/agent-backed.
   `compose.yaml` forwards the host's `SSH_AUTH_SOCK` into both runners
   (`${SSH_AUTH_SOCK}:/ssh-agent.sock`, `SSH_AUTH_SOCK=/ssh-agent.sock` in
   the container's own environment) so the container's `ssh`/`scp` calls
   ask the host agent to sign, exactly as an interactive shell on this Mac
   does; the private key itself never leaves the host. `docker compose`
   must be invoked from a shell where `$SSH_AUTH_SOCK` is set (any normal
   login shell on this Mac already has it).

`benchrun.Dockerfile` also now carries a docker CLI (client binary only, no
dockerd/containerd/runc: official static tarball, pinned by URL and sha256
in `pins.env`), needed for finding (1) above to be possible at all from
inside a runner container.
