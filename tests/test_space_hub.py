"""SpaceHub turns view rows and reducer calls back into the old hub's JSON shapes."""
import json

import pytest

from cadswarm.client import HubRequestError
from cadswarm.space import SpaceError
from cadswarm.space_hub import SpaceHub


class FakeSpace:
    """In-memory stand-in for SpaceClient: tables by view name, a log of reducer calls."""

    def __init__(self, tables):
        self.tables = {k: list(v) for k, v in tables.items()}
        self.calls = []
        self.fail = {}
        self.on_sql = None
        self.timeout = 30

    def call(self, reducer, *args):
        self.calls.append((reducer, args))
        if reducer in self.fail:
            raise SpaceError(530, self.fail[reducer])
        if reducer == "submit_op":
            self.tables.setdefault("v_op", []).append(
                {"id": args[1], "kind": args[2], "status": "queued", "result_json": "", "error_code": "", "error": ""})

    def sql(self, query):
        if self.on_sql:
            self.on_sql(query)
        view = query.split("FROM")[1].split()[0]
        rows = self.tables.get(view, [])
        if "WHERE seq >" in query:
            since = int(query.rsplit(">", 1)[1])
            rows = [r for r in rows if r["seq"] > since]
        if "WHERE id =" in query:
            want = query.rsplit("=", 1)[1].strip().strip("'")
            rows = [r for r in rows if r["id"] == want]
        if "WHERE hash =" in query:
            want = query.rsplit("=", 1)[1].strip().strip("'")
            rows = [r for r in rows if r["hash"] == want]
        return [dict(r) for r in rows]


def member(id, name, kind="agent", status="active", **kw):
    return {"id": id, "name": name, "kind": kind, "client": "claude", "role": "member", "status": status,
            "status_text": "", "fence": 1, "last_seen_ms": 0, "context_seq": 0, **kw}


@pytest.fixture
def world():
    tables = {
        "v_me": [member(1, "alice-claude", fence=2)],
        "v_member": [member(1, "alice-claude", fence=2), member(2, "bob-codex"), member(3, "carol", kind="human")],
        "v_lock": [{"object": "Plate", "holder": 1}, {"object": "Mount", "holder": 2}],
        "v_object": [{"name": "Plate", "label": "Plate", "type_id": "Part::Box", "rev": 2, "bbox": [0, 0, 0, 1, 1, 1],
                      "volume": 1.0, "visible": True, "broken": False, "uses": [], "used_by": [],
                      "expressions_json": "{}", "placement_json": ""}],
        "v_param": [{"alias": "pitch", "value": "40.0", "num": 40.0}],
        "v_task": [{"id": "t_1", "title": "lid", "detail": "", "objects": [], "status": "open", "owner": 0,
                    "created_by": 1, "rev": 1, "result_json": "", "assignee": "", "brief": "", "created_ms": 1, "updated_ms": 1}],
        "v_message": [
            {"id": "m_a", "from": 2, "to": "member:1", "kind": "message", "body": "widen the slot", "task_id": "", "created_ms": 1},
            {"id": "m_b", "from": 3, "to": "member:1", "kind": "steer", "body": "use M4 bolts", "task_id": "", "created_ms": 2},
            {"id": "m_c", "from": 1, "to": "all", "kind": "message", "body": "mine", "task_id": "", "created_ms": 3},
            {"id": "m_d", "from": 2, "to": "member:3", "kind": "message", "body": "for carol", "task_id": "", "created_ms": 4},
        ],
        "v_receipt": [],
        "v_event": [{"seq": 5, "kind": "op", "actor": 2, "at_ms": 1000,
                     "data_json": json.dumps({"name": "bob-codex", "op": "create_primitive", "created": ["Mount"],
                                              "modified": [], "deleted": [], "recomputed": [], "broken": []})}],
    }
    fake = FakeSpace(tables)
    hub = SpaceHub({"uri": "http://x", "db": "d", "name": "alice-claude"},
                   {"participant_id": 1, "token": "t", "fence": 2}, space=fake)
    return hub, fake


def test_model_has_the_old_shape_with_lock_holders_by_name(world):
    hub, _ = world
    m = hub.get("/model")
    plate = m["objects"][0]
    assert plate["locked_by"] == "alice-claude" and plate["rev"] == 2 and plate["type"] == "Part::Box"
    assert m["params"] == {"pitch": 40.0}
    assert {p["name"] for p in m["participants"]} == {"alice-claude", "bob-codex", "carol"}
    assert m["seq"] == 5


def test_reducers_carry_the_fence_and_errors_keep_their_code(world):
    hub, fake = world
    hub.post("/locks", {"names": ["Lid"]})
    assert fake.calls[-1] == ("lock_objects", (2, ["Lid"]))
    fake.fail["lock_objects"] = "locked: Already locked: Mount by bob-codex (active)."
    with pytest.raises(HubRequestError) as e:
        hub.post("/locks", {"names": ["Mount"]})
    assert e.value.status == 409 and e.value.code == "locked" and "bob-codex" in str(e.value)


def test_an_op_is_queued_then_awaited_until_the_host_finishes_it(world):
    hub, fake = world

    def host_finishes(query):
        for o in fake.tables.get("v_op", []):
            if o["status"] == "queued":
                o["status"] = "done"
                o["result_json"] = json.dumps({"created": ["Lid"], "revs": {"Lid": 1}})

    fake.on_sql = host_finishes
    r = hub.post("/ops", {"op": "create_primitive", "args": {"kind": "box", "name": "Lid"}})
    assert r["created"] == ["Lid"]
    reducer, args = fake.calls[-1]
    assert reducer == "submit_op" and args[0] == 2 and args[2] == "create_primitive"
    assert args[1].startswith("o_") and len(args[1]) == 14 and json.loads(args[3]) == {"kind": "box", "name": "Lid"}
    assert args[4] == ""


def test_a_failed_op_surfaces_its_code(world):
    hub, fake = world

    def host_rejects(query):
        for o in fake.tables.get("v_op", []):
            o.update(status="rejected", error_code="lock_violation", error="would change ['Plate']")

    fake.on_sql = host_rejects
    with pytest.raises(HubRequestError) as e:
        hub.post("/ops", {"op": "run_part_script", "args": {"code": "..."}})
    assert e.value.status == 409 and e.value.code == "lock_violation"


def test_inbox_puts_human_steering_first_skips_my_own_and_others_mail(world):
    hub, fake = world
    msgs = hub.get("/inbox")["messages"]
    assert [m["id"] for m in msgs] == ["m_b", "m_a"]
    assert msgs[0]["from"] == "carol" and msgs[0]["kind"] == "steer"
    assert ("mark_delivered", (2, ["m_b", "m_a"])) in fake.calls


def test_consumed_messages_leave_the_inbox(world):
    hub, fake = world
    fake.tables["v_receipt"] = [{"message_id": "m_b", "member": 1, "state": "consumed"}]
    assert [m["id"] for m in hub.get("/inbox")["messages"]] == ["m_a"]


def test_tasks_have_names_resolved(world):
    hub, _ = world
    t = hub.get("/tasks")["tasks"][0]
    assert t["id"] == "t_1" and t["created_by_name"] == "alice-claude" and t["owner_name"] is None


def test_events_since_a_cursor(world):
    hub, _ = world
    r = hub.get("/events", since=0, wait=0)
    assert r["seq"] == 5 and r["events"][0]["created"] == ["Mount"]
    assert hub.get("/events", since=5, wait=0) == {"seq": 5, "events": []}


def test_context_mentions_team_changes_and_unread_and_advances_the_cursor(world):
    hub, fake = world
    text = hub.get("/context")["context"]
    assert "bob-codex" in text and "created ['Mount']" in text and "use M4 bolts" in text
    assert ("set_context_seq", (2, 5)) in fake.calls


def test_join_rejoins_and_takes_the_new_fence(world):
    hub, fake = world
    fake.tables["v_me"] = [member(1, "alice-claude", fence=3)]
    ident = hub.join()
    assert fake.calls[0] == ("rejoin", ("",))
    assert ident["fence"] == 3 and hub.fence == 3 and ident["participant_id"] == 1


def test_an_unknown_path_is_a_404(world):
    hub, _ = world
    with pytest.raises(HubRequestError) as e:
        hub.get("/nope")
    assert e.value.status == 404
