"""CADSwarm's home page: the first tab of the app instead of the engine's Start page.

New part, Open file, Open team project, and the recent files, in the design tokens' colours. It is an ordinary
tab in the document area (closable; File > Home brings it back via the ``Start_Start`` menu entry, which the
Rebrander points here). ``recent_files`` is pure and unit-tested; the widget needs Qt and FreeCAD.
"""
import html
import os

RECENT_GROUP = "User parameter:BaseApp/Preferences/RecentFiles"
TITLE = "Home"
OBJECT_NAME = "CADSwarmHome"


def recent_files(get_int, get_string, exists=os.path.exists, limit=12):
    """Recent files, newest first, that still exist. get_int/get_string read FreeCAD's RecentFiles group
    (RecentFiles = count, MRU0, MRU1, ...)."""
    count = get_int("RecentFiles", 0) or 0
    out = []
    for i in range(max(count, limit)):
        path = get_string(f"MRU{i}", "")
        if path and path not in out and exists(path):
            out.append(path)
        if len(out) >= limit:
            break
    return out


def home_stylesheet(t):
    """The home page's sheet from pro_style tokens: square, ink on panel, one orange primary action."""
    return f"""
#CADSwarmHome {{ background: {t['BG']}; }}
#CADSwarmHome QLabel {{ color: {t['TEXT']}; background: transparent; }}
#CADSwarmHome QLabel#homeTitle {{ font-size: 28px; font-weight: 700; }}
#CADSwarmHome QLabel#homeLead {{ color: {t['MUTED']}; font-size: 13px; }}
#CADSwarmHome QLabel#homeGroup {{ color: {t['MUTED']}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
#CADSwarmHome QPushButton {{ text-align: left; padding: 10px 14px; font-size: 14px; color: {t['TEXT']};
    background: {t['SURFACE']}; border: 2px solid {t['LINE_STRONG']}; border-radius: 0; min-width: 260px; }}
#CADSwarmHome QPushButton:hover {{ border-color: {t['ACCENT']}; background: {t['ACCENT_SOFT']}; }}
#CADSwarmHome QPushButton#primary {{ background: {t['ACCENT']}; color: {t['ON_ACCENT']}; font-weight: 700;
    border-color: {t['ACCENT']}; }}
#CADSwarmHome QPushButton#primary:hover {{ background: {t['ACCENT_HOVER']}; border-color: {t['ACCENT_HOVER']}; }}
#CADSwarmHome QListWidget {{ background: {t['SURFACE']}; color: {t['TEXT']}; border: 2px solid {t['LINE_STRONG']};
    border-radius: 0; font-size: 13px; outline: 0; }}
#CADSwarmHome QListWidget::item {{ padding: 8px 10px; border-bottom: 1px solid {t['LINE']}; }}
#CADSwarmHome QListWidget::item:hover, #CADSwarmHome QListWidget::item:selected {{
    background: {t['ACCENT_SOFT']}; color: {t['TEXT']}; }}
"""


def _run(cmd):
    import FreeCADGui
    try:
        FreeCADGui.runCommand(cmd)
    except Exception:  # noqa: BLE001 - e.g. the team add-on is not loaded
        return False
    return True


def new_part():
    import FreeCAD
    import FreeCADGui
    FreeCAD.newDocument("Part")
    FreeCADGui.activateWorkbench("PartDesignWorkbench")
    _run("PartDesign_Body")


def make_page():
    from PySide6 import QtCore, QtWidgets
    import FreeCAD
    from cadswarm.app import branded, rebrand_qt
    from cadswarm.gui import pro_style

    t = pro_style.tokens(branded.current_mode())
    page = QtWidgets.QWidget()
    page.setObjectName(OBJECT_NAME)
    page.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
    page.setStyleSheet(home_stylesheet(t))
    page.setWindowTitle(TITLE)
    page.setWindowIcon(rebrand_qt.app_icon())

    outer = QtWidgets.QHBoxLayout(page)
    outer.setContentsMargins(64, 56, 64, 56)
    outer.setSpacing(64)

    left = QtWidgets.QVBoxLayout()
    left.setSpacing(12)
    head = QtWidgets.QHBoxLayout()
    mark = QtWidgets.QLabel()
    mark.setPixmap(rebrand_qt.app_icon().pixmap(56, 56))
    head.addWidget(mark)
    title = QtWidgets.QLabel(branded.APP_NAME)
    title.setObjectName("homeTitle")
    head.addWidget(title)
    head.addStretch(1)
    left.addLayout(head)
    lead = QtWidgets.QLabel("Parametric 3D CAD where your team and its AI helpers build one model together.")
    lead.setObjectName("homeLead")
    lead.setWordWrap(True)
    left.addWidget(lead)
    left.addSpacing(16)
    group = QtWidgets.QLabel("START")
    group.setObjectName("homeGroup")
    left.addWidget(group)

    def button(text, slot, primary=False):
        b = QtWidgets.QPushButton(text)
        if primary:
            b.setObjectName("primary")
        b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        left.addWidget(b)
        return b

    button("New part", new_part, primary=True)
    button("Open file…", lambda: _run("Std_Open"))
    button("Open team project…", lambda: _run("CadSwarm_Connect"))
    left.addStretch(1)
    outer.addLayout(left, 2)

    right = QtWidgets.QVBoxLayout()
    right.setSpacing(12)
    rgroup = QtWidgets.QLabel("RECENT FILES")
    rgroup.setObjectName("homeGroup")
    right.addWidget(rgroup)
    grp = FreeCAD.ParamGet(RECENT_GROUP)
    files = recent_files(grp.GetInt, grp.GetString)
    lst = QtWidgets.QListWidget()
    for path in files:
        item = QtWidgets.QListWidgetItem(f"{os.path.basename(path)}    {os.path.dirname(path)}")
        item.setData(QtCore.Qt.ItemDataRole.UserRole, path)
        item.setToolTip(html.escape(path))
        lst.addItem(item)
    if not files:
        empty = QtWidgets.QListWidgetItem("No recent files yet. Start a new part or open one.")
        empty.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        lst.addItem(empty)
    lst.itemActivated.connect(lambda it: it.data(QtCore.Qt.ItemDataRole.UserRole)
                              and FreeCAD.openDocument(it.data(QtCore.Qt.ItemDataRole.UserRole)))
    lst.itemClicked.connect(lambda it: it.data(QtCore.Qt.ItemDataRole.UserRole)
                            and FreeCAD.openDocument(it.data(QtCore.Qt.ItemDataRole.UserRole)))
    right.addWidget(lst, 1)
    outer.addLayout(right, 3)
    return page


def show_home(mw):
    """Open (or bring forward) the home tab in the main window's document area."""
    from PySide6 import QtWidgets
    mdi = mw.findChild(QtWidgets.QMdiArea)
    if mdi is None:
        return None
    for sub in mdi.subWindowList():
        if sub.widget() is not None and sub.widget().objectName() == OBJECT_NAME:
            mdi.setActiveSubWindow(sub)
            return sub
    sub = mdi.addSubWindow(make_page())
    sub.setWindowTitle(TITLE)
    sub.showMaximized()
    mdi.setActiveSubWindow(sub)
    return sub
