"""The Windows setup installs a managed standalone runtime and never launches FreeCAD."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_installer_sets_up_standalone_runtime():
    script = (ROOT / "install" / "install.ps1").read_text(encoding="utf-8")
    assert "Python.Python.3.12" in script
    assert 'pip install "$($AppDir)[desktop]"' in script
    assert 'cadswarm.app' in script
    assert "FindFreeCAD" not in script
    assert "FreeCAD.exe" not in script
