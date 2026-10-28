"""A task's ops become one episode; unrelated ops fall into a freestyle stretch."""
from cadswarm.training import episodes, split, state

T0 = 1_800_000_000_000


def op(ident, actor, at, created=(), modified=(), deleted=(), reason="create_primitive", task=""):
    import json
    return {"id": ident, "by": actor, "kind": reason, "created_ms": T0 + at, "status": "done",
            "error_code": "",
            "args_json": json.dumps({"name": (created or modified or deleted or ["?"])[0], "kind": "box"}),
            "result_json": json.dumps({"result": {}, "created": list(created), "modified": list(modified),
                                       "deleted": list(deleted), "recomputed": [], "broken": [], "seq": 1}),
            **({"task": task} if task else {})}


def raw():
    return {
        "member": [{"id": 1, "name": "alice", "kind": "human"},
                   {"id": 2, "name": "lucky", "kind": "agent"}],
        "task": [{"id": "t1", "title": "Body", "detail": "", "objects": ["Body", "Wheels"],
                  "status": "done", "brief": "b1", "created_ms": T0, "updated_ms": T0 + 60_000}],
        "brief": [{"id": "b1", "text": "build a car", "board": "", "status": "ready",
                   "plan_json": "{}", "stl_hash": "abc", "created_ms": T0, "updated_ms": T0 + 60_000}],
        "review": [], "style_events": [],
        "op": [op("op_1", 2, 1_000, created=["Body"]),
               op("op_2", 2, 2_000, modified=["Body"]),
               op("op_3", 1, 30 * 60_000, created=["Spoiler"]),   # 30 min later: new freestyle stretch
               op("op_4", 2, 5_000, created=["Wheels"], task="t1")],
    }


def test_ops_are_attributed_by_object_and_time_then_by_explicit_task():
    got = {e["id"].split("/")[-1]: e for e in episodes.build_episodes(raw(), "P")}
    # op_4 carries an explicit task, and explicit evidence outranks the object window.
    assert got["t1"]["attribution"] == "task_linked"
    assert [s["op_id"] for s in got["t1"]["steps"]] == ["op_1", "op_2", "op_4"]


def test_an_idle_gap_starts_a_new_freestyle_episode():
    free = [e for e in episodes.build_episodes(raw(), "P") if e["attribution"] == "freestyle"]
    assert len(free) == 1 and [s["op_id"] for s in free[0]["steps"]] == ["op_3"]


def test_a_task_op_ends_the_freestyle_stretch():
    data = raw()
    data["op"] += [op("op_5", 1, 30 * 60_000 + 30_000, created=["Body"], task="t1"),
                   op("op_6", 1, 31 * 60_000, created=["Flap"]),
                   op("op_7", 1, 31 * 60_000 + 30_000, created=["Rudder"])]
    found = episodes.build_episodes(data, "P")
    free = [e for e in found if e["attribution"] == "freestyle"]
    assert sorted(len(e["steps"]) for e in free) == [1, 2]
    t1 = [e for e in found if e["id"].endswith("/t1")][0]
    assert [s["op_id"] for s in t1["steps"]] == ["op_1", "op_2", "op_4", "op_5"]


def test_legacy_ops_without_a_task_column_are_attributed_by_objects():
    data = raw()
    for op_row in data["op"]:
        op_row.pop("task", None)
    got = {e["id"].split("/")[-1]: e for e in episodes.build_episodes(data, "P")}
    assert got["t1"]["attribution"] == "object_window"
    assert [s["op_id"] for s in got["t1"]["steps"]] == ["op_1", "op_2", "op_4"]


def test_every_op_lands_in_exactly_one_episode():
    seen = [s["op_id"] for e in episodes.build_episodes(raw(), "P") for s in e["steps"]]
    assert sorted(seen) == sorted(o["id"] for o in raw()["op"])


def test_an_explicit_task_column_beats_object_matching():
    data = raw()
    data["op"][2]["task"] = "t1"
    got = {e["id"].split("/")[-1]: e for e in episodes.build_episodes(data, "P")}
    assert got["t1"]["attribution"] == "task_linked" and len(got["t1"]["steps"]) == 4


def test_steps_carry_the_state_before_them_and_a_label():
    episode = episodes.build_episodes(raw(), "P")[0]
    first = episode["steps"][0]
    assert episode["id"].endswith("/t1")
    assert first["state_before"] == state.empty()
    assert first["state_before"] == state.empty()
    assert episode["labels"]["outcome"] == "accepted"
    assert episode["labels"]["step_labels"]["0"] == "good"
    assert episode["final"]["stl_sha256"] == "abc"


def test_split_holds_out_a_whole_project_when_there_are_three():
    eps = [{"id": "a/1", "project": "A", "first_ms": 1}, {"id": "b/1", "project": "B", "first_ms": 2},
           {"id": "c/1", "project": "C", "first_ms": 3}]
    got = split.assign(eps)
    assert got["mode"] == "project" and got["test"] == ["c/1"] and got["val"] == ["b/1"]
    assert got["train"] == ["a/1"]


def test_split_falls_back_to_time_with_one_project():
    eps = [{"id": f"a/{i}", "project": "A", "first_ms": i} for i in range(10)]
    got = split.assign(eps)
    assert got["mode"] == "temporal" and not set(got["train"]) & set(got["test"])
    assert got["test"] == ["a/8", "a/9"]
