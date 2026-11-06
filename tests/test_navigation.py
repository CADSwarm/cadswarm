"""Left-drag moves / right-drag turns: the turntable math, and the real thing in an offscreen FreeCAD."""
import json
import math
import os
import subprocess
from pathlib import Path

import pytest

from cadswarm.gui.navigation import orbit, rotate

FRONT = (math.sin(math.pi / 4), 0.0, 0.0, math.cos(math.pi / 4))  # looking along +Y, Z up


def close(a, b, tol=1e-9):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def test_orbit_keeps_the_focal_point_and_distance():
    position = (0.0, -100.0, 0.0)
    q, p = orbit(FRONT, position, 100.0, 37, -21)
    look = rotate(q, (0.0, 0.0, -1.0))
    assert close(tuple(a + d * 100.0 for a, d in zip(p, look)), (0.0, 0.0, 0.0))
    assert math.isclose(math.dist(p, (0.0, 0.0, 0.0)), 100.0)


def test_drag_right_turns_the_front_of_the_model_right():
    # The camera swings left round the model, so a point on the model's front moves right on screen.
    q, p = orbit(FRONT, (0.0, -100.0, 0.0), 100.0, 50, 0)
    assert p[0] < 0 and math.isclose(p[2], 0.0, abs_tol=1e-9)
    assert close(rotate(q, (0.0, 1.0, 0.0)), (0.0, 0.0, 1.0))  # Z stays up on a sideways drag


def test_drag_up_shows_the_underside():
    _, p = orbit(FRONT, (0.0, -100.0, 0.0), 100.0, 0, -50)
    assert p[2] < 0  # the camera dips below the model


def test_no_drag_changes_nothing():
    q, p = orbit(FRONT, (1.0, -100.0, 2.0), 100.0, 0, 0)
    assert close(q, FRONT) and close(p, (1.0, -100.0, 2.0))


@pytest.mark.skipif(os.environ.get("CADSWARM_TEST_PANEL_GUI") != "1",
                    reason="Set CADSWARM_TEST_PANEL_GUI=1 for Qt integration (opens a FreeCAD window)")
def test_real_view_pans_orbits_and_still_clicks(tmp_path):
    from test_structural_freecad import ROOT, runtime
    python, env = runtime()
    env.pop("QT_QPA_PLATFORM", None)  # the camera moves redraw the view, which needs a real GL context
    profile = tmp_path / "freecad-profile"
    profile.mkdir()
    env["FREECAD_USER_HOME"] = env["FREECAD_USER_DATA"] = str(profile)
    proc = subprocess.run([python, str(ROOT / "tests" / "navigation_gui_check.py"), str(tmp_path)],
                          env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=240)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    r = json.loads(Path(tmp_path / "navigation-verified.json").read_text(encoding="utf-8"))

    # Left-drag on empty space slides the view: the point grabbed stays under the mouse, no turning.
    assert r["pan"]["turned"] is False and r["pan"]["moved"] > 0
    assert r["pan"]["grabbed_point_error"] < 0.05 * r["pan"]["moved"], r["pan"]
    assert r["pan"]["freecad_saw"] == []
    # Right-drag turns it round the focal point and FreeCAD never sees the drag (no menu).
    assert r["orbit"]["turned"] is True and r["orbit"]["focal_kept"] is True
    assert r["orbit"]["freecad_saw"] == []
    # Clicks are handed back: a left click on empty space and a right click both reach FreeCAD.
    assert r["left_click"]["freecad_saw"] == ["press", "release"]
    assert r["right_click"]["freecad_saw"] == ["press", "release"]
    # A left press on a part goes straight through (picking, dragging handles), so does Ctrl+drag.
    assert r["on_part"]["freecad_saw"][:2] == ["press", "move"] and r["on_part"]["moved"] == 0
    assert r["ctrl_drag"]["freecad_saw"][:2] == ["press", "move"]
    # Switched off, nothing is intercepted.
    assert r["disabled"]["freecad_saw"][:2] == ["press", "move"] and r["disabled"]["moved"] == 0
