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
                          capture_output=True, encoding="utf-8", errors="replace", timeout=240)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    r = json.loads(Path(tmp_path / "panel-verified.json").read_text(encoding="utf-8"))
    assert all(c["no_horizontal_overflow"] and c["scrolls_when_short"] for c in r["layout"].values()), r["layout"]
    assert r["chat_has_reply"] and r["tasks_grouped_under_request"]
    # A long chat follows the newest message; scrolled up, it keeps the place and offers a jump back down.
    assert r["chat_jump"] == {"followed": True, "jump_shown": True, "kept_place": True, "unseen_text": "↓  3 new",
                              "back_down": True}, r["chat_jump"]
    assert r["toast"] == "✓  Created task “Lid”"  # confirmations reach people who never open the console
    assert "teammate's computer" in r["away_tip"]
    # The panes the site shows are real tabs: shared sizes, the project folder with history, refusals as cards.
    assert [t.split(" ")[0] for t in r["tabs"]] == ["Team", "Tasks", "Activity", "Sizes", "Files", "Library",
                                                     "Remote", "Sponsors", "Tools"], r["tabs"]
    assert r["sizes"] == [["Pcb l", "65 mm"], ["Wall", "3 mm"]], r["sizes"]
    # Values edit in place, like the site's size fields; files show their size like the site's rows.
    assert r["sizes_editable"] is True, r["sizes_editable"]
    assert r["sizes_inline"] == [["pcb_l", "70 mm"]], r["sizes_inline"]
    assert sorted(r["files"]) == ["bom.csv", "notes/plan.md"], r["files"]
    assert r["file_sizes"] == ["1.8 KB", "640 B"], r["file_sizes"]
    assert r["history"][0][0] == "2 ✓" and r["history"][0][3] == "current version", r["history"]
    assert r["history"][1][0] == "1" and r["history"][1][3] == "replaced", r["history"]
    assert r["refusal_card"] == {"holder": True, "nothing_changed": True}, r["refusal_card"]
    assert r["refusal_parse"] == ["Refused · held by ann-claude", "Nothing changed. Ask them instead.",
                                  "ann-claude"], r["refusal_parse"]
    assert "Round 2 asks for changes" in r["review"] and "Lid clip is 0.4 mm too short." in r["review"]
    assert "256 x 256 x 256" in r["review"]
    # The Sponsors tab is a first-class tab, built only from the snapshot this refresh already read.
    assert "Sponsors" in r["sponsors_tab"]
    assert "SpacetimeDB" in r["sponsors"] and "Factory.ai" in r["sponsors"] and "Elastic" in r["sponsors"]
    assert "team-db" in r["sponsors"] and "checked " in r["sponsors"]
    # The Remote tab hands a phone one code: its link opens the public site, and the QR is that link.
    assert r["remote"]["link"].startswith("https://cadswarm.design/app/#t="), r["remote"]
    assert r["remote"]["no_horizontal_overflow"], r["remote"]
    assert r["remote"]["qr_pixmap"] == (r["remote"]["qr_modules"] > 0), r["remote"]
    assert "One code works once" in r["remote"]["note"], r["remote"]
    # The banner's Invite button copies the code a teammate pastes into FreeCAD (it does not open the QR).
    assert r["invite_button"] == {"copied_code": True, "code_in_dialog": True, "stayed_on_team_tab": True}, r["invite_button"]
    assert r["helpers_listed"] == ["ann-claude", "bob-codex", "dee-droid"]  # people aren't in the helper list
    assert "carol (you)" in r["people"]
    # Default names stay; the Task column says what each helper is on (or what's waiting for it).
    assert r["helper_header"] == ["Helper", "Task", "Status"]
    rows = {name.replace("🤖 ", ""): (task, status) for name, task, status in r["helper_rows"]}
    assert rows["ann-claude"] == ("Base with port cutouts", "Working")
    assert rows["bob-codex"] == ("next: Lid", "Ready")
    assert rows["dee-droid"] == ("no task", "away")
    # @ offers every helper plus @team and @all; @b narrows it; picking one fills in "@name ".
    assert r["offered_all"][:3] == ["ann-claude", "bob-codex", "dee-droid"] and {"team", "all"} <= set(r["offered_all"])
    assert r["offered_b"] == ["bob-codex"] and r["filled"] == "@bob-codex "
    # "@bob-codex ..." is a task for bob-codex plus the message that wakes it; "@team ..." is a whole-team request.
    (task_path, task), (msg_path, msg), (brief_path, brief) = r["sent"]
    assert task_path == "/tasks" and task["assignee"] == "bob-codex" and task["title"] == "make the lid 20 mm tall"
    assert msg_path == "/messages" and msg["to"] == "bob-codex" and msg["kind"] == "command" and msg["task_id"] == "t_new"
    assert brief_path == "/briefs" and brief["text"] == "a stand for a 7 mm phone"
    # Suggestions sit on the Team tab with Accept/Dismiss; answering one sends it and takes it off the card.
    assert r["suggestions_shown"] and r["suggestions_cleared"]
    assert r["answered"] == [["/settings/suggestions/7", {"accept": True}],
                             ["/style/resolve", {"rule_id": 3, "decision": "dismiss"}]]
