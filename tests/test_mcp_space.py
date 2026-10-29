"""The MCP tools log activity (which moves the agent's pointer) and stop acting once superseded."""
import json
import threading
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

    unread = ()

    def unread_notes(self, seen):
        return [m for m in self.unread if m["id"] not in seen]


def fake_session(joined=True):
    ready = threading.Event()
    if joined:
        ready.set()
    return types.SimpleNamespace(client=RecordingHub(), dead=None, doing="", cfg={"name": "a"}, ready=ready,
                                 join_error=None, seen_messages=set(), inbox_checked=0.0)


def test_a_message_that_arrives_mid_task_rides_on_the_next_tool_result_once(session, monkeypatch):
    """A headless worker gets one prompt; without this a person's steer never reached it until it finished."""
    monkeypatch.setattr(mcp_server, "INBOX_CHECK_SECONDS", 0)
    assert "new_messages" not in mcp_server.lock_objects(["Lid"])
    session.client.unread = [{"id": "m_1", "from": "Lucky", "kind": "steer", "body": "stop the door, do the hood"}]
    r = mcp_server.lock_objects(["Lid"])
    assert r["ok"] and r["new_messages"]["messages"][0]["body"] == "stop the door, do the hood"
    assert "COMMAND" in r["new_messages"]["note"]
    assert "new_messages" not in mcp_server.lock_objects(["Lid"]), "each message is shown once"


@pytest.fixture
def session(monkeypatch):
    s = fake_session()
    monkeypatch.setattr(mcp_server, "S", s)
    return s


def test_a_tool_called_during_the_join_waits_for_it(monkeypatch):
    """The handshake no longer waits for the join, so a model can call a tool while it is still going."""
    s = fake_session(joined=False)
    monkeypatch.setattr(mcp_server, "S", s)
    threading.Timer(0.2, s.ready.set).start()
    assert mcp_server.lock_objects(["Lid"]) == {"ok": True}
    assert s.client.posts == [("/locks", {"names": ["Lid"]})]


def test_a_join_that_never_finishes_or_failed_says_so(monkeypatch):
    s = fake_session(joined=False)
    monkeypatch.setattr(mcp_server, "S", s)
    monkeypatch.setattr(mcp_server, "JOIN_WAIT_SECONDS", 0.05)
    assert mcp_server.lock_objects(["Lid"])["code"] == "joining"
    s.join_error = "Couldn't join the team at u / db: removed: a person removed this helper from the team"
    s.ready.set()
    r = mcp_server.lock_objects(["Lid"])
    assert r["code"] == "not_joined" and "removed" in r["error"]
    assert s.client.posts == [], "nothing reaches the team before the join"


def test_the_join_retries_an_unreachable_database_then_runs_heartbeats(monkeypatch):
    s = fake_session(joined=False)
    s.cfg = {"name": "a", "uri": "u", "db": "d", "ephemeral": True}
    tries = []

    def connect():
        tries.append(1)
        if len(tries) < 3:
            raise HubRequestError(0, {"error": "connection refused", "code": "unreachable"})
        return {"name": "a", "participant_id": 1, "fence": 2}

    s.connect, s.heartbeat_loop = connect, lambda: None
    monkeypatch.setattr(mcp_server, "S", s)
    monkeypatch.setattr(mcp_server.time, "sleep", lambda _: None)
    mcp_server.join_team()
    assert len(tries) == 3 and s.ready.is_set() and s.join_error is None


def test_a_refused_join_is_not_retried(monkeypatch):
    s = fake_session(joined=False)
    s.cfg = {"name": "a", "uri": "u", "db": "d", "ephemeral": True}
    tries = []

    def connect():
        tries.append(1)
        raise HubRequestError(403, {"error": "removed: a person removed this helper", "code": "removed"})

    s.connect = connect
    monkeypatch.setattr(mcp_server, "S", s)
    mcp_server.join_team()
    assert len(tries) == 1 and s.ready.is_set() and "removed" in s.join_error


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


def test_a_warm_server_shows_idle_once_its_runner_waits(tmp_path):
    """The MCP server outlives the run: without this, the team list says "working on Lid" for minutes."""
    import time

    (tmp_path / ".cadswarm").mkdir()
    runner = tmp_path / ".cadswarm" / "runner.json"
    s = types.SimpleNamespace(doing='working on "Lid"', project=str(tmp_path))
    runner.write_text(json.dumps({"state": "listening", "beat": time.time()}))
    assert mcp_server.Session.doing_now(s) == "listening for instructions"
    runner.write_text(json.dumps({"state": "working", "beat": time.time()}))
    assert mcp_server.Session.doing_now(s) == 'working on "Lid"'
    # A stale beat means no runner is looking after the folder: keep what the run was doing.
    runner.write_text(json.dumps({"state": "listening", "beat": 0}))
    assert mcp_server.Session.doing_now(s) == 'working on "Lid"'
    # A run that never claimed a task leaves "starting"; the waiting runner wins over it too.
    s.doing = "starting"
    runner.write_text(json.dumps({"state": "listening", "beat": time.time()}))
    assert mcp_server.Session.doing_now(s) == "listening for instructions"
    # A host-spawned agent has no runner at all: it just says what it is doing.
    (tmp_path / ".cadswarm" / "runner.json").unlink()
    assert mcp_server.Session.doing_now(s) == "starting"


def test_surface_quality_composes_the_verdict_from_the_topic(monkeypatch, session):
    session.client.post = lambda path, body=None: {
        "objects": [{"name": "BodyShell", "faces": 194, "planar": 146, "curved": 48, "valid": True}]}
    got = mcp_server.surface_quality(["BodyShell"], topic="a Lexus IS350 body")
    assert got["intent"] == "aesthetic"
    assert got["failing"] == ["BodyShell"]
    assert got["objects"][0]["verdict"] == "fails"
    assert "block model" in got["objects"][0]["advice"][0]


def test_surface_quality_leaves_a_bracket_alone_and_takes_an_override(monkeypatch, session):
    session.client.post = lambda path, body=None: {
        "objects": [{"name": "Bracket", "faces": 6, "planar": 6, "curved": 0, "valid": True}]}
    assert mcp_server.surface_quality(["Bracket"], topic="a mount bracket")["failing"] == []
    got = mcp_server.surface_quality(["Bracket"], topic="a mount bracket", intent="aesthetic")
    assert got["intent"] == "aesthetic" and got["failing"] == ["Bracket"]
    assert mcp_server.surface_quality(intent="sideways")["code"] == "bad_args"


def test_record_feedback_attaches_the_shape_evidence(monkeypatch, session):
    posts = []
    def route(path, body=None):
        posts.append((path, body))
        if path == "/surface":
            return {"objects": [{"name": "BodyShell", "faces": 194, "planar": 146, "curved": 48, "valid": True}]}
        return {"id": 7, "subject": "car", "recorded": 1}
    session.client.post = route
    got = mcp_server.record_feedback("bad", ["roof is a box"], subject="car", objects=["BodyShell"],
                                     topic="a Lexus IS350 body")
    assert got["id"] == 7
    feedback_post = posts[-1][1]
    assert feedback_post["verdict"] == "bad" and feedback_post["issues"] == ["roof is a box"]
    assert feedback_post["evidence"]["intent"] == "aesthetic"
    assert feedback_post["evidence"]["objects"][0]["planar_share"] == 0.753


def test_record_feedback_refuses_a_bad_verdict(session):
    assert mcp_server.record_feedback("meh")["code"] == "bad_args"
    assert mcp_server.record_feedback("bad", by="nobody")["code"] == "bad_args"


def test_add_reference_reads_a_local_image_and_posts_it(monkeypatch, session, tmp_path):
    image = tmp_path / "is350.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    posts = []
    session.client.post = lambda path, body=None: posts.append((path, body)) or {"subject": "car"}
    mcp_server.add_reference("car", str(image), label="front", dimensions="4710 x 1840 x 1435")
    body = posts[0][1]
    assert body["filename"] == "is350.png" and body["label"] == "front"
    import base64
    assert base64.b64decode(body["image_base64"]) == image.read_bytes()


def test_add_reference_refuses_a_missing_file(session):
    got = mcp_server.add_reference("car", "C:/nope/none.png")
    assert got["code"] == "bad_args" and "no file" in got["error"]


def test_add_reference_downloads_an_image_url_with_its_credit(monkeypatch, session):
    import urllib.request
    class Response:
        headers = {"Content-Type": "image/png"}
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self, n):
            return b"\x89PNG\r\n\x1a\n" + b"0" * 16
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response())
    posts = []
    session.client.post = lambda path, body=None: posts.append((path, body)) or {"image": "reference/car/x.png"}
    got = mcp_server.add_reference("car", image_url="https://example.com/is350-front.png", label="front",
                                   credit="press photo", license="CC BY 4.0")
    assert got["image"].endswith(".png")
    body = posts[0][1]
    assert body["filename"] == "is350-front.png"
    assert body["source_url"] == "https://example.com/is350-front.png"
    assert body["credit"] == "press photo" and body["license"] == "CC BY 4.0"


def test_add_reference_refuses_a_url_that_is_not_an_image(monkeypatch, session):
    class Response:
        headers = {"Content-Type": "text/html"}
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self, n):
            return b"<html>"
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response())
    got = mcp_server.add_reference("car", image_url="https://example.com/page")
    assert got["code"] == "bad_args" and "PNG, JPEG or WebP" in got["error"]

def test_add_reference_reports_a_missing_file_instead_of_raising(session):
    got = mcp_server.add_reference("car", image_path="C:/nope/none.png")
    assert got["code"] == "bad_args" and "no file" in got["error"]

def test_render_views_proxies_the_worker_query(session):
    posts = []
    session.client.post = lambda path, body=None: posts.append((path, body)) or {
        "files": ["renders/2026-10-27 2300/front.png"]}
    got = mcp_server.render_views(["BodyShell"], views=["front"], size=400)
    assert got["files"]
    assert posts[0][0] == "/render" and posts[0][1]["size"] == 400
    assert posts[0][1]["views"] == ["front"] and posts[0][1]["names"] == ["BodyShell"]
    assert posts[0][1]["deviation"] is None, "left out, the worker sizes the tolerance to the model"
    mcp_server.render_views(deviation=4)
    assert posts[1][1]["deviation"] == 4.0


def test_a_reference_image_larger_than_one_message_is_refused(tmp_path, session):
    big = tmp_path / "big.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 1_600_000)
    got = mcp_server.add_reference("car", image_path=str(big))
    assert got["code"] == "bad_args" and "Resize or compress" in got["error"]
