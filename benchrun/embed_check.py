"""Conformance check for the embedder (nomic-embed-text-v1.5), never part of
the comparative bench, only of a compliance pass: prefix gain, Matryoshka
truncation, and proven context.

The gateway does not relay embeddings (RELAYED_POSTS in benchrun.gateway
lists only the three chat/completion paths), so this talks straight to the
base_url the embedder itself serves, exactly as docs/api-usage.md documents
for /v1/embeddings.

The 20 question/document pairs are private (Bench-LLM/sets/embed-pairs.jsonl,
not redistributed): each question is answerable from exactly one document's
passage, real text read from this repository's own docs. Recall@1 is whether
the paired passage ranks first by cosine similarity among the 20 candidate
passages, which is what "conformance" means here: retrieval works, not that
the model scores well on a public benchmark (the embedder was pulled out of
the comparative bench for that reason, per the design spec).
"""
from __future__ import annotations

import json
import math
import pathlib
import urllib.error
import urllib.request

from benchrun.suites.corpus import CHARS_PER_TOKEN, DEFAULT_CORPUS, filler_text

EMBEDDINGS_PATH = "/v1/embeddings"
# nomic-embed-text-v1.5's own task prefixes (model card): a query asks a
# question, a document is retrieved. Recall is compared with both prefixes
# applied against both left bare, never one prefixed and not the other.
QUERY_PREFIX = "search_query: "
DOCUMENT_PREFIX = "search_document: "
MATRYOSHKA_DIMS = (768, 512, 256)
# torch.nn.functional.layer_norm's own default when no eps is passed, which
# is what the card's sample calls with (see _layer_norm docstring).
LAYER_NORM_EPS = 1e-5


def _post_embedding(base_url: str, text: str) -> list[float] | None:
    body = json.dumps({"model": "embed", "input": text}).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + EMBEDDINGS_PATH, data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError:
        # An answer from the server, refusing the request: a real "no vector
        # for this input" (used by _max_ctx_ok to find where it stops).
        return None
    except urllib.error.URLError as exc:
        # No answer at all: the embedder or the network is down. Silently
        # reading this as "no vector" would make max_ctx_ok read 0 whenever
        # the station is simply unreachable, indistinguishable from a real
        # batch-size refusal. Raise instead.
        raise RuntimeError(f"embedder connection failed for {base_url}: {exc}") from exc
    try:
        vector = payload["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError):
        return None
    return [float(x) for x in vector] if vector else None


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


def _layer_norm(vector: list[float], eps: float = LAYER_NORM_EPS) -> list[float]:
    """Port of F.layer_norm(embeddings, normalized_shape=(embeddings.shape[1],))
    with no weight or bias (no affine), read live from the model card's own
    Matryoshka sample code (huggingface.co/nomic-ai/nomic-embed-text-v1.5,
    README, "Matryoshka Representation Learning" section, read 2026-09-23):
    embeddings = F.layer_norm(embeddings, normalized_shape=(embeddings.shape[1],))
    embeddings = embeddings[:, :matryoshka_dim]
    embeddings = F.normalize(embeddings, p=2, dim=1)
    PyTorch's layer_norm computes the mean and the BIASED (population)
    variance over the last dimension, no affine transform when weight/bias
    are omitted, and divides by sqrt(var + eps), eps defaulting to 1e-5."""
    n = len(vector)
    mean = sum(vector) / n
    var = sum((x - mean) ** 2 for x in vector) / n
    denom = math.sqrt(var + eps)
    return [(x - mean) / denom for x in vector]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _recall_at_1(queries: list[list[float]], documents: list[list[float]]) -> float:
    """Fraction of queries whose own paired document is the closest of the
    whole pool by cosine similarity (both sides L2-normalized first)."""
    docs = [_l2_normalize(d) for d in documents]
    hits = 0
    for i, q in enumerate(queries):
        qn = _l2_normalize(q)
        sims = [_cosine(qn, d) for d in docs]
        if sims.index(max(sims)) == i:
            hits += 1
    return hits / len(queries) if queries else 0.0


def _embed_all(base_url: str, texts: list[str]) -> list[list[float]] | None:
    vectors = []
    for text in texts:
        vector = _post_embedding(base_url, text)
        if vector is None:
            return None
        vectors.append(vector)
    return vectors


def _truncate(vectors: list[list[float]], dims: int) -> list[list[float]]:
    return [v[:dims] for v in vectors]


def _prepare(vectors: list[list[float]], dims: int) -> list[list[float]]:
    """Card's exact sequence for a target dimension: layer norm over the
    FULL vector first, truncate second, L2-normalize last (_recall_at_1 does
    the final normalize). Truncating before layer_norm would compute the
    mean and variance over the wrong (already cut) set of dimensions."""
    return _truncate([_layer_norm(v) for v in vectors], dims)


def load_pairs(pairs_path: str | pathlib.Path) -> list[dict]:
    pairs = []
    for line in pathlib.Path(pairs_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            pairs.append(json.loads(line))
    return pairs


def _max_ctx_ok(base_url: str, corpus_dir: pathlib.Path) -> int:
    """The larger of 8192 and 32768 tokens whose filler document returns a
    vector with no error; 0 if even 8192 fails. Filler text is real source
    code, built the same way the speed suite builds its own, never one block
    repeated."""
    best = 0
    for n in (8192, 32768):
        filler = filler_text(corpus_dir, n * CHARS_PER_TOKEN)
        if _post_embedding(base_url, DOCUMENT_PREFIX + filler) is not None:
            best = n
    return best


def embed_check(base_url: str, pairs_path: str | pathlib.Path | None = None,
                 corpus_dir: str | pathlib.Path | None = None) -> dict:
    if pairs_path is None:
        raise RuntimeError("embed_check needs pairs_path (the private embed-pairs.jsonl)")
    pairs = load_pairs(pairs_path)
    if not pairs:
        raise RuntimeError(f"{pairs_path} carries no pair")
    questions = [p["question"] for p in pairs]
    passages = [p["passage"] for p in pairs]

    raw_q = _embed_all(base_url, questions)
    raw_d = _embed_all(base_url, passages)
    prefixed_q = _embed_all(base_url, [QUERY_PREFIX + q for q in questions])
    prefixed_d = _embed_all(base_url, [DOCUMENT_PREFIX + d for d in passages])
    if None in (raw_q, raw_d, prefixed_q, prefixed_d):
        raise RuntimeError("embedder returned no vector for a required pass, cannot grade conformance")

    recall_raw = _recall_at_1(raw_q, raw_d)
    recall_prefixed = _recall_at_1(prefixed_q, prefixed_d)

    matryoshka = {}
    for dims in MATRYOSHKA_DIMS:
        matryoshka[dims] = _recall_at_1(_prepare(prefixed_q, dims), _prepare(prefixed_d, dims))

    corpus_dir = pathlib.Path(corpus_dir) if corpus_dir else DEFAULT_CORPUS
    return {
        "prefix_gain": recall_prefixed - recall_raw,
        "matryoshka": matryoshka,
        "max_ctx_ok": _max_ctx_ok(base_url, corpus_dir),
    }
