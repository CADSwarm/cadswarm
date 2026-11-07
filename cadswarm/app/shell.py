"""CADSwarm's main window (prototype shell: proves the engine adoption; the full workspace replaces it)."""
from PySide6 import QtCore, QtWidgets


class Shell(QtWidgets.QMainWindow):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.setWindowTitle("CADSwarm")
        self.resize(1500, 950)
        bar = QtWidgets.QLabel("  CADSWARM   FILE  EDIT  VIEW   |   PART  SKETCH  ASSEMBLY  DRAWING  AI BUILD")
        bar.setStyleSheet("background:#000;color:#fff;font:600 13px 'Consolas';padding:8px;border-bottom:2px solid #ff5a1f")
        self.setMenuWidget(bar)
        mdi, tasks = engine.adopt()
        self.setCentralWidget(mdi)
        right = QtWidgets.QWidget()
        right.setMinimumWidth(340)
        lay = QtWidgets.QVBoxLayout(right)
        lay.setContentsMargins(0, 0, 0, 0)
        if tasks is not None:
            lay.addWidget(tasks)
            tasks.show()
        dock = QtWidgets.QDockWidget("PROPERTIES", self)
        dock.setWidget(right)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.status = QtWidgets.QLabel("READY")
        self.statusBar().addWidget(self.status, 1)
        engine.status_signal().connect(lambda m: self.status.setText(m or "READY"))

    def open_path(self, path):
        self.engine.App.openDocument(path)

    def demo_part(self):
        App, Gui = self.engine.App, self.engine.Gui
        import Part
        import Sketcher
        doc = App.newDocument("Part1")
        body = doc.addObject("PartDesign::Body", "Body")
        sk = body.newObject("Sketcher::SketchObject", "Sketch")
        sk.AttachmentSupport = [(doc.getObject("XY_Plane"), "")]
        sk.MapMode = "FlatFace"
        V = App.Vector
        pts = [V(0, 0, 0), V(40, 0, 0), V(40, 25, 0), V(0, 25, 0)]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
        for i in range(4):
            sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sk
        pad.Length = 10
        doc.recompute()
        v = Gui.ActiveDocument.ActiveView
        v.viewIsometric()
        v.fitAll()

    def closeEvent(self, ev):
        self.engine.shutdown()
        super().closeEvent(ev)
