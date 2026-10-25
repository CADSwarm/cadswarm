"""CADSwarm's own surfaces.

The team panel and the setup guide must read the same on every computer. FreeCAD follows
the system theme, and its Qt theme is light on most Linux desktops, so a stylesheet that
reads from ``palette(...)`` paints these windows flat white there. Instead the CadSwarm
windows carry their own colours, taken from the web app's tokens (``web/src/style.css``),
so a screenshot of the panel and the site reads as the same product.

Only CadSwarm's own widgets are styled: the stylesheet is set on the panel and each dialog,
never on QApplication, so FreeCAD's dock chrome and the rest of its UI are left alone.
"""
from string import Template

from PySide import QtGui

# One palette, shared by the panel, the guide and the dialogs.
BG = "#121212"           # window / page
SURFACE = "#181818"      # cards, lists, the chat
SURFACE_2 = "#1e1e1e"    # buttons, combo boxes
LINE = "#2a2a2a"         # hairlines
LINE_STRONG = "#3a3a3a"  # button borders, scroll handles
TEXT = "#f4f3f1"
BODY = "#d4d2cd"
MUTED = "#8f8d88"
ACCENT = "#d9822b"       # CADSwarm orange
ACCENT_HOVER = "#e8913a"
OK = "#4fb35a"
ERR = "#e5534b"
WARN = "#e3b341"

_TOKENS = dict(BG=BG, SURFACE=SURFACE, SURFACE_2=SURFACE_2, LINE=LINE, LINE_STRONG=LINE_STRONG,
               TEXT=TEXT, BODY=BODY, MUTED=MUTED, ACCENT=ACCENT, ACCENT_HOVER=ACCENT_HOVER,
               OK=OK, ERR=ERR, WARN=WARN)

# The team panel (its dock is #CadSwarmTeam, the styled sheet inside it is #cadswarmPanel).
PANEL = Template("""
#CadSwarmTeam { background: $BG; }
#CadSwarmTeam::title { background: $SURFACE_2; color: $TEXT; padding: 4px 8px; }
#CadSwarmTeam::close-button, #CadSwarmTeam::float-button { background: transparent; }
#CadSwarmTeam QScrollArea, #CadSwarmTeam QScrollArea > QWidget { background: $BG; border: none; }
#cadswarmPanel { font-size: 10pt; background: $BG; color: $TEXT; }
#cadswarmPanel QLabel { color: $TEXT; background: transparent; }
#cadswarmPanel QPushButton { min-width: 0px; padding: 6px 12px; border: 1px solid $LINE_STRONG;
    border-radius: 6px; background: $SURFACE_2; color: $TEXT; }
#cadswarmPanel QPushButton:hover { border-color: $ACCENT; }
#cadswarmPanel QPushButton:pressed { background: $LINE; }
#cadswarmPanel QPushButton:disabled { color: #6f6c66; border-color: $LINE; }
#cadswarmPanel QPushButton#primary { background: $ACCENT; border-color: $ACCENT; color: white; font-weight: bold; }
#cadswarmPanel QPushButton#primary:hover { background: $ACCENT_HOVER; border-color: $ACCENT_HOVER; }
#cadswarmPanel QPushButton#primary:disabled { background: #6b4a20; border-color: #6b4a20; color: #b9b5af; }
#cadswarmPanel QPushButton#quiet, #cadswarmPanel QPushButton#danger { border: none; background: transparent;
    color: $ACCENT; font-weight: bold; padding: 3px 4px; }
#cadswarmPanel QPushButton#danger { color: $ERR; font-weight: normal; text-align: left; }
#cadswarmPanel QPushButton#quiet:hover, #cadswarmPanel QPushButton#danger:hover { text-decoration: underline; }
#cadswarmPanel QPushButton#jump { background: $ACCENT; border: none; border-radius: 14px; color: white;
    font-weight: bold; padding: 4px 10px; min-height: 20px; }
#cadswarmPanel QPushButton#jump:hover { background: $ACCENT_HOVER; }
#cadswarmPanel QPushButton#tool { text-align: left; padding: 4px 10px; border: none; background: transparent;
    color: $TEXT; }
#cadswarmPanel QPushButton#tool:hover { background: $SURFACE_2; color: $ACCENT; }
#cadswarmPanel QToolButton { padding: 6px 10px; color: $TEXT; }
#cadswarmPanel QFrame#card { background: $SURFACE; border: 1px solid $LINE; border-radius: 10px; }
#cadswarmPanel QFrame#suggest { background: $SURFACE; border: 1px solid $ACCENT; border-radius: 10px; }
#cadswarmPanel QLabel#title { font-size: 12pt; font-weight: bold; }
#cadswarmPanel QTabWidget::pane { border: none; border-top: 1px solid $LINE; background: $BG; }
#cadswarmPanel QTabBar::tab { background: transparent; border: none; border-bottom: 2px solid transparent;
    padding: 6px 9px; margin-right: 2px; min-height: 20px; color: $MUTED; }
#cadswarmPanel QTabBar::tab:!selected { color: $MUTED; }
#cadswarmPanel QTabBar::tab:selected { border-bottom: 2px solid $ACCENT; font-weight: bold; color: $TEXT; }
#cadswarmPanel QTabBar QToolButton { padding: 0; background: $BG; color: $TEXT; }
#cadswarmPanel QTreeWidget, #cadswarmPanel QTextBrowser { border: 1px solid $LINE; border-radius: 8px;
    background: $SURFACE; color: $TEXT; }
#cadswarmPanel QTreeWidget { alternate-background-color: $SURFACE_2; }
#cadswarmPanel QTreeWidget::item { padding: 4px 2px; }
#cadswarmPanel QTreeWidget::item:selected { background: $LINE; color: $TEXT; }
#cadswarmPanel QTreeWidget::item:hover { background: $SURFACE_2; }
#cadswarmPanel QTextBrowser { padding: 4px; }
#cadswarmPanel QHeaderView { background: transparent; border: none; }
#cadswarmPanel QHeaderView::section { background: transparent; color: $MUTED; font-weight: bold; border: none;
    border-bottom: 1px solid $LINE; padding: 5px 8px; }
#cadswarmPanel QLabel#toast { background: $SURFACE; color: $TEXT; border: 1px solid $OK;
    border-left: 4px solid $OK; border-radius: 6px; padding: 6px 10px; }
#cadswarmPanel QLineEdit { min-height: 28px; border: 1px solid $LINE; border-radius: 6px; padding: 0 8px;
    background: $SURFACE; color: $TEXT; selection-background-color: $ACCENT; selection-color: white; }
#cadswarmPanel QLineEdit:focus { border-color: $ACCENT; }
#cadswarmPanel QComboBox { min-height: 26px; background: $SURFACE_2; color: $TEXT; border: 1px solid $LINE;
    border-radius: 6px; padding: 0 6px; }
#cadswarmPanel QComboBox QAbstractItemView { background: $SURFACE; color: $TEXT; border: 1px solid $LINE;
    selection-background-color: $LINE; selection-color: $TEXT; }
#cadswarmPanel QPlainTextEdit { border: 1px solid $ACCENT; border-radius: 4px; background: $SURFACE; color: $TEXT;
    selection-background-color: $ACCENT; selection-color: white; }
#cadswarmPanel QGroupBox { font-weight: bold; margin-top: 14px; padding-top: 6px; color: $TEXT; }
#cadswarmPanel QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 8px;
    padding: 0 4px; }
#cadswarmPanel QCheckBox, #cadswarmPanel QRadioButton { color: $TEXT; background: transparent; }
#cadswarmPanel QAbstractScrollArea { background: $BG; }
#cadswarmPanel QMenu { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; }
#cadswarmPanel QMenu::item:selected { background: $LINE; }
#cadswarmPanel QToolTip { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; }
#cadswarmPanel QScrollBar:vertical { background: $BG; width: 12px; margin: 0; }
#cadswarmPanel QScrollBar::handle:vertical { background: $LINE_STRONG; border-radius: 6px; min-height: 24px; }
#cadswarmPanel QScrollBar::handle:vertical:hover { background: #4a4a4a; }
#cadswarmPanel QScrollBar:horizontal { background: $BG; height: 12px; margin: 0; }
#cadswarmPanel QScrollBar::handle:horizontal { background: $LINE_STRONG; border-radius: 6px; min-width: 24px; }
#cadswarmPanel QScrollBar::add-line, #cadswarmPanel QScrollBar::sub-line { height: 0; width: 0; }
#cadswarmPanel QScrollBar::add-page, #cadswarmPanel QScrollBar::sub-page { background: transparent; }
""").substitute(_TOKENS)

# A CadSwarm dialog (the setup guide, the library, the optimization dialogs). Unscoped, because the
# sheet is set on the dialog itself and only reaches that dialog's own widgets.
DIALOG = Template("""
QDialog { background: $BG; color: $TEXT; font-size: 11pt; }
QLabel { color: $TEXT; background: transparent; }
QLabel#title { font-size: 17pt; font-weight: 600; color: $TEXT; }
QLabel#lead { font-size: 11.5pt; color: $TEXT; }
QLabel#hint { color: $MUTED; font-size: 10pt; }
QLabel#error { color: $ERR; font-weight: 600; }
QLabel#ok { color: $OK; font-weight: 600; }
QFrame#card { border: 1px solid $LINE; border-radius: 8px; background: $SURFACE; padding: 8px; }
QPushButton { border: 1px solid $LINE_STRONG; border-radius: 6px; background: $SURFACE_2; color: $TEXT;
    padding: 6px 12px; min-height: 24px; }
QPushButton:hover { border-color: $ACCENT; }
QPushButton:disabled { color: #6f6c66; border-color: $LINE; }
QPushButton#primary { background: $ACCENT; border-color: $ACCENT; color: white; font-weight: 600; padding: 7px 18px; }
QPushButton#primary:hover { background: $ACCENT_HOVER; border-color: $ACCENT_HOVER; }
QPushButton#primary:disabled { background: #6b4a20; border-color: #6b4a20; color: #b9b5af; }
QPushButton#quiet { border: none; background: transparent; color: $ACCENT; }
QPlainTextEdit { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; border-radius: 6px;
    selection-background-color: $ACCENT; selection-color: white; }
QPlainTextEdit#code { font-family: Consolas, monospace; font-size: 10.5pt; }
QLineEdit { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; border-radius: 6px; padding: 0 8px;
    min-height: 26px; selection-background-color: $ACCENT; selection-color: white; }
QLineEdit:focus { border-color: $ACCENT; }
QComboBox { background: $SURFACE_2; color: $TEXT; border: 1px solid $LINE; border-radius: 6px; padding: 0 6px;
    min-height: 26px; }
QComboBox QAbstractItemView { background: $SURFACE; color: $TEXT; border: 1px solid $LINE;
    selection-background-color: $LINE; selection-color: $TEXT; }
QSpinBox, QDoubleSpinBox { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; border-radius: 6px;
    padding: 0 6px; min-height: 26px; selection-background-color: $ACCENT; selection-color: white; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { background: $SURFACE_2; border: none; width: 18px; }
QRadioButton, QCheckBox { color: $TEXT; background: transparent; }
QGroupBox { color: $TEXT; border: 1px solid $LINE; border-radius: 8px; margin-top: 12px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 4px;
    color: $MUTED; font-weight: bold; }
QTextBrowser { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; border-radius: 6px; }
QListWidget, QTreeWidget, QTableWidget { background: $SURFACE; color: $TEXT; border: 1px solid $LINE;
    border-radius: 6px; alternate-background-color: $SURFACE_2; }
QListWidget::item, QTreeWidget::item, QTableWidget::item { padding: 4px 2px; }
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected { background: $LINE;
    color: $TEXT; }
QHeaderView { background: transparent; border: none; }
QHeaderView::section { background: transparent; color: $MUTED; font-weight: bold; border: none;
    border-bottom: 1px solid $LINE; padding: 5px 8px; }
QTabWidget::pane { border: none; border-top: 1px solid $LINE; background: $BG; }
QTabBar::tab { background: transparent; border: none; border-bottom: 2px solid transparent; padding: 6px 9px;
    margin-right: 2px; color: $MUTED; }
QTabBar::tab:selected { border-bottom: 2px solid $ACCENT; font-weight: bold; color: $TEXT; }
QCommandLinkButton { color: $TEXT; background: $SURFACE; border: 1px solid $LINE; border-radius: 8px;
    padding: 8px 10px; text-align: left; }
QCommandLinkButton:hover { border-color: $ACCENT; }
QToolButton { color: $TEXT; }
QMenu { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; }
QMenu::item:selected { background: $LINE; }
QProgressBar { background: $SURFACE_2; border: 1px solid $LINE; border-radius: 6px; text-align: center;
    color: $TEXT; }
QProgressBar::chunk { background: $ACCENT; border-radius: 5px; }
QSlider::groove:horizontal { background: $LINE; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: $ACCENT; width: 14px; margin: -5px 0; border-radius: 7px; }
QScrollArea { background: $BG; border: none; }
QScrollArea > QWidget > QWidget { background: $BG; }
QStackedWidget { background: $BG; }
QToolTip { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; }
QScrollBar:vertical { background: $BG; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: $LINE_STRONG; border-radius: 6px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #4a4a4a; }
QScrollBar:horizontal { background: $BG; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: $LINE_STRONG; border-radius: 6px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
""").substitute(_TOKENS)


def apply(widget, style=DIALOG):
    """Give a CadSwarm window its own surface. Returns the widget for one-line use."""
    widget.setStyleSheet(style)
    return widget
