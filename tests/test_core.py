"""Hub coordination rules, with a fake FreeCAD worker and a controllable clock."""
import pytest

from cadswarm.config import ORPHAN_AFTER_SECONDS, STALE_AFTER_SECONDS
from cadswarm.hub.core import Hub, HubError
from cadswarm.hub.worker_client import WorkerError


class FakeWorker:
    """Mimics the real worker's contract: created/modified lists and `allowed` enforcement."""

    doc_path = "fake.FCStd"

    def __init__(self):
        self.objects = {"Params": {}}
        self.users = {}  # object -> objects whose expressions use it

    def call(self, op, args=None, allowed=None):
        args = args or {}
        if op == "status":
            return {"result": {"objects": [{"name": n} for n in self.objects], "params": {}}}
        if op in ("brep", "interference", "ping"):
            return {"result": {"objects": {}, "clashes": [], "freecad": ["1", "1", "4"]}}
        created, modified = [], []
        if op in ("create_primitive", "create_extrusion"):
            if args["name"] in self.objects:
                raise WorkerError("exists", "exists")
            created = [args["name"]]
        elif op == "set_param":
            modified = ["Params"]
        elif op == "run_part_script" and args.get("touch"):
            modified = list(args["touch"])
        else:
            modified = [args["name"]]
        if allowed is not None and set(modified) - set(allowed):
            raise WorkerError("lock_violation", f"would change {sorted(set(modified) - set(allowed))}")
        for n in created:
            self.objects[n] = {}
        recomputed = sorted({u for m in modified for u in self.users.get(m, [])})
        return {"ok": True, "result": {"name": args.get("name")}, "created": created, "modified": modified,
                "deleted": [], "recomputed": recomputed, "broken": []}

    def stop(self):
        pass


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    hub = Hub(FakeWorker(), str(tmp_path / "hub.sqlite"), "secret", clock=clock)
    alice = hub.join("secret", "alice-claude", client="claude", role="lead")
    bob = hub.join("secret", "bob-codex", client="codex")
    return hub, clock, alice["participant_id"], bob["participant_id"]


def box(hub, pid, name):
    return hub.op(pid, "create_primitive", {"kind": "box", "name": name})


def test_join_requires_workspace_token(tmp_path):
    hub = Hub(FakeWorker(), str(tmp_path / "h.sqlite"), "secret")
    with pytest.raises(HubError) as e:
        hub.join("wrong", "eve")
    assert e.value.code == "bad_workspace_token"


def test_creator_owns_new_objects_and_others_cannot_edit(env):
    hub, _, alice, bob = env
    r = box(hub, alice, "Plate")
    assert r["created"] == ["Plate"] and r["revs"] == {"Plate": 1}
    assert hub.holder("Plate") == alice
    with pytest.raises(HubError) as e:
        hub.op(bob, "set_props", {"name": "Plate", "props": {"Length": 5}})
    assert e.value.code == "not_locked" and e.value.data["heldBy"]["Plate"]["name"] == "alice-claude"
    with pytest.raises(HubError) as e:
        hub.acquire(bob, ["Plate"])
    assert e.value.status == 409 and "alice-claude" in str(e.value)


def test_worker_rollback_on_lock_violation_surfaces_as_409(env):
    hub, _, alice, bob = env
    box(hub, alice, "Plate")
    box(hub, bob, "Mount")
    with pytest.raises(HubError) as e:
        hub.op(bob, "run_part_script", {"code": "...", "touch": ["Plate"]})
    assert e.value.status == 409 and e.value.code == "lock_violation"


def test_reserved_name_cannot_be_created_by_someone_else(env):
    hub, _, alice, bob = env
    hub.acquire(alice, ["Lid"])
    with pytest.raises(HubError) as e:
        box(hub, bob, "Lid")
    assert e.value.code == "locked"
    assert box(hub, alice, "Lid")["created"] == ["Lid"]


def test_expected_rev_detects_concurrent_change(env):
    hub, _, alice, _ = env
    box(hub, alice, "Plate")
    hub.op(alice, "set_props", {"name": "Plate", "props": {"Length": 2}}, expected_revs={"Plate": 1})
    with pytest.raises(HubError) as e:
        hub.op(alice, "set_props", {"name": "Plate", "props": {"Length": 3}}, expected_revs={"Plate": 1})
    assert e.value.code == "stale_rev" and e.value.data["currentRev"] == 2


def test_param_change_notifies_owners_of_dependents(env):
    hub, _, alice, bob = env
    box(hub, bob, "Mount")
    hub.worker.users["Params"] = ["Mount"]
    hub.acquire(alice, ["Params"])
    r = hub.op(alice, "set_param", {"alias": "pitch", "value": "40 mm"})
    assert r["recomputed"] == ["Mount"] and r["notified"] == {"bob-codex": ["Mount"]}
    inbox = hub.inbox(bob)
    assert any("recomputed your objects ['Mount']" in m["body"] for m in inbox)
    assert hub.one("SELECT rev FROM objects WHERE name='Mount'")["rev"] == 2


def test_fence_supersedes_older_process(env):
    hub, _, alice, _ = env
    row = hub.one("SELECT token, fence FROM participants WHERE id=?", alice)
    again = hub.join("secret", "alice-claude", resume={"id": alice, "token": row["token"]})
    assert again["participant_id"] == alice and again["fence"] == row["fence"] + 1
    with pytest.raises(HubError) as e:
        hub.authenticate(alice, row["token"], row["fence"])
    assert e.value.code == "fence_stale"
    hub.authenticate(alice, row["token"], again["fence"])


def test_stale_then_takeover_moves_locks_and_tasks(env):
    hub, clock, alice, bob = env
    box(hub, bob, "Mount")
    t = hub.create_task(alice, "motor mount", assignee="bob-codex")
    hub.claim_task(bob, t["id"])
    with pytest.raises(HubError) as e:
        hub.take_over(alice, "bob-codex")  # bob is alive: compare-and-set refuses
    assert e.value.code == "not_available"
    clock.t += STALE_AFTER_SECONDS + 1
    hub.heartbeat(alice)
    hub.sweep()
    assert hub.task(t["id"])["status"] == "awaiting_takeover"
    assert hub.holder("Mount") == bob  # a stale participant's half-built part stays protected
    r = hub.take_over(alice, "bob-codex")
    assert r["locks"] == ["Mount"] and hub.holder("Mount") == alice
    assert hub.task(t["id"])["owner"] == alice and hub.task(t["id"])["status"] == "claimed"
    token = hub.one("SELECT token FROM participants WHERE id=?", bob)["token"]
    with pytest.raises(HubError) as e:
        hub.authenticate(bob, token, None)
    assert e.value.code == "taken_over"


def test_names_unique_and_rejoin_under_stale_name_inherits_work(env):
    hub, clock, alice, bob = env
    box(hub, bob, "Mount")
    with pytest.raises(HubError) as e:
        hub.join("secret", "BOB-codex")  # bob is live: refused, case-insensitively
    assert e.value.code == "name_in_use"
    clock.t += STALE_AFTER_SECONDS + 1
    hub.heartbeat(alice)
    hub.sweep()
    new_bob = hub.join("secret", "bob-codex")["participant_id"]  # lost identity file, came back
    assert new_bob != bob and hub.holder("Mount") == new_bob


def test_stale_participant_resumes_on_heartbeat(env):
    hub, clock, alice, bob = env
    t = hub.create_task(alice, "lid")
    hub.claim_task(bob, t["id"])
    clock.t += STALE_AFTER_SECONDS + 1
    hub.heartbeat(alice)
    hub.sweep()
    hub.heartbeat(bob)
    assert hub.task(t["id"])["status"] == "claimed"
    assert hub.one("SELECT status FROM participants WHERE id=?", bob)["status"] == "active"


def test_orphaned_work_is_freed_eventually(env):
    hub, clock, alice, bob = env
    box(hub, bob, "Mount")
    clock.t += STALE_AFTER_SECONDS + 1
    hub.sweep()
    clock.t += ORPHAN_AFTER_SECONDS + 1
    hub.sweep()
    assert hub.holder("Mount") is None


def test_task_claim_and_report_rules(env):
    hub, _, alice, bob = env
    t = hub.create_task(alice, "base plate")
    with pytest.raises(HubError):
        hub.claim_task(bob, t["id"], expected_rev=99)
    hub.claim_task(bob, t["id"], expected_rev=1)
    with pytest.raises(HubError) as e:
        hub.claim_task(alice, t["id"])
    assert e.value.code == "not_claimable"
    with pytest.raises(HubError) as e:
        hub.report_task(alice, t["id"], "done", "x")
    assert e.value.code == "not_owner"
    done = hub.report_task(bob, t["id"], "done", "plate built", ["Plate vol 30000"])
    assert done["status"] == "done"
    assert any("reported task" in m["body"] for m in hub.inbox(alice))


def test_inbox_human_steering_first_and_ack_requires_delivery(env):
    hub, _, alice, bob = env
    human = hub.join("secret", "carol", kind="human", client="freecad")["participant_id"]
    hub.send(alice, "bob-codex", "please widen the slot")
    hub.send(human, "bob-codex", "stop, use M4 bolts")
    msgs = hub.inbox(bob)
    assert msgs[0]["kind"] == "steer" and msgs[0]["from"] == "carol"
    other = hub.send(alice, "carol", "for carol only")["id"]
    with pytest.raises(HubError):
        hub.ack(bob, [other])
    hub.ack(bob, [m["id"] for m in msgs])
    assert hub.inbox(bob) == []


def test_chat_is_not_a_command_and_comes_last(env):
    hub, _, alice, bob = env
    human = hub.join("secret", "carol", kind="human", client="freecad")["participant_id"]
    dave = hub.join("secret", "dave", kind="human", client="freecad")["participant_id"]
    hub.send(human, "all", "lunch at 1?", kind="chat")
    hub.send(alice, "bob-codex", "please widen the slot")
    hub.send(human, "bob-codex", "use M4 bolts", kind="command")
    kinds = [m["kind"] for m in hub.inbox(bob)]
    assert kinds == ["steer", "message", "chat"]
    from_helper = hub.send(alice, "bob-codex", "do it now", kind="command")["id"]
    assert {m["id"]: m["kind"] for m in hub.inbox(bob)}[from_helper] == "message"  # helpers can't give orders
    hub.send(human, "dave", "people can't be ordered around", kind="command")
    assert hub.inbox(dave)[-1]["kind"] == "chat"  # a person's "command" to a person is just chat
    with pytest.raises(HubError):
        hub.send(human, "all", "x", kind="shout")
    assert "[COMMAND] carol: use M4 bolts" in hub.context(bob) and "[chat] carol" in hub.context(bob)


def test_commands_show_sent_seen_done(env):
    hub, _, alice, bob = env
    human = hub.join("secret", "carol", kind="human", client="freecad")["participant_id"]
    one = hub.send(human, "bob-codex", "make the lid 20 mm", kind="command")["id"]
    hub.send(human, "all", "check for clashes", kind="command")
    hub.send(human, "all", "hi team", kind="chat")
    cmds = {c["body"]: c for c in hub.commands(human)}
    assert set(cmds) == {"make the lid 20 mm", "check for clashes"}  # chat isn't an instruction
    assert cmds["make the lid 20 mm"]["state"] == "sent" and cmds["make the lid 20 mm"]["to"] == "bob-codex"
    hub.inbox(bob)
    c = {c["body"]: c for c in hub.commands(human)}
    assert c["make the lid 20 mm"]["state"] == "seen"
    assert c["check for clashes"]["state"] == "sent"  # alice hasn't read it yet
    assert c["check for clashes"]["by_helper"] == {"alice-claude": "sent", "bob-codex": "seen"}
    hub.ack(bob, [one])
    assert {c["body"]: c for c in hub.commands(human)}["make the lid 20 mm"]["state"] == "done"


def test_participants_say_what_each_helper_is_working_on(env):
    hub, clock, alice, bob = env
    t = hub.create_task(alice, "Base plate")
    team = {p["name"]: p for p in hub.participants()}
    assert team["bob-codex"]["task"] is None and team["bob-codex"]["idle_s"] is None
    hub.claim_task(bob, t["id"])
    clock.t += 5
    box(hub, bob, "Plate")
    clock.t += 12
    team = {p["name"]: p for p in hub.participants()}
    assert team["bob-codex"]["task"] == {"id": t["id"], "title": "Base plate"}
    assert team["bob-codex"]["idle_s"] == 12.0
    hub.report_task(bob, t["id"], "done", "built")
    assert {p["name"]: p for p in hub.participants()}["bob-codex"]["task"] is None


def test_human_can_force_release_and_force_takeover(env):
    hub, _, alice, bob = env
    human = hub.join("secret", "carol", kind="human")["participant_id"]
    box(hub, bob, "Mount")
    assert hub.release(alice, ["Mount"], force=True)["released"] == []  # agents cannot force
    hub.take_over(human, "bob-codex", force=True)
    assert hub.holder("Mount") == human


def test_context_block_mentions_team_locks_and_unread(env):
    hub, _, alice, bob = env
    box(hub, bob, "Mount")
    hub.send(bob, "alice-claude", "mount is at x=40")
    text = hub.context(alice)
    assert "bob-codex" in text and "Mount" in text and "mount is at x=40" in text
    assert "created ['Mount']" in text
    assert "created ['Mount']" not in hub.context(alice)  # cursor advanced


def test_events_since_cursor(env):
    hub, _, alice, _ = env
    seq = hub.seq()
    box(hub, alice, "Plate")
    ev = hub.events(seq)
    assert [e["kind"] for e in ev["events"]] == ["op"] and ev["seq"] > seq


def test_assign_and_message_before_teammate_joins(env):
    hub, _, alice, _ = env
    t = hub.create_task(alice, "lid", assignee="dana-codex")
    assert t["assignee"] == "dana-codex" and "has joined yet" in t["note"]
    assert "waits for them" in hub.send(alice, "dana-codex", "lid must clear the posts")["note"]
    dana = hub.join("secret", "Dana-Codex")["participant_id"]
    bodies = [m["body"] for m in hub.inbox(dana)]
    assert any("assigned you task" in b for b in bodies) and "lid must clear the posts" in bodies


# ------------------------------------------------------------ hardening


def test_fence_is_required_and_must_be_an_integer(env):
    hub, _, alice, _ = env
    row = hub.one("SELECT token, fence FROM participants WHERE id=?", alice)
    with pytest.raises(HubError) as e:
        hub.authenticate(alice, row["token"], None)
    assert e.value.status == 400 and e.value.code == "missing_fence"
    with pytest.raises(HubError) as e:
        hub.authenticate(alice, row["token"], "abc")
    assert e.value.status == 400 and e.value.code == "bad_fence"
    assert hub.authenticate(alice, row["token"], str(row["fence"]))["id"] == alice


def http_hub(tmp_path):
    from fastapi.testclient import TestClient
    from cadswarm.hub.app import create_app
    worker = FakeWorker()
    worker.doc_path = str(tmp_path / "model.FCStd")
    (tmp_path / "model.FCStd").write_bytes(b"model-bytes")
    hub = Hub(worker, str(tmp_path / "hub.sqlite"), "secret")
    return hub, TestClient(create_app(hub, str(tmp_path), sweep_seconds=3600))


def test_http_refuses_routes_without_a_fence_and_bad_input_is_400(tmp_path):
    hub, http = http_hub(tmp_path)
    ident = http.post("/join", json={"workspace_token": "secret", "name": "alice"}).json()
    h = {"x-participant": ident["participant_id"], "authorization": f"Bearer {ident['token']}"}
    assert http.get("/model", headers=h).json()["code"] == "missing_fence"
    assert http.get("/model", headers={**h, "x-fence": "abc"}).status_code == 400
    assert http.get("/model", headers={**h, "x-fence": "99"}).status_code == 409
    h["x-fence"] = str(ident["fence"])
    assert http.get("/model", headers=h).status_code == 200
    for path, body in [("/messages", {"body": 5}), ("/ops", {"op": "set_param", "args": "x"}),
                       ("/ops", {"op": ["a"]}), ("/locks", {"names": [["a"]]}),
                       ("/locks/release", {"names": "a"}),
                       ("/ops", {"op": "set_param", "args": {}, "expected_revs": {"Params": "x"}})]:
        r = http.post(path, json=body, headers=h)
        assert r.status_code == 400, (path, body, r.text)


def test_snapshot_download_is_served_under_the_hub_lock(tmp_path):
    hub, http = http_hub(tmp_path)
    ident = http.post("/join", json={"workspace_token": "secret", "name": "alice"}).json()
    h = {"x-participant": ident["participant_id"], "authorization": f"Bearer {ident['token']}",
         "x-fence": str(ident["fence"])}
    import cadswarm.hub.app as app_mod
    held = []

    def spy_open(*a, **k):
        held.append(hub.lock._is_owned())
        return open(*a, **k)
    app_mod.open = spy_open
    try:
        assert http.get("/snapshot.FCStd", headers=h).content == b"model-bytes"
    finally:
        del app_mod.open
    assert held == [True]  # the live file is read while the hub lock keeps the worker from replacing it
    (tmp_path / "model.FCStd").replace(tmp_path / "other")  # nothing holds the live file open


def test_events_does_not_sleep_through_an_event_emitted_before_waiting(env):
    import time
    hub, _, alice, _ = env
    since = hub.seq()
    real_q, fired = hub.q, []

    def q_then_emit(*a, **k):
        rows = real_q(*a, **k)
        if not fired:  # an event lands in the gap between the query and the wait
            fired.append(1)
            hub.emit("ping", alice)
        return rows
    hub.q = q_then_emit
    t0 = time.time()
    r = hub.events(since, wait=10)
    assert time.time() - t0 < 2 and [e["kind"] for e in r["events"]] == ["ping"]


def test_rejoin_after_leaving_cannot_duplicate_a_live_name(env):
    hub, _, alice, _ = env
    old = hub.one("SELECT token FROM participants WHERE id=?", alice)
    hub.leave(alice)
    hub.join("secret", "alice-claude")  # someone else takes the freed name
    with pytest.raises(HubError) as e:
        hub.join("secret", "alice-claude", resume={"id": alice, "token": old["token"]})
    assert e.value.code == "name_in_use"
    assert len(hub.q("SELECT 1 FROM participants WHERE lower(name)='alice-claude' AND status='active'")) == 1


def test_rejoin_after_leaving_with_name_free_resumes(env):
    hub, _, alice, _ = env
    old = hub.one("SELECT token FROM participants WHERE id=?", alice)
    hub.leave(alice)
    again = hub.join("secret", "alice-claude", resume={"id": alice, "token": old["token"]})
    assert again["participant_id"] == alice


def test_restore_file_honours_expected_version(tmp_path):
    hub, _http = http_hub(tmp_path)
    pid = hub.join("secret", "alice")["participant_id"]
    hub.save_file(pid, "notes.txt", "one")
    hub.save_file(pid, "notes.txt", "two", if_exists="replace", expected_version=1)
    with pytest.raises(HubError) as e:
        hub.restore_file(pid, "notes.txt", 1, expected_version=1)  # current is v2
    assert e.value.status == 409 and e.value.code == "stale_version"
    assert hub.restore_file(pid, "notes.txt", 1, expected_version=2)["version"] == 3
