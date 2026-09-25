"""Drive one llama-server station over SSH through llm-ctl.ps1 -Action bench."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.request

from .config import BenchConfig, render_launch_spec

SPEC_DIR = r"D:\LLM-Setup\bench-specs"


class StationError(Exception):
    pass


def parse_vram(nvidia_out: str, shared_bytes_out: str) -> dict:
    used, total = (int(v) for v in nvidia_out.strip().splitlines()[0].split(","))
    shared = int(float(shared_bytes_out.strip() or 0)) // (1024 * 1024)
    return {"used_mb": used, "total_mb": total, "shared_mb": shared}


def ps_quote(value: str) -> str:
    """Escape a value for interpolation inside a PowerShell single-quoted string.

    PowerShell's own escape for a literal single quote inside '...' is to
    double it: doing this at every interpolation point, rather than trusting
    the caller, is what keeps ctl_path or a spec path free to contain a quote
    without breaking out of the quoted argument.
    """
    return str(value).replace("'", "''")


class Station:
    def __init__(self, ssh_target: str, base_url: str, ctl_path: str, runner=subprocess.run):
        self.ssh_target = ssh_target
        self.base_url = base_url.rstrip("/")
        self.ctl_path = ctl_path
        self.run = runner

    def _ps(self, command: str, encoded: bool = False) -> subprocess.CompletedProcess:
        # Windows OpenSSH hands the command line to cmd.exe. Short commands go
        # through plain -Command; anything with a pipe or nested quotes goes
        # through -EncodedCommand (UTF-16LE, base64), which cmd.exe cannot mangle.
        if encoded:
            blob = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
            remote_cmd = f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {blob}"
        else:
            remote_cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -Command "{command}"'
        # errors="replace": PowerShell writes progress noise to stderr in the
        # station's console codepage (French, not UTF-8); only stdout is read.
        return self.run(["ssh", self.ssh_target, remote_cmd],
                        capture_output=True, text=True, errors="replace", check=True)

    def _health_ok(self) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/health", timeout=5) as r:
                return json.load(r).get("status") == "ok"
        except OSError:
            return False

    def start(self, cfg: BenchConfig, timeout_s: int = 900) -> None:
        spec = render_launch_spec(cfg)
        remote = f"{SPEC_DIR}\\{spec['name']}.json"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(spec, fh)
        try:
            self.run(["scp", "-q", fh.name, f"{self.ssh_target}:{remote.replace(chr(92), '/')}"], check=True)
        finally:
            os.unlink(fh.name)
        self._ps(f"& '{ps_quote(self.ctl_path)}' -Action bench -Spec '{ps_quote(remote)}'")
        deadline = time.monotonic() + timeout_s
        while True:
            if self._health_ok():
                return
            if time.monotonic() >= deadline:
                raise StationError(f"health never ok for {spec['name']} within {timeout_s}s")
            time.sleep(5)

    def stop(self) -> None:
        self._ps(f"& '{ps_quote(self.ctl_path)}' -Action stop")

    def warmup(self) -> None:
        body = json.dumps({"messages": [{"role": "user", "content": "Say ok."}], "max_tokens": 16}).encode()
        req = urllib.request.Request(self.base_url + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=600).read()

    def vram(self) -> dict:
        used = self._ps("nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits").stdout
        shared = self._ps(
            "(Get-Counter '\\GPU Process Memory(*)\\Shared Usage').CounterSamples | "
            "Measure-Object CookedValue -Sum | ForEach-Object Sum",
            encoded=True,
        ).stdout
        return parse_vram(used, shared)
