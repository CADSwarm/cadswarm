"""The macOS/Linux quick installer (install/install.sh), run against a fake FreeCAD in a sandboxed home."""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")


def fake_freecad(tmp_path):
    """A 'FreeCAD' whose freecadcmd answers the installer's question about its add-on folder."""
    bin_dir = tmp_path / "My Apps" / "FreeCAD" / "bin"
    bin_dir.mkdir(parents=True)
    user_dir = tmp_path / "home" / "FreeCAD user"
    (bin_dir / "freecadcmd").write_text(f"#!/bin/sh\necho noise\necho 'CADSWARM_DIR={user_dir}/'\n")
    (bin_dir / "freecad").write_text("#!/bin/sh\nexit 0\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    return bin_dir / "freecad", user_dir


def run(tmp_path, *args, **env):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    full_env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "XDG_DATA_HOME": str(home / ".local" / "share"),
                "LANG": "C.UTF-8", **env}
    return subprocess.run(["bash", os.path.join(ROOT, "install", "install.sh"), "--no-launch", *args],
                          capture_output=True, text=True, env=full_env, stdin=subprocess.DEVNULL, timeout=120)


def test_installs_into_the_folder_freecad_reports_and_adds_a_shortcut(tmp_path):
    exe, user_dir = fake_freecad(tmp_path)
    r = run(tmp_path, "--yes", CADSWARM_FREECAD=str(exe))
    assert r.returncode == 0, r.stdout + r.stderr
    dest = user_dir / "Mod" / "CadSwarm"
    assert (dest / "InitGui.py").exists() and (dest / "cadswarm" / "fea.py").exists()
    for skipped in (".git", ".venv", "__pycache__"):
        assert not list(dest.rglob(skipped))
    assert "no Python of its own" in r.stdout  # a distro FreeCAD can only join
    if sys.platform == "darwin":  # no Python of its own: the launcher opens FreeCAD (made with osacompile)
        assert (tmp_path / "home" / "Applications" / "CADSwarm.app").is_dir(), r.stdout
    else:
        desktop = (tmp_path / "home" / ".local" / "share" / "applications" / "cadswarm.desktop").read_text()
        assert f'Exec="{exe}" "{dest}/cadswarm/gui/open_cadswarm.py"' in desktop  # paths with spaces stay whole
        assert "Icon=" in desktop

    r = run(tmp_path, "--yes", "--no-shortcut", CADSWARM_FREECAD=str(exe))  # again: a clean update
    assert r.returncode == 0 and (dest / "InitGui.py").exists() and not list(user_dir.parent.glob("**/CadSwarm.new"))


def test_without_freecad_yes_never_downloads(tmp_path):
    r = run(tmp_path, "--yes")
    assert r.returncode == 1 and "isn't installed" in r.stdout
    assert "Downloading" not in r.stdout and not (tmp_path / "home" / ".local" / "share" / "CadSwarm").exists()


def test_rejects_unknown_options(tmp_path):
    assert run(tmp_path, "--bogus").returncode == 2


def fake_mac_bundle(tmp_path):
    """A 'FreeCAD.app' with its own python, as the official macOS download has."""
    app = tmp_path / "Apps" / "FreeCAD 1.1.app"
    bin_dir = app / "Contents" / "Resources" / "bin"
    bin_dir.mkdir(parents=True)
    user_dir = tmp_path / "home" / "FreeCAD user"
    (bin_dir / "freecadcmd").write_text(f"#!/bin/sh\necho 'CADSWARM_DIR={user_dir}/'\n")
    (bin_dir / "python").write_text("#!/bin/sh\nexit 0\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    return app, user_dir


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS app bundle")
def test_macos_makes_a_cadswarm_app_bundle_running_freecads_python(tmp_path):
    app, user_dir = fake_mac_bundle(tmp_path)
    r = run(tmp_path, "--yes", CADSWARM_FREECAD=str(app))
    assert r.returncode == 0, r.stdout + r.stderr
    bundle = tmp_path / "home" / "Applications" / "CADSwarm.app" / "Contents"
    plist = (bundle / "Info.plist").read_text()
    assert "<string>CADSwarm</string>" in plist and "org.cadswarm.desktop" in plist
    launcher = (bundle / "MacOS" / "CADSwarm").read_text()
    main = user_dir / "Mod" / "CadSwarm" / "cadswarm" / "app" / "__main__.py"
    assert f'"{app}/Contents/Resources/bin/python" "{main}" "$@"' in launcher
    assert os.access(bundle / "MacOS" / "CADSwarm", os.X_OK)
