"""Runtime rebranding of FreeCAD's main window: titles, menus, Help links, About box, icon. Needs Qt."""

from PySide6 import QtCore, QtGui, QtWidgets

from cadswarm.app import app_icon as icon_files, branded

def app_icon():
    """CADSwarm's icon at every size it was built in (window, taskbar, Alt+Tab, About box)."""
    icon = QtGui.QIcon()
    for size, path in icon_files.icon_files():
        icon.addFile(path, QtCore.QSize(size, size))
    return icon


class Rebrander(QtCore.QObject):
    """Event filter that keeps every visible title and menu entry branded CADSwarm."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.mw = main_window
        self._busy = False

    def install(self):
        app = QtWidgets.QApplication.instance()
        icon = app_icon()
        if not icon.isNull():
            app.setWindowIcon(icon)
            self.mw.setWindowIcon(icon)
        app.setApplicationName(branded.APP_NAME)
        app.setApplicationDisplayName(branded.APP_NAME)
        app.installEventFilter(self)
        self.fix_title(self.mw)
        self.fix_menus()
        return self

    # --- titles -----------------------------------------------------------------------------------------
    def fix_title(self, w):
        if self._busy:
            return
        t = w.windowTitle()
        nt = branded.rebrand_text(t)
        if w is self.mw and not nt:
            nt = branded.APP_NAME
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
            t = act.text()
            nt = branded.rebrand_text(t)
            if nt != t:
                act.setText(nt)
            tip = act.toolTip()
            if tip and "FreeCAD" in tip:
                act.setToolTip(branded.rebrand_text(tip))
            st = act.statusTip()
            if st and "FreeCAD" in st:
                act.setStatusTip(branded.rebrand_text(st))

    def show_about(self, *_):
        engine = ""
        try:
            import FreeCAD
            engine = branded.engine_version(FreeCAD.Version())
        except Exception:  # noqa: BLE001
            pass
        box = QtWidgets.QMessageBox(self.mw)
        box.setObjectName("CADSwarmAbout")
        box.setWindowTitle("About CADSwarm")
        box.setIconPixmap(app_icon().pixmap(96, 96))
        box.setTextFormat(QtCore.Qt.TextFormat.RichText)
        box.setText(branded.about_html(branded.cadswarm_version(), engine))
        box.exec()

    def replace_about(self, dlg):
        """FreeCAD's own About dialog (opened by its command, not our menu entry) gives way to CADSwarm's."""
        if dlg.property("cadswarm_replaced"):
            return
        dlg.setProperty("cadswarm_replaced", True)
        dlg.setWindowOpacity(0.0)
        QtCore.QTimer.singleShot(0, dlg.reject)
        QtCore.QTimer.singleShot(0, self.show_about)

    def eventFilter(self, obj, ev):
        et = ev.type()
        if et == QtCore.QEvent.Type.WindowTitleChange and isinstance(obj, QtWidgets.QWidget):
            if obj.isWindow() or isinstance(obj, QtWidgets.QDockWidget):
                self.fix_title(obj)
        elif et == QtCore.QEvent.Type.Show:
            if isinstance(obj, QtWidgets.QMenu):
                self.fix_menu(obj)
            elif isinstance(obj, QtWidgets.QWidget) and obj.isWindow():
                if obj.metaObject().className().endswith("AboutDialog") and isinstance(obj, QtWidgets.QDialog):
                    self.replace_about(obj)
                    return False
                self.fix_title(obj)
                if (isinstance(obj, QtWidgets.QMessageBox) and obj.objectName() != "CADSwarmAbout"
                        and "FreeCAD" in obj.text()):
                    obj.setText(branded.rebrand_text(obj.text()))
        return False
