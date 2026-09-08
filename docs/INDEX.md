# Documentation index

Seven documents, each answering a different question. Read them in this order the
first time; jump straight to one afterwards.

| Document | Answers |
|---|---|
| [prerequisites.md](prerequisites.md) | What hardware, drivers and toolkits this station assumes, and the failure modes that look like something else. |
| [building-llama-cpp.md](building-llama-cpp.md) | How the four coexisting llama.cpp builds are compiled, and why they are not interchangeable. |
| [api-usage.md](api-usage.md) | How to call the server: endpoints, payloads, and the fields that carry the measurements. |
| [claude-code-integration.md](claude-code-integration.md) | How an agentic client is pointed at this server, and the environment variables that matter. |
| [fetching-models.md](fetching-models.md) | Which client to use to pull weights, why the in-house fetcher was deleted, and how to measure a transfer honestly. |
| [tuning-log.md](tuning-log.md) | Every tuning campaign run on this box, including the ones that found nothing. |
| [etude-vitesse-qwen-2026-09-06.md](etude-vitesse-qwen-2026-09-06.md) | Study of 2026-09-06, in French: every remaining speed lever for the reasoning profile, ranked, with what was already closed by measurement and the campaign plan. |

## Where the truth lives

`llm-ctl.ps1` is the source of truth for a launch profile, not any document
here. A comment in that script sits next to the flag it justifies, so it is the
one place that cannot silently drift from what actually runs. When a document
and the script disagree, the script wins and the document is the bug.

The per-model notes live next to the weights they describe, under
[../models/](../models/), and carry provenance, checksums and the traps specific
to each model. Anything measured rather than assumed belongs in
[tuning-log.md](tuning-log.md), with its date and its protocol.

## What is deliberately not here

Product, infrastructure and operations documentation lives in the wiki. This
folder carries only what ships with the code: conventions, decisions, and the
procedures a contributor needs to reproduce a result.
