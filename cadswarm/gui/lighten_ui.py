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
import time
import uuid

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

from .. import fea
from ..client import HubRequestError
from ..files import MAX_BINARY_MB
from ..lattice_jobs import CELLS, LatticeJobError, LatticeJobs, cell_library, estimate, freecad_python
from . import theme

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
        self.log = []
        self.started = time.time()
        self.finished = None
        self.cancel_flag = False
        self.result = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        def on_progress(p):
            entry = {"at": p.get("at") or time.time(), "phase": p.get("phase"),
                     "text": p.get("text") or "", "fraction": p.get("fraction")}
            self.log = (self.log + [entry])[-400:]
            self.state.update(status="generating", phase=p.get("phase"), progress=p.get("fraction"),
                              note=p.get("text"))
        try:
            self.result = LatticeJobs._run_process(freecad_python(), self.job, self.work, on_progress,
                                                   lambda: self.cancel_flag)
        except Exception as e:
            self.result = {"ok": False, "code": "lattice_error", "error": f"{type(e).__name__}: {e}"}
        self.finished = time.time()
        if self.result.get("ok"):
            self.state["status"] = "done"
        else:
            self.state["status"] = "cancelled" if self.result.get("code") == "cancelled" else "failed"

    def status(self):
        log = list(self.log)
        out = dict(self.state, job_id=self.id, log=log, started_at=self.started, finished_at=self.finished,
                   updated_at=log[-1].get("at") if log else None,
                   elapsed_s=round((self.finished or time.time()) - self.started, 1))
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
        theme.apply(self)
        self.setWindowTitle("CadSwarm: Lighten part")
        from .panel import fit_to_screen, SESSION
        from ..naming import is_mirror_doc
        fit_to_screen(self, 720, 760)
        self.obj_name, self.doc_name = obj.Name, obj.Document.Name
        self.team = is_mirror_doc(self.doc_name) and SESSION.connected
        self.client = SESSION.client if self.team else None
        self.shape = obj.Shape.copy()
        self.job = None          # team: job id; local: LocalJob
        self.last = None
        self._log_at = None      # newest progress line shown, so a poll never repeats one
        self._note = None        # latest note, for a server too old to send the log
        self._started = time.time()
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
            self.check_out.setText(f"<span style='color:#ff7b72'>{html.escape(str(e))}</span>")
            return
        lines = []
        if out.get("build_voxels"):
            lines.append(f"About {out['build_voxels'] / 1e6:.1f} M voxels at {out['build_voxel_mm']:g} mm, roughly "
                         f"{out.get('minutes_estimate', '?')} min.")
        big = self._mesh_size_note(out.get("build_voxel_mm"))
        if big:
            lines.append(f"<span style='color:#ff7b72'>• {big}</span>")
        for w in out.get("warnings") or []:
            colour = "#e3b341" if w["severity"] == "warning" else "#b9b5af"
            lines.append(f"<span style='color:{colour}'>• {html.escape(w['text'])}</span>")
        self.check_out.setText("<br>".join(lines) or "Looks good.")

    def _mesh_size_note(self, voxel_mm):
        """Warn before a long run when the STL would be too big for the project files (MAX_BINARY_MB per file)."""
        if not self.team:
            return ""  # on this computer the files are not project files, so the limit does not apply
        try:
            voxel_mm = float(voxel_mm)
            volume, area = abs(self.shape.Volume), self.shape.Area
            cell = _num(self.cell_size, 8) or 8
        except (TypeError, ValueError, AttributeError):
            return ""
        if voxel_mm <= 0:
            return ""
        # A stress-graded TPMS at about 30 % material has ~3 mm² of wall per mm³ of cell; marching cubes
        # makes ~2 triangles per voxel of surface and a binary STL is 50 bytes per triangle.
        megabytes = 2 * (area + 3 * volume / cell) / voxel_mm ** 2 * 50 / 1e6
        warn_at = MAX_BINARY_MB * 0.9  # leave room for the 3MF and the report in the same folder
        if megabytes <= warn_at:
            return ""
        fit = voxel_mm * (megabytes / warn_at) ** 0.5
        return (f"At {voxel_mm:.2f} mm detail the STL will be roughly {megabytes:.0f} MB. Project files take at "
                f"most {MAX_BINARY_MB} MB per file, so it may be too big to save. Use Detail {fit:.2f} mm or "
                "more, or a smaller part.")

    def choose_cell(self):
        args = {"target": "density", "value": 0.3, "cell_size_mm": _num(self.cell_size, 8),
                "min_wall_mm": _num(self.min_wall, 0.8)}
        try:
            out = self.client.post("/lattice/cells", args) if self.team else cell_library(args)
        except (LatticeJobError, HubRequestError) as e:
            _p().hint(str(e))
            return
        dlg = QtGui.QDialog(self)
        theme.apply(dlg)
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
        lay.addWidget(self.summary)
        head = QtGui.QHBoxLayout()
        head.addWidget(QtGui.QLabel("Activity"))
        head.addStretch(1)
        self.activity = QtGui.QLabel("")
        self.activity.setObjectName("hint")
        head.addWidget(self.activity)
        lay.addLayout(head)
        self.log_view = QtGui.QPlainTextEdit()
        self.log_view.setObjectName("code")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QtGui.QPlainTextEdit.NoWrap)
        self.log_view.setMaximumBlockCount(600)
        self.log_view.setPlaceholderText("What the job is doing appears here while it runs.")
        lay.addWidget(self.log_view, 1)
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
        self.log_view.clear()
        self._log_at = None
        self._note = None
        self._started = time.time()
        self.activity.setText("")
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
        where = f"the team server, job {self.job}" if self.team else f"this computer, job {self.job.id}"
        self._add_log(self._started, f"Started on {where}.")
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
        self._add_log(time.time(), "Stop asked for.")
        self.phase.setText("Stopping…")

    def _add_log(self, at, text):
        """Append one timestamped line, following the tail unless the person scrolled up to read."""
        if not text:
            return
        bar = self.log_view.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 2
        stamp = time.strftime("%H:%M:%S", time.localtime(at)) if at else "--:--:--"
        self.log_view.appendPlainText(f"{stamp}  {text}")
        if follow:
            bar.setValue(bar.maximum())

    def _show_log(self, s):
        entries = s.get("log") or []
        if entries:
            newest = self._log_at
            for e in entries:
                at = e.get("at")
                if newest is None or (isinstance(at, (int, float)) and at > newest):
                    self._add_log(at, e.get("text"))
                    if isinstance(at, (int, float)):
                        newest = at
            self._log_at = newest
        elif s.get("note") and s["note"] != self._note:  # a team server from before the log field
            self._add_log(time.time(), s["note"])
        self._note = s.get("note")

    @staticmethod
    def _duration(seconds):
        seconds = int(max(0, seconds or 0))
        if seconds < 3600:
            return f"{seconds // 60}:{seconds % 60:02d}"
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"

    def _update_activity(self, s, ended=False):
        elapsed = s.get("elapsed_s")
        if elapsed is None:
            elapsed = time.time() - (s.get("started_at") or self._started)
        if ended:
            self.activity.setText(f"finished in {self._duration(elapsed)}")
            return
        updated = s.get("updated_at")
        age = time.time() - updated if updated else elapsed
        self.activity.setText(f"running {self._duration(elapsed)} · updated {self._duration(age)} ago")

    def poll(self):
        try:
            s = self.client.get(f"/lattice/jobs/{self.job}") if self.team else self.job.status()
        except HubRequestError as e:
            self.phase.setText(f"Can't reach the team server: {e}")
            return
        self.bar.setValue(int(100 * (s.get("progress") or 0)))
        if s.get("note"):
            self.phase.setText(s["note"])
        self._show_log(s)
        self._update_activity(s)
        if s["status"] not in ("done", "failed", "cancelled", "expired"):
            return
        self.timer.stop()
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.last = s
        self._update_activity(s, ended=True)
        if s["status"] != "done":
            self.bar.setValue(0)
            self.phase.setText("Stopped." if s["status"] == "cancelled" else "It didn't work.")
            self.summary.setText(f"<span style='color:#ff7b72'>{html.escape(str(s.get('error') or ''))}</span>")
            # A failed publish keeps the lattice files: let the person reach them.
            self.files_btn.setEnabled(bool(s.get("out_dir")) or isinstance(self.job, LocalJob))
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
        verdict = {"passed": "<b style='color:#4fb35a'>It keeps the stiffness you asked for.</b>",
                   "no_passing_design": "<b style='color:#e3b341'>No lattice kept enough stiffness.</b>",
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
            rows.append(f"<span style='color:#b9b5af'>• {html.escape(text)}</span>")
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
            # The lattice fills the same space as the part, so showing both makes their faces flicker into each other.
            source = doc.getObject(self.obj_name)
            if source is not None and getattr(source, "ViewObject", None):
                source.ViewObject.Visibility = False
            doc.recompute()
            _p().say(f"Added {name} in place of {self.obj_name}, which is hidden and unchanged. "
                     "Save the document to keep it.")
        self.apply_btn.setEnabled(False)

    def open_files(self):
        if self.team:  # the files live on the hosting computer: show them in the team's project files
            folder = (self.last or {}).get("folder")
            if not folder:
                out = (self.last or {}).get("out_dir")
                _p().hint(f"The job could not save the lattice into the project files. Its files are still on "
                          f"the hosting computer in {out}." if out else
                          "The job did not save any files.")
                return
            _p().hint(f"The STL, 3MF and report are in the project files: {folder}")
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
