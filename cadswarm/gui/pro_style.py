"""The professional workspace's look, as plain data and strings (no Qt, no FreeCAD: unit-testable).

While the CADSwarm workbench is active, FreeCAD wears this look: CADSwarm's design tokens
(design/tokens.json: ink, paper and panel greys, one signal-orange accent, or the dark variant), a dense
12 px UI, a quiet grey gradient behind the model and orange selection. Everything here is a value or a pure function; ``workspace.py`` applies it and puts the
user's own settings back afterwards.

* ``tokens(mode)``             the colour tokens for "light" or "dark"
* ``app_stylesheet(t)``        the Qt stylesheet for the whole application (main window, menus, docks...)
* ``ribbon_stylesheet(t)``     the command ribbon's own sheet
* ``view_params(t)``           the FreeCAD parameters behind the 3D view's background and selection colours
* ``ParamStash``               saves a parameter's old value (or that it did not exist) and restores it
"""
import json
import os
from string import Template

# ---------------------------------------------------------------------------------------------- colours

# The single source is design/tokens.json (shared with the web app and the branded desktop app): signal orange,
# warm neutrals, a restrained orange accent and softly rounded controls. This copy is only the fallback for an add-on folder that
# lost the design/ directory; tests keep it equal to the file.
_TOKENS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "design", "tokens.json")
_FALLBACK = {'light': {'ink': '#1D1D1F',
           'paper': '#FFFFFF',
           'panel': '#F5F5F7',
           'chrome': '#F5F5F7',
           'hairline': '#D2D2D7',
           'structure': '#C7C7CC',
           'muted': '#6E6E73',
           'signal': '#FF6B2C',
           'signal-ink': '#C2410C',
           'signal-tint': '#FFF0E8',
           'on-signal': '#FFFFFF',
           'warn': '#FF9F0A',
           'error': '#FF3B30',
           'error-tint': '#FFE5E3',
           'ok': '#34C759',
           'sk-under': '#0A84FF',
           'sk-full': '#1D1D1F',
           'sk-over': '#FF3B30',
           'sk-driven': '#8E8E93',
           'sk-construction': '#8E8E93',
           'preselect': '#FFB18A',
           'select': '#FF6B2C',
           'vp-top': '#E8EAF0',
           'vp-bottom': '#F6F7F9',
           'vp-edge': '#3A3A3C',
           'vp-face': '#C8CBD2',
           'axis-x': '#FF453A',
           'axis-y': '#30D158',
           'axis-z': '#0A84FF',
           'signal-hover': '#A8380D'},
 'dark': {'ink': '#F5F5F7',
          'paper': '#1C1C1E',
          'panel': '#2C2C2E',
          'chrome': '#1C1C1E',
          'hairline': '#38383A',
          'structure': '#48484A',
          'muted': '#AEAEB2',
          'signal': '#FF7A36',
          'signal-ink': '#FF9A62',
          'signal-tint': '#3B261B',
          'on-signal': '#1D1D1F',
          'warn': '#FFD60A',
          'error': '#FF453A',
          'error-tint': '#44211F',
          'ok': '#30D158',
          'sk-under': '#64D2FF',
          'sk-full': '#F5F5F7',
          'sk-over': '#FF453A',
          'sk-driven': '#8E8E93',
          'sk-construction': '#8E8E93',
          'preselect': '#FFAD80',
          'select': '#FF7A36',
          'vp-top': '#252527',
          'vp-bottom': '#353539',
          'vp-edge': '#E5E5EA',
          'vp-face': '#73737A',
          'axis-x': '#FF6961',
          'axis-y': '#30D158',
          'axis-z': '#64D2FF',
          'signal-hover': '#FFB07B'},
 'team': ['#7C3AED', '#0891B2', '#16A34A', '#DB2777', '#65A30D', '#0D9488', '#9333EA', '#A16207']}


def design_tokens():
    """The shared design specification, or its compact built-in fallback."""
    try:
        with open(_TOKENS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, KeyError):
        return {"color": _FALLBACK, "border": {"radius": 8}}


def design_colours():
    """{"light": {...}, "dark": {...}, "team": [...]} from design/tokens.json (or the fallback copy)."""
    return design_tokens().get("color", _FALLBACK)


def mix(a, b, t):
    """Mix two #rrggbb colours: t=0 gives a, t=1 gives b."""
    ah, bh = a.lstrip("#"), b.lstrip("#")
    return "#" + "".join("%02x" % round(int(ah[i:i + 2], 16) + (int(bh[i:i + 2], 16) - int(ah[i:i + 2], 16)) * t)
                         for i in (0, 2, 4))


def _palette(mode):
    c = {k: v.lower() for k, v in design_colours()[mode].items()}
    radius = int(design_tokens().get("border", {}).get("radius", 8))
    dark = mode == "dark"
    surface = c["chrome"] if dark else c["paper"]
    return {
        "RADIUS": f"{radius}px",
        "BG": c["panel"],               # main window, docks, the team panel
        "CHROME": c["chrome"],          # menu bar, title strips, tab bars
        "SURFACE": surface,             # lists, editors, cards
        "SURFACE_2": mix(c["chrome"], c["hairline"], 0.45) if dark else c["chrome"],  # buttons, alternate rows
        "HOVER": c["signal-tint"],
        "ACCENT_SOFT": c["signal-tint"],  # selected rows
        "LINE": c["hairline"],
        "LINE_STRONG": c["structure"],  # 2px structure borders
        "HANDLE_HOVER": c["muted"],
        "TEXT": c["ink"],
        "BODY": c["ink"],
        "MUTED": c["muted"],
        "DISABLED_TEXT": mix(c["ink"], c["panel"], 0.55),
        "ACCENT": c["signal-ink"],
        "ACCENT_HOVER": c["signal-hover"],
        "ACCENT_DISABLED": mix(c["signal"], c["panel"], 0.6),
        "ON_ACCENT": c["on-signal"],
        "OK": c["ok"],
        "WARN": c["warn"],
        "ERR": c["error"],
        "ICON": c["ink"],
        # The 3D view: a quiet two-grey vertical gradient, orange selection.
        "VIEW_TOP": c["vp-top"],
        "VIEW_BOTTOM": c["vp-bottom"],
        "VIEW_MID": mix(c["vp-top"], c["vp-bottom"], 0.5),
        "SELECT": c["select"],
        "PRESELECT": c["preselect"],
        "SHAPE": c["vp-face"],
        "SHAPE_LINE": c["vp-edge"],
    }


LIGHT = _palette("light")
DARK = _palette("dark")

# Teammates' colours, shared with the web app.
TEAMMATES = tuple(c.lower() for c in design_colours().get("team", _FALLBACK["team"]))

MODES = ("light", "dark", "auto")


def tokens(mode="light"):
    """The colour tokens for a mode ("light" or "dark"; anything else is light)."""
    return dict(DARK if mode == "dark" else LIGHT)


def mode_from_theme(theme_name, env_value=None):
    """"light" / "dark" for CADSwarm's own surfaces. CADSWARM_THEME wins; otherwise the app's theme name
    (the branded app writes "CADSwarm Light" / "CADSwarm Dark"; plain FreeCAD has "FreeCAD Dark" and the like)."""
    if env_value in ("light", "dark"):
        return env_value
    return "dark" if "dark" in (theme_name or "").lower() else "light"


def app_mode():
    """The running app's mode: CADSWARM_THEME, else FreeCAD's MainWindow/Theme parameter. Light without FreeCAD."""
    theme_name = ""
    try:
        import FreeCAD  # noqa: PLC0415
        theme_name = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/MainWindow").GetString("Theme", "")
    except Exception:  # noqa: BLE001 - tests, or FreeCAD not started
        pass
    return mode_from_theme(theme_name, os.environ.get("CADSWARM_THEME"))


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
QMenuBar::item { background: transparent; padding: 4px 9px; border-radius: $RADIUS; }
QMenuBar::item:selected { background: $HOVER; color: $TEXT; }
QMenuBar::item:pressed { background: $ACCENT_SOFT; }
QMenu { background: $SURFACE; color: $TEXT; border: 1px solid $LINE_STRONG; padding: 4px 0; }
QMenu::item { padding: 5px 26px 5px 26px; }
QMenu::item:selected { background: $ACCENT; color: $ON_ACCENT; }
QMenu::item:disabled { color: $DISABLED_TEXT; }
QMenu::separator { height: 1px; background: $LINE; margin: 4px 8px; }

QToolBar { background: $CHROME; border: none; border-bottom: 1px solid $LINE; spacing: 2px; padding: 2px; }
QToolBar::separator { background: $LINE; width: 1px; margin: 4px 3px; }
QToolBar QToolButton { background: transparent; color: $TEXT; border: 1px solid transparent; border-radius: $RADIUS;
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
    padding: 5px 12px; margin-right: 1px; border-top-left-radius: $RADIUS; border-top-right-radius: $RADIUS; }
QTabBar::tab:hover { color: $TEXT; background: $HOVER; }
QTabBar::tab:selected { background: $SURFACE; color: $TEXT; border-top: 2px solid $ACCENT; }
QTabBar::tab:bottom { border-top: none; border-bottom: 1px solid $LINE; border-top-left-radius: $RADIUS;
    border-top-right-radius: $RADIUS; border-bottom-left-radius: $RADIUS; border-bottom-right-radius: $RADIUS; }
QTabBar::tab:bottom:selected { border-bottom: 2px solid $ACCENT; }
QMdiArea { background: $BG; }

QLineEdit, QAbstractSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: $SURFACE; color: $TEXT; border: 1px solid $LINE_STRONG; border-radius: $RADIUS;
    selection-background-color: $ACCENT; selection-color: $ON_ACCENT; }
QLineEdit, QAbstractSpinBox, QComboBox { min-height: 20px; padding: 1px 6px; }
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: $ACCENT; }
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled { color: $DISABLED_TEXT; background: $SURFACE_2; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView { background: $SURFACE; color: $TEXT; border: 1px solid $LINE_STRONG;
    selection-background-color: $ACCENT; selection-color: $ON_ACCENT; }

QPushButton { background: $SURFACE_2; color: $TEXT; border: 1px solid $LINE_STRONG; border-radius: $RADIUS;
    padding: 4px 12px; min-height: 18px; }
QPushButton:hover { background: $HOVER; border-color: $ACCENT; }
QPushButton:pressed { background: $ACCENT_SOFT; }
QPushButton:default { border-color: $ACCENT; }
QPushButton:disabled { color: $DISABLED_TEXT; border-color: $LINE; }
QCheckBox, QRadioButton { color: $TEXT; spacing: 6px; }
QGroupBox { border: 1px solid $LINE; border-radius: $RADIUS; margin-top: 10px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; color: $MUTED; }
QProgressBar { background: $SURFACE_2; border: 1px solid $LINE; border-radius: $RADIUS; text-align: center;
    color: $TEXT; max-height: 14px; }
QProgressBar::chunk { background: $ACCENT; border-radius: $RADIUS; }

QScrollBar:vertical { background: transparent; width: 11px; margin: 0; }
QScrollBar::handle:vertical { background: $LINE; border-radius: $RADIUS; min-height: 24px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: $HANDLE_HOVER; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 0; }
QScrollBar::handle:horizontal { background: $LINE; border-radius: $RADIUS; min-width: 24px; margin: 2px; }
QScrollBar::handle:horizontal:hover { background: $HANDLE_HOVER; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QStatusBar { background: $CHROME; color: $BODY; border-top: 1px solid $LINE; min-height: 24px;
    max-height: 24px; font-size: ${PX_SMALL}px; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: $BODY; padding: 0 6px; font-size: ${PX_SMALL}px; }
QSplitter::handle { background: $LINE; }
QSlider::groove:horizontal { background: $LINE; height: 4px; border-radius: $RADIUS; }
QSlider::handle:horizontal { background: $ACCENT; width: 12px; margin: -5px 0; border-radius: $RADIUS; }
""")

RIBBON = Template("""
#CadSwarmRibbon { background: $CHROME; border: none; border-bottom: 1px solid $LINE_STRONG; padding: 0; }
#csRibbon { background: $CHROME; font-size: ${PX}px; }
#csRibbon QWidget#csRibbonBar { background: $CHROME; }
#csRibbon QLabel#csMark { color: $ACCENT; font-weight: 700; padding: 0 10px 0 8px; letter-spacing: 1px; }
#csRibbon QToolButton#csQuick { background: transparent; border: 1px solid transparent; border-radius: $RADIUS;
    padding: 2px; }
#csRibbon QToolButton#csQuick:hover { background: $HOVER; border-color: $LINE; }
#csRibbon QLineEdit#csSearch { background: $SURFACE; border: 1px solid $LINE_STRONG; border-radius: $RADIUS;
    padding: 0 10px; min-height: 22px; color: $TEXT; }
#csRibbon QLineEdit#csSearch:focus { border-color: $ACCENT; }
#csRibbon QTabBar#csTabs { background: transparent; }
#csRibbon QTabBar#csTabs::tab { background: transparent; color: $BODY; border: none;
    border-bottom: 2px solid transparent; padding: 6px 14px 5px 14px; margin: 0; font-weight: 600;
    border-radius: $RADIUS; }
#csRibbon QTabBar#csTabs::tab:hover { color: $TEXT; background: $HOVER; }
#csRibbon QTabBar#csTabs::tab:selected { color: $ACCENT; border-bottom: 2px solid $ACCENT; background: $SURFACE; }
#csRibbon QStackedWidget#csPages { background: $SURFACE; border-top: 1px solid $LINE; }
#csRibbon QWidget#csPage { background: $SURFACE; }
#csRibbon QFrame#csGroup { background: transparent; border: none; border-right: 1px solid $LINE; }
#csRibbon QLabel#csGroupLabel { color: $MUTED; font-size: ${PX_SMALL}px; padding: 0 0 2px 0; }
#csRibbon QToolButton#csCommand { background: transparent; color: $TEXT; border: 1px solid transparent;
    border-radius: $RADIUS; padding: 2px 4px 4px 4px; font-size: ${PX_SMALL}px; }
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
            "RADIUS": t["RADIUS"], "LINE_STRONG": t["LINE_STRONG"], "TEXT": t["TEXT"], "BODY": t["BODY"], "MUTED": t["MUTED"],
            "ACCENT": t["ACCENT"], "ACCENT_HOVER": t["ACCENT_HOVER"], "OK": t["OK"], "ERR": t["ERR"],
            "WARN": t["WARN"], "DISABLED_TEXT": t["DISABLED_TEXT"], "ACCENT_DISABLED": t["ACCENT_DISABLED"],
            "HANDLE_HOVER": t["HANDLE_HOVER"], "HOVER": t["HOVER"], "ACCENT_SOFT": t["ACCENT_SOFT"],
            "ON_ACCENT": t["ON_ACCENT"], "CHROME": t["CHROME"]}


# ------------------------------------------------------------------------------------ FreeCAD parameters

VIEW = "User parameter:BaseApp/Preferences/View"
TOOLBARS = "User parameter:BaseApp/MainWindow/Toolbars"


def view_params(t):
    """(group, type, name, value) for the 3D view's look: a two-colour linear gradient, orange selection,
    light-orange preselection, and a neutral default part colour."""
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
