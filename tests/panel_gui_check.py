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
# FreeCAD puts its Mod folders ahead of PYTHONPATH when it starts, so an installed CadSwarm
# would shadow this checkout. Test the repository, not whatever happens to be installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _name in [n for n in sys.modules if n == "cadswarm" or n.startswith("cadswarm.")]:
    del sys.modules[_name]
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
    "reviews": [{"id": "r_1", "brief": "b_1", "round": 2, "passed": False, "at": now - 60,
                 "findings": ["Lid clip is 0.4 mm too short.", "USB-C cutout sits 0.6 mm low."]}],
    "workspace": {"bed": [256.0, 256.0, 256.0], "bed_x_mm": 256.0, "bed_y_mm": 256.0, "bed_z_mm": 256.0},
    "messages": [{"id": "m_1", "from": "carol", "to": "ann-claude", "kind": "steer", "body": "make the walls 3 mm",
                  "at": now - 300},
                 {"id": "m_2", "from": "ann-claude", "to": "carol", "kind": "chat",
                  "body": "On it: I'll thicken the walls to 3 mm and keep the port cutouts where they are.",
                  "at": now - 290},
                 {"id": "m_3", "from": "ann-claude", "to": "carol", "kind": "chat",
                  "body": "Done. The base walls are 3 mm now; the lid still clears the posts by 0.4 mm.",
                  "at": now - 120},
                 {"id": "m_4", "from": "bob-codex", "to": "carol", "kind": "chat",
                  "body": "Refused · held by ann-claude\nNothing changed. Ask them instead.",
                  "at": now - 60}],
}


class FakeClient:
    cfg = {"db": "team-db"}
    hub = "http://x"

    def get(self, path):
        if path == "/project/files":
            return {"files": [
                {"path": "notes/plan.md", "version": 2, "size": 1840, "author": "ann-claude",
                 "updated_at": now - 600},
                {"path": "bom.csv", "version": 1, "size": 640, "author": "bob-codex",
                 "updated_at": now - 2400}],
                "folders": ["notes"]}
        raise panel.HubRequestError(404, {"code": "not_found", "error": path})

    def post(self, path, body=None):
        if path == "/project/files/history":
            return {"path": body["path"], "current": {"path": body["path"], "version": 2, "size": 1840,
                                                      "author": "ann-claude", "updated_at": now - 600},
                    "older_versions": [{"version": 1, "size": 1500, "author": "bob-codex",
                                        "saved_at": now - 9000, "reason": "replaced"}]}
        raise panel.HubRequestError(404, {"code": "not_found", "error": path})
    def create_invite(self, kind, role, ttl, project):
        from cadswarm import invite, ticket
        return invite.make_invite(ticket.decode(ticket.encode({
            "v": 1, "uri": "https://maincloud.spacetimedb.com", "db": "team-db", "token": "ab" * 32,
            "kind": kind, "role": role, "host_addr": ""})), project)


s = panel.SESSION
s.client, s.ident, s.state = FakeClient(), {"name": "carol"}, "connected"
s.mirror = lambda: type("Doc", (), {"Objects": []})()
panel.start_local_helpers = lambda session, restart=False: []
p = panel.TeamPanel(s)
s.panel = p
s.apply_snapshot(snap)
s.apply_snapshot(snap)  # an unchanged refresh must not rebuild anything
# What AI helpers suggested waits on the Team tab, in plain words, with the yes/no right there.
p.fetching = True  # FakeClient can't be read; the suggestions arrive as if read from the team
p.show_suggestions({
    "setting": [panel.setting_suggestion({"id": 7, "setting": "tol_clearance", "label": "Clearance fit", "value": 0.3,
                                          "current": 0.2, "reason": "PLA prints tight on this printer",
                                          "by": "ann-claude", "component": None},
                                         {"kind": "number", "unit": "mm"})],
    "style": [panel.style_suggestion({"id": 3, "statement": "Lids are held by four M3 heat-set inserts",
                                      "status": "candidate", "type": "modelling_practice", "source": "learned",
                                      "evidence": {"n": 4, "people": 2}, "confidence": 0.62})]})

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
    for name, page in (("tasks", p.tasks_page), ("tools", p.tools_page)):
        p.show_tab(page)
        QtGui.QApplication.processEvents()
        p.grab().save(str(out / f"panel-{w}x{h}-{name}.png"))
    p.show_tab(p.team_page)

# The three panes the site shows: shared sizes, the project folder with history, and refusals as cards.
p.resize(520, 900)
p.show()
for _ in range(5):
    QtGui.QApplication.processEvents()
tabs = [p.tabs.tabText(i) for i in range(p.tabs.count())]
p.show_tab(p.sizes_page)
QtGui.QApplication.processEvents()
p.grab().save(str(out / "panel-520x900-sizes.png"))
sizes = [[p.sizes_page.rows.topLevelItem(i).text(c) for c in range(2)]
         for i in range(p.sizes_page.rows.topLevelItemCount())]
size_item = p.sizes_page.rows.topLevelItem(0)
sizes_editable = bool(p.sizes_page.rows.editTriggers() & QtGui.QAbstractItemView.DoubleClicked) \
    and bool(size_item.flags() & QtCore.Qt.ItemIsEditable)
# Typing in the Value cell must go through the same set_param path as the dialog and the site.
inline = []
real_change = panel.panes.change_param
panel.panes.change_param = lambda session, alias, value, done=None, failed=None: inline.append((alias, value))
try:
    size_item.setText(1, "70 mm")
    QtGui.QApplication.processEvents()
finally:
    panel.panes.change_param = real_change
sizes_inline = inline
p.show_tab(p.files_page)
p.files_page.reload(force=True)
QtGui.QApplication.processEvents()
p.grab().save(str(out / "panel-520x900-files.png"))
file_item = p.files_page.find_path("notes/plan.md")
p.files_page.tree.setCurrentItem(file_item)
for _ in range(5):
    QtGui.QApplication.processEvents()
files = [item.data(0, QtCore.Qt.UserRole) for item in
         [p.files_page.find_path(f["path"]) for f in p.files_page.listing["files"]]]
file_sizes = [p.files_page.find_path(f["path"]).text(2) for f in p.files_page.listing["files"]]
history = [[p.files_page.history.topLevelItem(i).text(c) for c in range(4)]
           for i in range(p.files_page.history.topLevelItemCount())]
chat_text = p.chat.toPlainText()
p.show_tab(p.team_page)

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
suggestions_shown = p.suggest_box.isVisibleTo(p) and p.suggest_lay.count() == 3  # heading + two cards
p.answer(p.suggestions["setting"][0], True)
p.answer(p.suggestions["style"][0], False)

# The chat shows the instruction with its status and the helper's replies.
chat = p.chat.toPlainText()
brief = p.task_list.topLevelItem(0)

# A long chat follows the newest message; scrolling up stops that, keeps the place as messages arrive and shows
# a "new" button that jumps back down.
p.show()
p.show_tab(p.team_page)
long_chat = [{"id": f"m_l{i}", "from": "ann-claude", "to": "carol", "kind": "chat",
              "body": f"Progress note {i}: the base walls and the lid lip are still lined up.", "at": now + i}
             for i in range(40)]


def chat_snapshot(messages):
    s.apply_snapshot({**snap, "messages": snap["messages"] + messages})
    for _ in range(10):
        QtGui.QApplication.processEvents()


chat_snapshot(long_chat)
bar = p.chat.verticalScrollBar()
followed = bar.maximum() > 0 and p.chat_at_bottom() and not p.jump.isVisible()
bar.triggerAction(QtGui.QAbstractSlider.SliderToMinimum)  # what a wheel or drag does
for _ in range(5):
    QtGui.QApplication.processEvents()
jump_shown = p.jump.isVisible() and not p.chat_follow
chat_snapshot(long_chat + [{**long_chat[0], "id": f"m_n{i}", "at": now + 100 + i} for i in range(3)])
kept_place, unseen_text = bar.value() == 0, p.jump.text()
p.jump.click()
for _ in range(5):
    QtGui.QApplication.processEvents()
back_down = p.chat_at_bottom() and p.chat_follow and not p.jump.isVisible()

# Confirmations show in the panel, not only in FreeCAD's console.
panel.say("Created task “Lid”")
QtGui.QApplication.processEvents()
toast = p.toast.text() if p.toast.isVisible() else None
p.grab().save(str(out / "panel-toast.png"))
# The Remote tab is where a phone joins from: one code, its link, and the QR that link draws.
p.resize(340, 640)
p.show_remote()
for _ in range(5):
    QtGui.QApplication.processEvents()
remote = {
    "link": p.remote_link.text(),
    "qr_pixmap": not p.remote_qr.pixmap().isNull(),
    "qr_modules": len(panel.qr.rows(p.remote_url)),
    "note": p.remote_note.text(),
    "no_horizontal_overflow": p.widget().widget().width() <= p.widget().viewport().width() + 1,
}
p.grab().save(str(out / "panel-340x640-remote.png"))
p.show_tab(p.team_page)
# The banner's Invite button hands a teammate the code they paste into FreeCAD, not the phone's QR.
shown = []
info = QtGui.QMessageBox.information
QtGui.QMessageBox.information = staticmethod(lambda parent, title, text, *a, **k: shown.append(text))
QtGui.QApplication.clipboard().clear()
p.invite_button.click()
QtGui.QApplication.processEvents()
QtGui.QMessageBox.information = info
copied = QtGui.QApplication.clipboard().text()
invite_button = {"copied_code": copied.startswith("cadswarm-2:"), "code_in_dialog": bool(copied) and copied in "".join(shown),
                 "stayed_on_team_tab": p.tabs.currentWidget() is p.team_page}
# Run a simulation: a person fills the form and gets run_fea's request, no helper in the loop.
sim = panel.fea_ui.RunSimulationDialog(s)
sim.fixed.setText("zmin, cyl r=2")
sim.newtons.setText("80")
body = sim.request()
sim.analysis.setCurrentIndex(sim.analysis.findData("modal"))
run_simulation = {"parts": sim.part.count(), "target": body["target"], "fixed": body["fixed"],
                  "newtons": body["loads"][0]["newtons"], "material": body["material"],
                  "modal_hides_load": not sim.newtons.isEnabled()}
sim.close()
# A report that needs changes offers to send it to a helper, who reads it with fea_report and fixes the part.
report = {"id": "fea_1a2b3c4d", "title": "Linear static of Base", "analysis_name": "Linear static", "by": "carol",
          "verdict": "fail", "headline": "safety factor 0.8", "inputs": {"placeholders": []}, "files": {},
          "model": {"target": ["Base"]}}
real_post, sent_fix = s.client.post, []
s.client.post = lambda path, body=None: {"text": json.dumps(report) if body["path"].endswith(".json") else "<p>x</p>"}
dlg = panel.FeaReportDialog("fea/x")
s.client.post = real_post
pick, act = QtGui.QInputDialog.getItem, s.act_async
QtGui.QInputDialog.getItem = staticmethod(lambda parent, title, label, items, *a, **k: (items[0], True))
s.act_async = lambda method, path, body=None, **k: sent_fix.append((path, body))
dlg.fix_button.click()
QtGui.QInputDialog.getItem, s.act_async = pick, act
dlg.close()
fix_request = {"to": sent_fix[0][1]["to"], "kind": sent_fix[0][1]["kind"],
               "reads_report": "fea_report('fea_1a2b3c4d')" in sent_fix[0][1]["body"]} if sent_fix else None
result = {
    "layout": checks,
    "remote": remote,
    "invite_button": invite_button,
    "run_simulation": run_simulation,
    "fix_request": fix_request,
    "follows_window": {"at_1600": wide_w, "at_1000": narrow_w},
    "chat_has_reply": "lid still clears the posts" in chat and "✓ done" in chat,
    "offered_all": offered_all,
    "offered_b": offered_b,
    "filled": filled,
    "sent": sent[:3],
    "answered": sent[3:],
    "suggestions_shown": suggestions_shown,
    "suggestions_cleared": not p.suggest_box.isVisibleTo(p),
    "helpers_listed": [p.team.topLevelItem(i).data(0, QtCore.Qt.UserRole) for i in range(p.team.topLevelItemCount())],
    "helper_rows": [[p.team.topLevelItem(i).text(c).lstrip("◐◓◑◒ ") for c in range(3)]
                    for i in range(p.team.topLevelItemCount())],
    "helper_header": [p.team.headerItem().text(c) for c in range(3)],
    "people": p.people.text(),
    "tasks_grouped_under_request": brief.text(0).startswith("🧠") and brief.childCount() == 2,
    "away_tip": p.team_tip.text(),
    "review": p.review_box.text(),
    "sponsors_tab": [p.tabs.tabText(i) for i in range(p.tabs.count())],
    "sponsors": p.sponsors_box.text(),
    "chat_jump": {"followed": followed, "jump_shown": jump_shown, "kept_place": kept_place,
                  "unseen_text": unseen_text, "back_down": back_down},
    "toast": toast,
    "tabs": tabs,
    "sizes": sizes,
    "sizes_editable": sizes_editable,
    "sizes_inline": sizes_inline,
    "files": files,
    "file_sizes": file_sizes,
    "history": history,
    "refusal_card": {"holder": "Refused · held by ann-claude" in chat_text,
                     "nothing_changed": "Nothing changed" in chat_text},
    # The team writes "Already locked: Base by ann-claude (active)"; the card names just the holder.
    "refusal_parse": list(panel.panes.parse_refusal(
        "Already locked: Base by ann-claude (active). Message the holder")),
}
(out / "panel-verified.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
QtCore.QTimer.singleShot(0, QtGui.QApplication.quit)
sys.exit(0)
