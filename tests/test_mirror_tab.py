"""The live model stays one tab and never steals the active tab from the person's own document."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_structural_freecad import ROOT, runtime


@pytest.mark.skipif(os.environ.get("CADSWARM_TEST_PANEL_GUI") != "1", reason="Set CADSWARM_TEST_PANEL_GUI=1 for offscreen Qt integration")
def test_a_suffixed_live_model_is_reused_and_never_steals_the_tab(tmp_path):
    python, env = runtime()
    env["QT_QPA_PLATFORM"] = "offscreen"
    profile = tmp_path / "freecad-profile"
    profile.mkdir()
    env["FREECAD_USER_HOME"] = env["FREECAD_USER_DATA"] = str(profile)
    proc = subprocess.run([python, str(ROOT / "tests" / "mirror_tab_check.py"), str(tmp_path)], env=env,
                          capture_output=True, encoding="utf-8", errors="replace", timeout=240)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    r = json.loads(Path(tmp_path / "mirror-tab.json").read_text(encoding="utf-8"))
    assert r["reused_suffixed_mirror"], r
    assert r["one_mirror_tab"] and r["still_one_mirror_tab"], r
    assert r["label"] == "Racing Drone (live, read-only)", r
    assert r["kept_its_parts"] == ["Hub"], r  # the existing mirror's parts are not thrown away
    assert r["active_after_refresh"] == "MyDrone", r  # a refresh leaves the person where they were
    assert r["active_after_show"] == "CadSwarmLive1", r  # asking for the model does bring it forward
    # A dead team says what happened and what to do, instead of the raw database error.
    assert "isn't on this computer's team server any more" in r["missing_db"], r
    assert "paused on SpacetimeDB" in r["paused_db"], r
