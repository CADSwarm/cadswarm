"""SpaceClient against a tiny fake SpacetimeDB HTTP server."""
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cadswarm.space import SpaceClient, SpaceError, decode_sql

SEEN = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0))).decode()
        SEEN.append((self.path, self.headers.get("authorization"), body))
        if self.path == "/v1/identity":
            return self.reply(200, {"identity": "abc", "token": "tok"})
        if self.path.endswith("/call/lock_objects"):
            return self.reply(530, "locked: Already locked: Plate by alice (active).", raw=True)
        if self.path.endswith("/call/heartbeat"):
            return self.reply(200, None)
        if self.path.endswith("/sql"):
            return self.reply(200, [{"schema": {"elements": [
                {"name": {"some": "id"}, "algebraic_type": {"U64": []}},
                {"name": {"some": "name"}, "algebraic_type": {"String": []}}]},
                "rows": [[1, "alice"], [2, "bob"]]}])
        self.reply(404, "no", raw=True)

    def reply(self, status, payload, raw=False):
        data = (payload if raw else json.dumps(payload) if payload is not None else "").encode()
        self.send_response(status)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def uri():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_new_identity_returns_a_token(uri):
    assert SpaceClient.new_identity(uri)["token"] == "tok"


def test_call_sends_args_as_a_json_array_with_the_bearer_token(uri):
    SpaceClient(uri, "db", "tok").call("heartbeat", 3, "busy")
    path, auth, body = SEEN[-1]
    assert path == "/v1/database/db/call/heartbeat" and auth == "Bearer tok"
    assert json.loads(body) == [3, "busy"]


def test_a_reducer_error_carries_its_code(uri):
    with pytest.raises(SpaceError) as e:
        SpaceClient(uri, "db", "tok").call("lock_objects", 1, ["Plate"])
    assert e.value.code == "locked" and "alice" in e.value.message


def test_sql_rows_come_back_as_dicts(uri):
    rows = SpaceClient(uri, "db", "tok").sql("SELECT * FROM v_member")
    assert rows == [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]
    assert SEEN[-1][2] == "SELECT * FROM v_member"


def test_decode_handles_plain_names_options_and_bytes():
    body = [{"schema": {"elements": [{"name": "a"}, {"name": {"some": "b"}}, {"name": "c"}]},
             "rows": [[{"some": 5}, {"none": []}, "0a0b"]]}]
    assert decode_sql(body) == [{"a": 5, "b": None, "c": "0a0b"}]


class KeepAlive(Fake):
    """Same answers, but HTTP/1.1 so the client may keep the socket for the next request."""
    protocol_version = "HTTP/1.1"


class Drops(BaseHTTPRequestHandler):
    """Accepts the request and closes without answering (like a pooled socket the server gave up on)."""
    protocol_version = "HTTP/1.1"
    seen = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        Drops.seen += 1
        self.close_connection = True
        self.connection.close()


def serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_reads_reuse_one_connection_instead_of_a_new_tls_handshake_each():
    srv = serve(KeepAlive)
    try:
        client = SpaceClient(f"http://127.0.0.1:{srv.server_port}", "db", "tok")
        opened = []
        real = client._open

        def counting():
            opened.append(1)
            return real()
        client._open = counting
        client.sql("SELECT * FROM v_member")
        client.sql("SELECT * FROM v_task")
        assert len(opened) == 1  # the second read went out on the same socket
        assert len(client._idle) == 1
        client.close()
        assert client._idle == []
    finally:
        srv.shutdown()


def test_a_dropped_read_is_retried_but_a_dropped_reducer_is_not():
    """Repeating a reducer could post the same chat message twice; repeating a query cannot hurt."""
    srv = serve(Drops)
    try:
        uri = f"http://127.0.0.1:{srv.server_port}"
        with pytest.raises(SpaceError):
            SpaceClient(uri, "db", "tok", timeout=5).sql("SELECT * FROM v_member")
        assert Drops.seen == 2
        Drops.seen = 0
        with pytest.raises(SpaceError):
            SpaceClient(uri, "db", "tok", timeout=5).call("send_message", 1, "m1", "all", "hi", "chat", "")
        assert Drops.seen == 1
    finally:
        srv.shutdown()


class ClosesAfterReply(Fake):
    """Answers keep-alive, then closes the socket anyway (a server's idle timeout, early)."""
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        super().do_POST()
        self.close_connection = True


def test_a_socket_the_server_closed_while_idle_is_not_reused_for_a_reducer():
    """A sent reducer is never retried, so it must not go out on a socket the server already closed."""
    srv = serve(ClosesAfterReply)
    try:
        client = SpaceClient(f"http://127.0.0.1:{srv.server_port}", "db", "tok", timeout=5)
        client.sql("SELECT * FROM v_member")
        assert len(client._idle) == 1
        dead, used = client._idle[0][0], []
        real = dead.request
        dead.request = lambda *a, **k: (used.append(1), real(*a, **k))
        time.sleep(0.2)  # the server's close reaches the pooled socket
        client.call("heartbeat", 1, "")
        assert SEEN[-1][0] == "/v1/database/db/call/heartbeat"
        # On Linux a write to it "succeeds" and only the read fails, after which a reducer can't be retried.
        assert used == []
    finally:
        srv.shutdown()


def test_a_configured_proxy_is_still_used(monkeypatch):
    """Kept-alive sockets go straight to the server, so behind a proxy the client goes through it instead."""
    srv = serve(Fake)
    try:
        for var in ("no_proxy", "NO_PROXY", "https_proxy", "HTTPS_PROXY", "HTTP_PROXY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{srv.server_port}")
        monkeypatch.setattr(urllib.request, "_opener", None)  # urlopen built its opener before the proxy was set
        rows = SpaceClient("http://spacetime.invalid", "db", "tok", timeout=5).sql("SELECT * FROM v_member")
        assert rows[0]["name"] == "alice"
        assert SEEN[-1][0] == "http://spacetime.invalid/v1/database/db/sql"  # the proxy saw the full URL
    finally:
        srv.shutdown()


def test_an_unreachable_server_is_a_space_error():
    with pytest.raises(SpaceError) as e:
        SpaceClient("http://127.0.0.1:9", "db", "tok", timeout=2).call("heartbeat", 1, "")
    assert e.value.status == 0 and e.value.code == "unreachable"
