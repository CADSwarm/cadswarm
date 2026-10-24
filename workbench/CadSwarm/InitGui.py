import FreeCADGui as Gui


class CadSwarmWorkbench(Gui.Workbench):
    MenuText = "CadSwarm"
    ToolTip = "Watch and steer your team's agents building one shared model"
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
        import cadswarm_wb
        cadswarm_wb.register_commands()
        cmds = ["CadSwarm_Connect", "CadSwarm_Panel", "CadSwarm_Lock", "CadSwarm_Release",
                "CadSwarm_Interference", "CadSwarm_Export", "CadSwarm_Disconnect"]
        self.appendToolbar("CadSwarm", cmds)
        self.appendMenu("CadSwarm", cmds)

    def Activated(self):
        import cadswarm_wb
        cadswarm_wb.show_panel()
        cadswarm_wb.SESSION.try_auto_rejoin()  # back in the team you used last time, if it's up

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(CadSwarmWorkbench())
