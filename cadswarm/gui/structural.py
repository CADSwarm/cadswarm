"""Human-controlled optimization preview. Numerical work runs in a dedicated process."""
import json
import os
from pathlib import Path
import uuid

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui

from ..paths import ROOT, app_dir, freecad_python
from ..structural.collaboration import accepted_operation


DEMO = {
    "material": {"name": "Demo aluminum (illustrative)", "young_mpa": 70000, "poisson": .33,
                 "yield_mpa": 240, "density_kg_mm3": 2.7e-6},
    "loads": [{"side": "x_max", "force_n": [0, 0, -200]}], "supports": [{"side": "x_min"}],
    "constraints": {"max_stress_mpa": 80, "max_displacement_mm": .05, "safety_factor": 2,
                    "target_reduction": .1, "max_stress_ratio": 1.25, "max_displacement_ratio": 1.15,
                    "skin_cells": 1, "max_trials": 24},
    "pitch_mm": 3, "solver": "calculix", "preserve_boxes": [],
}


class OptimizeDialog(QtGui.QDialog):
    def __init__(self, obj):
        super().__init__(Gui.getMainWindow())
        self.setWindowTitle("CADSwarm — Optimize Part")
        self.resize(760, 780)
        self.source_doc, self.source_name = obj.Document.Name, obj.Name
        self.source_brep = obj.Shape.exportBrepToString()
        self.preview_doc = None
        self.process = None
        self.result = None
        self.cancelling = False
        self.client = None
        from .panel import SESSION, MIRROR_DOC
        if obj.Document.Name == MIRROR_DOC:
            if not SESSION.connected:
                raise ValueError("Join the team before optimizing a shared part")
            self.client = SESSION._bg_client()
            before = next(o["rev"] for o in self.client.get("/model")["objects"] if o["name"] == obj.Name)
            self.source_brep = self.client.post("/brep", {"names": [obj.Name]})["objects"][obj.Name]["brep"]
            self.revision = next(o["rev"] for o in self.client.get("/model")["objects"] if o["name"] == obj.Name)
            if before != self.revision:
                raise ValueError("Source changed while reading it; select it again")
        layout = QtGui.QVBoxLayout(self)
        note = QtGui.QLabel("Exact grid-aligned solids only. Fixed/loaded sides are whole extreme faces in world coordinates.\n"
                           "All existing surfaces, holes and one cell of skin are retained. Curved boundaries are rejected.\n"
                           "Enter explicit engineering inputs below, or load the clearly labeled cantilever demo assumptions.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QtGui.QFormLayout()
        self.fields = {}
        labels = [("name", "Material name"), ("young_mpa", "Young's modulus (MPa)"), ("poisson", "Poisson ratio"),
                  ("yield_mpa", "Yield stress (MPa)"), ("density_kg_mm3", "Density (kg/mm³)"),
                  ("force", "Total force [Fx, Fy, Fz] (N)"), ("pitch_mm", "Grid pitch (mm)"),
                  ("max_stress_mpa", "Maximum stress (MPa)"), ("max_displacement_mm", "Maximum displacement (mm)"),
                  ("safety_factor", "Factor of safety on yield"), ("target_reduction", "Target removal fraction (0–1)"),
                  ("max_stress_ratio", "Maximum stress / original stress"),
                  ("max_displacement_ratio", "Maximum displacement / original displacement")]
        for key, label in labels:
            self.fields[key] = QtGui.QLineEdit()
            form.addRow(label, self.fields[key])
        self.fixed, self.loaded, self.solver = QtGui.QComboBox(), QtGui.QComboBox(), QtGui.QComboBox()
        for combo in (self.fixed, self.loaded):
            combo.addItems([f"{axis}_{end}" for axis in "xyz" for end in ("min", "max")])
        self.loaded.setCurrentIndex(1)
        self.solver.addItems(["calculix", "scipy"])
        form.addRow("Fully fixed face", self.fixed)
        form.addRow("Uniformly loaded face", self.loaded)
        form.addRow("Explicit solver backend", self.solver)
        self.preserve = QtGui.QLineEdit("[]")
        self.preserve.setToolTip("JSON: [[[xmin,ymin,zmin],[xmax,ymax,zmax]], ...] in mm; retain every intersecting solid cell")
        form.addRow("Preserve boxes (world mm, JSON)", self.preserve)
        layout.addLayout(form)
        row = QtGui.QHBoxLayout()
        demo = QtGui.QPushButton("Load demo assumptions")
        demo.clicked.connect(self.load_demo)
        self.start_button = QtGui.QPushButton("Analyze and optimize")
        self.start_button.clicked.connect(self.start)
        row.addWidget(demo)
        row.addWidget(self.start_button)
        layout.addLayout(row)
        self.log = QtGui.QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)
        row = QtGui.QHBoxLayout()
        self.accept_button = QtGui.QPushButton("Accept as new part" + (" and share" if self.client else ""))
        self.accept_button.setEnabled(False)
        self.accept_button.clicked.connect(self.accept_design)
        self.files_button = QtGui.QPushButton("Open report, stress plot and exports")
        self.files_button.setEnabled(False)
        self.files_button.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.output))))
        reject = QtGui.QPushButton("Reject / cancel")
        reject.clicked.connect(self.reject)
        for button in (self.accept_button, self.files_button, reject):
            row.addWidget(button)
        layout.addLayout(row)

    def load_demo(self):
        for key, value in {**DEMO["material"], **DEMO["constraints"], "pitch_mm": 3, "force": "[0, 0, -200]"}.items():
            if key in self.fields:
                self.fields[key].setText(str(value))
        self.fixed.setCurrentIndex(0)
        self.loaded.setCurrentIndex(1)
        self.log.setPlainText("Loaded illustrative assumptions for a 72 × 24 × 24 mm cantilever. Confirm suitability before running.")

    def start(self):
        try:
            material = {k: self.fields[k].text().strip() if k == "name" else float(self.fields[k].text())
                        for k in DEMO["material"]}
            constraints = {k: float(self.fields[k].text()) for k in DEMO["constraints"] if k in self.fields}
            request = {"brep": self.source_brep, "pitch_mm": float(self.fields["pitch_mm"].text()), "material": material,
                       "loads": [{"side": self.loaded.currentText(), "force_n": json.loads(self.fields["force"].text())}],
                       "supports": [{"side": self.fixed.currentText()}], "constraints": constraints,
                       "solver": self.solver.currentText(), "preserve_boxes": json.loads(self.preserve.text())}
            run = Path(app_dir()) / "structural" / uuid.uuid4().hex
            run.mkdir(parents=True)
            request_path = run / "request.json"
            self.cancel_path = run / "cancel"
            request["cancel_path"] = str(self.cancel_path)
            request_path.write_text(json.dumps(request, allow_nan=False), encoding="utf-8")
            self.output = run / "output"
            python = freecad_python()
            self.process = QtCore.QProcess(self)
            env = QtCore.QProcessEnvironment.systemEnvironment()
            env.insert("PYTHONPATH", ROOT + os.pathsep + str(Path(python).parent))
            env.insert("PYTHONUNBUFFERED", "1")
            self.process.setProcessEnvironment(env)
            self.process.setWorkingDirectory(ROOT)
            self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
            self.process.readyReadStandardOutput.connect(self.read_output)
            self.process.finished.connect(self.process_finished)
            self.process.errorOccurred.connect(self.process_error)
            self.start_button.setEnabled(False)
            self.accept_button.setEnabled(False)
            self.files_button.setEnabled(False)
            self.result = None
            self.close_preview()
            self.log.setPlainText("Starting isolated analysis process…")
            self.process.start(python, ["-m", "cadswarm.structural.job", str(request_path), str(self.output)])
        except Exception as exc:
            self.log.appendPlainText(str(exc))
            self.start_button.setEnabled(True)

    def process_error(self, error):
        self.log.appendPlainText("Analysis process error: " + self.process.errorString())
        self.start_button.setEnabled(True)

    def read_output(self):
        self.log.appendPlainText(bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace").strip())

    def process_finished(self, code, status):
        if self.cancelling:
            # Closing a dialog may destroy its QProcess. Defer that destruction
            # until Qt has finished dispatching the process's signal.
            QtCore.QTimer.singleShot(0, self.reject)
            return
        self.start_button.setEnabled(True)
        if code != 0 or status != QtCore.QProcess.NormalExit:
            self.log.appendPlainText("Analysis failed or cancelled. No candidate can be accepted. Check the error above.")
            return
        try:
            report = json.loads((self.output / "report.json").read_text(encoding="utf-8"))
            self.log.setPlainText((self.output / "report.md").read_text(encoding="utf-8"))
            self.files_button.setEnabled(True)
            if report["status"] != "improved":
                return
            shape = Part.Shape()
            shape.read(str(self.output / "optimized.brep"))
            if not shape.isValid() or len(shape.Solids) != 1:
                raise ValueError("Invalid reconstructed output")
            self.result = (report, shape.exportBrepToString())
            self.preview_doc = App.newDocument("CADSwarmOptimizationPreview").Name
            doc = App.getDocument(self.preview_doc)
            original = doc.addObject("Part::Feature", "Original")
            source = Part.Shape()
            source.importBrepFromString(self.source_brep)
            original.Shape = source
            candidate = doc.addObject("Part::Feature", "Optimized")
            display = shape.copy()
            display.translate(App.Vector(source.BoundBox.XLength * 1.2, 0, 0))
            candidate.Shape = display
            original.ViewObject.ShapeColor = (.7, .7, .7)
            candidate.ViewObject.ShapeColor = (.2, .65, .85)
            doc.recompute()
            Gui.activeDocument().activeView().viewIsometric()
            Gui.activeDocument().activeView().fitAll()
            self.accept_button.setEnabled(True)
        except Exception as exc:
            self.log.appendPlainText("Preview failed: " + str(exc))
            self.result = None

    def accept_design(self):
        if not self.result:
            return
        report, brep = self.result
        name = "Optimized_" + uuid.uuid4().hex[:12]
        try:
            if self.client:
                body = accepted_operation(self.source_name, self.revision, self.source_brep, brep, name, report)
                receipt = self.client.post("/ops", body)
            else:
                doc = App.getDocument(self.source_doc)
                source = doc.getObject(self.source_name)
                expected = Part.Shape()
                expected.importBrepFromString(self.source_brep)
                if source is None or source.Shape.cut(expected).Volume > 1e-7 or expected.cut(source.Shape).Volume > 1e-7:
                    raise ValueError("Source changed; rerun optimization")
                doc.openTransaction("Accept structural optimization")
                try:
                    shape = Part.Shape()
                    shape.importBrepFromString(brep)
                    obj = doc.addObject("Part::Feature", name)
                    obj.Shape = shape
                    obj.addProperty("App::PropertyString", "StructuralReport", "CADSwarm")
                    obj.StructuralReport = json.dumps(report)
                    doc.recompute()
                    doc.commitTransaction()
                except Exception:
                    doc.abortTransaction()
                    # A user's local document may have UndoMode=0, in which case
                    # abortTransaction does not remove the newly created object.
                    if doc.getObject(name):
                        doc.removeObject(name)
                        doc.recompute()
                    raise
                receipt = {"created": [name]}
            self.accept_button.setEnabled(False)
            self.result = None
            self.log.appendPlainText("Accepted as " + name + ("; shared through the existing hub transaction." if self.client else ". Save the source document to persist it."))
            try:
                (self.output / "acceptance.json").write_text(json.dumps({"accepted_by_user": True, "receipt": receipt}), encoding="utf-8")
            except OSError as exc:
                self.log.appendPlainText("The part was accepted, but its local receipt could not be saved: " + str(exc))
        except Exception as exc:
            self.log.appendPlainText("Acceptance failed: " + str(exc))

    def close_preview(self):
        if self.preview_doc and App.getDocument(self.preview_doc):
            App.closeDocument(self.preview_doc)
        self.preview_doc = None

    def reject(self):
        if self.process and self.process.state() != QtCore.QProcess.NotRunning:
            self.cancelling = True
            self.cancel_path.touch()
            self.log.appendPlainText("Cancellation requested; waiting for the current geometry/solver operation to finish.")
            self.start_button.setEnabled(False)
            return
        self.close_preview()
        super().reject()


_dialogs = []


class OptimizeCommand:
    def GetResources(self):
        return {"MenuText": "Optimize Part…", "ToolTip": "Analyze, reduce material and review a selected solid"}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        try:
            selected = Gui.Selection.getSelection()
            if len(selected) != 1 or not hasattr(selected[0], "Shape"):
                raise ValueError("Select exactly one solid CAD object")
            dlg = OptimizeDialog(selected[0])
            _dialogs.append(dlg)
            dlg.finished.connect(lambda *_: _dialogs.remove(dlg) if dlg in _dialogs else None)
            dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            dlg.show()
        except Exception as exc:
            QtGui.QMessageBox.warning(Gui.getMainWindow(), "Optimize Part", str(exc))


def register_command():
    Gui.addCommand("CadSwarm_OptimizePart", OptimizeCommand())
