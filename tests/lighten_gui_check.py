"""Offscreen FreeCAD GUI check of Lighten part, in local mode (no team): a person's whole flow.

Run by test_lighten_gui.py with FreeCAD's Python (QT_QPA_PLATFORM=offscreen): make a part, select it, open
the dialog, fill in the four steps, check the settings, run, wait, read the result, preview it and add it
to the document. The job is the real runner in its own FreeCAD process."""
import json
import sys
import time
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui

Gui.showMainWindow()
from PySide import QtGui  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
exec((ROOT / "InitGui.py").read_text(encoding="utf-8"), {"__name__": "__main__"}, {})
from cadswarm.gui import lighten_ui, panel  # noqa: E402

out = Path(sys.argv[1])
panel.hint = lambda text: print("hint:", text)
panel.say = lambda text: print("say:", text)
QtGui.QMessageBox.warning = staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError(a[2])))
lighten_ui.register_command()
assert "CadSwarm_Lighten" in Gui.listCommands()

doc = App.newDocument("Bench")
part = doc.addObject("Part::Box", "Bracket")
part.Length, part.Width, part.Height = 36, 12, 12
doc.recompute()
Gui.Selection.clearSelection()
Gui.Selection.addSelection(doc.Name, "Bracket")
dlg = lighten_ui.show_lighten()
assert dlg is not None and not dlg.team

# Step 1: part. Choosing a powder process proposes two opposite openings for drainage.
dlg.material.setCurrentIndex(dlg.material.findData("aluminium_6061_t6"))
dlg.process.setCurrentIndex(1)
assert dlg.open_boxes["+x"].isChecked() and dlg.open_boxes["-x"].isChecked()
dlg.process.setCurrentIndex(0)  # back to filament: closed skin
# Step 2: held at xmin, 300 N down on xmax.
dlg.fixed.setText("xmin")
dlg.loaded.setText("xmax")
for e, v in zip(dlg.force, ("0", "0", "-300")):
    e.setText(v)
# Step 3: lattice.
dlg.cell.setCurrentIndex(dlg.cell.findData("gyroid"))
dlg.cell_size.setText("5")
dlg.skin.setText("1.0")
dlg.voxel.setText("0.45")
dlg.target.setValue(70)
dlg.check()
assert "voxels" in dlg.check_out.text(), dlg.check_out.text()
# Step 4: run and wait.
dlg.tabs.setCurrentIndex(3)
dlg.run()
t = time.time()
while dlg.timer.isActive() and time.time() - t < 600:
    QtGui.QApplication.processEvents()
    time.sleep(0.2)
assert not dlg.timer.isActive(), "the job did not finish"
s = dlg.last
assert s["status"] == "done", s
text = dlg.summary.text()
assert "lighter" in text and "not a certification" in text
if s["verdict"] == "passed":
    assert "Stiffness kept" in text and s["verify"]["stiffness_retained"] >= 0.7
assert App.listDocuments().get(lighten_ui.PREVIEW_DOC), "no preview document"
prev = App.getDocument(lighten_ui.PREVIEW_DOC)
assert prev.getObject("Lattice").Mesh.CountFacets > 0
dlg.toggle_section(True)
dlg.toggle_section(False)
if dlg.apply_btn.isEnabled():
    dlg.apply()
    added = doc.getObject("Bracket_lattice")
    assert added is not None and added.TypeId == "Mesh::Feature" and added.Mesh.isSolid()
    assert doc.getObject("Bracket").Shape.Volume == 36 * 12 * 12  # the original is unchanged
    assert not doc.getObject("Bracket").ViewObject.Visibility  # and hidden, so it doesn't z-fight the lattice
dlg.reject()
assert not App.listDocuments().get(lighten_ui.PREVIEW_DOC)
(out / "lighten-gui-verified.json").write_text(json.dumps({"ok": True, "verdict": s["verdict"],
                                                           "saving": s["weight_saving_pct"]}))
print("lighten gui ok", s["verdict"], s["weight_saving_pct"], flush=True)
# Closing the main window would ask to save the changed document, a prompt nobody answers offscreen.
# Close the documents (no prompt) and let Qt quit, like panel_gui_check.py.
for name in list(App.listDocuments()):
    App.closeDocument(name)
from PySide import QtCore  # noqa: E402
QtCore.QTimer.singleShot(0, QtGui.QApplication.quit)
sys.exit(0)
