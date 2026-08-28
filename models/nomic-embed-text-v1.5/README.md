# nomic-embed-text-v1.5

768-dimension text embeddings. Embeddings only, no completion.

```powershell
.\llm-ctl.ps1 -Action embed
```

| | |
|---|---|
| Weights | `nomic-embed-text-v1.5.Q8_0.gguf` |
| Context | 131,072 |
| VRAM | ~1 GB |
| Parallel slots | 4 |
| Build | the frozen `turboquant` fork |

Use it for semantic similarity: deduplication, RAG, clustering.

## `/v1/embeddings` returns 501 when another model is loaded

The server is single-slot and shares port 8080 with the generation models, so
the embedder is mutually exclusive with them. Asking for embeddings while a
generation model is loaded returns **HTTP 501**, not an empty result.

Sequence your work: load `embed`, index, then reload the generation model. There
is no way around this without a second server on a second port.

## This is the last user of the frozen fork

The `turboquant` build exists only for a cache-quant feature of a model that was
removed. Validating this embedder on the upstream build would allow retiring
that fork entirely. Nobody has done the check yet, which is the only reason the
fork is still installed.
