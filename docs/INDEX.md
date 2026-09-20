# Documentation index

Twelve documents, each answering a different question. Read them in this order
the first time; jump straight to one afterwards.

| Document                                                             | Answers                                                                                                                                                                                                                                                                                     |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [security-gate.md](security-gate.md)                                 | What runs before a push leaves this machine, how to wire it in a fresh clone, and what to do when it blocks. Read it first: it is the only document whose absence can cost something irreversible.                                                                                          |
| [prerequisites.md](prerequisites.md)                                 | What hardware, drivers and toolkits this station assumes, and the failure modes that look like something else.                                                                                                                                                                              |
| [building-llama-cpp.md](building-llama-cpp.md)                       | How the six coexisting llama.cpp builds are installed, why five are official releases and the sixth a fork, and why none is interchangeable.                                                                                                                                                |
| [api-usage.md](api-usage.md)                                         | How to call the server: endpoints, payloads, and the fields that carry the measurements.                                                                                                                                                                                                    |
| [claude-code-integration.md](claude-code-integration.md)             | How an agentic client is pointed at this server, and the environment variables that matter.                                                                                                                                                                                                 |
| [fetching-models.md](fetching-models.md)                             | Which client to use to pull weights, why the in-house fetcher was deleted, and how to measure a transfer honestly.                                                                                                                                                                          |
| [tuning-log.md](tuning-log.md)                                       | Every tuning campaign run on this box, including the ones that found nothing.                                                                                                                                                                                                               |
| [etude-vitesse-qwen-2026-09-06.md](etude-vitesse-qwen-2026-09-06.md) | Study of 2026-09-06, in French: every remaining speed lever for the reasoning profile, ranked, with what was already closed by measurement and the campaign plan.                                                                                                                           |
| [quel-modele-pour-quel-usage.md](quel-modele-pour-quel-usage.md)     | Which model to reach for, per use, in French. Separates what was measured from what was inferred: the agentic and coding rows are informed opinion, not results.                                                                                                                            |
| [campagne-mesures-2026-09-10.md](campagne-mesures-2026-09-10.md)     | Campaign of 2026-09-10, in French: nine models through one bench in one day, what it says about the parc, and the four method errors that each produced a false and credible result. **Supersedes every quality figure published before it.**                                               |
| [jeu-inedit-2026-09-11.md](jeu-inedit-2026-09-11.md)                 | Campaign of 2026-09-11, in French, on a 16 GB box: 235 never-published questions against four models, why two of them lose fifteen points the moment the questions are new, and the four protocol faults found on the way. Read it for those faults even if the models do not interest you. |
| [schema-jeu-inedit.md](schema-jeu-inedit.md)                         | The contract that question set obeys, in French: field layout, the five correction modes and no others, the reading rule, and the four verification stages. Enough to rebuild an equivalent set; the questions themselves are deliberately absent from this repository.                     |

## Where the truth lives

`llm-ctl.ps1` is the source of truth for a launch profile, not any document
here. A comment in that script sits next to the flag it justifies, so it is the
one place that cannot silently drift from what actually runs. When a document
and the script disagree, the script wins and the document is the bug.

There are two of them, one per machine, and they are not interchangeable.
`llm-ctl.ps1` drives the 32 GB box this repository was built around.
[../llm-ctl-16gb.ps1](../llm-ctl-16gb.ps1) drives a second box, an RTX 4080 SUPER
with 32 GB of host RAM. Since 2026-09-14 it serves `tiel` and `qwen36`, both
35B-A3B in UD-IQ3_XXS at the full 262,144 window, on the BeeLlama fork for its
KVarN cache, with one card recipe shared by both profiles; it refuses `mlock`
because of that host RAM. Its profiles were tuned separately because the same
flags land differently on half the memory. Copying a profile from one box to the
other is how the 16 GB box once served half the window its weights offer, and
how it locked 10 GB of host RAM the 32 GB box could spare.

The per-model notes live next to the weights they describe, under
[../models/](../models/), and carry provenance, checksums and the traps specific
to each model. Anything measured rather than assumed belongs in
[tuning-log.md](tuning-log.md), with its date and its protocol.

## What is deliberately not here

Product, infrastructure and operations documentation lives in the wiki. This
folder carries only what ships with the code: conventions, decisions, and the
procedures a contributor needs to reproduce a result.
