"""CADSwarm MCP server: one per agent session (Claude Code or Codex).

Started by the agent CLI from a project folder prepared by `cadswarm setup --invite`.
On start it rejoins the team on SpacetimeDB (resuming the folder's stored identity
at a new fence), then heartbeats every 15s in the background so the team sees it as
live and its locks stay protected. If SpacetimeDB says this process has been
superseded (fence) or its work was taken over, every tool refuses to act. A stale
agent must never keep editing the shared model. Every tool call is
logged as activity, which also moves this agent's pointer to its target object.
"""
import os
import sys
import threading
import time

from mcp.server.mcpserver import MCPServer

from .client import HubRequestError, find_project_dir, load_json, save_json, state_dir
from .config import HEARTBEAT_SECONDS
from .files import unique_path
from .space_hub import SpaceHub

INSTRUCTIONS = """You are one of several people or AI agents building one shared CADSwarm project through the
team service. The project is a CADSwarm `.cadswarm` document; there is no FreeCAD runtime. Start with
model_status and list_tasks. Claim a ready task before editing, lock every existing feature you change, and
use the team's MCP tools for all model edits. New output features are reserved by the host. Never write the
project file directly while the team is running.

Supported model operations include parametric box/cylinder/sphere primitives, constrained line/circle/arc
sketches, sketch edit, extrude, revolve, pocket, hole, fillet, chamfer, linear/circular patterns, booleans,
placement, dimension/parameter edits, delete, export to STEP/STL, mesh preview, and interference checks. All
dimensions are millimetres. STEP/STL files saved in the shared project files can be imported into the model.
Sketch entities have stable IDs. Fillet/chamfer edge indices are revision-local;
refresh the source feature before selecting edges after an upstream change. A failed constraint solve or
feature rebuild is rejected without saving a partial edit.

Keep task plans, locks, messages, handoffs, and results current. Check model_status and get_object before
editing, check interference after meaningful geometry changes, and release locks when finished. Use the
shared Params sheet for dimensions multiple parts depend on. The host user can view changes in the CADSwarm
desktop; while a team is active that desktop is read-only so edits cannot bypass the shared locks.

The standalone alpha does not yet support legacy FreeCAD documents, arbitrary part scripts, assemblies,
drawings, FEA, lattice optimization, or the former FreeCAD-dependent component analysis flows. If an MCP
operation returns `unsupported`, do not retry it or claim it ran; describe the limitation plainly. Team chat,
invites, browser/phone presence, task planning, and Claude/Codex/OpenCode handoff remain available."""
BACKGROUND = os.environ.get("CADSWARM_BACKGROUND") == "1"  # started by the host agent runner, no terminal
# The team is joined in the background while the client already has our tools. A tool called before the join
# finished waits this long for it; an unreachable database is retried within the same window.
JOIN_WAIT_SECONDS = 90

mcp = MCPServer("cadswarm", instructions=INSTRUCTIONS)

UNSUPPORTED_STANDALONE_ROUTES = (
    ("/library", "The standard component library has not been migrated to the standalone engine. "
                 "Upload a STEP/STP or STL file to the shared project files and use import_exchange_file."),
    ("/fea", "FEA has not been migrated to the standalone engine."),
    ("/printability", "Printability analysis has not been migrated to the standalone engine."),
    ("/lattice", "Lattice optimization has not been migrated to the standalone engine."),
    ("/constrain", "Automatic constraint suggestions are not available yet. Add explicit sketch constraints "
                   "or bind dimensions to shared Params."),
)


class Session:
    def __init__(self):
        self.project = find_project_dir(os.environ.get("CADSWARM_PROJECT") or os.getcwd())
        if not self.project:
            raise SystemExit("cadswarm: no .cadswarm/config.json here or above; run `cadswarm setup` first")
        self.cfg = load_json(os.path.join(state_dir(self.project), "config.json"))
        self.ident_path = os.path.join(state_dir(self.project), "identity.json")
        self.client = SpaceHub(self.cfg, load_json(self.ident_path, {}))
        self.doing = "starting"
        self.dead = None  # set to an error message once this process must stop acting
        self.seq = None   # event cursor for wait_for_changes
        self.lock = threading.Lock()
        self.fea_jobs = set()  # this session's unfinished FEA jobs, polled so their outcome always reaches the team
        self.ready = threading.Event()  # set once the join finished, joined or not
        self.join_error = None  # why this process could not join, for every tool call to say

    def connect(self):
        ident = self.client.join(None, self.cfg["name"], "agent", self.cfg.get("client"), self.cfg.get("role", "member"))
        save_json(self.ident_path, {**load_json(self.ident_path, {}), **ident})
        # A warm OpenCode server outlives the run that started this MCP server. Leave a note of the fence
        # that now holds the helper's seat, so its runner heartbeats instead of rejoining (which would
        # supersede us and break every tool call).
        save_json(os.path.join(state_dir(self.project), "mcp-seat.json"),
                  {"pid": os.getpid(), "fence": ident.get("fence", 0), "at": time.time()})
        return ident

    def greet(self, ident):
        """Say hello once, so people see who joined without the model spending a turn on it. Background helpers
        are greeted by their runner the moment they wake (faster); a terminal helper is greeted here, once."""
        if (load_json(self.ident_path, {}) or {}).get("greeted"):
            return
        try:
            self.client.post("/messages", {"to": "all", "body": f"Hi, I'm {ident['name']}, ready to help.",
                                           "kind": "chat"})
        except HubRequestError:
            return  # a greeting is a courtesy; try again on the next start
        save_json(self.ident_path, {**load_json(self.ident_path, {}), "greeted": True})

    def doing_now(self):
        """What to tell the team this helper is doing. A background run sets `doing` from its tool calls and
        then exits, but a warm server's MCP server stays alive: once the runner says it is listening, show
        idle instead of the last thing it built, or the team list says "working on X" (or "starting") for
        minutes. A run that never claimed a task leaves `doing` at "starting", so idle wins over any value."""
        st = {}
        try:
            st = load_json(os.path.join(state_dir(self.project), "runner.json")) or {}
        except (OSError, ValueError):
            pass
        if st.get("state") == "listening" and time.time() - float(st.get("beat") or 0) < 30:
            return "listening for instructions"
        return self.doing or "listening for instructions"

    def heartbeat_loop(self):
        backoff = HEARTBEAT_SECONDS
        while not self.dead:
            time.sleep(backoff)
            try:
                self.client.post("/heartbeat", {"doing": self.doing_now()})
                backoff = HEARTBEAT_SECONDS
                self.poll_fea()
            except HubRequestError as e:
                if e.code in ("fence_stale", "taken_over", "left", "removed", "not_member"):
                    self.dead = str(e)
                    return
                backoff = min(backoff * 2, 60)  # hub unreachable: back off, never hammer


    def poll_fea(self):
        """A failed or forgotten FEA job is announced (and auto-published) when its status is read."""
        for jid in list(self.fea_jobs):
            try:
                if self.client.get(f"/fea/jobs/{jid}").get("status") in ("published", "failed"):
                    self.fea_jobs.discard(jid)
            except HubRequestError as e:
                if e.code == "not_found":
                    self.fea_jobs.discard(jid)

    def track_fea(self, r):
        if isinstance(r, dict) and r.get("job_id"):
            (self.fea_jobs.discard if r.get("status") in ("published", "failed") else self.fea_jobs.add)(r["job_id"])
        return r


S = None


def joined():
    """None once this process is on the team, else the error a tool should return."""
    if not S.ready.wait(JOIN_WAIT_SECONDS):
        return {"error": "Still joining the team: its database is slow to answer. Try again in a few seconds.",
                "code": "joining"}
    if S.join_error:
        return {"error": S.join_error, "code": "not_joined"}
    return None


def call(method, path, body=None, doing=None, targets=None, **params):
    for prefix, message in UNSUPPORTED_STANDALONE_ROUTES:
        if path == prefix or path.startswith(prefix + "/"):
            return {"error": message, "code": "unsupported"}
    waiting = joined()
    if waiting:
        return waiting
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
        if e.code in ("fence_stale", "taken_over", "removed", "left"):
            S.dead = str(e)
        return {"error": str(e), **{k: v for k, v in e.body.items() if k != "error"}}


def my_name():
    """This helper's name right now: people can rename it, and it follows its task, while this server runs."""
    if joined():
        return S.cfg["name"]
    try:
        return S.client.me()["name"]
    except HubRequestError:
        return S.cfg["name"]


def op(name, args, doing, expected_revs=None):
    targets = [args.get(k) for k in ("name", "base", "tool", "sketch_name", "source_name")
               if isinstance(args.get(k), str)]
    return call("POST", "/ops", {"op": name, "args": {k: v for k, v in args.items() if v is not None},
                                 "expected_revs": expected_revs}, doing=doing, targets=targets)


# ----------------------------------------------------------------- awareness

@mcp.tool()
def model_status(detail: bool = False) -> dict:
    """Objects in the shared model (owner/lock, revision, bbox, volume), the Params sheet, and the team.
    detail=True adds dependencies and expressions."""
    m = call("GET", "/model")
    if "error" in m:
        return m
    keep = ["name", "label", "type", "locked_by", "rev", "bbox", "volume", "visible", "broken"]
    if detail:
        keep += ["uses", "used_by", "expressions", "placement"]
    return {"seq": m["seq"], "params": m["params"],
            "objects": [{k: o.get(k) for k in keep if k in o} for o in m["objects"]],
            "team": [{k: p[k] for k in ("name", "kind", "client", "role", "status", "doing", "locks")}
                     for p in m["participants"]],
            "you": my_name()}


@mcp.tool()
def get_object(name: str) -> dict:
    """Full properties, placement, expressions and dependencies of one object (cells for the Params sheet)."""
    return call("GET", f"/objects/{name}")


@mcp.tool()
def point_at(name: str, point: list[float] | None = None, mode: str = "pointing", label: str | None = None) -> dict:
    """Show the team where you are working: your cursor moves onto `name` in everyone's 3D view, at
    `point` [x, y, z] in mm (default: a corner on the part's top face). mode: pointing, inspecting or editing."""
    return call("POST", "/pointer", {"object": name, "point": point or [], "mode": mode, "label": label or ""})


@mcp.tool()
def wait_for_changes(seconds: float = 20.0) -> dict:
    """Block up to `seconds` for teammates' activity (ops, locks, messages, tasks) and return it.
    New instructions people sent you from the CADSwarm desktop or web app come back in `commands`: do those first."""
    if BACKGROUND:
        return {"events": [], "note": "You're running in the background: finish what you're doing and stop. "
                                      "You're woken when something new arrives."}
    if S.seq is None:
        S.seq = call("POST", "/heartbeat", {"doing": S.doing}).get("seq", 0)
    r = call("GET", "/events", since=S.seq, wait=min(seconds, 25))
    if "seq" in r:
        S.seq = r["seq"]
        # People's chat (no @helper) is between people: helpers never see it, not even as an event.
        r["events"] = [e for e in r["events"] if not (e["kind"] == "message" and e.get("message_kind") == "chat")]
        me = my_name()
        if any(e["kind"] in ("task", "task_claimed") for e in r["events"]):
            spare = _spare_for_me()
            if spare:
                r["spare_work"] = spare
                r["note"] = "You're free and busy helpers have tasks queued (spare_work): pick_up_task takes one."
        if any(e["kind"] in ("message", "task") and (e.get("to") in ("all", me) or e.get("assignee") == me)
               for e in r["events"]):
            inbox = call("GET", "/inbox")
            cmds = [m for m in inbox.get("messages", []) if m["kind"] == "steer"]
            if cmds:
                r["commands"] = cmds
                r["note"] = ("A person sent you instructions from the CADSwarm client (commands). Do them now, reply in one "
                             "line, then ack_messages with their ids.")
    return r


# --------------------------------------------------------------------- locks

@mcp.tool()
def lock_objects(names: list[str]) -> dict:
    """Lock objects (or names you are about to create) so only you may change them. All-or-nothing;
    a conflict names the holder."""
    return call("POST", "/locks", {"names": names}, doing=f"locking {names}", targets=names)


@mcp.tool()
def release_objects(names: list[str]) -> dict:
    """Release your locks so teammates can edit those objects."""
    return call("POST", "/locks/release", {"names": names})


# ----------------------------------------------------------------- modelling

@mcp.tool()
def create_primitive(kind: str, name: str, props: dict | None = None, expressions: dict | None = None,
                     placement: dict | None = None, label: str | None = None) -> dict:
    """Create a parametric standalone primitive. Supported kinds: box, cylinder and sphere.
    props e.g. {"length": 100, "width": 60, "height": 5} (mm); expressions bind supported dimensions to Params,
    e.g. {"length": "Params.plate_len"}.
    placement: {"base": [x, y, z], "rotation": {"axis": [0, 0, 1], "angle": 90}}."""
    return op("create_primitive", locals(), f"creating {name}")


@mcp.tool()
def create_extrusion(name: str, points: list[list[float]], height: float, holes: list[dict] | None = None,
                     placement: dict | None = None, label: str | None = None) -> dict:
    """Extrude a closed XY polygon along +Z (static solid). holes: [{"x": 10, "y": 10, "r": 2.5}]."""
    return op("create_extrusion", locals(), f"creating {name}")


@mcp.tool()
def create_sketch(name: str, plane: str, entities: list[dict], constraints: list[dict] | None = None,
                  label: str | None = None) -> dict:
    """Create a parametric sketch on XY, XZ or YZ. Entities use stable ids and line/circle/arc geometry.
    Constraints refer to points such as `edge1.start`, `edge1.end` and `circle1.center`; line constraints
    use entity ids. Length values are millimetres and angle values are radians. The result includes solver
    status and remaining degrees of freedom."""
    args = {"name": name, "plane": plane, "entities": entities,
            "constraints": constraints or [], "label": label}
    return op("create_sketch", args, f"creating sketch {name}")


@mcp.tool()
def edit_sketch(name: str, entities: list[dict] | None = None,
                constraints: list[dict] | None = None, expected_rev: int | None = None) -> dict:
    """Replace the geometry and/or constraints of a locked sketch and recompute dependent features.
    Omitted fields stay unchanged. Pass expected_rev from model_status to reject stale edits."""
    args = {"name": name, "entities": entities, "constraints": constraints}
    return op("edit_sketch", args, f"editing sketch {name}",
              {name: expected_rev} if expected_rev is not None else None)


@mcp.tool()
def create_sketch_feature(kind: str, name: str, sketch_name: str | None = None,
                          parameters: dict | None = None, source_name: str | None = None,
                          selection: list[int] | None = None, label: str | None = None) -> dict:
    """Add a parametric feature from a sketch or solid. Kinds: extrude, revolve, pocket, hole, fillet,
    chamfer, linear_pattern, circular_pattern. Sketch features need sketch_name; pocket/hole also need source_name.
    Fillet/chamfer/pattern features need source_name. Edge selection indices are revision-local. Common parameters:
    depth, angle, axis, radius, count, spacing and direction."""
    args = {"kind": kind, "name": name, "sketch_name": sketch_name, "parameters": parameters or {},
            "source_name": source_name, "selection": selection or [], "label": label}
    return op("create_sketch_feature", args, f"creating {kind} feature {name}")


@mcp.tool()
def boolean(kind: str, name: str, base: str, tool: str) -> dict:
    """Parametric boolean: kind cut|fuse|common. You must hold locks on base and tool."""
    return op("boolean", locals(), f"{kind} {base}/{tool} -> {name}")


@mcp.tool()
def set_props(name: str, props: dict | None = None, expressions: dict | None = None,
              expected_rev: int | None = None) -> dict:
    """Change properties and/or expressions of an object you have locked. Pass expected_rev (from
    model_status) to be refused if someone changed it since you looked. An expression of null unbinds."""
    return op("set_props", {"name": name, "props": props, "expressions": expressions}, f"editing {name}",
              {name: expected_rev} if expected_rev is not None else None)


@mcp.tool()
def set_placement(name: str, base: list[float], axis: list[float] | None = None, angle: float = 0.0,
                  expected_rev: int | None = None) -> dict:
    """Move/rotate an object you have locked. base [x,y,z] mm; rotation `angle` degrees about `axis`."""
    return op("set_placement", {"name": name, "placement": {"base": base, "rotation": {"axis": axis or [0, 0, 1],
              "angle": angle}}}, f"placing {name}", {name: expected_rev} if expected_rev is not None else None)


@mcp.tool()
def delete_object(name: str, force: bool = False) -> dict:
    """Delete an object you have locked (refused while others use it unless force)."""
    return op("delete", {"name": name, "force": force}, f"deleting {name}")


@mcp.tool()
def set_param(alias: str, value: str, note: str | None = None) -> dict:
    """Set a shared dimension in the Params sheet, e.g. alias="hole_pitch", value="40 mm".
    Lock 'Params' first. Owners of parts bound to it are notified of the recompute."""
    return op("set_param", locals(), f"setting Params.{alias}")


@mcp.tool()
def auto_constrain(names: list[str] | None = None, apply: bool = False,
                   ids: list[str] | None = None) -> dict:
    """Turn sizes the model already repeats into shared Params, so one edit moves every part.

    It finds equal dimensions across parts, holes on one pitch, and a shaft in a hole that should
    use the team's slip/press fit. Preview first (the default): the plan lists each constraint with
    its Params and expressions. Call again with apply=True to write them; pass ids to write only
    the ones you mean. Applying locks Params and the parts, uses their current revisions, and
    needs no publish. `names` limits it to a selection."""
    body = {"apply": bool(apply)}
    if names:
        body["names"] = list(names)
    if ids is not None:
        body["ids"] = list(ids)
    return call("POST", "/constrain", body,
                doing="applying shared sizes" if apply else "looking for shared sizes")


@mcp.tool()
def run_part_script(code: str, name: str | None = None, label: str | None = None) -> dict:
    """Unavailable in the standalone alpha. Use supported feature operations such as create_sketch_feature,
    create_primitive, and boolean instead of arbitrary scripts."""
    return op("run_part_script", locals(), f"scripting {name or 'model'}")


@mcp.tool()
def check_interference(names: list[str] | None = None) -> dict:
    """Pairwise solid overlaps among visible parts (or `names`). Clashes list overlap volume in mm^3."""
    return call("POST", "/interference", {"names": names}, doing="checking interference", targets=names)


# ---------------------------------------------------------------------- FEA

FEA_WAIT = 45  # seconds a call waits for the solver; keeps every tool call under the CLIs' tool timeouts


@mcp.tool()
def list_faces(name: str | list[str], select: list[str] | None = None) -> dict:
    """Unavailable in the standalone alpha because FEA face selection has not been migrated."""
    return call("POST", "/fea/faces", {"names": [name] if isinstance(name, str) else name, "select": select})


@mcp.tool()
def run_fea(target: str | list[str], analysis: str = "static", material: str | dict | None = None,
            fixed: list[str] | None = None, loads: list[dict] | None = None, thermal: dict | None = None,
            mesh: dict | None = None, modes: int = 6, excitation_hz: float | None = None,
            convergence_check: bool = False, placeholders: list[str] | None = None,
            assumptions: str | None = None, label: str | None = None) -> dict:
    """Unavailable in the standalone alpha; FEA is not yet migrated to the CADSwarm model engine."""
    body = {k: v for k, v in locals().items() if v is not None}
    return S.track_fea(call("POST", "/fea/run", {**body, "wait": FEA_WAIT}, doing=f"running FEA on {target}",
                            targets=[target] if isinstance(target, str) else target))


@mcp.tool()
def fea_status(job_id: str) -> dict:
    """Unavailable in the standalone alpha because FEA jobs have not been migrated."""
    return S.track_fea(call("GET", f"/fea/jobs/{job_id}", wait=FEA_WAIT))


@mcp.tool()
def publish_fea_report(job_id: str, summary: str, improvements: list[str]) -> dict:
    """Unavailable in the standalone alpha because FEA reports have not been migrated."""
    return call("POST", f"/fea/jobs/{job_id}/publish", {"summary": summary, "improvements": improvements},
                doing="publishing an FEA report")


@mcp.tool()
def fea_reports(limit: int = 20) -> dict:
    """Unavailable in the standalone alpha because FEA reports have not been migrated."""
    return call("GET", "/fea/reports", limit=limit)


@mcp.tool()
def fea_report(report: str) -> dict:
    """Unavailable in the standalone alpha because FEA reports have not been migrated."""
    return call("GET", "/fea/reports", report=report)


@mcp.tool()
def export_model(format: str = "step", names: list[str] | None = None, label: str | None = None) -> dict:
    """Export visible parts (or `names`) to STEP/STL. Saved in the team project's exports/ folder under a
    new name (never overwrites) and also copied to ./exports in your workspace."""
    r = call("POST", "/export", {"format": format, "names": names, "label": label or ""})
    if "url" not in r:
        return r
    data = S.client.request("GET", r["url"], raw=True)
    path = unique_path(os.path.join(S.project, "exports"), r["file"])
    with open(path, "wb") as f:
        f.write(data)
    return {"team_file": f"exports/{r['file']}", "local_copy": path, "objects": r["objects"]}


@mcp.tool()
def import_exchange_file(path: str, name: str) -> dict:
    """Import a STEP/STP solid or STL mesh from the team's shared files folder into the model.
    Upload it first with save_file, then pass its path relative to the project files folder. STL remains a mesh
    reference; STEP/STP is imported as an exact solid. The import feature is new and reserved for you."""
    return op("insert_component", {"path": path, "name": name}, f"importing {path} as {name}")


# ------------------------------------------------------------ project files

@mcp.tool()
def list_files(folder: str = "") -> dict:
    """Files and subfolders in the team's shared project folder (files/), with version and author.
    folder narrows it, e.g. "parts/base"."""
    return call("GET", "/project/files", prefix=folder) if folder else call("GET", "/project/files")


@mcp.tool()
def read_file(path: str) -> dict:
    """Read a shared project file, e.g. "parts/base/notes.md". Returns text (or base64 for binary files)
    and its version: pass that version to save_file to replace it."""
    return call("POST", "/project/files/read", {"path": path})


@mcp.tool()
def save_file(path: str, text: str | None = None, base64: str | None = None, if_exists: str = "error",
              expected_version: int | None = None) -> dict:
    """Save a file in the team's shared project folder. Folders are created as needed, so organise by part:
    "parts/lid/notes.md", "docs/bom.csv", "scripts/hinge.py". Text formats: .md .txt .json .csv .py .FCMacro
    .svg .yaml .toml .html .xml; binary (send base64): .png .jpg .pdf .step .stl .dxf .3mf.
    if_exists: "error" (default) refuses to touch an existing file; "new_name" saves as name-2.md etc.;
    "replace" overwrites ONLY with the expected_version from read_file/list_files, and the old version is
    kept in history (the newest 20 versions of a file, plus everything from the last day). Text files can be
    5 MB, binary files 512 MB. Nothing outside the project folder can be written. If the project is full
    (`quota`) or the host's disk is nearly full (`disk_full`), don't delete things to make room: call
    storage_status and ask a person to free up space."""
    return call("POST", "/project/files/save", {"path": path, "text": text, "base64": base64,
                                                "if_exists": if_exists, "expected_version": expected_version},
                doing=f"saving {path}")


@mcp.tool()
def make_folder(path: str) -> dict:
    """Create a subfolder in the shared project folder, e.g. "parts/motor-mount"."""
    return call("POST", "/project/files/folder", {"path": path})


@mcp.tool()
def move_file(from_path: str, to_path: str) -> dict:
    """Move/rename a shared file. Refused if the destination exists; the original is kept in history."""
    return call("POST", "/project/files/move", {"from": from_path, "to": to_path})


@mcp.tool()
def delete_file(path: str) -> dict:
    """Delete a shared file. Its latest version stays in the project's trash and history, so it can be restored."""
    return call("POST", "/project/files/delete", {"path": path})


@mcp.tool()
def file_history(path: str) -> dict:
    """Current version plus the older versions still kept (the newest 20, plus everything from the last day):
    who saved it, when, why it was replaced."""
    return call("POST", "/project/files/history", {"path": path})


@mcp.tool()
def restore_file(path: str, version: int, expected_version: int | None = None) -> dict:
    """Bring back an older version of a file as a new version (the current one is kept too).
    expected_version (from read_file/list_files) refuses the restore if someone changed the file since you read it."""
    return call("POST", "/project/files/restore",
                {"path": path, "version": version, "expected_version": expected_version})


@mcp.tool()
def save_snapshot(label: str = "") -> dict:
    """Save a copy of the whole shared model in snapshots/ (e.g. before a risky change). Never overwrites, and
    is never deleted automatically, so save them for moments that matter, not after every step."""
    return call("POST", "/project/snapshot", {"label": label}, doing="saving a snapshot")


@mcp.tool()
def storage_status() -> dict:
    """How much room the project uses (live files, older versions, snapshots, exports), its limit, the host's
    free disk space and the history-retention settings. Read-only: only a person can free up space in the
    CADSwarm desktop app, so ask one if you are running out."""
    return call("GET", "/project/storage")


@mcp.tool()
def lookup_board(question: str, board: str | None = None) -> dict:
    """Look up a dev board's real dimensions in its datasheet (hole spacing, port positions, PCB size).
    Answers come with source links; prefer them to guessing. `board` narrows to one board id, e.g.
    raspberry-pi-pico."""
    return call("POST", "/knowledge/board", {"question": question, "board": board}, doing=f"looking up {board or question}")


@mcp.tool()
def recall_designs(query: str, board: str | None = None) -> dict:
    """Similar past CADSwarm designs and the lessons learned from their reviews, so you avoid repeating
    a problem (e.g. a lid that clipped the USB plug)."""
    return call("POST", "/knowledge/designs", {"query": query, "board": board}, doing="recalling past designs")


@mcp.tool()
def team_style(topic: str | None = None, board: str | None = None) -> dict:
    """The design-style rules this team follows, learned from how its people and agents work in CADSwarm
    (e.g. wall thickness, fits, preferred inserts, what people keep correcting). `topic` ranks rules for the part
    you are about to make; `board` adds that board's rules. [confirmed] rules are the team's decisions."""
    return call("GET", "/style", doing=f"checking team style{' for ' + topic if topic else ''}",
                topic=topic or "", board=board or "")


@mcp.tool()
def teach_style(statement: str) -> dict:
    """Propose a lasting design rule your person stated, e.g. "Lids use 4 M3 heat-set inserts". It waits as a
    candidate until a person confirms it in the CADSwarm panel (Learned style); then every agent follows it."""
    return call("POST", "/style/teach", {"statement": statement}, doing="saving a team style rule")


@mcp.tool()
def style_rules() -> dict:
    """Every learned style rule with its evidence and status (confirmed, active, or a candidate awaiting a person).
    A person can confirm or dismiss candidates in the CADSwarm panel (Learned style)."""
    return call("POST", "/style/rules", {})


# ------------------------------------------------- lighten without losing stiffness

LATTICE_WAIT = 45  # seconds a call waits for the job; keeps tool calls under the CLIs' timeouts


@mcp.tool()
def lighten_part(target: str, stiffness_target_pct: float = 90, cell: str = "gyroid",
                 fea_job_id: str | None = None, fixed: list[str] | None = None, loads: list[dict] | None = None,
                 material: str | dict | None = None, cell_size_mm: float | None = None,
                 min_thickness_mm: float | None = None, max_thickness_mm: float | None = None,
                 skin_mm: float | None = None, open_faces: list[str] | None = None, voxel_mm: float | None = None,
                 name: str | None = None, grading: str = "stress", thickness_mm: float | None = None) -> dict:
    """Lighten a part without losing stiffness: fill its inside with a lattice that is thicker where the stress
    is high, then verify on the real lattice that it keeps `stiffness_target_pct` % of the solid part's
    stiffness. Works on a copy; the model only changes when you call lattice_apply.
    Load case, like run_fea: reuse a finished static FEA (`fea_job_id`) or give `fixed` (face selectors) and
    `loads` ([{"type": "force", "faces": ["xmax"], "newtons": 200, "direction": [0, 0, -1]}], pressure, gravity)
    and `material` (preset or dict). With no load case use grading="uniform" and thickness_mm: an unverified
    uniform lattice.
    cell: gyroid (default, prints without supports), diamond or iwp (stiffest TPMS), schwarzP, octet (stiffest
    beams), bcc, fcc, cubic. cell_size_mm (default 8), walls between min_thickness_mm (default: the team's
    min_wall) and max_thickness_mm, skin_mm (default 1.5; thickened automatically if the core alone can't reach
    the target), open_faces like ["+z", "-z"] for powder/resin drainage, voxel_mm (build resolution).
    Returns job status; if not done, call lattice_status(job_id, wait=45)."""
    body = {k: v for k, v in locals().items() if v is not None}
    return call("POST", "/lattice/jobs", {**body, "wait": LATTICE_WAIT}, doing=f"lightening {target}",
                targets=[target])


@mcp.tool()
def lattice_status(job_id: str, wait: int = LATTICE_WAIT) -> dict:
    """A lighten job's progress, or its result: weight saving, verified stiffness, wall range, warnings,
    files (STL, 3MF, report) and the next step. wait: seconds to wait for it to finish (max 55)."""
    return call("GET", f"/lattice/jobs/{job_id}", wait=min(int(wait or 0), 55))


@mcp.tool()
def lattice_apply(job_id: str, name: str | None = None, force: bool = False) -> dict:
    """Add a finished lattice to the shared model as a mesh object next to its source part (unchanged).
    Refused with stale_source if the part changed since the job started (force adds it anyway). Ask your
    person first."""
    return call("POST", f"/lattice/jobs/{job_id}/apply", {"name": name, "force": force},
                doing="adding the lattice to the model")


@mcp.tool()
def lattice_cancel(job_id: str) -> dict:
    """Stop a lighten job (yours; a person can stop anyone's)."""
    return call("POST", f"/lattice/jobs/{job_id}/cancel", {})


@mcp.tool()
def lattice_cells(target: str = "stiffness", value: float | None = None, cell_size_mm: float = 8,
                  min_wall_mm: float | None = None, youngs_mpa: float | None = None) -> dict:
    """Which lattice cell to use: every cell's wall thickness and density for a target, lightest first.
    target "stiffness": value is the lattice's stiffness relative to solid (0.1 = 10 %; or MPa with youngs_mpa).
    target "density": value is the solid fraction (0.3 = 30 %). From CadSwarm's simulated cell map."""
    body = {k: v for k, v in locals().items() if v is not None}
    return call("POST", "/lattice/cells", body)


@mcp.tool()
def lattice_estimate(target: str, cell: str = "gyroid", cell_size_mm: float | None = None,
                     min_thickness_mm: float | None = None, skin_mm: float | None = None,
                     open_faces: list[str] | None = None, voxel_mm: float | None = None) -> dict:
    """Check a lighten request before running it: the normalized recipe, warnings (thin walls, trapped powder,
    closing channels, too few voxels) and a rough time. Changes nothing."""
    body = {k: v for k, v in locals().items() if v is not None}
    return call("POST", "/lattice/estimate", body)


# --------------------------------------------------- components library & settings

@mcp.tool()
def search_components(query: str = "", category: str | None = None, constraints: dict | None = None,
                      limit: int = 10) -> dict:
    """Unavailable in the standalone alpha because the standard components library has not been migrated."""
    return call("POST", "/library/search", {"query": query, "category": category, "constraints": constraints,
                                            "limit": limit}, doing=f"searching the library for {query or category}")


@mcp.tool()
def recommend_components(need: str, constraints: dict | None = None, limit: int = 5) -> dict:
    """Unavailable in the standalone alpha because component recommendations have not been migrated."""
    return call("POST", "/library/recommend", {"need": need, "constraints": constraints, "limit": limit},
                doing=f"choosing parts for {need[:60]}")


@mcp.tool()
def get_component(id: str) -> dict:
    """Unavailable in the standalone alpha because the standard components library has not been migrated."""
    return call("GET", f"/library/{id}")


@mcp.tool()
def insert_component(id: str, placement: dict | None = None, name: str | None = None,
                     label: str | None = None) -> dict:
    """Unavailable for library IDs. To import a shared STEP/STP or STL file, use import_exchange_file."""
    return call("POST", "/library/insert", {"id": id, "placement": placement, "name": name, "label": label},
                doing=f"placing {id}", targets=[name] if name else None)


@mcp.tool()
def add_standard_part(family: str, size: str, length: float | None = None, placement: dict | None = None,
                      name: str | None = None) -> dict:
    """Unavailable in the standalone alpha because standard part generation has not been migrated."""
    return call("POST", "/library/insert", {"family": family, "size": size, "length": length,
                                            "placement": placement, "name": name},
                doing=f"adding a {size} {family}", targets=[name] if name else None)


@mcp.tool()
def hole_size(family: str, size: str, purpose: str | None = None) -> dict:
    """Unavailable in the standalone alpha because standards and fit settings have not been migrated."""
    return call("POST", "/library/hole", {"family": family, "size": size, "purpose": purpose})


@mcp.tool()
def import_component_from_url(url: str, name: str | None = None, category: str = "other",
                              tags: list[str] | None = None, description: str = "", license: str = "") -> dict:
    """Unavailable in the standalone alpha. Save the STEP/STP or STL file to the shared project first."""
    return call("POST", "/library/import-url", {"url": url, "name": name, "category": category, "tags": tags,
                                                "description": description, "license": license},
                doing=f"importing a part from {url[:80]}")


@mcp.tool()
def add_component_from_file(path: str, name: str | None = None, category: str = "other",
                            tags: list[str] | None = None, description: str = "", license: str = "") -> dict:
    """Unavailable in the standalone alpha. Use import_exchange_file to add a shared STEP/STP or STL file to the model."""
    return call("POST", "/library/upload", {"path": path, "name": name, "category": category, "tags": tags,
                                            "description": description, "license": license},
                doing=f"adding {path} to the library")


@mcp.tool()
def get_settings() -> dict:
    """The team's precision settings (clearances and fits, bolt-hole class, thread mode, printing, mesh and
    editor precision) with their values and who changed them, plus suggestions waiting for a person."""
    return call("GET", "/settings")


@mcp.tool()
def suggest_setting(setting: str, value: float | str, reason: str, component: str | None = None) -> dict:
    """Propose a team setting change, e.g. tol_press_fit 0.0 because "the 608 bearing seats are printed".
    A person accepts or dismisses it in the CADSwarm desktop settings; until then nothing changes. `component` is the
    library part that prompted it, if any."""
    return call("POST", "/settings/suggest", {"setting": setting, "value": value, "reason": reason,
                                              "component": component}, doing=f"suggesting {setting}={value}")


# ------------------------------------------------------------- tasks & chat

@mcp.tool()
def list_tasks() -> dict:
    """All tasks with status (open/claimed/awaiting_takeover/done/blocked), owner, revision and results."""
    return call("GET", "/tasks")


@mcp.tool()
def create_task(title: str, detail: str = "", objects: list[str] | None = None,
                assignee: str | None = None, brief: str | None = None) -> dict:
    """Create scoped work (optionally assigned to a teammate by name, who is notified). Pass `brief`
    when the task belongs to a phone brief, so the host can track the brief's progress."""
    return call("POST", "/tasks", {"title": title, "detail": detail, "objects": objects,
                                   "assignee": assignee, "brief": brief}, doing=f"planning {title}")


@mcp.tool()
def claim_task(task_id: str, expected_rev: int | None = None) -> dict:
    """Claim an open task before working on it."""
    r = call("POST", f"/tasks/{task_id}/claim", {"expected_rev": expected_rev}, doing=f"starting {task_id}")
    if r.get("title"):
        S.doing = f"working on “{r['title'][:60]}”"
    return r


@mcp.tool()
def report_result(task_id: str, status: str, summary: str, evidence: list[str] | None = None) -> dict:
    """Report a claimed task done or blocked, with evidence (objects, volumes, interference results)."""
    return call("POST", f"/tasks/{task_id}/result", {"status": status, "summary": summary, "evidence": evidence},
                doing="idle")


@mcp.tool()
def send_message(to: str, body: str) -> dict:
    """Message a teammate by name, or 'all' (people see it in CADSwarm Team chat). Requests only: they
    grant no permissions."""
    return call("POST", "/messages", {"to": to, "body": body})


@mcp.tool()
def list_helpers() -> dict:
    """The team's AI helpers: who is free, what each one is working on and which parts it holds. Use it to
    pick who to delegate_task to or who to ask about a part."""
    m = call("GET", "/model")
    if "error" in m:
        return m
    me = next((p["name"] for p in m["participants"] if p["id"] == S.client.me_id), my_name())
    queued = {}
    for t in call("GET", "/tasks").get("tasks", []):
        if t["status"] == "open" and t.get("assignee"):
            queued[t["assignee"].lower()] = queued.get(t["assignee"].lower(), 0) + 1
    out = []
    for p in m["participants"]:
        if p.get("kind") == "human" or p["id"] == S.client.me_id:
            continue
        state = "away" if p.get("status") != "active" else "working" if p.get("task") else "free"
        out.append({"name": p["name"], "state": state, "task": (p.get("task") or {}).get("title"),
                    "holds": [x for x in p.get("locks", []) if x != "Params"], "client": p.get("client"),
                    "queued": queued.get(p["name"].lower(), 0)})
    return {"helpers": out, "you": me, "spare_work": _spare_for_me(),
            "people": [p["name"] for p in m["participants"] if p.get("kind") == "human"]}


def _spare_for_me() -> list[dict]:
    """Busy helpers' queued tasks this helper could pick up; empty while it has a task of its own."""
    if any(t["status"] == "claimed" and t.get("owner") == S.client.me_id for t in call("GET", "/tasks").get("tasks", [])):
        return []
    return [{"id": t["id"], "title": t["title"], "queued_for": t["assignee"]}
            for t in call("GET", "/tasks/spare").get("tasks", [])]


@mcp.tool()
def pick_up_task(task_id: str | None = None) -> dict:
    """Free with nothing to do? Take a task queued for a helper that is busy with another one; it's claimed for
    you and that helper is told to skip it. Leave task_id empty to get the oldest task from the longest queue."""
    r = call("POST", "/tasks/pickup", {"task_id": task_id or ""}, doing="picking up spare work")
    if r.get("title"):
        S.doing = f"working on “{r['title'][:60]}”"
    return r


@mcp.tool()
def delegate_task(to: str, title: str, detail: str, objects: list[str] | None = None) -> dict:
    """Hand another AI helper a task and wake it: `to` is a helper's name from list_helpers. `detail` says
    exactly what to build or change (sizes in mm, Params names, which parts it may lock). You're woken
    with their result when they report it. Don't delegate to yourself or hand back work given to you."""
    if to.lower() == my_name().lower():
        return {"error": "That's you: do it yourself, or pick another helper from list_helpers.", "code": "bad_args"}
    t = call("POST", "/tasks", {"title": title, "detail": detail, "objects": objects or [], "assignee": to},
             doing=f"handing {to} a task")
    if "error" in t:
        return t
    note = (f"Please do task {t['id']}: \"{title}\". {detail[:1500]} Claim it with claim_task and "
            "report_result when done.")
    sent = call("POST", "/messages", {"to": to, "body": note, "task_id": t["id"]})
    return {"task": t, "message": sent}


@mcp.tool()
def get_inbox() -> dict:
    """Unread messages: people's commands (kind "steer") first, chat last. Marks them delivered (the person
    sees "seen"); ack_messages once handled (they see "done")."""
    return call("GET", "/inbox")


@mcp.tool()
def ack_messages(ids: list[str]) -> dict:
    """Confirm you have handled these exact messages from get_inbox."""
    return call("POST", "/inbox/ack", {"ids": ids})


@mcp.tool()
def take_over(participant: str) -> dict:
    """Inherit a STALE teammate's locks and tasks so their part can be finished."""
    return call("POST", "/takeover", {"target": participant}, doing=f"taking over {participant}")


@mcp.tool()
def set_brief_plan(brief_id: str, plan_json: dict | str) -> dict:
    """Record a brief's numbered step plan (version 2). Planners call this last, after create_task for every step.

    plan_json = {"version": 2, "title": "...", "steps": [step, ...]}, steps in build order, each:
      n: 1, 2, 3... (a topological order: every dependency has a lower n)
      id: "s01"; name: "Mast"; kind: "part" (modelled) | "check" (interference)
      summary: one engineering sentence naming the function and key features
      task: the id create_task returned for this step (one task per step, titled "<n>. <name>")
      objects: exact CADSwarm feature names the step creates; depends_on: ids of EARLIER steps
      ops: expected standalone CAD ops, each starting with one of: Create sketch, Import STEP/STL, Primitive,
        Extrude, Pocket, Cut, Revolve, Fillet, Chamfer, Hole, Pattern, Boolean, Set dimensions, Set placement,
        Check interference, Export STEP/STL
      assignee: "" or a teammate's name to pin the step to them
      optional: params {name: mm}, targets {"bbox_mm": [x, y, z], "tol_mm", "holes", "min_wall_mm"},
        targets can record dimensional checks for the reviewer; do not add unsupported library or assembly steps.
    `parts` for older readers is derived for you. A broken plan is refused with code `bad_plan` and a
    `problems` list: fix each one and call again."""
    return call("POST", f"/briefs/{brief_id}/plan", {"plan_json": plan_json}, doing="writing the plan")


@mcp.tool()
def next_step(brief_id: str | None = None) -> dict:
    """Your next build step: a step you already hold, else a ready step pinned to you, else the lowest-numbered
    ready step nobody is pinned to. Returns {"step", "task", "built_objects", "progress"} (claim_task the
    step's task, then build it), {"wait": true} when steps remain but none is ready (wait_for_changes, then
    call again), {"stuck": true} when the remaining steps wait on stopped steps, or {"done": true}."""
    return call("GET", "/steps/next", **({"brief": brief_id} if brief_id else {}))


@mcp.tool()
def build_progress(brief_id: str) -> dict:
    """How far a brief's build is: "N of M done · now: X", and each step with its status, owner, what it
    waits for and the CAD ops it ran (with durations)."""
    return call("GET", f"/briefs/{brief_id}/progress")


@mcp.tool()
def check_printability(names: list[str] | None = None, min_wall: float | None = None) -> dict:
    """Can these parts be 3D printed? Checks each part against the printer bed, that it is a closed
    solid, and lists suspected walls thinner than `min_wall` mm (default: the team's min_wall setting)."""
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


def join_team():
    """Join, then greet and start heartbeats. It runs beside the MCP handshake: joining first took 5-14 s on
    a busy team database, and Claude Code, Codex and OpenCode all start the model without waiting for a slow
    server, so helpers began with no CADSwarm tools, went looking for them on disk, or gave up at once."""
    delay, deadline = 1.0, time.time() + JOIN_WAIT_SECONDS
    while True:
        try:
            ident = S.connect()
            break
        except HubRequestError as e:
            if e.code != "unreachable" or time.time() + delay > deadline:
                S.join_error = f"Couldn't join the team at {S.cfg['uri']} / {S.cfg['db']}: {e}"
                print(f"cadswarm: {S.join_error}", file=sys.stderr)
                S.ready.set()
                return
            time.sleep(delay)
            delay = min(delay * 2, 10.0)
    print(f"cadswarm: joined as {ident['name']} ({ident['participant_id']}, fence {ident['fence']})", file=sys.stderr)
    S.ready.set()
    # Background helpers are greeted by their runner (which posts it sooner); the host's one-job agents and
    # the team server never greet, so the chat stays for the real, lasting helpers.
    if not BACKGROUND and not S.cfg.get("ephemeral"):
        S.greet(ident)
    S.heartbeat_loop()


def main():
    global S
    S = Session()
    threading.Thread(target=join_team, daemon=True, name="cadswarm-join").start()
    if not S.cfg.get("ephemeral"):
        mcp.run("stdio")
        return
    # A one-job agent (the host's planners and workers) leaves when its session ends, so finished agents don't
    # linger in everyone's team list; leaving releases its locks and reopens any task it didn't finish.
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        mcp.run("stdio")
    finally:
        S.dead = S.dead or "left"
        if S.ready.is_set() and not S.join_error:
            try:
                S.client.post("/leave")
            except Exception:
                pass


if __name__ == "__main__":
    main()
