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
        toolbar = ["CadSwarm_Connect", "CadSwarm_Panel", "CadSwarm_AddHelper", "CadSwarm_Invite",
                   "CadSwarm_Interference", "CadSwarm_Export", "CadSwarm_FeaReports"]
        self.appendToolbar("CadSwarm", toolbar)
        self.appendMenu("CadSwarm", toolbar + ["Separator", "CadSwarm_Lock", "CadSwarm_Release",
                                               "Separator", "CadSwarm_Disconnect", "CadSwarm_StopHosting"])

    def Activated(self):
        from cadswarm.gui import panel
        panel.show_panel()
        panel.SESSION.try_auto_rejoin()  # first time: opens the setup guide; later: back in your team

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(CadSwarmWorkbench())
