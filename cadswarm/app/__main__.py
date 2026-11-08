"""CADSwarm desktop app. Run with FreeCAD's bundled Python:  pythonw -m cadswarm.app [files...]

Test hooks (milestone screenshots and smoke runs):
  --demo              build a small sample part after start
  --shot PATH         save a screenshot of the main window to PATH
  --quit-after MS     close the app MS milliseconds after start
  --dark              CADSwarm dark theme
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _set_app_id():
    if sys.platform.startswith("win"):
        try:  # own taskbar group and icon instead of python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CADSwarm.Desktop")
        except Exception:  # noqa: BLE001
            pass


def find_root():
    from cadswarm.app import engine
    exe_root = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    return exe_root if engine.find_freecad({"CADSWARM_FREECAD": exe_root}) else engine.find_freecad()


def demo_part():
    import FreeCAD as App
    import FreeCADGui as Gui
    import Part
    import Sketcher
    doc = App.newDocument("Bracket")
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "BaseSketch")
    sk.AttachmentSupport = (doc.getObject("XY_Plane"), [""])
    sk.MapMode = "FlatFace"
    pts = [(-40, -25), (40, -25), (40, 25), (-40, 25)]
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        sk.addGeometry(Part.LineSegment(App.Vector(*a, 0), App.Vector(*b, 0)))
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 12
    hole_sk = body.newObject("Sketcher::SketchObject", "HoleSketch")
    hole_sk.AttachmentSupport = (pad, ["Face6"])
    hole_sk.MapMode = "FlatFace"
    for x in (-25, 25):
        hole_sk.addGeometry(Part.Circle(App.Vector(x, 0, 0), App.Vector(0, 0, 1), 6))
    pocket = body.newObject("PartDesign::Pocket", "Holes")
    pocket.Profile = hole_sk
    pocket.Type = 1
    doc.recompute()
    for o in (sk, hole_sk):
        o.Visibility = False
    Gui.activeDocument().activeView().viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cadswarm-app")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--shot")
    ap.add_argument("--quit-after", type=int, default=0)
    ap.add_argument("--dark", action="store_true")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv)

    from cadswarm.app import branded, engine
    root = find_root()
    if not root:
        sys.stderr.write("CADSwarm needs its geometry engine (FreeCAD 1.0 or later) and could not find it.\n")
        return 2
    home = engine.prepare(root)
    _set_app_id()
    mode = "dark" if args.dark else os.environ.get("CADSWARM_THEME", "light")
    theme_file = branded.write_theme(root, home, mode)
    branded.link_addon(home)

    import FreeCAD
    for k, v in branded.BRAND_CONFIG.items():
        FreeCAD.ConfigSet(k, v)
    engine.apply_params(FreeCAD, branded.brand_params(theme_file, mode))

    import FreeCADGui
    FreeCADGui.showMainWindow()
    from PySide6 import QtCore, QtWidgets
    from cadswarm.app.rebrand_qt import Rebrander
    mw = FreeCADGui.getMainWindow()
    Rebrander(mw).install()
    mw.showMaximized()

    t = 1500
    if args.demo:
        QtCore.QTimer.singleShot(t, demo_part)
        t += 2000
    for path in args.files:
        QtCore.QTimer.singleShot(t, lambda p=path: FreeCAD.openDocument(p))
    if args.shot:
        QtCore.QTimer.singleShot(t + 4000, lambda: mw.grab().save(args.shot))
    if args.quit_after:
        def _quit():
            for name in list(FreeCAD.listDocuments()):
                FreeCAD.closeDocument(name)
            QtWidgets.QApplication.instance().quit()
        QtCore.QTimer.singleShot(args.quit_after, _quit)
    FreeCADGui.exec_loop()
    return 0


if __name__ == "__main__":
    os._exit(main() or 0)  # skip interpreter teardown: FreeCAD's GL objects do not survive Python's finalizer
