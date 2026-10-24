# CadSwarm Python Implementation Plan (Person C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Revised 2026-10-24 against `main` `75bcb43`** (fddfa28's beginner setup, the hardening, the script
> sandbox and the storage policy, plus the module and bindings). Tasks 1–3 are as before, plus the invite v2
> step in Task 2 and the extra routes in Task 3. Task 4 was rewritten. Tasks 5 (helper) and 6 (FreeCAD guide)
> are new. The old Tasks 5–7 are now 7–9. Order for the MVP: 1 → 2 → 3 → 4 → 5 → 7 → 8 (M1/M2), then 6, then 9.

**Goal:** Move the Python side of CadSwarm (MCP server, prompt hook, CLI, FreeCAD guide) from the FastAPI hub to SpacetimeDB. Add the host-side helper for project files and storage, the two new FreeCAD worker queries, the board library and pairing by invite, and (P1) the Droid reviewer.

**Architecture:** `SpaceClient` is a stdlib-only HTTP client for SpacetimeDB (reducer calls, SQL on views, identity creation). `SpaceHub` sits on top and answers the old hub's REST paths (`/model`, `/ops`, `/inbox`, `/context`, ...) with the same JSON shapes, so `mcp_server.py` and `prompt_hook.py` barely change. Ops are queued with `submit_op` and awaited by polling `v_op`. The project-file and storage ops run in `cadswarm/helper.py`, which the Rust host drives like the FreeCAD worker. The FastAPI hub is deleted; `files.py` and `storage.py` move out of it unchanged.

**Tech Stack:** Python 3.11+ stdlib (`urllib`, `json`, `hashlib`, `base64`, `secrets`), the existing `mcp` package, pytest; FreeCAD 1.x for worker tests; `droid-sdk` (P1).

Read first: `docs/superpowers/plans/2026-10-23-cadswarm-00-overview.md` (contract), `cadswarm/hub/core.py` (the behaviour you are re-exposing), `cadswarm/mcp_server.py`.

## Global Constraints

- `cadswarm/space.py`, `cadswarm/ticket.py` and `cadswarm/space_hub.py` use only the standard library.
- Reducer errors arrive as text `"<code>: <message>"`; surface them as `HubRequestError(status, {"error", "code"})` so the MCP tools' error output is unchanged.
- Heartbeat every 15 s (`HEARTBEAT_SECONDS = 15`).
- IDs: op `o_` + 12 hex, task `t_` + 8, message `m_` + 12, brief `b_` + 8, review `r_` + 8.
- Tickets: base32 (uppercase, no padding) of `{"v":1,"uri","db","token","kind","role","host_addr"}`.
- `.cadswarm/identity.json` holds a SpacetimeDB token: the folder keeps its `*` gitignore.
- Invite codes for people and the FreeCAD guide are `cadswarm-2:` + base64url of the ticket JSON (plus `p`, the project name). Every reader also accepts the bare base32 ticket and phone links with `#t=`. Invites work once.
- Over `/sql`, a `Vec<u8>` is a lowercase hex string (`bytes.fromhex`). Reducer calls send bytes as a JSON array of ints.
- An op result of `{"blob": "<sha256>"}` means the host spilled it (over 32 KB). Read it from `v_artifact_blob WHERE hash = ...`.
- Polling floors (plan 00): `wait_for_changes` starts at 1 s and backs off to 3 s. Op waits start at 0.5 s and back off to 1 s.
- `helper.py` and everything it imports use only the standard library: the host may run it with FreeCAD's Python.

## File structure

| File | Responsibility |
|---|---|
| `cadswarm/space.py` | `SpaceClient`, `SpaceError`, row decoding |
| `cadswarm/ticket.py` | Tokens, hashes, ids, ticket encode/decode |
| `cadswarm/space_hub.py` | `SpaceHub` (old REST paths over SpacetimeDB), `project_hub()` |
| `cadswarm/mcp_server.py` | Uses `SpaceHub`; logs activity; new tools |
| `cadswarm/prompt_hook.py` | Uses `project_hub()` |
| `cadswarm/cli.py` | `setup --invite` (claude, codex, droid), `brief`; `hub` and `invite` commands removed |
| `cadswarm/agent_setup.py` | `redeem`, `write_agent_config(project, client, invite, name, ...)`, `setup_droid` |
| `cadswarm/invite.py` | `cadswarm-2:` codes; also reads base32 tickets and phone links |
| `cadswarm/helper.py` | Host-side JSON-lines helper: project files, storage, snapshots (plan 05 adds Elastic) |
| `cadswarm/files.py`, `cadswarm/storage.py` | Moved unchanged from `cadswarm/hub/` |
| `cadswarm/hosting.py`, `cadswarm/gui/wizard.py`, `cadswarm/gui/panel.py` | FreeCAD guide: start `cadswarm-host`, mint invites, talk through SpaceHub |
| `cadswarm/config.py` | `HEARTBEAT_SECONDS = 15`, `helper_script()` |
| `cadswarm/worker_client.py` | Moved from `cadswarm/hub/worker_client.py` (tests use it) |
| `cadswarm/worker/freecad_worker.py` | New queries `mesh`, `printability` |
| `cadswarm/reviewer.py` | P1: Droid reviewer |
| `boards/boards.json` | Board library |
| `tests/test_space_client.py`, `tests/test_ticket.py`, `tests/test_space_hub.py`, `tests/test_worker_queries.py`, `tests/test_space_live.py`, `tests/test_reviewer.py` | Tests |
| Deleted | `cadswarm/hub/app.py`, `core.py` (ported to `rules/` and the module), `tests/test_core.py`; `tests/test_integration.py` is rewritten in Task 8 |

---

### Task 1: SpaceClient

**Files:**
- Create: `cadswarm/space.py`, `tests/test_space_client.py`

**Interfaces:**
- Produces: `class SpaceError(Exception)` with `.status: int`, `.code: str`, `.message: str`; `class SpaceClient(uri, db, token=None, timeout=30)` with `.call(reducer, *args) -> None`, `.sql(query) -> list[dict]`, `@staticmethod new_identity(uri) -> dict` (`{"identity", "token"}`); `decode_sql(body) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_space_client.py`:
```python
"""SpaceClient against a tiny fake SpacetimeDB HTTP server."""
import json
import threading
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
```

- [ ] **Step 2: Run to see them fail**

Run: `python -m pytest tests/test_space_client.py -q`
Expected: `ModuleNotFoundError: No module named 'cadswarm.space'`.

- [ ] **Step 3: Implement `cadswarm/space.py`**

Verified live on 2.13.0 by 3d-cad-08 at M0:
- `/sql` returns 200 with one envelope per statement: `[{"schema":{"elements":[{"name":{"some":"id"},...}]},"rows":[[...],...],"total_duration_micros":...}]`. Rows are arrays in column order, and `u64` values are plain JSON numbers. A view queried by a non-member returns 200 with `rows: []`.
- A reducer `Err` returns **530** with a plain-text body that is exactly the Err string, for example `fence_stale: This process was superseded…`. An anonymous call returns 530 `not_member: …`, not 401, because the server mints an identity.
- A reducer success returns 200 with an empty body.
The fake server in Step 1 and the code below already match these.

```python
"""Stdlib-only client for SpacetimeDB's HTTP API.

Reducers are called with `POST /v1/database/<db>/call/<reducer>` and a JSON array of arguments;
reads are SQL over the module's member-only views (`v_*`). A reducer refusal comes back as a
non-2xx response whose body is the module's `"<code>: <message>"` text.
"""
import json
import urllib.error
import urllib.request


class SpaceError(Exception):
    def __init__(self, status, text):
        self.status = status
        text = (text or "").strip()
        code, sep, message = text.partition(": ")
        if sep and code.replace("_", "").isalnum() and code.islower():
            self.code, self.message = code, message
        else:
            self.code, self.message = ("unreachable" if status == 0 else "error"), text
        super().__init__(f"{self.code}: {self.message}")


def _unwrap(v):
    """SATS JSON wraps options as {"some": v} / {"none": []}; columns here never need more."""
    if isinstance(v, dict) and len(v) == 1:
        if "some" in v:
            return v["some"]
        if "none" in v:
            return None
    return v


def decode_sql(body):
    out = []
    for table in body or []:
        names = [_unwrap(el["name"]) if isinstance(el["name"], dict) else el["name"]
                 for el in table["schema"]["elements"]]
        out += [{n: _unwrap(v) for n, v in zip(names, row)} for row in table["rows"]]
    return out


class SpaceClient:
    def __init__(self, uri, db, token=None, timeout=30):
        self.uri = uri.rstrip("/")
        self.db = db
        self.token = token
        self.timeout = timeout

    @staticmethod
    def _request(url, data, token=None, content_type="application/json", timeout=30):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("content-type", content_type)
        if token:
            req.add_header("authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text.strip() else None
        except urllib.error.HTTPError as e:
            raise SpaceError(e.code, e.read().decode("utf-8", "replace"))
        except urllib.error.URLError as e:
            raise SpaceError(0, f"Cannot reach SpacetimeDB at {url}: {e.reason}")

    @staticmethod
    def new_identity(uri, timeout=30):
        return SpaceClient._request(uri.rstrip("/") + "/v1/identity", b"", timeout=timeout)

    def call(self, reducer, *args):
        url = f"{self.uri}/v1/database/{self.db}/call/{reducer}"
        self._request(url, json.dumps(list(args)).encode(), self.token, timeout=self.timeout)

    def sql(self, query):
        url = f"{self.uri}/v1/database/{self.db}/sql"
        return decode_sql(self._request(url, query.encode(), self.token, "text/plain", self.timeout))
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_space_client.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```powershell
git switch -c feat/python
git add cadswarm/space.py tests/test_space_client.py
git commit -m "feat(python): stdlib SpacetimeDB HTTP client"
```

---

### Task 2: Tickets and ids

**Files:**
- Create: `cadswarm/ticket.py`, `tests/test_ticket.py`

**Interfaces:**
- Produces: `new_token() -> str` (64 hex), `hash_token(str) -> str`, `new_id(prefix: str, hex_len: int) -> str`, `encode(dict) -> str`, `decode(str) -> dict`, `ID_LEN = {"o_": 12, "t_": 8, "m_": 12, "b_": 8, "r_": 8}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_ticket.py`:
```python
from cadswarm import ticket


def test_ticket_round_trips_case_insensitively_and_matches_the_host_format():
    t = {"v": 1, "uri": "http://127.0.0.1:3000", "db": "cadswarm", "token": "ab" * 32,
         "kind": "agent", "role": "member", "host_addr": ""}
    s = ticket.encode(t)
    assert s == s.upper() and "=" not in s
    assert ticket.decode(s) == t
    assert ticket.decode(s.lower() + "\n") == t


def test_tokens_hashes_and_ids():
    tok = ticket.new_token()
    assert len(tok) == 64 and int(tok, 16) >= 0
    assert ticket.hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    oid = ticket.new_id("o_")
    assert oid.startswith("o_") and len(oid) == 2 + 12
    assert len(ticket.new_id("t_")) == 2 + 8
```

- [ ] **Step 2: Run to see them fail**

Run: `python -m pytest tests/test_ticket.py -q`
Expected: `ImportError`.

- [ ] **Step 3: Implement `cadswarm/ticket.py`**

```python
"""Pairing tickets and client-side ids (same formats as the Rust host and the web app)."""
import base64
import hashlib
import json
import secrets

ID_LEN = {"o_": 12, "t_": 8, "m_": 12, "b_": 8, "r_": 8}


def new_token():
    return secrets.token_hex(32)


def hash_token(token):
    return hashlib.sha256(token.strip().encode()).hexdigest()


def new_id(prefix, hex_len=None):
    n = hex_len or ID_LEN.get(prefix, 12)
    return prefix + secrets.token_hex((n + 1) // 2)[:n]


def encode(t):
    raw = json.dumps(t, separators=(",", ":")).encode()
    return base64.b32encode(raw).decode().rstrip("=")


def decode(s):
    cleaned = "".join(s.split()).upper()
    cleaned += "=" * (-len(cleaned) % 8)
    return json.loads(base64.b32decode(cleaned))
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_ticket.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```powershell
git add cadswarm/ticket.py tests/test_ticket.py
git commit -m "feat(python): pairing tickets and ids"
```

- [ ] **Step 6: Invite codes v2 (`cadswarm-2:`), test first**

`tests/test_invite_v2.py`:
```python
import pytest

from cadswarm import ticket
from cadswarm.invite import InviteError, make_invite, read_invite

T = {"v": 1, "uri": "https://maincloud.spacetimedb.com", "db": "cadswarm-team-1a2b", "token": "ab" * 32,
     "kind": "human", "role": "member", "host_addr": ""}


def test_v2_code_round_trips_through_chat_noise():
    code = make_invite(T, "Phone stand")
    assert code.startswith("cadswarm-2:")
    got = read_invite(f"“{code[:30]}\n{code[30:]}” thanks!")
    assert got["token"] == T["token"] and got["db"] == T["db"] and got["project"] == "Phone stand"


def test_base32_tickets_and_phone_links_are_accepted():
    b32 = ticket.encode(T)
    assert read_invite(b32)["token"] == T["token"]
    assert read_invite(f"https://cadswarm.app/#t={b32}")["uri"] == T["uri"]


def test_old_codes_get_a_clear_message():
    with pytest.raises(InviteError, match="older CadSwarm"):
        read_invite("cadswarm-1:eyJoIjoiaHR0cDovLzE5Mi4xNjguMS4yMDo4NzY1In0")
```
Implement by changing `PREFIX`, `make_invite` and `_decode` in `invite.py`. Keep the lenient chunk scanner. Validate that the decoded dict has `uri` (http or https), `db` and `token`. Route `read_invite` in `cadswarm/cli.py` and `agent_setup` through this module. Update the existing invite tests in `tests/test_setup*.py` to the v2 format.

Run: `python -m pytest tests/test_invite_v2.py tests/test_ticket.py -q`. Expected: all pass.

```powershell
git add cadswarm/invite.py tests/test_invite_v2.py tests/test_setup*.py
git commit -m "feat(python): cadswarm-2 invite codes carry the SpacetimeDB ticket"
```

---

### Task 3: SpaceHub, the hub-compatible adapter

**Files:**
- Create: `cadswarm/space_hub.py`, `tests/test_space_hub.py`

**Interfaces:**
- Consumes: `SpaceClient`, `SpaceError`, `ticket.new_id`, `client.HubRequestError`, `client.load_json`, `client.state_dir`.
- Produces: `class SpaceHub(cfg: dict, identity: dict | None, timeout=60, space=None)` with `.identity`, `.timeout`, `.hub` (the uri, for messages), `.get(path, **params)`, `.post(path, body=None)`, `.request(method, path, body=None, params=None, timeout=None, raw=False)`, `.join(workspace_token, name, kind, client, role) -> dict`, `.log_activity(kind, summary, targets) -> None`; `project_hub(project_dir) -> tuple[SpaceHub | None, dict | None]`. Paths answered: everything in the route table below.

- [ ] **Step 1: Write the failing tests**

`tests/test_space_hub.py`:
```python
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
```

- [ ] **Step 2: Run to see them fail**

Run: `python -m pytest tests/test_space_hub.py -q`
Expected: `ModuleNotFoundError: No module named 'cadswarm.space_hub'`.

- [ ] **Step 3: Implement `cadswarm/space_hub.py`**

```python
"""The old CadSwarm hub's REST paths, answered by SpacetimeDB.

The MCP server, prompt hook and FreeCAD workbench were written against `HubClient.get/post`
and the JSON shapes `cadswarm/hub/core.py` returned. SpaceHub keeps those paths and shapes:
writes become reducer calls (with this process's fence), reads become SQL over the module's
member-only views, and ops are queued with `submit_op` and awaited until the host finishes them.
"""
import json
import os
import re
import time

from . import ticket
from .client import HubRequestError, load_json, state_dir
from .space import SpaceClient, SpaceError

STATUS = {"not_member": 401, "bad_invite": 401, "fence_stale": 409, "taken_over": 403, "left": 403,
          "not_allowed": 403, "not_owner": 403, "unknown_participant": 404, "unknown_task": 404,
          "not_found": 404, "bad_args": 400, "unknown_op": 400, "unreachable": 0}
OP_WAIT_SECONDS = 150
EVENT_KINDS_FOR_CONTEXT = ("op", "taken_over", "stale", "joined", "task", "task_reported")


def _err(e):
    return HubRequestError(STATUS.get(e.code, 409), {"error": e.message, "code": e.code})


def _json(text, default):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


class SpaceHub:
    def __init__(self, cfg, identity=None, timeout=60, space=None):
        self.cfg = cfg
        self.identity = identity or {}
        self.timeout = timeout
        self.hub = cfg.get("uri", "")
        self.space = space or SpaceClient(cfg["uri"], cfg["db"], self.identity.get("token"), timeout)
        self._seq = 0
        self.routes = [
            ("POST", r"/heartbeat", self.heartbeat), ("POST", r"/leave", self.leave),
            ("GET", r"/model", self.model), ("GET", r"/objects/(?P<name>[^/]+)", self.get_object),
            ("POST", r"/ops", self.ops), ("POST", r"/locks", self.lock), ("POST", r"/locks/release", self.release),
            ("POST", r"/interference", self.interference), ("POST", r"/printability", self.printability),
            ("POST", r"/dependents", self.dependents), ("POST", r"/export", self.export),
            ("GET", r"/participants", self.participants_route), ("POST", r"/takeover", self.takeover),
            ("GET", r"/tasks", self.tasks_route), ("POST", r"/tasks", self.create_task),
            ("POST", r"/tasks/(?P<tid>[^/]+)/claim", self.claim), ("POST", r"/tasks/(?P<tid>[^/]+)/result", self.result),
            ("POST", r"/messages", self.send), ("GET", r"/messages", self.chat),
            ("GET", r"/inbox", self.inbox), ("POST", r"/inbox/ack", self.ack),
            ("GET", r"/events", self.events), ("GET", r"/context", self.context),
            ("POST", r"/briefs/(?P<bid>[^/]+)/plan", self.brief_plan), ("POST", r"/briefs", self.submit_brief),
            ("POST", r"/reviews", self.report_review),
            ("POST", r"/handoffs/(?P<hid>[^/]+)/accept", self.accept_handoff),
        ]

    # ---------------------------------------------------------------- plumbing

    @property
    def me_id(self):
        return self.identity.get("participant_id")

    @property
    def fence(self):
        return self.identity.get("fence", 0)

    def _call(self, reducer, *args):
        try:
            self.space.call(reducer, *args)
        except SpaceError as e:
            raise _err(e)

    def _f(self, reducer, *args):
        self._call(reducer, self.fence, *args)

    def _rows(self, view, where=""):
        try:
            return self.space.sql(f"SELECT * FROM {view}" + (f" WHERE {where}" if where else ""))
        except SpaceError as e:
            raise _err(e)

    def request(self, method, path, body=None, params=None, timeout=None, raw=False):
        if raw and path.startswith("/artifacts/"):
            return self.artifact_bytes(path.rsplit("/", 1)[1])
        for m, pattern, fn in self.routes:
            match = re.fullmatch(pattern, path)
            if m == method and match:
                kwargs = dict(match.groupdict())
                if method == "GET":
                    kwargs.update(params or {})
                    return fn(**kwargs)
                return fn(body or {}, **kwargs)
        raise HubRequestError(404, {"error": f"No route {method} {path}", "code": "not_found"})

    def get(self, path, **params):
        return self.request("GET", path, params=params)

    def post(self, path, body=None):
        return self.request("POST", path, body or {})

    # ---------------------------------------------------------------- identity

    def me(self):
        rows = self._rows("v_me")
        if not rows:
            raise HubRequestError(401, {"error": "This identity is not a member; run cadswarm setup --invite", "code": "not_member"})
        return rows[0]

    def join(self, workspace_token=None, name=None, kind="agent", client=None, role="member"):
        """Resume this folder's identity at a new fence (the old hub's /join with `resume`)."""
        self._call("rejoin", client or self.cfg.get("client") or "")
        m = self.me()
        self.identity.update({"participant_id": m["id"], "fence": m["fence"], "name": m["name"],
                              "role": m["role"], "kind": m["kind"], "seq": self.latest_seq()})
        return dict(self.identity)

    def heartbeat(self, body):
        self._f("heartbeat", body.get("doing") or "")
        return {"seq": self.latest_seq(), "unread": len(self._unconsumed()), "locks": self._my_locks()}

    def leave(self, body):
        locks = self._my_locks()
        self._f("leave")
        return {"released": locks}

    def log_activity(self, kind, summary, targets=None):
        try:
            self._f("log_activity", kind, summary[:300], [t for t in (targets or []) if t])
        except HubRequestError:
            pass  # presence is best-effort; never block a tool on it

    # ------------------------------------------------------------------ lookups

    def _names(self):
        names = {m["id"]: m["name"] for m in self._rows("v_member")}
        names[0] = "hub"
        return names

    def _my_locks(self):
        return sorted(l["object"] for l in self._rows("v_lock") if l["holder"] == self.me_id)

    def latest_seq(self):
        rows = self._rows("v_event", f"seq > {int(self._seq)}")
        if rows:
            self._seq = max(r["seq"] for r in rows)
        return self._seq

    def participants(self):
        names = self._names()
        locks = {}
        for l in self._rows("v_lock"):
            locks.setdefault(l["holder"], []).append(l["object"])
        return [{"id": m["id"], "name": m["name"], "kind": m["kind"], "client": m["client"], "role": m["role"],
                 "status": m["status"], "doing": m["status_text"], "last_seen": m["last_seen_ms"] / 1000,
                 "locks": sorted(locks.get(m["id"], []))}
                for m in sorted(self._rows("v_member"), key=lambda m: m["id"])
                if m["status"] not in ("left", "taken_over") and names]

    def participants_route(self):
        return {"participants": self.participants()}

    def model(self):
        names = self._names()
        holder = {l["object"]: names.get(l["holder"], str(l["holder"])) for l in self._rows("v_lock")}
        objects = []
        for o in self._rows("v_object"):
            objects.append({"name": o["name"], "label": o["label"], "type": o["type_id"], "rev": o["rev"],
                            "locked_by": holder.get(o["name"]), "bbox": o["bbox"] or None, "volume": o["volume"],
                            "visible": o["visible"], "broken": o["broken"], "uses": o["uses"], "used_by": o["used_by"],
                            "expressions": _json(o["expressions_json"], {}),
                            "placement": _json(o["placement_json"], None)})
        params = {p["alias"]: _num(p["value"]) for p in self._rows("v_param")}
        return {"objects": objects, "params": params, "participants": self.participants(), "seq": self.latest_seq()}

    # ---------------------------------------------------------------------- ops

    def run_op(self, kind, args, expected_revs=None):
        op_id = ticket.new_id("o_")
        self._f("submit_op", op_id, kind, json.dumps(args or {}),
                json.dumps(expected_revs) if expected_revs else "")
        deadline, delay = time.time() + OP_WAIT_SECONDS, 0.5
        while time.time() < deadline:
            rows = self._rows("v_op", f"id = '{op_id}'")
            if rows and rows[0]["status"] == "done":
                return _json(rows[0]["result_json"], {})
            if rows and rows[0]["status"] in ("failed", "rejected"):
                code = rows[0]["error_code"] or "op_error"
                raise HubRequestError(409 if rows[0]["status"] == "rejected" else 422,
                                      {"error": rows[0]["error"], "code": code})
            time.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        raise HubRequestError(504, {"error": f"The host did not run op {op_id} within {OP_WAIT_SECONDS}s; "
                                             "is cadswarm-host running?", "code": "host_timeout"})

    def ops(self, body):
        return self.run_op(body.get("op"), body.get("args"), body.get("expected_revs"))

    def get_object(self, name):
        info = self.run_op("get_object", {"name": name})
        info["locked_by"] = next((self._names().get(l["holder"]) for l in self._rows("v_lock") if l["object"] == name), None)
        return info

    def interference(self, body):
        return self.run_op("interference", {"names": body.get("names"), "min_volume": body.get("min_volume", 1e-3)})

    def printability(self, body):
        args = {"names": body.get("names"), "min_wall": body.get("min_wall", 1.2)}
        return self.run_op("printability", args)

    def dependents(self, body):
        return self.run_op("dependents", {"names": body.get("names", [])})

    def export(self, body):
        r = self.run_op("export", {"format": body.get("format", "step"), "names": body.get("names")})
        return {"file": r["name"], "url": f"/artifacts/{r['hash']}", "objects": r.get("objects", [])}

    def artifact_bytes(self, sha):
        # Read the one blob by hash; v_artifact carries no bytes (plan 00: never pull every export).
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise HubRequestError(404, {"error": "No such export", "code": "not_found"})
        rows = self._rows("v_artifact_blob", f"hash = '{sha}'")
        if not rows or not rows[0]["bytes"]:
            raise HubRequestError(404, {"error": "No such export (or it is too large to store inline)", "code": "not_found"})
        b = rows[0]["bytes"]
        return bytes.fromhex(b) if isinstance(b, str) else bytes(b)

    # -------------------------------------------------------------------- locks

    def lock(self, body):
        names = sorted(set(body.get("names", [])))
        self._f("lock_objects", names)
        return {"locked": names, "all_my_locks": self._my_locks()}

    def release(self, body):
        before = set(self._my_locks())
        self._f("release_objects", sorted(set(body.get("names", []))), bool(body.get("force")))
        return {"released": sorted(before - set(self._my_locks())), "all_my_locks": self._my_locks()}

    def takeover(self, body):
        target = body.get("target")
        self._f("take_over", target, bool(body.get("force")))
        ev = [e for e in self._rows("v_event") if e["kind"] == "taken_over" and e["actor"] == self.me_id]
        data = _json(ev[-1]["data_json"], {}) if ev else {}
        return {"from": data.get("target", target), "locks": data.get("locks", []), "tasks": data.get("tasks", [])}

    # -------------------------------------------------------------------- tasks

    def _task_shape(self, t, names):
        return {"id": t["id"], "title": t["title"], "detail": t["detail"], "objects": t["objects"],
                "status": t["status"], "owner": t["owner"] or None, "created_by": t["created_by"], "rev": t["rev"],
                "result": _json(t["result_json"], None), "assignee": t["assignee"] or None, "brief": t["brief"] or None,
                "owner_name": names.get(t["owner"]) if t["owner"] else None,
                "created_by_name": names.get(t["created_by"]),
                "created_at": t["created_ms"] / 1000, "updated_at": t["updated_ms"] / 1000}

    def tasks(self):
        names = self._names()
        return [self._task_shape(t, names) for t in sorted(self._rows("v_task"), key=lambda t: t["created_ms"])]

    def tasks_route(self):
        return {"tasks": self.tasks()}

    def task(self, tid):
        for t in self.tasks():
            if t["id"] == tid:
                return t
        raise HubRequestError(404, {"error": f"No task {tid}", "code": "unknown_task"})

    def _joined(self, name):
        return any(m["name"].lower() == (name or "").lower() and m["status"] in ("active", "stale")
                   for m in self._rows("v_member"))

    def create_task(self, body):
        tid = ticket.new_id("t_")
        assignee = body.get("assignee") or ""
        self._f("create_task", tid, body.get("title", ""), body.get("detail", ""), body.get("objects") or [],
                assignee, body.get("brief") or "")
        t = self.task(tid)
        if assignee and not self._joined(assignee):
            t["note"] = f"No one named {assignee!r} has joined yet; they will be notified when they do."
        return t

    def claim(self, body, tid):
        self._f("claim_task", tid, int(body.get("expected_rev") or 0))
        return self.task(tid)

    def result(self, body, tid):
        self._f("report_task", tid, body.get("status", "done"), body.get("summary", ""), body.get("evidence") or [])
        return self.task(tid)

    # ----------------------------------------------------------------- messages

    def send(self, body):
        mid = ticket.new_id("m_")
        to = body.get("to") or "all"
        self._f("send_message", mid, to, body.get("body", ""), body.get("kind", "message"), body.get("task_id") or "")
        out = {"id": mid, "to": to}
        if to != "all" and not self._joined(to):
            out["note"] = f"No one named {to!r} has joined yet; the message waits for them."
        return out

    def chat(self, limit=50):
        names = self._names()
        rows = sorted(self._rows("v_message"), key=lambda m: m["created_ms"])[-int(limit):]
        return {"messages": [{"id": m["id"], "from": names.get(m["from"], "?"),
                              "to": "all" if m["to"] == "all" else names.get(int(m["to"].split(":")[1]), m["to"])
                              if m["to"].startswith("member:") else m["to"][1:],
                              "kind": m["kind"], "body": m["body"], "at": m["created_ms"] / 1000} for m in rows]}

    def _unconsumed(self):
        me = self.me_id
        consumed = {r["message_id"] for r in self._rows("v_receipt") if r["member"] == me and r["state"] == "consumed"}
        rank = {"steer": 0, "handoff": 1}
        mine = [m for m in self._rows("v_message")
                if m["to"] in ("all", f"member:{me}") and m["from"] != me and m["id"] not in consumed]
        return sorted(mine, key=lambda m: (rank.get(m["kind"], 2), m["created_ms"]))

    def inbox(self, limit=10):
        names = self._names()
        rows = self._unconsumed()[:int(limit)]
        if rows:
            self._f("mark_delivered", [m["id"] for m in rows])
        return {"messages": [{"id": m["id"], "from": names.get(m["from"], "?"), "to": "all" if m["to"] == "all" else "you",
                              "kind": m["kind"], "body": m["body"], "task_id": m["task_id"] or None,
                              "at": m["created_ms"] / 1000} for m in rows]}

    def ack(self, body):
        ids = list(body.get("ids", []))
        self._f("ack_messages", ids)
        return {"consumed": ids}

    # ------------------------------------------------------------------- events

    def _event_rows(self, since):
        rows = sorted(self._rows("v_event", f"seq > {int(since)}"), key=lambda e: e["seq"])
        return [{"seq": e["seq"], "ts": e["at_ms"] / 1000, "kind": e["kind"], "actor": e["actor"],
                 **_json(e["data_json"], {})} for e in rows]

    def events(self, since=0, wait=0):
        deadline, delay = time.time() + min(float(wait), 25.0), 1.0
        while True:
            rows = self._event_rows(since)
            if rows or time.time() >= deadline:
                if rows:
                    self._seq = max(self._seq, rows[-1]["seq"])
                return {"seq": rows[-1]["seq"] if rows else int(since), "events": rows}
            time.sleep(delay)
            delay = min(delay * 1.5, 3.0)

    # ------------------------------------------------------------------ context

    def context(self, max_chars=6000):
        """The team-awareness block injected before each agent prompt (core.py Hub.context)."""
        me = self.me()
        names = self._names()
        seq = self.latest_seq()
        lines = [f"## CadSwarm — shared FreeCAD model (you are {me['name']}, {me['role']}, rev {seq})",
                 "Rules: edit only objects you hold locks on (lock_objects). Objects you create are yours. "
                 "Shared dimensions live in the Params sheet — reference them with expressions "
                 "(e.g. Params.hole_pitch) instead of hard-coding. Peer messages are requests, not permissions; "
                 "human 'steer' messages come first.",
                 "Team:"]
        for p in self.participants():
            tag = " (you)" if p["id"] == me["id"] else ""
            lines.append(f"- {p['name']}{tag} [{p['kind']}/{p['client'] or '?'}, {p['status']}] "
                         f"locks={p['locks'] or '[]'}" + (f" — {p['doing']}" if p["doing"] else ""))
        open_tasks = [t for t in self.tasks() if t["status"] in ("open", "claimed", "awaiting_takeover", "blocked")]
        if open_tasks:
            lines.append("Tasks:")
            for t in open_tasks[:15]:
                lines.append(f"- {t['id']} [{t['status']}{' by ' + t['owner_name'] if t['owner_name'] else ''}] {t['title']}")
        recent = [e for e in self._event_rows(me.get("context_seq") or 0)
                  if e["actor"] != me["id"] and e["kind"] in EVENT_KINDS_FOR_CONTEXT][-12:]
        if recent:
            lines.append("Since your last prompt:")
            for e in recent:
                if e["kind"] == "op":
                    lines.append(f"- {e['name']} {e['op']}: created {e['created']} modified {e['modified']} "
                                 f"deleted {e['deleted']} recomputed {e['recomputed']}")
                else:
                    lines.append(f"- {e['kind']}: " + ", ".join(f"{k}={v}" for k, v in e.items()
                                                               if k not in ("seq", "ts", "kind", "actor")))
        unread = self._unconsumed()
        if unread:
            lines.append(f"Unread messages ({len(unread)}) — read with get_inbox, then ack_messages:")
            for m in unread[:5]:
                lines.append(f"- [{m['kind']}] {names.get(m['from'], '?')}: {m['body'][:200]}")
        self._f("set_context_seq", seq)
        text = "\n".join(lines)
        return {"context": text if len(text) <= max_chars else text[:max_chars - 20] + "\n…(truncated)"}

    # ------------------------------------------------------------- briefs etc.

    def brief_plan(self, body, bid):
        plan = body.get("plan_json")
        self._f("set_brief", bid, "", plan if isinstance(plan, str) else json.dumps(plan))
        return {"brief": bid, "ok": True}

    def submit_brief(self, body):
        bid = ticket.new_id("b_")
        self._f("submit_brief", bid, body.get("text", ""), body.get("board", ""))
        return {"brief": bid}

    def accept_handoff(self, body, hid):
        self._f("accept_handoff", hid)
        return {"handoff": hid, "accepted": True, "all_my_locks": self._my_locks()}

    def report_review(self, body):
        rid = ticket.new_id("r_")
        self._f("report_review", rid, body["brief_id"], int(body.get("round", 1)), bool(body["passed"]),
                json.dumps(body.get("findings", [])))
        return {"review": rid}


def project_hub(project_dir):
    """SpaceHub for a set-up project folder, using (but not refreshing) its stored identity."""
    cfg = load_json(os.path.join(state_dir(project_dir), "config.json"))
    if not cfg or "db" not in cfg:
        return None, cfg
    ident = load_json(os.path.join(state_dir(project_dir), "identity.json"), {})
    return SpaceHub(cfg, ident), cfg
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_space_hub.py -q`
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```powershell
git add cadswarm/space_hub.py tests/test_space_hub.py
git commit -m "feat(python): SpaceHub answers the old hub paths over SpacetimeDB"
```

- [ ] **Step 6: The routes the beginner UX and the file tools use (added against main `75bcb43`)**

The MCP file tools, the FreeCAD panel and the storage pages call more hub paths than Steps 1–4 cover. Add them now so Task 4 can delete the hub. Every file and storage path becomes a queued op that the host's helper runs (Task 5). Results larger than 32 KB arrive as `{"blob": "<sha256>"}` (plan 02 Task 5, Step 1b) and are read from `v_artifact_blob`. Over `/sql` a `Vec<u8>` is a lowercase hex string.

Append to `tests/test_space_hub.py`:
```python
def finish_ops_with(fake, result):
    def host(query):
        for o in fake.tables.get("v_op", []):
            if o["status"] == "queued":
                o["status"], o["result_json"] = "done", json.dumps(result)
    fake.on_sql = host


def test_file_routes_become_helper_ops(world):
    hub, fake = world
    finish_ops_with(fake, {"path": "notes.md", "version": 1})
    assert hub.post("/project/files/save", {"path": "notes.md", "text": "hi"})["version"] == 1
    reducer, args = fake.calls[-1]
    assert reducer == "submit_op" and args[2] == "save_file" and json.loads(args[3])["text"] == "hi"
    hub.post("/project/files/move", {"from": "a.md", "to": "b.md"})
    assert fake.calls[-1][1][2] == "move_file"
    hub.get("/project/files", prefix="notes")
    assert json.loads(fake.calls[-1][1][3]) == {"prefix": "notes"}


def test_large_results_are_read_from_the_blob_view(world):
    hub, fake = world
    big = {"objects": {"Plate": {"brep": "x" * 50}}}
    fake.tables["v_artifact_blob"] = [{"hash": "ab" * 32, "ws": 1, "bytes": json.dumps(big).encode().hex()}]
    finish_ops_with(fake, {"blob": "ab" * 32})
    assert hub.post("/brep", {"names": ["Plate"]}) == {"objects": big["objects"]}
    assert fake.calls[-1][1][2] == "brep"


def test_raw_download_decodes_base64(world):
    hub, fake = world
    finish_ops_with(fake, {"path": "a.bin", "base64": "AAE="})
    assert hub.request("GET", "/project/files/download?path=a.bin", raw=True) == b"\x00\x01"
    assert json.loads(fake.calls[-1][1][3]) == {"path": "a.bin", "raw": True}


def test_health_is_ok_while_a_host_member_is_active(world):
    hub, fake = world
    assert hub.get("/health")["ok"] is False
    fake.tables["v_member"].append(member(9, "host", kind="host"))
    assert hub.get("/health")["ok"] is True


def test_storage_settings_are_host_only(world):
    hub, _ = world
    with pytest.raises(HubRequestError) as e:
        hub.post("/project/storage/settings", {"limit_gb": 5})
    assert e.value.code == "host_only"
```
The fake's `sql` must also filter `WHERE hash = '…'`. Add this branch to `FakeSpace.sql`:
```python
        if "WHERE hash =" in query:
            want = query.rsplit("=", 1)[1].strip().strip("'")
            rows = [r for r in rows if r["hash"] == want]
```

Run: `python -m pytest tests/test_space_hub.py -q`. Expected: the five new tests fail with `No route`.

Add to `SpaceHub.routes`:
```python
            ("GET", r"/health", self.health), ("POST", r"/join", self.join_route), ("POST", r"/brep", self.brep),
            ("GET", r"/project", self.project), ("GET", r"/project/files", self.list_files),
            ("POST", r"/project/files/read", self.read_file), ("POST", r"/project/files/save", self.save_file),
            ("POST", r"/project/files/folder", self.make_folder), ("POST", r"/project/files/move", self.move_file),
            ("POST", r"/project/files/delete", self.delete_file), ("POST", r"/project/files/history", self.file_history),
            ("POST", r"/project/files/restore", self.restore_file), ("GET", r"/project/storage", self.storage_status),
            ("POST", r"/project/storage/prune", self.prune_storage),
            ("POST", r"/project/storage/settings", self.storage_settings),
            ("POST", r"/project/snapshot", self.snapshot),
```
In `request`, before the route loop, handle the raw download:
```python
        if raw and path.startswith("/project/files/download"):
            from urllib.parse import parse_qs, urlsplit
            q = {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}
            q.update(params or {})
            r = self.run_op("read_file", {"path": q.get("path"), "raw": True})
            return base64.b64decode(r["base64"])
```
(add `import base64` at the top). In `run_op`, read results through `_result`:
```python
            if rows and rows[0]["status"] == "done":
                return self._result(rows[0]["result_json"])
```
```python
    def _result(self, result_json):
        r = _json(result_json, {})
        if isinstance(r, dict) and set(r) == {"blob"}:
            rows = self._rows("v_artifact_blob", f"hash = '{r['blob']}'")
            if not rows:
                raise HubRequestError(410, {"error": "That result expired; run it again", "code": "expired"})
            b = rows[0]["bytes"]
            r = json.loads(bytes.fromhex(b) if isinstance(b, str) else bytes(b))
        return r

    def health(self):
        hosts = [m for m in self._rows("v_member") if m["kind"] == "host" and m["status"] == "active"]
        return {"ok": bool(hosts), "seq": self.latest_seq(), "freecad": None, "script_sandbox": None}

    def join_route(self, body):
        return self.join(None, body.get("name"), body.get("kind", "agent"), body.get("client"), body.get("role"))

    def brep(self, body):
        r = self.run_op("brep", {"names": body.get("names")})
        names = self._names()
        holder = {l["object"]: names.get(l["holder"]) for l in self._rows("v_lock")}
        for n, o in (r.get("objects") or {}).items():
            if o is not None:
                o["locked_by"] = holder.get(n)
        return {"objects": r.get("objects") or {}}

    def project(self):
        return {"root": "", "folders": {"files": "files", "exports": "exports", "snapshots": "snapshots"},
                "host": True}

    def list_files(self, prefix=""):
        return self.run_op("list_files", {"prefix": prefix} if prefix else {})

    def read_file(self, body):
        return self.run_op("read_file", {"path": body.get("path")})

    def save_file(self, body):
        return self.run_op("save_file", {k: body.get(k) for k in ("path", "text", "base64", "if_exists", "expected_version")
                                         if body.get(k) is not None})

    def make_folder(self, body):
        return self.run_op("make_folder", {"path": body.get("path")})

    def move_file(self, body):
        return self.run_op("move_file", {"from": body.get("from"), "to": body.get("to")})

    def delete_file(self, body):
        return self.run_op("delete_file", {"path": body.get("path")})

    def file_history(self, body):
        return self.run_op("file_history", {"path": body.get("path")})

    def restore_file(self, body):
        return self.run_op("restore_file", {k: body.get(k) for k in ("path", "version", "expected_version")
                                            if body.get(k) is not None})

    def storage_status(self):
        return self.run_op("storage_status", {})

    def prune_storage(self, body):
        return self.run_op("prune_storage", dict(body or {}))

    def snapshot(self, body):
        return self.run_op("snapshot", {"label": body.get("label", "")})

    def storage_settings(self, body):
        """Only the computer hosting the team changes its storage limits: it writes them directly."""
        host_dir = self.cfg.get("host_dir")
        if not host_dir:
            raise HubRequestError(403, {"error": "Only the computer hosting the team can change its storage limits.",
                                        "code": "host_only"})
        import sqlite3
        from .files import ProjectFiles
        from .paths import project_layout
        lay = project_layout(host_dir)
        db = sqlite3.connect(lay["db"], isolation_level=None)
        db.row_factory = sqlite3.Row
        files = ProjectFiles(lay, db)
        files.storage.update(body or {})
        return files.storage.status()
```
`cfg["host_dir"]` is set only by the FreeCAD guide on the hosting computer (Task 6). `storage_settings` imports `cadswarm.files`, which Task 4 moves out of `cadswarm/hub/`. Until then, guard the import, or do this step after Task 4 Step 1.

Run: `python -m pytest tests/test_space_hub.py -q`. Expected: `14 passed`.

- [ ] **Step 7: Commit**

```powershell
git add cadswarm/space_hub.py tests/test_space_hub.py
git commit -m "feat(python): SpaceHub answers file, storage, brep and health routes; reads spilled results"
```

---

### Task 4: Setup by invite, MCP server and prompt hook on SpaceHub; retire the FastAPI hub

**Files:**
- Modify: `cadswarm/cli.py`, `cadswarm/agent_setup.py`, `cadswarm/mcp_server.py`, `cadswarm/prompt_hook.py`, `cadswarm/config.py`, `cadswarm/client.py`, `pyproject.toml`
- Move: `cadswarm/hub/files.py` → `cadswarm/files.py`, `cadswarm/hub/storage.py` → `cadswarm/storage.py`, `cadswarm/hub/worker_client.py` → `cadswarm/worker_client.py`
- Delete: `cadswarm/hub/` (app, core, `__init__`), `tests/test_core.py` (ported to `rules/`). Keep `tests/test_files.py`, `tests/test_storage.py`, `tests/test_worker.py`, `tests/test_sandbox.py` and fix their imports; rewrite `tests/test_integration.py` in Task 8.
- Test: `tests/test_setup_space.py`

**Interfaces:**
- Consumes: `SpaceHub`, `project_hub`, `SpaceClient.new_identity`, `ticket.decode`.
- Produces:
  - `agent_setup.redeem(t: dict, name: str, client: str) -> dict`, which returns the identity `{token, participant_id, fence, name, role, kind}`.
  - `agent_setup.write_agent_config(project, client, invite: dict, name, python=None, fresh=False) -> list[str]`, where `invite` is a decoded ticket. It redeems only when the folder has no identity yet, writes `.cadswarm/config.json` as `{uri, db, name, client, role}`, and dispatches to `setup_claude` / `setup_codex` / `setup_droid`.
  - CLI: `cadswarm setup --client claude|codex|droid --invite CODE --name NAME [--dir .] [--fresh]` and `cadswarm brief --dir DIR [--board ID] TEXT`. `cadswarm hub` and `cadswarm invite` are removed; `cadswarm-host invite` and the FreeCAD guide mint invites.
  - New MCP tools: `create_task(..., brief)`, `set_brief_plan`, `check_printability`, `accept_handoff`, `report_review`. Every tool call logs activity with its target objects.

- [ ] **Step 1: Move the modules that survive and delete the hub**

```powershell
git mv cadswarm/hub/files.py cadswarm/files.py
git mv cadswarm/hub/storage.py cadswarm/storage.py
git mv cadswarm/hub/worker_client.py cadswarm/worker_client.py
git rm -r cadswarm/hub tests/test_core.py
```
- In `cadswarm/files.py` keep `from .storage import FileError, Storage`.
- In `cadswarm/worker_client.py`, change `from ..config import` to `from .config import`.
- Update the imports in `tests/test_files.py`, `tests/test_storage.py`, `tests/test_worker.py` and `tests/test_sandbox.py`: `cadswarm.hub.files` becomes `cadswarm.files`, and likewise for `storage` and `worker_client`.
- In `pyproject.toml`, drop `fastapi` and `uvicorn` from the dependencies, keeping `mcp>=2.3`.
- In `cadswarm/config.py`, set `HEARTBEAT_SECONDS = 15`, delete `DEFAULT_PORT`, and remove `"fastapi"` and `"uvicorn"` from `paths.DEPENDENCIES`.

Run `python -m pytest tests/test_files.py tests/test_storage.py -q`. Expected: everything that passed before still passes.

- [ ] **Step 2: Write the failing setup test**

`tests/test_setup_space.py`:
```python
"""Setting up an agent folder from an invite redeems once and writes the SpacetimeDB config."""
import json
import os

from cadswarm import agent_setup


def test_write_agent_config_redeems_once_and_writes_the_space_config(tmp_path, monkeypatch):
    calls = []

    def fake_redeem(t, name, client):
        calls.append((t["token"], name, client))
        return {"token": "tok", "participant_id": 4, "fence": 1, "name": name, "role": "member", "kind": "agent"}

    monkeypatch.setattr(agent_setup, "redeem", fake_redeem)
    t = {"v": 1, "uri": "http://127.0.0.1:3000", "db": "cadswarm", "token": "ab" * 32, "kind": "agent",
         "role": "member", "host_addr": ""}
    agent_setup.write_agent_config(str(tmp_path), "claude", t, "alice-claude", python="python")
    agent_setup.write_agent_config(str(tmp_path), "claude", t, "alice-claude", python="python")
    assert calls == [("ab" * 32, "alice-claude", "claude")]
    cfg = json.load(open(tmp_path / ".cadswarm" / "config.json"))
    assert cfg == {"uri": "http://127.0.0.1:3000", "db": "cadswarm", "name": "alice-claude",
                   "client": "claude", "role": "member"}
    ident = json.load(open(tmp_path / ".cadswarm" / "identity.json"))
    assert ident["participant_id"] == 4 and ident["token"] == "tok"
    assert open(tmp_path / ".cadswarm" / ".gitignore").read() == "*\n"
    assert os.path.exists(tmp_path / ".mcp.json")


def test_droid_gets_a_factory_mcp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_setup, "redeem", lambda t, n, c: {"token": "t", "participant_id": 1, "fence": 1,
                                                                 "name": n, "role": "member", "kind": "agent"})
    t = {"v": 1, "uri": "u", "db": "d", "token": "x", "kind": "agent", "role": "member", "host_addr": ""}
    written = agent_setup.write_agent_config(str(tmp_path), "droid", t, "bob-droid", python="python")
    mcp = json.load(open(tmp_path / ".factory" / "mcp.json"))
    assert mcp["mcpServers"]["cadswarm"]["args"] == ["-m", "cadswarm.mcp_server"]
    assert str(tmp_path / ".factory" / "mcp.json") in written
```
Run: `python -m pytest tests/test_setup_space.py -q`. Expected: FAIL. `write_agent_config` still has the old `(hub, token, ...)` signature, and `redeem` is missing.

- [ ] **Step 3: Rewrite `write_agent_config` and add `redeem` and `setup_droid` in `cadswarm/agent_setup.py`**

Add `"droid"` to `CLIENTS` (`{"exe": "droid", "label": "Factory Droid"}`). Keep `upsert_block`, `hook_command`, `setup_claude`, `setup_codex`, the Codex trust helpers, `find_agent_cli` and `open_agent_terminal` unchanged. Replace `write_agent_config` with:
```python
def redeem(t, name, client):
    """Create a SpacetimeDB identity and become a member with the invite (single use)."""
    from .space import SpaceClient
    ident = SpaceClient.new_identity(t["uri"])
    space = SpaceClient(t["uri"], t["db"], ident["token"])
    space.call("redeem_invite", t["token"], name, client, "")
    me = space.sql("SELECT * FROM v_me")[0]
    return {"token": ident["token"], "participant_id": me["id"], "fence": me["fence"], "name": me["name"],
            "role": me["role"], "kind": me["kind"]}


def setup_droid(project, python):
    path = os.path.join(project, ".factory", "mcp.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg = _read_config(path)
    _dict_at(cfg, "mcpServers", path)["cadswarm"] = {
        "type": "stdio", "command": python, "args": ["-m", "cadswarm.mcp_server"],
        "env": {"CADSWARM_PROJECT": project}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    upsert_block(os.path.join(project, "AGENTS.md"), AGENT_DOC)
    return [path, os.path.join(project, "AGENTS.md")]


def write_agent_config(project, client, invite, name, python=None, fresh=False):
    """Everything an AI helper in `project` needs to join the team. `invite` is a decoded ticket.
    Redeems the invite only if this folder has no identity yet (invites work once)."""
    if client not in CLIENTS:
        raise ValueError(f"client must be one of {sorted(CLIENTS)}")
    python = python or sys.executable
    project = os.path.abspath(project)
    os.makedirs(project, exist_ok=True)
    ident_path = os.path.join(state_dir(project), "identity.json")
    if fresh and os.path.exists(ident_path):
        os.remove(ident_path)
    with open(os.path.join(state_dir(project), ".gitignore"), "w") as f:
        f.write("*\n")  # identity.json holds a SpacetimeDB token
    if not load_json(ident_path):
        save_json(ident_path, redeem(invite, name, client))
    save_json(os.path.join(state_dir(project), "config.json"), {
        "uri": invite["uri"], "db": invite["db"], "name": name, "client": client,
        "role": invite.get("role", "member")})
    writers = {"claude": setup_claude, "codex": setup_codex, "droid": setup_droid}
    return writers[client](project, python)
```
`AGENT_DOC` is the existing instructions block constant; if the file names it differently, use that name. `state_dir` must create the folder: check `client.state_dir` and add `os.makedirs(..., exist_ok=True)` if it doesn't.

- [ ] **Step 4: Rewrite the CLI**

In `cadswarm/cli.py`, delete `cmd_hub`, `cmd_invite` and `PROJECT_README`. `PROJECT_README` moves to `hosting.py` in Task 6. Replace `cmd_setup` and `main`:
```python
def cmd_setup(a):
    from .invite import InviteError, read_invite
    try:
        t = read_invite(a.invite)
        written = write_agent_config(os.path.abspath(a.dir), a.client, t, a.name, sys.executable, a.fresh)
    except (InviteError, ValueError) as e:
        raise SystemExit(f"cadswarm: {e}")
    except Exception as e:  # SpaceError from redeeming: used, expired or unreachable
        raise SystemExit(f"cadswarm: could not join with this invite: {e}")
    print(f"CadSwarm set up for {a.client} as '{a.name}' in {os.path.abspath(a.dir)}")
    for p in written:
        print(f"  wrote {p}")


def cmd_brief(a):
    from .space_hub import project_hub
    hub, _cfg = project_hub(os.path.abspath(a.dir))
    if not hub:
        raise SystemExit("cadswarm: run `cadswarm setup --invite ...` in that folder first")
    hub.join()
    print(hub.post("/briefs", {"text": a.text, "board": a.board or ""})["brief"])


def main(argv=None):
    p = argparse.ArgumentParser(prog="cadswarm")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="join with an invite and connect an agent CLI in a folder")
    s.add_argument("--client", choices=["claude", "codex", "droid"], required=True)
    s.add_argument("--invite", required=True, help="a cadswarm-2: code, a ticket, or a phone link")
    s.add_argument("--name", required=True)
    s.add_argument("--dir", default=".")
    s.add_argument("--fresh", action="store_true", help="forget the stored identity and join as a new member")
    s.set_defaults(fn=cmd_setup)
    b = sub.add_parser("brief", help="submit a brief")
    b.add_argument("--dir", default=".")
    b.add_argument("--board")
    b.add_argument("text")
    b.set_defaults(fn=cmd_brief)
    w = sub.add_parser("install-workbench", help="install the FreeCAD workbench")
    w.set_defaults(fn=cmd_install_workbench)
    a = p.parse_args(argv)
    a.fn(a)
```
Keep the Codex trust and `/hooks` guidance that the old `cmd_setup` printed: print it when `a.client == "codex"`.

- [ ] **Step 5: Switch the MCP server to SpaceHub**

In `cadswarm/mcp_server.py`:
- Replace the import of `HubClient` with `from .space_hub import SpaceHub`. Keep `HubRequestError`, `find_project_dir`, `load_json`, `save_json` and `state_dir` from `.client`.
- In `Session.__init__`, set `self.client = SpaceHub(self.cfg, load_json(self.ident_path, {}))`.
- Replace `Session.connect` with:
```python
    def connect(self):
        ident = self.client.join(None, self.cfg["name"], "agent", self.cfg.get("client"), self.cfg.get("role", "member"))
        save_json(self.ident_path, {**load_json(self.ident_path, {}), **ident})
        return ident
```
- Replace `call` and `op` with versions that log activity first:
```python
def call(method, path, body=None, doing=None, targets=None, **params):
    if S.dead:
        return {"error": f"This agent session can no longer act: {S.dead}", "code": "inactive"}
    if doing:
        S.doing = doing
        S.client.log_activity("tool_call", doing, targets)
    try:
        if method == "GET":
            return S.client.get(path, **params)
        return S.client.post(path, body)
    except HubRequestError as e:
        if e.code in ("fence_stale", "taken_over"):
            S.dead = str(e)
        return {"error": str(e), **{k: v for k, v in e.body.items() if k != "error"}}


def op(name, args, doing, expected_revs=None):
    targets = [args.get(k) for k in ("name", "base", "tool") if isinstance(args.get(k), str)]
    return call("POST", "/ops", {"op": name, "args": {k: v for k, v in args.items() if v is not None},
                                 "expected_revs": expected_revs}, doing=doing, targets=targets)
```
- Pass `targets=names` from `lock_objects` and `check_interference`.
- The file tools and `storage_status` keep their paths; SpaceHub answers them (Task 3).
- `export_model` keeps its shape: the `url` it returns is now `/artifacts/<sha256>`.
- Replace `create_task` and add the new tools after `take_over`:
```python
@mcp.tool()
def create_task(title: str, detail: str = "", objects: list[str] | None = None,
                assignee: str | None = None, brief: str | None = None) -> dict:
    """Create scoped work (optionally assigned to a teammate by name, who is notified). Pass `brief`
    when the task belongs to a phone brief, so the host can track the brief's progress."""
    return call("POST", "/tasks", {"title": title, "detail": detail, "objects": objects,
                                   "assignee": assignee, "brief": brief}, doing=f"planning {title}")


@mcp.tool()
def set_brief_plan(brief_id: str, plan_json: dict | str) -> dict:
    """Record the plan for a brief: {"parts": [{"name", "task", "summary"}]}. Planners call this last."""
    return call("POST", f"/briefs/{brief_id}/plan", {"plan_json": plan_json}, doing="writing the plan")


@mcp.tool()
def check_printability(names: list[str] | None = None, min_wall: float = 1.2) -> dict:
    """Can these parts be 3D printed? Checks each part against the printer bed, that it is a closed
    solid, and lists suspected walls thinner than `min_wall` mm."""
    return call("POST", "/printability", {"names": names, "min_wall": min_wall},
                doing="checking printability", targets=names)


@mcp.tool()
def accept_handoff(handoff_id: str) -> dict:
    """Pick up a person's handed-off work: their locked parts and in-progress tasks become yours.
    Call this first when your instructions name a handoff id."""
    return call("POST", f"/handoffs/{handoff_id}/accept", {}, doing="picking up handed-off work")


@mcp.tool()
def report_review(brief_id: str, round: int, passed: bool, findings: list[dict]) -> dict:
    """Reviewer only: record the review verdict for a brief. findings: [{"object", "problem", "task_id"}]."""
    return call("POST", "/reviews", {"brief_id": brief_id, "round": round, "passed": passed,
                                     "findings": findings}, doing="reporting the review")
```
- In `main`, change the error message's `S.cfg['hub']` to `S.cfg['uri']`.

- [ ] **Step 6: Switch the prompt hook**

In `cadswarm/prompt_hook.py`, replace the `try:` body after `start = ...` with:
```python
    try:
        from cadswarm.client import find_project_dir
        from cadswarm.space_hub import project_hub
        project = find_project_dir(start or os.getcwd())
        if not project:
            return
        hub, _cfg = project_hub(project)
        if not hub or not hub.identity.get("participant_id"):
            return  # the MCP server has not joined yet
        hub.space.timeout = 1.8
        text = hub.get("/context")["context"]
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text[:6000] + "\n")
        sys.stdout.flush()
    except Exception:
        return
```
The hook never calls `rejoin`. It reuses the fence the MCP server stored (plan 00).

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -q --ignore=tests/test_integration.py`
Expected:
- The new tests pass, and `test_files`, `test_storage`, `test_worker` and `test_sandbox` still pass.
- `grep -rn "cadswarm.hub\|from .hub\|HubClient" cadswarm tests` finds only `client.py` and the GUI. `HubClient` stays until Task 6.

- [ ] **Step 8: Commit**

```powershell
git add -A cadswarm tests pyproject.toml
git commit -m "feat(python): setup by invite; MCP server and prompt hook on SpacetimeDB; retire the FastAPI hub"
```

---

### Task 5: The host's Python helper: project files and storage

The host runs file and storage ops in this helper (plan 02 Task 5, Step 1b). It speaks the FreeCAD worker's JSON-lines protocol, uses `ProjectFiles` and `Storage` unchanged, and keeps their SQLite file history in `.cadswarm-team/hub.sqlite`, just as the hub did. Plan 05 Task 6 later adds the Elastic handlers to the same `HANDLERS` table.

**Files:**
- Create: `cadswarm/helper.py`, `tests/test_helper_files.py`
- Modify: `cadswarm/config.py` (add `helper_script()`)

**Interfaces:**
- Consumes: `files.ProjectFiles`, `files.unique_path`, `files.safe_label`, `storage.FileError`, `paths.project_layout`.
- Produces:
  - Running `python <path>/cadswarm/helper.py <project_dir>` prints `{"ready": true}`, then answers `{"id", "op", "args"}` lines with `{"id", "ok": true, "result"}` or `{"id", "ok": false, "code", "error"}`.
  - `helper.HANDLERS: dict[str, Callable[[Project, dict], dict]]`, `helper.Project(root, clock=time.time)`, `helper.handle(project, request) -> dict`.
  - `cadswarm.config.helper_script() -> str`, the absolute path of `helper.py`.
  - `args["_actor"] = {"id", "name", "kind"}` is supplied by the host.
- Ops:
  - Reads: `list_files {prefix}`, `read_file {path, raw?}` (`raw` returns `{"path", "base64"}`), `file_history {path}`, `storage_status {}`.
  - Writes: `save_file {path, text?, base64?, if_exists, expected_version?}`, `make_folder {path}`, `move_file {from, to}`, `delete_file {path}`, `restore_file {path, version, expected_version?}`, `snapshot {label}`, `prune_storage {...}` (people only).

- [ ] **Step 1: Write the failing tests**

`tests/test_helper_files.py`:
```python
"""The helper answers the hub's old file and storage calls in the worker's reply format."""
import base64
import json
import subprocess
import sys

from cadswarm import helper
from cadswarm.config import helper_script

HUMAN = {"id": 3, "name": "carol", "kind": "human"}
AGENT = {"id": 4, "name": "alice-claude", "kind": "agent"}


def ask(project, op, actor=AGENT, **args):
    return helper.handle(project, {"id": "1", "op": op, "args": {**args, "_actor": actor}})


def test_save_read_list_and_history(tmp_path):
    p = helper.Project(str(tmp_path))
    r = ask(p, "save_file", path="notes/plan.md", text="v1")
    assert r["ok"] and r["result"]["path"] == "notes/plan.md" and r["result"]["version"] == 1
    assert ask(p, "read_file", path="NOTES/plan.md")["result"]["text"] == "v1"  # case-insensitive keys
    ask(p, "save_file", path="notes/plan.md", text="v2", if_exists="replace")
    assert [v["version"] for v in ask(p, "file_history", path="notes/plan.md")["result"]["versions"]][:2] == [2, 1]
    names = json.dumps(ask(p, "list_files")["result"])
    assert "plan.md" in names


def test_raw_read_returns_base64(tmp_path):
    p = helper.Project(str(tmp_path))
    ask(p, "save_file", path="a.bin", base64=base64.b64encode(b"\x00\x01").decode())
    r = ask(p, "read_file", path="a.bin", raw=True)["result"]
    assert base64.b64decode(r["base64"]) == b"\x00\x01"


def test_errors_keep_their_codes_and_never_raise(tmp_path):
    p = helper.Project(str(tmp_path))
    r = ask(p, "read_file", path="../escape.txt")
    assert r["ok"] is False and r["code"]
    assert ask(p, "nope")["code"] == "unknown_op"


def test_only_people_can_free_up_space(tmp_path):
    p = helper.Project(str(tmp_path))
    assert ask(p, "prune_storage", actor=AGENT)["code"] == "humans_only"
    assert ask(p, "prune_storage", actor=HUMAN)["ok"] is True
    assert "limit" in json.dumps(ask(p, "storage_status")["result"])


def test_snapshot_copies_the_model(tmp_path):
    p = helper.Project(str(tmp_path))
    (tmp_path / "model.FCStd").write_bytes(b"PK fake model")
    r = ask(p, "snapshot", label="before lid")["result"]
    assert r["file"].startswith("snapshots/") and r["file"].endswith(".FCStd")
    assert (tmp_path / r["file"]).read_bytes() == b"PK fake model"


def test_runs_as_a_script_with_the_worker_protocol(tmp_path):
    proc = subprocess.Popen([sys.executable, helper_script(), str(tmp_path)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    assert json.loads(proc.stdout.readline()) == {"ready": True}
    proc.stdin.write(json.dumps({"id": "7", "op": "list_files", "args": {}}) + "\n")
    proc.stdin.flush()
    reply = json.loads(proc.stdout.readline())
    proc.stdin.close()
    proc.wait(timeout=10)
    assert reply["id"] == "7" and reply["ok"] is True
```
Run: `python -m pytest tests/test_helper_files.py -q`. Expected: `ImportError`.

Before implementing, read the real return shapes of `ProjectFiles.save`, `read`, `listing`, `history`, `restore`, `move` and `delete`, and of `Storage.status` and `prune` (in `cadswarm/files.py` and `cadswarm/storage.py`). Adjust the assertions above if a key is named differently (for example `versions`); don't change those modules.

- [ ] **Step 2: Implement `cadswarm/helper.py`**

```python
"""Host-side helper for ops that are not FreeCAD geometry: project files and storage (and the
Elastic lookups added by plan 05). The Rust host drives it like the FreeCAD worker:

    python cadswarm/helper.py <project_dir>

First line out is {"ready": true}; then one request per line {"id", "op", "args"} and one reply per
line {"id", "ok": true, "result"} or {"id", "ok": false, "code", "error"}. A bad request never kills
it. The host adds args["_actor"] = {"id", "name", "kind"} for authorship and people-only actions.
"""
import base64
import json
import os
import shutil
import sqlite3
import sys
import time

if __package__ in (None, ""):  # run as a script by the host: make `cadswarm` importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cadswarm.files import ProjectFiles, safe_label, unique_path  # noqa: E402
from cadswarm.paths import project_layout  # noqa: E402
from cadswarm.storage import FileError  # noqa: E402


class HelperError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Project:
    def __init__(self, root, clock=time.time):
        self.layout = project_layout(root)
        os.makedirs(self.layout["internal"], exist_ok=True)
        for k in ("files", "exports", "snapshots"):
            os.makedirs(self.layout[k], exist_ok=True)
        self.db = sqlite3.connect(self.layout["db"], check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.files = ProjectFiles(self.layout, self.db, clock)
        self.clock = clock


def _author(a):
    return (a.get("_actor") or {}).get("name") or "someone"


def _people_only(a, what):
    if (a.get("_actor") or {}).get("kind") != "human":
        raise HelperError("humans_only", f"Only a person can {what}. Ask someone on the team to do it.")


def read_file(p, a):
    if a.get("raw"):
        clean, data = p.files.raw(a.get("path"))
        return {"path": clean, "base64": base64.b64encode(data).decode()}
    return p.files.read(a.get("path"))


def snapshot(p, a):
    model = p.layout["model"]
    if not os.path.exists(model):
        raise HelperError("not_found", "There is no model to snapshot yet.")
    p.files.storage.check_add(os.path.getsize(model))
    stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(p.clock()))
    dest = unique_path(p.layout["snapshots"], f"{stamp} {safe_label(a.get('label', ''), 'snapshot')}.FCStd")
    shutil.copy2(model, dest)  # the host runs one op at a time, and the worker saved before acknowledging
    rel = os.path.relpath(dest, p.layout["root"]).replace("\\", "/")
    return {"file": rel, "size": os.path.getsize(dest)}


def prune_storage(p, a):
    _people_only(a, "free up space")
    return p.files.storage.prune({k: v for k, v in a.items() if k != "_actor"})


HANDLERS = {
    "list_files": lambda p, a: p.files.listing(a.get("prefix", "")),
    "read_file": read_file,
    "file_history": lambda p, a: p.files.history(a.get("path")),
    "save_file": lambda p, a: p.files.save(a.get("path"), _author(a), a.get("text"), a.get("base64"),
                                           a.get("if_exists", "error"), a.get("expected_version")),
    "make_folder": lambda p, a: p.files.make_folder(a.get("path")),
    "move_file": lambda p, a: p.files.move(a.get("from"), a.get("to"), _author(a)),
    "delete_file": lambda p, a: p.files.delete(a.get("path"), _author(a)),
    "restore_file": lambda p, a: p.files.restore(a.get("path"), a.get("version"), _author(a),
                                                 a.get("expected_version")),
    "snapshot": snapshot,
    "storage_status": lambda p, a: p.files.storage.status(),
    "prune_storage": prune_storage,
}


def handle(project, req):
    rid, op, args = req.get("id"), req.get("op"), req.get("args") or {}
    fn = HANDLERS.get(op)
    if fn is None:
        return {"id": rid, "ok": False, "code": "unknown_op", "error": f"The helper has no op {op!r}"}
    try:
        return {"id": rid, "ok": True, "result": fn(project, args)}
    except FileError as e:
        return {"id": rid, "ok": False, "code": e.code, "error": str(e)}
    except HelperError as e:
        return {"id": rid, "ok": False, "code": e.code, "error": str(e)}
    except Exception as e:  # report, never die: the host keeps one helper for the whole session
        return {"id": rid, "ok": False, "code": "op_error", "error": f"{type(e).__name__}: {e}"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    project = Project(argv[0] if argv else os.getcwd())
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except ValueError:
            req = {"id": None, "op": None}
        print(json.dumps(handle(project, req)), flush=True)


if __name__ == "__main__":
    main()
```
`FileError` may carry its code under a different attribute name. Read `storage.FileError.__init__(status, code, message, **data)` and use that attribute; it is `code` there. Also return `e.data` merged into the reply if it is non-empty, so details such as `current_version` reach the agent.

In `cadswarm/config.py`, add:
```python
def helper_script():
    """The host runs this file with any Python 3.11+ (stdlib only) to answer file and storage ops."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "helper.py")
```

- [ ] **Step 3: Run the tests**

Run: `python -m pytest tests/test_helper_files.py tests/test_files.py tests/test_storage.py -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```powershell
git add cadswarm/helper.py cadswarm/config.py tests/test_helper_files.py
git commit -m "feat(python): host-side helper answers project file and storage ops"
```

---

### Task 6: The FreeCAD guide on SpacetimeDB (keep the beginner UX)

The beginner setup from `fddfa28` (FreeCAD wizard and panel) stays the way people meet CadSwarm. Underneath, it starts `cadswarm-host` (and `spacetime start` in local mode) instead of the hub, it uses `cadswarm-2:` invite codes, and it talks to SpacetimeDB through `SpaceHub`. Do this task after M1 works end to end, since the demo path is phone, host and agents. Read `cadswarm/gui/wizard.py`, `cadswarm/gui/panel.py`, `cadswarm/hosting.py` and `cadswarm/invite.py` before starting.

**Files:**
- Modify: `cadswarm/invite.py`, `cadswarm/hosting.py`, `cadswarm/space_hub.py`, `cadswarm/gui/wizard.py`, `cadswarm/gui/panel.py`, `cadswarm/client.py`, `cadswarm_launch.py`
- Test: `tests/test_invite_v2.py`, `tests/test_hosting_space.py`; update `tests/test_setup.py` and `tests/test_setup_hardening.py`

**Interfaces:**
- `invite.make_invite(ticket: dict, project="") -> str` returns `"cadswarm-2:" + base64url(json(ticket + {"p": project}))`.
- `invite.read_invite(text) -> dict` returns the ticket dict plus `project`. It stays lenient (quotes, wrapping, invisible characters, trailing chat). It also accepts a bare base32 ticket and a phone link containing `#t=`. A `cadswarm-1:` code raises `InviteError("This invite is from an older CadSwarm. Ask your teammate for a new one.")`.
- `SpaceHub.create_invite(kind="human", role="member", ttl=86400, project="") -> str` returns a fresh single-use code. Any member may mint human or agent invites; the module enforces that only the host mints host and reviewer invites.
- `hosting.find_tool(name, env) -> str | None` looks at the `env` variable, then PATH, then the usual install folders: `%LOCALAPPDATA%\SpacetimeDB` for `spacetime`, and `~/.cargo/bin` and the repo's `host/target/release` for `cadswarm-host`.
- `hosting.start_team(python, data_dir, mode, db) -> dict` returns `{"host_pid", "spacetime_pid"}`. It runs `cadswarm-host init` once, then `cadswarm-host run`, both in the background with no window and with output logged to `.cadswarm-team/host.log`. In local mode it first starts `spacetime start --listen-addr 127.0.0.1:3000` if nothing answers on that port, and publishes the module from `CADSWARM_MODULE_WASM` or the repo's built wasm.
- `hosting.team_ready(data_dir) -> bool` reads `.cadswarm-team/host-status.json`, written by the host (plan 02 Task 5, Step 1b), and checks its pid is alive.
- `hosting.stop_team(info) -> bool` stops only processes whose command line names `cadswarm-host` or `spacetime`. It is never a blanket kill.

- [ ] **Step 1: Invite codes v2**

Done in Task 2, Step 6.

- [ ] **Step 2: Minting invites from Python**

Add to `SpaceHub`, with a test in `tests/test_space_hub.py` that checks the reducer call `("create_invite", (fence, sha256(token), "agent", "member", 86400))` and that the code decodes back to a ticket with this hub's uri and db:
```python
    def create_invite(self, kind="human", role="member", ttl=86400, project=""):
        from .invite import make_invite
        token = ticket.new_token()
        self._f("create_invite", ticket.hash_token(token), kind, role, int(ttl))
        return make_invite({"v": 1, "uri": self.cfg["uri"], "db": self.cfg["db"], "token": token,
                            "kind": kind, "role": role, "host_addr": ""}, project)
```

- [ ] **Step 3: Hosting starts cadswarm-host**

Write `tests/test_hosting_space.py`, mocking `subprocess.Popen` and `subprocess.run`. Test that:
- `find_tool` prefers the env var.
- `start_team` in maincloud mode starts only `cadswarm-host` (init when `host.json` is missing, then run) and never `spacetime start`.
- In local mode it starts `spacetime start --listen-addr 127.0.0.1:3000` only when nothing answers on the port.
- `team_ready` is False until `host-status.json` says `ready` with a live pid.

Then replace the following in `hosting.py`, keeping `deps_ready`, `install_deps`, `local_ips`, `lan_ip`, `free_port`, `pid_alive` and `_command_line`:

| Old | New |
|---|---|
| `start_hub` | `start_team` |
| `wait_healthy` | `wait_ready(data_dir, timeout, pids)` (same messages and `log_tail`) |
| `health` | `team_ready` |
| `is_our_hub` | `is_our_process(pid, names=("cadswarm-host", "spacetime"))` |
| `stop_hub` | `stop_team` |
| `pid_on_port` | `pid_on_port`, now matching `spacetime` |

Move `PROJECT_README` here and write it into new project folders. Add a `helper` subcommand to `cadswarm_launch.py` only if the host needs it; the host runs `helper.py` directly.

For the MVP, the binaries come from the PATH or environment variables (`CADSWARM_HOST_BIN`, `SPACETIME_BIN`, `CADSWARM_MODULE_WASM`). Downloading release assets is P1.

- [ ] **Step 4: Wizard and panel on SpaceHub**

In `gui/wizard.py`:
- `step_start_hub` becomes `step_start_team`, which calls `hosting.start_team` and then `hosting.wait_ready`. Maincloud is the default mode, with the database name `cadswarm-<slug>-<4 hex>`. Local mode sits behind an "Advanced" choice.
- The host person's own membership: `step_host_join` mints a human lead invite with `cadswarm-host --data <dir> invite --kind human --role lead`, parses the `ticket: ` line, redeems it with `agent_setup.redeem`, and stores the identity at `<app_dir>/gui/<db>.json`.
- `join_team` reads a v2 code with `read_invite`, redeems it as a human, and stores the identity the same way.
- "Invite teammates" mints a fresh code with `SpaceHub.create_invite("human")` each time. Copy text: "This code works once. Make a new one for each teammate."
- "Add an AI helper" mints `SpaceHub.create_invite("agent")` and passes the decoded ticket to `write_agent_config`.

In `gui/panel.py`:
- `Session` builds `SpaceHub({"uri", "db", "name"}, identity)` instead of `HubClient`. `connect` becomes `join()`, which rejoins with a new fence.
- `/model`, `/events` (long-poll), `/brep`, `/project/*` and the storage pages keep their calls; SpaceHub answers them.
- The storage settings page calls `SpaceHub.storage_settings` (host-only). Any `HubClient`-specific attributes the panel reads (`.hub`, `.identity`, `.timeout`) exist on SpaceHub.

Drive the guide in real FreeCAD: host a team in Maincloud mode, join from a second user profile with the copied code, add a Claude helper, and watch its box appear in both FreeCAD windows. Then commit.

```powershell
git add -A cadswarm cadswarm_launch.py tests
git commit -m "feat(python): FreeCAD guide hosts cadswarm-host on SpacetimeDB with cadswarm-2 invites"
```

---

### Task 7: Worker queries `mesh` and `printability`

**Files:**
- Modify: `cadswarm/worker/freecad_worker.py`
- Create: `tests/test_worker_queries.py`

**Interfaces:**
- Produces (called by the host): `mesh {"names", "tolerance", "max_bytes"} -> {"meshes": {name: base64 binary STL}}`; `printability {"names", "bed", "min_wall"} -> {"ok", "objects": [{"name", "size", "fits_bed", "closed_solid", "thin_wall_suspects"}]}`.

- [ ] **Step 1: Write the failing tests (real FreeCAD; skipped if absent)**

`tests/test_worker_queries.py`:
```python
"""The new read-only worker queries, against a real FreeCAD worker."""
import base64
import struct

import pytest

from cadswarm.config import find_freecad_python
from cadswarm.worker_client import WorkerClient

try:
    find_freecad_python()
except RuntimeError:
    pytest.skip("FreeCAD not installed", allow_module_level=True)


@pytest.fixture(scope="module")
def worker(tmp_path_factory):
    w = WorkerClient(str(tmp_path_factory.mktemp("w") / "model.FCStd"))
    w.start()
    w.call("create_primitive", {"kind": "box", "name": "Big", "props": {"Length": 300, "Width": 20, "Height": 10}})
    w.call("create_primitive", {"kind": "box", "name": "Small", "props": {"Length": 20, "Width": 20, "Height": 10}})
    w.call("run_part_script", {"name": "Thin", "code":
        "outer = Part.makeBox(30, 30, 10)\ninner = Part.makeBox(29.4, 29.4, 10, Vector(0.3, 0.3, 1))\n"
        "shape = outer.cut(inner)"})
    yield w
    w.stop()


def test_mesh_returns_binary_stl_per_solid(worker):
    r = worker.call("mesh", {"names": ["Small"], "tolerance": 0.2, "max_bytes": 200000})["result"]
    data = base64.b64decode(r["meshes"]["Small"])
    (count,) = struct.unpack("<I", data[80:84])
    assert count >= 12 and len(data) == 84 + 50 * count


def test_mesh_respects_the_size_budget(worker):
    r = worker.call("mesh", {"names": ["Small"], "tolerance": 0.01, "max_bytes": 2000})["result"]
    assert len(base64.b64decode(r["meshes"]["Small"])) <= 2000 or "Small" in r["meshes"]


def test_printability_flags_oversize_parts_and_thin_walls(worker):
    r = worker.call("printability", {"names": ["Big", "Small", "Thin"], "bed": [180, 180, 180], "min_wall": 1.2})["result"]
    by = {o["name"]: o for o in r["objects"]}
    assert not by["Big"]["fits_bed"] and by["Small"]["fits_bed"]
    assert by["Small"]["closed_solid"] and not by["Small"]["thin_wall_suspects"]
    assert by["Thin"]["thin_wall_suspects"], "0.3 mm walls must be flagged"
    assert r["ok"] is False
```

- [ ] **Step 2: Run to see them fail**

Run: `python -m pytest tests/test_worker_queries.py -q`
Expected: `WorkerError: Unknown op 'mesh'` (or skipped if FreeCAD is not installed on this machine; then run on the FreeCAD machine).

- [ ] **Step 3: Implement the queries**

In `cadswarm/worker/freecad_worker.py`, add `import base64` and `import struct` to the imports, and add above `QUERIES = {...}`:
```python
def stl_bytes(shape, tolerance):
    """Binary STL of a shape's tessellation (written by hand: no file round trip, no ASCII surprise)."""
    verts, tris = shape.tessellate(tolerance)
    out = bytearray(b"cadswarm preview".ljust(80, b"\0"))
    out += struct.pack("<I", len(tris))
    for a, b, c in tris:
        p, q, r = verts[a], verts[b], verts[c]
        n = (q - p).cross(r - p)
        if n.Length > 0:
            n.normalize()
        out += struct.pack("<12fH", n.x, n.y, n.z, p.x, p.y, p.z, q.x, q.y, q.z, r.x, r.y, r.z, 0)
    return bytes(out)


def q_mesh(a):
    tol = float(a.get("tolerance", 0.2))
    budget = int(a.get("max_bytes", 200_000))
    out = {}
    for name in a.get("names") or [o.Name for o in doc.Objects]:
        o = doc.getObject(name)
        shape = getattr(o, "Shape", None) if o else None
        if shape is None or shape.isNull() or not shape.Faces:
            continue
        t, data = tol, stl_bytes(shape, tol)
        for _ in range(4):  # coarsen until it fits the preview budget
            if len(data) <= budget:
                break
            t *= 2
            data = stl_bytes(shape, t)
        out[name] = base64.b64encode(data).decode()
    return {"meshes": out}


def face_normal(face):
    u0, u1, v0, v1 = face.ParameterRange
    return face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)


def thin_walls(shape, min_wall, limit=20):
    """Best effort: pairs of opposite-facing planar faces closer than `min_wall`."""
    planar = [(i, f) for i, f in enumerate(shape.Faces) if type(f.Surface).__name__ == "Plane"]
    out = []
    for x in range(len(planar)):
        i, fi = planar[x]
        ni = face_normal(fi)
        for y in range(x + 1, len(planar)):
            j, fj = planar[y]
            if ni.dot(face_normal(fj)) > -0.99:
                continue
            d = fi.distToShape(fj)[0]
            if 1e-6 < d < min_wall:
                out.append({"face_a": i, "face_b": j, "distance": round(d, 3)})
                if len(out) >= limit:
                    return out
    return out


def q_printability(a):
    bed = a.get("bed") or [180, 180, 180]
    min_wall = float(a.get("min_wall", 1.2))
    names = a.get("names") or [o.Name for o in doc.Objects if getattr(o, "Visibility", False)
                               and hasattr(o, "Shape") and not o.Shape.isNull() and o.Shape.Solids]
    objects = []
    for n in names:
        s = get_obj(n).Shape
        bb = s.BoundBox
        size = [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]
        fits = sorted(size[:2]) <= sorted(bed[:2]) and all(
            x <= b for x, b in zip(sorted(size[:2]), sorted(bed[:2]))) and size[2] <= bed[2]
        closed = bool(s.Solids) and all(sol.isClosed() for sol in s.Solids) and s.isValid()
        objects.append({"name": n, "size": size, "fits_bed": fits, "closed_solid": closed,
                        "thin_wall_suspects": thin_walls(s, min_wall)})
    ok = all(o["fits_bed"] and o["closed_solid"] and not o["thin_wall_suspects"] for o in objects)
    return {"ok": ok, "objects": objects}
```
and add both to the `QUERIES` dict: `"mesh": q_mesh, "printability": q_printability,`.

- [ ] **Step 4: Run the tests (on the FreeCAD machine)**

Run: `python -m pytest tests/test_worker_queries.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```powershell
git add cadswarm/worker/freecad_worker.py tests/test_worker_queries.py
git commit -m "feat(worker): binary STL preview meshes and a printability check"
```

---

### Task 8: Board library and the live end-to-end test

**Files:**
- Create: `boards/boards.json`, `tests/test_space_live.py`

**Interfaces:**
- Produces: the board library read by host and web; a live test proving SpaceHub against A's published module.

- [ ] **Step 1: Write `boards/boards.json`**

```json
{
  "esp32-s3-devkitc-1": {
    "label": "ESP32-S3-DevKitC-1",
    "pcb": [62.7, 25.4, 1.6],
    "holes": [],
    "hole_d": 0,
    "clear_top": 9.0,
    "clear_bottom": 3.0,
    "ports": [
      {"name": "usb_uart", "side": "-x", "center": [6.4, 1.6], "size": [9.5, 3.6]},
      {"name": "usb_otg", "side": "-x", "center": [19.0, 1.6], "size": [9.5, 3.6]}
    ],
    "verified": false
  },
  "raspberry-pi-4": {
    "label": "Raspberry Pi 4 Model B",
    "pcb": [85.0, 56.0, 1.4],
    "holes": [[3.5, 3.5], [61.5, 3.5], [3.5, 52.5], [61.5, 52.5]],
    "hole_d": 2.7,
    "clear_top": 17.0,
    "clear_bottom": 3.0,
    "ports": [
      {"name": "usb_c", "side": "-y", "center": [11.2, 1.6], "size": [9.5, 3.6]},
      {"name": "hdmi0", "side": "-y", "center": [26.0, 1.6], "size": [7.5, 3.6]},
      {"name": "hdmi1", "side": "-y", "center": [39.5, 1.6], "size": [7.5, 3.6]},
      {"name": "usb_eth", "side": "+x", "center": [28.0, 8.0], "size": [54.0, 16.0]}
    ],
    "verified": false
  },
  "raspberry-pi-pico": {
    "label": "Raspberry Pi Pico",
    "pcb": [51.0, 21.0, 1.0],
    "holes": [[2.0, 4.8], [2.0, 16.2], [49.0, 4.8], [49.0, 16.2]],
    "hole_d": 2.1,
    "clear_top": 4.0,
    "clear_bottom": 2.0,
    "ports": [{"name": "usb", "side": "-x", "center": [10.5, 1.4], "size": [8.5, 3.2]}],
    "verified": false
  },
  "arduino-uno-r3": {
    "label": "Arduino Uno R3",
    "pcb": [68.6, 53.3, 1.6],
    "holes": [[14.0, 2.5], [15.3, 50.7], [66.1, 7.6], [66.1, 35.5]],
    "hole_d": 3.2,
    "clear_top": 13.0,
    "clear_bottom": 3.0,
    "ports": [
      {"name": "usb_b", "side": "-x", "center": [38.1, 5.5], "size": [12.5, 11.0]},
      {"name": "dc_jack", "side": "-x", "center": [7.6, 5.5], "size": [9.5, 11.0]}
    ],
    "verified": false
  }
}
```

- [ ] **Step 2: Verify the boards you will demo with calipers**

Measure the actual ESP32-S3 board(s) your team brings (PCB length, width, thickness; each USB port's center along the short edge and height above the PCB's top face; opening size), update the entry, and set `"verified": true`. Do the same for any board a judge-facing team brings. These numbers are the whole "real dimensions, not guessed ones" claim.

- [ ] **Step 3: Write `tests/test_space_live.py`**

```python
"""SpaceHub against a real published module. Run with A's server up:
    $env:CADSWARM_LIVE = "1"; python -m pytest tests/test_space_live.py -q
Needs the CLI's owner token (spacetime login show --token) or CADSWARM_OWNER_TOKEN."""
import os
import re
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
        out = subprocess.run(["spacetime", "login", "show", "--token"], capture_output=True, text=True).stdout
        tok = re.search(r"([\w-]+\.[\w-]+\.[\w-]+)", out).group(1)
    c = SpaceClient(URI, DB, tok)
    return c, c.sql("SELECT * FROM v_me")[0]["fence"]


def make_ticket(kind="agent"):
    c, fence = owner()
    tok = ticket.new_token()
    c.call("create_invite", fence, ticket.hash_token(tok), kind, "member", 3600)
    return ticket.encode({"v": 1, "uri": URI, "db": DB, "token": tok, "kind": kind, "role": "member", "host_addr": ""})


def test_two_agents_coordinate_through_spacetimedb(tmp_path):
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    cli.main(["setup", "--client", "claude", "--invite", make_ticket(), "--name", f"live-a-{ticket.new_id('x', 4)}", "--dir", str(a_dir)])
    cli.main(["setup", "--client", "codex", "--invite", make_ticket(), "--name", f"live-b-{ticket.new_id('x', 4)}", "--dir", str(b_dir)])
    a, _ = project_hub(str(a_dir))
    b, _ = project_hub(str(b_dir))
    a.join()
    b.join()
    part = f"Part{ticket.new_id('x', 6)}"
    assert a.post("/locks", {"names": [part]})["locked"] == [part]
    with pytest.raises(Exception) as e:
        b.post("/locks", {"names": [part]})
    assert "locked" in str(e.value)
    t = a.post("/tasks", {"title": "lid", "assignee": b.identity["name"]})
    b.post(f"/tasks/{t['id']}/claim", {})
    b.post("/messages", {"to": a.identity["name"], "body": "on it"})
    assert any(m["body"] == "on it" for m in a.get("/inbox")["messages"])
    assert b.identity["name"] in a.get("/context")["context"]
```

- [ ] **Step 4: Run it against A's server (M0)**

Run: `$env:CADSWARM_LIVE = "1"; python -m pytest tests/test_space_live.py -q`
Expected: `1 passed`. Failures here are contract mismatches: compare reducer argument order with the overview and fix the side that is wrong (tell A if it is the module).

- [ ] **Step 5: Commit**

```powershell
git add boards/boards.json tests/test_space_live.py
git commit -m "feat: board library and a live SpacetimeDB test for SpaceHub"
```

---

### Task 9 (P1): Droid reviewer

**Files:**
- Create: `cadswarm/reviewer.py`, `tests/test_reviewer.py`
- Modify: `pyproject.toml` (optional extra), `README.md` ("Where Droid runs")

**Interfaces:**
- Consumes: `project_hub`, MCP tools `model_status`, `get_object`, `check_interference`, `check_printability`, `create_task`, `send_message`, `report_review`; `droid_sdk.Session`, `SessionConfig`, `Autonomy`, `StdioMcpServerConfig`, `ToolCall`, `ToolResult`, `AssistantMessage`.
- Produces: `python -m cadswarm.reviewer --brief ID --round N --dir DIR` (started by the host); `review_prompt(brief, board, bed, round) -> str`; `activity_for(event) -> tuple[str, str, list[str]] | None`.

- [ ] **Step 1: Write the failing tests for the pure parts**

`tests/test_reviewer.py`:
```python
from types import SimpleNamespace

from cadswarm.reviewer import REVIEW_TOOLS, activity_for, review_prompt


def test_prompt_names_the_brief_the_board_the_bed_and_the_round():
    p = review_prompt({"id": "b_1", "text": "case for an ESP32"}, "ESP32-S3-DevKitC-1", [180, 180, 180], 2)
    assert "b_1" in p and "ESP32-S3-DevKitC-1" in p and "180" in p and "round 2" in p
    assert "report_review" in p


def test_tool_events_become_activity_with_targets():
    call = SimpleNamespace(name="check_interference", input={"names": ["Lid", "Base"]})
    kind, summary, targets = activity_for(call)
    assert kind == "tool_call" and "check_interference" in summary and targets == ["Lid", "Base"]


def test_the_reviewer_cannot_reach_geometry_tools():
    assert not any(t.endswith(("create_primitive", "set_props", "run_part_script", "delete_object", "lock_objects"))
                   for t in REVIEW_TOOLS)
```

- [ ] **Step 2: Run to see them fail**

Run: `python -m pytest tests/test_reviewer.py -q`
Expected: `ModuleNotFoundError: No module named 'cadswarm.reviewer'`.

- [ ] **Step 3: Implement `cadswarm/reviewer.py`**

```python
"""The Droid design reviewer, started by cadswarm-host when a brief's parts are all built.

It is not a chat agent: the host starts it, it may only inspect the model and report (Droid's
tool allowlist, and the module refuses lock/submit_op from a `reviewer` member), and every step
it takes is written to the `activity` table so the phone shows it live.
"""
import argparse
import asyncio
import json
import os
import sys

from .space_hub import project_hub

# Tool ids for MCP tools: verify the exact format with `session.list_mcp_tools()` (see Step 5).
REVIEW_TOOLS = [f"cadswarm___{t}" for t in (
    "model_status", "get_object", "check_interference", "check_printability",
    "create_task", "send_message", "report_review")]


def review_prompt(brief, board_label, bed, round_no):
    return (
        f"You are the design REVIEWER for CadSwarm brief {brief['id']} (review round {round_no}).\n"
        f"The person asked: \"{brief['text']}\". Board: {board_label}. Printer bed: {bed[0]} x {bed[1]} x {bed[2]} mm.\n"
        "Check, using the tools only (you cannot change geometry):\n"
        "1. model_status: read the Params sheet (the board's measured dimensions) and the parts.\n"
        "2. check_interference on all parts: any clash between separate parts is a problem.\n"
        "3. Fit: the enclosure's inner size must exceed pcb_l/pcb_w by at least 2*gap; standoffs must sit at\n"
        "   hole_N_x/hole_N_y; each port cutout must be centred on its <port>_pos and <port>_z.\n"
        "4. check_printability: every part must fit the bed, be a closed solid, and have no wall under 1.2 mm.\n"
        "For each problem: create_task assigned to the part's owner (see locked_by in model_status) with a\n"
        "precise fix, brief set to this brief, and send_message to that owner.\n"
        f"Finish by calling report_review(brief_id=\"{brief['id']}\", round={round_no}, passed=<true only if no problems>,\n"
        "findings=[{\"object\", \"problem\", \"task_id\"}])."
    )


def activity_for(event):
    """Map a Droid stream event to (kind, summary, targets) for the activity feed, or None."""
    name = getattr(event, "name", None)
    if name is not None and hasattr(event, "input"):
        args = event.input if isinstance(event.input, dict) else {}
        targets = args.get("names") or ([args["name"]] if isinstance(args.get("name"), str) else [])
        return "tool_call", f"{name.split('___')[-1]} {json.dumps(args)[:200]}", targets
    if hasattr(event, "tool_name") and hasattr(event, "is_error"):
        return "tool_result", f"{event.tool_name.split('___')[-1]} {'failed' if event.is_error else 'ok'}", []
    text = getattr(event, "text", None)
    if text:
        return "say", text[:300], []
    return None


async def review(project, brief_id, round_no):
    from droid_sdk import (AssistantMessage, Autonomy, Session, SessionConfig, StdioMcpServerConfig,
                           ToolCall, ToolResult)
    hub, _cfg = project_hub(project)
    hub.join()
    brief = next(b for b in hub.space.sql("SELECT * FROM v_brief") if b["id"] == brief_id)
    ws = hub.space.sql("SELECT * FROM v_workspace")[0]
    bed = [ws["bed_x_mm"], ws["bed_y_mm"], ws["bed_z_mm"]]
    config = SessionConfig(
        autonomy=Autonomy.LOW,
        mcp_servers=[StdioMcpServerConfig(name="cadswarm", command=sys.executable,
                                          args=["-m", "cadswarm.mcp_server"],
                                          env={"CADSWARM_PROJECT": project})],
        restrict_tools=REVIEW_TOOLS,
    )
    async with Session(cwd=project, config=config) as session:
        async with session.stream(review_prompt(brief, brief["board"] or "unspecified", bed, round_no)) as stream:
            async for ev in stream:
                if isinstance(ev, (ToolCall, ToolResult, AssistantMessage)):
                    mapped = activity_for(ev)
                    if mapped:
                        hub.log_activity(*mapped)
    # If Droid never recorded a verdict, record a failing one so the brief does not hang.
    reviews = [r for r in hub.space.sql("SELECT * FROM v_review") if r["brief"] == brief_id and r["round"] == round_no]
    if not reviews:
        hub.post("/reviews", {"brief_id": brief_id, "round": round_no, "passed": False,
                              "findings": [{"object": "", "problem": "the reviewer did not report a verdict", "task_id": ""}]})


def main(argv=None):
    p = argparse.ArgumentParser(prog="cadswarm.reviewer")
    p.add_argument("--brief", required=True)
    p.add_argument("--round", type=int, default=1)
    p.add_argument("--dir", required=True)
    a = p.parse_args(argv)
    asyncio.run(review(os.path.abspath(a.dir), a.brief, a.round))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_reviewer.py -q`
Expected: `3 passed` (the SDK is only imported inside `review`, so these run without it).

- [ ] **Step 5: Verify the MCP tool id format against Droid**

On the host machine: `pip install droid-sdk` (not `droid-sdk[mcp]`: that extra requires `mcp<2`, which conflicts with the repo's `mcp>=2.3`), install the `droid` CLI, set `FACTORY_API_KEY`, then in a reviewer folder made by `cadswarm-host init-reviewer`:
```powershell
python -c "import asyncio, droid_sdk as d; from cadswarm.reviewer import *; import sys, os
async def go():
    cfg = d.SessionConfig(mcp_servers=[d.StdioMcpServerConfig(name='cadswarm', command=sys.executable, args=['-m','cadswarm.mcp_server'], env={'CADSWARM_PROJECT': os.getcwd()})])
    async with d.Session(cwd=os.getcwd(), config=cfg) as s: print(await s.list_mcp_tools())
asyncio.run(go())"
```
Use the ids it prints to fix the `cadswarm___` prefix in `REVIEW_TOOLS` (and in `activity_for`'s `split`) if they differ.

- [ ] **Step 6: Write the README section (Factory eligibility rule)**

Add to `README.md`:
```markdown
## Where Droid runs

CadSwarm uses the Factory Droid SDK (`cadswarm/reviewer.py`) as the swarm's design reviewer.

- **What starts it:** `cadswarm-host`, when every task of a brief is done (SpacetimeDB subscription), and again after the owners fix what it found. Nobody prompts it.
- **What it checks:** collisions between parts, that the board actually fits (measured board dimensions, mounting holes, port cutouts), and printability (print bed size, closed solids, walls under 1.2 mm).
- **What it can touch:** only inspection and reporting tools (`restrict_tools`). It joins as a `reviewer` member, which the SpacetimeDB module refuses for `lock_objects` and `submit_op`, so it cannot change geometry even if a tool slipped through.
- **Where you see its work:** every tool call and finding is written to the `activity` table and shown live in the app, and its verdict is stored in `review` and shown as a review card on the brief.
```

- [ ] **Step 7: Commit**

```powershell
git add cadswarm/reviewer.py tests/test_reviewer.py README.md
git commit -m "feat: Droid design reviewer started by the host"
```
