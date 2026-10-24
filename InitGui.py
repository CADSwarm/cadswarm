# FreeCAD runs this when it starts. This folder (the CadSwarm add-on) is on sys.path,
# so the `cadswarm` package beside this file is importable.
import FreeCADGui as Gui


class CadSwarmWorkbench(Gui.Workbench):
    MenuText = "CadSwarm"
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
        toolbar = ["CadSwarm_Connect", "CadSwarm_Panel", "CadSwarm_NewTask", "CadSwarm_TellHelper",
                   "CadSwarm_EditPart", "CadSwarm_Library", "CadSwarm_Settings", "CadSwarm_AddHelper",
                   "CadSwarm_Invite",
                   "CadSwarm_Interference", "CadSwarm_Export", "CadSwarm_Lighten", "CadSwarm_OptimizePart",
                   "CadSwarm_FeaReports", "CadSwarm_Pointers"]
        self.appendToolbar("CadSwarm", toolbar)
        self.appendMenu("CadSwarm", toolbar + ["CadSwarm_Style", "Separator", "CadSwarm_Lock", "CadSwarm_Release",
                                               "Separator", "CadSwarm_Disconnect", "CadSwarm_StopHosting"])

    def Activated(self):
        from cadswarm.gui import panel
        panel.show_panel()
        panel.SESSION.try_auto_rejoin()  # first time: opens the setup guide; later: back in your team

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
