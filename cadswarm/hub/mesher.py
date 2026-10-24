"""Gmsh, installed on demand when FreeCAD doesn't come with it (some Linux packages and snaps, older Macs).

Standard FreeCAD downloads ship `gmsh`, and the FEA runner uses that first. If it can't find one, the hub
installs the official Gmsh build from PyPI (one pinned version, checked against a hard-coded SHA-256) into
CadSwarm's own folder, `<app dir>/tools/gmsh-<version>/`. Nothing goes into FreeCAD or the system. The wheel
has no stand-alone program, only the Gmsh library and its Python API, so a two-line launcher runs it with the
hub's own Python as `python gmsh_launcher.py - shape2mesh.geo`, which takes the same arguments as `gmsh`.

Set CADSWARM_NO_AUTO_INSTALL=1 to never download anything (the analysis then fails with instructions).
Stdlib only.
"""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile

from ..paths import app_dir

GMSH_VERSION = "4.15.2"
_PYPI = "https://files.pythonhosted.org/packages/"
# (system, machine) -> (wheel URL, sha256, size). From https://pypi.org/pypi/gmsh/4.15.2/json
WHEELS = {
    ("Windows", "x86_64"): (_PYPI + "78/e9/63c763c0d75544680800651d5ea8c7c3203ae4b5e054706f19a54021a7fb/"
                            "gmsh-4.15.2-py2.py3-none-win_amd64.whl",
                            "7b36083bb410fa27c5d0e052929d1a9844a5b09169d66017b72b41aabd49d711", 42235999),
    ("Linux", "x86_64"): (_PYPI + "ab/d4/336becb1488ac4fbab205043987128839c8c62d54e2036b7e887bd009040/"
                          "gmsh-4.15.2-py2.py3-none-manylinux_2_24_x86_64.whl",
                          "4076a948ce22625330d1413d4982e22b5c69fc2f0f7951f5df64c778cf54108c", 40006930),
    ("Darwin", "x86_64"): (_PYPI + "19/a6/f3f74f53234c812ffa0191ce2aceb0811ea82bdf12867a73bebb03f28f7a/"
                           "gmsh-4.15.2-py2.py3-none-macosx_10_15_x86_64.whl",
                           "9f5a97b6633e40be331cb777ab571cef96e6ff5979bb7b20aad2438724ca417b", 40171095),
    ("Darwin", "arm64"): (_PYPI + "3d/b4/0d25938ad62ea4db6bcbdecbc77c72d05d8a6a679abe4addcd86167ffd77/"
                          "gmsh-4.15.2-py2.py3-none-macosx_12_0_arm64.whl",
                          "f6649b3e59f49272e7ee8ab282ecb4d1a6e0d627e86cf3e3b1a83fd07417e4f8", 36427761),
}
LAUNCHER = """import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gmsh
gmsh.initialize(sys.argv, run=True)
gmsh.finalize()
"""
MANUAL = ("Install Gmsh yourself (https://gmsh.info, or your package manager: e.g. `sudo apt install gmsh`) and "
          "run the analysis again, or set its path in FreeCAD's FEM > Gmsh preferences.")
_lock = threading.Lock()


class MesherError(Exception):
    pass


def install_dir():
    return os.path.join(app_dir(), "tools", f"gmsh-{GMSH_VERSION}")


def _machine():
    m = platform.machine().lower()
    return {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(m, m)


def command(python=None):
    """Argv prefix that runs the installed Gmsh like the `gmsh` program, or None if it isn't installed."""
    d = install_dir()
    if not os.path.exists(os.path.join(d, "installed.json")):
        return None
    return [python or sys.executable, os.path.join(d, "gmsh_launcher.py")]


def _verify(cmd):
    """Run `gmsh -version`; a missing system library (e.g. OpenGL on a bare server) shows up here."""
    try:
        p = subprocess.run(cmd + ["-version"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise MesherError(f"The downloaded Gmsh couldn't start ({e}). {MANUAL}")
    text = (p.stdout + p.stderr).decode("utf-8", "replace")
    if p.returncode != 0 or GMSH_VERSION not in text:
        raise MesherError("The downloaded Gmsh couldn't start"
                          + (": " + text.strip().splitlines()[-1][:300] if text.strip() else "")
                          + f". {MANUAL}")


def install(python=None, progress=None, opener=urllib.request.urlopen):
    """Download, check and unpack Gmsh once; returns the argv prefix that runs it. Safe to call concurrently."""
    with _lock:
        existing = command(python)
        if existing:
            return existing
        if os.environ.get("CADSWARM_NO_AUTO_INSTALL"):
            raise MesherError("No Gmsh mesher was found and automatic installs are turned off "
                              f"(CADSWARM_NO_AUTO_INSTALL). {MANUAL}")
        key = (platform.system(), _machine())
        if key not in WHEELS:
            raise MesherError(f"No Gmsh mesher was found, and there's no ready-made Gmsh for {key[0]} on {key[1]} "
                              f"to install automatically. {MANUAL}")
        url, sha, size = WHEELS[key]
        dest = install_dir()
        parent = os.path.dirname(dest)
        os.makedirs(parent, exist_ok=True)
        if shutil.disk_usage(parent).free < 4 * size + 500 * 1024 * 1024:
            raise MesherError(f"Not enough free disk space to install the Gmsh mesher (needs about "
                              f"{4 * size // 2 ** 20 + 500} MB in {parent}).")
        if progress:
            progress(f"Installing the Gmsh mesher ({size // 2 ** 20} MB download, one time only)…")
        staging = tempfile.mkdtemp(prefix=f".gmsh-{GMSH_VERSION}-", dir=parent)
        try:
            wheel = os.path.join(staging, "gmsh.whl")
            digest = hashlib.sha256()
            try:
                with opener(url, timeout=60) as r, open(wheel, "wb") as f:
                    got = 0
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        got += len(chunk)
                        if got > size:
                            raise MesherError("The Gmsh download was larger than expected; it was discarded.")
                        digest.update(chunk)
                        f.write(chunk)
            except OSError as e:
                raise MesherError(f"Couldn't download the Gmsh mesher from PyPI ({e}). Check the internet "
                                  f"connection of the computer running the team server, or: {MANUAL}")
            if digest.hexdigest() != sha:
                raise MesherError("The Gmsh download didn't match its expected checksum, so it wasn't installed. "
                                  "Try again later, or: " + MANUAL)
            out = os.path.join(staging, "gmsh")
            os.makedirs(os.path.join(out, "lib"))
            with zipfile.ZipFile(wheel) as z:
                for member in z.namelist():
                    name = member.replace("\\", "/")
                    if name == "gmsh.py":
                        target = os.path.join(out, "gmsh.py")
                    elif "/data/lib/" in name and not name.endswith("/") and name.count("/") == 3 \
                            and not name.endswith(".jl"):
                        target = os.path.join(out, "lib", os.path.basename(name))  # flat: no path from the zip
                    else:
                        continue
                    with z.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            if not os.path.exists(os.path.join(out, "gmsh.py")) or not os.listdir(os.path.join(out, "lib")):
                raise MesherError("The Gmsh download didn't contain the expected files. " + MANUAL)
            with open(os.path.join(out, "gmsh_launcher.py"), "w", encoding="utf-8") as f:
                f.write(LAUNCHER)
            _verify([python or sys.executable, os.path.join(out, "gmsh_launcher.py")])
            with open(os.path.join(out, "installed.json"), "w", encoding="utf-8") as f:
                json.dump({"version": GMSH_VERSION, "source": url, "sha256": sha}, f)
            if os.path.exists(dest):
                shutil.rmtree(dest)  # a broken earlier attempt (no installed.json)
            os.replace(out, dest)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        if progress:
            progress("Gmsh mesher installed.")
        return command(python)
