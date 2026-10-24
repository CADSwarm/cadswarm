"""Builds the real CadSwarm Team panel in an offscreen FreeCAD, feeds it a team, and checks the layout.

    python panel_gui_check.py <out dir>

Writes panel-<w>x<h>.png screenshots and panel-verified.json. Run by test_panel_gui.py.
"""
import json
import sys
import time
from pathlib import Path

import FreeCADGui as Gui
Gui.showMainWindow()
from PySide import QtCore, QtGui  # noqa: E402

from cadswarm.gui import panel  # noqa: E402

out = Path(sys.argv[1])
now = time.time()


def member(i, name, kind="agent", client="claude", status="active", doing="", task=None, locks=()):
    return {"id": i, "name": name, "kind": kind, "client": client, "role": "member", "status": status,
            "doing": doing, "last_seen": now, "locks": list(locks), "task": task, "idle_s": 5}


snap = {
    "model": {"objects": [{"name": "Base", "label": "Base", "bbox": [0, 0, 0, 1, 1, 1], "locked_by": "ann-claude"}],
              "params": {"wall": 3.0, "pcb_l": 65.0},
              "participants": [member(1, "carol", kind="human"),
                               member(2, "ann-claude", task={"id": "t_1", "title": "Base with port cutouts"},
                                      locks=["Base"]),
                               member(3, "bob-codex", client="codex", doing="listening for instructions"),
                               member(4, "dee-droid", client="droid", status="stale")]},
    "tasks": [{"id": "t_1", "title": "Base with port cutouts for the USB-C and HDMI connectors", "status": "claimed",
               "owner_name": "ann-claude", "assignee": "ann-claude", "brief": "b_1", "detail": ""},
              {"id": "t_2", "title": "Lid", "status": "open", "owner_name": None, "assignee": "bob-codex", "brief": "b_1",
               "detail": ""},
              {"id": "t_3", "title": "Check strength of the stand", "status": "done", "owner_name": "bob-codex",
               "assignee": "bob-codex", "brief": None, "detail": ""}],
    "commands": [{"id": "m_1", "state": "done", "by_helper": {"ann-claude": "done"}, "to": "ann-claude",
                  "body": "make the walls 3 mm", "at": now - 300, "task_id": None}],
    "briefs": [{"id": "b_1", "text": "An enclosure for a Raspberry Pi 5", "status": "building", "by": "carol",
                "at": now - 600, "tasks": 2, "done": 0}],
    "messages": [{"id": "m_1", "from": "carol", "to": "ann-claude", "kind": "steer", "body": "make the walls 3 mm",
                  "at": now - 300},
                 {"id": "m_2", "from": "ann-claude", "to": "carol", "kind": "chat",
                  "body": "On it: I'll thicken the walls to 3 mm and keep the port cutouts where they are.",
                  "at": now - 290},
                 {"id": "m_3", "from": "ann-claude", "to": "carol", "kind": "chat",
                  "body": "Done. The base walls are 3 mm now; the lid still clears the posts by 0.4 mm.",
                  "at": now - 120}],
}


class FakeClient:
    cfg = {"db": "team-db"}
    hub = "http://x"


s = panel.SESSION
s.client, s.ident, s.state = FakeClient(), {"name": "carol"}, "connected"
s.mirror = lambda: type("Doc", (), {"Objects": []})()
panel.start_local_helpers = lambda session, restart=False: []
p = panel.TeamPanel(s)
s.panel = p
s.apply_snapshot(snap)
s.apply_snapshot(snap)  # an unchanged refresh must not rebuild anything

checks = {}
for w, h in ((340, 640), (520, 900)):
    p.resize(w, h)
    p.show()
    for _ in range(5):
        QtGui.QApplication.processEvents()
    scroll = p.widget()
    inner = scroll.widget()
    checks[f"{w}x{h}"] = {
        "no_horizontal_overflow": inner.width() <= scroll.viewport().width() + 1,
        "scrolls_when_short": scroll.verticalScrollBar().maximum() > 0 if h < 800 else True,
    }
    p.grab().save(str(out / f"panel-{w}x{h}.png"))
    p.tasks_button.setChecked(True)
    QtGui.QApplication.processEvents()
    p.grab().save(str(out / f"panel-{w}x{h}-tasks.png"))
    p.tasks_button.setChecked(False)

wide = sorted((w.minimumSizeHint().width(), type(w).__name__, w.objectName(), getattr(w, "text", lambda: "")()[:30],
               type(w.parent()).__name__, w.parent().objectName() if w.parent() else "")
              for w in p.widget().widget().findChildren(QtGui.QWidget)
              if w.minimumSizeHint().width() > 200)
(out / "panel-wide.json").write_text(json.dumps(wide, ensure_ascii=False, indent=1), encoding="utf-8")

# Docked in the main window, the panel follows the window when it is resized.
p.hide()
mw = Gui.getMainWindow()
mw.resize(1600, 1000)
mw.show()
panel.SESSION.panel = None
panel.show_panel = panel.show_panel  # noqa: PLW0127  (the real function, as the workbench calls it)
QtGui.QApplication.processEvents()
docked = panel.TeamPanel(s)
s.panel = docked
mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, docked)
mw.resizeDocks([docked], [480], QtCore.Qt.Horizontal)
docked.window_width = mw.width()
docked.share = 480 / 1600
for _ in range(5):
    QtGui.QApplication.processEvents()
wide_w = docked.width()
mw.resize(1000, 700)
for _ in range(10):
    QtGui.QApplication.processEvents()
narrow_w = docked.width()
mw.grab().save(str(out / "window-1000x700.png"))
s.panel = p

# Typing @li offers the helpers whose names start that way.
p.msg.setText("@")
p.msg.setCursorPosition(1)
p.maybe_complete()
offered_all = [p.completer.completionModel().index(i, 0).data() for i in range(p.completer.completionCount())]
p.msg.setText("@b")
p.msg.setCursorPosition(2)
p.maybe_complete()
offered_b = [p.completer.completionModel().index(i, 0).data() for i in range(p.completer.completionCount())]
p.completer.popup().hide()
p.insert_mention("bob-codex")
filled = p.msg.text()
p.completed_at = 0

# "@bob-codex ..." gives bob-codex a task and wakes it; people can't be handed tasks.
sent = []


class Recorder:
    def post(self, path, body=None):
        sent.append((path, body))
        return {"id": "t_new"} if path == "/tasks" else {}


s.ensure_connected = lambda purpose: True
s.run_async = lambda work, done=None, purpose="", failed=None: work(Recorder())
s.act_async = lambda method, path, body=None, done=None, purpose="", failed=None: sent.append((path, body))
p.msg.setText("@bob-codex make the lid 20 mm tall")
p.send()
p.msg.setText("@team a stand for a 7 mm phone")
p.send()

# The chat shows the instruction with its status and the helper's replies.
chat = p.chat.toPlainText()
brief = p.task_list.topLevelItem(0)
result = {
    "layout": checks,
    "follows_window": {"at_1600": wide_w, "at_1000": narrow_w},
    "chat_has_reply": "lid still clears the posts" in chat and "✓ done" in chat,
    "offered_all": offered_all,
    "offered_b": offered_b,
    "filled": filled,
    "sent": sent,
    "helpers_listed": [p.team.topLevelItem(i).data(0, QtCore.Qt.UserRole) for i in range(p.team.topLevelItemCount())],
    "helper_rows": [[p.team.topLevelItem(i).text(c).lstrip("◐◓◑◒ ") for c in range(3)]
                    for i in range(p.team.topLevelItemCount())],
    "helper_header": [p.team.headerItem().text(c) for c in range(3)],
    "people": p.people.text(),
    "tasks_grouped_under_request": brief.text(0).startswith("🧠") and brief.childCount() == 2,
    "away_tip": p.team_tip.text(),
}
(out / "panel-verified.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
QtCore.QTimer.singleShot(0, QtGui.QApplication.quit)
sys.exit(0)
