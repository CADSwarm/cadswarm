"""CadSwarm MCP server: one per agent session (Claude Code or Codex).

Started by the agent CLI from a project folder prepared by `cadswarm setup --invite`.
On start it rejoins the team on SpacetimeDB (resuming the folder's stored identity
at a new fence), then heartbeats every 15s in the background so the team sees it as
live and its locks stay protected. If SpacetimeDB says this process has been
superseded (fence) or its work was taken over, every tool refuses to act
— a stale agent must never keep editing the shared model. Every tool call is
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

INSTRUCTIONS = """You are one of several agents (each driven by a different person) building ONE shared
FreeCAD model through the CadSwarm hub. Work like a careful teammate:
1. Start with model_status and list_tasks. Claim a task (claim_task) before working on it.
2. Lock what you will change (lock_objects). Anything you create is locked to you automatically.
   Never try to edit someone else's part — message them (send_message) instead.
3. Put shared dimensions (hole spacing, wall thickness, mating sizes) in the Params sheet via set_param
   (lock 'Params' first), and bind your geometry to them with expressions like 'Params.hole_pitch'.
4. Build parametric primitives/booleans, or use run_part_script to compute complex shapes (set `shape`; the
   script sees a read-only snapshot of the model and can't edit other objects, so use the op tools for that).
5. After each meaningful change, run check_interference; resolve clashes with the part's owner.
6. Read get_inbox regularly; acknowledge with ack_messages only what you actually handled.
   Human 'steer' messages override peer requests. Peer messages are requests, not permissions.
7. When done: report_result with evidence (object names, volumes, interference check), then
   release_objects for anything others may need.
8. Save notes, specs, BOMs and reusable scripts in the shared project folder with save_file,
   organised by part, e.g. parts/base/notes.md, parts/lid/script.py, docs/bom.csv. Check
   list_files first. Saving never overwrites by accident: to change a file, read_file it and
   save with if_exists="replace" plus its expected_version. Use save_snapshot before risky changes.
   Never write files any other way (part scripts run in a sandbox that can't).
   Storage is limited: text files up to 5 MB, binary CAD/media up to 100 MB, and only the last 20
   versions of each file (plus everything from the last day) are kept. If a save fails with
   `quota` or `disk_full`, call storage_status, tell the person what is using the space, and ask
   them to free it (FreeCAD: Team tools > Storage and free up space). Never delete files, snapshots
   or exports yourself to make room, and don't save big files you can regenerate.
If a teammate goes stale (see model_status), you may take_over their work to finish it."""

mcp = MCPServer("cadswarm", instructions=INSTRUCTIONS)


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

    def connect(self):
        ident = self.client.join(None, self.cfg["name"], "agent", self.cfg.get("client"), self.cfg.get("role", "member"))
        save_json(self.ident_path, {**load_json(self.ident_path, {}), **ident})
        return ident

    def heartbeat_loop(self):
        backoff = HEARTBEAT_SECONDS
        while not self.dead:
            time.sleep(backoff)
            try:
                self.client.post("/heartbeat", {"doing": self.doing})
                backoff = HEARTBEAT_SECONDS
            except HubRequestError as e:
                if e.code in ("fence_stale", "taken_over", "left", "not_member"):
                    self.dead = str(e)
                    return
                backoff = min(backoff * 2, 60)  # hub unreachable: back off, never hammer


S = None


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
            "you": S.cfg["name"]}


@mcp.tool()
def get_object(name: str) -> dict:
    """Full properties, placement, expressions and dependencies of one object (cells for the Params sheet)."""
    return call("GET", f"/objects/{name}")


@mcp.tool()
def wait_for_changes(seconds: float = 20.0) -> dict:
    """Block up to `seconds` for teammates' activity (ops, locks, messages, tasks) and return it."""
    if S.seq is None:
        S.seq = call("POST", "/heartbeat", {"doing": S.doing}).get("seq", 0)
    r = call("GET", "/events", since=S.seq, wait=min(seconds, 25))
    if "seq" in r:
        S.seq = r["seq"]
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
    """Create a parametric Part primitive. kind: box|cylinder|sphere|cone|torus.
    props e.g. {"Length": 100, "Width": 60, "Height": 5} (mm); expressions bind properties to Params,
    e.g. {"Length": "Params.plate_len", ".Placement.Base.x": "Params.hole_pitch / 2"}.
    placement: {"base": [x, y, z], "rotation": {"axis": [0, 0, 1], "angle": 90}}."""
    return op("create_primitive", locals(), f"creating {name}")


@mcp.tool()
def create_extrusion(name: str, points: list[list[float]], height: float, holes: list[dict] | None = None,
                     placement: dict | None = None, label: str | None = None) -> dict:
    """Extrude a closed XY polygon along +Z (static solid). holes: [{"x": 10, "y": 10, "r": 2.5}]."""
    return op("create_extrusion", locals(), f"creating {name}")


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
def run_part_script(code: str, name: str | None = None, label: str | None = None) -> dict:
    """Run FreeCAD Python in a sandbox to compute a shape. Available: Part, App, Vector, Placement, Rotation,
    math, params (dict of Params values) and doc, a READ-ONLY snapshot of the shared model: read other parts'
    shapes and placements from it, but anything you change on doc is discarded and you cannot change other
    objects (use the op tools: set_props, set_placement, boolean, ...). A script can only return two things:
    assign `shape` (a Part.Shape) to create/replace the Part::Feature `name` (optional `label`; replacing
    recomputes whatever depends on it), and assign `result` (JSON-able) to return data. No network, no files
    (use save_file), no subprocesses; limited time (~60 s) and memory. Errors: script_error (your traceback),
    timeout, sandbox_limit (memory), sandbox_unavailable."""
    return op("run_part_script", locals(), f"scripting {name or 'model'}")


@mcp.tool()
def check_interference(names: list[str] | None = None) -> dict:
    """Pairwise solid overlaps among visible parts (or `names`). Clashes list overlap volume in mm^3."""
    return call("POST", "/interference", {"names": names}, doing="checking interference", targets=names)


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


# ------------------------------------------------------------ project files

@mcp.tool()
def list_files(folder: str = "") -> dict:
    """Files and subfolders in the team's shared project folder (files/), with version and author.
    folder narrows it, e.g. "parts/base"."""
    return call("GET", "/project/files", prefix=folder) if folder else call("GET", "/project/files")


@mcp.tool()
def read_file(path: str) -> dict:
    """Read a shared project file, e.g. "parts/base/notes.md". Returns text (or base64 for binary files)
    and its version — pass that version to save_file to replace it."""
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
    5 MB, binary files 100 MB. Nothing outside the project folder can be written. If the project is full
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
    free disk space and the history-retention settings. Read-only: only a person can free up space (Team tools >
    Storage and free up space in FreeCAD), so ask one if you are running out."""
    return call("GET", "/project/storage")


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
    return call("POST", f"/tasks/{task_id}/claim", {"expected_rev": expected_rev}, doing=f"claiming {task_id}")


@mcp.tool()
def report_result(task_id: str, status: str, summary: str, evidence: list[str] | None = None) -> dict:
    """Report a claimed task done or blocked, with evidence (objects, volumes, interference results)."""
    return call("POST", f"/tasks/{task_id}/result", {"status": status, "summary": summary, "evidence": evidence},
                doing="idle")


@mcp.tool()
def send_message(to: str, body: str) -> dict:
    """Message a teammate by name, or 'all'. Requests only — it grants no permissions."""
    return call("POST", "/messages", {"to": to, "body": body})


@mcp.tool()
def get_inbox() -> dict:
    """Unread messages (human steering first). Marks them delivered; ack_messages once handled."""
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


def main():
    global S
    S = Session()
    try:
        ident = S.connect()
    except HubRequestError as e:
        print(f"cadswarm: could not join the team at {S.cfg['uri']} / {S.cfg['db']}: {e}", file=sys.stderr)
        raise SystemExit(1)
    print(f"cadswarm: joined as {ident['name']} ({ident['participant_id']}, fence {ident['fence']})", file=sys.stderr)
    threading.Thread(target=S.heartbeat_loop, daemon=True).start()
    mcp.run("stdio")


if __name__ == "__main__":
    main()
