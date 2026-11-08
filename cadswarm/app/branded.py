"""CADSwarm desktop: the full FreeCAD workbench suite, shipped and branded as CADSwarm.

Like an editor built on another open-source editor, CADSwarm runs FreeCAD's real application (every workbench,
the sketcher, PartDesign, Assembly, TechDraw, FEM, the tree, property view, report view and Python console)
unmodified, and puts CADSwarm's name, icon, colours and the CADSwarm team panel (AI build, chat, multiplayer,
library, reviewer, FEA) on top. FreeCAD's source is not copied into this repository; it is the runtime.

How the branding works, all without touching FreeCAD's install folder:

* FreeCAD's settings for CADSwarm live in CADSwarm's own folder (``FREECAD_USER_HOME``), never the person's
  FreeCAD profile, so installing CADSwarm changes nothing in an existing FreeCAD.
* Before the GUI starts we set FreeCAD's branding config (``Application``/``ExeName``/``WindowTitle``/
  ``WindowIcon``) and write a CADSwarm theme (FreeCAD's own theme engine, with our colour tokens) plus the
  3D-view, selection and sketcher colours into that profile.
* After the window exists, ``Rebrander`` keeps every window title and menu entry saying CADSwarm, drops
  links to FreeCAD's website, forum and donation pages, and replaces the About box.
* The CADSwarm add-on (this repository's ``InitGui.py``) is linked into the profile's ``Mod`` folder, so the
  team panel opens on every start, whatever workbench is active.

Stdlib only at import time (tests import it without FreeCAD).
"""
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOKENS = os.path.join(REPO, "design", "tokens.json")
LOGO = os.path.join(REPO, "cadswarm", "gui", "cadswarm.svg")

APP_NAME = "CADSwarm"

# FreeCAD's application config keys read when its GUI starts (see FreeCAD src/Gui/StartupProcess.cpp).
BRAND_CONFIG = {
    "Application": APP_NAME,
    "ExeName": APP_NAME,
    "ExeVendor": APP_NAME,
    "WindowTitle": APP_NAME,
    "StartWorkbench": "PartDesignWorkbench",
}

# Help-menu commands that send people to FreeCAD's website, forum, wiki or donation pages.
FREECAD_LINK_COMMANDS = {
    "Std_FreeCADWebsite", "Std_FreeCADUserHub", "Std_FreeCADPowerUserHub", "Std_FreeCADForum",
    "Std_FreeCADFAQ", "Std_FreeCADDonation", "Std_ReportBug", "Std_DevHandbook", "Std_OnlineHelp",
    "Std_WhatsThis", "Std_TextDocument",
}

_WORD = re.compile(r"\bFreeCAD\b")


def rebrand_text(text):
    """Window titles and menu labels: 'FreeCAD' becomes 'CADSwarm' (version numbers dropped with it)."""
    if not text or "FreeCAD" not in text:
        return text
    text = re.sub(r"\bFreeCAD\s+\d+(?:\.\d+)*(?:\s*\([^)]*\))?", APP_NAME, text)
    return _WORD.sub(APP_NAME, text)


def rgba_uint(hex_colour, alpha=255):
    """FreeCAD stores colours as unsigned 0xRRGGBBAA."""
    h = hex_colour.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r << 24) | (g << 16) | (b << 8) | alpha


def colours(mode="light"):
    with open(TOKENS, encoding="utf-8") as f:
        return json.load(f)["color"][mode]


def theme_overrides(mode="light"):
    """CADSwarm values for FreeCAD's theme tokens (FreeCAD.qss reads these through its theme engine)."""
    c = colours(mode)
    dark = mode == "dark"
    return {
        "PrimaryColor": c["chrome"],
        "TextForegroundColor": c["ink"],
        "StylesheetIconsColor": "white" if dark else "black",
        "GeneralBorderColor": c["structure"] if not dark else c["hairline"],
        "GeneralBackgroundColor": c["panel"],
        "GeneralAlternateBackgroundColor": c["chrome"],
        "DialogBackgroundColor": c["panel"],
        "TextEditFieldBackgroundColor": c["paper"],
        "MenuBackgroundColor": c["paper"],
        "GroupboxBackgroundColor": c["panel"],
        "AccentColor": c["signal"],
        "AccentBackgroundColor": c["signal-tint"],
        "AccentHoverColor": c["signal-tint"],
        "TextSelectBackgroundColor": c["signal"],
        "TextUrlColor": c["signal-ink"],
        "InputFieldBorderRadius": "0px",
        "SketcherFullyConstrainedColor": c["ok"],
        "SketcherUnderConstrainedColor": c["sk-under"],
        "SketcherConflictingConstraintsColor": c["error"],
        "SketcherSolverFailedColor": c["error"],
    }


def write_theme(freecad_root, home, mode="light"):
    """Copy FreeCAD's own light/dark theme parameters and apply CADSwarm's tokens. Returns the file path."""
    base_name = "FreeCAD Dark.yaml" if mode == "dark" else "FreeCAD Light.yaml"
    base = os.path.join(freecad_root, "data", "Gui", "Stylesheets", "parameters", base_name)
    lines = []
    if os.path.exists(base):
        with open(base, encoding="utf-8") as f:
            lines = f.read().splitlines()
    over = theme_overrides(mode)
    out, seen = [], set()
    for line in lines:
        key = line.split(":", 1)[0].strip()
        if key in over:
            out.append(f'{key}: "{over[key]}"')
            seen.add(key)
        else:
            out.append(line)
    out += [f'{k}: "{v}"' for k, v in over.items() if k not in seen]
    os.makedirs(os.path.join(home, "Themes"), exist_ok=True)
    path = os.path.join(home, "Themes", f"CADSwarm {mode.title()}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return path


def brand_params(theme_file, mode="light"):
    """(group, type, key, value) parameters for a CADSwarm-branded FreeCAD profile."""
    c = colours(mode)
    view = "BaseApp/Preferences/View"
    mw = "BaseApp/Preferences/MainWindow"
    return [
        ("BaseApp/Preferences/Mod/Start", "Bool", "ShowOnStartup", False),
        ("BaseApp/Preferences/Mod/Start", "Bool", "FirstStart2024", False),
        ("BaseApp/Preferences/Mod/Start", "Bool", "CloseStart", True),
        ("BaseApp/Preferences/General", "Bool", "ShowVersionInTitle", False),
        ("BaseApp/Preferences/General", "String", "AutoloadModule", "PartDesignWorkbench"),
        ("BaseApp/Preferences/General", "Bool", "FirstRun", False),
        ("BaseApp/Preferences/Units", "Int", "UserSchema", 0),
        ("BaseApp/Preferences/Units", "Int", "Decimals", 2),
        ("BaseApp/Preferences/Document", "Bool", "CreateBackupFiles", True),
        (mw, "Bool", "StatusBar", True),
        (mw, "String", "Theme", f"CADSwarm {mode.title()}"),
        (mw, "String", "ThemeStyleParametersFile", theme_file),
        (mw, "String", "StyleSheet", "FreeCAD.qss"),
        (mw, "String", "QtStyle", "FreeCAD"),
        (mw, "String", "OverlayActiveStyleSheet", "Freecad Overlay.qss"),
        ("BaseApp/Preferences/Themes", "Unsigned", "ThemeAccentColor1", rgba_uint(c["signal"])),
        ("BaseApp/Preferences/Themes", "Unsigned", "ThemeAccentColor2", rgba_uint(c["signal-ink"])),
        ("BaseApp/Preferences/Themes", "Unsigned", "ThemeAccentColor3", rgba_uint(c["signal-tint"])),
        (view, "Bool", "Gradient", True),
        (view, "Bool", "RadialGradient", False),
        (view, "Unsigned", "BackgroundColor2", rgba_uint(c["vp-top"])),
        (view, "Unsigned", "BackgroundColor3", rgba_uint(c["vp-bottom"])),
        (view, "Unsigned", "HighlightColor", rgba_uint(c["preselect"])),
        (view, "Unsigned", "SelectionColor", rgba_uint(c["select"])),
        (view, "Unsigned", "DefaultShapeColor", rgba_uint(c["vp-face"])),
        (view, "Unsigned", "DefaultShapeLineColor", rgba_uint(c["vp-edge"])),
        (view, "Bool", "ShowFPS", False),
        (view, "Bool", "UseVBO", True),
        (view, "Bool", "ShowNaviCube", True),
    ]


def link_addon(home, repo=REPO):
    """Make the CADSwarm add-on (this repo) a Mod in the engine profile. Junction on Windows, symlink elsewhere."""
    mod = os.path.join(home, "Mod")
    os.makedirs(mod, exist_ok=True)
    dest = os.path.join(mod, "CadSwarm")
    if os.path.exists(os.path.join(dest, "InitGui.py")):
        if os.path.realpath(dest) == os.path.realpath(repo):
            return dest
    if os.path.lexists(dest):
        if os.path.isdir(dest) and not os.path.islink(dest) and not _is_junction(dest):
            return dest  # a real copied add-on (installed build): keep it
        _unlink(dest)
    if sys.platform.startswith("win"):
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", dest, repo], capture_output=True, check=False)
    else:
        os.symlink(repo, dest)
    return dest


def _is_junction(path):
    try:
        return bool(os.readlink(path))
    except (OSError, ValueError):
        return False


def _unlink(path):
    try:
        os.rmdir(path) if sys.platform.startswith("win") else os.unlink(path)
    except OSError:
        os.unlink(path)
