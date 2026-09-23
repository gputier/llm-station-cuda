import subprocess
import pytest
from benchrun.station import Station, StationError, parse_vram
from benchrun.config import load_config
import pathlib

CFG = load_config(pathlib.Path(__file__).parent / "fixtures" / "config_ok.yaml")


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_start_uploads_spec_then_launches(monkeypatch):
    run = FakeRun()
    st = Station("u@h", "http://h:8080", r"D:\LLM-Setup\llm-ctl.ps1", runner=run)
    monkeypatch.setattr(st, "_health_ok", lambda: True)
    st.start(CFG, timeout_s=1)
    assert run.calls[0][0] == "scp"
    assert "-Action bench" in run.calls[1][-1]
    assert "bench-tiel-R1.json" in run.calls[1][-1]


def test_start_times_out_when_health_never_ok(monkeypatch):
    st = Station("u@h", "http://h:8080", r"D:\x.ps1", runner=FakeRun())
    monkeypatch.setattr(st, "_health_ok", lambda: False)
    with pytest.raises(StationError, match="health"):
        st.start(CFG, timeout_s=0)


def test_parse_vram():
    assert parse_vram("24576\n", "123456789\n") == {"used_mb": 24576, "shared_mb": 117}
