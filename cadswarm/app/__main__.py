"""CADSwarm desktop app. Run with FreeCAD's bundled Python:  pythonw -m cadswarm.app [files...]

The installer's "CADSwarm" shortcut runs exactly that from the installed CADSwarm folder. Files named on the
command line (or double-clicked with "Open with CADSwarm") open in the CADSwarm window that is already running,
if there is one (see instance.py).

Everything the app prints, and any startup failure, goes to a log file in CADSwarm's app folder
(``<app folder>/logs/cadswarm-app.log``); a launch that can't start shows a dialog saying why.

Test hooks (milestone screenshots and smoke runs; they also skip the hand-off to a running app):
  --demo              build a small sample part after start
  --shot PATH         save a screenshot of the main window to PATH
  --quit-after MS     close the app MS milliseconds after start
  --dark              CADSwarm dark theme
  --new-instance      don't hand files to a running CADSwarm; always start a new window
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

APP_ID = "CADSwarm.Desktop"  # Windows AppUserModelID; the installer's shortcuts carry the same one
FREECAD_DOWNLOAD = "https://www.freecad.org/downloads.php"
LOG_LIMIT = 1_000_000  # bytes; the previous log is kept as .1


def _set_app_id():
    if sys.platform.startswith("win"):
        try:  # own taskbar group and icon instead of python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:  # noqa: BLE001
            pass


def log_path():
    """<CADSwarm app folder>/logs/cadswarm-app.log. A run with its own engine profile (tests, parallel agents)
    logs inside that profile instead, so runs never interleave in one file."""
    if os.environ.get("CADSWARM_LOG"):
        return os.environ["CADSWARM_LOG"]
    if os.environ.get("CADSWARM_ENGINE_HOME"):
        return os.path.join(os.environ["CADSWARM_ENGINE_HOME"], "logs", "cadswarm-app.log")
    from cadswarm.paths import app_dir
    return os.path.join(app_dir(), "logs", "cadswarm-app.log")


class _Tee:
    """sys.stdout/stderr replacement: the log file, plus the console when there is one (not under pythonw)."""

    def __init__(self, log, console):
        self.log, self.console = log, console

    def write(self, text):
        for stream in (self.log, self.console):
            if stream is not None:
                try:
                    stream.write(text)
                    stream.flush()
                except (OSError, ValueError):
                    pass
        return len(text)

    def flush(self):
        pass

    def fileno(self):
        return self.log.fileno()

    def isatty(self):
        return False


def setup_log(path=None):
    """Send stdout/stderr (and native crash tracebacks) to the log file. Returns the open log, or None."""
    import datetime
    import faulthandler
    path = path or log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > LOG_LIMIT:
            os.replace(path, path + ".1")
        log = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None
    log.write(f"\n=== CADSwarm started {datetime.datetime.now().isoformat(timespec='seconds')} "
              f"python={sys.executable} argv={sys.argv[1:]}\n")
    sys.stdout = _Tee(log, sys.__stdout__)
    sys.stderr = _Tee(log, sys.__stderr__)
    try:
        faulthandler.enable(log)  # a crash inside FreeCAD's C++ still leaves the Python stack in the log
    except (OSError, ValueError, RuntimeError):
        pass
    return log


def _osascript_quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def show_error(title, text):
    """A dialog that works before Qt (or FreeCAD) is available. Also printed, so it lands in the log."""
    sys.stderr.write(f"{title}: {text}\n")
    if os.environ.get("CADSWARM_NO_DIALOG"):
        return
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 | 0x10000)  # MB_ICONERROR | MB_SETFOREGROUND
        elif sys.platform == "darwin":
            import subprocess
            script = f"display alert {_osascript_quote(title)} message {_osascript_quote(text)} as critical"
            subprocess.run(["osascript", "-e", script], check=False, timeout=600)
        else:
            import shutil
            import subprocess
            for cmd in (["zenity", "--error", "--title", title, "--text", text],
                        ["kdialog", "--title", title, "--error", text],
                        ["xmessage", "-center", f"{title}\n\n{text}"]):
                if shutil.which(cmd[0]):
                    subprocess.run(cmd, check=False, timeout=600)
                    break
    except Exception:  # noqa: BLE001 - the message is already in the log
        pass


def engine_missing_message(log=None):
    msg = ("CADSwarm runs on FreeCAD (version 1.0 or later) and couldn't find it on this computer.\n\n"
           f"Install FreeCAD from {FREECAD_DOWNLOAD} (the default options are fine), or run the CADSwarm "
           "installer again, which installs it for you. Then start CADSwarm again.")
    if log:
        msg += f"\n\nDetails are in {log}"
    return msg


def find_root():
    from cadswarm.app import engine
    exe_root = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    return exe_root if engine.find_freecad({"CADSWARM_FREECAD": exe_root}) else engine.find_freecad()


def open_files(paths):
    """Open documents in the running GUI. Importable formats (STEP, STL, ...) go through FreeCAD's importers."""
    import FreeCAD
    import FreeCADGui
    for path in paths:
        try:
            if path.lower().endswith(".fcstd"):
                FreeCAD.openDocument(path)
            else:
                FreeCADGui.open(path)
        except Exception as e:  # noqa: BLE001 - one bad file must not take the app down
            sys.stderr.write(f"Couldn't open {path}: {e}\n")


def raise_window(mw):
    if mw.isMinimized():
        mw.showMaximized()
    mw.raise_()
    mw.activateWindow()


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
    doc.recompute()  # Build Face6 before the next sketch attaches to it.
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


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="cadswarm-app")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--shot")
    ap.add_argument("--quit-after", type=int, default=0)
    ap.add_argument("--dark", action="store_true")
    ap.add_argument("--new-instance", action="store_true")
    ap.add_argument("files", nargs="*")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    log = setup_log()
    log_file = log.name if log else None
    try:
        return run(args, log_file)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - a startup failure must be visible, not a silent pythonw exit
        import traceback
        traceback.print_exc()
        last = traceback.format_exc().strip().splitlines()[-1]
        show_error("CADSwarm couldn't start", "Something went wrong while starting CADSwarm:\n\n" + last
                   + (f"\n\nThe full report is in {log_file}" if log_file else ""))
        return 1


def run(args, log_file=None):
    from cadswarm.app import engine, instance
    testing = bool(args.demo or args.shot or args.quit_after)
    single = not (testing or args.new_instance or os.environ.get("CADSWARM_NEW_INSTANCE"))
    if single and instance.forward(engine.engine_home(), args.files):
        print(f"Handed {len(args.files)} file(s) to the CADSwarm window that is already open.")
        return 0

    from cadswarm.app import branded
    root = find_root()
    if not root:
        show_error("CADSwarm can't find FreeCAD", engine_missing_message(log_file))
        return 2
    print(f"FreeCAD engine: {root}")
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
    from PySide6 import QtCore, QtWidgets
    # Qt names the app after the executable ("pythonw"); FreeCAD puts that name in the window title.
    QtCore.QCoreApplication.setApplicationName(branded.APP_NAME)
    QtWidgets.QApplication.setApplicationDisplayName(branded.APP_NAME)
    FreeCADGui.showMainWindow()
    from cadswarm.app.rebrand_qt import Rebrander
    mw = FreeCADGui.getMainWindow()
    Rebrander(mw).install()
    mw.showMaximized()

    server = instance.Server(home) if single else None
    if server is not None and server.start():
        def _handoffs():
            for files in server.drain():
                open_files(files)
                raise_window(mw)
        poll = QtCore.QTimer(mw)
        poll.timeout.connect(_handoffs)
        poll.start(250)

    t = 1500
    if args.demo:
        QtCore.QTimer.singleShot(t, demo_part)
        t += 2000
    if args.files:
        QtCore.QTimer.singleShot(t, lambda: open_files(args.files))
    if args.shot:
        QtCore.QTimer.singleShot(t + 4000, lambda: mw.grab().save(args.shot))
    if args.quit_after:
        def _quit():
            for name in list(FreeCAD.listDocuments()):
                FreeCAD.closeDocument(name)
            QtWidgets.QApplication.instance().quit()
        QtCore.QTimer.singleShot(args.quit_after, _quit)
    try:
        FreeCADGui.exec_loop()
    finally:
        if server is not None:
            server.close()
    print("CADSwarm closed.")
    return 0


if __name__ == "__main__":
    code = main() or 0
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass
    os._exit(code)  # skip interpreter teardown: FreeCAD's GL objects do not survive Python's finalizer
