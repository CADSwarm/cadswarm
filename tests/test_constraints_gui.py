"""The Auto-constrain dialog in a real (offscreen) FreeCAD: lists what it inferred, and Apply sends only the
ticked constraints. The planner itself is covered in test_constraints.py."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_structural_freecad import ROOT, runtime


@pytest.mark.skipif(os.environ.get("CADSWARM_TEST_PANEL_GUI") != "1",
                    reason="Set CADSWARM_TEST_PANEL_GUI=1 for offscreen Qt integration")
def test_real_freecad_constrain_dialog_applies_the_ticked_rows(tmp_path):
    python, env = runtime()
    env["QT_QPA_PLATFORM"] = "offscreen"
    profile = tmp_path / "freecad-profile"
    profile.mkdir()
    env["FREECAD_USER_HOME"] = env["FREECAD_USER_DATA"] = str(profile)
    proc = subprocess.run([python, str(ROOT / "tests" / "constraints_gui_check.py"), str(tmp_path)],
                          env=env, capture_output=True, text=True, timeout=240)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    r = json.loads((tmp_path / "constraints-verified.json").read_text(encoding="utf-8"))
    assert r["titles"] == ["One height for 2 parts", "Hole1 follows the team's slip fit"]
    assert r["fits_line"] and r["ticked_by_default"]
    # Unticking the fit and pressing Apply sends exactly the shared size, scoped to what was selected.
    assert r["sent"] == [["/constrain", {"apply": True, "ids": ["shared:height"],
                                         "names": ["Base", "Lid", "Hole1", "Pin"]}]]
    assert r["said"] == ["Auto-constrain: 1 constraint(s); shared sizes height; recomputed 2 part(s)"]
    assert r["closed"]
    assert (tmp_path / "constraints-dialog.png").exists()
