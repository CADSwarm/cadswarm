# CadSwarm on SpacetimeDB: overview, shared contract and team split

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement each workstream plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing FastAPI/SQLite CadSwarm hub into a SpacetimeDB-backed system where a phone brief becomes a printable part, built live by a swarm of agents on one FreeCAD model.

**Architecture:** A SpacetimeDB module holds every rule and all shared state. A Rust host runs queued ops in the existing FreeCAD worker and orchestrates briefs. The Python MCP server talks to SpacetimeDB over HTTP through a compatibility adapter, so the agent tools keep their current shapes. A Vite web app shows everything live, with animated pointers.

**Tech Stack:** SpacetimeDB 2.x (Rust module, Rust client SDK, TypeScript client SDK `spacetimedb`), Rust stable, Python 3.11+ (stdlib only in the client paths), FreeCAD 1.x, Vite + TypeScript + three.js, Claude Code headless (`claude -p`), Factory `droid-sdk` (P1).

Spec: `docs/superpowers/specs/2026-10-23-cadswarm-spacetime-design.md`.

## Global Constraints

- Submission deadline: **Sun 2026-10-25 9:00 AM PT**. Code freeze Sun 06:00.
- All code is written during the event. Pushin's iroh code is a reference for patterns only; never copy it.
- SpacetimeDB tables are all private. Clients read only through the `v_*` views listed below.
- Column types used anywhere a client reads: `u64`, `u32`, `f64`, `f32`, `bool`, `String`, `Vec<String>`, `Vec<u8>`, `Vec<f64>`, `Vec<f32>`. No `Option`, no enums, no `Timestamp` columns. "None" is `""` for strings and `0` for ids. (Python decodes SATS JSON by hand; these types keep that trivial.) The one exception is `member.identity: Identity`, which clients never need to read.
- Times are `u64` milliseconds since the Unix epoch, from `ctx.timestamp`.
- Status and kind values are lowercase strings (lists below).
- Reducer errors are `Err("<code>: <message>")`. Clients split on the first `": "` to get the code.
- Every reducer except `init`, the two lifecycle reducers, `redeem_invite`, `rejoin` and `sweep` takes `fence: u32` as its first argument and rejects a stale fence. The fence is never optional. Only the MCP server (and the web app on connect) calls `rejoin`; the prompt hook and every helper reuse the fence stored by the live process and never rejoin, or they would supersede it.
- The FreeCAD worker saves `model.FCStd` before committing its transaction; if the save fails (file locked by OneDrive or antivirus), the op aborts so memory and disk never diverge.
- Project file paths are compared case-insensitively and with Windows 8.3 short names resolved (`NOTES.md` is `notes.md`; `LONGFI~1.MD` is the long name), in `ProjectFiles` and anywhere a path is used as a key.
- IDs a client creates are strings with a prefix and random hex: op `o_` + 12 hex, task `t_` + 8, message `m_` + 12, brief `b_` + 8, review `r_` + 8, handoff `h_` + 8. Member ids and event seqs are `u64` from `auto_inc`.
- Heartbeat every 15 s; stale after 30 s without activity (WebSocket clients that are connected never go stale); orphaned after 600 s stale.
- No secret ever goes into the repo, the web app bundle, or a phone. Ticket tokens travel in the URL fragment only.
- Database name: `cadswarm` locally; `cadswarm-<team>` on Maincloud.
- SpacetimeDB is pinned to **2.11** everywhere: the module crate (`spacetimedb = "=2.11.0"`), the host SDK (`spacetimedb-sdk = "=2.11.0"`), the web SDK (`spacetimedb@~2.11.0`), and the CLI that generates bindings (`spacetime version install 2.11.0`, then `spacetime version use 2.11.0`). 2.12 and later need rustc 1.97; the team boxes have 1.96. Move everyone together or not at all.

---

## Team split

| Person | Plan | Owns | Starts with (before the contract lands) |
|---|---|---|---|
| **A** | `2026-10-23-cadswarm-01-module.md` | `rules/`, `module/`, publishing, generated bindings, smoke tests, Maincloud deploy | Everything: A is the critical path for the first 3 hours |
| **B** | `2026-10-23-cadswarm-02-host.md` | `host/` (Rust): worker driver, executor, previews, exports, brief orchestration, agent spawning | The FreeCAD worker driver (needs no SpacetimeDB) |
| **C** | `2026-10-23-cadswarm-03-python.md` | `cadswarm/` (Python): SpaceClient, ticket, setup, the hub-compatible adapter, MCP and hook port, new worker queries, board library, Droid reviewer | SpaceClient and ticket code against a fake HTTP server; worker `mesh` and `printability` queries |
| **A after M0** (or whoever is free) | `2026-10-23-cadswarm-05-elastic.md` | Elastic Serverless setup, datasheet ingestion, Agent Builder board expert, lessons Workflow, the host's Python helper process, wiring into briefs and agents | Task 1 needs only an Elastic sign-up, so it can start any time |
| **D** | `2026-10-23-cadswarm-04-web.md` | `web/`: pairing, live panels, three.js viewer, pointer animation, params, STL download | The viewer and pointer animation against a mock store |

Branch per person (`feat/module`, `feat/host`, `feat/python`, `feat/web`), small PRs into `main`, rebase often. Only person A edits `rules/` and `module/`; contract changes go through A and get a line in the "Contract changes" log at the bottom of this file.

## Milestones

| When (PT) | Milestone | Proof |
|---|---|---|
| Fri +3 h | **M0 contract live.** Module published to A's local SpacetimeDB; Rust and TS bindings committed to `main` | `python module/tests/smoke.py` passes |
| Sat 10:00 | **M1 one op end to end.** An agent's `create_primitive` goes Python → SpacetimeDB → host → FreeCAD → SpacetimeDB → web shows the object and its preview | Screen recording |
| Sat 12:00 | **First print.** Export any STL and submit it to the venue printer queue form (slots are 3.5 h; queues fill) | Queue confirmation |
| Sat 16:00 | **M2 brief flow.** Phone brief → planner → 3 workers → STL, on Maincloud, with agent pointers animating | Full run from a phone on cellular |
| Sat 23:00 | **M3 P1.** Droid reviewer loop, human pointers, printability checks, local-mode reach plan started | Reviewer catches a seeded clash |
| Sun 06:00 | **Freeze.** README (incl. "Where Droid runs"), Devpost draft, demo video | — |
| Sun 08:30 | Submit on Devpost | — |

## Repository layout after this work

```
rules/                 pure Rust coordination rules + native tests       (A)
module/                SpacetimeDB module: tables, views, reducers        (A)
module/tests/smoke.py  end-to-end reducer test against local SpacetimeDB  (A)
host/                  cadswarm-host (Rust)                               (B)
host/src/module_bindings/   generated by A, committed                     (A)
cadswarm/              Python package (MCP server, CLI, adapter, worker)  (C)
cadswarm/hub/          DELETED at M1                                      (C)
boards/boards.json     board library, read by host, web and Python        (C)
web/                   Vite + TS + three.js app                           (D)
web/src/module_bindings/    generated by A, committed                     (A)
```

---

## The shared contract

### Tables (module, all private)

| Table | Primary key | Indexed | Other columns |
|---|---|---|---|
| `workspace` | `id: u64` | — | `name: String`, `owner: Identity`, `bed_x_mm: f64`, `bed_y_mm: f64`, `bed_z_mm: f64` |
| `member` | `id: u64` auto_inc | `ws: u64`, `identity: Identity` | `name`, `kind`, `client`, `role`, `status`, `status_text: String`, `fence: u32`, `last_seen_ms: u64`, `joined_ms: u64`, `stale_since_ms: u64`, `taken_over_by: u64`, `endpoint_id: String`, `online: bool`, `context_seq: u64` |
| `invite` | `token_hash: String` | `ws` | `kind`, `role: String`, `created_by: u64`, `expires_ms: u64`, `used_by: u64` |
| `cad_lock` | `key: String` (`"{ws}:{object}"`) | `ws`, `holder: u64` | `object: String`, `acquired_ms: u64` |
| `cad_object` | `key: String` (`"{ws}:{name}"`) | `ws` | `name`, `label`, `type_id: String`, `rev: u32`, `placement_json: String`, `bbox: Vec<f64>` (6 values or empty), `volume: f64`, `visible: bool`, `broken: bool`, `uses: Vec<String>`, `used_by: Vec<String>`, `expressions_json: String`, `last_actor: u64`, `updated_ms: u64` |
| `param` | `key: String` (`"{ws}:{alias}"`) | `ws` | `alias`, `value: String`, `num: f64`, `updated_ms: u64` |
| `op` | `id: String` | `ws` | `by: u64`, `fence: u32`, `kind`, `args_json`, `expected_revs_json: String`, `allowed: Vec<String>`, `mutating: bool`, `status`, `result_json`, `error_code`, `error: String`, `created_ms`, `started_ms`, `finished_ms: u64` |
| `task` | `id: String` | `ws` | `title`, `detail: String`, `objects: Vec<String>`, `status: String`, `owner: u64`, `created_by: u64`, `rev: u32`, `result_json`, `assignee`, `brief: String`, `created_ms`, `updated_ms: u64` |
| `message` | `id: String` | `ws` | `from: u64` (0 = system), `to: String` (`"all"`, `"member:<id>"`, or `"@<lowercase name>"` until that name joins), `kind`, `body`, `task_id: String`, `created_ms: u64` |
| `receipt` | `key: String` (`"{message_id}:{member}"`) | `ws` | `message_id: String`, `member: u64`, `state: String`, `at_ms: u64` |
| `team_event` | `seq: u64` auto_inc | `ws` | `kind: String`, `actor: u64`, `data_json: String`, `at_ms: u64` |
| `activity` | `id: u64` auto_inc | `ws`, `member: u64` | `kind`, `summary: String`, `at_ms: u64` |
| `pointer` | `member: u64` | `ws` | `object: String`, `point: Vec<f32>` (3 values or empty), `mode`, `label`, `op: String`, `seq: u32`, `at_ms: u64` |
| `brief` | `id: String` | `ws` | `by: u64`, `text`, `board`, `status`, `plan_json`, `stl_hash: String`, `created_ms`, `updated_ms: u64` |
| `handoff` | `id: String` | `ws` | `from: u64`, `note`, `trigger`, `status: String`, `taker: u64`, `context_json: String`, `created_ms`, `updated_ms: u64` |
| `review` | `id: String` | `ws` | `brief: String`, `round: u32`, `passed: bool`, `findings_json: String`, `at_ms: u64` |
| `preview` | `key: String` (`"{ws}:{object}"`) | `ws` | `object: String`, `rev: u32`, `mesh: Vec<u8>` (binary STL ≤ 200 KB) |
| `artifact` | `hash: String` (sha256 hex) | `ws` | `name`, `mime: String`, `size: u64`, `bytes: Vec<u8>` (≤ 5 MB, else empty), `brief: String`, `created_ms: u64` |
| `sweep_schedule` | `scheduled_id: u64` auto_inc | — | `scheduled_at: ScheduleAt` (every 5 s) |

**Value lists:**
- `member.kind`: `human`, `agent`, `host`, `reviewer`. `member.role`: `lead`, `member`. `member.status`: `active`, `stale`, `left`, `taken_over`. `member.client`: free text (`phone`, `claude`, `codex`, `droid`, `host`, `workbench`).
- `op.status`: `queued`, `running`, `done`, `rejected` (lock violation), `failed`.
- `task.status`: `open`, `claimed`, `awaiting_takeover`, `done`, `blocked` (same as the current hub).
- `message.kind`: `message`, `steer`, `notice`, `handoff`, `help`, `result`. `receipt.state`: `delivered`, `consumed`.
- `activity.kind`: `tool_call`, `tool_result`, `say`, `status`.
- `pointer.mode`: `pointing`, `inspecting`, `editing`, `idle`.
- `brief.status`: `new`, `planning`, `building`, `reviewing`, `ready`, `failed`.
- `handoff.trigger`: `now`, `on_leave`. `handoff.status`: `waiting` (armed, person still here), `open`, `accepted`, `cancelled`.
- `handoff.context_json`: `{"from": name, "note", "pointer": {"object", "point", "mode", "label"} | null, "locks": [...], "tasks": [{"id", "title", "detail", "status", "objects"}], "recent_activity": [...], "recent_ops": [{"kind", "args", "status", "error"}], "recent_messages": [{"to", "body"}]}`.

### Views (what clients read and subscribe to)

Every view returns `Vec<Row>` and is `public`. Each looks up the caller's latest `member` row whose status is `active` or `stale`, and returns that workspace's rows; a non-member gets an empty list.

`v_workspace`, `v_me` (the caller's own member row), `v_member`, `v_lock`, `v_object`, `v_param`, `v_op`, `v_task`, `v_message`, `v_receipt`, `v_event`, `v_activity`, `v_pointer`, `v_brief`, `v_review`, `v_preview`, `v_artifact`, `v_handoff`.

### Reducers

`[host]` = caller must be a `host` member; `[reviewer]` = caller must be a `reviewer` member.

| Reducer | Args after `ctx` | Effect |
|---|---|---|
| `init` (lifecycle) | — | workspace 1 (bed 180 × 180 × 180 mm); the publisher becomes member `host` (kind `host`, role `lead`); sweep every 5 s |
| `on_connect` (client_connected) | — | caller's member `online = true` |
| `on_disconnect` (client_disconnected) | — | `online = false`, `last_seen_ms = now`, pointer `idle` |
| `redeem_invite` | `token: String, name: String, client: String, endpoint_id: String` | Consume invite (sha256 of `token`), create member with the invite's kind and role; deliver mail queued for `@name`; inherit a stale namesake's work |
| `rejoin` | `client: String` | `active`/`stale` member: `fence += 1`, active, resume tasks. `left`/`taken_over`: new member row with the same name if free |
| `heartbeat` | `fence, status_text: String` | `last_seen_ms = now`; stale → active and resume tasks |
| `set_context_seq` | `fence, seq: u64` | Records the prompt hook's event cursor |
| `leave` | `fence` | Release locks, reopen tasks, status `left` |
| `create_invite` | `fence, token_hash: String, kind: String, role: String, ttl_secs: u64` | Only hosts may invite `host` or `reviewer`; TTL ≤ 86 400 |
| `revoke_member` | `fence, member_id: u64` | Humans and hosts only; same as that member leaving |
| `take_over` | `fence, target: String, force: bool` | Target by name; stale only unless a human forces |
| `lock_objects` | `fence, names: Vec<String>` | All or nothing |
| `release_objects` | `fence, names: Vec<String>, force: bool` | Humans may force |
| `submit_op` | `fence, op_id: String, kind: String, args_json: String, expected_revs_json: String` | Pre-checks locks and revisions, then queues; reviewers may only queue queries |
| `start_op` [host] | `fence, op_id: String` | `queued` → `running` |
| `complete_op` [host] | `fence, op_id: String, ok: bool, result_json: String, created: Vec<String>, modified: Vec<String>, deleted: Vec<String>, recomputed: Vec<String>, broken: Vec<String>, error_code: String, error: String` | Commit (revisions, creator locks, notify owners of recomputed parts) and write the full response into `op.result_json` |
| `sync_objects` [host] | `fence, objects_json: String, params_json: String` | Upsert object details and params from the worker's `status` |
| `put_preview` [host] | `fence, object: String, mesh: Vec<u8>` | Store a preview at the object's current revision |
| `put_artifact` [host] | `fence, hash: String, name: String, mime: String, bytes: Vec<u8>, brief: String` | Store an export |
| `create_task` | `fence, task_id: String, title: String, detail: String, objects: Vec<String>, assignee: String, brief: String` | Notifies the assignee (queued if not joined) |
| `claim_task` | `fence, task_id: String, expected_rev: u32` | `expected_rev` 0 skips the check |
| `report_task` | `fence, task_id: String, status: String, summary: String, evidence: Vec<String>` | `done` or `blocked`; owner only; notifies the creator |
| `send_message` | `fence, message_id: String, to: String, body: String, kind: String, task_id: String` | A human's `message` becomes `steer` |
| `mark_delivered` | `fence, message_ids: Vec<String>` | Inbox read |
| `ack_messages` | `fence, message_ids: Vec<String>` | Must have been delivered |
| `log_activity` | `fence, kind: String, summary: String, target: Vec<String>` | Also points the caller's pointer at `target[0]` |
| `move_pointer` | `fence, object: String, point: Vec<f32>, mode: String, label: String` | Human pointers |
| `submit_brief` | `fence, brief_id: String, text: String, board: String` | Status `new` |
| `set_brief` | `fence, brief_id: String, status: String, plan_json: String` | Empty `status` or `plan_json` leaves that field unchanged |
| `set_brief_stl` [host] | `fence, brief_id: String, hash: String` | Status `ready` |
| `report_review` [reviewer] | `fence, review_id: String, brief_id: String, round: u32, passed: bool, findings_json: String` | Stores the review |
| `set_bed` | `fence, x_mm: f64, y_mm: f64, z_mm: f64` | Printer bed size |
| `request_handoff` [human] | `fence, handoff_id: String, note: String, trigger: String` | `now` → `open` with a snapshot; `on_leave` → `waiting` (replaces the person's earlier waiting one) |
| `cancel_handoff` | `fence, handoff_id: String` | Requester only, before it is accepted |
| `accept_handoff` [agent] | `fence, handoff_id: String` | `open` → `accepted`; moves the person's locks and tasks to the caller (retiring the person if they are stale); the caller's pointer starts where theirs was; messages the person |
| `sweep` (scheduled) | `row: SweepSchedule` | Stale and orphan transitions (a stale person's `waiting` handoff opens), expired invites, activity pruned to 200 per member |

**Error codes:** `not_member`, `fence_stale`, `taken_over`, `left`, `not_allowed`, `bad_args`, `bad_invite`, `already_member`, `name_in_use`, `unknown_participant`, `not_available`, `locked`, `not_locked`, `stale_rev`, `unknown_op`, `unknown_task`, `not_claimable`, `not_owner`, `not_delivered`, `not_found`.

### Op kinds (the `kind` of `submit_op`)

- **Mutating** (need locks; run in a FreeCAD transaction): `create_primitive`, `create_extrusion`, `boolean`, `set_property`, `set_props`, `set_expression`, `set_placement`, `delete`, `set_param`, `run_part_script`. Args are exactly what the current MCP tools send.
- **Queries** (no locks): `get_object` `{name}`, `interference` `{names, min_volume}`, `export` `{format: "stl"|"step", names, label}`, `dependents` `{names}`, `printability` `{names, bed: [x, y, z], min_wall: 1.2}`; Elastic lookups `lookup_board` `{question, board}` and `recall_designs` `{query}`; project file reads `list_files` `{folder}`, `read_file` `{path}`, `file_history` `{path}`.
- **File writes** (no FreeCAD locks; refused for reviewers): `save_file` `{path, text | base64, if_exists, expected_version}`, `make_folder` `{path}`, `move_file` `{from_path, to_path}`, `delete_file` `{path}`, `restore_file` `{path, version}`, `snapshot` `{label}`. The host runs these with `cadswarm/hub/files.py`'s `ProjectFiles` rules (sandboxed to the project's `files/`, versioned history, never silently overwriting); results are the shapes those methods return today.

**`op.result_json` on success:**
- mutating: `{"result": <worker result>, "created": [...], "modified": [...], "deleted": [...], "recomputed": [...], "broken": [...], "revs": {"Name": 2}, "seq": 17, "notified": {"bob-codex": ["Mount"]}}` (the shape `Hub._commit` returns today).
- query: the worker's `result` object. For `export` the host replaces it with `{"hash": "...", "name": "model-<seq>.stl", "size": 12345, "objects": [...]}`.

### Worker protocol additions (C implements, B calls)

The existing JSON-lines protocol in `cadswarm/worker/freecad_worker.py` gains two queries:
- `mesh` `{"names": [...], "tolerance": 0.2, "max_bytes": 200000}` → `{"meshes": {"Name": "<base64 binary STL>"}}`. Names without a solid shape are omitted.
- `printability` `{"names": [...] | null, "bed": [x, y, z], "min_wall": 1.2}` → `{"ok": bool, "objects": [{"name", "size": [x, y, z], "fits_bed": bool, "closed_solid": bool, "thin_wall_suspects": [{"face_a": int, "face_b": int, "distance": float}]}]}`.

### Tickets (pairing)

A ticket is base32 (RFC 4648, uppercase, no padding) of UTF-8 JSON:
`{"v": 1, "uri": "<SpacetimeDB base URL>", "db": "<database name>", "token": "<raw invite token>", "kind": "human|agent|reviewer|host", "role": "member|lead", "host_addr": ""}`.
The raw token is 32 random bytes as lowercase hex; the module stores only its sha256 hex. The phone link is `<web app URL>/#t=<ticket>`.

### Board library: `boards/boards.json`

```json
{
  "<board id>": {
    "label": "Raspberry Pi 4 Model B",
    "pcb": [85.0, 56.0, 1.4],
    "holes": [[3.5, 3.5], [61.5, 3.5], [3.5, 52.5], [61.5, 52.5]],
    "hole_d": 2.7,
    "clear_top": 17.0,
    "clear_bottom": 3.0,
    "ports": [{"name": "usb_c", "side": "-y", "center": [11.2, 2.0], "size": [9.5, 3.5]}]
  }
}
```
Coordinates in mm from the PCB's lower-left corner, viewed from the top. `side` is the edge a port faces (`-x`, `+x`, `-y`, `+y`); `center` is [position along that edge, height above the PCB's top face]. The host seeds these Params per brief: `pcb_l`, `pcb_w`, `pcb_t`, `hole_d`, `hole_N_x`, `hole_N_y` (N from 1), `clear_top`, `clear_bottom`, `<port>_pos`, `<port>_z`, `<port>_w`, `<port>_h`, plus defaults `wall` = 2, `gap` = 0.5, `standoff_h` = 4.

### Local endpoints for development

- SpacetimeDB: `http://127.0.0.1:3000`, database `cadswarm`.
- Web dev server: `http://localhost:5173`.

## Integration checkpoints

1. **M0 (A → everyone):** `module/tests/smoke.py` green; bindings in `host/src/module_bindings/` and `web/src/module_bindings/` on `main`. C runs `tests/test_space_live.py` against A's published module.
2. **M1 (B + C):** `cadswarm-host run` on the FreeCAD machine; `cadswarm setup --invite ...` in a scratch folder; the scratch agent's `create_primitive` returns `created: ["Box"]` and `v_object` has `Box` with a preview.
3. **M1 (D):** the web app pairs with a human invite and shows `Box` with its preview.
4. **M2:** a brief from the phone completes on Maincloud.

## Contract changes log

| When | Who | Change |
|---|---|---|
| 2026-10-23 | from 3d-cad-08's testing of fddfa28 | Fence never optional; hooks reuse the live fence and never rejoin; the worker saves before committing; file paths are case-insensitive keys on Windows (see Global Constraints) |
| 2026-10-23 | plan author | Team decision: SpacetimeDB is the core; the teammate's FreeCAD setup guide, `cadswarm-N:` invite codes and file tools stay on top of it. Added op kinds `lookup_board`, `recall_designs` (Elastic, via the host) and the project-file ops; `rules::is_write` refuses file writes for reviewers |
| 2026-10-23 | plan author | Added `handoff` table, `v_handoff`, `request_handoff`, `cancel_handoff`, `accept_handoff`; disconnects keep the pointer's object and point (mode `idle`) so a handoff snapshot knows where the person was |
| 2026-10-23 | from 3d-cad-08's M0 build (`feat/module` 49eb37d) | No contract changes: 19 tables, 18 views, 33 reducers as specified. SpacetimeDB pinned to 2.11 everywhere (see Global Constraints). `sweep` has no `ctx.identity()` guard; in 2.x only the database can invoke a scheduled reducer. Still unverified until a server runs: the HTTP `/sql` response shape and how a reducer `Err` text arrives over HTTP (plan 03 Task 1 depends on both) |
