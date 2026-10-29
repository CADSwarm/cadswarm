"""Builds the real update window in an offscreen FreeCAD and walks the update sequence with fakes.

    python updates_gui_check.py <out dir>

Writes update-verified.json. Run by test_updates_gui.py. Nothing is installed, stopped or
restarted: every step that would do that is replaced with a note in `order`, so this checks the
sequence (save -> leave -> helpers -> server -> install -> close -> restart) and the window.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui

Gui.showMainWindow()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _name in [n for n in sys.modules if n == "cadswarm" or n.startswith("cadswarm.")]:
    del sys.modules[_name]
from PySide import QtCore  # noqa: E402

from cadswarm import update  # noqa: E402
from cadswarm.gui import panel, updates  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
RELEASE = {
    "tag": "v0.1.18", "version": "0.1.18",
    "url": "https://github.com/CADSwarm/cadswarm/releases/tag/v0.1.18",
    "notes": "## What's Changed\n* a new thing\n* a fixed thing\n",
    "published": "2026-10-28T01:33:24Z",
}


def wait(seconds=0.12):
    for _ in range(max(1, int(seconds / 0.02))):
        QtCore.QCoreApplication.processEvents()
        time.sleep(0.02)


# Saving: a document with a file is saved in place, one that was never saved keeps a recovery copy
# (and neither can block the restart with a prompt).
updates.app_dir = lambda: str(out / "appdata")
app_dir = out / "appdata"
saved = App.newDocument("UpdateSaveCheck")
saved.addObject("Part::Box", "Box")
saved.recompute()
saved_path = out / "saved.FCStd"
saved.saveAs(str(saved_path))
before = saved_path.stat().st_mtime_ns
saved.getObject("Box").Length = 25
saved.recompute()
unsaved = App.newDocument("UpdateUnsavedCheck")
unsaved.addObject("Part::Box", "Box")
unsaved.recompute()
reopen = updates.save_documents()
recovery = [p for p in reopen if "update-recovery" in p]
saving = {"reopen_has_saved": str(saved_path) in reopen, "rewritten": saved_path.stat().st_mtime_ns > before,
          "recovery_copies": len(recovery), "recovery_files_exist": all(Path(p).is_file() for p in recovery),
          "recovery_in_app_folder": all(str(app_dir) in p for p in recovery)}
App.closeDocument(saved.Name)
App.closeDocument(unsaved.Name)

facts = {}
facts["saving"] = saving
order = []
dialog = updates.UpdateDialog(RELEASE)
dialog.show()
wait(0.3)
facts["window"] = {
    "size": [dialog.width(), dialog.height()],
    "headline": dialog.headline.text(),
    "lede": dialog.lede.text(),
    "buttons": [dialog.now.text(), dialog.later.text(), dialog.whats_new.text()],
    "notes_visible": dialog.notes.isVisible(),
    "on_top": bool(dialog.windowFlags() & QtCore.Qt.WindowStaysOnTopHint),
}
dialog.grab().save(str(out / "update-window.png"))

# A teammate is online and this computer hosts: the window asks once before stopping the team.
class FakeSession:
    connected = True
    ident = {"name": "carol"}
    model = {"participants": [{"name": "carol", "kind": "human", "status": "active"},
                              {"name": "dan", "kind": "human", "status": "active"},
                              {"name": "bob-codex", "kind": "agent", "status": "active"}]}

real_session = panel.SESSION
panel.SESSION = FakeSession()
facts["others"] = [p["name"] for p in updates._others_on_team()]
panel.SESSION = real_session

# Walk the sequence with fakes.
stage = Path(tempfile.mkdtemp(prefix="update-check-")) / "app"
stage.mkdir(parents=True)
(Path(tempfile.gettempdir()) / "reopen.FCStd").touch()


def note(step, value=None):
    order.append(step)
    return value


updates.save_documents = lambda: note("save", [str(Path(tempfile.gettempdir()) / "reopen.FCStd")])
updates.close_documents = lambda: note("close")
panel.SESSION.disconnect = lambda *a, **k: note("leave")
panel.local_helper_folders = lambda: []
panel.host_dir_for = lambda db: "C:/host"
updates.stop_helpers = lambda folders, timeout=10: note("helpers")
updates.stop_hosting = lambda pids, timeout=10: note("server")
update.install = lambda app, root=update.ROOT, log=None: note("install")
remembered = {}


def remember(paths):
    remembered["paths"] = list(paths)
    note("remember")


update.remember_reopen = remember
update.spawn_restart = lambda *a, **k: note("restart")
updates.close_documents = lambda: note("close")
updates.UpdateDialog.quit_freecad = lambda self: note("quit")

dialog.staged(str(stage))
for _ in range(150):  # the stop/install worker posts back; drain like the window's timer does
    dialog.drain()
    wait(0.02)
dialog.grab().save(str(out / "update-installed.png"))

facts["order"] = order
facts["remembered"] = remembered.get("paths")
facts["reopen"] = str(Path(tempfile.gettempdir()) / "reopen.FCStd")

# A failure before stopping leaves the app alone, offers Try again, and says so in red.
failed = updates.UpdateDialog(RELEASE)
failed.show()
failed.set_busy(True)
failed.failed("Couldn't download the new version. Check your internet connection and try again.")
wait(0.2)
facts["failed"] = {"text": failed.status.text(), "button": failed.now.text(),
                   "object": failed.status.objectName(), "busy": failed.busy}
failed.grab().save(str(out / "update-failed.png"))

# "Later" postpones this release for a day (and a different release still shows).
panel.prefs().SetString("UpdateLaterTag", "")
panel.prefs().SetString("UpdateLaterAt", "")
failed.set_busy(False)
failed.postpone()
facts["later"] = {"postponed": updates._postponed(RELEASE),
                  "newer_still_shows": not updates._postponed({**RELEASE, "tag": "v0.1.19"})}
panel.prefs().SetString("UpdateLaterTag", "")
panel.prefs().SetString("UpdateLaterAt", "")

facts["short_notes"] = len(updates._short_notes("line\n" * 400)) <= 901

# A real check runs on a thread: what it finds must still reach the window (a QTimer made on that
# thread would never fire), both a newer release and a manual check's "you're up to date".
update.updates_allowed = lambda root=update.ROOT: True
hints = []
updates._hint = hints.append
updates._dialog = None
update.check_latest = lambda force=False: RELEASE
updates.check(manual=True)
for _ in range(150):
    wait(0.02)
    if updates._dialog is not None:
        break
facts["thread_check_shows_window"] = updates._dialog is not None and updates._dialog.isVisible()
if updates._dialog is not None:
    updates._dialog.hide()
update.check_latest = lambda force=False: {**RELEASE, "tag": "v0.0.1", "version": "0.0.1"}
updates.check(manual=True)
for _ in range(150):
    wait(0.02)
    if hints:
        break
facts["thread_check_says_up_to_date"] = any("newest version" in h for h in hints)
(out / "update-verified.json").write_text(json.dumps(facts, indent=2), encoding="utf-8")
print(json.dumps(facts, indent=2))
