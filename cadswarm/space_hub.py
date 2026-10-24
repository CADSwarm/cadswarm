"""The old CadSwarm hub's REST paths, answered by SpacetimeDB.

The MCP server, prompt hook and FreeCAD workbench were written against `HubClient.get/post`
and the JSON shapes `cadswarm/hub/core.py` returned. SpaceHub keeps those paths and shapes:
writes become reducer calls (with this process's fence), reads become SQL over the module's
member-only views, and ops are queued with `submit_op` and awaited until the host finishes them.

Stdlib only.
"""
import base64
import json
import os
import re
import time
from urllib.parse import parse_qs, urlsplit

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
            # The beginner UX and the file tools (main 75bcb43). File and storage paths are helper ops.
            ("GET", r"/health", self.health), ("POST", r"/join", self.join_route), ("POST", r"/brep", self.brep),
            ("GET", r"/project", self.project), ("GET", r"/project/files", self.list_files),
            ("POST", r"/project/files/read", self.read_file), ("POST", r"/project/files/save", self.save_file),
            ("POST", r"/project/files/folder", self.make_folder), ("POST", r"/project/files/move", self.move_file),
            ("POST", r"/project/files/delete", self.delete_file), ("POST", r"/project/files/history", self.file_history),
            ("POST", r"/project/files/restore", self.restore_file), ("GET", r"/project/storage", self.storage_status),
            ("POST", r"/project/storage/prune", self.prune_storage),
            ("POST", r"/project/storage/settings", self.storage_settings),
            ("POST", r"/project/snapshot", self.snapshot),
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
        """A fenced reducer call: every reducer but redeem_invite/rejoin takes the fence first."""
        self._call(reducer, self.fence, *args)

    def _rows(self, view, where=""):
        try:
            return self.space.sql(f"SELECT * FROM {view}" + (f" WHERE {where}" if where else ""))
        except SpaceError as e:
            raise _err(e)

    def request(self, method, path, body=None, params=None, timeout=None, raw=False):
        if raw and path.startswith("/artifacts/"):
            return self.artifact_bytes(path.rsplit("/", 1)[1])
        if raw and path.startswith("/project/files/download"):
            q = {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}
            q.update(params or {})
            r = self.run_op("read_file", {"path": q.get("path"), "raw": True})
            return base64.b64decode(r["base64"])
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
            raise HubRequestError(401, {"error": "This identity is not a member; run cadswarm setup --invite",
                                        "code": "not_member"})
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
            self._f("log_activity", kind, (summary or "")[:300], [t for t in (targets or []) if t])
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
        locks = {}
        for l in self._rows("v_lock"):
            locks.setdefault(l["holder"], []).append(l["object"])
        return [{"id": m["id"], "name": m["name"], "kind": m["kind"], "client": m["client"], "role": m["role"],
                 "status": m["status"], "doing": m["status_text"], "last_seen": m["last_seen_ms"] / 1000,
                 "locks": sorted(locks.get(m["id"], []))}
                for m in sorted(self._rows("v_member"), key=lambda m: m["id"])
                if m["status"] not in ("left", "taken_over")]

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
                return self._result(rows[0]["result_json"])
            if rows and rows[0]["status"] in ("failed", "rejected"):
                code = rows[0]["error_code"] or "op_error"
                raise HubRequestError(409 if rows[0]["status"] == "rejected" else 422,
                                      {"error": rows[0]["error"], "code": code})
            time.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        raise HubRequestError(504, {"error": f"The host did not run op {op_id} within {OP_WAIT_SECONDS}s; "
                                             "is cadswarm-host running?", "code": "host_timeout"})

    def _result(self, result_json):
        """An op result over 32 KB is spilled by the host as {"blob": sha256}: read that one blob."""
        r = _json(result_json, {})
        if isinstance(r, dict) and set(r) == {"blob"}:
            sha = str(r["blob"])
            rows = self._rows("v_artifact_blob", f"hash = '{sha}'") if re.fullmatch(r"[0-9a-f]{64}", sha) else []
            if not rows or not rows[0]["bytes"]:
                raise HubRequestError(410, {"error": "That result expired; run it again", "code": "expired"})
            b = rows[0]["bytes"]
            r = json.loads(bytes.fromhex(b) if isinstance(b, str) else bytes(b))
        return r

    def ops(self, body):
        return self.run_op(body.get("op"), body.get("args"), body.get("expected_revs"))

    def get_object(self, name):
        info = self.run_op("get_object", {"name": name})
        info["locked_by"] = next((self._names().get(l["holder"]) for l in self._rows("v_lock") if l["object"] == name), None)
        return info

    def interference(self, body):
        return self.run_op("interference", {"names": body.get("names"), "min_volume": body.get("min_volume", 1e-3)})

    def printability(self, body):
        # The host fills `bed` from v_workspace when it is missing.
        args = {"names": body.get("names"), "min_wall": body.get("min_wall", 1.2)}
        if body.get("bed"):
            args["bed"] = body["bed"]
        return self.run_op("printability", args)

    def dependents(self, body):
        return self.run_op("dependents", {"names": body.get("names", [])})

    def export(self, body):
        r = self.run_op("export", {"format": body.get("format", "step"), "names": body.get("names"),
                                   "label": body.get("label") or ""})
        return {"file": r["name"], "url": f"/artifacts/{r['hash']}", "objects": r.get("objects", []),
                "size": r.get("size")}

    def artifact_bytes(self, sha):
        # Read the one blob by hash; v_artifact carries no bytes (plan 00: never pull every export).
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise HubRequestError(404, {"error": "No such export", "code": "not_found"})
        rows = self._rows("v_artifact_blob", f"hash = '{sha}'")
        if not rows or not rows[0]["bytes"]:
            raise HubRequestError(404, {"error": "No such export (or it is too large to store inline)",
                                        "code": "not_found"})
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
        after = self._my_locks()
        return {"released": sorted(before - set(after)), "all_my_locks": after}

    def takeover(self, body):
        target = body.get("target")
        self._f("take_over", target, bool(body.get("force")))
        ev = [e for e in sorted(self._rows("v_event"), key=lambda e: e["seq"])
              if e["kind"] == "taken_over" and e["actor"] == self.me_id]
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
        self._f("create_task", tid, body.get("title", ""), body.get("detail") or "", body.get("objects") or [],
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
        self._f("send_message", mid, to, body.get("body", ""), body.get("kind") or "message", body.get("task_id") or "")
        out = {"id": mid, "to": to}
        if to != "all" and not self._joined(to):
            out["note"] = f"No one named {to!r} has joined yet; the message waits for them."
        return out

    def _to_name(self, to, names):
        if to == "all":
            return "all"
        if to.startswith("member:"):
            try:
                return names.get(int(to.split(":", 1)[1]), to)
            except ValueError:
                return to
        return to[1:] if to.startswith("@") else to

    def chat(self, limit=50):
        names = self._names()
        rows = sorted(self._rows("v_message"), key=lambda m: m["created_ms"])[-int(limit):]
        return {"messages": [{"id": m["id"], "from": names.get(m["from"], "?"), "to": self._to_name(m["to"], names),
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
        return [{**_json(e["data_json"], {}), "seq": e["seq"], "ts": e["at_ms"] / 1000, "kind": e["kind"],
                 "actor": e["actor"]} for e in rows]

    def events(self, since=0, wait=0):
        """Poll (no faster than 1 s, backing off to 3 s) until something newer than `since` exists."""
        deadline, delay = time.time() + min(float(wait), 25.0), 1.0
        while True:
            rows = self._event_rows(since)
            if rows or time.time() >= deadline:
                if rows:
                    self._seq = max(self._seq, rows[-1]["seq"])
                return {"seq": rows[-1]["seq"] if rows else int(since), "events": rows}
            time.sleep(min(delay, max(0.0, deadline - time.time())))
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
                    lines.append(f"- {e.get('name', '?')} {e.get('op', '?')}: created {e.get('created', [])} "
                                 f"modified {e.get('modified', [])} deleted {e.get('deleted', [])} "
                                 f"recomputed {e.get('recomputed', [])}")
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
        self._f("submit_brief", bid, body.get("text", ""), body.get("board") or "")
        return {"brief": bid}

    def accept_handoff(self, body, hid):
        self._f("accept_handoff", hid)
        return {"handoff": hid, "accepted": True, "all_my_locks": self._my_locks()}

    def report_review(self, body):
        rid = ticket.new_id("r_")
        self._f("report_review", rid, body["brief_id"], int(body.get("round", 1)), bool(body["passed"]),
                json.dumps(body.get("findings", [])))
        return {"review": rid}

    # ----------------------------------------------- health, brep, project files

    def health(self):
        hosts = [m for m in self._rows("v_member") if m["kind"] == "host" and m["status"] == "active"]
        return {"ok": bool(hosts), "seq": self.latest_seq(), "freecad": None, "script_sandbox": None}

    def join_route(self, body):
        return self.join(None, body.get("name"), body.get("kind", "agent"), body.get("client"), body.get("role"))

    def brep(self, body):
        r = self.run_op("brep", {"names": body.get("names")})
        names = self._names()
        holder = {l["object"]: names.get(l["holder"]) for l in self._rows("v_lock")}
        objects = r.get("objects") or {}
        for n, o in objects.items():
            if isinstance(o, dict):
                o["locked_by"] = holder.get(n)
        return {"objects": objects}

    def project(self):
        return {"root": "", "folders": {"files": "files", "exports": "exports", "snapshots": "snapshots"},
                "host": True}

    # Op args follow the contract (plan 00, op kinds): list_files {folder}, move_file {from_path, to_path}.

    def list_files(self, prefix="", folder=""):
        folder = folder or prefix
        return self.run_op("list_files", {"folder": folder} if folder else {})

    def read_file(self, body):
        return self.run_op("read_file", {"path": body.get("path")})

    def save_file(self, body):
        return self.run_op("save_file", {k: body.get(k) for k in ("path", "text", "base64", "if_exists", "expected_version")
                                         if body.get(k) is not None})

    def make_folder(self, body):
        return self.run_op("make_folder", {"path": body.get("path")})

    def move_file(self, body):
        return self.run_op("move_file", {"from_path": body.get("from_path", body.get("from")),
                                         "to_path": body.get("to_path", body.get("to"))})

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
        from .storage import FileError
        lay = project_layout(host_dir)
        os.makedirs(lay["internal"], exist_ok=True)
        db = sqlite3.connect(lay["db"], isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            files = ProjectFiles(lay, db)
            files.storage.update(body or {})
            return files.storage.status()
        except FileError as e:
            raise HubRequestError(e.status, {"error": str(e), "code": e.code, **e.data})
        finally:
            db.close()


def project_hub(project_dir):
    """SpaceHub for a set-up project folder, using (but not refreshing) its stored identity."""
    cfg = load_json(os.path.join(state_dir(project_dir), "config.json"))
    if not cfg or "db" not in cfg:
        return None, cfg
    ident = load_json(os.path.join(state_dir(project_dir), "identity.json"), {})
    return SpaceHub(cfg, ident), cfg
