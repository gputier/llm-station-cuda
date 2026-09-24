import io
import json
import math
import pathlib
import urllib.error

import pytest

from benchrun import embed_check as ec

FIX = pathlib.Path(__file__).parent / "fixtures"
# Real /v1/embeddings responses captured from the embed profile on the 99,
# 2026-09-23 (nomic-embed-text-v1.5.Q8_0, spec t13-embed.json): one pair
# (ep-001, ep-002) in each of the four conditions embed_check() needs.
CAPTURES = json.loads((FIX / "embed_response_sample.json").read_text())
# Real 500 body captured the same day: the embed profile's physical batch
# size (2048, set in t13-embed.json) refuses any single input above it, so
# both the 8192- and the 32768-token filler documents are rejected.
CTX_ERROR = json.loads((FIX / "embed_ctx_error_sample.json").read_text())
PAIRS_PATH = FIX / "embed_pairs_sample.jsonl"


def test_load_pairs_reads_jsonl():
    pairs = ec.load_pairs(PAIRS_PATH)
    assert [p["id"] for p in pairs] == ["ep-001", "ep-002"]


def test_recall_at_1_perfect_match():
    # Each query is closest to its own document: identity-like vectors.
    queries = [[1.0, 0.0], [0.0, 1.0]]
    documents = [[1.0, 0.0], [0.0, 1.0]]
    assert ec._recall_at_1(queries, documents) == 1.0


def test_recall_at_1_counts_misses():
    queries = [[1.0, 0.0], [1.0, 0.0]]
    documents = [[1.0, 0.0], [0.0, 1.0]]
    # Both queries are closest to document 0: one hit (index 0), one miss.
    assert ec._recall_at_1(queries, documents) == 0.5


def _by_input(entries):
    return {e["request_body"]["input"]: e["response_body"] for e in entries.values()}


def _fake_urlopen_factory(monkeypatch):
    responses = _by_input(CAPTURES)

    class _Resp:
        def __init__(self, body):
            self._body = json.dumps(body).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._body

    def fake_urlopen(req, timeout=None):
        text = json.loads(req.data.decode("utf-8"))["input"]
        if text in responses:
            return _Resp(responses[text])
        # Any text not in the captured set is one of the long context-probe
        # fillers: the real server refused both lengths tried here, so every
        # such call replays the real error instead of a made-up one.
        raise urllib.error.HTTPError(
            req.full_url, CTX_ERROR["status"], "server_error",
            hdrs=None, fp=io.BytesIO(json.dumps(CTX_ERROR["body"]).encode("utf-8")),
        )

    monkeypatch.setattr(ec.urllib.request, "urlopen", fake_urlopen)


def test_embed_check_reads_the_real_response_shape(monkeypatch):
    _fake_urlopen_factory(monkeypatch)
    result = ec.embed_check("http://station-under-test:8080", pairs_path=PAIRS_PATH)
    assert set(result) == {"prefix_gain", "matryoshka", "max_ctx_ok"}
    assert set(result["matryoshka"]) == {768, 512, 256}
    # Both real pairs are matched to their own passage in every condition on
    # this captured sample: recall 1.0 raw and prefixed, so prefix_gain is 0.
    assert result["prefix_gain"] == 0.0
    assert result["matryoshka"][768] == 1.0
    # The real embed profile refused both context lengths (batch size 2048):
    # max_ctx_ok is proven 0, not silently left out.
    assert result["max_ctx_ok"] == 0


def test_embed_check_requires_pairs_path():
    with pytest.raises(RuntimeError):
        ec.embed_check("http://station-under-test:8080")


def test_post_embedding_parses_the_real_flat_shape(monkeypatch):
    _fake_urlopen_factory(monkeypatch)
    text = CAPTURES["raw_q_0"]["request_body"]["input"]
    vector = ec._post_embedding("http://station-under-test:8080", text)
    expected = CAPTURES["raw_q_0"]["response_body"]["data"][0]["embedding"]
    assert vector == expected
    assert len(vector) == 768


def test_layer_norm_matches_hand_computed_values():
    # mean 5, population var 25 (four 0s and four 10s), eps 1e-5.
    vector = [0.0, 0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 10.0]
    normed = ec._layer_norm(vector)
    expected_low = (0.0 - 5.0) / math.sqrt(25.0 + 1e-5)
    expected_high = (10.0 - 5.0) / math.sqrt(25.0 + 1e-5)
    assert normed[:4] == pytest.approx([expected_low] * 4)
    assert normed[4:] == pytest.approx([expected_high] * 4)


def test_matryoshka_prepare_layer_norms_before_truncating():
    # Card's order (huggingface.co/nomic-ai/nomic-embed-text-v1.5, README,
    # read 2026-09-23): layer_norm over the FULL vector, THEN truncate.
    # Truncating first would compute the mean/variance over only the kept
    # dims, a different and wrong statistic. This vector makes the two
    # orders diverge sharply: the first 4 dims are constant (0), so a wrong
    # "truncate then layer_norm" reads zero variance and returns all zeros,
    # while the correct order carries the real full-vector statistics
    # through the cut.
    vector = [0.0, 0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 10.0]
    prepared = ec._prepare([vector], 4)[0]
    wrong_order = ec._truncate([ec._l2_normalize(v) for v in [vector[:4]]], 4)[0]
    assert prepared != pytest.approx(wrong_order)
    assert prepared == pytest.approx(ec._truncate([ec._layer_norm(vector)], 4)[0])
    assert all(x != 0.0 for x in prepared)


def test_post_embedding_raises_on_connection_failure(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ec.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="connection failed"):
        ec._post_embedding("http://station-under-test:8080", "hello")


def test_post_embedding_returns_none_on_http_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 500, "server_error", hdrs=None,
            fp=io.BytesIO(json.dumps(CTX_ERROR["body"]).encode("utf-8")),
        )

    monkeypatch.setattr(ec.urllib.request, "urlopen", fake_urlopen)
    assert ec._post_embedding("http://station-under-test:8080", "hello") is None
