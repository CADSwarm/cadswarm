"""Step plans (plan_json v2): reading, the rules, readiness, assignment and the progress line. No FreeCAD."""
import json

import pytest

from cadswarm import step_plan as sp


def plan():
    return {
        "version": 2, "title": "Rotor head",
        "steps": [
            {"n": 1, "id": "s01", "name": "Base", "kind": "part", "summary": "The base: a plate.", "task": "t1",
             "objects": ["Base"], "depends_on": []},
            {"n": 2, "id": "s02", "name": "Mast", "kind": "part", "summary": "The mast.", "task": "t2",
             "objects": ["Mast"], "depends_on": ["s01"]},
            {"n": 3, "id": "s03", "name": "Servo screws", "kind": "library", "summary": "Four M3x10.", "task": "t3",
             "objects": ["ServoScrew1"], "depends_on": ["s01"],
             "component": {"family": "iso4762", "size": "M3", "length": 10}},
            {"n": 4, "id": "s04", "name": "Assembly", "kind": "assembly", "summary": "Base fixed.", "task": "t4",
             "depends_on": ["s01", "s02", "s03"]},
            {"n": 5, "id": "s05", "name": "Check fit", "kind": "check", "summary": "Interference.", "task": "t5",
             "depends_on": ["s04"]},
        ],
    }


def tasks(**status):
    base = {f"t{i}": "open" for i in range(1, 6)}
    base.update(status)
    return [{"id": k, "status": v, "owner": 0, "assignee": ""} for k, v in base.items()]


def test_a_valid_plan_normalises_and_derives_parts_for_old_readers():
    p = sp.check(plan(), task_ids={"t1", "t2", "t3", "t4", "t5"})
    assert p["version"] == 2 and [s["n"] for s in p["steps"]] == [1, 2, 3, 4, 5]
    assert p["parts"] == [{"name": "Base", "task": "t1", "summary": "The base: a plate."},
                          {"name": "Mast", "task": "t2", "summary": "The mast."},
                          {"name": "Servo screws", "task": "t3", "summary": "Four M3x10."}]
    assert p["steps"][3]["objects"] == [] and p["steps"][0]["ops"] == [] and p["steps"][0]["assignee"] == ""


def test_json_text_and_dependencies_by_number_are_accepted():
    raw = plan()
    raw["steps"][1]["depends_on"] = [1]
    raw["steps"].reverse()  # out of order on input, sorted by n on output
    p = sp.check(json.dumps(raw))
    assert p["steps"][1]["depends_on"] == ["s01"] and p["steps"][0]["id"] == "s01"


def test_a_version_1_plan_reads_as_ordered_steps_without_dependencies():
    p = sp.normalise({"parts": [{"name": "Base", "task": "t1", "summary": "a"}, {"name": "Lid", "task": "t2"}],
                      "sources": [{"url": "x"}]})
    assert [(s["n"], s["id"], s["kind"], s["depends_on"]) for s in p["steps"]] == [
        (1, "s01", "part", []), (2, "s02", "part", [])]
    assert p["parts"][0]["name"] == "Base" and p["sources"] == [{"url": "x"}]  # kept as written
    assert sp.validate(p) == []


@pytest.mark.parametrize("mutate, needle", [
    (lambda p: p["steps"][1].update(id="s01"), "used twice"),
    (lambda p: p["steps"][1].update(n=1), "number 1 is used twice"),
    (lambda p: p["steps"][1].update(depends_on=["s09"]), "not a step id"),
    (lambda p: p["steps"][1].update(depends_on=["s04"]), "comes later"),
    (lambda p: p["steps"][1].update(depends_on=["s02"]), "itself"),
    (lambda p: p["steps"][1].update(kind="widget"), "must be one of"),
    (lambda p: p["steps"][2].pop("component"), "no `component`"),
    (lambda p: p["steps"][2].update(component={"family": "iso4762"}), "no `component`"),
    (lambda p: p["steps"][0].update(name=""), "no `name`"),
    (lambda p: p["steps"][0].update(task=""), "no `task`"),
    (lambda p: p["steps"][1].update(task="t1"), "one task per step"),
    (lambda p: p.update(steps=[]), "no steps"),
    (lambda p: p.update(steps="three"), "must be a list"),
])
def test_each_broken_rule_is_named(mutate, needle):
    p = plan()
    mutate(p)
    problems = sp.validate(p)
    assert any(needle in x for x in problems), problems
    with pytest.raises(sp.PlanError) as e:
        sp.check(p)
    assert needle in str(e.value)


def test_unknown_tasks_and_bad_json_are_refused():
    assert any("not a task of this brief" in x for x in sp.validate(plan(), task_ids={"t1"}))
    assert "not valid JSON" in sp.validate("{nope")[0]
    assert "JSON object" in sp.validate("[1, 2]")[0]


def test_a_library_step_may_name_a_library_id_instead_of_a_family():
    p = plan()
    p["steps"][2]["component"] = "c_m3_insert"
    assert sp.validate(p) == []
    p["steps"][2]["component"] = {"id": "c_608"}
    assert sp.validate(p) == []


def test_cycles_are_detected_even_when_read_leniently():
    p = {"steps": [{"n": 1, "id": "a", "name": "A", "task": "t1", "depends_on": ["b"]},
                   {"n": 2, "id": "b", "name": "B", "task": "t2", "depends_on": ["a"]}]}
    assert sp._has_cycle(sp.normalise(p)["steps"])


def test_ready_steps_follow_the_dependencies():
    p = sp.normalise(plan())
    assert [s["id"] for s in sp.ready_steps(p, tasks())] == ["s01"]
    assert [s["id"] for s in sp.ready_steps(p, tasks(t1="done"))] == ["s02", "s03"]
    assert [s["id"] for s in sp.ready_steps(p, tasks(t1="done", t2="done", t3="claimed"))] == []
    assert [s["id"] for s in sp.ready_steps(p, tasks(t1="done", t2="done", t3="done"))] == ["s04"]
    deps = sp.unfinished_deps(p["steps"][3], p, tasks(t1="done"))
    assert [(d["name"], d["status"]) for d in deps] == [("Mast", "open"), ("Servo screws", "open")]


def test_next_step_prefers_my_assignment_then_the_lowest_number():
    p = sp.normalise(plan())
    t = tasks(t1="done")
    assert sp.next_step(p, t, "w1")["step"]["id"] == "s02"
    p["steps"][2]["assignee"] = "W1"
    assert sp.next_step(p, t, "w1")["step"]["id"] == "s03"
    # Pinned to someone else: skipped by me, unless that person is not on the team.
    assert sp.next_step(p, t, "w2")["step"]["id"] == "s02"
    t2 = tasks(t1="done", t2="claimed")
    assert sp.next_step(p, t2, "w2", live=["w1", "w2"])["wait"] is True
    assert sp.next_step(p, t2, "w2", live=["w2"])["step"]["id"] == "s03"


def test_a_task_pinned_on_the_row_counts_as_assigned():
    p = sp.normalise(plan())
    t = tasks(t1="done")
    t[1]["assignee"] = "carol"
    assert sp.next_step(p, t, "w1")["step"]["id"] == "s03"
    assert sp.next_step(p, t, "carol")["step"]["id"] == "s02"


def test_next_step_returns_the_step_i_already_hold_first():
    p = sp.normalise(plan())
    t = tasks(t1="done")
    t[2].update(status="claimed", owner=7)
    r = sp.next_step(p, t, "w1", me_id=7)
    assert r["step"]["id"] == "s03" and r["claimed"] is True


def test_next_step_waits_while_dependencies_are_being_built_and_is_done_at_the_end():
    p = sp.normalise(plan())
    r = sp.next_step(p, tasks(t1="claimed"), "w1")
    assert r["wait"] is True
    assert {b["name"]: b["waiting_for"] for b in r["blocked"]}["Mast"] == ["Base"]
    done = tasks(**{f"t{i}": "done" for i in range(1, 6)})
    assert sp.next_step(p, done, "w1") == {"done": True}
    # A stopped step is not work left.
    assert sp.next_step(p, tasks(**{f"t{i}": "done" for i in range(1, 5)}, t5="cancelled"), "w1") == {"done": True}


def test_a_cancelled_dependency_is_a_dead_end_not_a_wait():
    p = sp.normalise(plan())
    r = sp.next_step(p, tasks(t1="done", t2="cancelled", t3="done"), "w1")
    assert r.get("stuck") is True and "Assembly" in r["message"] and "Check fit" in r["message"]
    # A dependency waiting for a person's answer is not a dead end: it may still be finished.
    assert sp.next_step(p, tasks(t1="done", t2="blocked", t3="done"), "w1")["wait"] is True


def test_open_tasks_outside_the_plan_are_handed_out_after_the_steps():
    p = sp.normalise(plan())
    done = tasks(**{f"t{i}": "done" for i in range(1, 6)})
    fix = {"id": "t_fix", "title": "Widen the lid lip", "status": "open", "objects": ["Lid"], "assignee": ""}
    r = sp.next_step(p, done, "w1", extra_tasks=[fix])
    assert r["step"]["kind"] == "fix" and r["task"]["id"] == "t_fix"


def test_progress_line_counts_done_and_names_the_current_steps():
    p = sp.normalise(plan())
    t = tasks(t1="done", t2="claimed", t3="claimed")
    t[1]["owner_name"] = "worker1"
    pr = sp.progress(p, t)
    assert pr["line"] == "1 of 5 done · now: Mast, Servo screws"
    assert (pr["total"], pr["done"], pr["current"]) == (5, 1, ["Mast", "Servo screws"])
    s4 = pr["steps"][3]
    assert s4["status"] == "open" and s4["ready"] is False and s4["waiting_for"] == ["Mast", "Servo screws"]
    assert pr["steps"][1]["owner"] == "worker1"
    assert sp.progress(p, tasks())["line"] == "0 of 5 done"
    assert sp.progress(p, [])["steps"][0]["status"] == "missing"


def test_ops_of_a_step_are_its_owners_ops_in_the_window_or_name_its_objects():
    step = sp.normalise(plan())["steps"][1]
    ops = [
        {"id": "o1", "by": 7, "kind": "create_sketch", "args_json": '{"name": "SK_Mast"}', "status": "done",
         "started_ms": 1000, "finished_ms": 2900},
        {"id": "o2", "by": 7, "kind": "extrude", "args_json": '{"name": "Mast"}', "status": "running",
         "started_ms": 3000, "finished_ms": 0},
        {"id": "o3", "by": 8, "kind": "set_color", "args_json": '{"name": "Mast"}', "status": "done",
         "started_ms": 500, "finished_ms": 600},
        {"id": "o4", "by": 7, "kind": "create_primitive", "args_json": '{"name": "Other"}', "status": "done",
         "started_ms": 9000, "finished_ms": 9100},
    ]
    feed = sp.step_ops(step, ops, owner=7, start_ms=900, end_ms=5000)
    assert [f["id"] for f in feed] == ["o3", "o1", "o2"]
    assert feed[1]["label"] == "Create sketch" and feed[1]["duration_s"] == 1.9 and feed[1]["targets"] == ["SK_Mast"]
    assert feed[2]["running"] is True and feed[2]["duration_s"] is None


def assembly_plan(mates):
    p = plan()
    p["steps"][3]["mates"] = mates
    return p


def test_optional_params_targets_ports_and_vocabulary_ops_are_accepted():
    p = plan()
    p["steps"][1].update(params={"shaft_d": 8, "length": "Params.mast_l"},
                         targets={"bbox_mm": [8, 8, 120], "tol_mm": 0.5, "holes": 0, "min_wall_mm": 1.2},
                         ports=[{"name": "mast_axis", "type": "axis"}, {"name": "collar_bottom", "type": "plane"}],
                         ops=["Create sketch SK_Mast", "Revolve Mast", "Set color", "Save part"])
    p["steps"][3]["mates"] = [{"type": "fixed", "a": "Base"},
                              {"type": "concentric", "a": "Base.mast_bore", "b": "Mast.mast_axis"},
                              {"type": "coincident", "a": "Base.top", "b": "Mast.collar_bottom"},
                              {"type": "distance", "a": "Base.top", "b": "ServoScrew1.head", "value": 2}]
    assert sp.validate(p) == []
    assert [m["type"] for m in sp.mates(sp.normalise(p))] == ["fixed", "concentric", "coincident", "distance"]


@pytest.mark.parametrize("field, value, needle", [
    ("ops", ["Wiggle the mast"], "must start with one of"),
    ("params", {"shaft_d": [8]}, "`params` must map"),
    ("targets", {"bbox_mm": [8, 8]}, "bbox_mm"),
    ("targets", {"holes": 2.5}, "targets.holes"),
    ("targets", {"min_wall_mm": 0}, "min_wall_mm"),
    ("targets", "big", "`targets` must be an object"),
    ("ports", [{"type": "axis"}], "needs a `name`"),
    ("ports", [{"name": "a", "type": "blob"}], "type must be one of"),
    ("ports", [{"name": "a"}, {"name": "a"}], "named twice"),
    ("mates", [{"type": "fixed", "a": "Mast"}], "only assembly steps"),
])
def test_optional_fields_are_checked_when_present(field, value, needle):
    p = plan()
    p["steps"][1][field] = value
    problems = sp.validate(p)
    assert any(needle in x for x in problems), problems


@pytest.mark.parametrize("mates, needle", [
    ([{"type": "glue", "a": "Base", "b": "Mast"}], "must be one of"),
    ([{"type": "concentric", "a": "Base.bore", "b": "Mast.axis"}], "fix no part"),
    ([{"type": "fixed", "a": "Base"}, {"type": "fixed", "a": "Mast"}], "exactly one part is fixed"),
    ([{"type": "fixed", "a": "Base"}, {"type": "concentric", "a": "Base.bore"}], "has no `b`"),
    ([{"type": "fixed", "a": "Base"}, {"type": "distance", "a": "Base.top", "b": "Mast.end"}], "numeric `value`"),
    ([{"type": "fixed", "a": "Base"}, {"type": "coincident", "a": "Base.top", "b": "Base.side"}], "to itself"),
    ([{"type": "fixed", "a": "Base"}, {"type": "concentric", "a": "Base.x", "b": "Mast.x"},
      {"type": "revolute", "a": "Mast.y", "b": "Hub.y"}, {"type": "slider", "a": "Hub.z", "b": "Base.z"}],
     "closes a loop"),
])
def test_assembly_mates_ground_one_part_and_stay_a_tree(mates, needle):
    problems = sp.validate(assembly_plan(mates))
    assert any(needle in x for x in problems), problems


def test_two_mates_between_the_same_parts_are_one_joint_not_a_loop():
    ok = [{"type": "fixed", "a": "Base"}, {"type": "concentric", "a": "Base.bore", "b": "Mast.axis"},
          {"type": "coincident", "a": "Base.top", "b": "Mast.collar"}]
    assert sp.validate(assembly_plan(ok)) == []


def test_op_labels_use_the_plan_vocabulary():
    assert sp.op_label("create_extrusion") == "Extrude"
    assert sp.op_label("boolean", '{"kind": "cut"}') == "Cut"
    assert sp.op_label("boolean", {"kind": "fuse"}) == "Fuse"
    assert sp.op_label("add_standard_part") == "Place standard part"
    assert sp.op_label("set_props", {"props": {"ShapeColor": [1, 0, 0]}}) == "Set color"
    assert sp.op_label("set_placement") == "Mate"
    assert sp.op_label("run_part_script") == "Run part script"
