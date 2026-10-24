"""Locating FreeCAD and the hub's defaults."""
import glob
import os
import re
import shutil

HEARTBEAT_SECONDS = 15
STALE_AFTER_SECONDS = 30       # no heartbeat for this long -> stale, work awaits takeover
ORPHAN_AFTER_SECONDS = 600     # stale this long -> locks freed, tasks reopened


def _version_key(text):
    """'FreeCAD 1.10' -> (1, 10); no number -> (). Numeric, so 1.10 beats 1.9."""
    m = re.search(r"\d+(?:\.\d+)*", text or "")
    return tuple(int(n) for n in m.group().split(".")) if m else ()


def _python_in(bin_dir):
    for name in ("python.exe", "python3", "python"):
        cand = os.path.join(bin_dir, name)
        if os.path.isfile(cand):
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


def freecad_candidates():
    """Every FreeCAD python found in common locations, on PATH and in the registry, newest version first."""
    env = os.environ
    roots = [os.path.join(env.get("LOCALAPPDATA", ""), "Programs"),
             env.get("ProgramFiles", r"C:\Program Files"),
             env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             "/usr/lib/freecad", "/Applications"]
    found = {}  # python path -> version key
    for root in roots:
        for pat in (os.path.join(root, "FreeCAD*", "bin"),
                    os.path.join(root, "FreeCAD.app", "Contents", "Resources", "bin")):
            for bin_dir in glob.glob(pat):
                py = _python_in(bin_dir)
                if py:
                    found.setdefault(os.path.normpath(py),
                                     _version_key(os.path.basename(os.path.dirname(bin_dir))))
    for exe in ("freecadcmd", "FreeCADCmd"):
        hit = shutil.which(exe)
        if hit:
            bin_dir = os.path.dirname(os.path.realpath(hit))
            py = _python_in(bin_dir)
            if py:
                found.setdefault(os.path.normpath(py),
                                 _version_key(os.path.basename(os.path.dirname(bin_dir))))
    for where, ver in _registry_installs():  # DisplayVersion is more reliable than the folder name
        py = _python_in(os.path.join(where, "bin"))
        if py:
            found[os.path.normpath(py)] = _version_key(ver) or _version_key(os.path.basename(where))
    return [p for p, _ in sorted(found.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)]


def find_freecad_python():
    """FreeCAD's bundled python.exe — the worker must run in it to `import FreeCAD`."""
    explicit = os.environ.get("CADSWARM_FREECAD_PYTHON")
    if explicit:
        return explicit
    found = freecad_candidates()
    if not found:
        raise RuntimeError("FreeCAD not found. Install FreeCAD 1.x or set CADSWARM_FREECAD_PYTHON "
                           "to its bundled python executable (…/FreeCAD/bin/python.exe).")
    return found[0]


def worker_script():
    return os.path.join(os.path.dirname(__file__), "worker", "freecad_worker.py")


def helper_script():
    """The host runs this file with any Python 3.11+ (stdlib only) to answer file and storage ops."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "helper.py")
