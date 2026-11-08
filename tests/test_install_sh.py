"""Non-Windows installers clearly report that the standalone alpha is Windows-first."""
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")


@pytest.mark.parametrize("script", ["install/install.sh", "install/quick-install.sh"])
def test_non_windows_installers_report_the_supported_platform(script):
    result = subprocess.run(["bash", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "supports Windows only" in result.stdout
    assert "FreeCAD isn't installed" not in result.stdout
