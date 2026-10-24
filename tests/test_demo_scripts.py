"""demo-down.ps1 stops only the processes demo-up.ps1 recorded, and only when their command line matches."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt" or shutil.which("powershell") is None,
    reason="demo scripts are Windows PowerShell",
)

ROOT = Path(__file__).resolve().parents[1]


def run_down(data_dir):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts" / "demo-down.ps1"), "-Data", str(data_dir)],
        capture_output=True, text=True, timeout=120,
    )


def write_pids(data_dir, pids):
    state = data_dir / ".cadswarm-team"
    state.mkdir(parents=True, exist_ok=True)
    (state / "demo-pids.json").write_text(json.dumps(pids), encoding="utf-8")
    return state / "demo-pids.json"


def test_nothing_to_stop_without_a_pid_file(tmp_path):
    result = run_down(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "nothing to stop" in result.stdout


def test_never_stops_a_process_whose_command_line_does_not_match(tmp_path):
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        pid_file = write_pids(tmp_path, {"preview": victim.pid, "tunnel": victim.pid, "host": victim.pid})
        result = run_down(tmp_path)
        assert result.returncode == 0, result.stderr
        assert victim.poll() is None
        assert "skipped" in result.stdout
        assert not pid_file.exists()
    finally:
        victim.kill()
        victim.wait()


def test_stops_a_matching_recorded_process(tmp_path):
    script = tmp_path / "vite-standin.py"
    script.write_text("import time; time.sleep(120)", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)])
    try:
        write_pids(tmp_path, {"preview": proc.pid, "tunnel": 0, "host": 0})
        result = run_down(tmp_path)
        assert proc.wait(timeout=20) is not None
        assert "stopped preview" in result.stdout
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def run_up(data_dir):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts" / "demo-up.ps1"), "-Data", str(data_dir), "-SkipBuild"],
        capture_output=True, text=True, timeout=120,
    )


def test_up_refuses_while_a_recorded_stack_is_still_running(tmp_path):
    script = tmp_path / "cadswarm-host-standin.py"
    script.write_text("import time; time.sleep(120)", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)])
    try:
        pid_file = write_pids(tmp_path, {"preview": 0, "tunnel": 0, "host": proc.pid})
        result = run_up(tmp_path)
        assert result.returncode != 0
        assert "already running" in result.stdout + result.stderr
        assert proc.poll() is None and pid_file.exists()  # nothing stopped, nothing forgotten
    finally:
        proc.kill()
        proc.wait()
