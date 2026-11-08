"""Drive the simulation features inside the real branded CADSwarm app and screenshot each one.

    "<FreeCAD 1.1>/bin/python.exe" scripts/sim_branded.py <scenario> <out dir> [--case N]

Starts CADSwarm exactly like `python -m cadswarm.app` (FreeCAD's full GUI, CADSwarm branding, its own engine
profile in CADSWARM_ENGINE_HOME or a fresh temp folder), then runs one scenario on the GUI thread through the
same commands a person clicks, saves screenshots to <out dir> and writes <out dir>/<scenario>-<case>.json
({"ok": ..., numbers, "shots": [...]}), then quits.

Scenarios:
  fem        FreeCAD's FEM workbench by its own commands: analysis, CalculiX solver, material, fixed and force
             constraints, Gmsh mesh, FEM_SolverRun, then a result pipeline coloured by von Mises stress.
  lighten    CADSwarm's Lighten part dialog (local mode) on a bracket, run to the end, preview shown.
  optimize   CADSwarm's Optimize part dialog (CalculiX backend) on a bracket, run to the end.
  report     an FEA job solved by the real runner, published, and its report opened the way the panel does.
"""
import json
import os
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SCENARIO = sys.argv[1]
OUT = os.path.abspath(sys.argv[2])
CASE = int(sys.argv[sys.argv.index("--case") + 1]) if "--case" in sys.argv else 1
os.makedirs(OUT, exist_ok=True)
os.environ.setdefault("CADSWARM_ENGINE_HOME", tempfile.mkdtemp(prefix="cadswarm-engine-"))
import faulthandler  # noqa: E402
_FH = open(os.path.join(OUT, f"{SCENARIO}-{CASE}.stacks"), "w")
faulthandler.enable(file=_FH, all_threads=True)
faulthandler.dump_traceback_later(int(os.environ.get("CADSWARM_SIM_HANG_S", "240")), repeat=True, file=_FH)
RESULT = {"scenario": SCENARIO, "case": CASE, "ok": False, "shots": []}

# (length, width, height, force N) per case: the cantilever bracket arm the FEM scenario solves.
FEM_CASES = {1: (100.0, 20.0, 10.0, 500.0), 2: (150.0, 25.0, 12.0, 800.0), 3: (80.0, 15.0, 8.0, 200.0)}


def log(msg):
    with open(os.path.join(OUT, f"{SCENARIO}-{CASE}.log"), "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def pump(seconds):
    from PySide6 import QtWidgets
    end = time.time() + seconds
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.03)


def shot(name, widget=None):
    import FreeCADGui as Gui
    pump(0.6)
    path = os.path.join(OUT, f"{SCENARIO}-{CASE}-{name}.png" if CASE != 1 else f"{SCENARIO}-{name}.png")
    (widget or Gui.getMainWindow()).grab().save(path)
    RESULT["shots"].append(path)
    return path


def close_popups():
    """The first-start guide and other modal dialogs would sit over every screenshot."""
    from PySide6 import QtWidgets
    for w in QtWidgets.QApplication.topLevelWidgets():
        if isinstance(w, QtWidgets.QDialog) and w.isVisible():
            w.reject()


def accept_task():
    """Close the open task panel, keeping what it made. Its OK button re-validates references the panel's own
    list doesn't show (we set them in Python) and would pop a modal warning, so leave edit mode instead."""
    import FreeCADGui as Gui
    pump(0.3)
    try:
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        if Gui.ActiveDocument and Gui.ActiveDocument.getInEdit():
            Gui.ActiveDocument.resetEdit()
    except Exception as e:  # noqa: BLE001
        log(f"accept_task: {e}")
    pump(0.3)


def dismiss_message_boxes():
    """Safety net: a modal message box would block the scenario; log its text and close it."""
    from PySide6 import QtWidgets
    for w in QtWidgets.QApplication.topLevelWidgets():
        if isinstance(w, QtWidgets.QMessageBox) and w.isVisible():
            log("message box: " + w.text()[:300])
            RESULT.setdefault("message_boxes", []).append(w.text()[:300])
            w.done(0)


def view_fit(iso=True):
    import FreeCADGui as Gui
    v = Gui.activeDocument().activeView()
    if iso:
        v.viewIsometric()
    v.fitAll()


# --------------------------------------------------------------------------- FEM workbench

def scenario_fem():
    import FreeCAD as App
    import FreeCADGui as Gui
    import Part
    L, b, h, F = FEM_CASES[CASE]
    t0 = time.time()
    doc = App.newDocument("FemBracket")
    part = doc.addObject("Part::Box", "Bracket")
    part.Length, part.Width, part.Height = L, b, h
    doc.recompute()
    Gui.activateWorkbench("FemWorkbench")
    pump(1)
    view_fit()
    log("FEM_Analysis"); Gui.runCommand("FEM_Analysis")
    pump(0.5)
    analysis = next(o for o in doc.Objects if o.isDerivedFrom("Fem::FemAnalysis"))
    log("FEM_SolverCalculiXCcxTools"); Gui.runCommand("FEM_SolverCalculiXCcxTools")
    accept_task()
    log("FEM_MaterialSolid"); Gui.runCommand("FEM_MaterialSolid")
    pump(1)
    mat = next(o for o in doc.Objects if o.Name.startswith("MaterialSolid"))
    card = dict(mat.Material)
    card.update({"Name": "Steel", "YoungsModulus": "210000 MPa", "PoissonRatio": "0.3", "Density": "7850 kg/m^3"})
    mat.Material = card
    accept_task()

    def face(selector):
        from cadswarm.worker.fea_runner import select_faces
        return select_faces(part.Shape, [selector])

    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, part.Name, face("xmin")[0])
    log("FEM_ConstraintFixed"); Gui.runCommand("FEM_ConstraintFixed")
    pump(0.5)
    fixed = next(o for o in doc.Objects if o.isDerivedFrom("Fem::ConstraintFixed"))
    fixed.References = [(part, face("xmin"))]
    accept_task()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, part.Name, face("xmax")[0])
    log("FEM_ConstraintForce"); Gui.runCommand("FEM_ConstraintForce")
    pump(0.5)
    force = next(o for o in doc.Objects if o.isDerivedFrom("Fem::ConstraintForce"))
    force.References = [(part, face("xmax"))]
    force.Force = f"{F} N"
    line = doc.addObject("Part::Line", "LoadDirection")
    line.X1, line.Y1, line.Z1, line.X2, line.Y2, line.Z2 = 0, 0, 0, 0, 0, -1
    line.Visibility = False
    doc.recompute()
    force.Direction = (line, ["Edge1"])
    force.Reversed = False
    accept_task()
    doc.recompute()
    shot("setup")

    # Mesh: the same calls FEM_MeshGmshFromShape's task panel makes when you press Apply.
    import ObjectsFem
    from femmesh.gmshtools import GmshTools
    mesh = ObjectsFem.makeMeshGmsh(doc, "BracketMesh")
    mesh.Shape = part
    mesh.CharacteristicLengthMax = f"{min(b, h) / 3} mm"
    mesh.ElementOrder = "2nd"
    analysis.addObject(mesh)
    doc.recompute()
    t1 = time.time()
    err = GmshTools(mesh).create_mesh()
    if err:
        raise RuntimeError("Gmsh: " + err)
    doc.recompute()
    t2 = time.time()
    solver = next(o for o in analysis.Group if o.Name.startswith("SolverCcxTools"))
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, solver.Name)
    log("FEM_SolverRun"); Gui.runCommand("FEM_SolverRun")
    pump(1)
    t3 = time.time()
    results = [o for o in analysis.Group if o.isDerivedFrom("Fem::FemResultObject")]
    if not results:
        raise RuntimeError("FEM_SolverRun produced no result object")
    res = results[0]
    vm, disp = max(res.vonMises), max(res.DisplacementLengths)
    # Show it like a person would: a post-processing pipeline, coloured by von Mises stress.
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, res.Name)
    log("FEM_PostPipelineFromResult"); Gui.runCommand("FEM_PostPipelineFromResult")
    pump(1.5)
    pipe = next((o for o in doc.Objects if o.isDerivedFrom("Fem::FemPostPipeline")), None)
    if pipe is not None:
        doc.recompute()
        vp = pipe.ViewObject
        try:
            vp.Field = "von Mises Stress"
        except Exception:  # noqa: BLE001
            pass
        for o in doc.Objects:
            if o is not pipe and hasattr(o, "ViewObject") and o.ViewObject is not None:
                o.ViewObject.Visibility = False
        vp.Visibility = True
    close_popups()
    accept_task()
    view_fit()
    shot("result")
    from cadswarm.fea import PRESETS  # noqa: F401  (hand calc mirrors sim_validate.cantilever_hand)
    E, nu = 210000.0, 0.3
    G, I = E / (2 * (1 + nu)), b * h ** 3 / 12
    hand = F * L ** 3 / (3 * E * I) + F * L / ((5 / 6) * G * b * h)
    RESULT.update(ok=abs(disp - hand) / hand <= 0.05 and pipe is not None,
                  max_displacement_mm=round(disp, 5), hand_deflection_mm=round(hand, 5),
                  deflection_error_pct=round(100 * (disp - hand) / hand, 2), max_von_mises_mpa=round(vm, 2),
                  hand_root_stress_mpa=round(6 * F * L / (b * h * h), 2), nodes=mesh.FemMesh.NodeCount,
                  pipeline=bool(pipe), seconds={"setup": round(t1 - t0, 2), "mesh": round(t2 - t1, 2),
                                                "solve": round(t3 - t2, 2)})


# --------------------------------------------------------------------------- Lighten / Optimize dialogs

def _bracket_doc(name, l=36, w=12, h=12):
    import FreeCAD as App
    import FreeCADGui as Gui
    doc = App.newDocument(name)
    part = doc.addObject("Part::Box", "Bracket")
    part.Length, part.Width, part.Height = l, w, h
    doc.recompute()
    pump(0.5)
    view_fit()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, "Bracket")
    return doc


def scenario_lighten():
    from PySide6 import QtWidgets
    from cadswarm.gui import lighten_ui
    sizes = {1: ((36, 12, 12), "gyroid", "300"), 2: ((44, 14, 12), "bcc", "500"), 3: ((30, 12, 10), "octet", "150")}
    (l, w, h), cell, newtons = sizes[CASE]
    _bracket_doc("LightenBracket", l, w, h)
    t0 = time.time()
    dlg = lighten_ui.show_lighten()
    if dlg is None:
        raise RuntimeError("Lighten dialog did not open")
    dlg.material.setCurrentIndex(dlg.material.findData("aluminium_6061_t6"))
    dlg.fixed.setText("xmin")
    dlg.loaded.setText("xmax")
    for e, v in zip(dlg.force, ("0", "0", "-" + newtons)):
        e.setText(v)
    dlg.cell.setCurrentIndex(dlg.cell.findData(cell))
    dlg.cell_size.setText("5")
    dlg.skin.setText("1.0")
    dlg.voxel.setText("0.45")
    dlg.target.setValue(70)
    dlg.check()
    shot("setup", dlg)
    dlg.tabs.setCurrentIndex(3)
    dlg.run()
    t = time.time()
    while dlg.timer.isActive() and time.time() - t < 900:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.2)
    s = dlg.last or {}
    shot("dialog", dlg)
    shot("window")
    if s.get("status") == "done":
        dlg.toggle_section(True)
        pump(1)
        view_fit()
        shot("section")
    RESULT.update(ok=s.get("status") == "done", status=s.get("status"), verdict=s.get("verdict"),
                  weight_saving_pct=s.get("weight_saving_pct"),
                  stiffness_retained=(s.get("verify") or {}).get("stiffness_retained"),
                  summary=dlg.summary.text()[:400], seconds=round(time.time() - t0, 1))


def scenario_optimize():
    from PySide6 import QtCore, QtWidgets
    from cadswarm.gui import structural
    box, pitch, force = {1: ((48, 16, 16), 4, -200), 2: ((60, 15, 15), 5, -400), 3: ((36, 12, 12), 4, -150)}[CASE]
    doc = _bracket_doc("OptimizeBracket", *box)
    t0 = time.time()
    dlg = structural.OptimizeDialog(doc.getObject("Bracket"))
    dlg.show()
    dlg.load_demo()
    dlg.fields["pitch_mm"].setText(str(pitch))
    dlg.fields["force"].setText(f"[0, 0, {force}]")
    dlg.fields["target_reduction"].setText("0.05")
    for key, value in (("max_stress_mpa", "1000"), ("max_displacement_mm", "10")):
        if key in dlg.fields:
            dlg.fields[key].setText(value)
    dlg.solver.setCurrentIndex(dlg.solver.findText("calculix"))
    shot("setup", dlg)
    dlg.start()
    t = time.time()
    pump(1)
    while dlg.process is not None and dlg.process.state() != QtCore.QProcess.NotRunning and time.time() - t < 900:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.2)
    pump(2)
    shot("dialog", dlg)
    shot("window")
    if dlg.result:
        dlg.section_button.setChecked(True)
        pump(1)
        view_fit()
        shot("section")
    report = dlg.result[0] if dlg.result else {}
    RESULT.update(ok=dlg.result is not None, status=report.get("status"),
                  removed_pct=report.get("material_removed_percent"), log=dlg.log.toPlainText()[-600:],
                  seconds=round(time.time() - t0, 1))
    dlg.close_preview()


# --------------------------------------------------------------------------- FEA report

def scenario_report():
    import shutil
    from cadswarm import helper
    import FreeCAD as App
    import FreeCADGui as Gui
    L, b, h, F = FEM_CASES[CASE]
    root = tempfile.mkdtemp(prefix="cadswarm-report-")
    doc = App.newDocument("ReportBracket")
    doc.addObject("Part::Box", "Bracket")
    doc.Bracket.Length, doc.Bracket.Width, doc.Bracket.Height = L, b, h
    doc.recompute()
    doc.saveAs(os.path.join(root, "model.FCStd"))
    view_fit()
    p = helper.Project(root)
    t0 = time.time()
    s = p.fea.start({"target": "Bracket", "material": "steel_s235", "fixed": ["xmin"],
                     "loads": [{"type": "force", "faces": ["xmax"], "newtons": F * 4, "direction": [0, 0, -1]}],
                     "label": "bracket check"}, {"id": 1, "name": "sim-check", "kind": "human"})
    while s["status"] in ("queued", "running"):
        pump(0.5)
        s = p.fea.status(s["job_id"])
    if s["status"] != "done":
        raise RuntimeError(s.get("error"))
    pub = p.fea.publish(s["job_id"], summary="Checked in the branded app.", improvements=["Fillet the root"],
                        actor={"id": 1, "name": "sim-check", "kind": "human"})
    folder = os.path.join(root, "files", pub["folder"].removeprefix("files/"))
    RESULT.update(folder=folder, verdict=pub["verdict"], results=s["results"], seconds=round(time.time() - t0, 1))
    from cadswarm.gui import panel

    class FilesClient:  # what the panel's team client answers for the project's files, read from disk
        def post(self, path, body):
            with open(os.path.join(root, "files", body["path"]), encoding="utf-8") as f:
                return {"text": f.read()}

        def request(self, method, path, raw=False):
            from urllib.parse import parse_qs, urlparse
            rel = parse_qs(urlparse(path).query)["path"][0].removeprefix("files/")
            with open(os.path.join(root, "files", rel), "rb") as f:
                return f.read()
    panel.SESSION.client = FilesClient()
    opened = panel.FeaReportDialog(pub["folder"].removeprefix("files/"))
    opened.show()
    pump(3)
    shot("report", opened)
    shutil.copy2(os.path.join(folder, "plot.png"), os.path.join(OUT, f"report-{CASE}-plot.png"))
    RESULT["ok"] = True


SCENARIOS = {"fem": scenario_fem, "lighten": scenario_lighten, "optimize": scenario_optimize,
             "report": scenario_report}


def run_scenario():
    from PySide6 import QtWidgets
    try:
        close_popups()
        shot("start") if CASE == 1 and SCENARIO == "fem" else None
        SCENARIOS[SCENARIO]()
    except Exception as e:  # noqa: BLE001
        RESULT.update(ok=False, error=f"{type(e).__name__}: {e}", trace=traceback.format_exc(limit=8))
        try:
            shot("error")
        except Exception:  # noqa: BLE001
            pass
    with open(os.path.join(OUT, f"{SCENARIO}-{CASE}.json"), "w", encoding="utf-8") as f:
        json.dump(RESULT, f, indent=2, default=str)
    for w in QtWidgets.QApplication.topLevelWidgets():  # dialogs first: they hold preview documents
        if isinstance(w, QtWidgets.QDialog) and w.isVisible():
            log(f"closing {type(w).__name__}")
            w.reject()
    pump(1)
    log("closing documents")
    import FreeCAD
    for name in list(FreeCAD.listDocuments()):
        try:
            FreeCAD.closeDocument(name)
        except Exception:  # noqa: BLE001
            pass
    log("quit")
    QtWidgets.QApplication.instance().quit()


def main():
    from cadswarm.app import rebrand_qt
    original = rebrand_qt.Rebrander.install

    def install(self):  # runs right after FreeCAD's main window exists: schedule the scenario
        out = original(self)
        from PySide6 import QtCore
        QtCore.QTimer.singleShot(int(os.environ.get("CADSWARM_SIM_START_MS", "6000")), run_scenario)
        guard = QtCore.QTimer(self)
        guard.timeout.connect(dismiss_message_boxes)
        guard.start(700)
        return out
    rebrand_qt.Rebrander.install = install
    from cadswarm.app.__main__ import main as app_main
    code = app_main([])
    log("event loop returned")
    sys.stdout.write(json.dumps({k: RESULT.get(k) for k in ("scenario", "case", "ok", "error")}) + "\n")
    sys.stdout.flush()
    os._exit(0 if RESULT["ok"] else 1 if code == 0 else code)


if __name__ == "__main__":
    main()
