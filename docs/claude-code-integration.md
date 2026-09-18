# Running Claude Code against a local server

Claude Code is an agentic CLI that normally talks to the Anthropic API. It can
be pointed at any endpoint that speaks the Anthropic Messages format. Recent
`llama.cpp` builds expose `/v1/messages` **natively**, including SSE streaming,
`count_tokens` and `tool_use` blocks, so no translation proxy is involved. Only
the base URL is redirected.

That is the whole trick. The rest of this page is what it takes to make it work
in practice, and what it costs.

## What a launcher does

Every script in [../clients/](../clients/) sources the same body,
`llm-launch.sh`, which follows four steps:

1. `GET /props` and read `model_path` to see **which** model is loaded.
   `/health` is not enough: the server is single-slot, so whatever model sits on
   the port answers every request regardless of the model id the client asks
   for.
2. If the port refuses the connection, nothing runs and the load goes ahead. If
   another model answers, or the probe times out, or the answer names no model,
   ask before loading. Someone else may be using it, and a server busy on a long
   prompt can miss a three-second probe.
3. Load the right model over SSH, then poll until it actually answers.
4. `export` the environment and `exec claude`.

The exported variables live in that process only. A plain `claude` in another
shell still talks to Anthropic.

## The environment

```bash
export ANTHROPIC_AUTH_TOKEN="local"
export ANTHROPIC_BASE_URL="http://your-server:8080"

export ANTHROPIC_MODEL="qwen3.8-27b"
export ANTHROPIC_DEFAULT_OPUS_MODEL="qwen3.8-27b"
export ANTHROPIC_DEFAULT_SONNET_MODEL="qwen3.8-27b"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="qwen3.8-27b"
export ANTHROPIC_SMALL_FAST_MODEL="qwen3.8-27b"

export CLAUDE_CODE_MAX_CONTEXT_TOKENS=376832   # a 393,216 window minus the output budget
export CLAUDE_CODE_MAX_OUTPUT_TOKENS=16384
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1  # read for presence: "0" turns it on too
```

**`ANTHROPIC_AUTH_TOKEN` and not `ANTHROPIC_API_KEY`.** The latter triggers
Claude Code's custom-key approval prompt. A `llama-server` started without
`--api-key` accepts any bearer token, so the value is arbitrary. When the server
does run with `--api-key`, put the real key here: it is sent as
`Authorization: Bearer`, which llama.cpp accepts alongside `x-api-key`.

**Every model id resolves to the single loaded model**, but naming them all
keeps the UI honest and stops the client from reaching for a real Anthropic
model it cannot serve.

**`CLAUDE_CODE_MAX_CONTEXT_TOKENS` is an INPUT budget, not the window.** This is
the correction of 2026-09-08 and it cost a working session. `n_ctx` counts input
and output in the same pool. Announce the whole of it and the client fills the
last token, after which the server has no room left to write anything and
returns an **empty response**. It hit at 372,738 tokens of 393,216, 95% full:
`/compact` answered `summarization produced empty response` twice in a row,
because compacting is itself a request, and one that asks for 16,384 tokens of
room to write its summary.

The value is `default_generation_settings.n_ctx` from `/props`, **minus**
`CLAUDE_CODE_MAX_OUTPUT_TOKENS`. For a 393,216 window with 16,384 of output,
that is 376,832. Change one, change the other. Never read the number you passed
to `--ctx-size`: we briefly set 524288 on a server capped at 262144 and caught
it the same day.

**An `env` block in `~/.claude/settings.json` beats an exported variable.** The
client applies that block over its own process environment. A
`CLAUDE_CODE_MAX_OUTPUT_TOKENS` of 64000 there replaced the 16,384 exported by
the launchers in every local session, until 2026-09-15: `max_tokens` 64000 went
out with each request, and the compaction thresholds moved with it. Passing the
two budgets with `--settings '{"env":{...}}'` wins over the user file, measured
on Claude Code 2.1.271. That is what `clients/llm-launch.sh` does.

**Automatic compaction fails when the model answers it with a tool call.** The
summary request keeps the full tool list and forbids tools only in its text
("Respond with TEXT ONLY"). Replayed on 2026-09-15 from a real session at
222,000 tokens, Qwen3.6-35B-A3B answered with a `Bash` call of 102 tokens, and
the client reported `summarization produced empty response`. The same model
writes the summary on a short context. After three consecutive failures the
client stops trying for the rest of the session, then refuses the next turn
with `Context limit reached`. A manual `/compact` may still succeed once the
context is lower.

The launchers answer this per variant: an optional eighth field of
`llm_variant` sets where compaction starts, and `llm-launch.sh` turns it into
`CLAUDE_CODE_AUTO_COMPACT_WINDOW`, that size plus the output budget plus the
client's 13,000-token summary buffer. Only the trigger moves; the client still
refuses a turn at the real window. The Qwen3.6-35B-A3B variant of `qwen` starts
at 180,000, checked against a stub server on 2026-09-15 (effective window
193,000, `level=compact` past 180,000, no refusal below the real limit). That
the model writes its summary at 180,000 is not proven; it did at 186,351 once.

**A second slot forces you to divide it, and that is why there is no second
slot.** Every profile here runs `--parallel 1`, so `/props` and this variable
agree. The rule if that ever changes: `n_ctx` divided by the slot count, minus
room for `CLAUDE_CODE_MAX_OUTPUT_TOKENS`. `--parallel 2 --kv-unified` holds ONE
shared pool, not one window per slot, and `/props` announces the whole pool to
every caller.

`tiel` ran two slots from 2026-09-06 to 2026-09-08, on the reasoning that two
workstations call it. Two days were enough to show what that costs. Each caller
had to be told it could fill only half the pool, and the halved ceiling is what
a client reads to decide when to compact. Agents spawned inside one session
therefore threw their context away long before the model would have run out.
The failure mode when the announcement was left at the full pool is worth
keeping too: a 196,000-token prefill crawling at 1,546 tok/s against the 8,000
the bench gives, the other slot generating at **1.40 tok/s**, then `failed to
find free space in the KV cache` with the batch halved down to 16 and still
failing.

Both profiles went back to a single slot the same day. Concurrent callers queue,
which is the honest trade: waiting costs time, compacting costs work already
done. Note that two callers are not needed to trigger any of this. Agents
spawned inside one session hit it identically, and so do the client's own
background requests, which the launchers point at the same server.

## The chat template trap

This one blocks the integration entirely, and its symptom points the wrong way.

Most Qwen chat templates raise `System message must be at the beginning` as soon
as a system message arrives **after** a user message. Agentic clients inject
system messages mid-session. So `/v1/chat/completions` works perfectly in every
manual test, while the agentic client fails with `API Error: 500` on the very
first turn.

The fix is a derived template with a single line changed: render a late system
message as an ordinary ChatML system turn instead of raising. ChatML supports
this natively; it was the template refusing, not the format. Load it with
`--chat-template-file`.

See [../models/qwen3.8-27b/README.md](../models/qwen3.8-27b/README.md) for the
exact change.

**It bites every profile whose model comes from that family, and it bit two more
on 2026-09-18.** Both Bonsai generations were found raising it, each now carrying
its own `chat-template-system-anywhere.jinja` next to its weights. The first had
been doing it since its install of 2026-09-10 without anyone seeing it, because
the bench campaign of that day drove `/v1/chat/completions` directly and never a
client. The templates are close enough to look interchangeable and are not: the
Qwen one is Unsloth's rework and carries a developer role, tool-call argument
validation and a reasoning-effort mapping that the Bonsai models never declared.
Patch each model's own template, one line, and prove it with a real tool call
through the launcher. A profile that has only ever been driven by curl is not a
profile that works.

## Dropping MCP servers is not cosmetic

The launchers pass `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`.

Measured on this setup: the full client prompt is **109,738 tokens**, of which
108 KB is tool schemas alone, for 70 tools. Without MCP servers it falls to
roughly 44,000. On a 384k window that overhead is an eighth of the context gone
before the first user message, and it makes the first turn much slower.

Skills, slash commands, memory and project instructions are untouched by this
flag. Only MCP servers are dropped.

## What this costs

Pointing the client at a gateway credential replaces the subscription for the
whole session and disables the features that depend on it, such as remote
control and voice dictation. It is a per-session trade, not a permanent one:
the launchers export into their own process, so another terminal is unaffected.

Local models also have no web access and will confidently invent recent facts.
Use them for text-in / text-out work: summarising, extraction, exploration,
refactoring against code you provide.

## After idle, the first call is slow

Windows evicts weights from VRAM after inactivity. Measured: 29.5 GB down to
5.3 GB, with the `llama-server` process holding 26.2 GB in host memory instead.
The first call pulls them back and costs about 5 seconds. Steady-state
throughput is unaffected, but `nvidia-smi` read during that window gives a
misleading picture of what is actually resident.
