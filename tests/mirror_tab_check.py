"""Real (offscreen) FreeCAD: the live model is one tab that is reused, and a refresh leaves the person's
own document in front. Run by tests/test_mirror_tab.py with CADSWARM_TEST_PANEL_GUI=1."""
import json
import sys
from pathlib import Path


def main(out):
    import FreeCAD as App
    import FreeCADGui as Gui
    Gui.showMainWindow()

    from cadswarm.gui import panel

    # What the person has open: a live-model tab FreeCAD had to suffix earlier, and their own model.
    leftover = App.newDocument("CadSwarmLive1")
    leftover.addObject("Part::Box", "Hub")
    mine = App.newDocument("MyDrone")
    mine.addObject("Part::Box", "Frame")
    App.setActiveDocument("MyDrone")

    session = panel.Session()
    session.project = "Racing Drone"
    doc = session.mirror()
    result = {
        "reused_suffixed_mirror": doc.Name == "CadSwarmLive1",
        "one_mirror_tab": sorted(App.listDocuments()) == ["CadSwarmLive1", "MyDrone"],
        "label": doc.Label,
        "kept_its_parts": [o.Name for o in doc.Objects],
    }

    # A refresh (an agent added a part, a snapshot arrived) must not pull the person off their own document.
    session.frame_model()
    result["active_after_refresh"] = Gui.ActiveDocument.Document.Name
    result["mirror_framed"] = session._framed

    # "Done, show me the model" is the explicit request to bring it to the front.
    session.frame_model(show=True)
    result["active_after_show"] = Gui.ActiveDocument.Document.Name

    # A second connect reuses the tab it just used instead of opening another one.
    session.mirror()
    result["still_one_mirror_tab"] = sorted(App.listDocuments()) == ["CadSwarmLive1", "MyDrone"]

    class Boom(Exception):
        def __init__(self, code, status=None):
            super().__init__(code)
            self.code, self.status = code, status

    result["missing_db"] = panel.friendly_error(Boom(404), "http://127.0.0.1:3000")
    result["paused_db"] = panel.friendly_error(Boom(503), "https://maincloud.spacetimedb.com")

    Path(out, "mirror-tab.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
