# Passed to freecad.exe by the installer and the desktop shortcut: opens FreeCAD straight
# into CadSwarm (which shows the setup guide the first time).
import FreeCADGui as Gui
from PySide import QtCore


def _open():
    try:
        Gui.activateWorkbench("CadSwarmWorkbench")
    except Exception as e:  # never leave a hobbyist staring at a blank FreeCAD
        import FreeCAD
        FreeCAD.Console.PrintError(f"[CadSwarm] Couldn't open the CadSwarm workbench: {e}\n")


QtCore.QTimer.singleShot(1200, _open)
