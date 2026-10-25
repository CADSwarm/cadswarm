"""The Library tab and Settings dialog in a real (offscreen) FreeCAD GUI, where available."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from cadswarm.config import find_freecad_python, freecad_import_dirs
from cadswarm.library.present import decimals_for, describe, setting_value_text, size_label

ROOT = Path(__file__).resolve().parents[1]


def test_plain_words_for_parts_and_settings():
    assert size_label([0, 0, 0, 40, 20, 5]) == "40 × 20 × 5 mm" and size_label([]) == ""
    assert [decimals_for(s) for s in (1, 0.5, 0.05, 0.01, 0.001)] == [0, 1, 2, 2, 3]
    text = describe({"name": "Bracket <v2>", "source": "url", "category": "structural", "license": "CC-BY-4.0",
                     "features": {"holes": [{"d": 3.4, "count": 4}]}, "source_url": "https://a.test/b.step"})
    assert "Bracket &lt;v2&gt;" in text and "4 × Ø3.4 mm" in text and "From a link" in text
    assert setting_value_text({"kind": "num", "unit": "mm"}, 0.2) == "0.2 mm"
    assert setting_value_text({"kind": "choice"}, "heat_set_insert") == "heat set insert"


@pytest.mark.skipif(os.environ.get("CADSWARM_TEST_LIBRARY_GUI") != "1",
                    reason="Set CADSWARM_TEST_LIBRARY_GUI=1 for the offscreen FreeCAD GUI check")
def test_library_tab_and_settings_dialog_in_freecad(tmp_path):
    try:
        python = find_freecad_python()
    except RuntimeError:
        pytest.skip("FreeCAD Python unavailable")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), *freecad_import_dirs(python)]),
               QT_QPA_PLATFORM="offscreen")
    profile = tmp_path / "freecad-profile"
    profile.mkdir()
    env["FREECAD_USER_HOME"] = env["FREECAD_USER_DATA"] = str(profile)
    proc = subprocess.run([python, str(ROOT / "tests" / "library_gui_check.py"), str(tmp_path)], env=env,
                          capture_output=True, encoding="utf-8", errors="replace", timeout=240)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((tmp_path / "library-gui-verified.json").read_text())["ok"]
