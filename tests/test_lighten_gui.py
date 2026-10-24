"""The Lighten part dialog in a real (offscreen) FreeCAD GUI, where available."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from cadswarm.config import find_freecad_python, freecad_import_dirs

ROOT = Path(__file__).resolve().parents[1]


def test_the_dialog_describes_results_in_plain_words():
    pytest.importorskip("FreeCADGui")  # only importable inside FreeCAD


@pytest.mark.skipif(os.environ.get("CADSWARM_TEST_LIGHTEN_GUI") != "1",
                    reason="Set CADSWARM_TEST_LIGHTEN_GUI=1 for the offscreen FreeCAD GUI check")
def test_a_person_lightens_a_part_from_the_dialog(tmp_path):
    try:
        python = find_freecad_python()
    except RuntimeError:
        pytest.skip("FreeCAD Python unavailable")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), *freecad_import_dirs(python)]),
               QT_QPA_PLATFORM="offscreen")
    profile = tmp_path / "freecad-profile"
    profile.mkdir()
    env["FREECAD_USER_HOME"] = env["FREECAD_USER_DATA"] = str(profile)
    proc = subprocess.run([python, str(ROOT / "tests" / "lighten_gui_check.py"), str(tmp_path)], env=env,
                          capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    assert json.loads((tmp_path / "lighten-gui-verified.json").read_text())["ok"]
