"""Actual offscreen Qt/FreeCAD GUI acceptance and rejection smoke test."""
import json
from pathlib import Path
import sys
import time

import FreeCAD as App
import FreeCADGui as Gui
import Part
Gui.showMainWindow()
from PySide import QtCore, QtGui
from cadswarm.gui import structural

root = Path(sys.argv[1])
def mark(stage):
    with (root / "gui-stages.log").open("a", encoding="utf-8") as out:
        out.write(stage + "\n")
# Keep all UI-created reports inside the test directory, not the user's profile.
structural.app_dir = lambda: str(root)
doc = App.newDocument("GuiStructuralTest")
obj = doc.addObject("Part::Feature", "Source")
obj.Shape = Part.makeBox(48, 16, 16)
doc.recompute()
original_volume = obj.Shape.Volume


def run_dialog():
    dlg = structural.OptimizeDialog(obj)
    dlg.load_demo()
    dlg.fields["pitch_mm"].setText("4")
    dlg.fields["target_reduction"].setText("0.02")
    dlg.show()
    dlg.start()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        QtGui.QApplication.processEvents()
        if dlg.start_button.isEnabled():
            break
        time.sleep(.02)
    assert dlg.result is not None, dlg.log.toPlainText()
    assert dlg.accept_button.isEnabled()
    assert len(App.getDocument(dlg.preview_doc).Objects) == 2
    return dlg


dlg = run_dialog()
mark("preview complete")
assert len(doc.Objects) == 1  # preview never changes source document
dlg.accept_design()
mark("accept complete")
assert not dlg.accept_button.isEnabled(), dlg.log.toPlainText()
assert len(doc.Objects) == 2
assert obj.Shape.Volume == original_volume
optimized = next(o for o in doc.Objects if o.Name != "Source")
assert optimized.Shape.isValid() and optimized.Shape.Volume < original_volume
assert json.loads(optimized.StructuralReport)["status"] == "improved"
dlg.reject()
mark("accepted dialog closed")
assert dlg.preview_doc is None

rejected = run_dialog()
mark("second preview complete")
rejected.reject()
mark("reject complete")
assert len(doc.Objects) == 2 and rejected.preview_doc is None

cancelled = structural.OptimizeDialog(obj)
cancelled.load_demo()
cancelled.fields["pitch_mm"].setText("4")
cancelled.show()
cancelled.start()
cancelled.reject()
mark("cancel requested")
deadline = time.monotonic() + 120
while cancelled.process.state() != QtCore.QProcess.NotRunning and time.monotonic() < deadline:
    QtGui.QApplication.processEvents()
    time.sleep(.02)
QtGui.QApplication.processEvents()
mark("cancel process finished")
assert cancelled.process.state() == QtCore.QProcess.NotRunning
assert cancelled.result is None and len(doc.Objects) == 2

doc.saveAs(str(root / "gui-accepted.FCStd"))
mark("saved accepted document")
(root / "gui-verified.json").write_text(json.dumps({"preview": True, "accepted": True, "rejected": True,
    "cancelled": True, "original_preserved": True, "original_volume_mm3": original_volume,
    "optimized_volume_mm3": optimized.Shape.Volume}), encoding="utf-8")
App.closeDocument(doc.Name)
