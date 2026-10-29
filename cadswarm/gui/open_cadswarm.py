# Passed to freecad.exe by the installer and the desktop shortcut: opens FreeCAD straight
# into CADSwarm (which shows the setup guide the first time). An update also leaves the files
# that were open for this to reopen, so the restart puts the person back where they were.
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore


def _open():
    try:
        from cadswarm import update
        for path in update.take_reopen():
            try:
                App.openDocument(path)
            except Exception as e:  # one missing file must not stop the rest
                App.Console.PrintWarning(f"[CADSwarm] couldn't reopen {path}: {e}\n")
    except Exception as e:  # noqa: BLE001 - never leave a hobbyist staring at a blank FreeCAD
        App.Console.PrintWarning(f"[CADSwarm] couldn't reopen your documents: {e}\n")
    try:
        Gui.activateWorkbench("CadSwarmWorkbench")
    except Exception as e:  # never leave a hobbyist staring at a blank FreeCAD
        App.Console.PrintError(f"[CADSwarm] Couldn't open the CADSwarm workbench: {e}\n")


QtCore.QTimer.singleShot(1200, _open)
