"""The update window in a real (offscreen) FreeCAD: it fits, asks before stopping a team, and saves
before stopping anything and restarting."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_structural_freecad import ROOT, runtime


@pytest.mark.skipif(os.environ.get("CADSWARM_TEST_PANEL_GUI") != "1",
                    reason="Set CADSWARM_TEST_PANEL_GUI=1 for offscreen Qt integration")
def test_real_update_window_and_sequence(tmp_path):
    python, env = runtime()
    env["QT_QPA_PLATFORM"] = "offscreen"
    profile = tmp_path / "freecad-profile"
    profile.mkdir()
    env["FREECAD_USER_HOME"] = env["FREECAD_USER_DATA"] = str(profile)
    proc = subprocess.run([python, str(ROOT / "tests" / "updates_gui_check.py"), str(tmp_path)],
                          env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=240)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(Path(tmp_path / "update-verified.json").read_text(encoding="utf-8"))

    # Saving works before anything stops: a real document is written and marked clean, an unsaved one
    # is kept in the app folder's recovery area (which reopens with it) instead of prompting.
    assert result["saving"] == {"reopen_has_saved": True, "rewritten": True, "recovery_copies": 1,
                                "recovery_files_exist": True, "recovery_in_app_folder": True}, result["saving"]

    window = result["window"]
    assert window["headline"] == "A new release is here"
    assert "v0.1.18" in window["lede"]
    assert window["buttons"] == ["Update now", "Later", "What's new"]
    assert window["notes_visible"] and window["on_top"]
    assert 380 <= window["size"][0] <= 600 and 250 <= window["size"][1] <= 520, window["size"]
    assert result["others"] == ["dan", "bob-codex"]  # the window asks before stopping the team

    # Everything that must happen, in this order: no update before the work is saved, no restart
    # before the new files are in place, and the documents come back.
    assert result["order"] == ["save", "leave", "helpers", "server", "install", "close", "remember",
                               "restart", "quit"], result["order"]
    assert result["remembered"] == [result["reopen"]]

    failed = result["failed"]
    assert "internet" in failed["text"] and failed["button"] == "Try again"
    assert failed["object"] == "error" and failed["busy"] is False
    assert result["later"] == {"postponed": True, "newer_still_shows": True}
    assert result["short_notes"] is True
    # The check thread hands its answer to the GUI thread (a QTimer started on that thread never fires).
    assert result["thread_check_shows_window"] is True
    assert result["thread_check_says_up_to_date"] is True
