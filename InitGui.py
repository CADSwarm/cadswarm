# FreeCAD runs this when it starts. This folder (the CADSwarm add-on) is on sys.path,
# so the `cadswarm` package beside this file is importable.
import FreeCADGui as Gui
from PySide import QtCore


def open_on_start():
    """Open the team panel on every launch, not only when someone picks the CADSwarm workbench.

    Starting FreeCAD plainly (its Start page, or a model opened from Explorer) never runs the
    workbench's Activated(), so the panel — and with it the join — only came up if you found
    CADSwarm in the workbench list. That is what "sometimes it doesn't connect" looked like. The
    panel rejoins the team by itself (panel.try_auto_rejoin), so this is the whole of "start CADSwarm".
    """
    try:  # FreeCAD closed while CADSwarm was active last time: give the user their own 3D view colours back
        from cadswarm.gui import workspace
        workspace.WORKSPACE.recover()
    except Exception as e:  # noqa: BLE001
        import FreeCAD
        FreeCAD.Console.PrintLog(f"[CADSwarm] workspace recovery: {e}\n")
    import os
    try:
        from cadswarm.gui import panel
        if panel.SESSION.panel is None:
            panel.show_panel()
        if os.environ.get("CADSWARM_UX_DEMO") == "1":  # a sample team, no server (screenshots, trying it out)
            from cadswarm.gui import demo_team
            demo_team.seed(panel.SESSION)
            if os.environ.get("CADSWARM_UX_TOUR"):
                demo_team.tour(panel.SESSION, os.environ["CADSWARM_UX_TOUR"])
        else:
            panel.SESSION.try_auto_rejoin()
    except Exception as e:  # noqa: BLE001 - never break FreeCAD's startup over this
        import FreeCAD
        FreeCAD.Console.PrintWarning(f"[CADSwarm] could not open the team panel: {e}\n")
    try:  # the prompt bar over the 3D view and Ctrl+K, in every workbench
        from cadswarm.gui import panel, prompt_bar
        prompt_bar.install(panel.SESSION)
    except Exception as e:  # noqa: BLE001
        import FreeCAD
        FreeCAD.Console.PrintWarning(f"[CADSwarm] could not add the prompt bar: {e}\n")


QtCore.QTimer.singleShot(2000, open_on_start)


class CadSwarmWorkbench(Gui.Workbench):
    MenuText = "CADSwarm"
    ToolTip = "Build one 3D model together with your team's AI helpers"
    Icon = """
/* XPM */
static char * icon_xpm[] = {
"16 16 3 1", " 	c None", ".	c #2E86DE", "+	c #F39C12",
"                ", "   ....  ++++   ", "  ...... ++++++ ", "  ...... ++++++ ",
"  ...... ++++++ ", "   ....  ++++   ", "                ", "      ......    ",
"     ........   ", "     ........   ", "     ........   ", "      ......    ",
"                ", "                ", "                ", "                "};
"""

    def Initialize(self):
        from cadswarm.gui import panel
        panel.register_commands()
        from cadswarm.gui import overlay
        overlay.register_command()
        overlay.install()
        from cadswarm.gui.structural import register_command
        register_command()
        from cadswarm.gui import lighten_ui
        lighten_ui.register_command()
        from cadswarm.gui import workspace
        workspace.register_command()
        # While the workbench is active its commands sit in the command ribbon (workspace.py), which hides the
        # stock toolbars; with the professional look off this toolbar opens the panel and the setup guide.
        self.appendToolbar("CADSwarm", ["CadSwarm_Connect", "CadSwarm_Panel", "CadSwarm_ProLook"])
        self.appendMenu("CADSwarm", ["CadSwarm_Connect", "CadSwarm_Panel", "CadSwarm_NewTask", "CadSwarm_TellHelper",
                                     "CadSwarm_EditPart", "CadSwarm_Library", "CadSwarm_Settings", "CadSwarm_Style",
                                     "CadSwarm_Constrain", "CadSwarm_AddHelper", "CadSwarm_Invite", "Separator", "CadSwarm_Interference",
                                     "CadSwarm_Export", "CadSwarm_Lighten", "CadSwarm_OptimizePart", "CadSwarm_RunFea",
                                     "CadSwarm_FeaReports",
                                     "CadSwarm_Pointers", "CadSwarm_ProLook", "Separator", "CadSwarm_Lock", "CadSwarm_Release",
                                     "Separator", "CadSwarm_Disconnect", "CadSwarm_StopHosting"])

    def Activated(self):
        # The professional workspace first (theme, ribbon, status bar), so the panel opens in its colours.
        # It is defensive throughout: a failure there is logged and never stops the panel or the join.
        try:
            from cadswarm.gui import workspace
            workspace.WORKSPACE.activate()
        except Exception as e:  # noqa: BLE001
            import FreeCAD
            FreeCAD.Console.PrintWarning(f"[CADSwarm] could not set up the workspace: {e}\n")
        from cadswarm.gui import panel
        panel.show_panel()
        panel.SESSION.try_auto_rejoin()  # first time: opens the setup guide; later: back in your team

    def Deactivated(self):
        # Another workbench: FreeCAD's own look, toolbars and 3D view colours come back.
        try:
            from cadswarm.gui import workspace
            workspace.WORKSPACE.deactivate()
        except Exception as e:  # noqa: BLE001
            import FreeCAD
            FreeCAD.Console.PrintWarning(f"[CADSwarm] could not restore FreeCAD's look: {e}\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(CadSwarmWorkbench())


class CadSwarmOptimizationWorkbench(Gui.Workbench):
    """Local structural workflow, available without starting a collaboration session."""
    MenuText = "CADSwarm Optimization"
    ToolTip = "Analyze and optimize a solid locally with CalculiX"
    Icon = ""

    def Initialize(self):
        from cadswarm.gui.structural import register_command
        register_command()
        from cadswarm.gui import lighten_ui
        lighten_ui.register_command()
        commands = ["CadSwarm_OptimizationDemo", "CadSwarm_Lighten", "CadSwarm_OptimizePart"]
        self.appendToolbar("Structural optimization", commands)
        self.appendMenu("Optimization", commands)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(CadSwarmOptimizationWorkbench())
