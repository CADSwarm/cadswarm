"""SpaceHub against a real published module. Run with the server up:
    $env:CADSWARM_LIVE = "1"; python -m pytest tests/test_space_live.py -q
Needs the publisher's token (`spacetime login show --token`) or CADSWARM_OWNER_TOKEN: the publisher
is the module's `host` member, the only one that may mint invites without being invited first.
CADSWARM_URI / CADSWARM_DB override http://127.0.0.1:3000 / cadswarm. The op test also needs a
running `cadswarm-host` (it is skipped when no host member is active).
"""
import os
import re
import shutil
import subprocess

import pytest

from cadswarm import cli, ticket
from cadswarm.space import SpaceClient
from cadswarm.space_hub import project_hub

if os.environ.get("CADSWARM_LIVE") != "1":
    pytest.skip("set CADSWARM_LIVE=1 to run against a local SpacetimeDB", allow_module_level=True)

URI = os.environ.get("CADSWARM_URI", "http://127.0.0.1:3000")
DB = os.environ.get("CADSWARM_DB", "cadswarm")


def owner():
    tok = os.environ.get("CADSWARM_OWNER_TOKEN")
    if not tok:
        if not shutil.which("spacetime"):
            pytest.skip("no CADSWARM_OWNER_TOKEN and no spacetime CLI")
        out = subprocess.run(["spacetime", "login", "show", "--token"], capture_output=True, text=True).stdout
        tok = re.search(r"([\w-]+\.[\w-]+\.[\w-]+)", out).group(1)
    c = SpaceClient(URI, DB, tok)
    me = c.sql("SELECT * FROM v_me")
    if not me or me[0]["status"] not in ("active", "stale"):
        pytest.skip("the owner token is not a live member of this database")
    return c, me[0]["fence"]


def make_ticket(kind="agent"):
    c, fence = owner()
    tok = ticket.new_token()
    c.call("create_invite", fence, ticket.hash_token(tok), kind, "member", 3600)
    return ticket.encode({"v": 1, "uri": URI, "db": DB, "token": tok, "kind": kind, "role": "member", "host_addr": ""})


def agent(folder, client, prefix):
    name = f"{prefix}-{ticket.new_id('x', 4)}"
    cli.main(["setup", "--client", client, "--invite", make_ticket(), "--name", name, "--dir", str(folder)])
    hub, _ = project_hub(str(folder))
    hub.join()
    return hub


def test_two_agents_coordinate_through_spacetimedb(tmp_path):
    a = agent(tmp_path / "a", "claude", "live-a")
    b = agent(tmp_path / "b", "codex", "live-b")
    part = f"Part{ticket.new_id('x', 6)}"
    assert a.post("/locks", {"names": [part]})["locked"] == [part]
    with pytest.raises(Exception) as e:
        b.post("/locks", {"names": [part]})
    assert "locked" in str(e.value) or getattr(e.value, "code", "") == "locked"
    t = a.post("/tasks", {"title": "lid", "assignee": b.identity["name"]})
    b.post(f"/tasks/{t['id']}/claim", {})
    mine = next(x for x in a.get("/tasks")["tasks"] if x["id"] == t["id"])
    assert mine["status"] == "claimed" and mine["owner_name"] == b.identity["name"]
    b.post("/messages", {"to": a.identity["name"], "body": "on it"})
    assert any(m["body"] == "on it" for m in a.get("/inbox")["messages"])
    assert b.identity["name"] in a.get("/context")["context"]
    a.post("/locks/release", {"names": [part]})


def test_an_agents_op_runs_on_the_host(tmp_path):
    """M1: Python -> SpacetimeDB -> cadswarm-host -> FreeCAD -> SpacetimeDB."""
    a = agent(tmp_path / "m1", "claude", "live-m1")
    if not a.get("/health")["ok"]:
        pytest.skip("no active cadswarm-host member: start `cadswarm-host run` to test ops end to end")
    name = f"Box{ticket.new_id('x', 6)}"
    r = a.post("/ops", {"op": "create_primitive", "args": {"kind": "box", "name": name,
                                                          "props": {"Length": 10, "Width": 10, "Height": 10}}})
    assert r["created"] == [name]
    assert name in {o["name"] for o in a.get("/model")["objects"]}
