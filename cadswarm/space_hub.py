"""The old CADSwarm hub's REST paths, answered by SpacetimeDB.

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
from .space_library import LibraryRoutes

STATUS = {"not_member": 401, "bad_invite": 401, "fence_stale": 409, "taken_over": 403, "left": 403,
          "not_allowed": 403, "not_owner": 403, "unknown_participant": 404, "unknown_task": 404,
          "not_found": 404, "bad_args": 400, "unknown_op": 400, "unreachable": 0}
OP_WAIT_SECONDS = 150
# Events that mean a member actually did something (the panel's "working" indicator).
ACTIVE_KINDS = ("op", "task_claimed", "task_reported", "file_saved", "file_moved", "fea_started", "fea_report",
                "snapshot", "exported", "message")
STYLE_PROFILE = "style-profile"  # artifact key the host refreshes whenever the style is re-learned
EVENT_KINDS_FOR_CONTEXT = ("op", "taken_over", "stale", "joined", "task", "task_reported", "fea_report", "fea_failed",
                           "library", "setting_changed", "setting_suggested")


def _err(e):
    return HubRequestError(STATUS.get(e.code, 409), {"error": e.message, "code": e.code})


def _json(text, default):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _findings(text):
    """Review findings as plain sentences. Reviewer JSON is free-form, so take whatever shape arrives:
    a list of strings, a list of objects, or {"findings": [...]} with message/text/detail/summary keys."""
    v = _json(text, [])
    if isinstance(v, dict):
        v = v.get("findings") or []
    if not isinstance(v, list):
        return [str(v)] if v else []
    out = []
    for f in v:
        if isinstance(f, str):
            out.append(f.strip())
        elif isinstance(f, dict):
            line = next((f[k] for k in ("message", "text", "detail", "summary", "finding") if f.get(k)), None)
            out.append(str(line).strip() if line else json.dumps(f))
        elif f is not None:
            out.append(str(f))
    return [f for f in out if f]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


class SpaceHub(LibraryRoutes):
    def __init__(self, cfg, identity=None, timeout=60, space=None):
        self.cfg = cfg
        self.identity = identity or {}
        self.timeout = timeout
        self.hub = cfg.get("uri", "")
        self.space = space or SpaceClient(cfg["uri"], cfg["db"], self.identity.get("token"), timeout)
        self._seq = 0
        self._cache = None  # view rows shared by one snapshot(); None = always read fresh
        self.routes = [
            ("POST", r"/heartbeat", self.heartbeat), ("POST", r"/leave", self.leave),
            ("POST", r"/me/name", self.rename_route),
            ("POST", r"/pointer", self.pointer_route),
            ("GET", r"/model", self.model), ("GET", r"/objects/(?P<name>[^/]+)", self.get_object),
            ("POST", r"/ops", self.ops), ("POST", r"/locks", self.lock), ("POST", r"/locks/release", self.release),
            ("POST", r"/interference", self.interference), ("POST", r"/printability", self.printability),
            ("POST", r"/dependents", self.dependents), ("POST", r"/export", self.export),
            ("GET", r"/participants", self.participants_route), ("POST", r"/takeover", self.takeover),
            ("GET", r"/tasks", self.tasks_route), ("POST", r"/tasks", self.create_task),
            ("POST", r"/tasks/pickup", self.pickup), ("GET", r"/tasks/spare", self.spare_route),
            ("POST", r"/tasks/(?P<tid>[^/]+)/claim", self.claim), ("POST", r"/tasks/(?P<tid>[^/]+)/result", self.result),
            ("POST", r"/tasks/(?P<tid>[^/]+)/cancel", self.cancel_task),
            ("POST", r"/tasks/(?P<tid>[^/]+)/delete", self.delete_task),
            ("POST", r"/messages", self.send), ("GET", r"/messages", self.chat), ("GET", r"/commands", self.commands),
            ("GET", r"/inbox", self.inbox), ("POST", r"/inbox/ack", self.ack),
            ("GET", r"/events", self.events), ("GET", r"/context", self.context),
            ("POST", r"/briefs/(?P<bid>[^/]+)/plan", self.brief_plan), ("POST", r"/briefs", self.submit_brief), ("GET", r"/briefs", self.briefs),
            ("POST", r"/reviews", self.report_review),
            ("POST", r"/briefs/(?P<bid>[^/]+)/cancel", self.cancel_brief),
            ("GET", r"/workspace", self.workspace), ("POST", r"/bed", self.set_bed),
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
            # FEA: faces is a worker query; the job ops run in the host helper (cadswarm/fea_jobs.py).
            ("POST", r"/fea/faces", self.fea_faces), ("POST", r"/fea/run", self.fea_run),
            ("GET", r"/fea/jobs/(?P<jid>[^/]+)", self.fea_job), ("POST", r"/fea/jobs/(?P<jid>[^/]+)/publish", self.fea_publish),
            ("GET", r"/fea/reports", self.fea_reports),
            # Elastic board knowledge and design memory (plan 05): helper ops on the host, which holds the key.
            ("POST", r"/knowledge/board", self.lookup_board), ("POST", r"/knowledge/designs", self.recall_designs),
            # The team's learned design style (cadswarm/style_memory.py), kept by the host helper.
            ("GET", r"/style", self.style_get), ("POST", r"/style/rules", self.style_rules),
            ("POST", r"/style/teach", self.style_teach), ("POST", r"/style/resolve", self.style_resolve),
            ("POST", r"/style/learn", self.style_learn),
            # Components library and team precision settings (space_library.py).
            *self.library_routes(),
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
        if self._cache is not None and (view, where) in self._cache:
            return self._cache[(view, where)]
        try:
            rows = self.space.sql(f"SELECT * FROM {view}" + (f" WHERE {where}" if where else ""))
        except SpaceError as e:
            raise _err(e)
        if self._cache is not None:
            self._cache[(view, where)] = rows
        return rows

    def panel_snapshot(self, chat_limit=80):
        """Everything the FreeCAD panel shows, reading each view once (one refresh used to be ~15 round trips
        to the database, which froze FreeCAD on a slow network)."""
        self._cache = {}
        try:
            return {"model": self.model(), "tasks": self.tasks(), "commands": self.commands()["commands"],
                    "briefs": self.briefs()["briefs"], "messages": self.chat(chat_limit)["messages"],
                    "reviews": self.reviews(), "workspace": self.workspace()}
        finally:
            self._cache = None

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

    def create_invite(self, kind="human", role="member", ttl=86400, project="", uri=None):
        """A fresh single-use `cadswarm-2:` code. `uri` overrides the address in it (a LAN host hands out its
        network address instead of 127.0.0.1). The module lets only the host mint host or reviewer invites."""
        from .invite import make_invite
        token = ticket.new_token()
        self._f("create_invite", ticket.hash_token(token), kind, role, int(ttl))
        return make_invite({"v": 1, "uri": (uri or self.cfg["uri"]).rstrip("/"), "db": self.cfg["db"], "token": token,
                            "kind": kind, "role": role, "host_addr": ""}, project)

    def heartbeat(self, body):
        self._f("heartbeat", body.get("doing") or "")
        return {"seq": self.latest_seq(), "unread": len(self._unconsumed()), "locks": self._my_locks()}

    def rename_me(self, name):
        """Rename this member (not fenced, see the module's rename_me). Open tasks for the old name follow."""
        self._call("rename_me", name)
        self.identity["name"] = name
        return {"name": name}

    def rename_route(self, body):
        return self.rename_me(body.get("name", ""))

    def leave(self, body):
        locks = self._my_locks()
        self._f("leave")
        return {"released": locks}

    def log_activity(self, kind, summary, targets=None):
        try:
            self._f("log_activity", kind, (summary or "")[:300], [t for t in (targets or []) if t])
        except HubRequestError:
            pass  # presence is best-effort; never block a tool on it

    def move_pointer(self, obj, point=None, mode="pointing", label=""):
        """Point at `obj` (at `point` [x, y, z] when given). Best-effort, like log_activity."""
        try:
            self._f("move_pointer", obj or "", [float(c) for c in (point or [])][:3], mode, (label or "")[:60])
        except (HubRequestError, TypeError, ValueError):
            pass

    def pointer_route(self, body):
        point, mode = body.get("point") or [], body.get("mode") or "pointing"
        if point and len(point) != 3:
            raise HubRequestError(400, {"error": "point must be [x, y, z]", "code": "bad_args"})
        if not point and body.get("object"):  # no spot given: the part's own anchor vertex
            point = (self.run_op("get_object", {"name": body["object"]}) or {}).get("anchor") or []
        self._f("move_pointer", body.get("object") or "", [float(c) for c in point], mode, (body.get("label") or "")[:60])
        return {"object": body.get("object") or "", "point": point, "mode": mode}

    def _point_after(self, kind, r):
        """After an op, rest this member's pointer on the vertex the worker picked for it."""
        target = r.get("pointer") if isinstance(r, dict) else None
        if isinstance(target, dict) and target.get("object") and len(target.get("point") or []) == 3:
            self.move_pointer(target["object"], target["point"], "pointing", kind.replace("_", " "))

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
        working = {}  # each member's oldest claimed task: what they're working on
        for t in sorted(self._rows("v_task"), key=lambda t: t["updated_ms"]):
            if t["status"] == "claimed":
                working.setdefault(t["owner"], {"id": t["id"], "title": t["title"]})
        acted = {}  # when each member last actually did something
        for e in self._rows("v_event"):
            if e["kind"] in ACTIVE_KINDS:
                acted[e["actor"]] = max(acted.get(e["actor"], 0), e["at_ms"])
        now_ms = time.time() * 1000
        return [{"id": m["id"], "name": m["name"], "kind": m["kind"], "client": m["client"], "role": m["role"],
                 "status": m["status"], "doing": m["status_text"], "last_seen": m["last_seen_ms"] / 1000,
                 "locks": sorted(locks.get(m["id"], [])), "task": working.get(m["id"]),
                 "idle_s": round(max(0.0, now_ms - acted[m["id"]]) / 1000, 1) if m["id"] in acted else None}
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
                r = self._result(rows[0]["result_json"])
                self._point_after(kind, r)
                return r
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
        # (a spilled library result also keeps its rows inline for complete_op: see host executor.rs)
        if isinstance(r, dict) and "blob" in r and set(r) <= {"blob", "_components", "_component_deletes"}:
            sha = str(r["blob"])
            rows = self._rows("v_artifact_blob", f"hash = '{sha}'") if re.fullmatch(r"[0-9a-f]{64}", sha) else []
            if not rows or not rows[0]["bytes"]:
                raise HubRequestError(410, {"error": "That result expired; run it again", "code": "expired"})
            b = rows[0]["bytes"]
            r = json.loads(bytes.fromhex(b) if isinstance(b, str) else bytes(b))
        return r

    def ops(self, body):
        return self.run_op(body.get("op"), body.get("args"), body.get("expected_revs"))

    # ---------------------------------------------------------------------- FEA

    def _fea_op(self, kind, args):
        r = self.run_op(kind, args)
        return {k: v for k, v in r.items() if k != "_events"} if isinstance(r, dict) else r  # events: for the module

    def _fea_wait(self, out, wait):
        """Poll fea_status from here (never inside the host, which runs one op at a time) for up to `wait` s."""
        deadline = time.time() + min(float(wait or 0), 55.0)
        while out.get("status") in ("queued", "running") and time.time() < deadline:
            time.sleep(min(3.0, max(0.0, deadline - time.time())))
            out = self._fea_op("fea_status", {"job_id": out["job_id"]})
        return out

    def fea_faces(self, body):
        names = body.get("names") or body.get("name")
        return self.run_op("faces", {"names": [names] if isinstance(names, str) else names or [],
                                     "select": body.get("select")})

    def fea_run(self, body):
        args = {k: v for k, v in body.items() if k != "wait"}
        target = args.get("target")
        targets = [target] if isinstance(target, str) else list(target or [])
        objects = {o["name"]: o for o in self._rows("v_object")}
        missing = [t for t in targets if t not in objects]
        if missing:
            raise HubRequestError(400, {"error": f"No part named {missing} in the model (see model_status).",
                                        "code": "bad_args"})
        args["revs"] = {t: objects[t]["rev"] for t in targets}
        args["seq"] = self.latest_seq()
        return self._fea_wait(self._fea_op("fea_run", args), body.get("wait", 0))

    def fea_job(self, jid, wait=0):
        return self._fea_wait(self._fea_op("fea_status", {"job_id": jid}), wait)

    def fea_publish(self, body, jid):
        return self._fea_op("fea_publish", {"job_id": jid, "summary": body.get("summary") or "",
                                            "improvements": body.get("improvements") or []})

    def fea_reports(self):
        return self._fea_op("fea_reports", {})

    def get_object(self, name):
        info = self.run_op("get_object", {"name": name})
        if info.get("anchor"):
            self.move_pointer(name, info["anchor"], "inspecting", f"looking at {name}")
        info["locked_by"] = next((self._names().get(l["holder"]) for l in self._rows("v_lock") if l["object"] == name), None)
        return info

    def interference(self, body):
        return self.run_op("interference", {"names": body.get("names"), "min_volume": body.get("min_volume", 1e-3)})

    def printability(self, body):
        # The host fills `bed` from v_workspace when it is missing.
        # min_wall defaults to the team's setting (the worker reads it from the op's _settings).
        args = {"names": body.get("names")}
        if body.get("min_wall"):
            args["min_wall"] = body["min_wall"]
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

    def pickup(self, body):
        """A free helper takes a task off a busy helper's queue; with no task_id the module picks one."""
        self._f("pick_up_task", body.get("task_id") or "")
        ev = [e for e in sorted(self._rows("v_event"), key=lambda e: e["seq"])
              if e["kind"] == "task_claimed" and e["actor"] == self.me_id]
        data = _json(ev[-1]["data_json"], {}) if ev else {}
        t = self.task(data.get("task") or body.get("task_id"))
        t["picked_up_from"] = data.get("from") or None
        return t

    def spare_tasks(self):
        """Tasks queued for a helper that is busy with another task (or gone stale) and whose parts nobody else
        holds: what this helper could pick_up_task. The module's pick_up_task has the final say."""
        tasks = self._rows("v_task")
        claimed = {t["owner"] for t in tasks if t["status"] == "claimed"}
        busy = {m["name"].lower() for m in self._rows("v_member")
                if m["status"] == "stale" or (m["status"] == "active" and m["id"] in claimed)}
        held = {l["object"]: l["holder"] for l in self._rows("v_lock")}
        me = self._names().get(self.me_id, "").lower()
        return [t for t in sorted(tasks, key=lambda t: t["created_ms"])
                if t["status"] == "open" and (t["assignee"] or "").lower() not in ("", me)
                and t["assignee"].lower() in busy and all(held.get(o, self.me_id) == self.me_id for o in t["objects"])]

    def spare_route(self):
        names = self._names()
        return {"tasks": [self._task_shape(t, names) for t in self.spare_tasks()]}

    def result(self, body, tid):
        self._f("report_task", tid, body.get("status", "done"), body.get("summary", ""), body.get("evidence") or [])
        return self.task(tid)

    def cancel_task(self, body, tid):
        """Stop a task. A person may stop any; a helper only one it asked for or claimed."""
        self._f("cancel_task", tid, body.get("reason") or "")
        return self.task(tid)

    def delete_task(self, body, tid):
        """Remove a leftover open task. A claimed one is stopped instead, so no work disappears."""
        t = self.task(tid)  # 404 before the call, so a bad id reads as unknown_task
        self._f("delete_task", tid)
        return {"task": tid, "title": t["title"], "deleted": True}

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
        rank = {"steer": 0, "handoff": 1, "chat": 3}  # people's chat is background, read last
        # People's chat (a message with no @helper in FreeCAD) is between people: AI helpers never see it.
        helper = next((m["kind"] for m in self._rows("v_member") if m["id"] == me), "agent") != "human"
        mine = [m for m in self._rows("v_message")
                if m["to"] in ("all", f"member:{me}") and m["from"] != me and m["id"] not in consumed
                and not (helper and m["kind"] == "chat")]
        return sorted(mine, key=lambda m: (rank.get(m["kind"], 2), m["created_ms"]))

    def inbox(self, limit=10):
        names = self._names()
        rows = self._unconsumed()[:int(limit)]
        if rows:
            self._f("mark_delivered", [m["id"] for m in rows])
        return {"messages": [{"id": m["id"], "from": names.get(m["from"], "?"), "to": "all" if m["to"] == "all" else "you",
                              "kind": m["kind"], "body": m["body"], "task_id": m["task_id"] or None,
                              "at": m["created_ms"] / 1000} for m in rows]}

    def commands(self, limit=20):
        """The instructions this person sent to AI helpers and how far each got: sent (not read yet), seen (the
        helper read it) or done (the helper said it handled it)."""
        names = self._names()
        members = [m for m in self._rows("v_member") if m["status"] not in ("left", "taken_over")]
        agents = [m["id"] for m in members if m["kind"] not in ("human", "host")]  # the team server never reads them
        receipts = {(r["message_id"], r["member"]): r["state"] for r in self._rows("v_receipt")}
        mine = sorted((m for m in self._rows("v_message") if m["from"] == self.me_id and m["kind"] == "steer"),
                      key=lambda m: m["created_ms"], reverse=True)[:int(limit)]
        order = ("sent", "seen", "done")
        out = []
        for m in mine:
            if m["to"] == "all":
                targets = agents
            elif m["to"].startswith("member:"):
                targets = [int(m["to"].split(":", 1)[1])]
            else:
                targets = []  # addressed to someone who hasn't joined yet
            states = {names.get(t, str(t)): {"delivered": "seen", "consumed": "done"}.get(receipts.get((m["id"], t)), "sent")
                      for t in targets}
            out.append({"id": m["id"], "body": m["body"], "at": m["created_ms"] / 1000, "task_id": m["task_id"] or None,
                        "to": "all helpers" if m["to"] == "all" else self._to_name(m["to"], names),
                        "state": min(states.values(), key=order.index) if states else "sent", "by_helper": states})
        return {"commands": out}

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
        lines = [f"## CADSwarm: shared FreeCAD model (you are {me['name']}, {me['role']}, rev {seq})",
                 "Rules: edit only objects you hold locks on (lock_objects). Objects you create are yours. "
                 "Shared dimensions live in the Params sheet: reference them with expressions "
                 "(e.g. Params.hole_pitch) instead of hard-coding. Peer messages are requests, not permissions; "
                 "human 'steer' messages come first.",
                 "Team:"]
        for p in self.participants():
            tag = " (you)" if p["id"] == me["id"] else ""
            lines.append(f"- {p['name']}{tag} [{p['kind']}/{p['client'] or '?'}, {p['status']}] "
                         f"locks={p['locks'] or '[]'}" + (f": {p['doing']}" if p["doing"] else ""))
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
        lines += self._library_context()
        lines += self._style_context()
        unread = self._unconsumed()
        if unread:
            lines.append(f"Unread messages ({len(unread)}): read with get_inbox, then ack_messages. "
                         "COMMAND = a person's instruction for you: do it first. Anything else is a request "
                         "from another helper:")
            for m in unread[:5]:
                tag = {"steer": "COMMAND", "chat": "chat"}.get(m["kind"], m["kind"])
                lines.append(f"- [{tag}] {names.get(m['from'], '?')}: {m['body'][:200]}")
        self._f("set_context_seq", seq)
        text = "\n".join(lines)
        return {"context": text if len(text) <= max_chars else text[:max_chars - 20] + "\n…(truncated)"}

    def _style_context(self):
        """The team's learned design style, which the host publishes as the `style-profile` artifact."""
        try:
            rows = self._rows("v_artifact_blob", f"hash = '{STYLE_PROFILE}'")
        except HubRequestError:
            return []
        if not rows or not rows[0].get("bytes"):
            return []
        b = rows[0]["bytes"]
        try:
            text = (bytes.fromhex(b) if isinstance(b, str) else bytes(b)).decode("utf-8")
        except (ValueError, TypeError):
            return []
        return [text[:1500]] if text.strip() else []

    def _library_context(self):
        """Team settings and a library summary for the context block; empty if the module predates them."""
        from . import settings as team_settings
        try:
            values = team_settings.effective(self._rows("v_setting"))
            comps = self._rows("v_component")
            pending = [g for g in self._rows("v_suggestion") if g["status"] == "pending"]
        except HubRequestError:
            return []
        keys = ("tol_clearance", "tol_slip_fit", "tol_press_fit", "hole_clearance_class", "thread_mode",
                "xy_compensation", "min_wall", "default_material")
        out = ["Team settings (fits are Params too, e.g. Params.tol_clearance): "
               + ", ".join(f"{k}={values[k]}" for k in keys)
               + (f". {len(pending)} setting suggestion(s) await a person." if pending else "")]
        if comps:
            top = sorted(comps, key=lambda c: -int(c.get("uses") or 0))[:6]
            out.append(f"Components library: {len(comps)} team part(s), e.g. "
                       + ", ".join(f"{c['name']} ({c['id']})" for c in top)
                       + "; plus standard screws, nuts, washers, inserts, bearings, pins and magnets. "
                         "Check it (recommend_components) before modelling a part.")
        else:
            out.append("Components library: standard screws, nuts, washers, inserts, bearings, pins and magnets "
                       "(add_standard_part); the team has no parts of its own yet.")
        return out

    # ------------------------------------------------------------- briefs etc.

    def brief_plan(self, body, bid):
        plan = body.get("plan_json")
        self._f("set_brief", bid, "", plan if isinstance(plan, str) else json.dumps(plan))
        return {"brief": bid, "ok": True}

    def briefs(self, limit=20):
        """Requests handed to the whole team (the host plans them into tasks and starts helpers), newest first,
        with how many of each one's tasks are done."""
        names = self._names()
        tasks = self._rows("v_task")
        out = []
        for b in sorted(self._rows("v_brief"), key=lambda b: b["created_ms"], reverse=True)[:int(limit)]:
            mine = [t for t in tasks if t.get("brief") == b["id"]]
            out.append({"id": b["id"], "text": b["text"], "board": b["board"], "status": b["status"],
                        "by": names.get(b["by"], "?"),
                        "at": b["created_ms"] / 1000, "tasks": len(mine),
                        "done": sum(1 for t in mine if t["status"] == "done"),
                        "stl": b["stl_hash"]})
        return {"briefs": out}

    def submit_brief(self, body):
        bid = ticket.new_id("b_")
        self._f("submit_brief", bid, body.get("text", ""), body.get("board") or "")
        return {"brief": bid}

    def cancel_brief(self, body, bid):
        """Stop a whole request; unstarted tasks go with it and the host no longer works on it."""
        self._f("cancel_brief", bid)
        return {"brief": bid, "status": "cancelled"}

    def workspace(self):
        """The team's printer bed, in millimetres. The reviewer and the printability check both use it."""
        rows = self._rows("v_workspace")
        bed = [180.0, 180.0, 180.0] if not rows else [rows[0]["bed_x_mm"], rows[0]["bed_y_mm"], rows[0]["bed_z_mm"]]
        return {"bed": bed, "bed_x_mm": bed[0], "bed_y_mm": bed[1], "bed_z_mm": bed[2]}

    def set_bed(self, body):
        try:
            x, y, z = (float(body[k]) for k in ("x_mm", "y_mm", "z_mm"))
        except (KeyError, TypeError, ValueError):
            raise HubRequestError(400, {"error": "Set the bed's width, depth and height in millimetres.",
                                        "code": "bad_args"})
        if not 20.0 <= min(x, y, z) or max(x, y, z) > 2000.0:
            raise HubRequestError(400, {"error": "A printer bed between 20 and 2000 mm on each side, please.",
                                        "code": "bad_args"})
        self._f("set_bed", x, y, z)
        return self.workspace()

    def reviews(self, limit=10):
        """The Droid reviewer's rounds, newest first, with findings as sentences a person can read."""
        out = []
        for r in sorted(self._rows("v_review"), key=lambda r: r["at_ms"], reverse=True)[:int(limit)]:
            out.append({"id": r["id"], "brief": r["brief"], "round": r["round"], "passed": bool(r["passed"]),
                        "findings": _findings(r.get("findings_json")), "at": r["at_ms"] / 1000})
        return out

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

    def mirror_changes(self, applied):
        """What a live mirror must change to match the team's model, given the version of each part it last applied
        ({name: version}): (breps fetched for new or changed parts, names to remove, the versions now applied).
        Compares rev, bbox, volume and placement, since the host re-syncs objects without bumping revs or making an
        event. A failed fetch raises, so the caller keeps `applied` and the next pass tries again."""
        want = {o["name"]: (o["rev"], tuple(o["bbox"] or ()), o["volume"], o["placement_json"])
                for o in self._rows("v_object")}
        stale = sorted(n for n, v in want.items() if applied.get(n) != v)
        breps = self.post("/brep", {"names": stale})["objects"] if stale else {}
        return breps, sorted(set(applied) - set(want)), want

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

    def lookup_board(self, body):
        return self.run_op("lookup_board", {"question": body.get("question") or "", "board": body.get("board") or ""})

    def recall_designs(self, body):
        return self.run_op("recall_designs", {"query": body.get("query") or "", "board": body.get("board") or ""})

    def style_get(self, topic=None, board=None):
        return self.run_op("style_context", {"query": topic or "", "board": board or ""})

    def style_rules(self, body):
        return self.run_op("list_style", {"statuses": body.get("statuses")} if body.get("statuses") else {})

    def style_teach(self, body):
        return self.run_op("teach_style", {"statement": body.get("statement") or "", "scope": body.get("scope") or "team"})

    def style_resolve(self, body):
        return self.run_op("resolve_style", {"rule_id": body.get("rule_id") or "", "decision": body.get("decision") or ""})

    def style_learn(self, body):
        return self.run_op("learn_style", {})

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
