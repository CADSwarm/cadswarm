"""The professional workspace's look, as plain data and strings (no Qt, no FreeCAD: unit-testable).

While the CADSwarm workbench is active, FreeCAD wears this look: neutral light greys with one blue
accent (or the dark variant), a dense 12 px UI, a neutral grey gradient behind the model and blue
selection. Everything here is a value or a pure function; ``workspace.py`` applies it and puts the
user's own settings back afterwards.

* ``tokens(mode)``             the colour tokens for "light" or "dark"
* ``app_stylesheet(t)``        the Qt stylesheet for the whole application (main window, menus, docks...)
* ``ribbon_stylesheet(t)``     the command ribbon's own sheet
* ``view_params(t)``           the FreeCAD parameters behind the 3D view's background and selection colours
* ``ParamStash``               saves a parameter's old value (or that it did not exist) and restores it
"""
import json
from string import Template

# ---------------------------------------------------------------------------------------------- colours

# The same tokens as the web app (docs/superpowers/research/2026-11-06-pro-cad-ui-and-ai-build.md, item 20),
# so a screenshot of FreeCAD and of the site read as one product. Original values, generic CAD vocabulary.
LIGHT = {
    "BG": "#eef0f3",           # --chrome-bg: main window, docks, ribbon
    "CHROME": "#eef0f3",       # menu bar, title strips, tab bars
    "SURFACE": "#ffffff",      # --panel-bg: lists, editors, the ribbon's page
    "SURFACE_2": "#f5f6f8",    # --panel-alt: buttons, group headers, alternate rows
    "HOVER": "#f5f6f8",        # hover row
    "ACCENT_SOFT": "#e4edfb",  # --accent-tint: selected rows, the active selection box
    "LINE": "#d3d7dd",         # --border
    "LINE_STRONG": "#bfc5cd",
    "HANDLE_HOVER": "#98a0ab",
    "TEXT": "#1f2329",         # --text
    "BODY": "#353b44",
    "MUTED": "#5e6773",        # --text-muted
    "DISABLED_TEXT": "#a0a7b0",  # --suppressed
    "ACCENT": "#2b6cd9",       # --accent
    "ACCENT_HOVER": "#3a7be6",
    "ACCENT_DISABLED": "#a9c3ee",
    "ON_ACCENT": "#ffffff",
    "OK": "#2e9d5b",
    "WARN": "#d99a00",
    "ERR": "#d23f3f",
    "ICON": "#353b44",         # icon strokes
    # The 3D view: a vertical gradient (blue-grey at the top, near-white at the bottom), two highlights.
    "VIEW_TOP": "#c5cfda",
    "VIEW_BOTTOM": "#f3f5f7",
    "VIEW_MID": "#dce2e9",
    "SELECT": "#1f7ae0",       # --select-highlight
    "PRESELECT": "#f2a93b",    # --hover-highlight
    "SHAPE": "#c4cbd4",
    "SHAPE_LINE": "#20242a",   # --edge
}

DARK = {
    "BG": "#1b1f24",
    "CHROME": "#1b1f24",
    "SURFACE": "#22272e",
    "SURFACE_2": "#2a3038",
    "HOVER": "#2a3038",
    "ACCENT_SOFT": "#24364f",
    "LINE": "#363d47",
    "LINE_STRONG": "#475060",
    "HANDLE_HOVER": "#5d6675",
    "TEXT": "#e6e8eb",
    "BODY": "#c8ccd2",
    "MUTED": "#9aa3ae",
    "DISABLED_TEXT": "#6b737d",
    "ACCENT": "#4c8df0",
    "ACCENT_HOVER": "#669ef3",
    "ACCENT_DISABLED": "#2f4b73",
    "ON_ACCENT": "#ffffff",
    "OK": "#3fb871",
    "WARN": "#e8b024",
    "ERR": "#ef5b5b",
    "ICON": "#c8ccd2",
    "VIEW_TOP": "#2a3038",
    "VIEW_BOTTOM": "#474f59",
    "VIEW_MID": "#383f48",
    "SELECT": "#4c9aff",
    "PRESELECT": "#f2a93b",
    "SHAPE": "#9aa3ae",
    "SHAPE_LINE": "#0e1013",
}

# Teammates' colours, shared with the web app.
TEAMMATES = ("#2b6cd9", "#e5484d", "#2fa567", "#d97706", "#8b5cf6", "#0ea5a4", "#db2777", "#65a30d")

MODES = ("light", "dark", "auto")


def tokens(mode="light"):
    """The colour tokens for a mode ("light" or "dark"; anything else is light)."""
    return dict(DARK if mode == "dark" else LIGHT)


def pick_mode(setting, window_rgb=None):
    """"light" / "dark" from the user's choice; "auto" follows FreeCAD's own theme (its window colour)."""
    if setting in ("light", "dark"):
        return setting
    if window_rgb is None:
        return "light"
    r, g, b = window_rgb[:3]
    return "dark" if (0.299 * r + 0.587 * g + 0.114 * b) < 128 else "light"


def hex_rgb(colour):
    c = colour.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def packed(colour, alpha=255):
    """A #rrggbb colour as FreeCAD stores it in a parameter: unsigned 0xRRGGBBAA."""
    r, g, b = hex_rgb(colour)
    return (r << 24) | (g << 16) | (b << 8) | (alpha & 0xFF)


def unpacked(value):
    value = int(value) & 0xFFFFFFFF
    return "#%02x%02x%02x" % ((value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF)


# ----------------------------------------------------------------------------------------- stylesheets

UI_PX = 12  # one dense UI size everywhere (tree, panels)
UI_PX_SMALL = 11  # ribbon labels, the status bar
# The system UI sans, first one installed wins (Qt 5 stylesheets take a single family, so workspace.py picks).
FONTS = ("Segoe UI", "SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue", "Inter", "Roboto", "Noto Sans",
         "Cantarell", "Ubuntu", "DejaVu Sans")


def pick_font(installed):
    """The first of FONTS that is installed ("" = leave Qt's default)."""
    have = {f.lower() for f in installed or ()}
    return next((f for f in FONTS if f.lower() in have), "")

APP = Template("""
QMainWindow, QDialog { background: $BG; color: $TEXT; }
QWidget { font-size: ${PX}px;$FONT }
QMainWindow::separator { background: $LINE; width: 1px; height: 1px; }
QMainWindow::separator:hover { background: $ACCENT; }
QToolTip { background: $SURFACE; color: $TEXT; border: 1px solid $LINE_STRONG; padding: 4px 6px; }

QMenuBar { background: $CHROME; color: $TEXT; border-bottom: 1px solid $LINE; padding: 1px 4px; }
QMenuBar::item { background: transparent; padding: 4px 9px; border-radius: 3px; }
QMenuBar::item:selected { background: $HOVER; color: $TEXT; }
QMenuBar::item:pressed { background: $ACCENT_SOFT; }
QMenu { background: $SURFACE; color: $TEXT; border: 1px solid $LINE_STRONG; padding: 4px 0; }
QMenu::item { padding: 5px 26px 5px 26px; }
QMenu::item:selected { background: $ACCENT; color: $ON_ACCENT; }
QMenu::item:disabled { color: $DISABLED_TEXT; }
QMenu::separator { height: 1px; background: $LINE; margin: 4px 8px; }

QToolBar { background: $CHROME; border: none; border-bottom: 1px solid $LINE; spacing: 2px; padding: 2px; }
QToolBar::separator { background: $LINE; width: 1px; margin: 4px 3px; }
QToolBar QToolButton { background: transparent; color: $TEXT; border: 1px solid transparent; border-radius: 3px;
    padding: 3px; }
QToolBar QToolButton:hover { background: $HOVER; border-color: $LINE; }
QToolBar QToolButton:pressed, QToolBar QToolButton:checked { background: $ACCENT_SOFT; border-color: $ACCENT; }

QDockWidget { color: $TEXT; titlebar-close-icon: none; }
QDockWidget::title { background: $CHROME; color: $BODY; padding: 5px 8px; border-bottom: 1px solid $LINE;
    text-align: left; }
QDockWidget > QWidget { background: $BG; }

QTreeView, QTreeWidget, QListView, QListWidget, QTableView, QTableWidget {
    background: $SURFACE; color: $TEXT; border: 1px solid $LINE; alternate-background-color: $SURFACE_2;
    selection-background-color: $ACCENT_SOFT; selection-color: $TEXT; outline: 0; }
QTreeView::item, QListView::item { padding: 2px 0; }
QTreeView::item:hover, QListView::item:hover, QTableView::item:hover { background: $HOVER; }
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected { background: $ACCENT_SOFT;
    color: $TEXT; }
QHeaderView { background: $SURFACE_2; border: none; }
QHeaderView::section { background: $SURFACE_2; color: $MUTED; border: none; border-right: 1px solid $LINE;
    border-bottom: 1px solid $LINE; padding: 4px 6px; font-weight: 600; }
QTableCornerButton::section { background: $SURFACE_2; border: none; }

QTabWidget::pane { border: 1px solid $LINE; background: $SURFACE; top: -1px; }
QTabBar { background: transparent; }
QTabBar::tab { background: $CHROME; color: $MUTED; border: 1px solid $LINE; border-bottom: none;
    padding: 5px 12px; margin-right: 1px; border-top-left-radius: 3px; border-top-right-radius: 3px; }
QTabBar::tab:hover { color: $TEXT; background: $HOVER; }
QTabBar::tab:selected { background: $SURFACE; color: $TEXT; border-top: 2px solid $ACCENT; }
QTabBar::tab:bottom { border-top: none; border-bottom: 1px solid $LINE; border-top-left-radius: 0;
    border-top-right-radius: 0; border-bottom-left-radius: 3px; border-bottom-right-radius: 3px; }
QTabBar::tab:bottom:selected { border-bottom: 2px solid $ACCENT; }
QMdiArea { background: $BG; }

QLineEdit, QAbstractSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: $SURFACE; color: $TEXT; border: 1px solid $LINE_STRONG; border-radius: 3px;
    selection-background-color: $ACCENT; selection-color: $ON_ACCENT; }
QLineEdit, QAbstractSpinBox, QComboBox { min-height: 20px; padding: 1px 6px; }
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: $ACCENT; }
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled { color: $DISABLED_TEXT; background: $SURFACE_2; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView { background: $SURFACE; color: $TEXT; border: 1px solid $LINE_STRONG;
    selection-background-color: $ACCENT; selection-color: $ON_ACCENT; }

QPushButton { background: $SURFACE_2; color: $TEXT; border: 1px solid $LINE_STRONG; border-radius: 3px;
    padding: 4px 12px; min-height: 18px; }
QPushButton:hover { background: $HOVER; border-color: $ACCENT; }
QPushButton:pressed { background: $ACCENT_SOFT; }
QPushButton:default { border-color: $ACCENT; }
QPushButton:disabled { color: $DISABLED_TEXT; border-color: $LINE; }
QCheckBox, QRadioButton { color: $TEXT; spacing: 6px; }
QGroupBox { border: 1px solid $LINE; border-radius: 3px; margin-top: 10px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; color: $MUTED; }
QProgressBar { background: $SURFACE_2; border: 1px solid $LINE; border-radius: 3px; text-align: center;
    color: $TEXT; max-height: 14px; }
QProgressBar::chunk { background: $ACCENT; border-radius: 2px; }

QScrollBar:vertical { background: transparent; width: 11px; margin: 0; }
QScrollBar::handle:vertical { background: $LINE_STRONG; border-radius: 4px; min-height: 24px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: $HANDLE_HOVER; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 0; }
QScrollBar::handle:horizontal { background: $LINE_STRONG; border-radius: 4px; min-width: 24px; margin: 2px; }
QScrollBar::handle:horizontal:hover { background: $HANDLE_HOVER; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QStatusBar { background: $CHROME; color: $BODY; border-top: 1px solid $LINE; min-height: 24px;
    max-height: 24px; font-size: ${PX_SMALL}px; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: $BODY; padding: 0 6px; font-size: ${PX_SMALL}px; }
QSplitter::handle { background: $LINE; }
QSlider::groove:horizontal { background: $LINE; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: $ACCENT; width: 12px; margin: -5px 0; border-radius: 6px; }
""")

RIBBON = Template("""
#CadSwarmRibbon { background: $CHROME; border: none; border-bottom: 1px solid $LINE_STRONG; padding: 0; }
#csRibbon { background: $CHROME; font-size: ${PX}px; }
#csRibbon QWidget#csRibbonBar { background: $CHROME; }
#csRibbon QLabel#csMark { color: $ACCENT; font-weight: 700; padding: 0 10px 0 8px; letter-spacing: 1px; }
#csRibbon QToolButton#csQuick { background: transparent; border: 1px solid transparent; border-radius: 3px;
    padding: 2px; }
#csRibbon QToolButton#csQuick:hover { background: $HOVER; border-color: $LINE; }
#csRibbon QLineEdit#csSearch { background: $SURFACE; border: 1px solid $LINE_STRONG; border-radius: 12px;
    padding: 0 10px; min-height: 22px; color: $TEXT; }
#csRibbon QLineEdit#csSearch:focus { border-color: $ACCENT; }
#csRibbon QTabBar#csTabs { background: transparent; }
#csRibbon QTabBar#csTabs::tab { background: transparent; color: $BODY; border: none;
    border-bottom: 2px solid transparent; padding: 6px 14px 5px 14px; margin: 0; font-weight: 600;
    border-radius: 0; }
#csRibbon QTabBar#csTabs::tab:hover { color: $TEXT; background: $HOVER; }
#csRibbon QTabBar#csTabs::tab:selected { color: $ACCENT; border-bottom: 2px solid $ACCENT; background: $SURFACE; }
#csRibbon QStackedWidget#csPages { background: $SURFACE; border-top: 1px solid $LINE; }
#csRibbon QWidget#csPage { background: $SURFACE; }
#csRibbon QFrame#csGroup { background: transparent; border: none; border-right: 1px solid $LINE; }
#csRibbon QLabel#csGroupLabel { color: $MUTED; font-size: ${PX_SMALL}px; padding: 0 0 2px 0; }
#csRibbon QToolButton#csCommand { background: transparent; color: $TEXT; border: 1px solid transparent;
    border-radius: 3px; padding: 2px 4px 4px 4px; font-size: ${PX_SMALL}px; }
#csRibbon QToolButton#csCommand:hover { background: $HOVER; border-color: $LINE; }
#csRibbon QToolButton#csCommand:pressed, #csRibbon QToolButton#csCommand:checked { background: $ACCENT_SOFT;
    border-color: $ACCENT; }
#csRibbon QToolButton#csCommand:disabled { color: $DISABLED_TEXT; }
""")


def _subst(template, t, family=""):
    values = dict(t)
    family = (family or "").replace('"', "")
    values.update(PX=str(UI_PX), PX_SMALL=str(UI_PX_SMALL), FONT=f' font-family: "{family}";' if family else "")
    return template.substitute(values)


def app_stylesheet(t, family=""):
    """The application-wide stylesheet. Classes only (the one QWidget rule sets the font, never a
    background), so the 3D view's OpenGL surface and FreeCAD's painted widgets draw themselves."""
    return _subst(APP, t, family)


def ribbon_stylesheet(t, family=""):
    return _subst(RIBBON, t, family)


def panel_tokens(t):
    """The CadSwarm panel's tokens (theme.PANEL_TEMPLATE) in the pro look's colours."""
    return {"BG": t["BG"], "SURFACE": t["SURFACE"], "SURFACE_2": t["SURFACE_2"], "LINE": t["LINE"],
            "LINE_STRONG": t["LINE_STRONG"], "TEXT": t["TEXT"], "BODY": t["BODY"], "MUTED": t["MUTED"],
            "ACCENT": t["ACCENT"], "ACCENT_HOVER": t["ACCENT_HOVER"], "OK": t["OK"], "ERR": t["ERR"],
            "WARN": t["WARN"], "DISABLED_TEXT": t["DISABLED_TEXT"], "ACCENT_DISABLED": t["ACCENT_DISABLED"],
            "HANDLE_HOVER": t["HANDLE_HOVER"], "HOVER": t["HOVER"], "ACCENT_SOFT": t["ACCENT_SOFT"]}


# ------------------------------------------------------------------------------------ FreeCAD parameters

VIEW = "User parameter:BaseApp/Preferences/View"
TOOLBARS = "User parameter:BaseApp/MainWindow/Toolbars"


def view_params(t):
    """(group, type, name, value) for the 3D view's look: a two-colour linear gradient, blue selection,
    light-blue preselection, and a neutral default part colour."""
    return [
        (VIEW, "Bool", "Simple", False),
        (VIEW, "Bool", "Gradient", True),
        (VIEW, "Bool", "RadialGradient", False),
        (VIEW, "Bool", "UseBackgroundColorMid", False),
        (VIEW, "Unsigned", "BackgroundColor", packed(t["VIEW_MID"])),
        (VIEW, "Unsigned", "BackgroundColor2", packed(t["VIEW_TOP"])),
        (VIEW, "Unsigned", "BackgroundColor3", packed(t["VIEW_BOTTOM"])),
        (VIEW, "Unsigned", "BackgroundColor4", packed(t["VIEW_MID"])),
        (VIEW, "Bool", "EnablePreselection", True),
        (VIEW, "Bool", "EnableSelection", True),
        (VIEW, "Unsigned", "HighlightColor", packed(t["PRESELECT"])),
        (VIEW, "Unsigned", "SelectionColor", packed(t["SELECT"])),
        (VIEW, "Unsigned", "DefaultShapeColor", packed(t["SHAPE"])),
        (VIEW, "Unsigned", "DefaultShapeLineColor", packed(t["SHAPE_LINE"])),
    ]


# Two defaults that no real value equals both of: a parameter that returns each one back is missing.
_PROBES = {"Unsigned": (0xDEADBEEF, 0xBEEFDEAD), "Int": (-987654321, 987654321), "Bool": (True, False),
           "String": ("\x00cs-a", "\x00cs-b"), "Float": (-9.87654e300, 9.87654e300)}


def read_param(group, kind, name):
    """(existed, value) for one parameter of a FreeCAD ParamGet group (or anything with the same methods)."""
    a, b = _PROBES[kind]
    get = getattr(group, "Get" + kind)
    first, second = get(name, a), get(name, b)
    if first == a and second == b:
        return False, None
    return True, first


def write_param(group, kind, name, value):
    getattr(group, "Set" + kind)(name, value)


def remove_param(group, kind, name):
    rem = getattr(group, "Rem" + kind, None)
    if rem is not None:
        rem(name)


class ParamStash:
    """Remembers the values a set of parameters had before we changed them, and puts them back.

    ``param_get(path)`` returns a group (FreeCAD's ``App.ParamGet``). The saved values are kept in
    ``store`` (a group too: CadSwarm's own preferences) under ``key`` as JSON, so a FreeCAD that closed
    or crashed while the workbench was active still gets the user's own settings back next time.
    A second ``apply`` before a ``restore`` keeps the first saved values (never saves our own look as
    "the user's")."""

    def __init__(self, param_get, store=None, key="ProUiStash"):
        self.param_get = param_get
        self.store = store
        self.key = key
        self.saved = self._load()

    def _load(self):
        if self.store is None:
            return []
        try:
            raw = self.store.GetString(self.key, "")
            return json.loads(raw) if raw else []
        except (ValueError, TypeError, AttributeError):
            return []

    def _save(self):
        if self.store is None:
            return
        if self.saved:
            self.store.SetString(self.key, json.dumps(self.saved))
        else:
            remove_param(self.store, "String", self.key)

    @property
    def pending(self):
        """True while values we changed are still waiting to be put back."""
        return bool(self.saved)

    def apply(self, params):
        """Set each (group, type, name, value), first remembering what was there. Returns the failures."""
        known = {(s["group"], s["type"], s["name"]) for s in self.saved}
        failures = []
        for group, kind, name, value in params:
            try:
                grp = self.param_get(group)
                if (group, kind, name) not in known:
                    existed, old = read_param(grp, kind, name)
                    self.saved.append({"group": group, "type": kind, "name": name, "existed": existed,
                                       "value": old})
                    known.add((group, kind, name))
                    self._save()
                write_param(grp, kind, name, value)
            except Exception as e:  # noqa: BLE001 - one odd parameter must not stop the rest
                failures.append((name, str(e)))
        return failures

    def remember(self, keys):
        """Record what (group, type, name) parameters hold now, without changing them, so ``restore`` puts
        them back even if FreeCAD itself rewrites them meanwhile (a toolbar's remembered visibility)."""
        known = {(s["group"], s["type"], s["name"]) for s in self.saved}
        for group, kind, name in keys:
            if (group, kind, name) in known:
                continue
            try:
                existed, old = read_param(self.param_get(group), kind, name)
            except Exception:  # noqa: BLE001
                continue
            self.saved.append({"group": group, "type": kind, "name": name, "existed": existed, "value": old})
            known.add((group, kind, name))
        self._save()

    def restore(self):
        """Put every remembered parameter back (or remove it if it was not there). Returns the failures."""
        failures, left = [], []
        for s in reversed(self.saved):
            try:
                grp = self.param_get(s["group"])
                if s["existed"]:
                    write_param(grp, s["type"], s["name"], s["value"])
                else:
                    remove_param(grp, s["type"], s["name"])
            except Exception as e:  # noqa: BLE001
                failures.append((s["name"], str(e)))
                left.append(s)
        self.saved = list(reversed(left))
        self._save()
        return failures
