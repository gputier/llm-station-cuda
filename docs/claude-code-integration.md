# Running Claude Code against a local server

Claude Code is an agentic CLI that normally talks to the Anthropic API. It can
be pointed at any endpoint that speaks the Anthropic Messages format. Recent
`llama.cpp` builds expose `/v1/messages` **natively**, including SSE streaming,
`count_tokens` and `tool_use` blocks, so no translation proxy is involved. Only
the base URL is redirected.

That is the whole trick. The rest of this page is what it takes to make it work
in practice, and what it costs.

## What a launcher does

Each script in [../clients/](../clients/) follows the same five steps:

1. `GET /health` to see if a server is up.
2. `GET /props` and read `model_path` to see **which** model is loaded. Health
   alone is not enough: the server is single-slot, so whatever model sits on the
   port answers every request regardless of the model id the client asks for.
3. If the wrong model is loaded, ask before swapping it. Someone else may be
   using it.
4. Load the right model over SSH, then poll until it actually answers.
5. `export` the environment and `exec claude`.

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

export CLAUDE_CODE_MAX_CONTEXT_TOKENS=393216
export CLAUDE_CODE_MAX_OUTPUT_TOKENS=16384
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=0
```

**`ANTHROPIC_AUTH_TOKEN` and not `ANTHROPIC_API_KEY`.** The latter triggers
Claude Code's custom-key approval prompt. A `llama-server` started without
`--api-key` accepts any bearer token, so the value is arbitrary. When the server
does run with `--api-key`, put the real key here: it is sent as
`Authorization: Bearer`, which llama.cpp accepts alongside `x-api-key`.

**Every model id resolves to the single loaded model**, but naming them all
keeps the UI honest and stops the client from reaching for a real Anthropic
model it cannot serve.

**`CLAUDE_CODE_MAX_CONTEXT_TOKENS` must match one caller's SHARE of what the
server serves.** This is not cosmetic. Set too low, the client compacts early
while the server still has window free. Set too high, a session crossing the
real ceiling is truncated server-side **with no warning**. The starting value is
`default_generation_settings.n_ctx` in `/props`, never the number you passed to
`--ctx-size`. We briefly set 524288 on a server capped at 262144 and caught it
the same day.

**Then divide by the number of slots.** Reading `/props` and stopping there is
what bit on 2026-09-08. A profile running `--parallel 2 --kv-unified` holds ONE
shared pool of 393,216 tokens, and `/props` announces that whole pool to every
caller. Two agents of a single session were each told they could fill 393,216.
One of them reached a 196,000-token prompt, and the log reads: prefill down to
1,546 tok/s against the 8,000 the bench gives, the other slot generating at
**1.40 tok/s**, then `failed to find free space in the KV cache` with the batch
size halved down to 16 and still failing.

Two callers are not needed to trigger it. Agents spawned inside one session hit
it identically, and so do the client's own background requests, which the
launchers point at the same server.

The rule, then: `n_ctx` from `/props`, divided by `--parallel`, minus room for
`CLAUDE_CODE_MAX_OUTPUT_TOKENS`. For `tiel` and `kat` that is 393,216 over two
slots, so 180,000 announced to each. Profiles running a single slot, like
`qwen`, keep the full figure.

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
