"""Auto-constrain: turning a finished model's repeated sizes and fits into shared Params.

The planner is pure Python (no FreeCAD): it reads what `model()` and `get_object` report and
proposes constraints. `apply` drives the existing /locks and /ops routes, so nothing here needs a
new op kind or a module republish.
"""
import pytest

from cadswarm import constraints


def obj(name, type="Part::Box", props=None, base=(0, 0, 0), rot=None, used_by=(), bbox=None,
        label=None, rev=1, locked_by=None, expressions=None):
    """One object as `model()` plus `get_object` describe it to the planner."""
    placement = {"base": list(base), "rotation": rot or {"axis": [0, 0, 1], "angle": 0}}
    return {"name": name, "label": label or name, "type": type, "rev": rev,
            "properties": props if props is not None else {}, "placement": placement,
            "used_by": list(used_by), "uses": [], "bbox": bbox, "locked_by": locked_by,
            "expressions": expressions or {}}


def aliases(constraint):
    return [p["alias"] for p in constraint["params"]]


def by_kind(plan, kind):
    return [c for c in plan["constraints"] if c["kind"] == kind]


# ----------------------------------------------------------------- shared sizes

def test_equal_heights_become_one_shared_size():
    plan = constraints.plan([
        obj("Base", props={"Length": 60, "Width": 40, "Height": 2}),
        obj("Lid", props={"Length": 70, "Width": 30, "Height": 2}),
    ])
    assert len(by_kind(plan, "shared_size")) == 1
    c = by_kind(plan, "shared_size")[0]
    assert aliases(c) == ["height"]
    assert {b["object"] for b in c["bindings"]} == {"Base", "Lid"}
    assert all(b["expression"] == "Params.height" for b in c["bindings"])
    assert c["params"][0]["value"] == "2 mm"


def test_an_existing_param_with_the_same_value_is_reused_not_duplicated():
    plan = constraints.plan([
        obj("Base", props={"Height": 2}),
        obj("Lid", props={"Height": 2}),
    ], params={"wall": 2})
    c = by_kind(plan, "shared_size")[0]
    assert aliases(c) == ["wall"] and c["params"][0]["reused"] is True
    assert plan["params"] == []  # nothing new to write: the param already exists


def test_a_single_part_is_not_constrained_and_bound_properties_are_left_alone():
    plan = constraints.plan([
        obj("Base", props={"Height": 2}),
        obj("Lid", props={"Height": 2}, expressions={"Height": "Params.wall"}),
    ], params={"wall": 2})
    assert plan["constraints"] == []
    assert any(s["object"] == "Lid" and "already" in s["reason"] for s in plan["skipped"])


def test_another_persons_locked_part_is_skipped_with_a_reason():
    plan = constraints.plan([
        obj("Base", props={"Height": 2}),
        obj("Lid", props={"Height": 2}, locked_by="bob"),
    ])
    assert plan["constraints"] == []
    assert any(s["object"] == "Lid" and "bob" in s["reason"] for s in plan["skipped"])


def test_only_the_requested_names_are_in_scope():
    plan = constraints.plan([
        obj("Base", props={"Height": 2}),
        obj("Lid", props={"Height": 2}),
        obj("Stand", props={"Height": 2}),
    ], names=["Base", "Lid"])
    c = by_kind(plan, "shared_size")[0]
    assert {b["object"] for b in c["bindings"]} == {"Base", "Lid"}
    assert "Stand" not in plan["scope"]


def test_different_values_are_not_grouped():
    plan = constraints.plan([
        obj("Base", props={"Height": 2}),
        obj("Lid", props={"Height": 2.5}),
    ])
    assert by_kind(plan, "shared_size") == []


def test_params_are_named_from_the_dimension_and_never_collide():
    plan = constraints.plan([
        obj("A", props={"Height": 2}), obj("B", props={"Height": 2}),
        obj("C", props={"Height": 5}), obj("D", props={"Height": 5}),
        obj("E", props={"Width": 10}), obj("F", props={"Width": 10}),
    ], params={"height": 9})
    names = [p["alias"] for c in plan["constraints"] for p in c["params"]]
    assert len(names) == len(set(names))
    assert "height_2" in names and "width" in names


# -------------------------------------------------------------------- patterns

def test_three_aligned_holes_with_equal_spacing_become_a_pitch_constraint():
    holes = [obj(f"Hole {i}", type="Part::Cylinder", props={"Radius": 1.6, "Height": 4},
                 base=(10 * i, 5, 0), label=f"Hole {i}", used_by=[f"Cut{i}"]) for i in (1, 2, 3)]
    plan = constraints.plan(holes)
    c = by_kind(plan, "pattern")[0]
    assert aliases(c) == ["hole_x0", "hole_pitch"]
    assert {p["alias"]: p["value"] for p in c["params"]} == {"hole_x0": "10 mm", "hole_pitch": "10 mm"}
    expr = {b["object"]: b["expression"] for b in c["bindings"]}
    assert expr["Hole 1"] == "Params.hole_x0"
    assert expr["Hole 3"] == "Params.hole_x0 + 2 * Params.hole_pitch"
    assert all(b["property"] == ".Placement.Base.x" for b in c["bindings"])


def test_unequal_spacing_is_not_called_a_pattern():
    holes = [obj(f"H{i}", type="Part::Cylinder", props={"Radius": 1.6}, base=(x, 0, 0))
             for i, x in enumerate((0, 10, 25))]
    assert by_kind(constraints.plan(holes), "pattern") == []


# ------------------------------------------------------------------------ fits

def test_a_shaft_in_a_hole_takes_the_teams_slip_fit():
    hole = obj("Hole1", type="Part::Cylinder", props={"Radius": 1.7, "Height": 4},
               base=(0, 0, 0), used_by=["Plate"])
    shaft = obj("Pin", type="Part::Cylinder", props={"Radius": 1.5, "Height": 10},
                base=(0, 0, -2), label="Pin")
    plate = obj("Plate", type="Part::Cut", props={})
    plan = constraints.plan([plate, hole, shaft],
                            settings={"tol_slip_fit": 0.1, "tol_press_fit": -0.05})
    c = by_kind(plan, "fit")[0]
    assert c["objects"] == ["Hole1", "Pin"]
    values = {p["alias"]: p["value"] for p in c["params"]}
    assert values["tol_slip_fit"] == "0.1 mm"
    hole_b = next(b for b in c["bindings"] if b["object"] == "Hole1")
    assert hole_b["property"] == "Radius"
    assert hole_b["expression"] == "Params.pin_r + Params.tol_slip_fit / 2"
    assert hole_b["new"] == pytest.approx(1.55)


def test_an_interfering_shaft_takes_the_press_fit():
    hole = obj("Hole1", type="Part::Cylinder", props={"Radius": 1.47}, used_by=["Plate"])
    shaft = obj("Pin", type="Part::Cylinder", props={"Radius": 1.5}, base=(0, 0, -2), label="Pin")
    plan = constraints.plan([obj("Plate", type="Part::Cut"), hole, shaft],
                            settings={"tol_slip_fit": 0.1, "tol_press_fit": -0.05})
    c = by_kind(plan, "fit")[0]
    hole_b = next(b for b in c["bindings"] if b["object"] == "Hole1")
    assert hole_b["expression"] == "Params.pin_r + Params.tol_press_fit / 2"
    assert hole_b["new"] == pytest.approx(1.475)
    # FreeCAD stores a bare "-0.05 mm" cell as text; the '=' keeps it a quantity an expression can read.
    assert {p["alias"]: p["value"] for p in c["params"]}["tol_press_fit"] == "=-0.05 mm"


def test_a_hole_with_no_mating_shaft_is_not_a_fit():
    hole = obj("Hole1", type="Part::Cylinder", props={"Radius": 1.7}, used_by=["Plate"])
    plan = constraints.plan([obj("Plate", type="Part::Cut"), hole])
    assert by_kind(plan, "fit") == []


# ---------------------------------------------------------------------- apply

class FakeHub:
    def __init__(self):
        self.calls = []

    def post(self, path, body=None):
        self.calls.append((path, body or {}))
        return {"recomputed": []} if path == "/ops" else {}


def shared_plan():
    return constraints.plan([
        obj("Base", props={"Height": 2}, rev=3),
        obj("Lid", props={"Height": 2}, rev=7),
    ])


def test_apply_locks_params_and_every_part_then_writes_and_releases():
    hub = FakeHub()
    out = constraints.apply(shared_plan(), hub)
    paths = [p for p, _ in hub.calls]
    assert paths[0] == "/locks" and paths[-1] == "/locks/release"
    assert set(hub.calls[0][1]["names"]) == {"Params", "Base", "Lid"}
    ops = [b for p, b in hub.calls if p == "/ops"]
    assert ops[0]["op"] == "set_param" and ops[0]["args"]["alias"] == "height"
    assert [o["args"]["name"] for o in ops[1:]] == ["Base", "Lid"]
    assert ops[1]["expected_revs"] == {"Base": 3}
    assert ops[1]["args"]["expressions"] == {"Height": "Params.height"}
    assert set(out["objects"]) == {"Base", "Lid"} and out["params"] == ["height"]


def test_apply_releases_the_locks_even_when_an_op_fails():
    class Failing(FakeHub):
        def post(self, path, body=None):
            super().post(path, body)
            if path == "/ops":
                raise RuntimeError("stale_rev: Base changed")
            return {}

    hub = Failing()
    with pytest.raises(RuntimeError):
        constraints.apply(shared_plan(), hub)
    assert hub.calls[-1][0] == "/locks/release"


def test_apply_can_take_only_the_constraints_a_person_ticked():
    plan = constraints.plan([
        obj("Base", props={"Length": 60, "Height": 2}),
        obj("Lid", props={"Length": 60, "Height": 2}),
    ])
    ids = [c["id"] for c in plan["constraints"] if "height" in c["id"]]
    hub = FakeHub()
    out = constraints.apply(plan, hub, ids=ids)
    assert out["applied"] == ids
    ops = [b for p, b in hub.calls if p == "/ops"]
    assert [o["args"]["alias"] for o in ops if o["op"] == "set_param"] == ["height"]


def test_apply_does_nothing_when_there_is_nothing_to_constrain():
    hub = FakeHub()
    assert constraints.apply({"constraints": []}, hub)["applied"] == []
    assert hub.calls == []


# -------------------------------------------------------------- model -> plan

def test_plan_from_model_merges_get_object_details_and_skips_the_params_sheet():
    model = {"params": {"wall": 2}, "objects": [
        {"name": "Base", "type": "Part::Box", "rev": 4, "placement": {"base": [0, 0, 0]}},
        {"name": "Lid", "type": "Part::Box", "rev": 9, "placement": {"base": [0, 0, 5]}},
        {"name": "Params", "type": "Spreadsheet::Sheet", "rev": 1},
    ]}
    details = {"Base": {"properties": {"Height": 2}}, "Lid": {"properties": {"Height": 2}}}
    plan = constraints.plan_from_model(model, details)
    assert plan["scope"] == ["Base", "Lid"]
    assert [c["id"] for c in plan["constraints"]] == ["shared:wall"]
    assert plan["revs"] == {"Base": 4, "Lid": 9}
    assert plan["params"] == []


def test_plan_from_model_takes_a_selection_and_the_teams_fits():
    model = {"params": {}, "objects": [
        {"name": "Plate", "type": "Part::Cut", "rev": 1},
        {"name": "Hole1", "type": "Part::Cylinder", "rev": 2, "used_by": ["Plate"],
         "placement": {"base": [0, 0, 0]}},
        {"name": "Pin", "type": "Part::Cylinder", "rev": 3, "label": "Pin",
         "placement": {"base": [0, 0, -2]}},
    ]}
    details = {"Hole1": {"properties": {"Radius": 1.7}},
               "Pin": {"properties": {"Radius": 1.5}}}
    plan = constraints.plan_from_model(model, details, names=["Hole1", "Pin"],
                                       settings={"tol_slip_fit": 0.2})
    fit = plan["constraints"][0]
    assert fit["kind"] == "fit"
    assert {p["alias"]: p["value"] for p in fit["params"]}["tol_slip_fit"] == "0.2 mm"


# ------------------------------------------------------------ the /constrain route

def hub_with(model, details, settings=None):
    from cadswarm import space_hub
    hub = space_hub.SpaceHub({"uri": "http://127.0.0.1:1", "db": "t"}, {"token": "x"})
    hub.model = lambda: model
    hub.run_op = lambda kind, args, expected_revs=None: details.get(args["name"]) or {}
    hub.setting_values = lambda: settings or {}
    hub.writes = []
    hub.post = lambda path, body=None: (hub.writes.append((path, body or {})), {})[1]
    return hub


def test_the_route_previews_without_writing_and_then_applies_the_ticked_constraint():
    hub = hub_with({"params": {}, "objects": [
        {"name": "Base", "type": "Part::Box", "rev": 4},
        {"name": "Lid", "type": "Part::Box", "rev": 9},
        {"name": "Params", "type": "Spreadsheet::Sheet", "rev": 1},
    ]}, {"Base": {"properties": {"Height": 2}}, "Lid": {"properties": {"Height": 2}}})
    preview = hub.constrain({"apply": False})
    assert preview["applied"] is None
    assert [c["id"] for c in preview["plan"]["constraints"]] == ["shared:height"]
    assert hub.writes == []

    out = hub.constrain({"apply": True, "ids": ["shared:height"]})
    paths = [p for p, _ in hub.writes]
    assert paths.count("/locks") == 1 and paths[-1] == "/locks/release"
    assert out["applied"] == ["shared:height"]
    assert out["objects"] == ["Base", "Lid"]
    assert out["plan"]["constraints"][0]["params"][0]["alias"] == "height"


def test_the_mcp_tool_previews_then_applies(monkeypatch):
    from cadswarm import mcp_server
    calls = []
    monkeypatch.setattr(mcp_server, "call",
                        lambda method, path, body=None, **kw: calls.append((method, path, body)) or {"ok": True})
    mcp_server.auto_constrain(names=["Base", "Lid"], apply=False)
    assert calls[-1] == ("POST", "/constrain", {"names": ["Base", "Lid"], "apply": False})
    mcp_server.auto_constrain(apply=True, ids=["shared:height"])
    assert calls[-1] == ("POST", "/constrain", {"apply": True, "ids": ["shared:height"]})



