# Launchers

One script per model. Each starts the right model on the server if needed, waits
for it to actually answer, then hands over to Claude Code pointed at it.

```bash
export LLM_HOST=your-server-hostname-or-ip
export LLM_SSH_USER=your-ssh-user
export LLM_SSH_KEY=~/.ssh/id_ed25519   # optional, see below

./tiel        # the default: coding and reasoning, fastest of all
./kat         # when arithmetic reasoning matters more than speed
./ornith      # the small one, a third of the VRAM, better than its size
./nex         # best quality measured here, no speculation, vision
./bonsai      # 27B in 7 GiB, the best quality per byte
./spark       # 4B, last on every measure
./muse        # agentic, vision, faithful OCR
./qwen        # reasoning and coding, superseded by tiel
./qwenu       # only when the aligned model refuses a legitimate task
```

Which one to reach for, with the figures behind each line, is in
[../docs/quel-modele-pour-quel-usage.md](../docs/quel-modele-pour-quel-usage.md).

`embed` has no launcher on purpose: it serves embeddings, not a chat endpoint.

## LLM_SSH_KEY, and why it exists

The newer launchers name their key explicitly and pass `IdentitiesOnly=yes`. An
ssh agent holding several keys offers them all, and a server that caps
authentication attempts refuses the connection before the right key is ever
tried, with a `Too many authentication failures` that says nothing about the
cause. Set `LLM_SSH_KEY` if you hit that; leave it alone otherwise.

Put them somewhere on your `PATH` to call them by name. They pass their
arguments through, so `qwen --help` or `qwen -p "..."` works as expected.

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
aligned build is the one **without** `uncensored`:

```bash
is_qwen() {
  local m; m=$(loaded_model)
  printf '%s' "$m" | grep -qi 'qwen3.8' && ! printf '%s' "$m" | grep -qi 'uncensored'
}
```

## They warm up on their own

Loading happens over SSH before `exec claude`, and the script polls until the
model answers, up to `LOAD_TIMEOUT` (180 s). There is never a manual warm-up to
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
