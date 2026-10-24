"""Real FreeCAD/solver/GUI and two-participant acceptance integration, where available."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from cadswarm.config import find_freecad_python

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    try:
        python = find_freecad_python()
    except RuntimeError:
        pytest.skip("FreeCAD Python unavailable")
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + str(Path(python).parent))
    check = subprocess.run([python, "-c", "import FreeCAD,Part,numpy,scipy"], capture_output=True, env=env)
    if check.returncode:
        pytest.skip("FreeCAD Python lacks structural numpy/scipy dependencies: " + check.stderr.decode(errors="replace"))
    return python, env


@pytest.mark.parametrize("solver", ["scipy", "calculix"])
def test_real_freecad_roundtrips_and_holes(tmp_path, solver):
    python, env = runtime()
    if solver == "calculix" and not (os.environ.get("CADSWARM_CCX") or (Path(python).parent / "ccx.exe").is_file()):
        pytest.skip("CalculiX unavailable")
    proc = subprocess.run([python, str(ROOT / "tests" / "structural_freecad_check.py"), str(tmp_path), solver],
                          env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    evidence = json.loads((tmp_path / "verified.json").read_text())
    assert evidence["optimized_volume_mm3"] < evidence["original_volume_mm3"]


def test_acceptance_uses_existing_hub_guards(tmp_path):
    python, env = runtime()
    from cadswarm.hub.core import Hub, HubError
    from cadswarm.hub.worker_client import WorkerClient
    from cadswarm.structural.collaboration import accepted_operation
    worker = WorkerClient(str(tmp_path / "model.FCStd"), python=python)
    hub = Hub(worker, str(tmp_path / "hub.sqlite"), "test")
    try:
        alice = hub.join("test", "alice", kind="human")["participant_id"]
        bob = hub.join("test", "bob", kind="human")["participant_id"]
        hub.op(bob, "create_primitive", {"kind": "box", "name": "Source", "props": {"Length": 12, "Width": 4, "Height": 4}})
        snapshot = hub.brep(["Source"])["Source"]["brep"]
        # Reuse actual engine output; no network mutation occurs during numerical search.
        request = {"brep": snapshot, "pitch_mm": 1, "solver": "scipy",
                   "material": {"name": "Test", "young_mpa": 70000, "poisson": .3, "yield_mpa": 240, "density_kg_mm3": 2.7e-6},
                   "loads": [{"side": "x_max", "force_n": [0, 0, -10]}], "supports": [{"side": "x_min"}],
                   "constraints": {"max_stress_mpa": 80, "max_displacement_mm": .1, "safety_factor": 2, "target_reduction": .02}}
        request_path = tmp_path / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        out = tmp_path / "output"
        proc = subprocess.run([python, "-m", "cadswarm.structural.job", str(request_path), str(out)], env=env,
                              capture_output=True, text=True, timeout=180)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        report = json.loads((out / "report.json").read_text())
        candidate = (out / "optimized.brep").read_text()
        assert {o["name"] for o in hub.model()["objects"]} == {"Params", "Source"}
        op = accepted_operation("Source", 1, snapshot, candidate, "Optimized", report)
        hub.acquire(bob, ["Optimized"])
        with pytest.raises(HubError) as reserved:
            hub.op(alice, **op)
        assert reserved.value.code == "locked"
        hub.release(bob, ["Optimized"])
        response = hub.op(alice, **op)
        assert response["created"] == ["Optimized"] and response["modified"] == []
        assert response["result"]["result"]["structural_report"]["status"] == "improved"
        assert hub.holder("Source") == bob and hub.holder("Optimized") == alice
        assert hub.brep(["Source"])["Source"]["brep"] == snapshot
        with pytest.raises(HubError):
            hub.op(alice, **op)  # never overwrite an accepted result
        hub.op(bob, "set_props", {"name": "Source", "props": {"Length": 16}})
        stale = accepted_operation("Source", 1, snapshot, candidate, "Another", report)
        with pytest.raises(HubError) as changed:
            hub.op(alice, **stale)
        assert changed.value.code == "stale_rev"
        assert "Another" not in {o["name"] for o in hub.model()["objects"]}
    finally:
        worker.stop()


@pytest.mark.skipif(os.environ.get("CADSWARM_TEST_STRUCTURAL_GUI") != "1", reason="Set CADSWARM_TEST_STRUCTURAL_GUI=1 for offscreen Qt integration")
def test_real_freecad_gui_accept_reject_cancel(tmp_path):
    python, env = runtime()
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.run([python, str(ROOT / "tests" / "structural_gui_check.py"), str(tmp_path)], env=env,
                          capture_output=True, text=True, timeout=240)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((tmp_path / "gui-verified.json").read_text())["accepted"]
