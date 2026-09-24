import dataclasses
import json
import pathlib
import pytest
from benchrun import runner as runner_mod
from benchrun.runner import Campaign, CampaignError, SuiteSkipped
from benchrun.config import load_config

CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")


class FakeStation:
    def __init__(self, shared_mb=0):
        self.log, self.shared_mb = [], shared_mb

    def start(self, cfg, timeout_s=900): self.log.append("start")
    def warmup(self): self.log.append("warmup")
    def stop(self): self.log.append("stop")
    def vram(self): return {"used_mb": 20000, "shared_mb": self.shared_mb}


class FailingStartStation(FakeStation):
    def start(self, cfg, timeout_s=900):
        self.log.append("start")
        raise RuntimeError("start blew up")


class FailingStartAndStopStation(FakeStation):
    """start() and stop() both raise, but only for the named model: the
    other model's pass goes through cleanly, so a campaign of two
    configurations can prove the second still runs to completion."""

    def __init__(self, failing_model):
        super().__init__()
        self.failing_model = failing_model
        self._current_model = None

    def start(self, cfg, timeout_s=900):
        self.log.append("start")
        self._current_model = cfg.model
        if cfg.model == self.failing_model:
            raise RuntimeError("start blew up")

    def stop(self):
        self.log.append("stop")
        if self._current_model == self.failing_model:
            raise RuntimeError("stop blew up")


class FailingStopStation(FakeStation):
    def stop(self):
        self.log.append("stop")
        raise RuntimeError("stop blew up")


class WarmupInterruptStation(FakeStation):
    """warmup() raises KeyboardInterrupt, as if an operator hit Ctrl-C mid
    campaign: stop() must still run, unlike an ordinary Exception it must
    not be swallowed."""

    def warmup(self):
        self.log.append("warmup")
        raise KeyboardInterrupt()


class FailingVramMidSuiteStation(FakeStation):
    """vram() raises on its second call onward, but only for the named
    model: the first (post-warmup) reading succeeds, the one after the
    first rep raises, and the other model's pass is unaffected."""

    def __init__(self, failing_model):
        super().__init__()
        self.failing_model = failing_model
        self._current_model = None
        self.vram_calls = 0

    def start(self, cfg, timeout_s=900):
        self.log.append("start")
        self._current_model = cfg.model
        self.vram_calls = 0

    def vram(self):
        self.vram_calls += 1
        if self._current_model == self.failing_model and self.vram_calls >= 2:
            raise RuntimeError("vram read blew up")
        return {"used_mb": 20000, "shared_mb": 10}


class RisingVramStation(FakeStation):
    """vram() reports low usage on its first call (right after warmup), then
    a spike on every later call (during a suite), to prove a spill that only
    shows up mid-suite is not missed."""

    def __init__(self):
        super().__init__()
        self.vram_calls = 0

    def vram(self):
        self.vram_calls += 1
        shared = 10 if self.vram_calls == 1 else 900
        return {"used_mb": 20000, "shared_mb": shared}


class FakeSuite:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        return [{"item_id": "i1", "passed": 1, "detail": {}}]


class RaisingSuite:
    name = "boom"

    def run(self, ctx):
        raise RuntimeError("suite exploded")


class EmptySuite:
    name = "empty"

    def run(self, ctx):
        return []


class NotApplicableSuite:
    name = "longctx"

    def run(self, ctx):
        raise SuiteSkipped("every length above the served window")


class SystemExitSuite:
    """Raises SystemExit on its second call, as if a suite called sys.exit()
    partway through a pass: the first rep's .done must survive, stop() must
    still run, and the SystemExit must still propagate out of run()."""

    name = "fake"

    def __init__(self):
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        if self.calls >= 2:
            raise SystemExit(1)
        return [{"item_id": "i1", "passed": 1, "detail": {}}]


class FailsOnceThenSucceeds:
    """Raises on its first call, succeeds on every one after: proves one
    suite's own failure does not stop a LATER suite in the same profile."""

    def __init__(self, name):
        self.name = name
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        if self.calls == 1:
            raise ValueError("boom on first call")
        return [{"item_id": "i1", "passed": 1, "detail": {}}]


class FailsForModel:
    """Raises only when run against the named model, to prove a campaign
    keeps going on the remaining configurations after one of them fails."""

    name = "fake"

    def __init__(self, failing_model):
        self.failing_model = failing_model
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        if ctx.cfg.model == self.failing_model:
            raise RuntimeError("suite exploded")
        return [{"item_id": "i1", "passed": 1, "detail": {}}]


def make(tmp_path, station=None, suite=None):
    return Campaign(station or FakeStation(), "http://gw", [suite or FakeSuite()], tmp_path, reps=3), suite


def test_set_context_lays_the_suite_override_over_the_config_sampling(tmp_path, monkeypatch):
    posted = []

    class _Resp:
        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=None):
        posted.append(json.loads(req.data))
        return _Resp()

    class PinsMaxTokens(FakeSuite):
        sampling_override = {"max_tokens": 256}

    monkeypatch.setattr(runner_mod.urllib.request, "urlopen", fake_urlopen)
    camp, _ = make(tmp_path)
    camp._set_context(dataclasses.replace(CFG, sampling={"temperature": 0.6, "max_tokens": 16384}), PinsMaxTokens(), 1)
    camp._set_context(dataclasses.replace(CFG, sampling={"temperature": 0.6, "max_tokens": 16384}), FakeSuite(), 1)
    assert posted[0]["sampling"] == {"temperature": 0.6, "max_tokens": 256}
    assert posted[1]["sampling"] == {"temperature": 0.6, "max_tokens": 16384}


def test_runner_runs_three_reps_after_warmup(tmp_path, monkeypatch):
    suite = FakeSuite()
    camp, _ = make(tmp_path, suite=suite)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    camp.run([CFG])
    assert suite.calls == 3
    assert camp.station.log == ["start", "warmup", "stop"]
    base = tmp_path / "99" / "tiel" / "R1" / "fake"
    assert all((base / f"rep{k}.done").exists() for k in (1, 2, 3))


def test_runner_resume_skips_complete_and_redoes_partial(tmp_path, monkeypatch):
    base = tmp_path / "99" / "tiel" / "R1" / "fake"
    base.mkdir(parents=True)
    (base / "rep1.jsonl").write_text('{"item_id":"i1","passed":1,"detail":{}}\n')
    (base / "rep1.done").write_text("")
    (base / "rep2.jsonl").write_text('{"item_id":"i1","passed":0,"detail":{}}\n')
    suite = FakeSuite()
    camp, _ = make(tmp_path, suite=suite)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    camp.run([CFG])
    assert suite.calls == 2
    assert len((base / "rep2.jsonl").read_text().splitlines()) == 1


def test_runner_skips_station_when_everything_done(tmp_path, monkeypatch):
    base = tmp_path / "99" / "tiel" / "R1" / "fake"
    base.mkdir(parents=True)
    for k in (1, 2, 3):
        (base / f"rep{k}.jsonl").write_text("")
        (base / f"rep{k}.done").write_text("")
    camp, _ = make(tmp_path)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    camp.run([CFG])
    assert camp.station.log == []


def test_runner_marks_spill(tmp_path, monkeypatch):
    camp, _ = make(tmp_path, station=FakeStation(shared_mb=900))
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    camp.run([CFG])
    meta = json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())
    assert meta["spill"] is True


def test_runner_writes_meta_when_suite_raises(tmp_path, monkeypatch):
    station = FakeStation()
    camp, _ = make(tmp_path, station=station, suite=RaisingSuite())
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(CampaignError, match="suite exploded"):
        camp.run([CFG])
    assert station.log == ["start", "warmup", "stop"]
    meta = json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())
    assert "error" in meta and "suite exploded" in meta["error"]


def test_runner_a_failing_suite_does_not_stop_the_next_suite(tmp_path, monkeypatch):
    # boom fails every rep; good must still run its own three reps for the
    # same profile, not be skipped because boom went first.
    station = FakeStation()
    good = FakeSuite()
    good.name = "good"
    camp = Campaign(station, "http://gw", [RaisingSuite(), good], tmp_path, reps=3)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(CampaignError, match="suite exploded"):
        camp.run([CFG])
    assert good.calls == 3
    good_base = tmp_path / "99" / "tiel" / "R1" / "good"
    assert all((good_base / f"rep{k}.done").exists() for k in (1, 2, 3))
    assert station.log == ["start", "warmup", "stop"]  # station only touched once


def test_runner_a_failing_rep_writes_an_error_marker_no_done(tmp_path, monkeypatch):
    suite = FailsOnceThenSucceeds(name="flaky")
    camp = Campaign(FakeStation(), "http://gw", [suite], tmp_path, reps=3)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(CampaignError, match="boom on first call"):
        camp.run([CFG])
    base = tmp_path / "99" / "tiel" / "R1" / "flaky"
    assert not (base / "rep1.done").exists()
    error_text = (base / "rep1.error").read_text(encoding="utf-8")
    assert "ValueError" in error_text and "boom on first call" in error_text
    assert "Traceback" in error_text
    # reps 2 and 3 got their own turn and succeeded, unaffected by rep1
    assert (base / "rep2.done").exists() and (base / "rep3.done").exists()


def test_runner_a_failing_rep_is_picked_up_on_resume(tmp_path, monkeypatch):
    # No .done marker for the failed rep means Campaign._pending() retries
    # it on the next launch, the same resumability every other rep gets.
    suite = FailsOnceThenSucceeds(name="flaky")
    camp = Campaign(FakeStation(), "http://gw", [suite], tmp_path, reps=3)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(CampaignError):
        camp.run([CFG])
    assert suite.calls == 3  # rep1 failed, rep2/rep3 succeeded
    pending = camp._pending(CFG)
    assert [rep for _, rep in pending] == [1]  # only the failed rep is left


def test_runner_cell_still_marked_failed_when_only_one_suite_failed(tmp_path, monkeypatch):
    station = FakeStation()
    good = FakeSuite()
    good.name = "good"
    camp = Campaign(station, "http://gw", [RaisingSuite(), good], tmp_path, reps=1)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(CampaignError, match="99/tiel/R1"):
        camp.run([CFG])
    meta = json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())
    assert "error" in meta and "suite exploded" in meta["error"]
    assert "boom rep1" in meta["error"]


def test_runner_records_a_suite_that_grades_nothing(tmp_path, monkeypatch):
    # A harness that silently selects zero items (a retired LiveBench task, a
    # wrong release) must fail the cell, not leave an empty "done" pass.
    camp, _ = make(tmp_path, suite=EmptySuite())
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(CampaignError, match="graded no item"):
        camp.run([CFG])
    cell = tmp_path / "99" / "tiel" / "R1"
    assert not (cell / "empty" / "rep1.done").exists()
    assert "graded no item" in json.loads((cell / "meta.json").read_text())["error"]


def test_runner_records_a_skipped_suite_without_failing_the_config(tmp_path, monkeypatch):
    # A long-context suite on an 8192-token model does not apply: the rep is
    # marked skipped with its reason and done, the configuration does not fail.
    camp, _ = make(tmp_path, suite=NotApplicableSuite())
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    camp.run([CFG])
    out = tmp_path / "99" / "tiel" / "R1" / "longctx"
    assert (out / "rep1.skipped").read_text() == "every length above the served window"
    assert (out / "rep1.done").exists()
    assert not (out / "rep1.jsonl").exists()
    assert all((out / f"rep{k}.skipped").exists() for k in (1, 2, 3))
    assert "error" not in json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())


def test_runner_records_start_failure_and_continues(tmp_path, monkeypatch):
    station = FailingStartStation()
    camp, _ = make(tmp_path, station=station)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(CampaignError, match="start blew up"):
        camp.run([CFG])
    assert station.log == ["start", "stop"]
    meta = json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())
    assert "error" in meta and "start blew up" in meta["error"]
    assert meta["vram"] is None


def test_runner_continues_after_one_config_fails(tmp_path, monkeypatch):
    cfg_bad = CFG
    cfg_good = dataclasses.replace(CFG, model="tiel2")
    suite = FailsForModel(failing_model=cfg_bad.model)
    station = FakeStation()
    camp = Campaign(station, "http://gw", [suite], tmp_path, reps=3)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(CampaignError, match="99/tiel/R1"):
        camp.run([cfg_bad, cfg_good])
    good_base = tmp_path / "99" / "tiel2" / "R1" / "fake"
    assert all((good_base / f"rep{k}.done").exists() for k in (1, 2, 3))
    bad_meta = json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())
    assert "error" in bad_meta and "suite exploded" in bad_meta["error"]
    good_meta = json.loads((tmp_path / "99" / "tiel2" / "R1" / "meta.json").read_text())
    assert "error" not in good_meta


def test_runner_marks_spill_seen_during_a_suite(tmp_path, monkeypatch):
    station = RisingVramStation()
    camp, _ = make(tmp_path, station=station)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    camp.run([CFG])
    meta = json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())
    assert meta["spill"] is True
    assert meta["vram"]["shared_mb"] == 900
    assert meta["vram"]["samples"] == 4


def test_runner_records_both_start_and_stop_failure_and_continues(tmp_path, monkeypatch):
    cfg_bad = CFG
    cfg_good = dataclasses.replace(CFG, model="tiel2")
    station = FailingStartAndStopStation(failing_model=cfg_bad.model)
    suite = FakeSuite()
    camp = Campaign(station, "http://gw", [suite], tmp_path, reps=3)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(CampaignError, match="99/tiel/R1"):
        camp.run([cfg_bad, cfg_good])
    good_base = tmp_path / "99" / "tiel2" / "R1" / "fake"
    assert all((good_base / f"rep{k}.done").exists() for k in (1, 2, 3))
    bad_meta = json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())
    assert "start blew up" in bad_meta["error"]
    assert "stop blew up" in bad_meta["stop_error"]
    good_meta = json.loads((tmp_path / "99" / "tiel2" / "R1" / "meta.json").read_text())
    assert "error" not in good_meta and "stop_error" not in good_meta
    error_txt = (tmp_path / "99" / "tiel" / "R1" / "error.txt").read_text()
    assert "start blew up" in error_txt and "stop blew up" in error_txt
    assert not (tmp_path / "99" / "tiel2" / "R1" / "error.txt").exists()


def test_runner_records_stop_failure_when_suite_succeeds(tmp_path, monkeypatch):
    station = FailingStopStation()
    suite = FakeSuite()
    camp, _ = make(tmp_path, station=station, suite=suite)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(CampaignError, match="stop blew up"):
        camp.run([CFG])
    assert suite.calls == 3
    base = tmp_path / "99" / "tiel" / "R1" / "fake"
    assert all((base / f"rep{k}.done").exists() for k in (1, 2, 3))
    meta = json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())
    assert "error" not in meta
    assert "stop blew up" in meta["stop_error"]
    assert (tmp_path / "99" / "tiel" / "R1" / "error.txt").exists()


def test_runner_records_vram_failure_mid_suite_and_continues(tmp_path, monkeypatch):
    cfg_bad = CFG
    cfg_good = dataclasses.replace(CFG, model="tiel2")
    station = FailingVramMidSuiteStation(failing_model=cfg_bad.model)
    suite = FakeSuite()
    camp = Campaign(station, "http://gw", [suite], tmp_path, reps=3)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(CampaignError, match="vram read blew up"):
        camp.run([cfg_bad, cfg_good])
    bad_base = tmp_path / "99" / "tiel" / "R1" / "fake"
    assert (bad_base / "rep1.done").exists()
    assert not (bad_base / "rep2.done").exists()
    bad_meta = json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())
    assert "vram read blew up" in bad_meta["error"]
    good_base = tmp_path / "99" / "tiel2" / "R1" / "fake"
    assert all((good_base / f"rep{k}.done").exists() for k in (1, 2, 3))
    good_meta = json.loads((tmp_path / "99" / "tiel2" / "R1" / "meta.json").read_text())
    assert "error" not in good_meta


def test_runner_stops_station_on_keyboard_interrupt_during_warmup(tmp_path, monkeypatch):
    station = WarmupInterruptStation()
    camp, _ = make(tmp_path, station=station)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(KeyboardInterrupt):
        camp.run([CFG])
    assert station.log == ["start", "warmup", "stop"]
    meta = json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())
    assert meta["interrupted"] == "KeyboardInterrupt"


def test_runner_stops_station_on_system_exit_during_suite(tmp_path, monkeypatch):
    station = FakeStation()
    camp = Campaign(station, "http://gw", [SystemExitSuite()], tmp_path, reps=3)
    monkeypatch.setattr(camp, "_set_context", lambda *a: None)
    with pytest.raises(SystemExit):
        camp.run([CFG])
    assert station.log == ["start", "warmup", "stop"]
    base = tmp_path / "99" / "tiel" / "R1" / "fake"
    assert (base / "rep1.done").exists()
    assert not (base / "rep2.done").exists()
    meta = json.loads((tmp_path / "99" / "tiel" / "R1" / "meta.json").read_text())
    assert meta["interrupted"] == "SystemExit"
