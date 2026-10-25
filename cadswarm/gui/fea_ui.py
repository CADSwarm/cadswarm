"""Run a simulation (FEA) from the panel, no AI helper needed.

A person picks the part, the material, which faces are held still and the load; the host runs the same
analysis an AI helper's run_fea would (on a copy of the model), then publishes it as that person's report,
which pops up for the whole team. The request rules live in fea.panel_request so they are tested without Qt.
"""
import FreeCADGui as Gui
from PySide import QtGui

from .. import fea
from . import theme

def _p():
    from . import panel
    return panel


class RunSimulationDialog(QtGui.QDialog):
    def __init__(self, session):
        super().__init__(Gui.getMainWindow())
        self.session = session
        self.setWindowTitle("CADSwarm: run a simulation")
        theme.apply(self)
        form = QtGui.QFormLayout(self)
        intro = QtGui.QLabel("Check how a part holds up under a load, without asking a helper. It runs on a copy of "
                             "the model; nothing in the shared model changes. The report pops up for everyone.")
        intro.setWordWrap(True)
        form.addRow(intro)
        self.part = QtGui.QComboBox()
        parts = [o["name"] for o in session.model["objects"] if o["name"] != "Params" and o.get("visible", True)]
        self.part.addItems(parts)
        picked = [n for n in session.selected_names() if n in parts]  # the part selected in the live model
        if picked:
            self.part.setCurrentIndex(parts.index(picked[0]))
        form.addRow("Part", self.part)
        self.analysis = QtGui.QComboBox()
        self.analysis.addItem("Load test: stress, bending and safety factor", "static")
        self.analysis.addItem("Vibration: natural frequencies", "modal")
        self.analysis.currentIndexChanged.connect(self.sync)
        form.addRow("Check", self.analysis)
        self.material = QtGui.QComboBox()
        for key, m in fea.PRESETS.items():
            self.material.addItem(m["name"], key)
        self.material.setCurrentIndex(self.material.findData("pla"))
        form.addRow("Material", self.material)
        self.fixed = QtGui.QLineEdit("zmin")
        self.fixed.setToolTip("Faces held completely still: zmin (bottom), zmax (top), xmin/xmax, ymin/ymax, "
                              "cyl r=2.5 (round holes of that radius), Face3. Separate several with commas.")
        form.addRow("Held still at", self.fixed)
        self.newtons = QtGui.QLineEdit("50")
        self.newtons.setToolTip("1 kg weighs about 10 N")
        form.addRow("Force (N)", self.newtons)
        self.direction = QtGui.QComboBox()
        self.direction.addItems(list(fea.DIRECTIONS))
        form.addRow("Pushing", self.direction)
        self.load_faces = QtGui.QLineEdit("zmax")
        self.load_faces.setToolTip("The faces the force pushes on, same words as Held still at")
        form.addRow("On faces", self.load_faces)
        self.error = QtGui.QLabel("")
        self.error.setStyleSheet("color: #ff7b72")
        self.error.setWordWrap(True)
        form.addRow(self.error)
        buttons = QtGui.QDialogButtonBox()
        self.run_button = buttons.addButton("Run", QtGui.QDialogButtonBox.AcceptRole)
        buttons.addButton(QtGui.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.run)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        if not parts:
            self.error.setText("There are no parts in the model yet.")
            self.run_button.setEnabled(False)
        self.sync()

    def sync(self):
        static = self.analysis.currentData() == "static"
        for w in (self.newtons, self.direction, self.load_faces):
            w.setEnabled(static)

    def request(self):
        return fea.panel_request(self.part.currentText(), self.analysis.currentData(), self.material.currentData(),
                                 self.fixed.text(), self.newtons.text(), self.direction.currentText(),
                                 self.load_faces.text())

    def run(self):
        try:
            body = self.request()
        except fea.FeaInputError as e:
            self.error.setText(str(e))
            return
        panel = _p()
        person = panel.prefs().GetString("Name") or "a teammate"

        def opened(r):
            folder = (r or {}).get("folder", "").removeprefix("files/")
            if folder:
                panel.open_fea_report(folder)
        self.session.run_async(lambda c: fea.run_and_publish(c, body, person), done=opened,
                               purpose="run a simulation")
        panel.say(f"Simulating {body['target']}. The report pops up when it's done (usually under a minute).")
        self.accept()


def show_run_simulation():
    session = _p().SESSION
    if not session.ensure_connected("run a simulation"):
        return
    dlg = RunSimulationDialog(session)
    (getattr(dlg, "exec", None) or dlg.exec_)()
