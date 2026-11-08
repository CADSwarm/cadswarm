"""CADSwarm's own surfaces: the team panel, the setup guide and CADSwarm's dialogs.

They wear the same design tokens as the rest of the app (design/tokens.json, through ``pro_style.tokens``):
ink on panel greys with one signal-orange accent, square corners, in the app's light or dark mode, so the
panel reads as part of the CAD program rather than a separate dark app. The mode follows the branded app's
theme ("CADSwarm Light" / "CADSwarm Dark"), or ``CADSWARM_THEME``, or FreeCAD's own theme name when the
add-on runs inside a plain FreeCAD (``pro_style.app_mode``).

Only CadSwarm's own widgets are styled here: the stylesheet is set on the panel and each dialog.
The whole-application look the CADSwarm workbench wears while it is active is pro_style.py, applied by
workspace.py; it restyles the panel through ``panel_sheet``.
"""
from string import Template

from . import pro_style

MODE = pro_style.app_mode()
_T = pro_style.panel_tokens(pro_style.tokens(MODE))

BG = _T["BG"]                    # window / page
SURFACE = _T["SURFACE"]          # cards, lists, the chat
SURFACE_2 = _T["SURFACE_2"]      # buttons, combo boxes
CHROME = _T["CHROME"]            # dock title strip
LINE = _T["LINE"]                # hairlines
LINE_STRONG = _T["LINE_STRONG"]  # 2px structure borders
TEXT = _T["TEXT"]
BODY = _T["BODY"]
MUTED = _T["MUTED"]
ACCENT = _T["ACCENT"]            # signal orange
ACCENT_HOVER = _T["ACCENT_HOVER"]
ON_ACCENT = _T["ON_ACCENT"]      # text on orange
ACCENT_SOFT = _T["ACCENT_SOFT"]  # selected rows, hover
OK = _T["OK"]
ERR = _T["ERR"]
WARN = _T["WARN"]

DISABLED_TEXT = _T["DISABLED_TEXT"]
ACCENT_DISABLED = _T["ACCENT_DISABLED"]
HANDLE_HOVER = _T["HANDLE_HOVER"]

_TOKENS = dict(BG=BG, SURFACE=SURFACE, SURFACE_2=SURFACE_2, CHROME=CHROME, LINE=LINE, LINE_STRONG=LINE_STRONG,
               TEXT=TEXT, BODY=BODY, MUTED=MUTED, ACCENT=ACCENT, ACCENT_HOVER=ACCENT_HOVER, ON_ACCENT=ON_ACCENT,
               ACCENT_SOFT=ACCENT_SOFT, OK=OK, ERR=ERR, WARN=WARN, DISABLED_TEXT=DISABLED_TEXT,
               ACCENT_DISABLED=ACCENT_DISABLED, HANDLE_HOVER=HANDLE_HOVER)

# The team panel (its dock is #CadSwarmTeam, the styled sheet inside it is #cadswarmPanel).
PANEL_TEMPLATE = Template("""
#CadSwarmTeam { background: $BG; }
#CadSwarmTeam::title { background: $CHROME; color: $TEXT; padding: 5px 8px; border-bottom: 2px solid $LINE_STRONG; }
#CadSwarmTeam::close-button, #CadSwarmTeam::float-button { background: transparent; }
#CadSwarmTeam QScrollArea, #CadSwarmTeam QScrollArea > QWidget { background: $BG; border: none; }
#cadswarmPanel { font-size: 10pt; background: $BG; color: $TEXT; }
#cadswarmPanel QLabel { color: $TEXT; background: transparent; }
#cadswarmPanel QPushButton { min-width: 0px; padding: 6px 12px; border: 1px solid $LINE_STRONG;
    border-radius: 0; background: $SURFACE_2; color: $TEXT; }
#cadswarmPanel QPushButton:hover { border-color: $ACCENT; }
#cadswarmPanel QPushButton:pressed { background: $LINE; border-color: $ACCENT; }
#cadswarmPanel QPushButton:disabled { color: $DISABLED_TEXT; border-color: $LINE; }
#cadswarmPanel QPushButton[busy="true"] { background: $ACCENT; border-color: $ACCENT; color: $ON_ACCENT; }
#cadswarmPanel QPushButton#primary { background: $ACCENT; border-color: $ACCENT; color: $ON_ACCENT; font-weight: bold; }
#cadswarmPanel QPushButton#primary:hover { background: $ACCENT_HOVER; border-color: $ACCENT_HOVER; }
#cadswarmPanel QPushButton#primary:pressed, #cadswarmPanel QPushButton#primary[busy="true"] {
    background: $ACCENT_HOVER; border-color: $ACCENT_HOVER; }
#cadswarmPanel QPushButton#primary:disabled { background: $ACCENT_DISABLED; border-color: $ACCENT_DISABLED; color: $DISABLED_TEXT; }
#cadswarmPanel QPushButton#quiet, #cadswarmPanel QPushButton#danger { border: none; background: transparent;
    color: $ACCENT; font-weight: bold; padding: 3px 4px; }
#cadswarmPanel QPushButton#danger { color: $ERR; font-weight: normal; text-align: left; }
#cadswarmPanel QPushButton#quiet:hover, #cadswarmPanel QPushButton#danger:hover { text-decoration: underline; }
#cadswarmPanel QPushButton#quiet[busy="true"], #cadswarmPanel QPushButton#danger[busy="true"] { color: $MUTED; }
#cadswarmPanel QPushButton#jump { background: $ACCENT; border: none; border-radius: 0; color: $ON_ACCENT;
    font-weight: bold; padding: 4px 10px; min-height: 20px; }
#cadswarmPanel QPushButton#jump:hover { background: $ACCENT_HOVER; }
#cadswarmPanel QPushButton#tool { text-align: left; padding: 4px 10px; border: none; background: transparent;
    color: $TEXT; }
#cadswarmPanel QPushButton#tool:hover { background: $SURFACE_2; color: $ACCENT; }
#cadswarmPanel QPushButton#tool:pressed, #cadswarmPanel QPushButton#tool[busy="true"] {
    background: $SURFACE_2; color: $ACCENT; border-left: 3px solid $ACCENT; padding-left: 7px; }
#cadswarmPanel QToolButton { padding: 6px 10px; color: $TEXT; }
#cadswarmPanel QFrame#card { background: $SURFACE; border: 1px solid $LINE; border-radius: 0; }
#cadswarmPanel QFrame#suggest { background: $SURFACE; border: 1px solid $ACCENT; border-radius: 0; }
#cadswarmPanel QLabel#title { font-size: 12pt; font-weight: bold; }
#cadswarmPanel QTabWidget::pane { border: none; border-top: 1px solid $LINE; background: $BG; }
#cadswarmPanel QTabBar::tab { background: transparent; border: none; border-bottom: 2px solid transparent;
    padding: 6px 9px; margin-right: 2px; min-height: 20px; color: $MUTED; }
#cadswarmPanel QTabBar::tab:!selected { color: $MUTED; }
#cadswarmPanel QTabBar::tab:selected { border-bottom: 2px solid $ACCENT; font-weight: bold; color: $TEXT; }
#cadswarmPanel QTabBar QToolButton { padding: 0; background: $BG; color: $TEXT; }
#cadswarmPanel QTreeWidget, #cadswarmPanel QTextBrowser { border: 1px solid $LINE; border-radius: 0;
    background: $SURFACE; color: $TEXT; }
#cadswarmPanel QTreeWidget { alternate-background-color: $SURFACE_2; }
#cadswarmPanel QTreeWidget::item { padding: 4px 2px; }
#cadswarmPanel QTreeWidget::item:selected { background: $ACCENT_SOFT; color: $TEXT; }
#cadswarmPanel QTreeWidget::item:hover { background: $SURFACE_2; }
#cadswarmPanel QTextBrowser { padding: 4px; }
#cadswarmPanel QHeaderView { background: transparent; border: none; }
#cadswarmPanel QHeaderView::section { background: transparent; color: $MUTED; font-weight: bold; border: none;
    border-bottom: 1px solid $LINE; padding: 5px 8px; }
#cadswarmPanel QLabel#toast { background: $SURFACE; color: $TEXT; border: 1px solid $OK;
    border-left: 4px solid $OK; border-radius: 0; padding: 6px 10px; }
#cadswarmPanel QLineEdit { min-height: 28px; border: 1px solid $LINE; border-radius: 0; padding: 0 8px;
    background: $SURFACE; color: $TEXT; selection-background-color: $ACCENT; selection-color: $ON_ACCENT; }
#cadswarmPanel QLineEdit:focus { border-color: $ACCENT; }
#cadswarmPanel QComboBox { min-height: 26px; background: $SURFACE_2; color: $TEXT; border: 1px solid $LINE;
    border-radius: 0; padding: 0 6px; }
#cadswarmPanel QComboBox QAbstractItemView { background: $SURFACE; color: $TEXT; border: 1px solid $LINE;
    selection-background-color: $ACCENT_SOFT; selection-color: $TEXT; }
#cadswarmPanel QPlainTextEdit { border: 1px solid $ACCENT; border-radius: 0; background: $SURFACE; color: $TEXT;
    selection-background-color: $ACCENT; selection-color: $ON_ACCENT; }
#cadswarmPanel QGroupBox { font-weight: bold; margin-top: 14px; padding-top: 6px; color: $TEXT; }
#cadswarmPanel QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 8px;
    padding: 0 4px; }
#cadswarmPanel QCheckBox, #cadswarmPanel QRadioButton { color: $TEXT; background: transparent; }
#cadswarmPanel QAbstractScrollArea { background: $BG; }
#cadswarmPanel QMenu { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; }
#cadswarmPanel QMenu::item:selected { background: $ACCENT_SOFT; color: $TEXT; }
#cadswarmPanel QToolTip { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; }
#cadswarmPanel QScrollBar:vertical { background: $BG; width: 12px; margin: 0; }
#cadswarmPanel QScrollBar::handle:vertical { background: $LINE; border-radius: 0; min-height: 24px; }
#cadswarmPanel QScrollBar::handle:vertical:hover { background: $HANDLE_HOVER; }
#cadswarmPanel QScrollBar:horizontal { background: $BG; height: 12px; margin: 0; }
#cadswarmPanel QScrollBar::handle:horizontal { background: $LINE; border-radius: 0; min-width: 24px; }
#cadswarmPanel QScrollBar::add-line, #cadswarmPanel QScrollBar::sub-line { height: 0; width: 0; }
#cadswarmPanel QScrollBar::add-page, #cadswarmPanel QScrollBar::sub-page { background: transparent; }
""")
PANEL = PANEL_TEMPLATE.substitute(_TOKENS)

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
QFrame#card { border: 1px solid $LINE; border-radius: 0; background: $SURFACE; padding: 8px; }
QPushButton { border: 1px solid $LINE_STRONG; border-radius: 0; background: $SURFACE_2; color: $TEXT;
    padding: 6px 12px; min-height: 24px; }
QPushButton:hover { border-color: $ACCENT; }
QPushButton:pressed { background: $LINE; border-color: $ACCENT; }
QPushButton:disabled { color: $DISABLED_TEXT; border-color: $LINE; }
QPushButton#primary { background: $ACCENT; border-color: $ACCENT; color: $ON_ACCENT; font-weight: 600; padding: 7px 18px; }
QPushButton#primary:hover { background: $ACCENT_HOVER; border-color: $ACCENT_HOVER; }
QPushButton#primary:pressed { background: $ACCENT_HOVER; border-color: $ACCENT_HOVER; }
QPushButton#primary:disabled { background: $ACCENT_DISABLED; border-color: $ACCENT_DISABLED; color: $DISABLED_TEXT; }
QPushButton#quiet { border: none; background: transparent; color: $ACCENT; }
QPlainTextEdit { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; border-radius: 0;
    selection-background-color: $ACCENT; selection-color: $ON_ACCENT; }
QPlainTextEdit#code { font-family: Consolas, monospace; font-size: 10.5pt; }
QLineEdit { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; border-radius: 0; padding: 0 8px;
    min-height: 26px; selection-background-color: $ACCENT; selection-color: $ON_ACCENT; }
QLineEdit:focus { border-color: $ACCENT; }
QComboBox { background: $SURFACE_2; color: $TEXT; border: 1px solid $LINE; border-radius: 0; padding: 0 6px;
    min-height: 26px; }
QComboBox QAbstractItemView { background: $SURFACE; color: $TEXT; border: 1px solid $LINE;
    selection-background-color: $ACCENT_SOFT; selection-color: $TEXT; }
QSpinBox, QDoubleSpinBox { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; border-radius: 0;
    padding: 0 6px; min-height: 26px; selection-background-color: $ACCENT; selection-color: $ON_ACCENT; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { background: $SURFACE_2; border: none; width: 18px; }
QRadioButton, QCheckBox { color: $TEXT; background: transparent; }
QGroupBox { color: $TEXT; border: 1px solid $LINE; border-radius: 0; margin-top: 12px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 4px;
    color: $MUTED; font-weight: bold; }
QTextBrowser { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; border-radius: 0; }
QListWidget, QTreeWidget, QTableWidget { background: $SURFACE; color: $TEXT; border: 1px solid $LINE;
    border-radius: 0; alternate-background-color: $SURFACE_2; }
QListWidget::item, QTreeWidget::item, QTableWidget::item { padding: 4px 2px; }
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected { background: $ACCENT_SOFT;
    color: $TEXT; }
QHeaderView { background: transparent; border: none; }
QHeaderView::section { background: transparent; color: $MUTED; font-weight: bold; border: none;
    border-bottom: 1px solid $LINE; padding: 5px 8px; }
QTabWidget::pane { border: none; border-top: 1px solid $LINE; background: $BG; }
QTabBar::tab { background: transparent; border: none; border-bottom: 2px solid transparent; padding: 6px 9px;
    margin-right: 2px; color: $MUTED; }
QTabBar::tab:selected { border-bottom: 2px solid $ACCENT; font-weight: bold; color: $TEXT; }
QCommandLinkButton { color: $TEXT; background: $SURFACE; border: 1px solid $LINE; border-radius: 0;
    padding: 8px 10px; text-align: left; }
QCommandLinkButton:hover { border-color: $ACCENT; }
QToolButton { color: $TEXT; }
QMenu { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; }
QMenu::item:selected { background: $ACCENT_SOFT; color: $TEXT; }
QProgressBar { background: $SURFACE_2; border: 1px solid $LINE; border-radius: 0; text-align: center;
    color: $TEXT; }
QProgressBar::chunk { background: $ACCENT; border-radius: 0; }
QSlider::groove:horizontal { background: $LINE; height: 4px; border-radius: 0; }
QSlider::handle:horizontal { background: $ACCENT; width: 14px; margin: -5px 0; border-radius: 0; }
QScrollArea { background: $BG; border: none; }
QScrollArea > QWidget > QWidget { background: $BG; }
QStackedWidget { background: $BG; }
QToolTip { background: $SURFACE; color: $TEXT; border: 1px solid $LINE; }
QScrollBar:vertical { background: $BG; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: $LINE; border-radius: 0; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: $HANDLE_HOVER; }
QScrollBar:horizontal { background: $BG; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: $LINE; border-radius: 0; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
""").substitute(_TOKENS)


def panel_sheet(tokens):
    """The team panel's stylesheet in other colours (the pro workspace's light or dark look). Missing tokens
    fall back to the app's current mode."""
    values = dict(_TOKENS)
    values.update({k: v for k, v in (tokens or {}).items() if k in _TOKENS})
    return PANEL_TEMPLATE.substitute(values)


def apply(widget, style=DIALOG):
    """Give a CadSwarm window its own surface. Returns the widget for one-line use."""
    widget.setStyleSheet(style)
    return widget
