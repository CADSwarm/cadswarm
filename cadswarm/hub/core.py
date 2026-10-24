"""CadSwarm coordination core — the CAD analogue of Amoeba's Brain.

Everything here is guarded by one re-entrant lock and backed by SQLite, and the
FreeCAD worker is reached only through `self.worker.call`, so the whole thing
is testable with a fake worker. Concepts carried over from Amoeba:

* Participants join with a workspace secret and get a token plus a FENCE.
  Re-joining as the same participant (an MCP server restart) bumps the fence,
  so a superseded process can no longer act — `SwarmRunLease.authority()`.
* Locks are per FreeCAD object. They are explicit (lock_objects), and every
  object you create is locked to you. The worker enforces them by rolling back
  any op that changes an object outside the caller's lock set.
* Heartbeats keep a participant `active`. A quiet one turns `stale`: its locks
  are KEPT (nobody else may edit its half-finished part) but its tasks show
  `awaiting_takeover`, and any teammate can `take_over` — a compare-and-set
  that moves its locks and tasks to the taker (Amoeba §14). Stale long enough
  and the locks are freed and tasks reopened.
* Every object has a revision; ops may carry expected revisions and get a
  409-style conflict if someone changed the object first.
* Tasks (create -> claim at revision -> report) and messages (delivered vs
  consumed receipts, human steering first) mirror Amoeba's swarm.
"""
import base64
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid

from ..config import ORPHAN_AFTER_SECONDS, STALE_AFTER_SECONDS
from . import fea
from .files import FileError, ProjectFiles, safe_label, unique_path
from .worker_client import WorkerError

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, client TEXT,
  role TEXT NOT NULL, token TEXT NOT NULL, fence INTEGER NOT NULL,
  status TEXT NOT NULL, status_text TEXT DEFAULT '', last_seen REAL NOT NULL,
  joined_at REAL NOT NULL, stale_since REAL, taken_over_by TEXT, context_seq INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS locks (object TEXT PRIMARY KEY, participant_id TEXT NOT NULL, acquired_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS objects (name TEXT PRIMARY KEY, rev INTEGER NOT NULL, last_actor TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, actor TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, detail TEXT, objects TEXT, status TEXT NOT NULL,
  owner TEXT, created_by TEXT, rev INTEGER NOT NULL, result TEXT, created_at REAL, updated_at REAL, assignee TEXT);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, from_id TEXT, to_id TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
  task_id TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS receipts (
  message_id TEXT, participant_id TEXT, state TEXT, at REAL, PRIMARY KEY (message_id, participant_id));
CREATE TABLE IF NOT EXISTS fea_jobs (
  id TEXT PRIMARY KEY, pid TEXT NOT NULL, status TEXT NOT NULL, job TEXT NOT NULL, result TEXT, plot BLOB,
  revs TEXT, seq INTEGER, folder TEXT, report TEXT, created_at REAL, finished_at REAL, published_at REAL);
"""

# Which argument names an op must hold locks on (beyond what it creates).
TARGETS = {
    "set_property": ["name"], "set_props": ["name"], "set_expression": ["name"],
    "set_placement": ["name"], "delete": ["name"], "boolean": ["base", "tool"],
}
CREATES = {"create_primitive": "name", "create_extrusion": "name", "boolean": "name",
           "run_part_script": "name"}
MUTATING = set(TARGETS) | set(CREATES) | {"set_param", "run_part_script"}
HUB_ID = "hub"
# "message" is the legacy kind (a person's becomes a command); "command" and "chat" are what the panel sends.
SEND_KINDS = {"message", "chat", "command", "steer", "handoff", "help", "result", "notice"}
# Events that mean a participant actually did something (for the "working" indicator).
ACTIVE_KINDS = ("op", "task_claimed", "task_reported", "file_saved", "file_moved", "fea_started", "fea_report",
                "snapshot", "exported", "message")


class HubError(Exception):
    """An actionable refusal. `status` maps to HTTP; `data` carries e.g. heldBy."""

    def __init__(self, status, code, message, **data):
        super().__init__(message)
        self.status, self.code, self.data = status, code, data

    def body(self):
        return {"error": str(self), "code": self.code, **self.data}


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class Hub:
    def __init__(self, worker, db_path, workspace_token, clock=time.time, layout=None):
        self.worker = worker
        self.workspace_token = workspace_token
        self.clock = clock
        self.lock = threading.RLock()
        self.changed = threading.Condition()
        self._version = 0  # bumped by emit under `changed`, so events() never misses a wakeup
        self.db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.layout = layout
        self.files = ProjectFiles(layout, self.db, clock) if layout else None
        self.fea_slot = threading.Semaphore(1)  # one solve at a time; CalculiX already uses every core
        self.fea_done = threading.Condition()
        self.fea_notes = {}  # job id -> latest progress note (e.g. "Installing the Gmsh mesher…")
        self.x("UPDATE fea_jobs SET status='failed', result=? WHERE status IN ('queued','running')",
               json.dumps({"ok": False, "code": "hub_restarted", "error": "The team server restarted mid-analysis."}))

    # ---------------------------------------------------------- project files

    def _files(self):
        if self.files is None:
            raise HubError(501, "no_project_folder", "This team server has no project folder for saved files.")
        return self.files

    def _file_call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FileError as e:
            raise HubError(e.status, e.code, str(e), **e.data)

    def list_files(self, prefix=""):
        with self.lock:
            return self._file_call(self._files().listing, prefix)

    def read_file(self, path):
        with self.lock:
            return self._file_call(self._files().read, path)

    def raw_file(self, path):
        with self.lock:
            return self._file_call(self._files().raw, path)

    def file_history(self, path):
        with self.lock:
            return self._file_call(self._files().history, path)

    def save_file(self, pid, path, text=None, base64_data=None, if_exists="error", expected_version=None):
        with self.lock:
            me = self.name_of(pid)
            r = self._file_call(self._files().save, path, me, text, base64_data, if_exists, expected_version)
            self.emit("file_saved", pid, name=me, path=r["path"], version=r["version"], replaced=r["replaced"])
            prev = r.get("previous_author")
            if r["replaced"] and prev and prev != me:
                to, _ = self.address(prev)
                if to != "all" and not to.startswith("@"):
                    self.system_message(to, f"{me} replaced your file {r['path']} (now version {r['version']}). "
                                        f"Your version is kept; file_history('{r['path']}') can restore it.")
            return r

    def make_folder(self, pid, path):
        with self.lock:
            return self._file_call(self._files().make_folder, path)

    def move_file(self, pid, src, dst):
        with self.lock:
            r = self._file_call(self._files().move, src, dst, self.name_of(pid))
            self.emit("file_moved", pid, name=self.name_of(pid), **r)
            return r

    def delete_file(self, pid, path):
        with self.lock:
            r = self._file_call(self._files().delete, path, self.name_of(pid))
            self.emit("file_deleted", pid, name=self.name_of(pid), path=r["deleted"])
            return r

    def restore_file(self, pid, path, version, expected_version=None):
        with self.lock:
            r = self._file_call(self._files().restore, path, version, self.name_of(pid), expected_version)
            self.emit("file_saved", pid, name=self.name_of(pid), path=r["path"], version=r["version"], replaced=True)
            return r

    def _model_bytes(self):
        try:
            return os.path.getsize(self.worker.doc_path)
        except OSError:
            return 0

    def storage_status(self):
        with self.lock:
            return self._files().storage.status()

    def prune_storage(self, pid, options):
        """'Free up space', on a person's request only: trim old versions, old snapshots, old exports."""
        self._human_only(pid, "free up space")
        with self.lock:
            r = self._file_call(self._files().storage.prune, options or {})
            self.emit("storage_pruned", pid, name=self.name_of(pid), freed=r["freed"]["total"], removed=r["files_removed"])
            return r

    def set_storage_settings(self, pid, changes):
        self._human_only(pid, "change the storage settings")
        with self.lock:
            self._file_call(self._files().storage.update, changes)
            self.emit("storage_settings", pid, name=self.name_of(pid), changes=changes)
            return self._files().storage.status()

    def _human_only(self, pid, what):
        row = self.one("SELECT kind FROM participants WHERE id=?", pid)
        if not row or row["kind"] != "human":
            raise HubError(403, "humans_only", f"Only a person can {what}. Ask someone on the team to do it "
                           "(in FreeCAD: Team tools > Storage and free up space).")

    def check_room(self, extra):
        """Disk guard and project budget for a snapshot or export of about `extra` bytes."""
        with self.lock:
            self._file_call(self._files().storage.check_add, extra)

    def snapshot(self, pid, label=""):
        """Copy the live model into snapshots/ under a new, never-reused name."""
        if not self.layout:
            raise HubError(501, "no_project_folder", "This team server has no project folder.")
        with self.lock:  # no op can be mid-save while we copy (ops hold this lock)
            self.check_room(self._model_bytes())
            stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(self.clock()))
            name = f"{stamp} {safe_label(label, 'snapshot')}.FCStd"
            dest = unique_path(self.layout["snapshots"], name)
            shutil.copy2(self.worker.doc_path, dest)
            rel = os.path.relpath(dest, self.layout["root"]).replace("\\", "/")
            self.emit("snapshot", pid, name=self.name_of(pid), file=rel)
            return {"file": rel, "size": os.path.getsize(dest)}

    def export_path(self, fmt, label=""):
        self.check_room(self._model_bytes())  # an export is about the size of the model; the real size is counted afterwards
        stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(self.clock()))
        return unique_path(self.layout["exports"], f"{safe_label(label, 'model')} {stamp}.{fmt}")

    # ------------------------------------------------------------------ helpers

    def q(self, sql, *args):
        return self.db.execute(sql, args).fetchall()

    def one(self, sql, *args):
        return self.db.execute(sql, args).fetchone()

    def x(self, sql, *args):
        self.db.execute(sql, args)

    def emit(self, kind, actor, **data):
        cur = self.db.execute("INSERT INTO events (ts, kind, actor, data) VALUES (?,?,?,?)",
                              (self.clock(), kind, actor, json.dumps(data)))
        with self.changed:
            self._version += 1
            self.changed.notify_all()
        return cur.lastrowid

    def seq(self):
        return self.one("SELECT COALESCE(MAX(seq), 0) AS s FROM events")["s"]

    def name_of(self, pid):
        if pid == HUB_ID:
            return "hub"
        row = self.one("SELECT name FROM participants WHERE id=?", pid)
        return row["name"] if row else pid

    def resolve(self, who):
        """Participant id from an id or a (case-insensitive) name."""
        row = self.one("SELECT id FROM participants WHERE id=? OR lower(name)=lower(?) "
                       "ORDER BY joined_at DESC LIMIT 1", who, who)
        if not row:
            raise HubError(404, "unknown_participant", f"No participant {who!r}")
        return row["id"]

    def address(self, who):
        """(to_id, queued): a live participant's id, or '@name' queued until someone joins under that name.

        Lets a lead plan and assign work before teammates have connected.
        """
        if who in (None, "", "all"):
            return "all", False
        row = self.one("SELECT id FROM participants WHERE (id=? OR lower(name)=lower(?)) AND status IN ('active','stale') "
                       "ORDER BY joined_at DESC LIMIT 1", who, who)
        return (row["id"], False) if row else ("@" + who.lower(), True)

    def locks_of(self, pid):
        return [r["object"] for r in self.q("SELECT object FROM locks WHERE participant_id=? ORDER BY object", pid)]

    def holder(self, obj):
        row = self.one("SELECT participant_id FROM locks WHERE object=?", obj)
        return row["participant_id"] if row else None

    def holder_info(self, obj):
        pid = self.holder(obj)
        if not pid:
            return None
        p = self.one("SELECT name, status FROM participants WHERE id=?", pid)
        return {"id": pid, "name": p["name"] if p else pid, "status": p["status"] if p else "unknown"}

    def system_message(self, to_id, body, kind="notice", task_id=None):
        mid = new_id("m")
        self.x("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", mid, HUB_ID, to_id, kind, body, task_id, self.clock())
        return mid

    # --------------------------------------------------------------- identity

    def join(self, workspace_token, name, kind="agent", client=None, role="member", resume=None):
        if workspace_token != self.workspace_token:
            raise HubError(401, "bad_workspace_token", "Wrong workspace token")
        if kind not in ("agent", "human"):
            raise HubError(400, "bad_args", "kind must be agent or human")
        now = self.clock()
        with self.lock:
            if resume:
                row = self.one("SELECT * FROM participants WHERE id=? AND token=?",
                               resume.get("id"), resume.get("token"))
                if row and row["status"] != "taken_over":
                    if row["status"] == "left" and self.one(
                            "SELECT 1 FROM participants WHERE lower(name)=lower(?) AND id!=? "
                            "AND status IN ('active','stale')", row["name"], row["id"]):
                        raise HubError(409, "name_in_use", f"{row['name']!r} was taken by someone else after you left. "
                                       "Pick another name and join again.")
                    fence = row["fence"] + 1  # supersedes any older process using this identity
                    was_stale = row["status"] == "stale"
                    self.x("UPDATE participants SET fence=?, status='active', last_seen=?, stale_since=NULL, "
                           "client=COALESCE(?, client) WHERE id=?", fence, now, client, row["id"])
                    if was_stale:
                        self._resume_tasks(row["id"])
                    self.emit("rejoined", row["id"], name=row["name"], fence=fence)
                    return self._identity(row["id"])
            # Names are how teammates address each other, so they are unique among live participants.
            # A fresh join under the name of a STALE participant is that person coming back without
            # their identity file: they inherit their own unfinished work instead of being locked out of it.
            namesake = self.one("SELECT * FROM participants WHERE lower(name)=lower(?) AND status IN ('active','stale')",
                                name)
            if namesake and namesake["status"] == "active":
                raise HubError(409, "name_in_use",
                               f"{name!r} is already connected. Pick another name, or if that is your own crashed "
                               f"session, wait {STALE_AFTER_SECONDS}s for it to go stale and join again to inherit its work.")
            pid = new_id("p")
            token = uuid.uuid4().hex
            self.x("INSERT INTO participants (id,name,kind,client,role,token,fence,status,last_seen,joined_at) "
                   "VALUES (?,?,?,?,?,?,1,'active',?,?)", pid, name, kind, client, role, token, now, now)
            self.emit("joined", pid, name=name, participant_kind=kind, client=client, role=role)
            self.x("UPDATE messages SET to_id=? WHERE to_id=?", pid, "@" + name.lower())  # deliver queued mail
            if namesake:
                self.take_over(pid, namesake["id"])
            return self._identity(pid)

    def _identity(self, pid):
        row = self.one("SELECT * FROM participants WHERE id=?", pid)
        return {"participant_id": row["id"], "token": row["token"], "fence": row["fence"],
                "name": row["name"], "role": row["role"], "kind": row["kind"], "seq": self.seq()}

    def authenticate(self, pid, token, fence):
        with self.lock:
            row = self.one("SELECT * FROM participants WHERE id=?", pid)
            if not row or row["token"] != token:
                raise HubError(401, "bad_token", "Unknown participant or token; join again")
            if row["status"] == "taken_over":
                raise HubError(403, "taken_over",
                               f"Your work was taken over by {self.name_of(row['taken_over_by'])}. "
                               "Join again as a new participant to keep working.")
            if row["status"] == "left":
                raise HubError(403, "left", "This participant left the session; join again")
            if fence is None:
                raise HubError(400, "missing_fence", "X-Fence header required; join first to get your fence.")
            try:
                fence = int(fence)
            except (TypeError, ValueError):
                raise HubError(400, "bad_fence", "X-Fence must be an integer")
            if fence != row["fence"]:
                raise HubError(409, "fence_stale",
                               "This process was superseded by a newer one using the same identity; stop acting.")
            return dict(row)

    def heartbeat(self, pid, status_text=None):
        with self.lock:
            row = self.one("SELECT status FROM participants WHERE id=?", pid)
            self.x("UPDATE participants SET last_seen=?, status_text=COALESCE(?, status_text) WHERE id=?",
                   self.clock(), status_text, pid)
            if row["status"] == "stale":
                self.x("UPDATE participants SET status='active', stale_since=NULL WHERE id=?", pid)
                self._resume_tasks(pid)
                self.emit("resumed", pid, name=self.name_of(pid))
            unread = len(self._unconsumed(pid))
            return {"seq": self.seq(), "unread": unread, "locks": self.locks_of(pid)}

    def leave(self, pid):
        with self.lock:
            released = self.locks_of(pid)
            self.x("DELETE FROM locks WHERE participant_id=?", pid)
            self.x("UPDATE tasks SET status='open', owner=NULL, rev=rev+1, updated_at=? "
                   "WHERE owner=? AND status IN ('claimed','awaiting_takeover')", self.clock(), pid)
            self.x("UPDATE participants SET status='left' WHERE id=?", pid)
            self.emit("left", pid, name=self.name_of(pid), released=released)
            return {"released": released}

    # ------------------------------------------------------- stale / takeover

    def sweep(self):
        """Mark quiet participants stale; free the work of long-gone ones."""
        now = self.clock()
        with self.lock:
            for row in self.q("SELECT * FROM participants WHERE status='active' AND last_seen < ?",
                              now - STALE_AFTER_SECONDS):
                pid = row["id"]
                self.x("UPDATE participants SET status='stale', stale_since=? WHERE id=?", now, pid)
                self.x("UPDATE tasks SET status='awaiting_takeover', rev=rev+1, updated_at=? "
                       "WHERE owner=? AND status='claimed'", now, pid)
                locks = self.locks_of(pid)
                tasks = [r["title"] for r in self.q("SELECT title FROM tasks WHERE owner=? AND status='awaiting_takeover'", pid)]
                self.emit("stale", pid, name=row["name"], locks=locks, tasks=tasks)
                if locks or tasks:
                    self.system_message("all", f"{row['name']} went quiet holding {locks or 'no locks'} and tasks "
                                        f"{tasks or 'none'}. Its parts stay locked; call take_over('{row['name']}') "
                                        "to continue its work.", kind="handoff")
            for row in self.q("SELECT * FROM participants WHERE status='stale' AND stale_since < ?",
                              now - ORPHAN_AFTER_SECONDS):
                self.leave(row["id"])
            # A finished analysis always reaches the team, even if its AI helper never publishes it.
            for row in self.q("SELECT j.id FROM fea_jobs j LEFT JOIN participants p ON p.id=j.pid "
                              "WHERE j.status='done' AND (j.finished_at < ? OR p.status IS NULL OR "
                              "p.status != 'active')", now - fea.PUBLISH_GRACE):
                try:
                    self.fea_publish(HUB_ID, row["id"], auto=True)
                except HubError as e:
                    print(f"[hub] auto-publishing {row['id']} failed: {e}")

    def _resume_tasks(self, pid):
        self.x("UPDATE tasks SET status='claimed', rev=rev+1, updated_at=? WHERE owner=? AND status='awaiting_takeover'",
               self.clock(), pid)

    def take_over(self, taker, target, force=False):
        with self.lock:
            target_id = self.resolve(target)
            if target_id == taker:
                raise HubError(400, "bad_args", "You cannot take over yourself")
            row = self.one("SELECT * FROM participants WHERE id=?", target_id)
            me = self.one("SELECT * FROM participants WHERE id=?", taker)
            allowed = row["status"] == "stale" or (force and me["kind"] == "human")
            if not allowed:
                # Compare-and-set: only a stale participant's work is up for grabs.
                raise HubError(409, "not_available",
                               f"{row['name']} is {row['status']}; only a stale participant can be taken over"
                               + ("" if me["kind"] == "human" else " (a human may force it)"),
                               state=row["status"], takenBy=self.name_of(row["taken_over_by"]) if row["taken_over_by"] else None)
            locks = self.locks_of(target_id)
            self.x("UPDATE locks SET participant_id=?, acquired_at=? WHERE participant_id=?", taker, self.clock(), target_id)
            moved = [r["id"] for r in self.q("SELECT id FROM tasks WHERE owner=? AND status IN ('claimed','awaiting_takeover')", target_id)]
            self.x("UPDATE tasks SET owner=?, status='claimed', rev=rev+1, updated_at=? "
                   "WHERE owner=? AND status IN ('claimed','awaiting_takeover')", taker, self.clock(), target_id)
            self.x("UPDATE participants SET status='taken_over', taken_over_by=? WHERE id=?", taker, target_id)
            self.emit("taken_over", taker, target=row["name"], taker=me["name"], locks=locks, tasks=moved)
            self.system_message("all", f"{me['name']} took over {row['name']}'s work: locks {locks}, tasks {moved}.",
                                kind="handoff")
            return {"from": row["name"], "locks": locks, "tasks": moved}

    # ------------------------------------------------------------------ locks

    @staticmethod
    def _names(names):
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise HubError(400, "bad_args", "names must be a list of strings")
        return names

    def acquire(self, pid, names):
        names = sorted(set(self._names(names)))
        if not names:
            raise HubError(400, "bad_args", "names is empty")
        with self.lock:
            conflicts = {}
            for n in names:
                info = self.holder_info(n)
                if info and info["id"] != pid:
                    conflicts[n] = info
            if conflicts:
                raise HubError(409, "locked",
                               "Already locked: " + ", ".join(f"{n} by {i['name']} ({i['status']})" for n, i in conflicts.items())
                               + ". Message the holder, or take_over a stale one.", heldBy=conflicts)
            for n in names:
                self.x("INSERT OR REPLACE INTO locks VALUES (?,?,?)", n, pid, self.clock())
            self.emit("locked", pid, name=self.name_of(pid), objects=names)
            return {"locked": names, "all_my_locks": self.locks_of(pid)}

    def release(self, pid, names, force=False):
        self._names(names)
        with self.lock:
            me = self.one("SELECT kind FROM participants WHERE id=?", pid)
            done = []
            for n in sorted(set(self._names(names))):
                holder = self.holder(n)
                if holder == pid or (holder and force and me["kind"] == "human"):
                    self.x("DELETE FROM locks WHERE object=?", n)
                    done.append(n)
                    if holder != pid:
                        self.system_message(holder, f"A human ({self.name_of(pid)}) released your lock on {n}.")
            if done:
                self.emit("released", pid, name=self.name_of(pid), objects=done)
            return {"released": done, "all_my_locks": self.locks_of(pid)}

    # -------------------------------------------------------------------- ops

    def op(self, pid, op, args, expected_revs=None):
        if not isinstance(op, str) or op not in MUTATING:
            raise HubError(400, "unknown_op", f"Unknown op {op!r}")
        if args is not None and not isinstance(args, dict):
            raise HubError(400, "bad_args", "args must be an object")
        if expected_revs is not None and (not isinstance(expected_revs, dict) or any(
                not isinstance(r, int) or isinstance(r, bool) for r in expected_revs.values())):
            raise HubError(400, "bad_args", "expected_revs must map object names to integer revisions")
        args = dict(args or {})
        with self.lock:
            mine = set(self.locks_of(pid))
            needed = [args.get(k) for k in TARGETS.get(op, []) if args.get(k)]
            if op == "set_param":
                needed.append("Params")
            if op == "run_part_script" and args.get("name") and self.one("SELECT 1 FROM objects WHERE name=?", args["name"]):
                needed.append(args["name"])
            missing = [n for n in needed if n not in mine]
            if missing:
                held = {n: self.holder_info(n) for n in missing}
                raise HubError(409, "not_locked",
                               f"Lock {missing} before {op}. Current holders: "
                               + ", ".join(f"{n}: {h['name'] if h else 'nobody'}" for n, h in held.items()),
                               heldBy={n: h for n, h in held.items() if h})
            new = args.get(CREATES.get(op, ""), None)
            if new and new not in mine:  # a name someone pre-reserved with lock_objects is theirs
                h = self.holder_info(new)
                if h and h["id"] != pid:
                    raise HubError(409, "locked", f"The name {new} is reserved by {h['name']}", heldBy={new: h})
            for name, rev in (expected_revs or {}).items():
                row = self.one("SELECT rev, last_actor FROM objects WHERE name=?", name)
                current = row["rev"] if row else 0
                if current != rev:
                    raise HubError(409, "stale_rev",
                                   f"{name} is at rev {current}, not {rev} (last changed by "
                                   f"{self.name_of(row['last_actor']) if row else 'nobody'}); re-read it first",
                                   currentRev=current)
            try:
                resp = self.worker.call(op, args, allowed=sorted(mine))
            except WorkerError as e:
                raise HubError(422 if e.code != "lock_violation" else 409, e.code, str(e))
            return self._commit(pid, op, args, resp)

    def _commit(self, pid, op, args, resp):
        now = self.clock()
        created, modified = resp.get("created", []), resp.get("modified", [])
        deleted, recomputed = resp.get("deleted", []), resp.get("recomputed", [])
        for n in created:
            self.x("INSERT OR REPLACE INTO objects VALUES (?,1,?,?)", n, pid, now)
            self.x("INSERT OR REPLACE INTO locks VALUES (?,?,?)", n, pid, now)  # creator owns it
        for n in modified + recomputed:
            self.x("INSERT INTO objects VALUES (?,1,?,?) ON CONFLICT(name) DO UPDATE SET rev=rev+1, last_actor=?, updated_at=?",
                   n, pid, now, pid, now)
        for n in deleted:
            self.x("DELETE FROM objects WHERE name=?", n)
            self.x("DELETE FROM locks WHERE object=?", n)
        # Tell owners whose parts moved because of someone else's change (e.g. a Params edit).
        notified = {}
        for n in recomputed:
            owner = self.holder(n)
            if owner and owner != pid:
                notified.setdefault(owner, []).append(n)
        for owner, objs in notified.items():
            what = f"Params.{args.get('alias')}={args.get('value')}" if op == "set_param" else f"{op} on {modified or created}"
            self.system_message(owner, f"{self.name_of(pid)}'s change ({what}) recomputed your objects {objs}. "
                                "Check they still fit (check_interference).")
        revs = {r["name"]: r["rev"] for r in self.q(
            f"SELECT name, rev FROM objects WHERE name IN ({','.join('?' * len(created + modified + recomputed))})",
            *(created + modified + recomputed))} if (created or modified or recomputed) else {}
        seq = self.emit("op", pid, name=self.name_of(pid), op=op, created=created, modified=modified,
                        deleted=deleted, recomputed=recomputed, broken=resp.get("broken", []))
        return {"result": resp.get("result"), "created": created, "modified": modified, "deleted": deleted,
                "recomputed": recomputed, "broken": resp.get("broken", []), "revs": revs, "seq": seq,
                "notified": {self.name_of(k): v for k, v in notified.items()}}

    def query(self, op, args=None):
        try:
            return self.worker.call(op, args or {})["result"]
        except WorkerError as e:
            raise HubError(422, e.code, str(e))

    def model(self):
        status = self.query("status")
        with self.lock:
            revs = {r["name"]: dict(r) for r in self.q("SELECT * FROM objects")}
            for o in status["objects"]:
                h = self.holder_info(o["name"])
                o["locked_by"] = h["name"] if h else None
                o["rev"] = revs.get(o["name"], {}).get("rev", 0)
            status["participants"] = self.participants()
            status["seq"] = self.seq()
        return status

    def brep(self, names=None):
        objs = self.query("brep", {"names": names})["objects"]
        with self.lock:
            for n, o in objs.items():
                if o is not None:
                    h = self.holder_info(n)
                    o["locked_by"] = h["name"] if h else None
                    o["owner_id"] = h["id"] if h else None
        return objs

    def participants(self):
        now = self.clock()
        acted = {r["actor"]: r["ts"] for r in self.q(
            "SELECT actor, MAX(ts) AS ts FROM events WHERE kind IN (" + ",".join("?" * len(ACTIVE_KINDS)) + ") "
            "GROUP BY actor", *ACTIVE_KINDS)}
        working = {}
        for t in self.q("SELECT id, title, owner FROM tasks WHERE status='claimed' ORDER BY updated_at"):
            working.setdefault(t["owner"], {"id": t["id"], "title": t["title"]})
        return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "client": r["client"], "role": r["role"],
                 "status": r["status"], "doing": r["status_text"], "last_seen": r["last_seen"],
                 "locks": self.locks_of(r["id"]), "task": working.get(r["id"]),
                 "idle_s": round(now - acted[r["id"]], 1) if r["id"] in acted else None}
                for r in self.q("SELECT * FROM participants WHERE status NOT IN ('left','taken_over') ORDER BY joined_at")]

    # -------------------------------------------------------------------- FEA

    def fea_faces(self, names, select=None):
        names = [names] if isinstance(names, str) else list(names or [])
        return self.query("faces", {"names": names, "select": select})

    def fea_start(self, pid, args, wait=0.0):
        """Queue an analysis of a copy of the target; it never touches the live model or its locks."""
        try:
            job = fea.normalize_job(args)
        except fea.FeaInputError as e:
            raise HubError(400, "bad_args", str(e))
        brep = self.query("fea_shape", {"names": job["target"]})["brep"]
        with self.lock:
            jid = new_id("fea")
            revs = {r["name"]: r["rev"] for r in self.q(
                f"SELECT name, rev FROM objects WHERE name IN ({','.join('?' * len(job['target']))})", *job["target"])}
            ahead = self.one("SELECT COUNT(*) AS n FROM fea_jobs WHERE status IN ('queued','running')")["n"]
            self.x("INSERT INTO fea_jobs (id, pid, status, job, revs, seq, created_at) VALUES (?,?,?,?,?,?,?)",
                   jid, pid, "queued", json.dumps(job), json.dumps(revs), self.seq(), self.clock())
            self.emit("fea_started", pid, name=self.name_of(pid), job_id=jid, target=job["target"],
                      analysis=job["analysis"])
        threading.Thread(target=self._fea_thread, args=(jid, job, brep), daemon=True).start()
        out = self.fea_job(jid, wait)
        if out["status"] in ("queued", "running") and ahead:
            out["queue_position"] = ahead
        return out

    def _fea_thread(self, jid, job, brep):
        with self.fea_slot:
            with self.lock:
                self.x("UPDATE fea_jobs SET status='running' WHERE id=?", jid)
            def progress(text):  # e.g. a one-time mesher install: tell the team why it's taking a while
                with self.lock:
                    row = self.one("SELECT pid FROM fea_jobs WHERE id=?", jid)
                    self.emit("fea_progress", row["pid"], name=self.name_of(row["pid"]), job_id=jid, text=text)
                    self.fea_notes[jid] = text
            try:
                result, png = fea.run_job(self.worker.python, job, brep, progress=progress)
            except Exception as e:  # never leave a job 'running' forever
                result, png = {"ok": False, "code": "fea_error", "error": f"{type(e).__name__}: {e}"}, None
        with self.lock:
            self.fea_notes.pop(jid, None)
            ok = bool(result.get("ok"))
            self.x("UPDATE fea_jobs SET status=?, result=?, plot=?, finished_at=? WHERE id=?",
                   "done" if ok else "failed", json.dumps(result), png, self.clock(), jid)
            row = self.one("SELECT pid FROM fea_jobs WHERE id=?", jid)
            if not ok:
                self.emit("fea_failed", row["pid"], name=self.name_of(row["pid"]), job_id=jid,
                          target=job["target"], error=result.get("error", "")[:300])
                self.system_message(row["pid"], f"FEA job {jid} failed: {result.get('error')}")
        with self.fea_done:
            self.fea_done.notify_all()

    def fea_job(self, jid, wait=0.0):
        """Job state; long-polls up to `wait` seconds while it is queued or running."""
        deadline = time.time() + min(float(wait or 0), 55.0)
        while True:
            with self.lock:
                row = self.one("SELECT * FROM fea_jobs WHERE id=?", jid)
            if not row:
                raise HubError(404, "unknown_job", f"No FEA job {jid}")
            if row["status"] not in ("queued", "running") or time.time() >= deadline:
                break
            with self.fea_done:
                self.fea_done.wait(min(1.0, max(0.05, deadline - time.time())))
        job = json.loads(row["job"])
        out = {"job_id": jid, "status": row["status"], "analysis": job["analysis"], "target": job["target"],
               "placeholders": job["placeholders"]}
        if row["status"] in ("queued", "running"):
            out["next_step"] = f"call fea_status('{jid}') to keep waiting"
            if jid in self.fea_notes:
                out["note"] = self.fea_notes[jid]
        elif row["status"] == "failed":
            r = json.loads(row["result"] or "{}")
            out.update(error=r.get("error"), code=r.get("code"))
        else:
            r = json.loads(row["result"])
            out.update(results=r.get("results"), mesh=r.get("mesh"), warnings=r.get("warnings"),
                       geometry=r.get("geometry"), resolved_faces=r.get("resolved"),
                       automatic_points=[i["text"] for i in fea.rules(job, r)],
                       headline=fea.build_report(jid, job, r, "", 0, 0, {}, 0, "", [], False, 0)["headline"])
            if row["status"] == "done":
                out["next_step"] = (f"publish_fea_report('{jid}', summary, improvements): explain the result in "
                                    "plain words and add 2-5 points of improvement of your own")
            else:
                out["report_folder"] = f"files/{row['folder']}"
        return out

    def fea_publish(self, pid, jid, summary="", improvements=None, auto=False):
        """Write the report into files/fea/<stamp> <label>/ and pop it up in every teammate's FreeCAD."""
        with self.lock:
            row = self.one("SELECT * FROM fea_jobs WHERE id=?", jid)
            if not row:
                raise HubError(404, "unknown_job", f"No FEA job {jid}")
            if row["status"] == "published":
                raise HubError(409, "already_published", f"{jid} is already published in files/{row['folder']}",
                               folder=row["folder"])
            if row["status"] != "done":
                raise HubError(409, "not_ready", f"{jid} is {row['status']}; only a finished analysis can be published")
            owner = self.one("SELECT status FROM participants WHERE id=?", row["pid"])
            if not auto and pid != row["pid"] and owner and owner["status"] == "active":
                raise HubError(403, "not_owner", f"{jid} belongs to {self.name_of(row['pid'])}")
            if self.files is None:
                raise HubError(501, "no_project_folder", "This team server has no project folder for reports.")
            points = []
            for p in improvements or []:
                p = {"text": p} if isinstance(p, str) else dict(p or {})
                if str(p.get("text") or "").strip():
                    sev = p.get("severity") if p.get("severity") in ("critical", "warning", "info") else "info"
                    points.append({"text": str(p["text"])[:1000], "severity": sev})
            job, result = json.loads(row["job"]), json.loads(row["result"])
            report = fea.build_report(jid, job, result, self.name_of(row["pid"]), row["created_at"], row["finished_at"],
                                      json.loads(row["revs"] or "{}"), row["seq"], str(summary or "")[:4000],
                                      points[:10], auto, self.clock())
            stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(self.clock()))
            base = f"fea/{stamp} {safe_label(job['label'] or '-'.join(job['target']), 'analysis')}"
            folder, n = base, 2
            while os.path.exists(self.files.full(self.files.clean(folder, allow_folder=True))):
                folder, n = f"{base} {n}", n + 1
            files = {"json": f"{folder}/report.json", "html": f"{folder}/report.html"}
            if row["plot"]:
                files["plot"] = f"{folder}/plot.png"
            report["files"] = files
            author = self.name_of(row["pid"])
            if row["plot"]:
                self._file_call(self.files.save, files["plot"], author,
                                base64_data=base64.b64encode(row["plot"]).decode())
            self._file_call(self.files.save, files["json"], author, text=json.dumps(report, indent=2))
            self._file_call(self.files.save, files["html"], author, text=fea.render_html(report))
            self.x("UPDATE fea_jobs SET status='published', folder=?, report=?, published_at=? WHERE id=?",
                   folder, json.dumps(report), self.clock(), jid)
            self.emit("fea_report", row["pid"], name=author, report_id=jid, folder=folder, title=report["title"],
                      analysis=job["analysis"], verdict=report["verdict"], headline=report["headline"],
                      placeholders=len(job["placeholders"]), auto=auto)
            return {"published": True, "folder": f"files/{folder}", "files": files, "verdict": report["verdict"],
                    "headline": report["headline"], "improvements": len(report["improvements"])}

    def fea_reports(self, limit=100):
        rows = self.q("SELECT id, pid, folder, report, published_at FROM fea_jobs WHERE status='published' "
                      "ORDER BY published_at DESC LIMIT ?", limit)
        out = []
        for r in rows:
            rep = json.loads(r["report"])
            out.append({"id": r["id"], "by": rep["by"], "folder": r["folder"], "title": rep["title"],
                        "analysis": rep["analysis"], "verdict": rep["verdict"], "headline": rep["headline"],
                        "published_at": r["published_at"], "placeholders": len(rep["inputs"]["placeholders"])})
        return {"reports": out}

    # ------------------------------------------------------------------ tasks

    def create_task(self, pid, title, detail="", objects=None, assignee=None):
        with self.lock:
            tid = new_id("t")
            owner, queued = self.address(assignee) if assignee else (None, False)
            self.x("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,1,NULL,?,?,?)", tid, title, detail,
                   json.dumps(objects or []), "open", None, pid, self.clock(), self.clock(),
                   (owner[1:] if queued else self.name_of(owner)) if owner else None)
            if owner:
                self.system_message(owner, f"{self.name_of(pid)} assigned you task {tid}: {title}. "
                                    "Read it (list_tasks) and claim_task it when you start.", kind="help", task_id=tid)
            self.emit("task", pid, task=tid, title=title, assignee=self.task(tid)["assignee"])
            t = self.task(tid)
            if queued:
                t["note"] = f"No one named {assignee!r} has joined yet; they will be notified when they do."
            return t

    def task(self, tid):
        row = self.one("SELECT * FROM tasks WHERE id=?", tid)
        if not row:
            raise HubError(404, "unknown_task", f"No task {tid}")
        t = dict(row)
        t["objects"] = json.loads(t["objects"] or "[]")
        t["result"] = json.loads(t["result"]) if t["result"] else None
        t["owner_name"] = self.name_of(t["owner"]) if t["owner"] else None
        t["created_by_name"] = self.name_of(t["created_by"])
        return t

    def tasks(self):
        return [self.task(r["id"]) for r in self.q("SELECT id FROM tasks ORDER BY created_at")]

    def claim_task(self, pid, tid, expected_rev=None):
        with self.lock:
            t = self.task(tid)
            if expected_rev is not None and int(expected_rev) != t["rev"]:
                raise HubError(409, "stale_rev", f"Task {tid} is at rev {t['rev']}; re-read it", currentRev=t["rev"])
            if t["status"] != "open":
                raise HubError(409, "not_claimable", f"Task {tid} is {t['status']} (owner {t['owner_name']})",
                               state=t["status"], owner=t["owner_name"])
            self.x("UPDATE tasks SET status='claimed', owner=?, rev=rev+1, updated_at=? WHERE id=?", pid, self.clock(), tid)
            self.emit("task_claimed", pid, task=tid, name=self.name_of(pid))
            return self.task(tid)

    def report_task(self, pid, tid, status, summary, evidence=None):
        if status not in ("done", "blocked"):
            raise HubError(400, "bad_args", "status must be done or blocked")
        with self.lock:
            t = self.task(tid)
            if t["owner"] != pid:
                raise HubError(403, "not_owner", f"Task {tid} is owned by {t['owner_name']}, not you")
            result = {"summary": summary, "evidence": evidence or [], "by": self.name_of(pid), "at": self.clock()}
            self.x("UPDATE tasks SET status=?, result=?, rev=rev+1, updated_at=? WHERE id=?",
                   status, json.dumps(result), self.clock(), tid)
            self.system_message(t["created_by"], f"{self.name_of(pid)} reported task {tid} {status}: {summary}",
                                kind="result", task_id=tid)
            self.emit("task_reported", pid, task=tid, status=status, name=self.name_of(pid))
            return self.task(tid)

    # --------------------------------------------------------------- messages

    def send(self, pid, to, body, kind="message", task_id=None):
        """kind: "chat" is people talking (agents read it as background, never as an order); "command"
        (stored as "steer") is an instruction for an AI helper and outranks everything in its inbox."""
        if not all(isinstance(v, str) for v in (to, body, kind)) or not (task_id is None or isinstance(task_id, str)):
            raise HubError(400, "bad_args", "to, body and kind must be strings")
        if not body.strip():
            raise HubError(400, "bad_args", "body is empty")
        if kind not in SEND_KINDS:
            raise HubError(400, "bad_args", f"kind must be one of {sorted(SEND_KINDS)}")
        with self.lock:
            me = self.one("SELECT kind FROM participants WHERE id=?", pid)
            to_id, queued = self.address(to)
            if kind == "command" or (me["kind"] == "human" and kind == "message"):
                kind = "steer"  # a person's instruction outranks peer requests in every inbox
            if kind == "steer" and me["kind"] != "human":
                kind = "message"  # only people give orders; a helper's "command" is a request
            if kind == "steer" and to_id != "all" and not queued:
                target = self.one("SELECT kind FROM participants WHERE id=?", to_id)
                if target and target["kind"] == "human":
                    kind = "chat"  # people can't be commanded; it's a chat message to them
            mid = new_id("m")
            self.x("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", mid, pid, to_id, kind, body[:4000], task_id, self.clock())
            self.emit("message", pid, id=mid, name=self.name_of(pid),
                      to="all" if to_id == "all" else (to_id[1:] if queued else self.name_of(to_id)),
                      message_kind=kind, body=body[:300])
            out = {"id": mid, "to": to_id}
            if queued:
                out["note"] = f"No one named {to!r} has joined yet; the message waits for them."
            return out

    def _unconsumed(self, pid):
        return self.q(
            "SELECT m.* FROM messages m LEFT JOIN receipts r ON r.message_id=m.id AND r.participant_id=? "
            "WHERE (m.to_id=? OR m.to_id='all') AND (m.from_id IS NULL OR m.from_id != ?) "
            "AND (r.state IS NULL OR r.state != 'consumed') "
            "ORDER BY CASE m.kind WHEN 'steer' THEN 0 WHEN 'handoff' THEN 1 WHEN 'chat' THEN 3 ELSE 2 END, m.created_at",
            pid, pid, pid)

    def commands(self, pid, limit=20):
        """The instructions this person sent to AI helpers, with how far each got: sent (not read yet),
        seen (the helper read it) or done (the helper said it handled it)."""
        with self.lock:
            rows = self.q("SELECT * FROM messages WHERE from_id=? AND kind='steer' ORDER BY created_at DESC LIMIT ?",
                          pid, limit)
            agents = [r["id"] for r in self.q("SELECT id FROM participants WHERE kind='agent' "
                                              "AND status NOT IN ('left','taken_over')")]
            out = []
            for m in rows:
                if m["to_id"] == "all":
                    targets = agents
                elif m["to_id"].startswith("@"):
                    targets = []  # addressed to someone who hasn't joined yet
                else:
                    targets = [m["to_id"]]
                states = {}
                for t in targets:
                    r = self.one("SELECT state FROM receipts WHERE message_id=? AND participant_id=?", m["id"], t)
                    states[self.name_of(t)] = {"delivered": "seen", "consumed": "done"}.get(r["state"] if r else None, "sent")
                order = ("sent", "seen", "done")
                overall = min(states.values(), key=order.index) if states else "sent"
                out.append({"id": m["id"], "body": m["body"], "at": m["created_at"], "task_id": m["task_id"],
                            "to": "all helpers" if m["to_id"] == "all" else
                            (m["to_id"][1:] if m["to_id"].startswith("@") else self.name_of(m["to_id"])),
                            "state": overall, "by_helper": states})
            return out

    def inbox(self, pid, limit=10):
        with self.lock:
            rows = self._unconsumed(pid)[:limit]
            for m in rows:
                self.x("INSERT OR IGNORE INTO receipts VALUES (?,?,'delivered',?)", m["id"], pid, self.clock())
            return [{"id": m["id"], "from": self.name_of(m["from_id"]), "to": "all" if m["to_id"] == "all" else "you",
                     "kind": m["kind"], "body": m["body"], "task_id": m["task_id"], "at": m["created_at"]} for m in rows]

    def ack(self, pid, ids):
        with self.lock:
            done = []
            for mid in ids:
                r = self.one("SELECT state FROM receipts WHERE message_id=? AND participant_id=?", mid, pid)
                if not r:
                    raise HubError(409, "not_delivered", f"Message {mid} was never delivered to you; read your inbox first")
                self.x("UPDATE receipts SET state='consumed', at=? WHERE message_id=? AND participant_id=?",
                       self.clock(), mid, pid)
                done.append(mid)
            return {"consumed": done}

    def chat(self, limit=50):
        rows = self.q("SELECT * FROM messages ORDER BY created_at DESC LIMIT ?", limit)
        return [{"id": m["id"], "from": self.name_of(m["from_id"]),
                 "to": "all" if m["to_id"] == "all" else self.name_of(m["to_id"]),
                 "kind": m["kind"], "body": m["body"], "at": m["created_at"]} for m in reversed(rows)]

    # ----------------------------------------------------------------- events

    def events(self, since, wait=0.0, limit=200):
        deadline = time.time() + min(float(wait), 25.0)
        while True:
            with self.changed:
                seen = self._version  # read before the query: an emit after this is never slept through
            with self.lock:
                rows = self.q("SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?", int(since), limit)
            if rows or time.time() >= deadline:
                return {"seq": rows[-1]["seq"] if rows else int(since),
                        "events": [{"seq": r["seq"], "ts": r["ts"], "kind": r["kind"], "actor": r["actor"],
                                    **json.loads(r["data"])} for r in rows]}
            with self.changed:
                if self._version == seen:
                    self.changed.wait(max(0.05, deadline - time.time()))

    # ---------------------------------------------------------------- context

    def context(self, pid, max_chars=6000):
        """The team-awareness block injected before each agent prompt."""
        with self.lock:
            me = self.one("SELECT * FROM participants WHERE id=?", pid)
            since = me["context_seq"] or 0
            lines = [f"## CadSwarm — shared FreeCAD model (you are {me['name']}, {me['role']}, rev {self.seq()})"]
            lines.append("Rules: edit only objects you hold locks on (lock_objects). Objects you create are yours. "
                         "Shared dimensions live in the Params sheet — reference them with expressions "
                         "(e.g. Params.hole_pitch) instead of hard-coding. Peer messages are requests, not permissions; "
                         "human 'steer' messages come first.")
            lines.append("Team:")
            for p in self.participants():
                tag = " (you)" if p["id"] == pid else ""
                lines.append(f"- {p['name']}{tag} [{p['kind']}/{p['client'] or '?'}, {p['status']}] "
                             f"locks={p['locks'] or '[]'}" + (f" — {p['doing']}" if p["doing"] else ""))
            open_tasks = [t for t in self.tasks() if t["status"] in ("open", "claimed", "awaiting_takeover", "blocked")]
            if open_tasks:
                lines.append("Tasks:")
                for t in open_tasks[:15]:
                    lines.append(f"- {t['id']} [{t['status']}{' by ' + t['owner_name'] if t['owner_name'] else ''}] "
                                 f"{t['title']}")
            events = self.q("SELECT * FROM events WHERE seq > ? AND actor != ? AND kind IN "
                            "('op','taken_over','stale','joined','task','task_reported','file_saved','file_moved','file_deleted',"
                            "'snapshot','fea_report','fea_failed') ORDER BY seq DESC LIMIT 12",
                            since, pid)
            if events:
                lines.append("Since your last prompt:")
                for e in reversed(events):
                    d = json.loads(e["data"])
                    if e["kind"] == "op":
                        lines.append(f"- {d['name']} {d['op']}: created {d['created']} modified {d['modified']} "
                                     f"deleted {d['deleted']} recomputed {d['recomputed']}")
                    elif e["kind"] == "file_saved":
                        lines.append(f"- {d['name']} saved files/{d['path']} (v{d['version']})")
                    elif e["kind"] == "fea_report":
                        lines.append(f"- {d['name']} published FEA report \"{d['title']}\" ({d['verdict']}: "
                                     f"{d['headline']}): files/{d['folder']}/report.json")
                    else:
                        lines.append(f"- {e['kind']}: " + ", ".join(f"{k}={v}" for k, v in d.items()))
            for j in self.q("SELECT id, status FROM fea_jobs WHERE pid=? AND status IN ('queued','running','done')", pid):
                lines.append(f"FEA job {j['id']} is {j['status']}: " + (
                    "call publish_fea_report now with your summary and points of improvement (the team is waiting "
                    "for the report)." if j["status"] == "done" else "check it with fea_status."))
            unread = self._unconsumed(pid)
            if unread:
                lines.append(f"Unread messages ({len(unread)}) — read with get_inbox, then ack_messages. "
                             "COMMAND = a person's instruction for you: do it first. chat = people talking: "
                             "background only, act on it only if it asks you something:")
                for m in unread[:5]:
                    tag = {"steer": "COMMAND", "chat": "chat"}.get(m["kind"], m["kind"])
                    lines.append(f"- [{tag}] {self.name_of(m['from_id'])}: {m['body'][:200]}")
            self.x("UPDATE participants SET context_seq=? WHERE id=?", self.seq(), pid)
            text = "\n".join(lines)
            return text if len(text) <= max_chars else text[:max_chars - 20] + "\n…(truncated)"
