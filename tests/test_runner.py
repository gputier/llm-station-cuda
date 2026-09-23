import dataclasses
import json
import pathlib
import pytest
from benchrun.runner import Campaign, CampaignError
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
