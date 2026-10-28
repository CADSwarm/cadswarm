"""The model state after every step, from the op log alone. Nothing is invented."""
from cadswarm.training import state


def step(op, args, created=(), modified=(), deleted=(), recomputed=(), status="done", volume=None):
    payload = {"volume": volume} if volume is not None else {}
    return {"op": op, "args": args, "status": status,
            "result": {"result": payload, "created": list(created), "modified": list(modified),
                       "deleted": list(deleted), "recomputed": list(recomputed), "broken": []}}


def test_created_objects_are_typed_from_the_op():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "Base"}, created=["Base"]))
    assert s["objects"]["Base"]["type"] == "Part::Box"
    state.apply(s, step("create_extrusion", {"name": "Hood"}, created=["Hood"]))
    assert s["objects"]["Hood"]["type"] == "Part::Feature"


def test_a_boolean_consumes_its_inputs():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("create_primitive", {"kind": "box", "name": "B"}, created=["B"]))
    state.apply(s, step("boolean", {"kind": "cut", "name": "C"}, created=["C"], deleted=["A", "B"]))
    assert sorted(s["objects"]) == ["C"] and s["objects"]["C"]["type"] == "Part::Cut"


def test_a_failed_op_changes_nothing():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("create_primitive", {"kind": "box", "name": "B"}, created=["B"], status="failed"))
    assert sorted(s["objects"]) == ["A"]


def test_replay_returns_the_state_before_each_step_and_flags_unknown_objects():
    steps = [step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]),
             step("set_props", {"name": "Ghost"}, modified=["Ghost"])]
    before, final, degraded = state.replay(steps)
    assert before[1]["objects"]["A"]["type"] == "Part::Box"
    assert "Ghost" not in final["objects"]
    assert degraded == [1]


def test_set_param_tracks_the_alias_and_its_number():
    s = state.empty()
    state.apply(s, step("set_param", {"alias": "wall", "value": "2.4 mm"}))
    assert s["params"]["wall"] == {"value": "2.4 mm", "num": 2.4}


def test_summary_is_bounded_and_sorted():
    s = state.empty()
    for i in range(130):
        state.apply(s, step("create_primitive", {"kind": "box", "name": f"o{i:03d}"}, created=[f"o{i:03d}"]))
    got = state.summary(s, max_objects=120)
    assert len(got["objects"]) == 120 and got["objects_total"] == 130
    assert [o["name"] for o in got["objects"]] == sorted(o["name"] for o in got["objects"])


def test_conformance_reports_missing_extra_and_type_mismatch():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("create_primitive", {"kind": "box", "name": "B"}, created=["B"]))
    report = state.conformance(s, [{"name": "A", "type_id": "Part::Box", "volume": 0.0},
                                   {"name": "C", "type_id": "Part::Cylinder", "volume": 0.0}], [])
    assert report["missing"] == ["C"] and report["extra"] == ["B"] and report["fraction"] == 0.3333
