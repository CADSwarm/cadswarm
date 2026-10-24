"""Offscreen FreeCAD GUI check of the Library tab, the Settings dialog and the part editor's precision.

Run by test_library_gui.py with FreeCAD's Python (QT_QPA_PLATFORM=offscreen). The team server is a
SpaceHub over the in-memory FakeSpace from test_space_hub.py, so every click goes through the real
routes; reducer calls are recorded instead of sent."""
import json
import sys
import types
from pathlib import Path

import FreeCADGui as Gui

Gui.showMainWindow()
from PySide import QtGui  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
exec((ROOT / "InitGui.py").read_text(encoding="utf-8"), {"__name__": "__main__"}, {})

# FreeCAD's Python has no pytest; test_space_hub only needs its fixture decorator to import.
sys.modules.setdefault("pytest", types.SimpleNamespace(fixture=lambda f=None, **k: f or (lambda g: g)))

from cadswarm.gui import library_ui, panel  # noqa: E402
from cadswarm.space_hub import SpaceHub  # noqa: E402
from test_space_hub import FakeSpace, member  # noqa: E402


def comp(id, name, **kw):
    row = {"id": id, "ws": 1, "name": name, "category": "other", "tags": [], "description": "", "source": "upload",
           "source_url": "", "license": "", "author": "carol", "format": "step", "hash": "h", "size": 10,
           "version": 1, "bbox": [0, 0, 0, 40, 20, 5], "volume": 1.0, "features_json": "{}", "std_json": "",
           "suggest_json": "[]", "uses": 0, "created_ms": 1, "updated_ms": 1}
    row.update(kw)
    return row

out = Path(sys.argv[1])
panel.register_commands()
assert "CadSwarm_Library" in Gui.listCommands() and "CadSwarm_Settings" in Gui.listCommands()

fake = FakeSpace({
    "v_me": [member(3, "carol", kind="human", fence=1)],
    "v_member": [member(3, "carol", kind="human", fence=1), member(1, "alice-claude")],
    "v_component": [comp("c_nema17_mount_aa11bb", "NEMA17 mount", category="motion", tags=["motor"],
                         features_json=json.dumps({"holes": [{"d": 3.4, "count": 4}]}))],
    "v_setting": [{"key": "1:edit_decimals", "ws": 1, "setting": "edit_decimals", "num": 3, "text": "",
                   "set_by": 3, "updated_ms": 1}],
    "v_suggestion": [{"id": "g_1", "ws": 1, "setting": "tol_press_fit", "num": 0.0, "text": "",
                      "reason": "printed 608 seats", "component": "", "by": 1, "status": "pending",
                      "created_ms": 1, "resolved_by": 0, "resolved_ms": 0}],
})
S = panel.SESSION
S.client = SpaceHub({"uri": "http://x", "db": "d", "name": "carol"}, {"participant_id": 3, "token": "t", "fence": 1},
                    space=fake)
S.ident = {"name": "carol", "participant_id": 3}
panel.confirm = lambda text: False   # never block on a question box offscreen
panel.hint = lambda text: None


def no_warnings(_parent, _title, text, *a):
    raise AssertionError(f"warning box: {text}")


QtGui.QMessageBox.warning = no_warnings  # a modal box would hang the offscreen run

# The Team panel gets a folding Library section; opening the library unfolds it.
panel.show_panel()
assert S.panel.library_button.text() == "Library" and S.panel.library.isHidden()
library_ui.show_library()
assert S.panel.library_button.isChecked() and not S.panel.library.isHidden()

tab = S.panel.library
tab.reload()
rows = [tab.tree.topLevelItem(i).text(0) for i in range(tab.tree.topLevelItemCount())]
assert rows[0] == "NEMA17 mount" and "Deep groove ball bearing" in rows, rows
tab.tree.setCurrentItem(tab.tree.topLevelItem(0))
assert "Ø3.4 mm" in tab.details.text() and tab.std_row.isHidden()
bearing = next(i for i in range(tab.tree.topLevelItemCount()) if tab.tree.topLevelItem(i).text(0) == "Deep groove ball bearing")
tab.tree.setCurrentItem(tab.tree.topLevelItem(bearing))
assert not tab.std_row.isHidden() and tab.size.currentText() == "608" and tab.length.isHidden()
assert "Ø21.95 mm" in tab.details.text(), tab.details.text()
tab.query.setText("motor")
tab.reload()
assert tab.tree.topLevelItemCount() == 1

# Placing a standard part queues insert_standard_part with the chosen size.
tab.query.setText("")
tab.reload()
screw = next(i for i in range(tab.tree.topLevelItemCount()) if tab.tree.topLevelItem(i).text(0) == "Socket head cap screw")
tab.tree.setCurrentItem(tab.tree.topLevelItem(screw))
assert tab.size.currentText() == "M3" and tab.length.currentText() == "10"
real_sql = fake.sql


def finish(query):
    for o in fake.tables.get("v_op", []):
        o.update(status="done", result_json=json.dumps({"result": {"name": "M3x10SocketHead1"},
                                                        "created": ["M3x10SocketHead1"]}))
    return real_sql(query)


fake.sql = finish
tab.place()
submits = [c for c in fake.calls if c[0] == "submit_op"]
assert submits and submits[-1][1][2] == "insert_standard_part", submits
args = json.loads(submits[-1][1][3])
assert args["size"] == "M3" and args["length"] == 10.0

# Settings: one widget per setting, the pending suggestion, and saving calls set_setting.
dlg = library_ui.SettingsDialog()
from cadswarm import settings as team_settings  # noqa: E402
assert set(dlg.widgets) == {s["key"] for s in team_settings.specs()}
assert dlg.sugg_lay.count() == 1
dlg.widgets["tol_clearance"].setValue(0.3)
dlg.save()
assert ("set_setting", (1, "tol_clearance", 0.3, "")) in fake.calls, fake.calls[-3:]
dlg.resolve("g_1", True)
assert ("resolve_suggestion", (1, "g_1", True)) in fake.calls

# The part editor's precision follows the team's edit_decimals setting.
assert library_ui.editor_precision(S) == (3, 0.1)
assert panel.describe_event({"kind": "setting_suggested", "name": "alice-claude", "setting": "tol_press_fit",
                             "value": 0, "reason": "printed seats"}).startswith("alice-claude suggests tol_press_fit")
assert "components library" in panel.describe_event({"kind": "library", "name": "carol", "added": ["Bracket"]})

(out / "library-gui-verified.json").write_text(json.dumps({"ok": True}))
print("library GUI ok", flush=True)
import os  # noqa: E402
os._exit(0)  # FreeCAD's GUI keeps the process alive after its window closes
