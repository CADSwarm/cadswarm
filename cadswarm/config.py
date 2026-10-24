"""Locating FreeCAD and the hub's defaults."""
import glob
import os

DEFAULT_PORT = 8765
HEARTBEAT_SECONDS = 10
STALE_AFTER_SECONDS = 30       # no heartbeat for this long -> stale, work awaits takeover
ORPHAN_AFTER_SECONDS = 600     # stale this long -> locks freed, tasks reopened


def find_freecad_python():
    """FreeCAD's bundled python.exe — the worker must run in it to `import FreeCAD`."""
    explicit = os.environ.get("CADSWARM_FREECAD_PYTHON")
    if explicit:
        return explicit
    roots = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
             os.environ.get("ProgramFiles", r"C:\Program Files"),
             "/usr/lib/freecad", "/Applications"]
    patterns = []
    for root in roots:
        patterns += [os.path.join(root, "FreeCAD*", "bin", "python.exe"),
                     os.path.join(root, "FreeCAD*", "bin", "python3"),
                     os.path.join(root, "FreeCAD.app", "Contents", "Resources", "bin", "python")]
    found = sorted({p for pat in patterns for p in glob.glob(pat)}, reverse=True)
    if not found:
        raise RuntimeError("FreeCAD not found. Install FreeCAD 1.x or set CADSWARM_FREECAD_PYTHON "
                           "to its bundled python executable (…/FreeCAD/bin/python.exe).")
    return found[0]


def worker_script():
    return os.path.join(os.path.dirname(__file__), "worker", "freecad_worker.py")
