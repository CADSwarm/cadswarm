"""The Team panel in a real (offscreen) FreeCAD: fits a narrow dock without cropping, scrolls when short, shows
the helpers' replies in the chat and groups a whole-team request's tasks."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_structural_freecad import ROOT, runtime


@pytest.mark.skipif(os.environ.get("CADSWARM_TEST_PANEL_GUI") != "1", reason="Set CADSWARM_TEST_PANEL_GUI=1 for offscreen Qt integration")
def test_real_freecad_panel_layout_and_chat(tmp_path):
    python, env = runtime()
    env["QT_QPA_PLATFORM"] = "offscreen"
    profile = tmp_path / "freecad-profile"
    profile.mkdir()
    env["FREECAD_USER_HOME"] = env["FREECAD_USER_DATA"] = str(profile)
    proc = subprocess.run([python, str(ROOT / "tests" / "panel_gui_check.py"), str(tmp_path)], env=env,
                          capture_output=True, text=True, timeout=240)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    r = json.loads(Path(tmp_path / "panel-verified.json").read_text(encoding="utf-8"))
    assert all(c["no_horizontal_overflow"] and c["scrolls_when_short"] for c in r["layout"].values()), r["layout"]
    assert r["chat_has_reply"] and r["tasks_grouped_under_request"]
    assert r["tabs"] == ["Chat", "Tasks", "Activity"]
    assert r["targets"][0].startswith("command:")  # an AI helper is the first choice to talk to
    assert "teammate's computer" in r["away_tip"]
