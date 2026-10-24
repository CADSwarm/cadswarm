"""Where things live, and how helper processes find their dependencies.

Hobbyists never install Python: everything (the team server, each AI helper's
MCP server, the prompt hook) runs in FreeCAD's own bundled Python. The few
packages the server side needs are pip-installed once into a private folder
(`deps_dir()`), never into FreeCAD itself, and only helper processes put that
folder on their path, so FreeCAD's own packages are never shadowed.
Stdlib only: imported by FreeCAD's GUI, the launcher and the CLI alike.
"""
import glob
import os
import platform
import re
import shutil
import site
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "cadswarm_launch.py")

# Keep in step with pyproject.toml's [project].dependencies.
DEPENDENCIES = ["mcp>=2.3"]


def app_dir():
    """Private per-user app folder: %APPDATA% on Windows, ~/Library/Application Support on macOS, XDG data dir on Linux."""
    if os.environ.get("APPDATA"):
        base = os.environ["APPDATA"]
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = xdg if xdg and os.path.isabs(xdg) else os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "CadSwarm")


def deps_dir():
    """Per Python minor version: compiled wheels (pydantic-core, pywin32) are ABI-specific."""
    explicit = os.environ.get("CADSWARM_DEPS")
    if explicit:
        return explicit
    return os.path.join(app_dir(), f"pylib-py{sys.version_info.major}{sys.version_info.minor}")


def activate_deps():
    """Put the private deps folder FIRST on sys.path, processing its .pth files (pywin32 needs them)."""
    d = deps_dir()
    if not os.path.isdir(d):
        return False
    before = list(sys.path)
    site.addsitedir(d)
    added = [p for p in sys.path if p not in before]
    sys.path[:] = added + [p for p in before if p not in added]
    return True


TEAM_DIR = ".cadswarm-team"


def project_layout(root):
    """Where everything lives inside a project folder the user chose.

    model.FCStd      the live shared model (written only by the team server)
    files/           AI helpers' saved files; subfolders grow as the project does
    exports/         STEP/STL exports (never overwritten)
    snapshots/       saved versions of the model
    .cadswarm-team/  server internals, file history and trash

    A folder made by an older CadSwarm (hub.sqlite at its top level) keeps its internals there.
    """
    root = os.path.abspath(root)
    legacy = os.path.exists(os.path.join(root, "hub.sqlite"))
    internal = root if legacy else os.path.join(root, TEAM_DIR)
    return {
        "root": root, "model": os.path.join(root, "model.FCStd"), "files": os.path.join(root, "files"),
        "exports": os.path.join(root, "exports"), "snapshots": os.path.join(root, "snapshots"),
        "internal": internal, "db": os.path.join(internal, "hub.sqlite"),
        "token": os.path.join(internal, "workspace_token"), "log": os.path.join(internal, "hub.log"),
        "history": os.path.join(internal, "history"), "trash": os.path.join(internal, "trash"),
    }


def is_project_folder(root):
    return os.path.exists(project_layout(root)["db"])


def resolve_project_folder(chosen, project):
    """Where the project will really live, never mixing with (or overwriting) unrelated files.

    Returns (folder, how): "existing" = continue a CadSwarm project already there,
    "new" = an empty or new folder, "inside" = a fresh subfolder because `chosen` had other things in it.
    """
    chosen = os.path.normpath(chosen)
    if is_project_folder(chosen):
        return chosen, "existing"
    if not os.path.exists(chosen) or (os.path.isdir(chosen) and not os.listdir(chosen)):
        return chosen, "new"
    base = os.path.join(chosen, re.sub(r'[<>:"/\\|?*]+', " ", project).strip() or "CadSwarm project")
    if is_project_folder(base):
        return base, "existing"
    candidate, n = base, 2
    while os.path.exists(candidate) and os.listdir(candidate):
        candidate, n = f"{base} ({n})", n + 1
    return candidate, "inside"


def projects_dir():
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "CadSwarm")


def droid_exe():
    """The Droid program the SDK can start: a real executable, not npm's shell shim.

    droid_sdk spawns Droid with asyncio.create_subprocess_exec, which needs an executable image.
    On Windows `droid` on PATH is a script (droid.cmd / droid.ps1), so that spawn fails with
    "Droid executable was not found" even though `droid` works in a terminal. npm unpacks the
    native binary into the platform package beside the shim; prefer that, or a native
    install on PATH. On macOS and Linux the shim is a script with a shebang, which runs fine.

    CADSWARM_DROID_EXE overrides everything (a copy of droid that is not on PATH).
    """
    override = os.environ.get("CADSWARM_DROID_EXE")
    if override:
        return override
    if os.name != "nt":
        return shutil.which("droid") or "droid"
    native = shutil.which("droid.exe")
    if native:
        return native
    arm = platform.machine().lower() in ("arm64", "aarch64")
    names = ["cli-win32-arm64"] if arm else ["cli-win32-x64", "cli-win32-x64-baseline"]
    dirs = []
    for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
        if base:
            dirs.append(os.path.join(base, "npm", "node_modules", "droid"))
    shim = shutil.which("droid") or shutil.which("droid.cmd")
    if shim:  # ...\npm\droid.cmd -> ...\npm\node_modules\droid
        dirs.append(os.path.join(os.path.dirname(os.path.abspath(shim)), "node_modules", "droid"))
    for pkg in dirs:
        for name in names:
            exe = os.path.join(pkg, "node_modules", "@factory", name, "bin", "droid.exe")
            if os.path.isfile(exe):
                return exe
        found = glob.glob(os.path.join(pkg, "node_modules", "@factory", "cli-win32-*", "bin", "droid.exe"))
        if found:
            return sorted(found)[0]
    return shutil.which("droid") or "droid"


def freecad_python():
    """FreeCAD's bundled python, from inside FreeCAD (where sys.executable is freecad.exe) or outside."""
    exe = os.path.basename(sys.executable).lower()
    if exe.startswith("python"):
        return sys.executable
    try:
        import FreeCAD
        home = FreeCAD.getHomePath()
        for cand in (os.path.join(home, "bin", "python.exe"), os.path.join(home, "bin", "python3"),
                     os.path.join(home, "bin", "python")):
            if os.path.isfile(cand) and (os.name == "nt" or os.access(cand, os.X_OK)):
                return os.path.normpath(cand)
        if os.name != "nt":
            # A distro FreeCAD embeds the system Python: the same version on PATH can import it.
            same = shutil.which(f"python{sys.version_info.major}.{sys.version_info.minor}")
            if same:
                return same
    except ImportError:
        pass
    from .config import find_freecad_python
    return find_freecad_python()
