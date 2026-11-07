"""Drives the mouse controls on a real 3D view in FreeCAD with synthetic mouse events. Needs a real
display: moving the camera makes Coin redraw, which crashes without a GL context (offscreen Qt).

    python navigation_gui_check.py <out dir>

Writes navigation-verified.json. Run by test_navigation.py. A recorder filter sits behind ours on the
viewport and notes what FreeCAD's own navigation would have received.
"""
import json
import math
import sys
import time
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui

Gui.showMainWindow()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _name in [n for n in sys.modules if n == "cadswarm" or n.startswith("cadswarm.")]:
    del sys.modules[_name]
from pivy import coin  # noqa: E402,F401 - getCameraNode() needs pivy's SWIG types
from PySide import QtCore, QtGui, QtWidgets  # noqa: E402

from cadswarm.gui import navigation  # noqa: E402

Qt = QtCore.Qt
Ev = QtCore.QEvent
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)


def wait(seconds=0.1):
    for _ in range(max(1, int(seconds / 0.02))):
        QtCore.QCoreApplication.processEvents()
        time.sleep(0.02)


mw = Gui.getMainWindow()
mw.resize(1200, 900)
doc = App.newDocument("Nav")
box = doc.addObject("Part::Box", "Box")
box.Length = box.Width = box.Height = 10
doc.recompute()
view = Gui.getDocument("Nav").mdiViewsOfType("Gui::View3DInventor")[0]
view.viewIsometric()
view.fitAll()
wait(0.5)
viewport = view.graphicsView().viewport()


class Recorder(QtCore.QObject):
    names = {Ev.MouseButtonPress: "press", Ev.MouseMove: "move", Ev.MouseButtonRelease: "release"}

    def __init__(self):
        super().__init__(viewport)
        self.seen = []

    def eventFilter(self, obj, event):
        name = self.names.get(event.type())
        if name:
            self.seen.append(name)
        return False


recorder = Recorder()
viewport.installEventFilter(recorder)  # installed first, so it runs after the mouse controls
navigation.install()  # what InitGui calls at FreeCAD start: it attaches to the views already open
assert viewport.property("cadswarmMouse"), "mouse controls not attached"
navigation.attach()  # a second call must not stack a second filter


def send(kind, pos, button=Qt.NoButton, buttons=Qt.NoButton, mods=Qt.NoModifier):
    pos = QtCore.QPointF(*pos)
    event = QtGui.QMouseEvent(kind, pos, QtCore.QPointF(viewport.mapToGlobal(pos.toPoint())), button, buttons, mods)
    QtWidgets.QApplication.sendEvent(viewport, event)


def drag(button, start, end, steps=8, mods=Qt.NoModifier):
    recorder.seen = []
    send(Ev.MouseButtonPress, start, button, button, mods)
    for i in range(1, steps + 1):
        t = i / steps
        send(Ev.MouseMove, (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t),
             Qt.NoButton, button, mods)
    send(Ev.MouseButtonRelease, end, button, Qt.NoButton, mods)
    wait()
    return list(recorder.seen)


def camera():
    cam = view.getCameraNode()
    return (tuple(cam.position.getValue().getValue()), tuple(cam.orientation.getValue().getValue()),
            cam.focalDistance.getValue())


def focal(cam):
    pos, rot, dist = cam
    look = navigation.rotate(rot, (0.0, 0.0, -1.0))
    return tuple(p + d * dist for p, d in zip(pos, look))


def coin_xy(x, y):
    dpr = viewport.devicePixelRatioF()
    return int(x * dpr), int(view.getSize()[1]) - 1 - int(y * dpr)


def turned(a, b):
    return any(abs(x - y) > 1e-6 for x, y in zip(a[1], b[1]))


w, h = viewport.width(), viewport.height()
corner = (30.0, 30.0)  # empty space, away from the box in the middle
centre = (w / 2.0, h / 2.0)
assert view.getObjectInfo(coin_xy(*corner)) is None, "corner should be empty"
assert view.getObjectInfo(coin_xy(*centre)) is not None, "the box should be in the middle"
result = {"viewport": [w, h]}

before = camera()
grabbed = view.getPointOnFocalPlane(*coin_xy(*corner))
end = (corner[0] + 120, corner[1] + 80)
seen = drag(Qt.LeftButton, corner, end)
after = camera()
under = view.getPointOnFocalPlane(*coin_xy(*end))
result["pan"] = {"moved": math.dist(before[0], after[0]), "turned": turned(before, after),
                 "grabbed_point_error": (grabbed - under).Length, "freecad_saw": seen}

before = camera()
seen = drag(Qt.RightButton, centre, (centre[0] + 150, centre[1] - 60))
after = camera()
result["orbit"] = {"turned": turned(before, after), "freecad_saw": seen,
                   "focal_kept": math.dist(focal(before), focal(after)) < 1e-3 * before[2]}

for name, button in (("left_click", Qt.LeftButton), ("right_click", Qt.RightButton)):
    pos = corner if button == Qt.LeftButton else (w - 30.0, h - 30.0)
    recorder.seen = []
    send(Ev.MouseButtonPress, pos, button, button)
    send(Ev.MouseButtonRelease, pos, button, Qt.NoButton)
    wait(0.3)
    menu = QtWidgets.QApplication.activePopupWidget()
    if menu is not None:
        menu.close()
    result[name] = {"freecad_saw": list(recorder.seen)}

view.viewIsometric()  # the drags above moved the box away from the middle
view.fitAll()
wait(0.3)
assert view.getObjectInfo(coin_xy(*centre)) is not None, "the box should be back in the middle"
before = camera()
seen = drag(Qt.LeftButton, centre, (centre[0] + 5, centre[1] + 5), steps=2)
result["on_part"] = {"freecad_saw": seen, "moved": math.dist(before[0], camera()[0])}
Gui.Selection.clearSelection()

seen = drag(Qt.LeftButton, corner, (corner[0] + 60, corner[1]), mods=Qt.ControlModifier)
result["ctrl_drag"] = {"freecad_saw": seen}

navigation.set_enabled(False)
before = camera()
seen = drag(Qt.LeftButton, corner, (corner[0] + 60, corner[1]))
result["disabled"] = {"freecad_saw": seen, "moved": math.dist(before[0], camera()[0])}

(out / "navigation-verified.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
App.closeDocument("Nav")
QtCore.QTimer.singleShot(0, QtWidgets.QApplication.quit)
