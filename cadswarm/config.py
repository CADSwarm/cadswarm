"""Locating FreeCAD and the hub's defaults."""
import glob
import os
import re
import shutil
import sys

from .paths import app_dir, droid_exe

HEARTBEAT_SECONDS = 15
STALE_AFTER_SECONDS = 30       # no heartbeat for this long -> stale, work awaits takeover
ORPHAN_AFTER_SECONDS = 600     # stale this long -> locks freed, tasks reopened
LINUX_FREECAD_LIBS = ("/usr/lib/freecad/lib", "/usr/lib/freecad-python3/lib",
                      "/usr/lib64/freecad/lib", "/usr/local/lib/freecad/lib")


def _version_key(text):
    """'FreeCAD 1.10' -> (1, 10); no number -> (). Numeric, so 1.10 beats 1.9."""
    m = re.search(r"\d+(?:\.\d+)*", text or "")
    return tuple(int(n) for n in m.group().split(".")) if m else ()


def _python_in(bin_dir):
    for name in ("python.exe", "python3", "python"):
        cand = os.path.join(bin_dir, name)
        if os.path.isfile(cand) and (os.name == "nt" or os.access(cand, os.X_OK)):
            return cand
    return None


def _registry_installs():
    """[(install_dir, display_version)] from the Windows uninstall keys; [] elsewhere."""
    try:
        import winreg
    except ImportError:
        return []
    out = []
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive, view in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
                       (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        try:
            root = winreg.OpenKey(hive, base, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    name = winreg.EnumKey(root, i)
                    if "freecad" not in name.lower():
                        continue
                    with winreg.OpenKey(root, name) as key:
                        where = winreg.QueryValueEx(key, "InstallLocation")[0]
                        try:
                            ver = winreg.QueryValueEx(key, "DisplayVersion")[0]
                        except OSError:
                            ver = name
                    if where:
                        out.append((where, str(ver)))
                except OSError:
                    continue
    return out


def _platform():
    return sys.platform  # a function so tests can check every platform's layout on any OS


def _install_patterns():
    """[(glob pattern for a FreeCAD bin dir, levels up to the version folder)] for this platform."""
    env = os.environ
    if _platform() == "win32":
        roots = []
        if env.get("LOCALAPPDATA"):  # never glob onto a bare "" root
            roots.append(os.path.join(env["LOCALAPPDATA"], "Programs"))
        roots.append(env.get("ProgramFiles", r"C:\Program Files"))
        roots.append(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        return [(os.path.join(r, "FreeCAD*", "bin"), 1) for r in roots]
    if _platform() == "darwin":
        roots = ["/Applications", os.path.expanduser("~/Applications")]
        return [(os.path.join(r, "FreeCAD*.app", "Contents", "Resources", "bin"), 3)
                for r in roots]
    roots = ["/opt", "/usr/local", os.path.expanduser("~/Applications"),
             os.path.expanduser("~/.local/share")]
    # Tarballs ship <FreeCAD>/bin/python; an extracted AppImage (--appimage-extract) has usr/bin/python.
    return [(os.path.join(r, pat, *sub), len(sub)) for r in roots for pat in ("FreeCAD*", "freecad*")
            for sub in (("bin",), ("usr", "bin"))]


def freecad_candidates():
    """Every FreeCAD python found in common locations, on PATH and in the registry, newest version first."""
    found = {}  # python path -> version key
    for pat, up in _install_patterns():
        for bin_dir in glob.glob(pat):
            py = _python_in(bin_dir)
            if py:
                folder = bin_dir
                for _ in range(up):  # the FreeCAD* folder holding the version text
                    folder = os.path.dirname(folder)
                found.setdefault(os.path.normpath(py), _version_key(os.path.basename(folder)))
    # The official Linux AppImage, unpacked by CADSwarm's installer into <app dir>/FreeCAD-<version>/.
    for bin_dir in glob.glob(os.path.join(app_dir(), "FreeCAD-*", "squashfs-root", "usr", "bin")):
        py = _python_in(bin_dir)
        if py:
            found.setdefault(os.path.normpath(py), _version_key(bin_dir.split("FreeCAD-")[-1]))
    for exe in ("freecadcmd", "FreeCADCmd"):
        hit = shutil.which(exe)
        if hit:
            bin_dir = os.path.dirname(os.path.realpath(hit))
            py = _python_in(bin_dir)
            if py:
                found.setdefault(os.path.normpath(py),
                                 _version_key(os.path.basename(os.path.dirname(bin_dir))))
    if _platform() not in ("win32", "darwin"):
        # distro packages run on the system python3; bundled installs still win
        if any(os.path.isfile(os.path.join(lib, "FreeCAD.so")) for lib in LINUX_FREECAD_LIBS):
            py = shutil.which("python3")
            if py:
                found.setdefault(os.path.normpath(py), ())
    for where, ver in _registry_installs():  # DisplayVersion is more reliable than the folder name
        py = _python_in(os.path.join(where, "bin"))
        if py:
            found[os.path.normpath(py)] = _version_key(ver) or _version_key(os.path.basename(where))
    return [p for p, _ in sorted(found.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)]


def freecad_import_dirs(python):
    """Folders that make `import FreeCAD` work for `python`: next to it (Windows), its sibling lib
    folder (macOS .app, extracted AppImage) and the distro lib folders (Linux). Existing ones only."""
    bin_dir = os.path.dirname(os.path.abspath(python))
    dirs = [bin_dir, os.path.join(os.path.dirname(bin_dir), "lib"), *LINUX_FREECAD_LIBS]
    return [d for d in dirs if os.path.isdir(d)]


def find_freecad_python():
    """FreeCAD's Python: the worker must run in it to `import FreeCAD`."""
    explicit = os.environ.get("CADSWARM_FREECAD_PYTHON")
    if explicit:
        return explicit
    found = freecad_candidates()
    if not found:
        raise RuntimeError("FreeCAD not found. Install FreeCAD 1.x or set CADSWARM_FREECAD_PYTHON "
                           "to its Python: …\\FreeCAD\\bin\\python.exe (Windows), "
                           "/Applications/FreeCAD.app/Contents/Resources/bin/python (macOS), "
                           "or python3 for a distro package (Linux).")
    return found[0]


def worker_script():
    return os.path.join(os.path.dirname(__file__), "worker", "freecad_worker.py")


def helper_script():
    """The host runs this file with any Python 3.11+ (stdlib only) to answer file and storage ops."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "helper.py")


def reviewer_can_start():
    """Whether the host may set up the Droid design reviewer by itself (no `init-reviewer`): Droid is
    installed and a FACTORY_API_KEY is available, from the environment or cadswarm.env. Without a key the
    reviewer can't sign in, and one that exits without a verdict fails the brief, so it stays opt-in.
    CADSWARM_AUTO_REVIEWER=0 turns it off."""
    if os.environ.get("CADSWARM_AUTO_REVIEWER", "").strip() in ("0", "false", "no"):
        return False
    from .creao import load_env_file
    load_env_file()
    exe = droid_exe()
    return bool(os.environ.get("FACTORY_API_KEY", "").strip()) and bool(os.path.isfile(exe) or shutil.which(exe))
