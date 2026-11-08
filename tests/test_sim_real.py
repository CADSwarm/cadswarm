"""Real-solver checks of the simulation features (FreeCAD 1.x's CalculiX and Gmsh), from scripts/sim_validate.py
and scripts/sim_branded.py. Slow, so they only run when asked:

    CADSWARM_TEST_SIM=1      headless: tools, FEA vs hand calc, reviewer, interference, lighten, optimize
    CADSWARM_TEST_SIM_GUI=1  the same features driven inside the branded CADSwarm app (a window opens)
    CADSWARM_TEST_SIM_RUNS=3 run every case (default: the first case of each)

Only the hand-calculation helper is checked in the normal (fast) suite.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sim_validate  # noqa: E402

RUNS = int(os.environ.get("CADSWARM_TEST_SIM_RUNS", "1"))
real = pytest.mark.skipif(os.environ.get("CADSWARM_TEST_SIM") != "1",
                          reason="Set CADSWARM_TEST_SIM=1 to run the real-solver simulation checks")
gui = pytest.mark.skipif(os.environ.get("CADSWARM_TEST_SIM_GUI") != "1",
                         reason="Set CADSWARM_TEST_SIM_GUI=1 to drive the simulation features in the branded app")


def test_cantilever_hand_calculation():
    h = sim_validate.cantilever_hand(100, 20, 10, 500, "steel_s235")
    assert h["stress_mpa"] == pytest.approx(150.0)  # 6FL/(bh^2)
    assert h["deflection_bending_only_mm"] == pytest.approx(500 * 100 ** 3 / (3 * 210000 * 20 * 10 ** 3 / 12))
    assert 1.0 < h["deflection_mm"] / h["deflection_bending_only_mm"] < 1.02  # shear adds under 2 % here


@real
@pytest.mark.parametrize("scenario", sim_validate.SCENARIOS)
def test_simulation_feature_on_real_solvers(scenario, tmp_path):
    rows = sim_validate.run_scenario(scenario, RUNS, str(tmp_path))
    assert rows and all(r["ok"] for r in rows), json.dumps(rows, indent=2, default=str)


@gui
@pytest.mark.parametrize("scenario", ["fem", "lighten", "optimize", "report"])
def test_simulation_feature_in_the_branded_app(scenario, tmp_path):
    python = sim_validate.find_freecad_python()
    for case in range(1, RUNS + 1):
        env = dict(os.environ, CADSWARM_ENGINE_HOME=str(tmp_path / f"engine-{case}"))
        p = subprocess.run([python, str(ROOT / "scripts" / "sim_branded.py"), scenario, str(tmp_path), "--case",
                            str(case)], env=env, capture_output=True, text=True, timeout=1500)
        result = json.loads((tmp_path / f"{scenario}-{case}.json").read_text(encoding="utf-8"))
        assert result["ok"], result
        assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]  # includes a clean exit (no crash on quit)
        assert all(Path(s).stat().st_size > 10_000 for s in result["shots"])
