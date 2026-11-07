"""CADSwarm's main window: the regions, and the Context that feature packages register into.

    +------------------------------------------------------------------+
    | title bar (chrome package)                                        |  menuWidget: top stack
    | ribbon (ribbon package)                                           |
    +-----------+------------------------------------------+-----------+
    | LEFT PANE | viewport: FreeCAD MDI area + overlays     | TASK PANE |
    | tree /    | (view cube, heads-up bar, filters,        | AI BUILD  |
    | property  |  teammate pointers)                       | TEAM ...  |
    | panel     |                                           |           |
    +-----------+------------------------------------------+-----------+
    | status bar                                                        |
    +------------------------------------------------------------------+

Packages are imported in PACKAGES order and their ``install(ctx)`` called. A package that fails to import or
install is logged and skipped: the app still opens.
"""
import importlib
import os
import traceback

from PySide6 import QtCore, QtGui, QtWidgets

from cadswarm.app import api, events, theme

PACKAGES = ["fonts", "chrome", "files", "nav", "ribbon", "tree", "props", "sketch", "evaluate", "library",
            "build", "team"]


def log(msg):
    path = os.path.join(os.environ.get("FREECAD_USER_HOME", "."), "cadswarm-app.log")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except OSError:
        pass


class Pane(QtWidgets.QWidget):
    """A side pane: tab pages, and (left pane) a property page that temporarily replaces them."""

    def __init__(self, name):
        super().__init__()
        self.setObjectName(name)
        self.stack = QtWidgets.QStackedWidget()
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)
        self.stack.addWidget(self.tabs)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.stack)
        self._pages = []  # (order, id, label, widget)

    def add_page(self, pid, label, widget, order=50):
        self._pages.append((order, pid, label, widget))
        self._pages.sort(key=lambda p: p[0])
        idx = [p[1] for p in self._pages].index(pid)
        self.tabs.insertTab(idx, widget, label)

    def show_page(self, pid):
        for _, p, _, w in self._pages:
            if p == pid:
                self.stack.setCurrentWidget(self.tabs)
                self.tabs.setCurrentWidget(w)
                return True
        return False

    def push(self, widget):
        if self.stack.indexOf(widget) < 0:
            self.stack.addWidget(widget)
        self.stack.setCurrentWidget(widget)

    def pop(self, widget=None):
        if widget is not None and self.stack.indexOf(widget) >= 0:
            self.stack.removeWidget(widget)
        self.stack.setCurrentWidget(self.tabs)


class Viewport(QtWidgets.QWidget):
    """Holds FreeCAD's MDI area and keeps overlay widgets pinned to its corners."""

    CORNERS = ("top-left", "top", "top-right", "bottom-left", "bottom", "bottom-right")

    def __init__(self, mdi):
        super().__init__()
        self.setObjectName("Viewport")
        self.mdi = mdi
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(mdi)
        self.overlays = []  # (widget, corner, margin)

    def add_overlay(self, widget, corner="top", margin=12):
        widget.setParent(self)
        self.overlays.append((widget, corner, margin))
        widget.show()
        widget.raise_()
        self._place()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place()

    def _place(self):
        W, H = self.width(), self.height()
        tab_h = 30  # leave the document tab strip visible
        for w, corner, m in self.overlays:
            w.adjustSize()
            s = w.sizeHint()
            x = {"left": m, "right": W - s.width() - m}.get(corner.split("-")[-1], (W - s.width()) // 2)
            if corner.startswith("bottom"):
                y = H - s.height() - m - tab_h
            else:
                y = m
            w.setGeometry(x, y, s.width(), s.height())
            w.raise_()


class Context:
    """What a package's install(ctx) gets. See cadswarm/app/api.py for the contract."""

    def __init__(self, shell):
        self.shell = shell
        self.engine = shell.engine
        self.App = shell.engine.App
        self.Gui = shell.engine.Gui
        self.commands = api.CommandRegistry()
        self.signals = shell.signals
        self.ribbon_tabs = []
        self.env = "part"
        self.theme_mode = "light"
        self.settings = QtCore.QSettings(os.path.join(os.environ.get("FREECAD_USER_HOME", "."), "cadswarm.ini"),
                                         QtCore.QSettings.Format.IniFormat)
        self.services = {}  # packages publish objects for each other: ctx.services["team"] = session
        self._shortcuts = {}
        self.commands.on_add(self._bind_shortcut)

    # regions -------------------------------------------------------------
    def set_titlebar(self, widget):
        self.shell.set_top(0, widget)

    def set_ribbon(self, widget):
        self.shell.set_top(1, widget)

    def add_ribbon_tab(self, tab):
        self.ribbon_tabs = [t for t in self.ribbon_tabs if t.id != tab.id] + [tab]
        self.signals.ribbon_changed.emit()

    def add_left_page(self, pid, label, widget, order=50):
        self.shell.left.add_page(pid, label, widget, order)

    def add_right_page(self, pid, label, widget, order=50):
        self.shell.right.add_page(pid, label, widget, order)

    def show_right_page(self, pid):
        self.shell.right_dock.show()
        return self.shell.right.show_page(pid)

    def show_left_page(self, pid):
        return self.shell.left.show_page(pid)

    def show_property_page(self, widget):
        """The property panel replaces the tree while a command runs (pro CAD convention)."""
        self.shell.left.push(widget)

    def close_property_page(self, widget=None):
        self.shell.left.pop(widget)

    def add_status_widget(self, widget, stretch=0, permanent=False):
        sb = self.shell.statusBar()
        (sb.addPermanentWidget if permanent else sb.addWidget)(widget, stretch)

    def add_overlay(self, widget, corner="top", margin=12):
        self.shell.viewport.add_overlay(widget, corner, margin)

    def tasks_dock(self):
        """FreeCAD's task panel (sketcher and feature dialogs). Must stay inside its QDockWidget."""
        return self.engine.tasks

    # state ---------------------------------------------------------------
    def set_env(self, env):
        if env != self.env and env in api.ENVIRONMENTS:
            self.env = env
            self.signals.env_changed.emit(env)

    def set_theme(self, mode):
        self.theme_mode = mode
        self.shell.apply_theme()
        self.settings.setValue("theme", mode)
        self.signals.theme_changed.emit(mode)

    def colors(self):
        return theme.colors(self.theme_mode)

    def notify(self, text, level="info", ms=5000):
        self.shell.statusBar().showMessage(("! " if level == "error" else "") + text, ms)
        log(f"[{level}] {text}")

    def active_doc(self):
        return self.App.ActiveDocument

    def run_freecad(self, command):
        """Run a FreeCAD command by name (loads its workbench's commands on demand)."""
        self.Gui.runCommand(command)

    # keymap ----------------------------------------------------------------
    def _bind_shortcut(self, cmd):
        if not cmd.shortcut:
            return
        old = self._shortcuts.pop(cmd.id, None)
        if old is not None:
            old.setParent(None)
        sc = QtGui.QShortcut(QtGui.QKeySequence(cmd.shortcut), self.shell)
        sc.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)

        def fire(c=cmd):
            if c.envs and self.env not in c.envs:
                return
            if isinstance(QtWidgets.QApplication.focusWidget(), (QtWidgets.QLineEdit, QtWidgets.QTextEdit,
                                                                   QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox)) \
                    and not any(m in c.shortcut for m in ("Ctrl", "Alt", "F")):
                return  # plain-letter keys belong to the text field being typed in
            self.commands.run(c.id)

        sc.activated.connect(fire)
        self._shortcuts[cmd.id] = sc


class Shell(QtWidgets.QMainWindow):
    def __init__(self, engine, packages=None):
        super().__init__()
        self.engine = engine
        self.setWindowTitle("CADSwarm")
        self.resize(1600, 1000)
        self.signals = events.Signals()
        self.bridge = events.Bridge(engine.App, engine.Gui, self.signals)

        top = QtWidgets.QWidget()
        top.setObjectName("Top")
        self._top = QtWidgets.QVBoxLayout(top)
        self._top.setContentsMargins(0, 0, 0, 0)
        self._top.setSpacing(0)
        self._top_slots = [None, None]
        self.setMenuWidget(top)

        mdi, tasks = engine.adopt()
        mdi.setViewMode(QtWidgets.QMdiArea.ViewMode.TabbedView)
        mdi.setDocumentMode(True)
        self.viewport = Viewport(mdi)
        self.setCentralWidget(self.viewport)

        self.left = Pane("LeftPane")
        self.right = Pane("TaskPane")
        self.left_dock = self._dock(self.left, QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, 300)
        self.right_dock = self._dock(self.right, QtCore.Qt.DockWidgetArea.RightDockWidgetArea, 340)
        if tasks is not None:  # FreeCAD's task panel: shown as a left-pane page while a dialog is open
            self.tasks_holder = QtWidgets.QWidget()
            tl = QtWidgets.QVBoxLayout(self.tasks_holder)
            tl.setContentsMargins(0, 0, 0, 0)
            tl.addWidget(tasks)
            tasks.show()
        self.signals.edit_changed.connect(self._on_edit)

        self.statusBar().setObjectName("StatusBar")
        self.statusBar().setSizeGripEnabled(False)
        self.ctx = Context(self)
        self.ctx.theme_mode = str(self.ctx.settings.value("theme", "light"))
        self.apply_theme()
        self.failed = {}
        for name in (PACKAGES if packages is None else packages):
            self._install(name)

    def _dock(self, widget, area, width):
        d = QtWidgets.QDockWidget(self)
        d.setObjectName(widget.objectName() + "Dock")
        d.setTitleBarWidget(QtWidgets.QWidget())
        d.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        d.setWidget(widget)
        self.addDockWidget(area, d)
        self._dock_widths = getattr(self, "_dock_widths", []) + [(d, width)]
        return d

    def showEvent(self, ev):
        super().showEvent(ev)
        if getattr(self, "_dock_widths", None):
            docks, self._dock_widths = self._dock_widths, None
            QtCore.QTimer.singleShot(0, lambda: self.resizeDocks([d for d, _ in docks], [w for _, w in docks],
                                                                 QtCore.Qt.Orientation.Horizontal))

    def set_top(self, slot, widget):
        old = self._top_slots[slot]
        if old is not None:
            self._top.removeWidget(old)
            old.setParent(None)
        self._top_slots[slot] = widget
        self._top.insertWidget(0 if slot == 0 else self._top.count(), widget)

    def _install(self, name):
        try:
            mod = importlib.import_module(f"cadswarm.app.{name}")
        except ModuleNotFoundError as e:
            if e.name == f"cadswarm.app.{name}":
                return  # not built yet
            self.failed[name] = traceback.format_exc()
            log(f"[package {name}] import failed\n{self.failed[name]}")
            return
        except Exception:  # noqa: BLE001
            self.failed[name] = traceback.format_exc()
            log(f"[package {name}] import failed\n{self.failed[name]}")
            return
        if hasattr(mod, "install"):
            try:
                mod.install(self.ctx)
            except Exception:  # noqa: BLE001
                self.failed[name] = traceback.format_exc()
                log(f"[package {name}] install failed\n{self.failed[name]}")

    def _on_edit(self, editing):
        """FreeCAD opened a task dialog (sketch, pad, fillet...): show it in place of the tree."""
        if not hasattr(self, "tasks_holder"):
            return
        if editing:
            self.left.push(self.tasks_holder)
        elif self.left.stack.currentWidget() is self.tasks_holder:
            self.left.pop()

    def apply_theme(self):
        mode = self.ctx.theme_mode
        QtWidgets.QApplication.instance().setStyleSheet(theme.qss(mode))
        try:
            p = self.engine.App.ParamGet("User parameter:BaseApp/Preferences/View")
            for kind, key, value in theme.view_params(mode):
                getattr(p, "Set" + kind)(key, value)
        except Exception:  # noqa: BLE001
            pass

    # used by the entry point's test hooks ----------------------------------
    def open_path(self, path):
        cmd = self.ctx.commands.get("file.open_path")
        if cmd is not None:
            return self.ctx.services["files"].open_path(path)
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
        sk2 = body.newObject("Sketcher::SketchObject", "Sketch001")
        sk2.AttachmentSupport = [(pad, "Face6")]
        sk2.MapMode = "FlatFace"
        sk2.addGeometry(Part.Circle(V(20, 12.5, 0), V(0, 0, 1), 6))
        pocket = body.newObject("PartDesign::Pocket", "Pocket")
        pocket.Profile = sk2
        pocket.Type = 1  # through all
        fil = body.newObject("PartDesign::Fillet", "Fillet")
        fil.Base = (pocket, ["Edge1", "Edge3", "Edge5", "Edge7"])
        fil.Radius = 2
        doc.recompute()
        v = Gui.ActiveDocument.ActiveView
        v.viewIsometric()
        v.fitAll()

    def closeEvent(self, ev):
        files = self.ctx.services.get("files")
        if files is not None and hasattr(files, "confirm_close_all") and not files.confirm_close_all():
            ev.ignore()
            return
        self.bridge.close()
        self.engine.shutdown()
        super().closeEvent(ev)
