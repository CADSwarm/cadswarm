"""Lighten part: fill a part with a lattice that is thicker where it works, and check it stays stiff.

Four steps in one dialog: the part and how it's made, how it's held and loaded, the lattice, then run and
review. With the team's live model the job runs on the team server (lattice_jobs.py, through the hub) and
"Add to model" puts the lattice in the shared model; without a team the same job runs on this computer
and the lattice goes into the open document. Either way the original part is never changed.

Runs on FreeCAD's GUI thread; the job itself is a separate FreeCAD process, polled with a timer.
"""
import html
import os
import threading
import uuid

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

from .. import fea
from ..client import HubRequestError
from ..lattice_jobs import CELLS, LatticeJobError, LatticeJobs, cell_library, estimate, freecad_python

# How a part is made decides the thinnest wall and whether loose powder or resin must drain out.
PROCESSES = [
    ("fdm", "Filament printing (FDM)", 0.8, False),
    ("sls", "Powder printing (SLS / MJF)", 0.8, True),
    ("sla", "Resin printing (SLA / DLP)", 0.5, True),
    ("lpbf", "Metal powder printing (LPBF)", 0.4, True),
]
CELL_WORDS = {
    "gyroid": "Gyroid: smooth, prints without supports, about as stiff in every direction",
    "diamond": "Diamond: the stiffest smooth cell for its weight",
    "schwarzP": "Schwarz P: big open pores, softer on the diagonals",
    "iwp": "I-WP: very stiff smooth cell, smaller pores",
    "octet": "Octet: the stiffest strut cell",
    "bcc": "BCC: simple diagonal struts, light and flexible",
    "fcc": "FCC: face-diagonal struts",
    "cubic": "Cubic: straight struts along the axes, stiff only along them",
}
PREVIEW_DOC = "CadSwarmLightenPreview"


def _p():
    from . import panel
    return panel


def _num(edit, default=None):
    try:
        return float(edit.text().replace(",", "."))
    except ValueError:
        return default


class LocalJob:
    """The lighten job on this computer (no team): the same runner, in a background thread."""

    def __init__(self, obj, request):
        self.id = "local_" + uuid.uuid4().hex[:8]
        from ..paths import app_dir
        self.work = os.path.join(app_dir(), "lattice", self.id)
        os.makedirs(self.work, exist_ok=True)
        model = os.path.join(self.work, "model.FCStd")
        try:
            doc = App.newDocument("CadSwarmLightenSource", hidden=True)
        except TypeError:  # a FreeCAD without hidden documents
            doc = App.newDocument("CadSwarmLightenSource")
        part = doc.addObject("Part::Feature", "Part")
        part.Shape = obj.Shape.copy()
        doc.saveAs(model)
        App.closeDocument(doc.Name)
        fea_job = None
        if request.get("fixed") or request.get("loads"):
            fea_job = fea.normalize_job({"analysis": "static", "target": "Part", "material": request.get("material"),
                                         "fixed": request.get("fixed"), "loads": request.get("loads")})
        recipe = {k: v for k, v in request.items() if k not in ("fixed", "loads", "material", "target")}
        if "stiffness_target_pct" in recipe:
            recipe["stiffness_target"] = recipe.pop("stiffness_target_pct") / 100
        self.job = {"doc_path": model, "target": "Part", "name": request.get("name") or obj.Name + "_lattice",
                    "recipe": recipe, "fea": fea_job, "out_dir": os.path.join(self.work, "out"),
                    "material": None if fea_job else fea.normalize_job(
                        {"analysis": "static", "target": "Part", "material": request.get("material"),
                         "fixed": ["zmin"], "loads": [{"type": "gravity"}]})["material"]}
        self.state = {"status": "queued", "phase": None, "progress": 0.0, "note": ""}
        self.cancel_flag = False
        self.result = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        def on_progress(p):
            self.state.update(status="generating", phase=p.get("phase"), progress=p.get("fraction"),
                              note=p.get("text"))
        try:
            self.result = LatticeJobs._run_process(freecad_python(), self.job, self.work, on_progress,
                                                   lambda: self.cancel_flag)
        except Exception as e:
            self.result = {"ok": False, "code": "lattice_error", "error": f"{type(e).__name__}: {e}"}
        if self.result.get("ok"):
            self.state["status"] = "done"
        else:
            self.state["status"] = "cancelled" if self.result.get("code") == "cancelled" else "failed"

    def status(self):
        out = dict(self.state, job_id=self.id)
        if self.result and not self.result.get("ok"):
            out.update(error=self.result.get("error"), code=self.result.get("code"))
        if self.result and self.result.get("ok"):
            rep = self.result["report"]
            out.update(verdict=rep.get("status"), weight_saving_pct=rep["mass"]["weight_saving_pct"],
                       mass=rep["mass"], thickness_mm=rep["thickness_mm"], verify=rep.get("verify"),
                       mesh=rep["mesh"], warnings=[w["text"] for w in rep.get("warnings") or []],
                       notes=rep.get("notes"), folder=self.job["out_dir"])
        return out


class LightenDialog(QtGui.QDialog):
    def __init__(self, obj):
        super().__init__(Gui.getMainWindow())
        self.setWindowTitle("CadSwarm: Lighten part")
        from .panel import fit_to_screen, MIRROR_DOC, SESSION
        fit_to_screen(self, 720, 760)
        self.obj_name, self.doc_name = obj.Name, obj.Document.Name
        self.team = obj.Document.Name == MIRROR_DOC and SESSION.connected
        self.client = SESSION.client if self.team else None
        self.shape = obj.Shape.copy()
        self.job = None          # team: job id; local: LocalJob
        self.last = None
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        bb = self.shape.BoundBox
        self.bbox = [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax]

        lay = QtGui.QVBoxLayout(self)
        intro = QtGui.QLabel(
            f"<b>{html.escape(obj.Label)}</b> ({bb.XLength:.0f} × {bb.YLength:.0f} × {bb.ZLength:.0f} mm). "
            "CadSwarm fills its inside with a lattice that is <i>thicker where the part works hard</i>, keeps a "
            "solid skin and solid zones where it is held and loaded, and checks the result keeps the stiffness "
            "you ask for. The original part is not changed.")
        intro.setWordWrap(True)
        intro.setTextFormat(QtCore.Qt.RichText)
        lay.addWidget(intro)
        self.tabs = QtGui.QTabWidget()
        self.tabs.addTab(self._part_tab(), "1  Part")
        self.tabs.addTab(self._load_tab(), "2  Held and loaded")
        self.tabs.addTab(self._lattice_tab(), "3  Lattice")
        self.tabs.addTab(self._run_tab(), "4  Run and review")
        lay.addWidget(self.tabs, 1)
        row = QtGui.QHBoxLayout()
        self.back = QtGui.QPushButton("◂ Back")
        self.back.clicked.connect(lambda: self.tabs.setCurrentIndex(max(0, self.tabs.currentIndex() - 1)))
        self.next = QtGui.QPushButton("Next ▸")
        self.next.clicked.connect(lambda: self.tabs.setCurrentIndex(min(3, self.tabs.currentIndex() + 1)))
        close = QtGui.QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(self.back)
        row.addWidget(self.next)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.tabs.currentChanged.connect(self._tab_changed)
        self._tab_changed(0)

    # ------------------------------------------------------------------ step 1: part

    def _part_tab(self):
        w = QtGui.QWidget()
        f = QtGui.QFormLayout(w)
        self.material = QtGui.QComboBox()
        for key, m in fea.PRESETS.items():
            self.material.addItem(m["name"], key)
        self.material.setCurrentIndex(max(0, self.material.findData("pla")))
        f.addRow("Material", self.material)
        self.process = QtGui.QComboBox()
        for key, label, wall, drain in PROCESSES:
            self.process.addItem(label, key)
        self.process.currentIndexChanged.connect(self._process_changed)
        f.addRow("How it's made", self.process)
        note = QtGui.QLabel("The process sets the thinnest wall that prints, and whether loose powder or resin "
                            "needs openings to drain out of the lattice.")
        note.setWordWrap(True)
        f.addRow(note)
        return w

    def _process_changed(self, _i=None):
        key, _, wall, drain = PROCESSES[self.process.currentIndex()]
        self.min_wall.setText(f"{wall:g}")
        for b in self.open_boxes.values():
            b.setChecked(False)
        if drain:  # two opposite openings along the longest axis
            sizes = [self.bbox[i + 3] - self.bbox[i] for i in range(3)]
            axis = "xyz"[sizes.index(max(sizes))]
            self.open_boxes["+" + axis].setChecked(True)
            self.open_boxes["-" + axis].setChecked(True)

    # -------------------------------------------------------------- step 2: load case

    def _load_tab(self):
        w = QtGui.QWidget()
        lay = QtGui.QVBoxLayout(w)
        self.mode_faces = QtGui.QRadioButton("I'll pick where it's held and where the load goes (recommended)")
        self.mode_uniform = QtGui.QRadioButton("No load case: an even lattice, not checked for stiffness")
        self.mode_faces.setChecked(True)
        lay.addWidget(self.mode_faces)
        box = QtGui.QGroupBox("Load case")
        f = QtGui.QFormLayout(box)
        self.fixed = QtGui.QLineEdit("xmin")
        self.fixed.setToolTip("Faces held still: select them in the 3D view and click Use selected faces, or type "
                              "Face3, xmin…zmax, cyl r=2.5")
        pick_fixed = QtGui.QPushButton("Use selected faces")
        pick_fixed.clicked.connect(lambda: self._pick_faces(self.fixed))
        rf = QtGui.QHBoxLayout()
        rf.addWidget(self.fixed, 1)
        rf.addWidget(pick_fixed)
        f.addRow("Held still", rf)
        self.loaded = QtGui.QLineEdit("xmax")
        pick_load = QtGui.QPushButton("Use selected faces")
        pick_load.clicked.connect(lambda: self._pick_faces(self.loaded))
        rl = QtGui.QHBoxLayout()
        rl.addWidget(self.loaded, 1)
        rl.addWidget(pick_load)
        f.addRow("Load acts on", rl)
        self.force = [QtGui.QLineEdit(v) for v in ("0", "0", "-200")]
        rv = QtGui.QHBoxLayout()
        for axis, e in zip("xyz", self.force):
            rv.addWidget(QtGui.QLabel(axis))
            rv.addWidget(e)
        rv.addWidget(QtGui.QLabel("N"))
        f.addRow("Force", rv)
        note = QtGui.QLabel("Tip: 10 N is about the weight of 1 kg. Use the largest load the part really sees.")
        note.setWordWrap(True)
        f.addRow(note)
        lay.addWidget(box)
        lay.addWidget(self.mode_uniform)
        self.uniform_wall = QtGui.QLineEdit("1.2")
        ru = QtGui.QHBoxLayout()
        ru.addWidget(QtGui.QLabel("Wall thickness (mm)"))
        ru.addWidget(self.uniform_wall)
        ru.addStretch(1)
        lay.addLayout(ru)
        self.mode_faces.toggled.connect(box.setEnabled)
        self.mode_faces.toggled.connect(lambda on: self.uniform_wall.setEnabled(not on))
        self.uniform_wall.setEnabled(False)
        lay.addStretch(1)
        return w

    def _pick_faces(self, edit):
        faces = []
        for sel in Gui.Selection.getSelectionEx():
            if sel.Object.Name == self.obj_name:
                faces += [s for s in sel.SubElementNames if s.startswith("Face")]
        if not faces:
            _p().hint(f"Select one or more faces of {self.obj_name} in the 3D view first (Ctrl+click for more).")
            return
        edit.setText(", ".join(dict.fromkeys(faces)))

    # ---------------------------------------------------------------- step 3: lattice

    def _lattice_tab(self):
        w = QtGui.QWidget()
        f = QtGui.QFormLayout(w)
        self.cell = QtGui.QComboBox()
        for c in CELLS:
            self.cell.addItem(CELL_WORDS[c], c)
        help_btn = QtGui.QPushButton("Help me choose…")
        help_btn.clicked.connect(self.choose_cell)
        rc = QtGui.QHBoxLayout()
        rc.addWidget(self.cell, 1)
        rc.addWidget(help_btn)
        f.addRow("Cell", rc)
        size = max(4.0, min(12.0, round(min(self.bbox[i + 3] - self.bbox[i] for i in range(3)) / 3)))
        self.cell_size = QtGui.QLineEdit(f"{size:g}")
        f.addRow("Cell size (mm)", self.cell_size)
        self.min_wall = QtGui.QLineEdit("0.8")
        self.max_wall = QtGui.QLineEdit("")
        self.max_wall.setPlaceholderText("auto (a third of the cell)")
        f.addRow("Thinnest wall (mm)", self.min_wall)
        f.addRow("Thickest wall (mm)", self.max_wall)
        self.skin = QtGui.QLineEdit("1.5")
        self.skin.setToolTip("A solid outer shell. In bending the skin carries most of the stiffness; CadSwarm "
                             "thickens it if the lattice alone can't keep the stiffness you ask for.")
        f.addRow("Solid skin (mm)", self.skin)
        ro = QtGui.QHBoxLayout()
        self.open_boxes = {}
        for face in ("+x", "-x", "+y", "-y", "+z", "-z"):
            b = QtGui.QCheckBox(face)
            self.open_boxes[face] = b
            ro.addWidget(b)
        f.addRow("Leave the skin open on", ro)
        self.target = QtGui.QSlider(QtCore.Qt.Horizontal)
        self.target.setRange(50, 99)
        self.target.setValue(90)
        self.target_label = QtGui.QLabel()
        self.target.valueChanged.connect(lambda v: self.target_label.setText(f"keep {v} % of the stiffness"))
        self.target.valueChanged.emit(90)
        rt = QtGui.QHBoxLayout()
        rt.addWidget(self.target, 1)
        rt.addWidget(self.target_label)
        f.addRow("Stiffness", rt)
        self.voxel = QtGui.QLineEdit("")
        self.voxel.setPlaceholderText("auto")
        self.voxel.setToolTip("Build resolution. Smaller is finer and slower.")
        f.addRow("Detail (voxel, mm)", self.voxel)
        check = QtGui.QPushButton("Check these settings")
        check.clicked.connect(self.check)
        f.addRow(check)
        self.check_out = QtGui.QLabel("")
        self.check_out.setWordWrap(True)
        self.check_out.setTextFormat(QtCore.Qt.RichText)
        f.addRow(self.check_out)
        self._process_changed()
        return w

    def request(self):
        r = {"target": self.obj_name, "cell": self.cell.currentData(), "material": self.material.currentData(),
             "cell_size_mm": _num(self.cell_size), "min_thickness_mm": _num(self.min_wall),
             "max_thickness_mm": _num(self.max_wall), "skin_mm": _num(self.skin, 1.5),
             "open_faces": [k for k, b in self.open_boxes.items() if b.isChecked()],
             "stiffness_target_pct": self.target.value(), "voxel_mm": _num(self.voxel)}
        if self.mode_faces.isChecked():
            r["fixed"] = [s.strip() for s in self.fixed.text().split(",") if s.strip()]
            r["loads"] = [{"type": "force", "faces": [s.strip() for s in self.loaded.text().split(",") if s.strip()],
                           "newtons": float(sum(v * v for v in (_num(e, 0) for e in self.force)) ** 0.5),
                           "direction": [_num(e, 0) for e in self.force]}]
            if not any(r["loads"][0]["direction"]):
                raise ValueError("The force is zero: give it a size and direction.")
        else:
            r["grading"], r["thickness_mm"] = "uniform", _num(self.uniform_wall, 1.2)
        return {k: v for k, v in r.items() if v is not None}

    def check(self):
        try:
            req = self.request()
            out = (self.client.post("/lattice/estimate", req) if self.team
                   else estimate(dict(req, _bbox=self.bbox)))
        except (ValueError, LatticeJobError, HubRequestError) as e:
            self.check_out.setText(f"<span style='color:#d1242f'>{html.escape(str(e))}</span>")
            return
        lines = []
        if out.get("build_voxels"):
            lines.append(f"About {out['build_voxels'] / 1e6:.1f} M voxels at {out['build_voxel_mm']:g} mm, roughly "
                         f"{out.get('minutes_estimate', '?')} min.")
        for w in out.get("warnings") or []:
            colour = "#9a6700" if w["severity"] == "warning" else "#57606a"
            lines.append(f"<span style='color:{colour}'>• {html.escape(w['text'])}</span>")
        self.check_out.setText("<br>".join(lines) or "Looks good.")

    def choose_cell(self):
        args = {"target": "density", "value": 0.3, "cell_size_mm": _num(self.cell_size, 8),
                "min_wall_mm": _num(self.min_wall, 0.8)}
        try:
            out = self.client.post("/lattice/cells", args) if self.team else cell_library(args)
        except (LatticeJobError, HubRequestError) as e:
            _p().hint(str(e))
            return
        dlg = QtGui.QDialog(self)
        dlg.setWindowTitle("Which lattice cell?")
        lay = QtGui.QVBoxLayout(dlg)
        lay.addWidget(QtGui.QLabel("At 30 % material, how stiff each cell is (lattice stiffness / solid), and "
                                   "the wall it needs. Stiffer for the same weight is better."))
        table = QtGui.QTableWidget(len(out["cells"]), 3)
        table.setHorizontalHeaderLabels(["Cell", "Stiffness vs solid", "Wall (mm)"])
        rows = sorted(out["cells"], key=lambda r: -r["stiffness_ratio"])
        for i, r in enumerate(rows):
            for j, v in enumerate((CELL_WORDS[r["cell"]].split(":")[0], f"{r['stiffness_ratio'] * 100:.1f} %",
                                   f"{r['thickness_mm']:.2f}" + ("" if r["printable"] else " (too thin)"))):
                table.setItem(i, j, QtGui.QTableWidgetItem(v))
        table.resizeColumnsToContents()
        lay.addWidget(table)
        use = QtGui.QPushButton("Use the selected cell")
        use.clicked.connect(lambda: (self.cell.setCurrentIndex(self.cell.findData(rows[max(0, table.currentRow())]
                                                                                    ["cell"])), dlg.accept()))
        lay.addWidget(use)
        (getattr(dlg, "exec", None) or dlg.exec_)()

    # ---------------------------------------------------------------- step 4: run

    def _run_tab(self):
        w = QtGui.QWidget()
        lay = QtGui.QVBoxLayout(w)
        row = QtGui.QHBoxLayout()
        self.run_btn = QtGui.QPushButton("Lighten it")
        self.run_btn.setStyleSheet("QPushButton { font-weight: bold; padding: 6px 14px; }")
        self.run_btn.clicked.connect(self.run)
        self.cancel_btn = QtGui.QPushButton("Stop")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        row.addWidget(self.run_btn)
        row.addWidget(self.cancel_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.bar = QtGui.QProgressBar()
        self.bar.setRange(0, 100)
        lay.addWidget(self.bar)
        self.phase = QtGui.QLabel("")
        lay.addWidget(self.phase)
        self.summary = QtGui.QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(QtCore.Qt.RichText)
        lay.addWidget(self.summary, 1)
        row = QtGui.QHBoxLayout()
        self.preview_btn = QtGui.QPushButton("Show it")
        self.preview_btn.clicked.connect(self.show_preview)
        self.section_btn = QtGui.QPushButton("Cut in half (view only)")
        self.section_btn.setCheckable(True)
        self.section_btn.toggled.connect(self.toggle_section)
        self.apply_btn = QtGui.QPushButton("Add to model")
        self.apply_btn.clicked.connect(self.apply)
        self.files_btn = QtGui.QPushButton("Open files")
        self.files_btn.clicked.connect(self.open_files)
        for b in (self.preview_btn, self.section_btn, self.apply_btn, self.files_btn):
            b.setEnabled(False)
            row.addWidget(b)
        lay.addLayout(row)
        return w

    def _tab_changed(self, i):
        self.back.setEnabled(i > 0)
        self.next.setEnabled(i < 3)

    def run(self):
        try:
            req = self.request()
        except ValueError as e:
            _p().hint(str(e))
            return
        self.summary.setText("")
        for b in (self.preview_btn, self.section_btn, self.apply_btn, self.files_btn):
            b.setEnabled(False)
        try:
            if self.team:
                self.job = self.client.post("/lattice/jobs", req)["job_id"]
            else:
                self.job = LocalJob(App.getDocument(self.doc_name).getObject(self.obj_name), req)
        except (HubRequestError, LatticeJobError, ValueError, fea.FeaInputError) as e:
            QtGui.QMessageBox.warning(self, "CadSwarm", str(e))
            return
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.phase.setText("Starting…")
        self.timer.start(2000)

    def cancel(self):
        if self.team and self.job:
            try:
                self.client.post(f"/lattice/jobs/{self.job}/cancel", {})
            except HubRequestError as e:
                _p().hint(str(e))
        elif isinstance(self.job, LocalJob):
            self.job.cancel_flag = True
        self.phase.setText("Stopping…")

    def poll(self):
        try:
            s = self.client.get(f"/lattice/jobs/{self.job}") if self.team else self.job.status()
        except HubRequestError as e:
            self.phase.setText(f"Can't reach the team server: {e}")
            return
        self.bar.setValue(int(100 * (s.get("progress") or 0)))
        if s.get("note"):
            self.phase.setText(s["note"])
        if s["status"] not in ("done", "failed", "cancelled", "expired"):
            return
        self.timer.stop()
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.last = s
        if s["status"] != "done":
            self.bar.setValue(0)
            self.phase.setText("Stopped." if s["status"] == "cancelled" else "It didn't work.")
            self.summary.setText(f"<span style='color:#d1242f'>{html.escape(str(s.get('error') or ''))}</span>")
            return
        self.bar.setValue(100)
        self.phase.setText("Done.")
        self.summary.setText(self.describe(s))
        for b in (self.preview_btn, self.files_btn):
            b.setEnabled(True)
        self.apply_btn.setEnabled(s.get("verdict") in ("passed", "not_verified"))
        self.show_preview()

    @staticmethod
    def describe(s):
        v, m, t = s.get("verify") or {}, s.get("mass") or {}, s.get("thickness_mm") or {}
        verdict = {"passed": "<b style='color:#1a7f37'>It keeps the stiffness you asked for.</b>",
                   "no_passing_design": "<b style='color:#9a6700'>No lattice kept enough stiffness.</b>",
                   "not_verified": "<b>An even lattice (no load case, so stiffness not checked).</b>"}
        rows = [verdict.get(s.get("verdict"), ""),
                f"<b>{m.get('weight_saving_pct', 0):g} % lighter</b>: {m.get('lattice_g')} g instead of "
                f"{m.get('solid_g')} g."]
        if v:
            rows.append(f"Stiffness kept: <b>{v['stiffness_retained'] * 100:.0f} %</b> (checked on the real "
                        f"lattice). It bends {v['displacement_ratio']:.2f}× as much"
                        + (f"; peak stress {v['stress_ratio']:.2f}×" if v.get("stress_ratio") else "")
                        + (f"; safety factor {v['safety_factor']:.1f}" if v.get("safety_factor") else "") + ".")
        rows.append(f"Walls {t.get('min')}–{t.get('max')} mm.")
        for text in (s.get("warnings") or []) + (s.get("notes") or []):
            rows.append(f"<span style='color:#57606a'>• {html.escape(text)}</span>")
        rows.append("<small>A design aid on a voxel model, not a certification: test printed parts.</small>")
        return "<br>".join(rows)

    # ------------------------------------------------------------- results

    def _preview_bytes(self):
        if self.team:
            path = (self.last or {}).get("folder", "").replace("files/", "", 1) + "/preview.stl"
            return self.client.request("GET", "/project/files/download", raw=True, params={"path": path})
        with open(os.path.join(self.job.job["out_dir"], "preview.stl"), "rb") as f:
            return f.read()

    def show_preview(self):
        import Mesh
        try:
            data = self._preview_bytes()
        except Exception as e:
            self.phase.setText(f"No preview: {e}")
            return
        tmp = os.path.join(App.getUserAppDataDir(), f"cadswarm-lighten-{os.getpid()}.stl")
        with open(tmp, "wb") as f:
            f.write(data)
        if App.listDocuments().get(PREVIEW_DOC):
            App.closeDocument(PREVIEW_DOC)
        doc = App.newDocument(PREVIEW_DOC)
        original = doc.addObject("Part::Feature", "Original")
        original.Shape = self.shape
        lattice = doc.addObject("Mesh::Feature", "Lattice")
        m = Mesh.Mesh(tmp)
        m.translate(self.shape.BoundBox.XLength * 1.2, 0, 0)
        lattice.Mesh = m
        os.remove(tmp)
        if Gui.getDocument(doc.Name):
            original.ViewObject.ShapeColor = (.7, .7, .7)
            original.ViewObject.Transparency = 60
            lattice.ViewObject.ShapeColor = (.2, .6, .85)
            Gui.activeDocument().activeView().viewIsometric()
            Gui.SendMsgToActiveView("ViewFit")
        self.section_btn.setEnabled(True)
        self.section_btn.setChecked(False)

    def toggle_section(self, on):
        """Show the inside through a clipping plane: display only, nothing is cut."""
        view = Gui.activeDocument() and Gui.activeDocument().activeView()
        if view is None or not hasattr(view, "toggleClippingPlane"):
            return
        bb = self.shape.BoundBox
        pla = App.Placement(App.Vector(0, 0, bb.ZMin + bb.ZLength / 2), App.Rotation(App.Vector(1, 0, 0), 180))
        try:
            view.toggleClippingPlane(1 if on else 0, False, True, pla)
        except Exception:
            pass
        self.section_btn.setText("Show it whole" if on else "Cut in half (view only)")

    def apply(self):
        if self.team:
            try:
                out = self.client.post(f"/lattice/jobs/{self.job}/apply", {})
            except HubRequestError as e:
                if getattr(e, "code", "") == "stale_source":
                    if QtGui.QMessageBox.question(self, "CadSwarm", f"{e}\n\nAdd it anyway?") != \
                            QtGui.QMessageBox.Yes:
                        return
                    out = self.client.post(f"/lattice/jobs/{self.job}/apply", {"force": True})
                else:
                    QtGui.QMessageBox.warning(self, "CadSwarm", str(e))
                    return
            _p().say(f"Added {out.get('name')} to the model. {out.get('note', '')}")
        else:
            import Mesh
            doc = App.getDocument(self.doc_name)
            name = f"{self.obj_name}_lattice"
            n = 2
            while doc.getObject(name):
                name, n = f"{self.obj_name}_lattice{n}", n + 1
            obj = doc.addObject("Mesh::Feature", name)
            obj.Mesh = Mesh.Mesh(os.path.join(self.job.job["out_dir"], "result.stl"))
            doc.recompute()
            _p().say(f"Added {name} next to {self.obj_name}. Save the document to keep it.")
        self.apply_btn.setEnabled(False)

    def open_files(self):
        if self.team:  # the files live on the hosting computer: show them in the team's project files
            _p().hint(f"The STL, 3MF and report are in the project files: {(self.last or {}).get('folder')}")
            show = getattr(_p(), "show_project_files", None)
            if show:
                show()
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.job.job["out_dir"]))

    def reject(self):
        if self.timer.isActive():
            if QtGui.QMessageBox.question(self, "CadSwarm", "The job is still running. Stop it and close?") != \
                    QtGui.QMessageBox.Yes:
                return
            self.cancel()
            self.timer.stop()
        if App.listDocuments().get(PREVIEW_DOC):
            App.closeDocument(PREVIEW_DOC)
        super().reject()


_dialogs = []


def show_lighten():
    sel = [o for o in Gui.Selection.getSelection() if hasattr(o, "Shape") and o.Shape.Solids]
    if len(sel) != 1:
        _p().hint("Select the one solid part you want to lighten, then choose Lighten part again.")
        return
    dlg = LightenDialog(sel[0])
    _dialogs.append(dlg)
    dlg.finished.connect(lambda *_: _dialogs.remove(dlg) if dlg in _dialogs else None)
    dlg.show()
    return dlg


class LightenCommand:
    def GetResources(self):
        return {"MenuText": "Lighten part…", "ToolTip": "Fill the selected part with a lattice that is thicker "
                                                        "where it works, and check it keeps its stiffness"}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        show_lighten()


def register_command():
    Gui.addCommand("CadSwarm_Lighten", LightenCommand())
