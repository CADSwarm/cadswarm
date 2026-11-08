"""Runtime rebranding of FreeCAD's main window: titles, menus, dialogs, Help links, About box, icon. Needs Qt."""

import os

from PySide6 import QtCore, QtGui, QtWidgets

from cadswarm.app import app_icon as icon_files, branded

ABOUT_NAME = "CADSwarmAbout"
# Text fields hold the person's own data (file paths, property values, code): never rewritten.
_EDITABLE = (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox)


def app_icon():
    """CADSwarm's icon at every size it was built in (window, taskbar, Alt+Tab, About box)."""
    icon = QtGui.QIcon()
    for size, path in icon_files.icon_files():
        icon.addFile(path, QtCore.QSize(size, size))
    return icon


def _swap(get, put):
    """put(rebranded) when get() mentions FreeCAD."""
    try:
        text = get()
    except (RuntimeError, TypeError):
        return
    if isinstance(text, str) and "FreeCAD" in text:
        put(branded.rebrand_text(text))


class Rebrander(QtCore.QObject):
    """Event filter that keeps every visible title, menu entry, dialog label and tooltip branded CADSwarm."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.mw = main_window
        self._busy = False
        self._engine_about = False  # True while the person opened the engine's credits on purpose

    def install(self):
        app = QtWidgets.QApplication.instance()
        icon = app_icon()
        if not icon.isNull():
            app.setWindowIcon(icon)
            self.mw.setWindowIcon(icon)
        app.setApplicationName(branded.APP_NAME)
        app.setApplicationDisplayName(branded.APP_NAME)
        app.installEventFilter(self)
        self.theme_watcher = ThemeWatcher.attach()
        self.fix_title(self.mw)
        self.fix_menus()
        self.add_logo(icon)
        QtCore.QTimer.singleShot(0, self.show_home)
        return self

    def add_logo(self, icon):
        """CADSwarm's mark and name at the left end of the menu bar, like any branded desktop app.
        Clicking it opens the home page."""
        btn = QtWidgets.QToolButton(self.mw.menuBar())
        btn.setObjectName("CADSwarmLogo")
        btn.setIcon(icon)
        btn.setIconSize(QtCore.QSize(20, 20))
        btn.setText(branded.APP_NAME)
        btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setAutoRaise(True)
        btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("CADSwarm home")
        btn.setStyleSheet("QToolButton#CADSwarmLogo { font-weight: 600; padding: 2px 10px 2px 6px; border: none; }")
        btn.clicked.connect(self.show_home)
        self.mw.menuBar().setCornerWidget(btn, QtCore.Qt.Corner.TopLeftCorner)
        btn.show()
        self.logo = btn

    # --- titles -----------------------------------------------------------------------------------------
    def fix_title(self, w):
        if self._busy:
            return
        t = w.windowTitle()
        nt = branded.main_title(t) if w is self.mw else branded.rebrand_text(t)
        if nt != t:
            self._busy = True
            try:
                QtWidgets.QWidget.setWindowTitle(w, nt)
            finally:
                self._busy = False

    # --- menus ------------------------------------------------------------------------------------------
    def fix_menus(self):
        for menu in self.mw.menuBar().findChildren(QtWidgets.QMenu):
            self.fix_menu(menu)

    def fix_menu(self, menu):
        _swap(menu.title, menu.setTitle)
        for act in menu.actions():
            name = act.objectName() or act.data() or ""
            if isinstance(name, str) and name in branded.FREECAD_LINK_COMMANDS:
                act.setVisible(False)
                continue
            if name == "Std_About" or (act.text().replace("&", "").startswith("About FreeCAD")):
                if not act.property("cadswarm_about"):
                    act.setProperty("cadswarm_about", True)
                    try:
                        act.triggered.disconnect()
                    except (RuntimeError, TypeError):
                        pass
                    act.triggered.connect(self.show_about)
                act.setText(f"&About {branded.APP_NAME}")
                act.setToolTip(f"About {branded.APP_NAME}")
                act.setStatusTip(act.toolTip())
            if name == "Start_Start" and not act.property("cadswarm_home"):
                act.setProperty("cadswarm_home", True)
                try:
                    act.triggered.disconnect()
                except (RuntimeError, TypeError):
                    pass
                act.triggered.connect(self.show_home)
                act.setText("&Home")
                act.setToolTip("CADSwarm's home page: new part, open, recent files, team projects")
                act.setStatusTip(act.toolTip())
            self.fix_action(act)

    def show_home(self, *_):
        from cadswarm.app import home
        return home.show_home(self.mw)

    @staticmethod
    def fix_action(act):
        _swap(act.text, act.setText)
        _swap(act.toolTip, act.setToolTip)
        _swap(act.statusTip, act.setStatusTip)
        _swap(act.whatsThis, act.setWhatsThis)

    # --- any widget -------------------------------------------------------------------------------------
    def fix_widget(self, w):
        """Labels, buttons, group titles, tab and list texts and every tooltip: FreeCAD becomes CADSwarm.
        Editable text (paths, values, code) is left alone."""
        if self._in_own_about(w):
            return
        _swap(w.toolTip, w.setToolTip)
        _swap(w.whatsThis, w.setWhatsThis)
        _swap(w.statusTip, w.setStatusTip)
        if isinstance(w, _EDITABLE):
            return
        if isinstance(w, (QtWidgets.QLabel, QtWidgets.QAbstractButton)):
            _swap(w.text, w.setText)
        elif isinstance(w, QtWidgets.QGroupBox):
            _swap(w.title, w.setTitle)
        elif isinstance(w, QtWidgets.QComboBox):
            if w.objectName() == "themesCombobox":
                self.fix_themes(w)
            # Item texts are left alone: preference pages read them back as values.
            for i in range(w.count()):
                _swap(lambda i=i: w.itemData(i, QtCore.Qt.ItemDataRole.ToolTipRole),
                      lambda t, i=i: w.setItemData(i, t, QtCore.Qt.ItemDataRole.ToolTipRole))
        elif isinstance(w, QtWidgets.QTabBar):
            for i in range(w.count()):
                _swap(lambda i=i: w.tabText(i), lambda t, i=i: w.setTabText(i, t))
                _swap(lambda i=i: w.tabToolTip(i), lambda t, i=i: w.setTabToolTip(i, t))
        elif isinstance(w, QtWidgets.QListWidget):
            for i in range(w.count()):
                item = w.item(i)
                _swap(item.text, item.setText)
                _swap(item.toolTip, item.setToolTip)
        if isinstance(w, QtWidgets.QMessageBox):
            _swap(w.text, w.setText)
            _swap(w.informativeText, w.setInformativeText)

    @staticmethod
    def fix_themes(combo):
        """Preferences > General > Theme offers CADSwarm Light and CADSwarm Dark, not the engine's own themes.
        Choosing one sets MainWindow/Theme; ThemeWatcher then rewrites and reloads the stylesheet."""
        want = branded.THEME_NAMES
        if [combo.itemText(i) for i in range(combo.count())] == list(want):
            return
        current = branded.theme_name(branded.current_mode())
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(list(want))
        combo.setCurrentIndex(want.index(current))
        combo.blockSignals(False)

    def _in_own_about(self, w):
        top = w.window()
        if top.objectName() == ABOUT_NAME:
            return True
        return self._engine_about and top.metaObject().className().endswith("AboutDialog")

    # --- About ------------------------------------------------------------------------------------------
    def show_about(self, *_):
        engine = ""
        try:
            import FreeCAD
            engine = branded.engine_version(FreeCAD.Version())
        except Exception:  # noqa: BLE001
            pass
        box = QtWidgets.QMessageBox(self.mw)
        box.setObjectName(ABOUT_NAME)
        box.setWindowTitle("About CADSwarm")
        box.setIconPixmap(app_icon().pixmap(96, 96))
        box.setTextFormat(QtCore.Qt.TextFormat.RichText)
        box.setText(branded.about_html(branded.cadswarm_version(), engine))
        credits = box.addButton("Engine credits and licences", QtWidgets.QMessageBox.ButtonRole.HelpRole)
        box.addButton(QtWidgets.QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is credits:
            self.show_engine_about()

    def show_engine_about(self):
        """The engine's own About dialog, unchanged: its authors, licence and third-party libraries (LGPL)."""
        self._engine_about = True
        try:
            import FreeCADGui
            FreeCADGui.runCommand("Std_About")
        finally:
            self._engine_about = False

    def replace_about(self, dlg):
        """FreeCAD's own About dialog (opened by its command, not our menu entry) gives way to CADSwarm's."""
        if dlg.property("cadswarm_replaced"):
            return
        dlg.setProperty("cadswarm_replaced", True)
        dlg.setWindowOpacity(0.0)
        QtCore.QTimer.singleShot(0, dlg.reject)
        QtCore.QTimer.singleShot(0, self.show_about)

    # --- events -----------------------------------------------------------------------------------------
    def eventFilter(self, obj, ev):
        et = ev.type()
        if et == QtCore.QEvent.Type.WindowTitleChange and isinstance(obj, QtWidgets.QWidget):
            if obj.isWindow() or isinstance(obj, QtWidgets.QDockWidget):
                if not self._in_own_about(obj):
                    self.fix_title(obj)
        elif et == QtCore.QEvent.Type.Show and isinstance(obj, QtWidgets.QWidget):
            if isinstance(obj, QtWidgets.QMenu):
                self.fix_menu(obj)
                return False
            if obj.isWindow() and isinstance(obj, QtWidgets.QDialog) \
                    and obj.metaObject().className().endswith("AboutDialog") and not self._engine_about:
                self.replace_about(obj)
                return False
            if obj.isWindow() and not self._in_own_about(obj):
                self.fix_title(obj)
            try:
                self.fix_widget(obj)
            except RuntimeError:  # deleted while showing
                pass
        return False


class ThemeWatcher:
    """Follows MainWindow/Theme: when it becomes "CADSwarm Light" or "CADSwarm Dark" (Preferences > General),
    writes that mode's theme files and points FreeCAD at them, so the whole app switches without a restart.
    (CADSwarm's own panel picks the new mode up on the next start.)"""

    GROUP = "User parameter:BaseApp/Preferences/MainWindow"

    def __init__(self, FreeCAD):
        self.App = FreeCAD
        self.grp = FreeCAD.ParamGet(self.GROUP)

    @classmethod
    def attach(cls):
        try:
            import FreeCAD
        except ImportError:
            return None
        watcher = cls(FreeCAD)
        watcher.grp.Attach(watcher)
        return watcher

    def onChange(self, grp, reason):  # noqa: N802 - FreeCAD's observer API
        if reason != "Theme":
            return
        mode = branded.mode_of_theme(grp.GetString("Theme", ""))
        if not mode:
            return
        params_file = grp.GetString("ThemeStyleParametersFile", "")
        if params_file.endswith(f"CADSwarm {mode.title()}.yaml"):
            return
        home = os.path.dirname(os.path.dirname(params_file)) if params_file else os.environ.get("FREECAD_USER_HOME", "")
        if not home:
            return
        theme_file = branded.write_theme(os.path.normpath(self.App.getHomePath()), home, mode)
        QtCore.QTimer.singleShot(0, lambda: self.switch(theme_file, mode))

    def switch(self, theme_file, mode):
        from cadswarm.app import engine
        engine.apply_params(self.App, branded.brand_params(theme_file, mode))
        try:  # CADSwarm's own panel follows too
            from cadswarm.gui import theme, workspace
            theme.set_mode(mode)
            if not workspace.WORKSPACE.active:
                workspace.WORKSPACE.mode = mode
            app = QtWidgets.QApplication.instance()
            for w in app.allWidgets():
                if isinstance(w, QtWidgets.QDockWidget) and w.objectName() == "CadSwarmTeam":
                    w.setStyleSheet(workspace.panel_style())
        except Exception:  # noqa: BLE001 - the add-on may not be loaded
            pass
