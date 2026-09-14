# Launchers

One script per model family. Each starts the right model on the right server if
needed, waits for it to actually answer, then hands over to Claude Code pointed
at it.

```bash
export LLM_HOST=your-32gb-box            # the 5090 box, every launcher
export LLM_HOST_16GB=your-16gb-box       # the second box, tiel and qwen only
export LLM_SSH_USER=your-ssh-user
export LLM_SSH_KEY=~/.ssh/id_ed25519     # optional, see below

./tiel        # the default: coding and reasoning, asks which box
./kat         # when arithmetic reasoning matters more than speed
./ornith      # the small one, a third of the VRAM, better than its size
./nex         # best quality measured here, no speculation, vision
./bonsai      # 27B in 7 GiB, the best quality per byte
./spark       # 4B, last on every measure
./muse        # agentic, vision, faithful OCR
./qwen        # Qwen3.8 aligned or uncensored on the 32 GB box, Qwen3.6 on the 16 GB one
```

## tiel and qwen ask which model, and on which box

The same families now live on two boxes, so these two launchers open with a
numbered menu instead of carrying one script per model per machine. The menu
marks the variant already loaded, if any. `LLM_CHOICE=2 ./tiel -p "..."` skips
it, and a run without a terminal must set it: a menu read from a pipe would take
the caller's input as a choice. Their shared body is
[llm-launch.sh](llm-launch.sh), sourced and never run on its own.

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
cases out of seven.

Which one to reach for, with the figures behind each line, is in
[../docs/quel-modele-pour-quel-usage.md](../docs/quel-modele-pour-quel-usage.md).

`embed` has no launcher on purpose: it serves embeddings, not a chat endpoint.

## LLM_SSH_KEY, and why it exists

The newer launchers name their key explicitly and pass `IdentitiesOnly=yes`. An
ssh agent holding several keys offers them all, and a server that caps
authentication attempts refuses the connection before the right key is ever
tried, with a `Too many authentication failures` that says nothing about the
cause. Set `LLM_SSH_KEY` if you hit that; leave it alone otherwise.

Put them somewhere on your `PATH` to call them by name, with `llm-launch.sh` next
to `tiel` and `qwen`. They pass their arguments through, so `kat -p "..."` works
as expected, and `LLM_CHOICE=1 qwen -p "..."` for the two launchers with a menu.

## They check which model is loaded, not just whether one is

The server is single-slot: whichever model sits on port 8080 answers every
request, whatever model id the client asks for. So `/health` is not enough, and
neither is a process check.

Each launcher reads `model_path` from `/props` and matches it against the model
it wants. If the wrong one is loaded it **asks before swapping**, since someone
else may be using it.

That matching is less trivial than it looks. Both the aligned and the abliterated
builds carry `qwen3.8` in their path, so a bare substring match treated the
abliterated model as if it were the aligned one and skipped the reload. The
aligned build is the one **without** `uncensored`, which `qwen` declares as the
EXCLUDE field of its first variant:

```bash
llm_variant "Qwen3.8-27B sur la machine 32 Go, fenêtre 393k" "${LLM_HOST:-your-32gb-box}" qwen qwen3.8 uncensored qwen3.8-27b 393216
```

`llm-launch.sh` lowercases `model_path` once and compares with bash substring
tests rather than `grep -q`: under `pipefail`, `grep -q` can close the pipe
before `printf` has written, and a match then reads as a failure.

## They warm up on their own

Loading happens over SSH before `exec claude`, and the script polls until the
model answers, up to `LOAD_TIMEOUT` (180 s, 240 s for `tiel` and `qwen`). There is never a manual warm-up to
perform. A failure prints the server-side log path rather than dropping you into
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
