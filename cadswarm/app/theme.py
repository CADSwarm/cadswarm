"""Design tokens (design/tokens.json) → Qt palette and stylesheet. Pure functions, no Qt at import.

Brutalist rules: 2px structure borders, 1px hairlines, radius 0, no shadows, uppercase mono labels, grotesk
headings, one loud accent (signal). Object names used by the shell and packages for styling:
  #TitleBar  #Ribbon  #RibbonTabs  #LeftPane  #TaskPane  #StatusBar  #Viewport
  QLabel[role="group"]  (uppercase mono section label)   QPushButton[kind="primary"]  [kind="ribbon"]
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOKENS_PATH = os.path.join(ROOT, "design", "tokens.json")
_cache = {}


def tokens():
    if "t" not in _cache:
        with open(TOKENS_PATH, encoding="utf-8") as f:
            _cache["t"] = json.load(f)
    return _cache["t"]


def colors(mode="light"):
    return dict(tokens()["color"][mode])


def font_stack(kind):
    return ", ".join(f'"{f}"' if " " in f else f for f in tokens()["font"][kind])


def qss(mode="light"):
    c = colors(mode)
    t = tokens()
    sz, b = t["size"], t["border"]
    S, H = b["structure"], b["hairline"]
    G, M = font_stack("grotesk"), font_stack("mono")
    return f"""
* {{ border-radius: 0px; }}
QWidget {{ background: {c['panel']}; color: {c['ink']}; font-family: {G}; font-size: 13px;
  selection-background-color: {c['signal']}; selection-color: {c['on-signal']}; }}
QMainWindow::separator {{ background: {c['structure']}; width: {S}px; height: {S}px; }}
QToolTip {{ background: {c['ink']}; color: {c['paper']}; border: {S}px solid {c['ink']}; font-family: {M};
  font-size: 11px; padding: 4px 6px; }}

#TitleBar, #Ribbon, #RibbonTabs, #StatusBar {{ background: {c['chrome']}; }}
#TitleBar {{ border-bottom: {S}px solid {c['structure']}; min-height: {sz['titlebar']}px; }}
#Ribbon {{ border-bottom: {S}px solid {c['structure']}; }}
#LeftPane {{ border-right: {S}px solid {c['structure']}; }}
#TaskPane {{ border-left: {S}px solid {c['structure']}; }}
#StatusBar, QStatusBar {{ background: {c['chrome']}; border-top: {S}px solid {c['structure']};
  font-family: {M}; font-size: 11px; min-height: {sz['status']}px; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ background: transparent; font-family: {M}; font-size: 11px; padding: 0 8px; }}

QLabel {{ background: transparent; }}
QLabel[role="group"] {{ font-family: {M}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px;
  color: {c['muted']}; text-transform: uppercase; }}
QLabel[role="title"] {{ font-size: 16px; font-weight: 700; }}
QLabel[role="value"] {{ font-family: {M}; font-size: 12px; }}

QPushButton, QToolButton {{ background: {c['paper']}; color: {c['ink']}; border: {S}px solid {c['structure']};
  padding: 4px 10px; min-height: 20px; font-family: {M}; font-size: 11px; font-weight: 600; }}
QPushButton:hover, QToolButton:hover {{ background: {c['chrome']}; }}
QPushButton:pressed, QToolButton:pressed, QToolButton:checked {{ background: {c['ink']}; color: {c['paper']}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {c['signal']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; border: {H}px dashed {c['hairline']}; }}
QPushButton[kind="primary"] {{ background: {c['signal']}; color: {c['on-signal']}; border-color: {c['ink']}; }}
QPushButton[kind="primary"]:hover {{ background: {c['ink']}; color: {c['signal']}; }}
QToolButton[kind="ribbon"] {{ background: transparent; border: {S}px solid transparent; min-width: 56px;
  max-width: 84px; min-height: {sz['ribbon-button']}px; padding: 2px; font-size: 10px; font-weight: 500; }}
QToolButton[kind="ribbon"]:hover {{ border-color: {c['ink']}; background: {c['paper']}; }}
QToolButton[kind="ribbon"]:pressed {{ background: {c['ink']}; color: {c['paper']}; }}
QToolButton[kind="ribbon"]:disabled {{ color: {c['muted']}; border: {S}px solid transparent; }}
QToolButton[kind="flat"] {{ background: transparent; border: {S}px solid transparent; padding: 2px 6px; }}
QToolButton[kind="flat"]:hover {{ border-color: {c['ink']}; }}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QAbstractSpinBox {{
  background: {c['paper']}; color: {c['ink']}; border: {S}px solid {c['structure']}; padding: 3px 6px;
  min-height: 18px; font-family: {M}; font-size: 12px; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{
  border-color: {c['signal']}; }}
QComboBox::drop-down {{ border-left: {S}px solid {c['structure']}; width: 18px; }}
QComboBox QAbstractItemView {{ background: {c['paper']}; border: {S}px solid {c['structure']}; }}
QCheckBox, QRadioButton {{ background: transparent; font-family: {M}; font-size: 11px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 12px; height: 12px; border: {S}px solid {c['structure']};
  background: {c['paper']}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {c['signal']}; }}

QTabWidget::pane {{ border: none; border-top: {S}px solid {c['structure']}; }}
QTabBar {{ background: {c['chrome']}; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 6px 12px; border: none;
  border-bottom: {b['active-tab']}px solid transparent; font-family: {M}; font-size: 11px; font-weight: 700; }}
QTabBar::tab:selected {{ color: {c['ink']}; border-bottom-color: {c['signal']}; }}
QTabBar::tab:hover {{ color: {c['ink']}; }}

QTreeView, QTreeWidget, QListView, QListWidget, QTableView, QTableWidget {{ background: {c['paper']};
  border: none; alternate-background-color: {c['panel']}; outline: 0; }}
QTreeView::item, QListView::item {{ min-height: {sz['row']}px; border-bottom: {H}px solid {c['panel']}; }}
QTreeView::item:hover, QListView::item:hover {{ background: {c['chrome']}; }}
QTreeView::item:selected, QListView::item:selected {{ background: {c['signal-tint']}; color: {c['ink']};
  border-left: {S}px solid {c['signal']}; }}
QHeaderView::section {{ background: {c['chrome']}; border: none; border-bottom: {S}px solid {c['structure']};
  border-right: {H}px solid {c['hairline']}; padding: 4px 6px; font-family: {M}; font-size: 10px; font-weight: 700; }}

QScrollBar:vertical {{ background: {c['panel']}; width: 12px; border-left: {H}px solid {c['hairline']}; }}
QScrollBar:horizontal {{ background: {c['panel']}; height: 12px; border-top: {H}px solid {c['hairline']}; }}
QScrollBar::handle {{ background: {c['muted']}; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {c['ink']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QMenu {{ background: {c['paper']}; border: {S}px solid {c['ink']}; font-size: 13px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; min-height: 16px; }}
QMenu::item:selected {{ background: {c['signal']}; color: {c['on-signal']}; }}
QMenu::separator {{ height: {S}px; background: {c['ink']}; margin: 2px 0; }}
QMenuBar {{ background: {c['chrome']}; font-family: {M}; font-size: 11px; font-weight: 700; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {c['ink']}; color: {c['paper']}; }}

QSplitter::handle {{ background: {c['structure']}; }}
QSplitter::handle:horizontal {{ width: {S}px; }}
QSplitter::handle:vertical {{ height: {S}px; }}
QProgressBar {{ background: {c['paper']}; border: {S}px solid {c['structure']}; height: 10px; text-align: center;
  font-family: {M}; font-size: 10px; }}
QProgressBar::chunk {{ background: {c['signal']}; }}
QGroupBox {{ border: {S}px solid {c['structure']}; margin-top: 14px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; font-family: {M}; font-size: 11px;
  font-weight: 700; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {c['chrome']}; padding: 4px 8px; font-family: {M}; font-size: 11px; }}
"""


def view_params(mode="light"):
    """FreeCAD View preferences that make the 3D view match the tokens: (type, key, value)."""
    c = colors(mode)

    def packed(hexcol):
        h = hexcol.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (r << 24) | (g << 16) | (b << 8)

    return [
        ("Bool", "Simple", False), ("Bool", "Gradient", True), ("Bool", "RadialGradient", False),
        ("Unsigned", "BackgroundColor2", packed(c["vp-top"])), ("Unsigned", "BackgroundColor3", packed(c["vp-bottom"])),
        ("Unsigned", "HighlightColor", packed(c["preselect"])), ("Unsigned", "SelectionColor", packed(c["select"])),
        ("Unsigned", "DefaultShapeColor", packed(c["vp-face"])), ("Unsigned", "DefaultShapeLineColor", packed(c["vp-edge"])),
        ("Int", "DefaultShapeLineWidth", 2),
    ]
