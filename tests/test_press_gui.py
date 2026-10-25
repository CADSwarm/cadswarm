"""A panel action button acknowledges the press and refuses a duplicate press (offscreen FreeCAD).

The panel used to start a fresh request on every click, so hammering a tool button ran the same action
many times over. This drives the real button offscreen and checks that one press is one action, that the
button shows it was pressed, and that a spammed Auto-constrain looks for shared sizes only once.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_structural_freecad import ROOT, runtime

pytestmark = pytest.mark.skipif(os.environ.get("CADSWARM_TEST_PANEL_GUI") != "1",
                                reason="Set CADSWARM_TEST_PANEL_GUI=1 for offscreen Qt integration")


def test_a_press_is_one_action_and_shows_it(tmp_path):
    python, env = runtime()
    env["QT_QPA_PLATFORM"] = "offscreen"
    profile = tmp_path / "freecad-profile"
    profile.mkdir()
    env["FREECAD_USER_HOME"] = env["FREECAD_USER_DATA"] = str(profile)
    proc = subprocess.run([python, str(ROOT / "tests" / "press_gui_check.py"), str(tmp_path)],
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    checks = json.loads((tmp_path / "press-verified.json").read_text(encoding="utf-8"))
    assert checks["three_fast_presses_run_once"]
    assert checks["lit_while_busy"] and checks["loading_glyph_while_busy"]
    assert checks["clears_when_flash_ends"] and checks["loading_glyph_removed"]
    assert checks["later_press_runs_again"]
    assert checks["held_press_runs_once"] and checks["held_button_is_lit"] and checks["held_button_loads"]
    assert checks["clears_when_request_ends"] and checks["held_button_glyph_removed"]
    assert checks["runs_again_after_finish"]
    assert checks["spam_starts_one_look"] and checks["can_run_again_after_answer"]
