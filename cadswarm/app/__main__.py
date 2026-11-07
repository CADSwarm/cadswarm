"""CADSwarm desktop app entry point. Run with FreeCAD's bundled Python:  pythonw -m cadswarm.app

Test hooks (used for milestone screenshots and CI smoke runs):
  --demo              build a small sample part after start
  --shot PATH         save a screenshot of the window to PATH (after the demo, if any)
  --quit-after MS     close the app MS milliseconds after start
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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cadswarm-app")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--shot")
    ap.add_argument("--quit-after", type=int, default=0)
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv)

    from cadswarm.app import engine
    exe_root = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    root = exe_root if engine.find_freecad({"CADSWARM_FREECAD": exe_root}) else engine.find_freecad()
    if not root:
        sys.stderr.write("CADSwarm needs its geometry engine (FreeCAD 1.0 or later) and could not find it.\n")
        return 2
    engine.prepare(root)
    _set_app_id()

    from PySide6 import QtCore, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("CADSwarm")
    app.setOrganizationName("CADSwarm")

    from cadswarm.app.shell import Shell
    eng = engine.Engine().boot()
    shell = Shell(eng)
    shell.show()

    t = 600
    if args.demo:
        QtCore.QTimer.singleShot(t, shell.demo_part)
        t += 1500
    for path in args.files:
        QtCore.QTimer.singleShot(t, lambda p=path: shell.open_path(p))
    if args.shot:
        QtCore.QTimer.singleShot(t + 500, lambda: shell.grab().save(args.shot))
    if args.quit_after:
        QtCore.QTimer.singleShot(args.quit_after, shell.close)
    code = app.exec()
    eng.shutdown()
    return code


if __name__ == "__main__":
    os._exit(main() or 0)  # skip interpreter teardown: FreeCAD's GL objects do not survive Python's finalizer
