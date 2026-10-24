"""install/install.sh on Linux, against a fake unpacked AppImage in the folder the installer manages."""
import os
import shutil
import subprocess
import sys

import pytest

from cadswarm.paths import ROOT

pytestmark = pytest.mark.skipif(sys.platform != "linux" or not shutil.which("bash"),
                                reason="the Linux branch of install.sh")


def fake_appimage(share):
    """<share>/CadSwarm/FreeCAD-1.1.4/squashfs-root, whose AppRun answers `freecadcmd -c CODE`."""
    root = share / "CadSwarm" / "FreeCAD-1.1.4" / "squashfs-root"
    lib = root / "usr" / "lib"
    lib.mkdir(parents=True)
    user = share / "FreeCAD"
    (lib / "FreeCAD.py").write_text(f"def getUserAppDataDir():\n    return {str(user) + '/'!r}\n")
    (root / "AppRun").write_text(f'#!/bin/sh\n[ "$1" = freecadcmd ] && shift\n'
                                 f'PYTHONPATH="{lib}" exec "{sys.executable}" "$@"\n')
    os.chmod(root / "AppRun", 0o755)
    return root, user


def run(home, share, script=None):
    env = {"HOME": str(home), "XDG_DATA_HOME": str(share), "PATH": "/usr/bin:/bin"}
    script = script or os.path.join(ROOT, "install", "install.sh")
    return subprocess.run(["bash", str(script), "--yes", "--no-launch"], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120)


def test_it_copies_the_add_on_and_writes_a_launcher(tmp_path):
    share = tmp_path / "share"
    root, user = fake_appimage(share)
    r = run(tmp_path, share)
    assert r.returncode == 0, r.stdout + r.stderr
    dest = user / "Mod" / "CadSwarm"
    assert (dest / "cadswarm" / "gui" / "open_cadswarm.py").is_file()
    assert not (dest / ".git").exists()
    desktop = (share / "applications" / "cadswarm.desktop").read_text()
    assert str(root / "AppRun") in desktop and "open_cadswarm.py" in desktop
    # Running it again from the installed copy must not delete the files it runs from.
    again = run(tmp_path, share, dest / "install" / "install.sh")
    assert again.returncode == 0 and "already installed" in again.stdout
    assert (dest / "cadswarm").is_dir()


def test_no_freecad_is_a_clear_failure_and_downloads_nothing(tmp_path):
    r = run(tmp_path, tmp_path / "share")
    assert r.returncode == 1 and "FreeCAD isn't installed" in r.stdout
    assert not (tmp_path / "share" / "CadSwarm").exists()
