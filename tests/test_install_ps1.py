"""install/install.ps1: the Windows desktop-app install, run into a sandbox (temp folders, a throwaway HKCU key)."""
import os
import shutil
import subprocess
import sys
import uuid

import pytest

from cadswarm.paths import ROOT

pytestmark = pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"),
                                reason="the Windows installer")
SCRIPT = os.path.join(ROOT, "install", "install.ps1")
REAL_FREECAD = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "FreeCAD 1.1")


def fake_freecad(tmp_path):
    """Just the files the installer looks for; nothing is run from it with -NoWorkbench -NoLaunch."""
    bin_dir = tmp_path / "FreeCAD 9.9" / "bin"
    bin_dir.mkdir(parents=True)
    for exe in ("freecad.exe", "python.exe", "pythonw.exe"):
        (bin_dir / exe).write_bytes(b"")
    return tmp_path / "FreeCAD 9.9"


@pytest.fixture
def classes():
    """A throwaway stand-in for HKCU\\Software\\Classes, removed afterwards."""
    import winreg
    sub = rf"Software\CADSwarmTest-{uuid.uuid4().hex[:8]}"
    yield sub + r"\Classes"
    try:
        subprocess.run(["reg", "delete", rf"HKCU\{sub}", "/f"], capture_output=True, check=False)
    finally:
        with pytest.raises(OSError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub)


def install(tmp_path, classes_key, *extra, freecad=None, env=None):
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT, "-Yes", "-NoLaunch",
            "-InstallDir", str(tmp_path / "Programs" / "CADSwarm"), "-StartMenuDir", str(tmp_path / "Start"),
            "-DesktopDir", str(tmp_path / "Desktop"), "-ClassesKey", rf"HKCU:\{classes_key}", *extra]
    if freecad:
        args += ["-FreeCAD", str(freecad)]
    full_env = {**os.environ, **(env or {})}
    full_env.pop("CADSWARM_FREECAD", None)
    return subprocess.run(args, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL, env=full_env)


def read_shortcut(path):
    cs = os.path.join(ROOT, "install", "shortcut.cs")
    ps = f"Add-Type -Path '{cs}'; [CadSwarmSetup.Shortcut]::Read('{path}') -join \"`n\""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    target, args, workdir, icon, description, app_id = out.stdout.rstrip("\r\n").split("\n")
    return {"target": target.strip(), "args": args.strip(), "workdir": workdir.strip(), "icon": icon.strip(),
            "app_id": app_id.strip()}


def reg_value(key, name=""):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
        return winreg.QueryValueEx(k, name)[0]


def test_installs_the_app_with_shortcuts_and_open_with(tmp_path, classes):
    fc = fake_freecad(tmp_path)
    r = install(tmp_path, classes, "-NoWorkbench", freecad=fc)
    assert r.returncode == 0, r.stdout + r.stderr
    app = tmp_path / "Programs" / "CADSwarm"
    assert (app / "cadswarm" / "app" / "__main__.py").is_file() and (app / "InitGui.py").is_file()
    assert "cadswarm_launch.py" in (app / "cadswarm.cmd").read_text()
    for skipped in (".git", ".venv", "__pycache__", "upstream"):
        assert not list(app.rglob(skipped)), skipped
    pythonw = str(fc / "bin" / "pythonw.exe")
    for folder in ("Start", "Desktop"):
        link = read_shortcut(tmp_path / folder / "CADSwarm.lnk")
        assert link["target"] == pythonw and link["args"] == "-m cadswarm.app"
        assert link["workdir"] == str(app) and link["app_id"] == "CADSwarm.Desktop"
    prog = classes + r"\CADSwarm.FCStd"
    command = reg_value(prog + r"\shell\open\command")
    assert command == f'"{pythonw}" "{app / "cadswarm" / "app" / "__main__.py"}" "%1"'
    assert reg_value(prog, "AppUserModelID") == "CADSwarm.Desktop"
    assert reg_value(classes + r"\.FCStd\OpenWithProgids", "CADSwarm.FCStd") == ""

    # Again: an update replaces the copy and leaves no staging folders behind.
    (app / "stale.txt").write_text("old")
    r = install(tmp_path, classes, "-NoWorkbench", "-NoDesktop", freecad=fc)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (app / "stale.txt").exists() and (app / "cadswarm").is_dir()
    assert sorted(p.name for p in (tmp_path / "Programs").iterdir()) == ["CADSwarm"]


def test_never_overwrites_a_folder_that_is_not_cadswarm(tmp_path, classes):
    target = tmp_path / "Programs" / "CADSwarm"
    target.mkdir(parents=True)
    (target / "precious.txt").write_text("keep me")
    r = install(tmp_path, classes, "-NoWorkbench", freecad=fake_freecad(tmp_path))
    assert r.returncode == 1 and "left alone" in r.stdout
    assert (target / "precious.txt").read_text() == "keep me"


def test_a_missing_freecad_is_a_clear_failure(tmp_path, classes):
    r = install(tmp_path, classes, freecad=tmp_path / "nowhere")
    assert r.returncode == 1 and "no FreeCAD" in r.stdout
    assert not (tmp_path / "Programs").exists()


@pytest.mark.skipif(not os.path.exists(os.path.join(REAL_FREECAD, "bin", "python.exe")), reason="needs FreeCAD 1.1")
def test_freecad_workbench_links_to_the_installed_app(tmp_path, classes):
    profile = tmp_path / "freecad profile"
    profile.mkdir()  # FreeCAD only honours FREECAD_USER_HOME when the folder exists
    old_copy = profile / "Mod" / "CadSwarm"
    old_copy.mkdir(parents=True)
    (old_copy / "InitGui.py").write_text("# the old installer's copy\n")
    # Never touch the real FreeCAD profile: make sure FreeCAD reports the sandbox first.
    ask = subprocess.run([os.path.join(REAL_FREECAD, "bin", "python.exe"), "-c",
                          "import sys,os; sys.path.append(os.path.dirname(sys.executable)); import FreeCAD; "
                          "print('DIR=' + FreeCAD.getUserAppDataDir())"],
                         capture_output=True, text=True, timeout=120, env={**os.environ, "FREECAD_USER_HOME": str(profile)})
    reported = [ln[4:] for ln in ask.stdout.splitlines() if ln.startswith("DIR=")]
    if not reported or os.path.normcase(os.path.realpath(reported[-1])) != os.path.normcase(os.path.realpath(profile)):
        pytest.skip(f"FreeCAD ignored FREECAD_USER_HOME ({reported})")
    r = install(tmp_path, classes, "-NoShortcut", "-NoFileAssoc", freecad=REAL_FREECAD,
                env={"FREECAD_USER_HOME": str(profile)})
    assert r.returncode == 0, r.stdout + r.stderr
    app = tmp_path / "Programs" / "CADSwarm"
    assert os.path.realpath(old_copy) == os.path.realpath(app)  # a junction to the one installed copy
    assert (old_copy / "cadswarm" / "app" / "__main__.py").is_file()
