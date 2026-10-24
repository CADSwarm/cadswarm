"""The MCP tools log activity (which moves the agent's pointer) and stop acting once superseded."""
import types

import pytest

from cadswarm import mcp_server
from cadswarm.client import HubRequestError


class RecordingHub:
    def __init__(self):
        self.activity, self.posts = [], []
        self.fail = None

    def log_activity(self, kind, summary, targets=None):
        self.activity.append((kind, summary, targets))

    def post(self, path, body=None):
        self.posts.append((path, body))
        if self.fail:
            raise self.fail
        return {"ok": True}

    def get(self, path, **params):
        return {"path": path, **params}


@pytest.fixture
def session(monkeypatch):
    s = types.SimpleNamespace(client=RecordingHub(), dead=None, doing="", cfg={"name": "a"})
    monkeypatch.setattr(mcp_server, "S", s)
    return s


def test_ops_log_activity_with_their_target_objects(session):
    mcp_server.boolean("cut", "Lid", "Plate", "Hole")
    assert session.client.activity == [("tool_call", "cut Plate/Hole -> Lid", ["Lid", "Plate", "Hole"])]
    path, body = session.client.posts[-1]
    assert path == "/ops" and body["op"] == "boolean" and body["args"]["base"] == "Plate"


def test_new_tools_reach_their_routes(session):
    mcp_server.create_task("lid", brief="b_1")
    assert session.client.posts[-1] == ("/tasks", {"title": "lid", "detail": "", "objects": None,
                                                    "assignee": None, "brief": "b_1"})
    mcp_server.check_printability(["Lid"])
    assert session.client.posts[-1] == ("/printability", {"names": ["Lid"], "min_wall": None})
    assert session.client.activity[-1][2] == ["Lid"]
    mcp_server.set_brief_plan("b_1", {"parts": []})
    assert session.client.posts[-1][0] == "/briefs/b_1/plan"
    mcp_server.accept_handoff("h_1")
    assert session.client.posts[-1][0] == "/handoffs/h_1/accept"
    mcp_server.report_review("b_1", 1, True, [])
    assert session.client.posts[-1] == ("/reviews", {"brief_id": "b_1", "round": 1, "passed": True, "findings": []})
    assert mcp_server.list_files("parts") == {"path": "/project/files", "prefix": "parts"}


def test_a_superseded_session_refuses_every_tool(session):
    session.client.fail = HubRequestError(409, {"error": "superseded", "code": "fence_stale"})
    r = mcp_server.lock_objects(["Lid"])
    assert r["code"] == "fence_stale"
    assert mcp_server.create_primitive("box", "Box")["code"] == "inactive"
