"""The professional workspace's pure logic: theme tokens and stylesheets, FreeCAD parameter save/restore,
the command ribbon's spec, the icons and the AI Build pane's view-model. No Qt and no FreeCAD needed."""
import json
import xml.etree.ElementTree as ET

import pytest

from cadswarm import step_plan
from cadswarm.gui import build_view, icons, pro_style, ribbon_spec, theme


# ------------------------------------------------------------------------------------------- theme

def test_tokens_match_the_web_palette():
    light, dark = pro_style.tokens("light"), pro_style.tokens("dark")
    assert light["CHROME"] == "#eef0f3" and light["SURFACE"] == "#ffffff" and light["LINE"] == "#d3d7dd"
    assert light["TEXT"] == "#1f2329" and light["ACCENT"] == "#2b6cd9" and light["ACCENT_SOFT"] == "#e4edfb"
    assert light["PRESELECT"] == "#f2a93b" and light["SELECT"] == "#1f7ae0"
    assert (light["OK"], light["WARN"], light["ERR"]) == ("#2e9d5b", "#d99a00", "#d23f3f")
    assert set(light) == set(dark)
    assert pro_style.tokens("anything") == light


def test_pick_mode_follows_the_setting_or_freecads_window_colour():
    assert pro_style.pick_mode("dark", (255, 255, 255)) == "dark"
    assert pro_style.pick_mode("light", (0, 0, 0)) == "light"
    assert pro_style.pick_mode("auto", (40, 40, 40)) == "dark"
    assert pro_style.pick_mode("auto", (239, 239, 239)) == "light"
    assert pro_style.pick_mode("auto", None) == "light"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_stylesheets_are_complete(mode):
    t = pro_style.tokens(mode)
    app = pro_style.app_stylesheet(t, "Inter")
    for selector in ("QMainWindow", "QMenuBar", "QMenu::item:selected", "QToolBar", "QDockWidget::title",
                     "QTreeView", "QTabBar::tab:selected", "QLineEdit", "QPushButton", "QScrollBar:vertical",
                     "QStatusBar", "QHeaderView::section"):
        assert selector in app, selector
    assert "$" not in app and t["ACCENT"] in app and 'font-family: "Inter"' in app
    assert "font-size: 12px" in app
    # Never a background on every QWidget: the 3D view's OpenGL surface draws itself.
    assert not any(line.startswith("QWidget {") and "background" in line for line in app.splitlines())
    ribbon = pro_style.ribbon_stylesheet(t)
    assert "$" not in ribbon and "#csRibbon" in ribbon and "font-family" not in ribbon
    panel = theme.panel_sheet(pro_style.panel_tokens(t))
    assert "$" not in panel and t["ACCENT"] in panel and t["SURFACE"] in panel


def test_panel_template_still_gives_the_dark_panel():
    assert theme.panel_sheet({}) == theme.PANEL
    assert theme.ACCENT in theme.PANEL and "$" not in theme.PANEL


def test_pick_font_takes_the_first_installed_system_sans():
    assert pro_style.pick_font(["DejaVu Sans", "Roboto"]) == "Roboto"
    assert pro_style.pick_font(["segoe ui"]) == "Segoe UI"
    assert pro_style.pick_font(["Comic Sans"]) == ""


def test_colours_pack_the_way_freecad_stores_them():
    assert pro_style.packed("#1f7ae0") == 0x1F7AE0FF
    assert pro_style.unpacked(0x1F7AE0FF) == "#1f7ae0"
    params = {name: value for _g, _k, name, value in pro_style.view_params(pro_style.tokens("light"))}
    assert params["Gradient"] is True and params["Simple"] is False
    assert pro_style.unpacked(params["BackgroundColor2"]) == "#c5cfda"   # top
    assert pro_style.unpacked(params["BackgroundColor3"]) == "#f3f5f7"   # bottom
    assert pro_style.unpacked(params["SelectionColor"]) == "#1f7ae0"
    assert pro_style.unpacked(params["HighlightColor"]) == "#f2a93b"


# ------------------------------------------------------------------------------ parameter save/restore

class FakeGroup:
    """FreeCAD's ParamGet group: Get<Type>(name, default), Set<Type>, Rem<Type>."""

    def __init__(self):
        self.values = {}

    def __getattr__(self, attr):
        for prefix in ("Get", "Set", "Rem"):
            if attr.startswith(prefix):
                kind = attr[len(prefix):]
                if prefix == "Get":
                    return lambda name, default=None: self.values.get((kind, name), default)
                if prefix == "Set":
                    return lambda name, value: self.values.__setitem__((kind, name), value)
                return lambda name: self.values.pop((kind, name), None)
        raise AttributeError(attr)


class FakeParams:
    def __init__(self):
        self.groups = {}

    def __call__(self, path):
        return self.groups.setdefault(path, FakeGroup())


VIEW = pro_style.VIEW


def test_stash_saves_then_restores_existing_and_missing_values():
    params, store = FakeParams(), FakeGroup()
    view = params(VIEW)
    view.SetUnsigned("BackgroundColor2", 0x334D80FF)
    view.SetBool("Gradient", False)
    stash = pro_style.ParamStash(params, store)
    assert stash.apply(pro_style.view_params(pro_style.tokens("light"))) == []
    assert view.GetUnsigned("BackgroundColor2") == pro_style.packed("#c5cfda")
    assert view.GetBool("Gradient") is True and stash.pending
    assert json.loads(store.GetString("ProUiStash"))  # kept in CADSwarm's preferences until restored
    assert stash.restore() == []
    assert view.GetUnsigned("BackgroundColor2") == 0x334D80FF
    assert view.GetBool("Gradient") is False
    assert ("Unsigned", "SelectionColor") not in view.values  # was not there: removed again
    assert not stash.pending and store.GetString("ProUiStash", "") == ""


def test_stash_survives_a_restart_and_never_saves_our_own_look_as_the_users():
    params, store = FakeParams(), FakeGroup()
    params(VIEW).SetUnsigned("SelectionColor", 0x1CAD1CFF)
    pro_style.ParamStash(params, store).apply([(VIEW, "Unsigned", "SelectionColor", 0x1F7AE0FF)])
    # FreeCAD closed while CADSwarm was active; the next start applies again (dark this time), then restores.
    again = pro_style.ParamStash(params, store)
    assert again.pending
    again.apply([(VIEW, "Unsigned", "SelectionColor", 0x4C9AFFFF)])
    assert params(VIEW).GetUnsigned("SelectionColor") == 0x4C9AFFFF
    again.restore()
    assert params(VIEW).GetUnsigned("SelectionColor") == 0x1CAD1CFF


def test_stash_remember_records_without_writing():
    params, store = FakeParams(), FakeGroup()
    bars = params(pro_style.TOOLBARS)
    bars.SetBool("File", True)
    stash = pro_style.ParamStash(params, store)
    stash.remember([(pro_style.TOOLBARS, "Bool", "File"), (pro_style.TOOLBARS, "Bool", "Macro")])
    assert bars.GetBool("File") is True and ("Bool", "Macro") not in bars.values
    bars.SetBool("File", False)   # FreeCAD saw the toolbar hidden and wrote it down
    bars.SetBool("Macro", False)
    stash.restore()
    assert bars.GetBool("File") is True and ("Bool", "Macro") not in bars.values


def test_stash_carries_on_past_a_failing_parameter():
    params, store = FakeParams(), FakeGroup()

    class Broken(FakeGroup):
        def SetBool(self, name, value):
            raise RuntimeError("read-only")
    params.groups["broken"] = Broken()
    stash = pro_style.ParamStash(params, store)
    failures = stash.apply([("broken", "Bool", "X", True), (VIEW, "Bool", "Gradient", True)])
    assert [f[0] for f in failures] == ["X"]
    assert params(VIEW).GetBool("Gradient") is True


# ------------------------------------------------------------------------------------------- ribbon

def test_ribbon_tabs_follow_the_shared_layout():
    assert ribbon_spec.TAB_NAMES == ["Features", "Sketch", "Evaluate", "Assembly", "Library", "AI Build", "View"]
    groups = dict(ribbon_spec.RIBBON)
    ai = [label for label, items in groups["AI Build"] for _ in items]
    assert ai[:4] == ["Plan", "Plan", "Plan", "Control"]
    ai_labels = [" ".join(b["label"].split()) for _g, items in groups["AI Build"] for b in items]
    for want in ("New Plan", "Run Next Step", "Run All Ready", "Pause", "Re-plan from Step", "Assign Step",
                 "Review"):
        assert want in ai_labels
    ids = [b["id"] for b in ribbon_spec.buttons()]
    assert len(ids) == len(set(ids))


def test_every_button_has_an_icon_and_something_to_run():
    for b in ribbon_spec.buttons() + ribbon_spec.QUICK:
        assert b["icon"] in icons.ICONS, b["id"]
        assert b["cmds"] or b["action"], b["id"]
        assert b["tip"], b["id"]


def test_feature_buttons_map_to_real_freecad_commands():
    by_id = {b["id"]: b for b in ribbon_spec.buttons()}
    assert by_id["extrude"]["cmds"] == ("PartDesign_Pad",)
    assert by_id["cut"]["cmds"] == ("PartDesign_Pocket",)
    assert by_id["revolve"]["cmds"] == ("PartDesign_Revolution",)
    assert by_id["shell"]["cmds"] == ("PartDesign_Thickness",)
    assert by_id["sketch"]["cmds"] == ("Sketcher_NewSketch",)
    assert by_id["boolean"]["cmds"] == ("Part_Boolean",)
    assert by_id["v_fit"]["cmds"] == ("Std_ViewFitAll",)


def test_resolve_takes_the_first_registered_command():
    b = next(b for b in ribbon_spec.buttons() if b["id"] == "sk_coincident")
    assert ribbon_spec.resolve(b, {"Sketcher_ConstrainCoincident"}) == "Sketcher_ConstrainCoincident"
    assert ribbon_spec.resolve(b, {"Sketcher_ConstrainCoincidentUnified", "Sketcher_ConstrainCoincident"}) == \
        "Sketcher_ConstrainCoincidentUnified"
    assert ribbon_spec.resolve(b, set()) == ""
    needed = ribbon_spec.workbenches_needed({"Std_ViewFitAll"})
    assert ribbon_spec.PD in needed and ribbon_spec.SK in needed


def test_command_search():
    assert ribbon_spec.search("fil")[0]["id"] == "fillet"
    assert ribbon_spec.search("run next")[0]["id"] == "ai_next"
    assert any(b["id"] == "hole" for b in ribbon_spec.search("tapped"))
    assert ribbon_spec.search("") == []


@pytest.mark.parametrize("name", sorted(icons.ICONS))
def test_icons_are_valid_svg(name):
    root = ET.fromstring(icons.svg(name, "#353b44", "#2b6cd9"))
    assert root.get("viewBox") == "0 0 24 24" and root.get("stroke-width") == "1.5"
    assert "@A" not in icons.svg(name)


# ----------------------------------------------------------------------------------- AI Build pane

def _tasks():
    rows = [("t1", "1. Mast", "done", "ann"), ("t2", "2. Base", "done", "ann"), ("t3", "3. Pitch link", "claimed", "bob"),
            ("t4", "4. Servo screws", "open", None), ("t5", "Check fit", "open", None)]
    return [{"id": i, "title": title, "status": st, "owner_name": who, "brief": "b1", "created_at": n, "detail": title}
            for n, (i, title, st, who) in enumerate(rows)]


def test_snapshot_view_reads_the_steps_back_from_task_titles():
    model = build_view.from_snapshot({"id": "b1", "text": "Rotor head"}, _tasks())
    assert [s["name"] for s in model["steps"]] == ["Mast", "Base", "Pitch link", "Servo screws", "Check fit"]
    assert [s["n"] for s in model["steps"]][:4] == [1, 2, 3, 4]
    assert [s["state"] for s in model["steps"]] == ["done", "done", "current", "pending", "pending"]
    assert model["line"] == "2 of 5 done · now: Pitch link"
    assert model["fraction"] == pytest.approx(0.4)
    assert model["steps"][2]["note"] == "bob is building it"
    assert build_view.from_snapshot(None, [])["line"] == "No steps yet"


def test_route_view_uses_the_hubs_op_feed():
    plan = {"version": 2, "title": "Rotor head", "steps": [
        {"n": 1, "id": "s01", "name": "Mast", "task": "t1", "objects": ["Mast", "SK_Mast"],
         "ops": ["Create sketch SK_Mast", "Revolve Mast"]},
        {"n": 2, "id": "s02", "name": "Base", "task": "t2"},
        {"n": 3, "id": "s03", "name": "Pitch link", "task": "t3", "depends_on": ["s01"],
         "ops": ["Create sketch SK_PitchLink", "Extrude PitchLink", "Set color", "Save part"]},
        {"n": 4, "id": "s04", "name": "Servo screws", "kind": "library", "task": "t4", "depends_on": ["s03"]}]}
    tasks = _tasks()[:4]
    answer = {"brief": "b1", "title": "Rotor head", "version": 2, **step_plan.progress(plan, tasks)}
    answer["steps"][2]["op_feed"] = [
        {"label": "Create sketch", "targets": ["SK_PitchLink"], "duration_s": 1.94, "running": False, "status": "done"},
        {"label": "Extrude", "targets": ["PitchLink"], "duration_s": None, "running": True, "status": "running"}]
    model = build_view.from_route(answer, {"id": "b1", "text": "x"}, tasks)
    pitch = model["steps"][2]
    assert [(f["label"], f["target"], f["time"]) for f in pitch["feed"]] == [
        ("Create sketch", "SK_PitchLink", "1.9 s"), ("Extrude", "PitchLink", "Running")]
    assert [p["label"] for p in pitch["planned"]] == ["Set color", "Save part"]  # what is still to come
    assert model["steps"][3]["kind_word"] == "Library"
    assert model["steps"][3]["note"] == "Waiting for Pitch link"
    assert model["line"] == "2 of 4 done · now: Pitch link" and model["title"] == "Rotor head"
    # A raw plan + ops answer is computed here with step_plan's op matching.
    raw = build_view.from_route({"plan": plan, "tasks": tasks, "ops": [
        {"id": "o1", "kind": "create_sketch", "args_json": json.dumps({"name": "SK_Mast"}), "by": 9,
         "started_ms": 1000, "finished_ms": 2900, "status": "done"}]})
    assert [(f["label"], f["target"], f["time"]) for f in raw["steps"][0]["feed"]] == [
        ("Create sketch", "SK_Mast", "1.9 s")]


def test_route_view_falls_back_to_the_snapshot():
    model = build_view.from_route(None, {"id": "b1", "text": "Rotor head"}, _tasks())
    assert model["total"] == 5


def test_feed_rows_and_durations():
    assert build_view.duration_text(1.94) == "1.9 s"
    assert build_view.duration_text(75) == "1 min 15 s"
    assert build_view.duration_text(120) == "2 min"
    assert build_view.duration_text(None) == ""
    failed = build_view.feed_row({"label": "Fillet", "targets": ["Hub"], "status": "failed", "running": True})
    assert failed["time"] == "Failed" and not failed["running"]
    raw = build_view.feed_row({"kind": "pad", "args_json": "{\"name\": \"Mast\"}", "started_ms": 0,
                               "finished_ms": 0, "status": "running"})
    assert raw["label"] == "Extrude" and raw["time"] == "Running"


def test_pick_brief_follows_the_live_request():
    briefs = [{"id": "a", "status": "ready", "at": 3}, {"id": "b", "status": "building", "at": 2},
              {"id": "c", "status": "building", "at": 1}]
    assert build_view.pick_brief(briefs)["id"] == "b"
    assert build_view.pick_brief(briefs, "a")["id"] == "a"
    assert build_view.pick_brief(briefs, "gone")["id"] == "b"
    assert build_view.pick_brief([]) is None


def test_view_key_changes_with_the_feed():
    a = build_view.from_snapshot({"id": "b1", "text": "x"}, _tasks())
    tasks = _tasks()
    tasks[2]["status"] = "done"
    b = build_view.from_snapshot({"id": "b1", "text": "x"}, tasks)
    assert build_view.view_key(a) != build_view.view_key(b)
    assert build_view.view_key(a) == build_view.view_key(build_view.from_snapshot({"id": "b1", "text": "x"}, _tasks()))
