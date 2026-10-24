"""Real FreeCAD/solver/GUI and two-participant acceptance integration, where available."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from cadswarm.config import find_freecad_python, freecad_import_dirs

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    try:
        python = find_freecad_python()
    except RuntimeError:
        pytest.skip("FreeCAD Python unavailable")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), *freecad_import_dirs(python)]))
    check = subprocess.run([python, "-c", "import FreeCAD,Part,numpy,scipy"], capture_output=True, env=env)
    if check.returncode:
        pytest.skip("FreeCAD Python lacks structural numpy/scipy dependencies: " + check.stderr.decode(errors="replace"))
    return python, env


@pytest.mark.parametrize("solver", ["scipy", "calculix"])
def test_real_freecad_roundtrips_and_holes(tmp_path, solver):
    python, env = runtime()
    if solver == "calculix" and not (os.environ.get("CADSWARM_CCX") or any((Path(python).parent / n).is_file() for n in ("ccx.exe", "ccx"))):
        pytest.skip("CalculiX unavailable")
    proc = subprocess.run([python, str(ROOT / "tests" / "structural_freecad_check.py"), str(tmp_path), solver],
                          env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    evidence = json.loads((tmp_path / "verified.json").read_text())
    assert evidence["optimized_volume_mm3"] < evidence["original_volume_mm3"]


def test_accepted_operation_runs_in_the_worker(tmp_path):
    python, env = runtime()
    from cadswarm.worker_client import WorkerClient, WorkerError
    from cadswarm.structural.collaboration import accepted_operation
    worker = WorkerClient(str(tmp_path / "model.FCStd"), python=python)
    try:
        worker.call("create_primitive", {"kind": "box", "name": "Source", "props": {"Length": 12, "Width": 4, "Height": 4}}, allowed=["Source"])
        snapshot = worker.call("brep", {"names": ["Source"]})["result"]["objects"]["Source"]["brep"]
        # Reuse actual engine output; no network mutation occurs during numerical search.
        request = {"brep": snapshot, "pitch_mm": 1, "solver": "scipy",
                   "material": {"name": "Test", "young_mpa": 70000, "poisson": .3, "yield_mpa": 240, "density_kg_mm3": 2.7e-6},
                   "loads": [{"side": "x_max", "force_n": [0, 0, -10]}], "supports": [{"side": "x_min"}],
                   "constraints": {"max_stress_mpa": 80, "max_displacement_mm": .1, "safety_factor": 2, "target_reduction": .02}}
        request_path = tmp_path / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        out = tmp_path / "output"
        proc = subprocess.run([python, "-m", "cadswarm.structural.job", str(request_path), str(out)], env=env,
                              capture_output=True, encoding="utf-8", errors="replace", timeout=180)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        report = json.loads((out / "report.json").read_text())
        candidate = (out / "optimized.brep").read_text()
        assert {o["name"] for o in worker.call("status")["result"]["objects"]} == {"Params", "Source"}
        op = accepted_operation("Source", 1, snapshot, candidate, "Optimized", report)
        assert op["op"] == "run_part_script" and op["expected_revs"] == {"Source": 1, "Optimized": 0}
        # Lock ownership and stale revisions are refused by the SpacetimeDB module before an op reaches the worker (rules::check_op / rules::check_expected_revs, tested in rules/).
        response = worker.call(op["op"], op["args"], allowed=["Optimized"])
        assert response["created"] == ["Optimized"] and response["modified"] == []
        assert response["result"]["result"]["structural_report"]["status"] == "improved"
        assert worker.call("brep", {"names": ["Source"]})["result"]["objects"]["Source"]["brep"] == snapshot
        with pytest.raises(WorkerError):
            worker.call(op["op"], op["args"], allowed=["Optimized"])  # never overwrite an accepted result
        worker.call("set_props", {"name": "Source", "props": {"Length": 16}}, allowed=["Source"])
        stale = accepted_operation("Source", 1, snapshot, candidate, "Another", report)
        with pytest.raises(WorkerError):
            worker.call(stale["op"], stale["args"], allowed=["Another"])
        assert "Another" not in {o["name"] for o in worker.call("status")["result"]["objects"]}
    finally:
        worker.stop()


@pytest.mark.skipif(os.environ.get("CADSWARM_TEST_STRUCTURAL_GUI") != "1", reason="Set CADSWARM_TEST_STRUCTURAL_GUI=1 for offscreen Qt integration")
def test_real_freecad_gui_accept_reject_cancel(tmp_path):
    python, env = runtime()
    env["QT_QPA_PLATFORM"] = "offscreen"
    # Keep startup workbenches/preferences isolated from an open desktop session.
    profile = tmp_path / "freecad-profile"
    profile.mkdir()
    env["FREECAD_USER_HOME"] = str(profile)
    env["FREECAD_USER_DATA"] = str(profile)
    proc = subprocess.run([python, str(ROOT / "tests" / "structural_gui_check.py"), str(tmp_path)], env=env,
                          capture_output=True, encoding="utf-8", errors="replace", timeout=240)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((tmp_path / "gui-verified.json").read_text())["accepted"]
