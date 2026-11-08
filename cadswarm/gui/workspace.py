"""The professional workspace CADSwarm puts on FreeCAD while its workbench is active, and takes off again.

On activation (``WORKSPACE.activate()``, from InitGui's ``Activated``):

* the whole application gets the pro look (pro_style.py: light greys, one blue accent, or the dark variant),
  with a matching palette, so menus, docks, trees, tabs, fields, scroll bars and the status bar all agree;
* the 3D view gets a neutral grey gradient and blue selection / amber preselection (FreeCAD parameters; the
  user's own values are saved first, in CADSwarm's preferences, and put back on deactivation, at quit, and on
  the next start if FreeCAD closed while CADSwarm was active);
* FreeCAD's stock toolbars are hidden and a tabbed command ribbon docks at the top
  (Features, Sketch, Evaluate, Assembly, Library, AI Build, View);
* the status bar shows units, connection, the selection and who is editing what;
* the CADSwarm task pane (the team panel) wears the same colours.

``WORKSPACE.deactivate()`` undoes each of these. Every step is wrapped: a failure is logged to the Report
view and never stops FreeCAD or the workbench. ``CadSwarm_ProLook`` turns the whole thing off and on.
"""
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

from . import icons, pro_style, ribbon_spec

PREFS = "User parameter:BaseApp/Preferences/Mod/CadSwarm"
WORKBENCH = "CadSwarmWorkbench"
RIBBON_NAME = "CadSwarmRibbon"
ENABLE_SECONDS = 0.8  # how often the visible ribbon page re-checks which commands can run
# Modules whose import registers a workbench's commands, so the ribbon can run them without switching
# workbench. Assembly (FreeCAD 1.0) is a Python workbench: its commands live in its Command*.py modules.
WB_MODULES = {
    ribbon_spec.PD: ("PartDesignGui",),
    ribbon_spec.SK: ("SketcherGui",),
    ribbon_spec.PART: ("PartGui", "MeasureGui"),
    ribbon_spec.ASM: ("AssemblyGui", "CommandCreateAssembly", "CommandInsertLink", "CommandCreateJoint",
                      "CommandSolveAssembly", "CommandCreateView"),
}


def log(msg):
    try:
        App.Console.PrintLog(f"[CADSwarm] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def warn(msg):
    try:
        App.Console.PrintWarning(f"[CADSwarm] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def guarded(what):
    """Run a step of (de)activation; log and carry on if it fails."""
    def wrap(fn):
        def run(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as e:  # noqa: BLE001 - the look must never break FreeCAD
                warn(f"{what} failed: {e}")
                log(traceback.format_exc())
                return None
        run.__name__ = fn.__name__
        run.__doc__ = fn.__doc__
        return run
    return wrap


def prefs():
    return App.ParamGet(PREFS)


def main_window():
    try:
        return Gui.getMainWindow()
    except Exception:  # noqa: BLE001
        return None


def available_commands():
    try:
        return set(Gui.listCommands())
    except Exception:  # noqa: BLE001
        return set()


def _enum(owner, scoped, name):
    """Qt5/Qt6 enum lookups (PySide6 scopes them: QPalette.ColorRole.Window)."""
    scope = getattr(owner, scoped, None)
    return getattr(scope, name) if scope is not None and hasattr(scope, name) else getattr(owner, name)


def make_palette(t):
    pal = QtGui.QPalette()
    roles = {"Window": "BG", "WindowText": "TEXT", "Base": "SURFACE", "AlternateBase": "SURFACE_2",
             "ToolTipBase": "SURFACE", "ToolTipText": "TEXT", "Text": "TEXT", "Button": "SURFACE_2",
             "ButtonText": "TEXT", "BrightText": "ON_ACCENT", "Highlight": "ACCENT", "HighlightedText": "ON_ACCENT",
             "Link": "ACCENT", "Light": "SURFACE", "Midlight": "LINE", "Mid": "LINE_STRONG", "Dark": "MUTED",
             "Shadow": "TEXT", "PlaceholderText": "MUTED"}
    for role, token in roles.items():
        try:
            pal.setColor(_enum(QtGui.QPalette, "ColorRole", role), QtGui.QColor(t[token]))
        except AttributeError:
            pass
    disabled = _enum(QtGui.QPalette, "ColorGroup", "Disabled")
    for role in ("WindowText", "Text", "ButtonText"):
        try:
            pal.setColor(disabled, _enum(QtGui.QPalette, "ColorRole", role), QtGui.QColor(t["DISABLED_TEXT"]))
        except AttributeError:
            pass
    return pal


# ----------------------------------------------------------------------------------------------- ribbon

class Ribbon(QtGui.QWidget):
    """Tabs of large icon-over-label commands in labelled groups, with quick access and a command search."""

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.ws = workspace
        self.setObjectName("csRibbon")
        self.buttons = []          # (QToolButton, spec, page index)
        self.quick = []
        lay = QtGui.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QtGui.QWidget()
        bar.setObjectName("csRibbonBar")
        bar.setFixedHeight(30)
        bl = QtGui.QHBoxLayout(bar)
        bl.setContentsMargins(4, 0, 8, 0)
        bl.setSpacing(2)
        mark = QtGui.QLabel("CADSWARM")
        mark.setObjectName("csMark")
        bl.addWidget(mark)
        for spec in ribbon_spec.QUICK:
            b = QtGui.QToolButton()
            b.setObjectName("csQuick")
            b.setToolTip(spec["tip"])
            b.setAutoRaise(True)
            b.setIconSize(QtCore.QSize(16, 16))
            b.clicked.connect(lambda _c=False, s=spec: self.ws.run(s))
            bl.addWidget(b)
            self.quick.append((b, spec))
        sep = QtGui.QFrame()
        sep.setFixedWidth(10)
        bl.addWidget(sep)
        self.tabs = QtGui.QTabBar()
        self.tabs.setObjectName("csTabs")
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        for name in ribbon_spec.TAB_NAMES:
            self.tabs.addTab(name)
        self.tabs.currentChanged.connect(self.show_page)
        self.tabs.tabBarDoubleClicked.connect(lambda _i: self.toggle_collapsed())
        self.tabs.setToolTip("Double-click a tab to fold the ribbon away")
        bl.addWidget(self.tabs)
        bl.addStretch(1)
        self.people = QtGui.QLabel("")
        self.people.setObjectName("csPeople")
        self.people.setTextFormat(QtCore.Qt.RichText)
        bl.addWidget(self.people)
        self.search = QtGui.QLineEdit()
        self.search.setObjectName("csSearch")
        self.search.setPlaceholderText("Search commands")
        self.search.setFixedWidth(220)
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.run_search)
        self.completer = QtGui.QCompleter([], self.search)
        self.completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.completer.setFilterMode(QtCore.Qt.MatchContains)
        self.search.setCompleter(self.completer)
        bl.addWidget(self.search)
        lay.addWidget(bar)

        self.pages = QtGui.QStackedWidget()
        self.pages.setObjectName("csPages")
        labels = []
        for index, (tab, groups) in enumerate(ribbon_spec.RIBBON):
            page = QtGui.QWidget()
            page.setObjectName("csPage")
            pl = QtGui.QHBoxLayout(page)
            pl.setContentsMargins(4, 3, 4, 2)
            pl.setSpacing(0)
            for group, items in groups:
                box = QtGui.QFrame()
                box.setObjectName("csGroup")
                gl = QtGui.QVBoxLayout(box)
                gl.setContentsMargins(4, 0, 4, 0)
                gl.setSpacing(0)
                row = QtGui.QHBoxLayout()
                row.setSpacing(1)
                for spec in items:
                    b = QtGui.QToolButton()
                    b.setObjectName("csCommand")
                    b.setText(spec["label"])
                    b.setToolTip(f"<b>{spec['label'].replace(chr(10), ' ')}</b><br>{spec['tip']}")
                    b.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
                    b.setIconSize(QtCore.QSize(32, 32))
                    b.setMinimumWidth(52)
                    b.setFixedHeight(70)
                    b.setAutoRaise(True)
                    b.clicked.connect(lambda _c=False, s=spec: self.ws.run(s))
                    row.addWidget(b)
                    self.buttons.append((b, spec, index))
                    labels.append(" ".join(spec["label"].split()))
                gl.addLayout(row)
                cap = QtGui.QLabel(group)
                cap.setObjectName("csGroupLabel")
                cap.setAlignment(QtCore.Qt.AlignCenter)
                gl.addWidget(cap)
                pl.addWidget(box)
            pl.addStretch(1)
            scroll = QtGui.QScrollArea()  # a narrow window scrolls the page sideways instead of cropping it
            scroll.setWidget(page)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtGui.QFrame.NoFrame)
            scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            scroll.setFixedHeight(90)
            self.pages.addWidget(scroll)
        self.completer.setModel(QtCore.QStringListModel(sorted(set(labels)), self.completer))
        lay.addWidget(self.pages)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh_enabled)
        self.timer.start(int(ENABLE_SECONDS * 1000))
        self.restyle()

    def restyle(self):
        t = self.ws.tokens()
        self.setStyleSheet(pro_style.ribbon_stylesheet(t, self.ws.family))
        for b, spec, _i in self.buttons:
            b.setIcon(icons.qicon(spec["icon"], t["ICON"], t["ACCENT"], t["DISABLED_TEXT"]))
        for b, spec in self.quick:
            b.setIcon(icons.qicon(spec["icon"], t["ICON"], t["ACCENT"], t["DISABLED_TEXT"]))
        self.refresh_enabled(force=True)

    def show_page(self, index):
        self.pages.setCurrentIndex(index)
        self.pages.show()
        self.refresh_enabled(force=True)

    def show_tab(self, name):
        if name in ribbon_spec.TAB_NAMES:
            self.tabs.setCurrentIndex(ribbon_spec.TAB_NAMES.index(name))

    def toggle_collapsed(self):
        self.pages.setVisible(not self.pages.isVisible())

    def refresh_enabled(self, force=False):
        """Grey out what cannot run now (no sketch selected for Extrude, no document for a view...)."""
        if not force and not self.isVisible():
            return
        have = available_commands()
        page = self.tabs.currentIndex()
        for b, spec, index in self.buttons:
            if index == page:
                b.setEnabled(self.ws.can_run(spec, have))
        for b, spec in self.quick:
            b.setEnabled(self.ws.can_run(spec, have))

    def run_search(self):
        hits = ribbon_spec.search(self.search.text())
        if hits:
            self.search.clear()
            self.show_tab(hits[0]["tab"])
            self.ws.run(hits[0])

    def set_people(self, people):
        """Teammates' initials in their colours (top right, as in the web app)."""
        chips = []
        for i, (name, colour) in enumerate(people[:6]):
            initial = html_escape((name or "?")[:1].upper())
            chips.append(f"<span style='background:{colour}; color:#ffffff; font-weight:700;'>&nbsp;{initial}&nbsp;"
                         "</span>")
        more = f" +{len(people) - 6}" if len(people) > 6 else ""
        self.people.setText("&nbsp;".join(chips) + more + ("&nbsp;&nbsp;" if chips else ""))
        self.people.setToolTip(", ".join(n for n, _ in people))


def html_escape(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------------------------------------ status bar

class StatusStrip(QtGui.QWidget):
    """Units, connection, selection and who is editing what, at the right of FreeCAD's status bar."""

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.ws = workspace
        self.setObjectName("csStatus")
        lay = QtGui.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.editing = QtGui.QLabel("")
        self.selection = QtGui.QLabel("")
        self.connection = QtGui.QLabel("")
        self.units = QtGui.QLabel("mm")
        self.units.setToolTip("Model units: millimetres")
        for w in (self.editing, self.selection, self.connection, self.units):
            w.setTextFormat(QtCore.Qt.RichText)
            lay.addWidget(w)

    def show_session(self, s):
        t = self.ws.tokens()
        state = getattr(s, "state", "disconnected") if s is not None else "disconnected"
        colour = {"connected": t["OK"], "connecting": t["WARN"], "offline": t["WARN"]}.get(state, t["MUTED"])
        word = {"connected": "Connected", "connecting": "Connecting…", "offline": "Offline",
                "ended": "Session ended"}.get(state, "Not connected")
        project = getattr(s, "project", "") if s is not None else ""
        self.connection.setText(f"<span style='color:{colour}'>●</span> {html_escape(word)}"
                                + (f" · {html_escape(project)}" if project and state == "connected" else ""))
        self.editing.setText(html_escape(editing_text(s)))

    def show_selection(self):
        self.selection.setText(html_escape(selection_text()))


def editing_text(s):
    """"Editing: Mast (ann), Base (bob)" from the parts' locks, or ""."""
    if s is None:
        return ""
    try:
        objects = (s.model or {}).get("objects") or []
    except AttributeError:
        return ""
    held = [(o.get("label") or o.get("name"), o.get("locked_by")) for o in objects if o.get("locked_by")]
    held = [(n, who) for n, who in held if n and n != "Params"]
    if not held:
        return ""
    shown = ", ".join(f"{n} ({who})" for n, who in held[:3])
    return "Editing: " + shown + (f" +{len(held) - 3}" if len(held) > 3 else "")


def selection_text():
    try:
        sel = Gui.Selection.getSelectionEx()
    except Exception:  # noqa: BLE001
        return ""
    if not sel:
        return ""
    if len(sel) == 1:
        o = sel[0]
        subs = list(getattr(o, "SubElementNames", []) or [])
        label = getattr(o.Object, "Label", o.ObjectName)
        if len(subs) == 1:
            kind = "".join(ch for ch in subs[0] if ch.isalpha())
            return f"{kind} of {label}" if kind else label
        if subs:
            return f"{len(subs)} items of {label}"
        return label
    return f"{len(sel)} selected"


# ------------------------------------------------------------------------------------------- workspace

class Workspace:
    def __init__(self):
        self.active = False
        self.switching = False      # loading another workbench's commands (switch there and back)
        self.mode = pro_style.app_mode()  # the app's light/dark until the workbench picks its own
        self.family = ""
        self.ribbon_bar = None      # the QToolBar holding the ribbon
        self.ribbon = None
        self.status = None
        self.hidden_toolbars = []   # toolbars we hid (shown again on deactivation)
        self.old_sheet = None
        self.old_palette = None
        self.old_font = None
        self.timer = None
        self._stash = None
        self._quit_hooked = False

    # ----------------------------------------------------------------- settings

    def enabled(self):
        try:
            return prefs().GetBool("ProUi", True)
        except Exception:  # noqa: BLE001
            return True

    def setting(self):
        try:
            v = prefs().GetString("ProTheme", "auto")
        except Exception:  # noqa: BLE001
            v = "auto"
        return v if v in pro_style.MODES else "auto"

    def tokens(self):
        return pro_style.tokens(self.mode)

    def stash(self):
        if self._stash is None:
            self._stash = pro_style.ParamStash(App.ParamGet, prefs())
        return self._stash

    # ----------------------------------------------------------------- lifecycle

    def activate(self):
        if self.switching or not self.enabled():
            return
        first = not self.active
        if first:
            self.mode = pro_style.pick_mode(self.setting(), self._window_rgb())
            self.family = self._pick_family()
        self.apply_stylesheet(first)
        self.apply_view_params()
        self.install_ribbon()
        self.hide_stock_toolbars()
        self.install_status()
        self.restyle_panel()
        self.hook_quit()
        self.active = True
        # FreeCAD finishes setting up the workbench's toolbars after Activated(): hide them once that is done.
        QtCore.QTimer.singleShot(0, self.hide_stock_toolbars)
        QtCore.QTimer.singleShot(400, self.hide_stock_toolbars)

    def deactivate(self):
        if self.switching or not self.active:
            return
        self.active = False
        self.remove_status()
        self.remove_ribbon()
        self.show_stock_toolbars()
        self.restore_view_params()
        self.restore_stylesheet()
        self.restyle_panel()

    def recover(self):
        """At start-up: FreeCAD closed while CADSwarm was active, so the 3D view still has our colours. Put the
        user's own back unless CADSwarm is the workbench that is open now."""
        try:
            wb = Gui.activeWorkbench().name()
        except Exception:  # noqa: BLE001
            wb = ""
        if wb != WORKBENCH and not self.active and self.stash().pending:
            self.stash().restore()
            log("put back the 3D view colours from before CADSwarm")

    def set_enabled(self, on):
        prefs().SetBool("ProUi", bool(on))
        if on and self._is_current():
            self.activate()
        elif not on:
            self.deactivate()

    def toggle_dark(self):
        self.mode = "light" if self.mode == "dark" else "dark"
        prefs().SetString("ProTheme", self.mode)
        if self.active:
            self.apply_stylesheet(False)
            self.apply_view_params()
            if self.ribbon:
                self.ribbon.restyle()
            self.restyle_panel()

    def _is_current(self):
        try:
            return Gui.activeWorkbench().name() == WORKBENCH
        except Exception:  # noqa: BLE001
            return False

    def _window_rgb(self):
        try:
            c = QtGui.QApplication.instance().palette().color(_enum(QtGui.QPalette, "ColorRole", "Window"))
            return (c.red(), c.green(), c.blue())
        except Exception:  # noqa: BLE001
            return None

    def _pick_family(self):
        try:
            try:
                families = QtGui.QFontDatabase.families()  # Qt 6: static
            except TypeError:
                families = QtGui.QFontDatabase().families()  # Qt 5
        except Exception:  # noqa: BLE001
            families = []
        return pro_style.pick_font(list(families))

    # ----------------------------------------------------------------- the look

    @guarded("restyling FreeCAD")
    def apply_stylesheet(self, first):
        app = QtGui.QApplication.instance()
        if app is None:
            return
        if first and self.old_sheet is None:
            self.old_sheet = app.styleSheet()
            self.old_palette = QtGui.QPalette(app.palette())
        t = self.tokens()
        app.setPalette(make_palette(t))
        app.setStyleSheet(pro_style.app_stylesheet(t, self.family))

    @guarded("putting FreeCAD's own look back")
    def restore_stylesheet(self):
        app = QtGui.QApplication.instance()
        if app is None:
            return
        if self.old_palette is not None:
            app.setPalette(self.old_palette)
        if self.old_sheet is not None:
            app.setStyleSheet(self.old_sheet)
        self.old_sheet = self.old_palette = None

    @guarded("setting the 3D view colours")
    def apply_view_params(self):
        failures = self.stash().apply(pro_style.view_params(self.tokens()))
        for name, err in failures:
            log(f"view parameter {name}: {err}")
        self.repaint_views()

    @guarded("putting the 3D view colours back")
    def restore_view_params(self):
        for name, err in self.stash().restore():
            log(f"restoring {name}: {err}")
        self.repaint_views()

    def repaint_views(self):
        """FreeCAD's views follow the View parameters by themselves; nudge them to redraw now."""
        try:
            mw = main_window()
            for w in (mw.findChildren(QtGui.QWidget, "View3DInventor") if mw is not None else []):
                w.update()
        except Exception:  # noqa: BLE001
            pass

    @guarded("restyling the task pane")
    def restyle_panel(self):
        from . import panel
        p = panel.SESSION.panel
        if p is None:
            return
        p.setStyleSheet(panel_style())
        page = getattr(p, "build_page", None)
        if page is not None:
            page.restyle()

    # ----------------------------------------------------------------- ribbon and toolbars

    @guarded("adding the command ribbon")
    def install_ribbon(self):
        mw = main_window()
        if mw is None:
            return
        self.load_command_modules()
        if self.ribbon_bar is None:
            bar = QtGui.QToolBar("CADSwarm Ribbon", mw)
            bar.setObjectName(RIBBON_NAME)
            bar.setMovable(False)
            bar.setFloatable(False)
            bar.setAllowedAreas(QtCore.Qt.TopToolBarArea)
            bar.setContextMenuPolicy(QtCore.Qt.PreventContextMenu)
            self.ribbon = Ribbon(self)
            bar.addWidget(self.ribbon)
            mw.addToolBar(QtCore.Qt.TopToolBarArea, bar)
            self.ribbon_bar = bar
        else:
            self.ribbon.restyle()
        self.ribbon_bar.toggleViewAction().setVisible(False)
        self.ribbon_bar.show()
        if self.ribbon_bar.isHidden():
            self.ribbon_bar.setVisible(True)

    @guarded("removing the command ribbon")
    def remove_ribbon(self):
        if self.ribbon_bar is not None:
            self.ribbon_bar.hide()

    def own(self, toolbar):
        return toolbar is self.ribbon_bar or toolbar.objectName() == RIBBON_NAME

    @guarded("hiding FreeCAD's toolbars")
    def hide_stock_toolbars(self):
        if not self.active and not self.ribbon_bar:
            return
        mw = main_window()
        if mw is None:
            return
        bars = [tb for tb in mw.findChildren(QtGui.QToolBar)
                if tb.parent() is mw and not self.own(tb) and tb.isVisible()]
        if not bars:
            return
        names = [tb.objectName() for tb in bars if tb.objectName()]
        # FreeCAD remembers toolbar visibility in BaseApp/MainWindow/Toolbars; remember what it said so a
        # toolbar hidden here is never saved as "the user hid it".
        self.stash().remember([(pro_style.TOOLBARS, "Bool", n) for n in names])
        for tb in bars:
            tb.hide()
            if tb not in self.hidden_toolbars:
                self.hidden_toolbars.append(tb)

    @guarded("showing FreeCAD's toolbars again")
    def show_stock_toolbars(self):
        for tb in self.hidden_toolbars:
            try:
                tb.show()
            except RuntimeError:  # deleted with its workbench
                pass
        self.hidden_toolbars = []

    def load_command_modules(self, wb=None):
        """Import the modules that register the ribbon's FreeCAD commands (no workbench switch)."""
        for name, modules in WB_MODULES.items():
            if wb and name != wb:
                continue
            for module in modules:
                try:
                    __import__(module)
                except Exception as e:  # noqa: BLE001 - missing module: those buttons stay grey
                    log(f"could not load {module}: {e}")

    def ensure_command(self, spec):
        """The command to run for a button, loading its workbench's commands first if need be."""
        name = ribbon_spec.resolve(spec, available_commands())
        if name or not spec.get("wb"):
            return name
        self.load_command_modules(spec["wb"])
        name = ribbon_spec.resolve(spec, available_commands())
        if name:
            return name
        # Last resort: open that workbench once (which registers its commands) and come straight back.
        try:
            self.switching = True
            Gui.activateWorkbench(spec["wb"])
            Gui.activateWorkbench(WORKBENCH)
        except Exception as e:  # noqa: BLE001
            warn(f"could not load the {spec['wb']} commands: {e}")
        finally:
            self.switching = False
            QtCore.QTimer.singleShot(0, self.hide_stock_toolbars)
        return ribbon_spec.resolve(spec, available_commands())

    def can_run(self, spec, have):
        if spec.get("action"):
            return True
        name = ribbon_spec.resolve(spec, have)
        if not name:
            return bool(spec.get("wb"))  # its workbench's commands load on the first click
        try:
            cmd = Gui.Command.get(name)
            return bool(cmd.isActive()) if cmd is not None and hasattr(cmd, "isActive") else True
        except Exception:  # noqa: BLE001
            return True

    def run(self, spec):
        try:
            if spec.get("action"):
                return run_action(spec["action"], spec.get("arg", ""))
            name = self.ensure_command(spec)
            if not name:
                warn(f"“{spec['label']}” needs a FreeCAD command this version doesn't have "
                     f"({', '.join(spec['cmds'])}).")
                return None
            Gui.runCommand(name)
        except Exception as e:  # noqa: BLE001
            warn(f"{spec.get('label')}: {e}")
        return None

    # ----------------------------------------------------------------- status bar

    @guarded("adding the status bar summary")
    def install_status(self):
        mw = main_window()
        if mw is None:
            return
        if self.status is None:
            self.status = StatusStrip(self)
            mw.statusBar().addPermanentWidget(self.status)
            self.timer = QtCore.QTimer(self.status)
            self.timer.timeout.connect(self.status.show_selection)
            self.timer.start(700)
        self.status.show()
        self.update()

    @guarded("removing the status bar summary")
    def remove_status(self):
        if self.status is not None:
            self.status.hide()

    def update(self, s=None):
        """The session changed (called from the panel's refresh): status bar and avatars follow."""
        if not self.active:
            return
        try:
            from . import panel
            s = s or panel.SESSION
            if self.status is not None:
                self.status.show_session(s)
                self.status.show_selection()
            if self.ribbon is not None:
                people = [(p.get("name"), pro_style.TEAMMATES[i % len(pro_style.TEAMMATES)])
                          for i, p in enumerate((s.model or {}).get("participants") or [])
                          if p.get("status") in ("active", "stale")]
                self.ribbon.set_people(people)
        except Exception as e:  # noqa: BLE001
            log(f"status bar: {e}")

    # ----------------------------------------------------------------- quitting

    def hook_quit(self):
        if self._quit_hooked:
            return
        try:
            QtGui.QApplication.instance().aboutToQuit.connect(self.at_quit)
            self._quit_hooked = True
        except Exception as e:  # noqa: BLE001
            log(f"quit hook: {e}")

    def at_quit(self):
        """FreeCAD writes its preferences when it closes: make them the user's own, not ours."""
        try:
            self.show_stock_toolbars()
            self.stash().restore()
        except Exception:  # noqa: BLE001
            pass


WORKSPACE = Workspace()


def panel_style():
    """The team panel's stylesheet: the pro look's colours while it is on, CadSwarm's dark one otherwise."""
    from . import theme
    if WORKSPACE.active:
        return theme.panel_sheet(pro_style.panel_tokens(WORKSPACE.tokens()))
    return theme.PANEL


# ------------------------------------------------------------------------------------------ actions

def run_action(action, arg=""):
    """A CADSwarm button: most open a tab of the task pane, the AI Build ones talk to the team."""
    from . import panel
    if action == "toggle_pro":
        return WORKSPACE.set_enabled(not WORKSPACE.active)
    if action == "toggle_dark":
        return WORKSPACE.toggle_dark()
    if action == "printability":
        return panel.check_printability() if panel.SESSION.ensure_connected("check printability") else None
    panel.show_panel()
    p = panel.SESSION.panel
    if p is None:
        return None
    if action == "library":
        p.show_tab(p.library)
        try:
            if arg:
                i = p.library.category.findData(arg)
                if i >= 0:
                    p.library.category.setCurrentIndex(i)
            p.library.query.setFocus()
        except Exception:  # noqa: BLE001
            pass
        return None
    if action == "tasks":
        return p.show_tasks()
    page = getattr(p, "build_page", None)
    if page is None:
        return None
    p.show_tab(page)
    handler = {"ai_plan": page.focus_prompt, "ai_next": page.run_next, "ai_all": page.run_all,
               "ai_pause": page.pause, "ai_replan": page.replan, "ai_assign": page.assign,
               "ai_review": page.review}.get(action)
    return handler() if handler else None


class ProLookCommand:
    def GetResources(self):
        return {"MenuText": "Professional look", "ToolTip": "Turn CADSwarm's command ribbon and professional look on "
                "or off (off: FreeCAD's own toolbars and colours)", "Checkable": WORKSPACE.enabled()}

    def Activated(self, checked=None):
        WORKSPACE.set_enabled(bool(checked) if checked is not None else not WORKSPACE.enabled())

    def IsActive(self):
        return True


def register_command():
    try:
        Gui.addCommand("CadSwarm_ProLook", ProLookCommand())
    except Exception as e:  # noqa: BLE001
        warn(f"could not add the look command: {e}")
