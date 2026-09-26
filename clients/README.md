# Launchers

One script per model family. Each starts the right model on the right server if
needed, waits for it to actually answer, then hands over to Claude Code pointed
at it.

```bash
export LLM_HOST=your-32gb-box            # the 5090 box, every launcher
export LLM_HOST_16GB=your-16gb-box       # the second box, tiel, qwen and the three APEX launchers
export LLM_SSH_USER=your-ssh-user
export LLM_SSH_KEY=~/.ssh/id_ed25519     # optional, see below

./tiel        # the default: coding and reasoning, asks which box
./kat         # when arithmetic reasoning matters more than speed
./ornith      # the small one, a third of the VRAM, better than its size
./nex         # best quality measured here, no speculation, vision
./bonsai      # 27B in 7 GiB, the best quality per byte, asks which generation
./spark       # 4B, last on every measure
./muse        # agentic, vision, faithful OCR
./qwen        # Qwen3.8 aligned, uncensored, TWIN-TURBO or the qwenf candidate on the 32 GB box, Qwen3.6 on the 16 GB one
./hemmingway  # Altworld Hemmingway-1, candidate since 2026-09-26
./veriloop    # VeriLoop-E2, no speculation, candidate since 2026-09-26
./xing        # Xing 4.0 29B-A4B, candidate since 2026-09-26, its own engine
./qwen36apex  # Qwen3.6-35B-A3B requantised by APEX, asks which box
./katapex     # KAT-Coder-V2.5-Dev requantised by APEX, asks which box
./occamy      # Occamy 1.0 requantised by APEX, vision, asks which box
./qwen36      # bench only, 16 GB box
```

## One body for all fifteen

Every launcher is a few lines that declare its model and source
[llm-launch.sh](llm-launch.sh), never run on its own. Until 2026-09-14 the six
single-model launchers each carried their own hundred lines, and the copies had
drifted apart: two announced a window the rule below forbids, and all six
reloaded over a busy server without asking. Whatever is decided about loading,
asking and the environment is now decided in one place.

A launcher sets two things of its own when the default does not suit it:
`LLM_LOAD_TIMEOUT`, see below, and `LLM_MCP`, `none` here, or `mail-imap` for a
mail server this repository does not ship.

## tiel, qwen, the three APEX launchers and bonsai ask which model

The same families live on two boxes, so `tiel`, `qwen`, `qwen36apex`, `katapex`
and `occamy` open with a numbered menu instead of carrying one script per model
per machine. `bonsai` does the same since 2026-09-18 for a different reason:
its two generations both sit on the 32 GB box, on two different engines, and
the second ingests sixty times faster than the first for the same generation
speed. The menu marks the variant already loaded, if any.
`LLM_CHOICE=2 ./tiel -p "..."` skips it, and a run without a terminal must set
it: a menu read from a pipe would take the caller's input as a choice. A
launcher that declares one variant shows no menu.

Proven on 2026-09-14 against both boxes: an out-of-range choice is refused, a
box serving another model triggers the swap question and a "no" leaves it
untouched, a run without a terminal refuses to swap, and
`LLM_CHOICE=3 ./qwen --bare -p "..."` answered through Claude Code on the 16 GB
box.

Only a port that refuses the connection counts as free, and loads without
asking. A timeout, or an answer that names no model, counts as busy and asks: a
server deep in a long prompt can miss a three-second probe, and reading that as
an empty box would load over whoever is using it. Found by review on 2026-09-14
and replayed the same day against stubbed `curl`, `ssh` and `claude`, seven
cases out of seven. The same stubs, run on the old `ornith` with a probe that
times out, showed it loading over the server without a word; the shared body
refuses.

Which one to reach for, with the figures behind each line, is in
[../docs/quel-modele-pour-quel-usage.md](../docs/quel-modele-pour-quel-usage.md).

`embed` has no launcher on purpose: it serves embeddings, not a chat endpoint.

## The bench's own profiles, in the same menus

`bench/benchrun/profiles.py` writes one launch-profile JSON per bench config
(`bench/configs/<machine>/<model>/R{1,2,3}.yaml`), the sampling and
chat-template settings the bench's own gateway would otherwise send per
request turned into `llama-server` flags (there is no gateway day to day).
`llm-ctl.ps1` and `llm-ctl-16gb.ps1` start one with `-Action profile -Name
<model>-r<N>`, the same way `-Action bench` starts a bench spec: tracked,
logged and stopped like any hand-written profile, never like the bench run
itself.

Every model the bench compared now has an R1/R2/R3 entry in a launcher's menu,
Bonsai 1 the one exception: its R2 was retired on 2026-09-25, no engine on the
station able to read its draft model. These entries sit after the
hand-written entries already there for `tiel`, `kat`, `muse`, `nex`, `ornith`,
`spark`, `bonsai` (bonsai and bonsai2) and `qwen` (qwen, qwenu, qwent and the
qwenf candidate, all four Qwen3.8-27B builds sharing one file, per the
one-launcher-per-family rule above), and, since 2026-09-26, `hemmingway`,
`veriloop` and `xing` on the 32 GB box and `qwen36apex`, `katapex` and
`occamy` on both boxes. `qwen36` alone keeps a launcher with bench entries
only, on the 16 GB box: its permanent profile sits in `qwen`'s menu. `tiel`,
`bonsai2` and the three APEX models run on both boxes, so their bench entries
appear twice, once per machine.

Our hand-written entries and the bench ones never take each other for the
loaded model: `_llm_matches` also requires the served alias to equal the last
word of the entry's action, `tiel` for the everyday profile, `tiel-r1` for
the bench one.

`qwen36apex`, `katapex` and `occamy` moved to both boxes on 2026-09-25, once
each was found to spill out of the 16 GB card at the full 262,144 window
(about 30 tokens/s instead of 130): the placement rule is that a model runs
entirely on the card at that window, with no overflow, or it changes machine
or quantization. Their full quant now runs on the 32 GB box; the 16 GB box
keeps a lighter quant of the same weights (NanoPlus for `occamy` and
`qwen36apex`, APEX-dynamic-v2 for `katapex`). All three also load a chat
template with one line replaced: the template baked into their GGUF raises on
a system message that arrives after the conversation has started, which
Claude Code sends mid-session, and only that one line differs from the
original.

Each entry's label is drawn from its config's own `label` field, reworded in
plain terms (no flag name, no internal mechanism): which R number, one line on
what changed, which box. `ACTION` is `profile -Name <model>-r<N>`, a two-word
string `llm_launch` passes whole into the remote command, exactly like a
one-word action. `MODEL_ID` and `MATCH` are both the profile's own alias
(`<model>-r<N>`), and `WINDOW` its config's `--ctx-size`.

MATCH here targets the alias, not `model_path`: several R-variants of the same
model can share the exact same weights file, differing only in sampling or
chat template, and `model_path` alone cannot tell them apart in that case. See
"they check which model is loaded" below for how `_llm_state` now reads both.

A bench profile also sets its own output-token budget, the eighth argument to
`llm_variant`, taken from its config's `max_tokens` rather than the global
`LLM_OUTPUT_TOKENS` (81,920 for our entries): one fixed constant cannot fit
sixty-three different profiles' own figures. It was the ninth until
2026-09-26, when the per-variant compaction trigger that sat eighth went.

These profile files are written and deployed by
`bench/scripts/deploy-profiles.sh --machine 99|97 --env <file>`, which also
sends the matching `llm-ctl` script to the station. It refuses to run against
a station a bench campaign is using: a `runner-<machine>` container still up,
or the station already serving an instance named `bench-*`, either one stops
it before anything is sent, since overwriting `llm-ctl` mid-campaign could
break the run in progress.

## LLM_SSH_KEY, and why it exists

The launchers call plain `ssh` unless `LLM_SSH_KEY` names a key, in which case
they pass it with `IdentitiesOnly=yes`. An ssh agent holding several keys offers
them all, and a server that caps authentication attempts refuses the connection
before the right key is ever tried, with a `Too many authentication failures`
that says nothing about the cause. Set `LLM_SSH_KEY` if you hit that; leave it
alone otherwise.

Put them somewhere on your `PATH` to call them by name, with `llm-launch.sh` next
to them. They pass their arguments through, so `kat -p "..."` works
as expected, and `LLM_CHOICE=1 qwen -p "..."` for the six launchers with a
menu.

## They check which model is loaded, not just whether one is

The server is single-slot: whichever model sits on port 8080 answers every
request, whatever model id the client asks for. So `/health` is not enough, and
neither is a process check.

Each launcher reads `model_path` from `/props` and matches it against the model
it wants. If the wrong one is loaded it **asks before swapping**, since someone
else may be using it.

That matching is less trivial than it looks. Every Qwen3.8-27B build carries
`qwen3.8` in its path, so a bare substring match treated the abliterated model
as if it were the aligned one and skipped the reload. Excluding `uncensored`
fixed that until 2026-09-19, when the TWIN-TURBO build, whose file says `Uncen`,
was taken for the aligned one in turn. Each `qwen` variant now matches on its
own model directory, and needs no EXCLUDE field:

```bash
llm_variant "Qwen3.8-27B sur la machine 32 Go, fenêtre 262k" "${LLM_HOST:-your-32gb-box}" qwen qwen3.8-27b-nvfp4 '' qwen3.8-27b 262144
```

Checked on 2026-09-19 against the live `/props` of the 32 GB box and the paths
of the other four Qwen builds: each matches its own variant and no other, and
the `qwenf` candidate matches none.

`llm-launch.sh` lowercases `model_path` once and compares with bash substring
tests rather than `grep -q`: under `pipefail`, `grep -q` can close the pipe
before `printf` has written, and a match then reads as a failure.

Since the bench profiles joined the menus, `_llm_state` also reads `/v1/models`
and appends its served `id` (the profile's `--alias`, every profile has
carried one since 2026-09-20) to `model_path` before matching. A model's own
weights answer to more than one bench profile when only sampling or the chat
template differs between them, in which case `model_path` is identical across
R1, R2 and R3 and cannot say which one is loaded; the alias can. Every MATCH
written before this change, against a `model_path` substring, still matches
exactly as before: it is a substring of the same combined string.

## They warm up on their own

Loading happens over SSH before `exec claude`, and the script polls until the
model answers, up to `LLM_LOAD_TIMEOUT` (240 s, 180 s for `spark` and `muse`).
There is never a manual warm-up to perform. A failure prints the server-side log path rather than dropping you into
a client that cannot reach anything.

Non-interactive SSH is required: the scripts call `ssh` without a TTY, so a key
with a passphrase prompt will hang them.

## Scope is per-process

The exported variables live in the launcher's process only. A plain `claude` in
another shell still talks to Anthropic. Nothing is written to any config file.

## What each one exports, and why

See [../docs/claude-code-integration.md](../docs/claude-code-integration.md).
The two settings that matter most:

- `ANTHROPIC_AUTH_TOKEN`, not `ANTHROPIC_API_KEY`. The latter triggers the
  client's custom-key approval prompt.
- `CLAUDE_CODE_MAX_CONTEXT_TOKENS` must match what the server actually serves,
  read from `/props`. Too high and a long session is truncated server-side with
  no warning.
