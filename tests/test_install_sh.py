"""install/install.sh on Linux, against a fake extracted FreeCAD (bin/python + lib/FreeCAD.py)."""
import os
import shutil
import subprocess
import sys

import pytest

from cadswarm.paths import ROOT

pytestmark = pytest.mark.skipif(sys.platform != "linux" or not shutil.which("sh"),
                                reason="the Linux branch of install.sh")


def fake_freecad(home):
    fc = home / "Applications" / "FreeCAD-1.1" / "bin"
    fc.mkdir(parents=True)
    # A symlink, not a wrapper script: like FreeCAD's own Python, sys.executable is then this path.
    os.symlink(os.path.realpath(sys.executable), fc / "python")
    (fc / "freecad").write_text("#!/bin/sh\nexit 0\n")
    os.chmod(fc / "freecad", 0o755)
    lib = fc.parent / "lib"
    lib.mkdir()
    user = home / ".local" / "share" / "FreeCAD"
    (lib / "FreeCAD.py").write_text(f"def getUserAppDataDir():\n    return {str(user) + '/'!r}\n")
    return user


def run(home, *args):
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "CADSWARM_YES": "1"}
    return subprocess.run(["sh", os.path.join(ROOT, "install", "install.sh"), *args], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)


def test_it_copies_the_add_on_and_writes_a_launcher(tmp_path):
    user = fake_freecad(tmp_path)
    r = run(tmp_path, "--no-launch")
    assert r.returncode == 0, r.stdout + r.stderr
    dest = user / "Mod" / "CadSwarm"
    assert (dest / "cadswarm" / "gui" / "open_cadswarm.py").is_file()
    assert not (dest / ".git").exists()
    assert os.access(dest / "install" / "install.sh", os.X_OK)
    desktop = (tmp_path / ".local" / "share" / "applications" / "cadswarm.desktop").read_text()
    assert "FreeCAD-1.1/bin/freecad" in desktop and "open_cadswarm.py" in desktop
    # Running it again from the installed copy must not delete the files it runs from.
    again = subprocess.run(["sh", str(dest / "install" / "install.sh"), "--no-launch"],
                           env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "CADSWARM_YES": "1"},
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    assert again.returncode == 0 and "already installed" in again.stdout
    assert (dest / "cadswarm").is_dir()


def test_no_freecad_is_a_clear_failure(tmp_path):
    r = run(tmp_path, "--no-launch")
    assert r.returncode == 1 and "FreeCAD isn't installed" in r.stdout
