import dataclasses
import json
import pathlib

import pytest

from benchrun.config import load_config
from benchrun.runner import SuiteContext, SuiteSkipped
from benchrun.suites import speed
from benchrun.suites.speed import DEFAULT_CORPUS, SpeedSuite, _filler_text, lengths_to_run, median_row

# Real /v1/chat/completions response and matching gateway journal line, both
# captured from a real speed pass on the 99 (spark, 2026-09-23, 512-token
# prompt) through gw-t13.
FIX = pathlib.Path(__file__).parent / "fixtures"
RESPONSE = json.loads((FIX / "speed_response_sample.json").read_text())
JOURNAL_LINE = (FIX / "speed_journal_sample.jsonl").read_text().strip()
CFG = load_config(FIX / "config_ok.yaml")


def test_median_row():
    rows = [{"decode_tps": 100}, {"decode_tps": 120}, {"decode_tps": 90}]
    assert median_row(rows, "decode_tps") == 100


def test_median_row_even_count_takes_the_lower_middle():
    # Ported from vitesse.ps1's Mediane: floor(count/2), never an average.
    rows = [{"x": 10}, {"x": 20}, {"x": 30}, {"x": 40}]
    assert median_row(rows, "x") == 30


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def test_run_reads_decode_and_prefill_from_the_response_and_ttft_from_the_journal(tmp_path, monkeypatch):
    journal_path = tmp_path / "journal.jsonl"
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        # The gateway appends one journal line per relayed request, before
        # the client sees the response: replaying that order here is what
        # proves _read_new_ttft matches the right line to the right call.
        with open(journal_path, "a", encoding="utf-8") as fh:
            fh.write(JOURNAL_LINE + "\n")
        return _FakeResponse(json.dumps(RESPONSE).encode("utf-8"))

    monkeypatch.setattr(speed.urllib.request, "urlopen", fake_urlopen)
    cfg = dataclasses.replace(CFG, model="spark")
    ctx = SuiteContext("http://gw:8081", cfg, 1, tmp_path, tmp_path)
    suite = SpeedSuite(prompt_tokens=(512,), gen_tokens=256, reps=3, journal_path=journal_path)
    rows = suite.run(ctx)
    assert len(calls) == 3
    assert rows == [{
        "item_id": "pp512",
        "passed": 1,
        "detail": {
            "decode_tps": RESPONSE["timings"]["predicted_per_second"],
            "prefill_tps": RESPONSE["timings"]["prompt_per_second"],
            "draft_n": 0,
            "draft_n_accepted": 0,
            "ttft_s": json.loads(JOURNAL_LINE)["ttft_s"],
        },
    }]


def test_run_requires_a_journal_path(tmp_path):
    cfg = dataclasses.replace(CFG, model="spark")
    ctx = SuiteContext("http://gw:8081", cfg, 1, tmp_path, tmp_path)
    suite = SpeedSuite(prompt_tokens=(512,), gen_tokens=256, reps=1)
    with pytest.raises(RuntimeError):
        suite.run(ctx)


# spark's real window (task9-check.json, measured live 2026-09-23): 8192.
SPARK_CFG = dataclasses.replace(
    CFG, model="spark",
    args=["-m", r"D:\models\spark-x2.5-4b\Spark-X2.5-4B-Q8_0.gguf", "--ctx-size", "8192",
          "--host", "0.0.0.0", "--port", "8080"],
)


def test_lengths_to_run_keeps_what_fits_with_room_for_gen_tokens():
    run, skipped = lengths_to_run((512, 8192, 32768), 256, SPARK_CFG)
    # 512 + 256 = 768 <= 8192: kept. 8192 + 256 > 8192: skipped, the prompt
    # alone already saturates the window before any output room (this is
    # what actually happened on the real spark pass, see task-13-report.md).
    assert run == [512]
    assert skipped == [8192, 32768]


def test_run_skips_lengths_above_the_window_and_records_them(tmp_path, monkeypatch):
    journal_path = tmp_path / "journal.jsonl"
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        with open(journal_path, "a", encoding="utf-8") as fh:
            fh.write(JOURNAL_LINE + "\n")
        return _FakeResponse(json.dumps(RESPONSE).encode("utf-8"))

    monkeypatch.setattr(speed.urllib.request, "urlopen", fake_urlopen)
    ctx = SuiteContext("http://gw:8081", SPARK_CFG, 1, tmp_path, tmp_path)
    suite = SpeedSuite(prompt_tokens=(512, 8192, 32768), gen_tokens=256, reps=1, journal_path=journal_path)
    rows = suite.run(ctx)
    # Only the length that fit was ever requested: no attempt was made to
    # build a 32768-token filler or send it, which would have been the
    # silent failure this fix closes (either a RuntimeError from the corpus
    # builder or an oversized request sent to a station that would refuse
    # or crash it).
    assert len(calls) == 1
    assert [r["item_id"] for r in rows] == ["pp512"]
    skipped = json.loads((tmp_path / "speed_skipped.json").read_text())
    assert skipped == {"skipped_lengths": [8192, 32768]}


def test_run_is_skipped_when_no_length_fits(tmp_path):
    ctx = SuiteContext("http://gw:8081", SPARK_CFG, 1, tmp_path, tmp_path)
    suite = SpeedSuite(prompt_tokens=(8192, 32768), gen_tokens=256, reps=1, journal_path=tmp_path / "j.jsonl")
    with pytest.raises(SuiteSkipped, match="exceeds the served window"):
        suite.run(ctx)


def test_suite_pins_max_tokens_over_the_config_sampling():
    # The gateway overwrites every sampling key of the context: without the
    # override a config's max_tokens 16384 would replace gen_tokens 256.
    assert SpeedSuite(gen_tokens=256).sampling_override == {"max_tokens": 256}


def test_corpus_widened_to_the_repo_reaches_32768_tokens_worth_of_filler():
    # Real filesystem scan, real repo: bench/benchrun alone (229 KB of .py)
    # is short of 32768 * 3 = 98,304 characters once other suites' fixtures
    # are excluded; the repo root (.py and .ps1) carries enough.
    target = 32768 * speed.CHARS_PER_TOKEN
    text = _filler_text(DEFAULT_CORPUS, target)
    assert len(text) == target


def test_one_pass_raises_when_the_response_carries_no_timings(tmp_path, monkeypatch):
    journal_path = tmp_path / "journal.jsonl"

    def fake_urlopen(req, timeout=None):
        with open(journal_path, "a", encoding="utf-8") as fh:
            fh.write(JOURNAL_LINE + "\n")
        # Real shape minus "timings": llama-server error responses and some
        # non-chat endpoints carry no timings field at all.
        broken = {k: v for k, v in RESPONSE.items() if k != "timings"}
        return _FakeResponse(json.dumps(broken).encode("utf-8"))

    monkeypatch.setattr(speed.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="no usable timings"):
        speed._one_pass("http://gw:8081", journal_path, "filler", 256)


def test_one_pass_raises_when_the_journal_has_no_ttft(tmp_path, monkeypatch):
    journal_path = tmp_path / "journal.jsonl"
    journal_path.write_text("")  # exists, but no line gets appended below

    def fake_urlopen(req, timeout=None):
        # The gateway never wrote a journal line for this request: a lost
        # write, a wrong path filter, or a race. Must not read as ttft 0.0.
        return _FakeResponse(json.dumps(RESPONSE).encode("utf-8"))

    monkeypatch.setattr(speed.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="no ttft_s"):
        speed._one_pass("http://gw:8081", journal_path, "filler", 256)
