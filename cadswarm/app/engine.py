"""FreeCAD as a geometry engine behind CADSwarm's own window.

CADSwarm is its own Qt application. FreeCAD's GUI library is started inside our process (FreeCAD's bundled
Python runs us), its main window is created but never shown, and we adopt the two FreeCAD widgets we need:

* the MDI area (the interactive 3D views: picking, preselection, sketch editing, view cube), and
* the "Tasks" dock (the task panel FreeCAD's sketcher and feature dialogs open into).

Everything else (menus, toolbars, tree, property editor, start page, workbench switcher, splash) is ours.

Two traps this module exists to avoid:

* ``DockWindowManager.getDockWindow("Tasks")`` returns the dock's *current* widget. Taking the TaskView out of its
  QDockWidget makes that lookup return null and no task dialog ever opens (sketch edit shows no panel). So we
  re-parent the whole QDockWidget, title bar hidden, as a plain child of our layout.
* FreeCAD's first-run page and Start page open as MDI tabs. They are switched off in our own parameter file before
  the GUI starts; FreeCAD's settings for CADSwarm live in CADSwarm's app folder, never the user's FreeCAD profile.

Stdlib only at import time, so tests and the launcher can import it without FreeCAD.
"""
import glob
import os
import sys

# Parameter presets written before FreeCAD's GUI starts: (group, type, key, value).
ENGINE_PARAMS = [
    ("BaseApp/Preferences/Mod/Start", "Bool", "ShowOnStartup", False),
    ("BaseApp/Preferences/Mod/Start", "Bool", "FirstStart2024", False),
    ("BaseApp/Preferences/Mod/Start", "Bool", "CloseStart", True),
    ("BaseApp/Preferences/General", "String", "AutoloadModule", "PartDesignWorkbench"),
    ("BaseApp/Preferences/MainWindow", "Bool", "StatusBar", False),
    ("BaseApp/Preferences/View", "Bool", "ShowFPS", False),
    ("BaseApp/Preferences/View", "Bool", "UseVBO", True),
    ("BaseApp/Preferences/Document", "Bool", "CreateBackupFiles", True),
    ("BaseApp/Preferences/Units", "Int", "UserSchema", 0),  # mm, kg, s, deg
    ("BaseApp/Preferences/Units", "Int", "Decimals", 2),
]


def candidate_roots(env=None, platform=None):
    """FreeCAD install folders to try, best first. CADSWARM_FREECAD (a folder or its executable) wins."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    out = []
    explicit = env.get("CADSWARM_FREECAD")
    if explicit:
        p = explicit
        for _ in range(3):  # accept .../bin/freecad.exe, .../bin or the root
            if os.path.isdir(os.path.join(p, "bin")) or os.path.isdir(os.path.join(p, "Contents")):
                break
            p = os.path.dirname(p)
        out.append(p)
    if platform.startswith("win"):
        for base in (env.get("LOCALAPPDATA", "") + "\\Programs", env.get("ProgramFiles", ""), env.get("ProgramFiles(x86)", "")):
            if base.strip("\\"):
                out += sorted(glob.glob(os.path.join(base, "FreeCAD*")), key=_version_key, reverse=True)
    elif platform == "darwin":
        out += ["/Applications/FreeCAD.app", os.path.expanduser("~/Applications/FreeCAD.app")]
    else:
        from cadswarm.paths import app_dir
        out += sorted(glob.glob(os.path.join(app_dir(), "FreeCAD-*", "usr")), key=_version_key, reverse=True)
        out += ["/usr/lib/freecad", "/usr/local/lib/freecad"]
    return out


def _version_key(path):
    import re
    m = re.search(r"(\d+(?:\.\d+)+)", os.path.basename(os.path.dirname(path)) + os.path.basename(path))
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)


def lib_dirs(root, platform=None):
    """Folders that hold FreeCAD.pyd/.so for an install root."""
    platform = platform or sys.platform
    if platform == "darwin":
        res = os.path.join(root, "Contents", "Resources")
        return [os.path.join(res, "lib"), os.path.join(res, "bin")]
    return [os.path.join(root, "bin"), os.path.join(root, "lib")]


def find_freecad(env=None, platform=None):
    for root in candidate_roots(env, platform):
        for d in lib_dirs(root, platform):
            if glob.glob(os.path.join(d, "FreeCAD.*")) and (glob.glob(os.path.join(d, "FreeCADGui.*"))):
                return root
    return None


def engine_home():
    from cadswarm.paths import app_dir
    return os.path.join(app_dir(), "engine")


def prepare(root):
    """Make FreeCAD importable and point its settings at CADSwarm's own folder. Call before importing FreeCAD."""
    home = engine_home()
    os.makedirs(home, exist_ok=True)
    os.environ["FREECAD_USER_HOME"] = home
    for d in reversed(lib_dirs(root)):
        if os.path.isdir(d) and d not in sys.path:
            sys.path.insert(0, d)
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(d)
    return home


def apply_params(FreeCAD, params=ENGINE_PARAMS):
    for group, kind, key, value in params:
        getattr(FreeCAD.ParamGet("User parameter:" + group), "Set" + kind)(key, value)


class Engine:
    """Owns FreeCAD's hidden main window and hands its 3D views and task panel to our shell."""

    def __init__(self):
        self.App = None
        self.Gui = None
        self.fc_window = None
        self.mdi = None
        self.tasks = None

    def boot(self):
        import FreeCAD
        import FreeCADGui
        apply_params(FreeCAD)
        FreeCADGui.showMainWindow()
        self.App, self.Gui = FreeCAD, FreeCADGui
        self.fc_window = FreeCADGui.getMainWindow()
        self.fc_window.hide()
        try:
            self.Gui.activateWorkbench("PartDesignWorkbench")
        except Exception:  # noqa: BLE001 - a missing module must not stop the app
            pass
        return self

    def adopt(self):
        """Detach the MDI area and the Tasks dock from FreeCAD's window. Returns (mdi_area, tasks_dock)."""
        from PySide6 import QtWidgets
        fcw = self.fc_window
        self.mdi = fcw.centralWidget()
        self.mdi.setParent(None)
        for dock in fcw.findChildren(QtWidgets.QDockWidget):
            if dock.objectName() == "Tasks":
                fcw.removeDockWidget(dock)
                dock.setTitleBarWidget(QtWidgets.QWidget())
                dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
                self.tasks = dock
            else:
                dock.hide()
        return self.mdi, self.tasks

    def status_signal(self):
        """FreeCAD's own status-bar messages (preselection info, command hints)."""
        return self.fc_window.statusBar().messageChanged

    def shutdown(self):
        """Close documents without prompting (the shell asks about unsaved work first), then let FreeCAD go."""
        if self.App is None:
            return
        try:
            if self.Gui.ActiveDocument and self.Gui.ActiveDocument.getInEdit():
                self.Gui.ActiveDocument.resetEdit()
        except Exception:  # noqa: BLE001
            pass
        for name in list(self.App.listDocuments()):
            try:
                self.App.closeDocument(name)
            except Exception:  # noqa: BLE001
                pass
