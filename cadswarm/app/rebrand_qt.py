"""Runtime rebranding of FreeCAD's main window: titles, menus, Help links, About box, icon. Needs Qt."""
import os

from PySide6 import QtCore, QtGui, QtWidgets

from cadswarm.app import branded

ABOUT_HTML = """<h2 style="margin:0">CADSwarm</h2>
<p>Parametric 3D CAD where your team and its AI helpers build one model together.</p>
<p>CADSwarm runs on the open-source FreeCAD geometry and modelling engine (LGPL&nbsp;2.1+),
used unmodified, with OpenCASCADE Technology underneath.</p>"""


def app_icon():
    return QtGui.QIcon(branded.LOGO) if os.path.exists(branded.LOGO) else QtGui.QIcon()


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
        box = QtWidgets.QMessageBox(self.mw)
        box.setWindowTitle("About CADSwarm")
        box.setIconPixmap(app_icon().pixmap(64, 64))
        box.setTextFormat(QtCore.Qt.TextFormat.RichText)
        box.setText(ABOUT_HTML)
        box.exec()

    def eventFilter(self, obj, ev):
        et = ev.type()
        if et == QtCore.QEvent.Type.WindowTitleChange and isinstance(obj, QtWidgets.QWidget):
            if obj.isWindow() or isinstance(obj, QtWidgets.QDockWidget):
                self.fix_title(obj)
        elif et == QtCore.QEvent.Type.Show:
            if isinstance(obj, QtWidgets.QMenu):
                self.fix_menu(obj)
            elif isinstance(obj, QtWidgets.QWidget) and obj.isWindow():
                self.fix_title(obj)
                if isinstance(obj, QtWidgets.QMessageBox) and "FreeCAD" in obj.text():
                    obj.setText(branded.rebrand_text(obj.text()))
        return False
