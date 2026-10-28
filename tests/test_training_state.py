"""The model state after every step, from the op log alone. Nothing is invented."""
from cadswarm.training import state


def step(op, args, created=(), modified=(), deleted=(), recomputed=(), status="done",
         volume=None, name=None):
    payload = {"volume": volume, "name": name} if volume is not None else {}
    return {"op": op, "args": args, "status": status,
            "result": {"result": payload, "created": list(created), "modified": list(modified),
                       "deleted": list(deleted), "recomputed": list(recomputed), "broken": []}}


def test_the_params_sheet_exists_before_the_first_op():
    assert state.empty()["objects"]["Params"]["type"] == "Spreadsheet::Sheet"


def test_set_param_is_not_degraded():
    s = state.empty()
    unknown = state.apply(s, step("set_param", {"alias": "wall", "value": "2.4 mm"},
                                  modified=["Params"]))
    assert unknown == [] and s["objects"]["Params"]["rev"] == 2
    assert s["params"]["wall"] == {"value": "2.4 mm", "num": 2.4}


def test_created_objects_are_typed_from_the_op():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "Base"}, created=["Base"]))
    assert s["objects"]["Base"]["type"] == "Part::Box"
    state.apply(s, step("create_extrusion", {"name": "Hood"}, created=["Hood"]))
    assert s["objects"]["Hood"]["type"] == "Part::Feature"


def test_a_capitalised_kind_is_still_recognised():
    s = state.empty()
    state.apply(s, step("boolean", {"kind": "Cut", "name": "C"}, created=["C"]))
    assert s["objects"]["C"]["type"] == "Part::Cut"


def test_insert_mesh_is_a_mesh_feature():
    s = state.empty()
    state.apply(s, step("insert_mesh", {"name": "Lattice"}, created=["Lattice"]))
    assert s["objects"]["Lattice"]["type"] == "Mesh::Feature"


def test_an_unknown_op_gets_no_invented_type():
    s = state.empty()
    state.apply(s, step("teleport_part", {"name": "X"}, created=["X"]))
    assert s["objects"]["X"]["type"] is None and s["objects"]["X"]["known"] is False


def test_a_boolean_removes_the_inputs_it_reports_deleted():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("create_primitive", {"kind": "box", "name": "B"}, created=["B"]))
    state.apply(s, step("boolean", {"kind": "cut", "name": "C"}, created=["C"], deleted=["A", "B"]))
    assert sorted(s["objects"]) == ["C", "Params"]
    assert s["objects"]["C"]["type"] == "Part::Cut"


def test_a_failed_op_changes_nothing():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("create_primitive", {"kind": "box", "name": "B"}, created=["B"], status="failed"))
    assert "B" not in s["objects"]


def test_a_reported_volume_goes_only_to_the_object_it_names():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("create_primitive", {"kind": "box", "name": "C"}, created=["C"]))
    state.apply(s, step("run_part_script", {"name": "A"}, modified=["A"], recomputed=["C"],
                        volume=12.5, name="A"))
    assert s["objects"]["A"]["volume"] == 12.5 and s["objects"]["A"]["known"] is True
    assert s["objects"]["C"]["volume"] is None   # nothing was reported for C


def test_a_new_volume_replaces_the_stale_one():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("run_part_script", {"name": "A"}, modified=["A"], volume=7.5, name="A"))
    state.apply(s, step("run_part_script", {"name": "A"}, modified=["A"], volume=12.5, name="A"))
    assert s["objects"]["A"]["volume"] == 12.5


def test_a_swept_result_marks_the_step_degraded():
    swept = step("create_primitive", {"kind": "box", "name": "A"}, created=["A"])
    swept["result"] = {"blob": "abc"}
    _, _, degraded = state.replay([swept])
    assert degraded == [0]


def test_replay_returns_the_state_before_each_step_and_flags_unknown_objects():
    steps = [step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]),
             step("set_props", {"name": "Ghost"}, modified=["Ghost"])]
    before, final, degraded = state.replay(steps)
    assert before[1]["objects"]["A"]["type"] == "Part::Box"
    assert "Ghost" not in final["objects"]
    assert degraded == [1]


def test_summary_is_bounded_and_sorted():
    s = state.empty()
    for i in range(130):
        state.apply(s, step("create_primitive", {"kind": "box", "name": f"o{i:03d}"},
                            created=[f"o{i:03d}"]))
    got = state.summary(s, max_objects=120)
    assert len(got["objects"]) == 120 and got["objects_total"] == 131
    assert [o["name"] for o in got["objects"]] == sorted(o["name"] for o in got["objects"])


def test_conformance_reports_missing_extra_and_type_mismatch():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("create_primitive", {"kind": "box", "name": "B"}, created=["B"]))
    report = state.conformance(s, [{"name": "Params", "type_id": "Spreadsheet::Sheet", "volume": 0.0},
                                   {"name": "A", "type_id": "Part::Box", "volume": 0.0},
                                   {"name": "C", "type_id": "Part::Cylinder", "volume": 0.0}], [])
    assert report["missing"] == ["C"] and report["extra"] == ["B"]
    assert report["fraction"] == 0.5


def test_conformance_does_not_convict_a_type_the_replay_lacks():
    s = state.empty()
    state.apply(s, step("teleport_part", {"name": "X"}, created=["X"]))
    report = state.conformance(s, [{"name": "Params", "type_id": "Spreadsheet::Sheet", "volume": 0.0},
                                   {"name": "X", "type_id": "Part::Box", "volume": 0.0}], [])
    assert report["type_mismatch"] == [] and report["unknown_type"] == ["X"]


def test_conformance_flags_a_volume_outside_one_percent():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("run_part_script", {"name": "A"}, modified=["A"], volume=100.0, name="A"))
    report = state.conformance(s, [{"name": "Params", "type_id": "Spreadsheet::Sheet", "volume": 0.0},
                                   {"name": "A", "type_id": "Part::Feature", "volume": 120.0}], [])
    assert report["volume_mismatch"] == ["A"]
