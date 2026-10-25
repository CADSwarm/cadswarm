"""Builds the Auto-constrain dialog in an offscreen FreeCAD and checks what Apply sends.

    python constraints_gui_check.py <out dir>

Writes constraints-dialog.png and constraints-verified.json. Run by test_constraints_gui.py.
"""
import json
import sys
from pathlib import Path

import FreeCADGui as Gui
Gui.showMainWindow()
from PySide import QtGui  # noqa: E402

from cadswarm.gui import constraints_ui, panel  # noqa: E402

out = Path(sys.argv[1])

result = {
    "names": ["Base", "Lid", "Hole1", "Pin"],
    "plan": {
        "fits": {"tol_slip_fit": 0.1, "tol_press_fit": -0.05},
        "skipped": [{"object": "Lid", "property": "Width", "reason": "already follows Params.wall"}],
        "constraints": [
            {"id": "shared:height", "kind": "shared_size", "title": "One height for 2 parts",
             "detail": "Base, Lid all use 2 mm; changing Params.height changes each.",
             "params": [{"alias": "height", "value": "2 mm"}],
             "bindings": [
                 {"object": "Base", "property": "Height", "old": 2, "new": 2,
                  "expression": "Params.height"},
                 {"object": "Lid", "property": "Height", "old": 2, "new": 2,
                  "expression": "Params.height"}]},
            {"id": "fit:Hole1", "kind": "fit", "title": "Hole1 follows the team's slip fit",
             "detail": "Hole1 (r 1.7) mates with Pin (r 1.5); Radius becomes 1.55 mm.",
             "params": [{"alias": "tol_slip_fit", "value": "0.1 mm"}],
             "bindings": [
                 {"object": "Hole1", "property": "Radius", "old": 1.7, "new": 1.55,
                  "expression": "Params.pin_r + Params.tol_slip_fit / 2"},
                 {"object": "Pin", "property": "Radius", "old": 1.5, "new": 1.5,
                  "expression": "Params.pin_r"}]},
        ],
    },
}

sent = []
said = []


class Recorder:
    def post(self, path, body=None):
        sent.append((path, body or {}))
        body = body or {}
        return {"applied": list(body.get("ids") or []), "params": ["height"],
                "objects": ["Base", "Lid"], "recomputed": []}


class Session:
    """The one call the dialog makes: run the request off the UI thread, then report back."""

    def run_async(self, work, done=None, purpose="", failed=None):
        if done:
            done(work(Recorder()))


panel.say = lambda msg: said.append(msg)
dlg = constraints_ui.AutoConstrainDialog(Session(), result, "the selected parts")
titles = [cb.text() for _, cb in dlg.checks]
ticked_by_default = all(cb.isChecked() for _, cb in dlg.checks)
dlg.checks[1][1].setChecked(False)  # leave the fit alone, apply only the shared height
dlg.show()
for _ in range(5):
    QtGui.QApplication.processEvents()
dlg.grab().save(str(out / "constraints-dialog.png"))
dlg.apply_selected()
for _ in range(5):
    QtGui.QApplication.processEvents()

labels = [label.text() for label in dlg.findChildren(QtGui.QLabel)]
verified = {
    "titles": titles,
    "fits_line": any("slip 0.1 mm" in text for text in labels),
    "ticked_by_default": ticked_by_default,
    "sent": sent,
    "said": said,
    "closed": not dlg.isVisible(),
}
(out / "constraints-verified.json").write_text(json.dumps(verified, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
QtGui.QApplication.processEvents()
sys.exit(0)
