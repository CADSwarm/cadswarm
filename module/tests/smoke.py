"""End-to-end check of the published module over SpacetimeDB's HTTP API (stdlib only).

Run against a local server after `scripts/publish-local.ps1`:
    python module/tests/smoke.py
The CLI's own identity published the module, so it is the `host` member. Its token comes from
`spacetime login show --token`, or CADSWARM_OWNER_TOKEN if set.
"""
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import urllib.error
import urllib.request

URI = os.environ.get("CADSWARM_URI", "http://127.0.0.1:3000")
DB = os.environ.get("CADSWARM_DB", "cadswarm")


def http(method, path, token=None, body=None, raw=None):
    data = raw.encode() if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(URI + path, data=data, method=method)
    req.add_header("content-type", "application/json" if raw is None else "text/plain")
    if token:
        req.add_header("authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text.strip() else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class Client:
    def __init__(self, token):
        self.token = token
        self.fence = None

    def call(self, reducer, *args, expect_error=None):
        status, body = http("POST", f"/v1/database/{DB}/call/{reducer}", self.token, list(args))
        if expect_error:
            assert status >= 400 and expect_error in str(body), f"{reducer}: wanted {expect_error}, got {status} {body}"
            return body
        assert status < 400, f"{reducer}{args!r} failed: {status} {body}"

    def sql(self, query):
        status, body = http("POST", f"/v1/database/{DB}/sql", self.token, raw=query)
        assert status < 400, f"sql {query!r} failed: {status} {body}"
        out = []
        for table in body:
            names = [el["name"]["some"] if isinstance(el["name"], dict) else el["name"]
                     for el in table["schema"]["elements"]]
            out += [dict(zip(names, row)) for row in table["rows"]]
        return out

    def me(self):
        rows = self.sql("SELECT * FROM v_me")
        assert rows, "v_me is empty"
        self.fence = rows[0]["fence"]
        return rows[0]


def owner_token():
    if os.environ.get("CADSWARM_OWNER_TOKEN"):
        return os.environ["CADSWARM_OWNER_TOKEN"]
    out = subprocess.run(["spacetime", "login", "show", "--token"], capture_output=True, text=True).stdout
    m = re.search(r"([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)", out)
    assert m, f"could not find a token in `spacetime login show --token` output:\n{out}"
    return m.group(1)


def new_identity():
    status, body = http("POST", "/v1/identity")
    assert status < 400, body
    return Client(body["token"])


def invite(host, kind="agent", role="member"):
    token = secrets.token_hex(32)
    host.call("create_invite", host.fence, hashlib.sha256(token.encode()).hexdigest(), kind, role, 3600)
    return token


def join(host, name, kind="agent"):
    c = new_identity()
    c.call("redeem_invite", invite(host, kind), name, "smoke", "")
    c.me()
    return c


def decode_bytes(v):
    """SATS JSON encodes Vec<u8> as a hex string (older builds: a list of ints)."""
    if isinstance(v, str):
        return bytes.fromhex(v[2:] if v.startswith("0x") else v)
    return bytes(v)


def main():
    host = Client(owner_token())
    me = host.me()
    assert me["kind"] == "host", me

    alice = join(host, "alice-claude")
    bob = join(host, "bob-codex")
    carol = join(host, "carol", kind="human")

    # A stranger sees nothing and can do nothing.
    eve = new_identity()
    assert eve.sql("SELECT * FROM v_member") == []
    eve.call("heartbeat", 1, "", expect_error="not_member")

    # Locks and the op queue.
    alice.call("lock_objects", alice.fence, ["Plate"])
    alice.call("submit_op", alice.fence, "o_smoke000001", "create_primitive",
               json.dumps({"kind": "box", "name": "Plate"}), "")
    bob.call("submit_op", bob.fence, "o_smoke000002", "set_props",
             json.dumps({"name": "Plate", "props": {"Length": 5}}), "", expect_error="not_locked")
    bob.call("lock_objects", bob.fence, ["Plate"], expect_error="locked")

    host.call("start_op", host.fence, "o_smoke000001")
    host.call("complete_op", host.fence, "o_smoke000001", True, json.dumps({"name": "Plate"}),
              ["Plate"], [], [], [], [], "", "")
    op = [o for o in alice.sql("SELECT * FROM v_op") if o["id"] == "o_smoke000001"][0]
    assert op["status"] == "done" and json.loads(op["result_json"])["revs"] == {"Plate": 1}, op
    plate = [o for o in alice.sql("SELECT * FROM v_object") if o["name"] == "Plate"][0]
    assert plate["rev"] == 1

    alice.call("submit_op", alice.fence, "o_smoke000003", "set_props",
               json.dumps({"name": "Plate", "props": {"Length": 2}}), json.dumps({"Plate": 2}),
               expect_error="stale_rev")

    # A pointer outlives the HTTP request that set it: every /call and /sql is a connect and a
    # disconnect, and a disconnect must not rest it. A tool call on the same part keeps the spot.
    alice_id = alice.me()["id"]
    alice.call("move_pointer", alice.fence, "Plate", [5.0, 5.0, 1.0], "editing", "checking the plate")
    p = [r for r in alice.sql("SELECT * FROM v_pointer") if r["member"] == alice_id][0]
    assert (p["object"], p["mode"], p["point"]) == ("Plate", "editing", [5.0, 5.0, 1.0]), p
    alice.call("log_activity", alice.fence, "tool_call", "measuring the plate", ["Plate"])
    p = [r for r in alice.sql("SELECT * FROM v_pointer") if r["member"] == alice_id][0]
    assert (p["mode"], p["point"]) == ("pointing", [5.0, 5.0, 1.0]), p

    if os.environ.get("CADSWARM_SMOKE_SLOW"):  # ~40 s: wait out STALE_AFTER_MS and a sweep
        import time
        dave = join(host, "dave-quiet")
        dave.call("create_task", dave.fence, "t_smoke_stale", "quiet work", "", [], "dave-quiet", "")
        dave.call("claim_task", dave.fence, "t_smoke_stale", 1)
        time.sleep(40)
        t = [x for x in host.sql("SELECT * FROM v_task") if x["id"] == "t_smoke_stale"][0]
        assert t["status"] == "awaiting_takeover", t
        dave.call("heartbeat", dave.fence, "back")  # an HTTP call: connect (revives) then heartbeat
        t = [x for x in host.sql("SELECT * FROM v_task") if x["id"] == "t_smoke_stale"][0]
        assert t["status"] == "claimed" and dave.me()["status"] == "active", t

    # Tasks and messages.
    alice.call("create_task", alice.fence, "t_smoke001", "motor mount", "", [], "bob-codex", "")
    bob.call("claim_task", bob.fence, "t_smoke001", 1)
    alice.call("claim_task", alice.fence, "t_smoke001", 0, expect_error="not_claimable")
    alice.call("report_task", alice.fence, "t_smoke001", "done", "x", [], expect_error="not_owner")
    bob.call("report_task", bob.fence, "t_smoke001", "done", "mount built", ["Mount vol 300"])
    carol.call("send_message", carol.fence, "m_smoke0000001", "bob-codex", "use M4 bolts", "message", "")
    msgs = [m for m in bob.sql("SELECT * FROM v_message") if m["id"] == "m_smoke0000001"]
    assert msgs and msgs[0]["kind"] == "steer", msgs
    bob.call("ack_messages", bob.fence, ["m_smoke0000001"], expect_error="not_delivered")
    bob.call("mark_delivered", bob.fence, ["m_smoke0000001"])
    bob.call("ack_messages", bob.fence, ["m_smoke0000001"])

    # Rebalancing: bob is busy with one task and has two queued; free alice picks them up and bob is told.
    for tid in ("t_smoke_busy", "t_smoke_q1", "t_smoke_q2"):
        alice.call("create_task", alice.fence, tid, tid, "", [], "bob-codex", "")
    bob.call("claim_task", bob.fence, "t_smoke_busy", 0)
    alice.call("create_task", alice.fence, "t_smoke_carol", "carol's", "", [], "carol", "")
    carol.call("pick_up_task", carol.fence, "", expect_error="not_allowed")
    bob.call("pick_up_task", bob.fence, "", expect_error="busy")
    alice.call("pick_up_task", alice.fence, "t_smoke_busy", expect_error="not_claimable")
    alice.call("pick_up_task", alice.fence, "")
    t = [x for x in alice.sql("SELECT * FROM v_task") if x["id"] == "t_smoke_q1"][0]
    assert (t["status"], t["owner"], t["assignee"]) == ("claimed", alice.me()["id"], "alice-claude"), t
    assert any(m["task_id"] == "t_smoke_q1" and m["kind"] == "notice" for m in bob.sql("SELECT * FROM v_message"))
    alice.call("pick_up_task", alice.fence, "", expect_error="busy")
    alice.call("report_task", alice.fence, "t_smoke_q1", "done", "q1 built", [])
    alice.call("pick_up_task", alice.fence, "t_smoke_carol", expect_error="not_spare")  # carol isn't busy
    alice.call("pick_up_task", alice.fence, "")
    t = [x for x in alice.sql("SELECT * FROM v_task") if x["id"] == "t_smoke_q2"][0]
    assert t["status"] == "claimed" and t["assignee"] == "alice-claude", t
    alice.call("report_task", alice.fence, "t_smoke_q2", "done", "q2 built", [])

    # A newer process with the same identity supersedes the old fence.
    old = alice.fence
    alice.call("rejoin", "smoke")
    alice.me()
    assert alice.fence == old + 1
    alice.call("heartbeat", old, "", expect_error="fence_stale")
    alice.call("heartbeat", alice.fence, "still here")

    # Carol hands her work to an AI; the agent picks it up where she left off.
    carol.call("lock_objects", carol.fence, ["Lid"])
    carol.call("move_pointer", carol.fence, "Lid", [1.0, 2.0, 3.0], "inspecting", "looking at Lid")
    carol.call("request_handoff", carol.fence, "h_smoke001", "make the lip 1 mm thinner", "now")
    bob.call("request_handoff", bob.fence, "h_smoke002", "x", "now", expect_error="not_allowed")
    h = [x for x in carol.sql("SELECT * FROM v_handoff") if x["id"] == "h_smoke001"][0]
    snap = json.loads(h["context_json"])
    assert h["status"] == "open" and snap["note"] == "make the lip 1 mm thinner"
    assert snap["locks"] == ["Lid"] and snap["pointer"]["object"] == "Lid", snap
    bob.call("accept_handoff", bob.fence, "h_smoke001")
    lid = [l for l in carol.sql("SELECT * FROM v_lock") if l["object"] == "Lid"][0]
    assert lid["holder"] == bob.me()["id"], lid
    assert carol.me()["status"] == "active", "a deliberate handoff keeps the person's seat"
    bob.call("accept_handoff", bob.fence, "h_smoke001", expect_error="not_available")

    # A reviewer cannot change geometry.
    rev = join(host, "reviewer", kind="reviewer")
    rev.call("submit_op", rev.fence, "o_smoke000004", "delete", json.dumps({"name": "Plate"}), "",
             expect_error="not_allowed")

    # Export bytes live in artifact_blob, never in v_artifact; WHERE on a view works over /sql.
    data = bytes(range(16))
    h = hashlib.sha256(data).hexdigest()
    host.call("put_artifact", host.fence, h, "model-1.stl", "model/stl", list(data), "")
    art = [a for a in alice.sql("SELECT * FROM v_artifact") if a["hash"] == h]
    assert len(art) == 1 and art[0]["size"] == 16 and "bytes" not in art[0], art
    blobs = alice.sql(f"SELECT * FROM v_artifact_blob WHERE hash = '{h}'")
    assert len(blobs) == 1, blobs
    print("smoke: v_artifact_blob bytes encoded as", repr(blobs[0]["bytes"]))
    assert decode_bytes(blobs[0]["bytes"]) == data, blobs[0]["bytes"]
    assert eve.sql(f"SELECT * FROM v_artifact_blob WHERE hash = '{h}'") == []
    host.call("put_artifact", host.fence, h, "model-1.stl", "model/stl", [], "")  # empty keeps the blob
    assert len(alice.sql(f"SELECT * FROM v_artifact_blob WHERE hash = '{h}'")) == 1

    # Components library: only a library op's result (completed by the host) adds or removes rows.
    alice.call("submit_op", alice.fence, "o_smoke000010", "library_add",
               json.dumps({"base64": "eA==", "filename": "bracket.step"}), "")
    host.call("start_op", host.fence, "o_smoke000010")
    row = {"id": "c_bracket_abc123", "name": "Bracket", "category": "structural", "tags": ["l bracket"],
           "source": "upload", "format": "step", "bbox": [0, 0, 0, 40, 20, 5], "volume": 900.5,
           "features": {"holes": [{"d": 3.4, "count": 2}]}, "suggest": []}
    host.call("complete_op", host.fence, "o_smoke000010", True,
              json.dumps({"_components": [row, {"id": "../evil", "name": "x"}]}), [], [], [], [], [], "", "")
    comps = carol.sql("SELECT * FROM v_component")
    assert [c["id"] for c in comps] == ["c_bracket_abc123"], comps
    c = comps[0]
    assert c["bbox"] == [0, 0, 0, 40, 20, 5] and c["tags"] == ["l bracket"], c
    assert json.loads(c["features_json"]) == {"holes": [{"d": 3.4, "count": 2}]}, c
    assert eve.sql("SELECT * FROM v_component") == []
    host.call("put_component_preview", host.fence, "c_bracket_abc123", list(b"solid"))
    alice.call("put_component_preview", alice.fence, "c_bracket_abc123", [], expect_error="not_allowed")
    prev = carol.sql("SELECT * FROM v_component_preview WHERE id = 'c_bracket_abc123'")
    assert decode_bytes(prev[0]["mesh"]) == b"solid", prev
    # Placing it counts a use.
    alice.call("submit_op", alice.fence, "o_smoke000011", "insert_component",
               json.dumps({"component": "c_bracket_abc123", "name": "Bracket1"}), "")
    host.call("start_op", host.fence, "o_smoke000011")
    host.call("complete_op", host.fence, "o_smoke000011", True, json.dumps({"name": "Bracket1"}),
              ["Bracket1"], [], [], [], [], "", "")
    assert carol.sql("SELECT * FROM v_component")[0]["uses"] == 1
    lock = [l for l in alice.sql("SELECT * FROM v_lock") if l["object"] == "Bracket1"]
    assert lock and lock[0]["holder"] == alice.me()["id"], "the placer owns the placed part"

    # Settings: people set them, helpers only suggest, a person accepts.
    alice.call("set_setting", alice.fence, "tol_clearance", 0.3, "", expect_error="not_allowed")
    carol.call("set_setting", carol.fence, "tol_clearance", 9.0, "", expect_error="bad_args")
    carol.call("set_setting", carol.fence, "hole_clearance_class", 0.0, "snug", expect_error="bad_args")
    carol.call("set_setting", carol.fence, "tol_clearance", 0.3, "")
    sets = {r["setting"]: r for r in alice.sql("SELECT * FROM v_setting")}
    assert sets["tol_clearance"]["num"] == 0.3, sets
    queued = [o for o in host.sql("SELECT * FROM v_op") if o["kind"] == "set_param" and o["status"] == "queued"]
    assert queued and json.loads(queued[-1]["args_json"])["alias"] == "tol_clearance", queued
    assert queued[-1]["allowed"] == ["Params"], queued[-1]
    alice.call("suggest_setting", alice.fence, "g_smoke001", "tol_press_fit", 0.0, "", "printed 608 seats",
               "c_bracket_abc123")
    alice.call("suggest_setting", alice.fence, "g_smoke002", "tol_press_fit", 0.0, "", " ", "", expect_error="bad_args")
    bob.call("resolve_suggestion", bob.fence, "g_smoke001", True, expect_error="not_allowed")
    carol.call("resolve_suggestion", carol.fence, "g_smoke001", True)
    g = [x for x in carol.sql("SELECT * FROM v_suggestion") if x["id"] == "g_smoke001"][0]
    assert g["status"] == "accepted" and g["resolved_by"] == carol.me()["id"], g
    sets = {r["setting"]: r for r in alice.sql("SELECT * FROM v_setting")}
    assert sets["tol_press_fit"]["num"] == 0.0, sets
    carol.call("resolve_suggestion", carol.fence, "g_smoke001", False, expect_error="already accepted")
    carol.call("reset_setting", carol.fence, "tol_clearance")
    assert "tol_clearance" not in {r["setting"] for r in alice.sql("SELECT * FROM v_setting")}
    kinds = {e["kind"] for e in carol.sql("SELECT * FROM v_event")}
    assert {"library", "setting_changed", "setting_suggested"} <= kinds, kinds

    print("smoke: all checks passed")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"smoke: FAILED: {e}")
        sys.exit(1)
