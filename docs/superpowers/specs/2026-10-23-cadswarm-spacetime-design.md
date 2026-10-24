# CadSwarm on SpacetimeDB: describe it, the swarm builds it, print it

Date: 2026-10-23 (Cal Hacks 13.0; submission deadline Sun 2026-10-25 9:00 AM PT)
Status: approved. Plans in `docs/superpowers/plans/2026-10-23-cadswarm-0*.md`; the overview's
contract and "Contract changes log" win over this document where they differ.
Team decisions (2026-10-23): SpacetimeDB is the core; the FreeCAD beginner setup from commit
`fddfa28` stays on top of it; Elastic is core functionality.

## The problem and who has it

Hackathon hardware teams need an enclosure, mount or bracket for their ESP32, Pi or Arduino. There
is a 3D printer queue in the building with 3.5 hour slots, and almost nobody on the team knows CAD.
Today they either skip the enclosure, tape the board to cardboard, or lose hours learning FreeCAD.

CadSwarm lets them describe the part on their phone in plain words. A swarm of agents designs the
pieces in parallel on one live FreeCAD model, a reviewer agent checks fit and printability, and the
team gets a printable STL. Nobody touches CAD unless they want to.

## What makes it work

- **One shared world state in SpacetimeDB.** People, agents, locks, the object tree, shared
  parameters, tasks, messages, live agent activity and preview meshes all live in one SpacetimeDB
  database. Every write is a reducer, every view is a live subscription. Delete SpacetimeDB and
  nothing coordinates, nothing updates, nobody can build.
- **FreeCAD is the geometry engine.** It runs on one host machine and executes ops that agents
  queue in SpacetimeDB, with the existing lock-checked transactional rollback.
- **Agents are brought by the people using it.** Claude Code and Codex connect through the existing
  MCP server; the host can also spawn headless Claude Code workers on demand for a brief.
- **Real dimensions, not guessed ones.** A board library (ESP32 DevKit, ESP32-S3, Raspberry Pi 4/5,
  Pi Pico, Arduino Uno) seeds the shared `Params` sheet with measured sizes, port positions and hole
  spacing, so agents bind geometry to facts.
- **Any board, not just the ones we measured.** Elastic indexes board datasheets and every finished
  design; an Elastic agent returns a board's dimensions with a source link for each number, and
  lessons from past designs reach the planner before it starts.
- **No terminal for beginners.** The FreeCAD setup guide starts a team or joins one with a pasted
  invite code; everything runs from FreeCAD's own Python plus two downloaded programs.
- **Pick up where someone left off.** A person can hand their work to an AI, now or when they leave.
- **Pair by scanning a QR.** The phone opens a web page and is in. No app install, no account, no
  API key on any device but the host.
- **Free and local by default, Maincloud when it matters.** SpacetimeDB runs on the host with no
  account. Teams that cannot afford to lose work when a laptop dies switch to a Maincloud database.
  Same module, same clients, only the address changes.

## Tracks this serves

| Track | How the project meets it |
|---|---|
| Spacetime | SpacetimeDB is the only coordination and state layer: AI agents and people coordinating in a persistent shared world state, synced live to every device. |
| Anthropic | Named community (hackathon hardware teams without CAD skills), a real problem, works end to end, and Claude's failure modes are caught by design: lock and rollback, shared measured params, worker timeouts, a reviewer. |
| Factory | A Droid reviewer started by SpacetimeDB events, restricted to read and report tools, its every step streamed live into the app. |
| Most Technically Complex / Overall | Multi-agent CAD on one live model, transactional rollback in FreeCAD, a real-time backend, peer-to-peer file mesh. |
| Best Hardware | A part the swarm designed, printed at the venue, on the judging table. |
| Elastic | Messy datasheets turned into board specs agents act on: hybrid search with JINA embeddings and the JINA reranker on Elastic Cloud Serverless, an Agent Builder agent, and a Workflow that distils lessons from past designs. |

## Scope and priorities

| Priority | What |
|---|---|
| **P0 (the demo path)** | SpacetimeDB module replacing the hub's SQLite coordination; Rust host running FreeCAD ops and project-file ops from the queue and publishing previews and STL; Python MCP server and CLI moved to the SpacetimeDB HTTP API, keeping the file tools; the FreeCAD setup guide starting SpacetimeDB and the host instead of the hub; phone web app (pair, brief, live tree, activity, tasks, 3D preview, STL download); board library; swarm on demand for a brief; both deployment modes selectable, with the demo running on Maincloud. Live presence for agents: animated agent pointers, lock tints and edit highlights in the 3D view. Elastic board knowledge and design memory. |
| **P1** | Handing off to an AI: an agent picks up exactly where a person left off (now, or automatically when they leave). Live presence for people: human pointers and selections from phones and laptop browsers. Local-mode reach: the host gateway, iroh tunnel for laptops and agents (pair once with a ticket), quick tunnel for phone browsers. Droid reviewer (fit, interference, printability); printer bed presets and checks. |
| **P2** | iroh file mesh (full `.FCStd` and STEP between laptops and hosts); everyone's pointers drawn inside FreeCAD's panel. |

Not in scope: CAD state across several FreeCAD hosts, roles beyond lead and member, a native mobile
app, direct geometry editing in the phone UI.

## Architecture

```
        SpacetimeDB module "cadswarm": local on the host, or on Maincloud
     members · locks · objects · params · op queue · tasks · messages ·
     events · agent activity · preview meshes · briefs      (all private,
                   read through member-only views, written only by reducers)
        ▲ WebSocket (live)        ▲ HTTP (reducers + SQL)        ▲ WebSocket (live)
        │                         │                              │
 ┌──────┴───────┐       ┌─────────┴──────────┐         ┌─────────┴──────────┐
 │ phone web app│       │ agents: Claude Code │         │ cadswarm-host (Rust)│
 │ TS + three.js│       │ / Codex / Droid via │         │  op executor ──────►│ FreeCAD worker
 │ GitHub Pages │       │ cadswarm MCP server │         │  previews, STL      │ (existing, JSON
 └──────────────┘       └────────────────────┘         │  swarm spawner      │  lines over stdio)
                                                        │  reviewer trigger   │
                                                        │  iroh files (P2)    │
                                                        └─────────────────────┘
```

### Components

| Component | Language | Responsibility |
|---|---|---|
| `module/` | Rust (SpacetimeDB module) | Tables, member-only views, reducers holding every rule from the current `hub/core.py`, the scheduled sweep. Rules live in plain functions with native unit tests. |
| `host/` (`cadswarm-host`) | Rust | Subscribes with the Rust SDK; claims and runs queued ops in the FreeCAD worker (port of `hub/worker_client.py`); writes results, revisions, object rows and preview meshes; exports STL; spawns headless agents for briefs; starts the reviewer. In local mode it also supervises `spacetime start` and runs the gateway (see Deployment modes). P2 serves files over iroh. Holds every service key in the OS keychain. |
| `cadswarm-host tunnel` | Rust (same binary, client mode) | On a teammate's laptop in local mode: listens on `127.0.0.1:<port>` and carries each connection over iroh to the host's gateway, so the Python MCP server and CLI talk to `http://127.0.0.1:<port>` as if SpacetimeDB were local. |
| `cadswarm/` (existing Python package) | Python | `mcp_server.py`, `prompt_hook.py`, `cli.py`, `client.py` switch from the FastAPI hub to SpacetimeDB's HTTP API. `hub/` is deleted. `worker/freecad_worker.py` is unchanged. |
| `cadswarm/reviewer.py` | Python | Droid reviewer using `droid-sdk`, started by the host. |
| `web/` | TypeScript, Vite, `spacetimedb` SDK, three.js | The phone app, built to static files and served from GitHub Pages. |
| `workbench/` | FreeCAD Python | P1: reads views over HTTP SQL instead of the hub. |

Why Rust for the host: SpacetimeDB has official live-subscription SDKs for Rust, TypeScript and C#
but not Python. The executor must react to queued ops immediately, so it uses the Rust SDK. Agents
work in request and response, so the Python MCP server is fine on the HTTP API.

## Positioning: a coordination harness, FreeCAD first

CadSwarm is the coordination layer that lets a swarm of agents and people build one CAD model
safely. FreeCAD is the first engine it drives, not the product. Everything novel (locks enforced by
rollback, fences, the op queue, tasks, live pointers, handoffs, the review loop, Elastic board
knowledge) lives in SpacetimeDB, the host and the clients, and none of it knows about FreeCAD. The
FreeCAD add-on is how beginners install it; people who don't know CAD never see FreeCAD at all.

### The CAD backend interface

FreeCAD sits behind one seam: the worker's JSON-lines protocol on stdin/stdout. Any CAD engine that
implements it can replace FreeCAD without changing the module, the host's coordination logic or
any client.

- **Start-up:** the backend prints `{"ready": true, "doc": "<path>"}` once it has loaded or created
  the document.
- **Request:** `{"id": n, "op": "<kind>", "args": {...}, "allowed": ["Name", ...] | null}`.
  `allowed` is the caller's lock set for mutating ops and `null` for queries.
- **Mutating reply:** `{"id", "ok": true, "result", "created": [...], "modified": [...],
  "deleted": [...], "recomputed": [...], "broken": [...]}`.
- **Error reply:** `{"id", "ok": false, "code", "error"}`.

**Guarantees a backend must provide:**
1. **Atomic ops with rollback.** If an op would modify or delete an object outside `allowed`,
   or leaves an object broken, nothing changes and the reply is `lock_violation` or
   `recompute_error`.
2. **Stable object names** that the coordination layer can lock.
3. **Dependency recompute,** reported in `recomputed`, so owners are told when someone else's
   change moved their part.
4. **A shared `Params` table** that geometry can bind to.
5. **Durability:** the document is saved before an op is acknowledged.
6. **The queries the product uses:** `status`, `get_object`, `interference`, `export`, `mesh`
   (binary STL previews), `printability`, `dependents`.

Op kinds a backend cannot support return `unknown_op`. Agents read `model_status` and adapt.

**Roadmap, not this weekend:**
- **build123d / CadQuery:** headless Python code-CAD, the easiest second backend.
- **Onshape:** professional teams, via its REST API.

A minimal build123d backend (`create_primitive` and `boolean` only) is an optional Saturday-night
proof if everything else is done.

## Deployment modes

| | Local (default) | Maincloud |
|---|---|---|
| Where the database runs | `spacetime start` on the host, supervised by `cadswarm-host` | A Maincloud database owned by the team's account |
| Account or key | None | The host owner runs `spacetime login` once (browser sign-in) |
| Cost | Free (BSL 1.1 allows one self-hosted production instance for your own app) | Free tier, then Pro at $25/month |
| If the host machine dies | The database is on that disk; work since the last copy is lost | Nothing is lost; the database lives on |
| Reach from anywhere | Through the host gateway (below) | Direct; every client connects to Maincloud |
| Stable address | No: the quick tunnel URL changes when the host restarts, so phones re-scan | Yes |

SpacetimeDB is source-available under BSL 1.1, not open source; it converts to AGPL v3 with a
linking exception four years after each release (for the current release, on 2031-10-14). Self-hosting for your own team is within the free
grant; offering SpacetimeDB itself as a service to others is not.

Moving a workspace from local to Maincloud is out of scope: a team starts a new Maincloud
workspace and carries geometry over as `.FCStd` or STEP.

### The host gateway (local mode, P1)

A bare SpacetimeDB node authenticates nobody for publishing, so it is never exposed directly. The
host binds it to `127.0.0.1` and runs a small reverse proxy in front of it that forwards only:

- the WebSocket connection for this database (`/v1/database/<db>/subscribe`, to verify),
- `POST /v1/database/<db>/call/<reducer>`,
- `POST /v1/database/<db>/sql`,
- `POST /v1/identity` and the routes under it (browsers call `POST /v1/identity/websocket-token`
  before opening the WebSocket).

Everything else (publish, delete, other databases, logs) returns 403. Requests are rate-limited per
source. Data stays protected by the member-only views and the reducers' membership checks either
way; the gateway only stops strangers from administering the node.

The gateway is exposed two ways:

- **iroh, for laptops and agents** (ALPN `cadswarm/gw/0`). Each inbound iroh stream is piped to the
  gateway. A peer whose endpoint id belongs to an active member gets the full allowlist; an unknown
  peer gets only the `/v1/identity` routes and the `redeem_invite` reducer, which is enough to pair. The
  invite ticket for a laptop is base32 of `{ mode, db, host_addr, token }`, where `host_addr` is the
  host's iroh `EndpointAddr`, minted after waiting up to 10 s for the relay address. `redeem_invite`
  takes the joiner's `endpoint_id` and records it on the member row. A member could register a
  different endpoint id than its own; since every member already has full control, that grants
  nothing new.
- **A Cloudflare quick tunnel, for phone browsers.** The host runs `cloudflared tunnel --url
  http://127.0.0.1:<gateway port>` (no Cloudflare account) and reads the `https://*.trycloudflare.com`
  URL it prints. That URL goes into the phone QR. Browsers need HTTPS here: the web app is served
  from GitHub Pages, and a page served over HTTPS cannot open a plain `ws://` connection to a LAN
  address.

In Maincloud mode neither is needed: the QR carries the Maincloud address and laptops connect
directly.

## Data model

Every table is private and carries an indexed `ws: u64` workspace id. Views can only read through
indexed lookups, so each view looks up the caller's active membership by identity and then filters
the table by that `ws`. A non-member's views return nothing. A hackathon deployment has one
workspace; the column also lets one database serve many teams later.

| Table | Fields (besides `ws`) |
|---|---|
| `workspace` | `id`, `name`, `owner`, `bed_x_mm`, `bed_y_mm`, `bed_z_mm` |
| `member` | `id`, `identity` (indexed), `name`, `kind: Human \| Agent \| Host \| Reviewer`, `client` (phone, claude, codex, droid, workbench), `role: Lead \| Member`, `status: Online \| Stale \| Left`, `fence: u32`, `last_active`, `endpoint_id: Option<String>` (P2), `taken_over_by: Option<u64>` |
| `invite` | `token_hash` (sha256 hex), `kind`, `role`, `created_by`, `expires_at`, `used_by: Option<Identity>` |
| `lock` | `object`, `holder` (member id), `acquired_at` |
| `object` | `name`, `label`, `type_id`, `owner`, `rev: u32`, `placement` (JSON), `bbox` (JSON), `depends_on: Vec<String>`, `last_actor`, `updated_at` |
| `param` | `name`, `value: f64`, `unit`, `note`, `updated_by`, `rev` |
| `op` | `id`, `by` (member), `fence`, `kind`, `args` (JSON), `expected_revs` (JSON), `status: Queued \| Running \| Done \| Rejected \| Failed`, `result` (JSON), `error`, `created_at`, `finished_at` |
| `task` | `id`, `title`, `detail`, `objects: Vec<String>`, `assignee: Option<String>` (member name, same reason), `status: Open \| Claimed \| Done \| Failed \| AwaitingTakeover`, `rev`, `created_by`, `summary`, `brief: Option<u64>` |
| `message` | `id`, `from`, `to: Option<String>` (member name, so it can address someone who has not joined yet; none = everyone), `kind: Message \| Steer \| Notice`, `body`, `task_id: Option<u64>` |
| `receipt` | `message_id`, `member`, `delivered_at`, `consumed_at` |
| `event` | `seq`, `kind`, `actor`, `data` (JSON), `at` |
| `activity` | `id`, `member`, `kind: ToolCall \| ToolResult \| Say \| Status`, `summary` (≤ 300 chars), `at` |
| `brief` | `id`, `by`, `text`, `board: Option<String>`, `status: New \| Planning \| Building \| Reviewing \| Ready \| Failed`, `plan` (JSON), `stl_artifact: Option<String>` |
| `preview` | `object`, `rev`, `mesh: Vec<u8>` (binary STL, decimated, target ≤ 200 KB) |
| `cursor` | `member` (one row per member, upserted), `object: Option<String>`, `point: Option<[f32; 3]>` (on the object's surface, model coordinates), `mode: Pointing \| Inspecting \| Editing \| Idle`, `label` (≤ 60 chars, e.g. "extruding lid"), `op: Option<u64>`, `seq: u32`, `at` |
| `handoff` | `id`, `from` (member), `note`, `trigger: now \| on_leave`, `status: waiting \| open \| accepted \| cancelled`, `taker` (member), `context_json` (snapshot of where the person left off), `created_ms`, `updated_ms` |
| `review` | `id`, `brief`, `round: u32`, `passed: bool`, `findings` (JSON), `at` |
| `artifact` | `hash`, `name`, `mime`, `size`, `bytes: Option<Vec<u8>>` (STL ≤ 5 MB inline), `holders: Vec<String>` (P2 iroh endpoint ids) |

`preview` and `artifact` are separate tables so subscriptions to the busy tables stay small.

## Rules (reducers)

Each rule below already exists in `hub/core.py` and `tests/test_core.py`; this is a port, not new
behaviour, except where marked new.

- **Joining.** `redeem_invite(token, name, client)` consumes a one-time invite and creates the
  member. Re-joining with a stored identity bumps `fence`; ops carrying an older fence are rejected,
  so a stale process with the same identity cannot act. Names are unique among live members.
- **Presence (changed).** WebSocket clients (phone, host) are online between `client_connected` and
  `client_disconnected`. HTTP-only clients (agents) call `heartbeat()` every 15 s while running. The
  scheduled `sweep` (every 10 s) marks members without activity for 30 s `Stale`, keeps their locks,
  moves their claimed tasks to `AwaitingTakeover` and notifies everyone. After 10 minutes stale the
  locks are freed and tasks reopened.
- **Takeover.** `take_over(target, force)` moves a stale member's locks and tasks to the caller;
  compare-and-set on stale status; humans may force.
- **Locks.** `lock(names)` and `release(names)`. You may only change objects you hold; creating an
  object needs its name locked first, and anything you create is yours. `Reviewer` members are
  refused by `lock` and `submit_op`.
- **Reviews (new).** `report_review(brief, passed, findings)` is callable only by a `Reviewer`; it
  records the round and, on a pass, tells the host to export.
- **Ops.** `submit_op(kind, args, expected_revs)` checks locks and expected revisions up front and
  queues the op. The host runs it in FreeCAD; the worker's allowed-objects check and transactional
  rollback stay as the second line of defence. `complete_op(id, result)` (host only) applies new
  revisions, object rows and recomputed dependents in one transaction, and messages the owners of
  parts that a `Params` change recomputed.
- **Params.** `set_param(name, value)` queues a recompute op; changes ripple to every dependent part.
- **Tasks and messages.** `create_task`, `claim_task(id, expected_rev)`, `report_task`, `send`,
  `ack`. Tasks and messages can address a name that has not joined yet. `Steer` messages from humans
  are always first in an inbox.
- **Briefs (new).** `submit_brief(text, board)` from the phone. The host sees it, seeds `Params` from
  the board library and starts the planner (below).
- **Activity (new).** `log_activity(kind, summary, target)` from the MCP server on every tool call and
  from the reviewer on every Droid event. When `target` (object names) is given, the same call moves
  the caller's `cursor` to that object, so agent pointers cost no extra calls. Rows older than the
  newest 200 per member are pruned by `sweep`.
- **Pointers (new).** `move_cursor(object, point, mode, label)` from human clients, throttled on the
  client. `submit_op` and `set_param` set the caller's cursor to `Editing` on the affected objects,
  and `lock` and `take_over` point it at the objects they move; `complete_op` returns it to
  `Pointing`. `client_disconnected` and `sweep` set departed members' cursors to
  `Idle`.

## The brief flow (the demo path)

1. A judge scans the QR. The page opens with the invite token in the URL fragment (never sent to a
   server), gets an anonymous SpacetimeDB identity, calls `redeem_invite`, and keeps the identity in
   local storage.
2. They type "case for an ESP32-S3 with a USB-C cutout and a snap-on lid" and pick the board.
3. `submit_brief` → the host seeds `Params` with the board's measured dimensions and starts a
   headless Claude Code **planner** with the cadswarm MCP server attached. The planner writes a plan
   (parts, parameters, who does what) into `brief.plan` and creates one task per part.
4. The host spawns up to 3 headless Claude Code **workers**, and any teammate's own agent can claim
   tasks too. Each worker locks its part's names, builds it bound to `Params` expressions, and
   reports.
5. The phone shows all of it live: the object tree filling in, each agent's pointer gliding to the
   part it is working on, locks tinting parts in their holder's colour, and each object's preview
   cross-fading in as its mesh lands.
6. When all tasks are done, the brief moves to `Reviewing` and the **reviewer** runs (P1; see the
   fix loop below). Once it passes (or straight away while P1 is not built), the host exports the
   STL into `artifact` and the brief becomes `Ready`. The phone offers the STL download.
7. The judge changes `wall_mm` from 2 to 3 on the phone: the change ripples, owners are notified,
   previews update.

The headless agents run on the host owner's Claude subscription, launched by the host with a
standing instruction: claim a task, build it, report, then call `wait_for_changes` until there is
nothing left. Each is killed after 15 minutes regardless.

## Live presence: pointers, edits and animation

Everyone working on the model, human or agent, is visible in the 3D view as it happens, like
multiplayer cursors in a design tool. The same web app runs on phones and on a laptop, which is the
big-screen view for the demo.

**Identity on screen.** Each member gets a stable colour (hashed from the member id) and a glyph: an
arrow for people, a small diamond for agents, a magnifier for the reviewer. The colour is used
everywhere that member appears: pointer, lock tint, object tree badge, activity feed.

**Agent pointers (P0).** Agents have no mouse, so their pointer follows what they do. The MCP server
passes the tool call's target objects to `log_activity`, which moves the agent's cursor to that
object's surface point nearest its bounding box centre, with the tool as the label ("extruding lid",
"checking interference"). The viewer animates the move.

**Human pointers (P1).** Tapping or hovering in the 3D view raycasts against the preview meshes and
sends `move_cursor` with the hit object and point. Selecting an object sets `Inspecting`. Changing a
parameter or using the lock and takeover buttons sets `Editing` on the affected objects. Clients send
at most 5 updates a second and only when the pointer actually moved, and stop after 3 seconds
without input.

**Manual edits are visible too.** Every change, whether an agent's op, a parameter change from a
phone, or a lock or takeover from the panel, goes through a reducer (`submit_op`, `set_param`,
`lock`, `take_over`) that also moves the actor's cursor onto the affected objects, so it shows the
same way regardless of who made it:

- an object being edited pulses in its editor's colour with their name tag;
- an object that is locked keeps a faint tint in the holder's colour;
- when an op completes, the new preview mesh cross-fades in and the changed object flashes once;
- a parameter change ripples: each recomputed dependent flashes in order along the dependency chain;
- a rejected op (lock or revision conflict) shakes the object briefly and shows the reason.

Direct geometry edits inside the FreeCAD GUI are not in scope (the mirror stays read-only); in P2
the workbench draws everyone's pointers inside FreeCAD as coloured markers.

**Animation in the viewer** (three.js):

- pointers never jump: each frame moves them along a critically damped spring towards the latest
  target, so an agent's pointer visibly glides from the base to the lid when it switches parts;
- name tags (CSS2D labels) follow their pointer and show the label text;
- a pointer in `Editing` mode draws a thin line from the tag to the object and pulses;
- an `Idle` pointer fades out over a second;
- updates arriving out of order are dropped using `seq`;
- respects `prefers-reduced-motion`: pointers snap and pulses become static outlines.

**Cost.** Agent pointers ride on calls already being made. Human pointers are the only new traffic:
at 5 updates a second while actively moving, one person moving for an hour is about 18,000 calls,
small against the free tier for a weekend. SpacetimeDB 2.x has event tables, but views cannot read
them and every client read here goes through a view, so cursors use the one-row-per-member upsert
above, which keeps storage flat.

## Handing off to an AI (P1)

A person can hand their work to an AI, which picks up exactly where they left off: their locked
parts, their in-progress tasks, and the context of what they were doing.

- **Two triggers, from the "Hand your work to an AI" card (phone) or the FreeCAD panel:**
  - **Now:** "I have to go to judging, finish my lid." The person stays a member; only their work
    moves.
  - **When I leave:** an armed handoff that opens automatically if the person goes stale (closed or
    offline for 30 s). They are then retired like any stale takeover.
- **The snapshot.** When a handoff opens, the module records in `context_json`: their note, what
  their pointer was on (object, point, label), the parts they hold, their claimed tasks with details,
  their last 15 activity lines, their last 10 ops (kind, arguments, outcome) and their last 5
  messages. A disconnect sets the pointer to `idle` but keeps its object and point.
- **Who picks it up.** The host spawns a headless Claude Code agent (`handoff-<id>`) with the snapshot
  in its prompt and the note as the main instruction. Its first call, `accept_handoff`, moves the
  person's locks and tasks to it in one transaction, starts its pointer on the person's part, and
  messages the person.
- **Rules.** Only humans request handoffs; only agents accept them; once only; the requester can
  cancel until accepted. Agents continue each other's work through the existing stale takeover.

## Setup for beginners: the FreeCAD guide (from commit `fddfa28`)

The guide inside FreeCAD stays the front door for people who never open a terminal. What changes is
what it starts:

- **Start a team:** pick a project folder (same layout: `model.FCStd`, `files/`, `exports/`,
  `snapshots/`, `AI helpers/`), then download once into the private app folder the `spacetime` CLI
  and a prebuilt `cadswarm-host` for this OS (GitHub release assets), start `spacetime start` and
  `cadswarm-host run` as background processes instead of the FastAPI hub, publish the prebuilt
  module (`.wasm` from the release, so no Rust toolchain is needed), and show an invite code.
  Maincloud mode adds one browser sign-in (`spacetime login`).
- **Invite codes** keep the guide's copy-paste style with a new version prefix: `cadswarm-2:` +
  base64url of the ticket JSON `{v, uri, db, token, kind, role, host_addr}`. `cadswarm-1:` codes
  (old hub) get a plain message asking for a new code.
- **Join:** paste the code; the guide redeems it and writes the agent's folder exactly as today.
- **File tools stay.** `list_files`, `read_file`, `save_file`, `make_folder`, `move_file`,
  `delete_file`, `file_history`, `restore_file` and `save_snapshot` become queued op kinds. The host
  runs them through a small Python helper process that wraps the existing `ProjectFiles` rules
  (sandboxed to `files/`, versioned history, no silent overwrites), so the guarantees are unchanged
  and agents on other machines can use them too.

## Elastic: board knowledge and design memory (P0)

**Purpose:** real dimensions for any board, and lessons from past designs, turned into actions.
Every Elastic call goes through the host, which holds the only Elastic key; agents on other
machines use queued op kinds (`lookup_board`, `recall_designs`) like FreeCAD queries.

- **Elastic Cloud Serverless**, Elasticsearch project. An inference endpoint for
  `jina-embeddings-v5-text-small` on the Elastic Inference Service (newer than v3; `jina-embeddings-v3`,
  in Preview on Serverless, is the fallback) and the preconfigured JINA reranker `.jina-reranker-v3.5`
  (setup checks the id against `GET _inference/_all`).
- **Indices:**
  - `boards`: chunks of datasheets, product pages and drawings (`board_id`, `title`, `source_url`,
    `source_type`, `content` as `semantic_text`). Text is extracted before indexing (`pypdf` for
    PDFs; no built-in PDF extraction is documented for Serverless). Sources are listed in
    `boards/sources.json`.
  - `designs`: one document per finished brief: request, board, plan, review findings, fixes,
    final params, outcome.
  - `lessons`: recurring problems per board, written by the Workflow below.
- **Search:** hybrid retrieval (lexical + semantic with `rrf`) re-ranked by `text_similarity_reranker`
  with the JINA reranker.
- **Agent Builder agent `cadswarm-board-expert`:** custom tools over the three indices; instructions
  to return board specs as strict JSON with a `source_url` for every number. The host calls it with
  `POST {KIBANA_URL}/api/agent_builder/converse`.
- **Workflow `distil-lessons`** (scheduled every 10 minutes): ES|QL groups review findings by board
  and problem and writes them into `lessons`.
- **In the product:**
  - A brief for a board missing from `boards.json` → the expert's spec seeds Params; each seeded
    value carries its source, shown in the app as "from datasheet". Measured boards always win.
  - Before planning → the planner prompt gets the top similar designs and the board's lessons.
  - A brief reaching `ready` → the host indexes it into `designs`.
  - The reviewer uses `lookup_board` to check cutouts against the datasheet.
- **Prize rules:** the 13.0 Elastic rules are not published; 12.0's required Agent Builder with MCP.
  Confirm at the booth. If MCP is required, agents also reach `cadswarm-board-expert` through Agent
  Builder's MCP endpoint (`/api/agent_builder/mcp`), alongside the host helper.
- **Risks:** datasheet values can be wrong (sources shown, reviewer cross-checks, calipers win); the
  free trial is 14 days with unverified limits.

## Droid reviewer (P1, Factory track)

The reviewer is not a chat agent. The app starts it, it can only inspect and report, and its work is
shown live.

- **Triggers** (the host watches SpacetimeDB):
  - **Full review:** every task of a brief is `Done`. The brief moves to `Reviewing`.
  - **Quick review:** a `Params` change recomputes a part owned by someone other than the member who
    changed it. Only the recomputed parts and their neighbours are checked.
- **Identity:** `cadswarm-host init` redeems a `Reviewer` invite once and keeps the reviewer's
  SpacetimeDB identity in its own folder (`<data_dir>/reviewer`). The MCP server Droid launches is
  pointed at that folder (`CADSWARM_HOME`), so every action is attributed to the reviewer member.
- **Run** (`cadswarm/reviewer.py`, started by the host as `python -m cadswarm.reviewer --brief <id>`
  or `--objects <names>`):

  ```python
  config = SessionConfig(
      autonomy=Autonomy.LOW,
      mcp_servers=[StdioMcpServerConfig(
          name="cadswarm", command=sys.executable, args=["-m", "cadswarm.mcp_server"],
          env={"CADSWARM_HOME": reviewer_dir})],
      restrict_tools=REVIEW_TOOLS,
  )
  async with Session(cwd=reviewer_dir, config=config) as session:
      async with session.stream(review_prompt(brief)) as stream:
          async for event in stream:
              log_activity(event)        # ToolCall / ToolResult / AssistantMessage -> `activity`
  ```

- **What it checks**, against facts rather than its own judgement:
  - parts colliding with each other (`check_interference`);
  - the board fitting: the enclosure's inner dimensions against the board library's measured size,
    standoffs against the board's hole spacing, and port cutouts (such as USB-C) against the
    board's measured port positions;
  - printability (`check_printability`, a new worker op): the bounding box against the workspace's
    bed size, a valid closed solid, and a best-effort thin-wall check below 1.2 mm (FreeCAD's
    distance between opposite faces, sampled per face; it reports suspects, it does not prove
    absence).
- **What it may touch, enforced twice:**
  1. `restrict_tools` allows only `model_status`, `get_object`, `check_interference`,
     `check_printability`, `create_task`, `send_message` and `report_review`. No shell, no file
     edits, no geometry tools.
  2. In the module, a `Reviewer` member cannot call `lock` or `submit_op`; those reducers reject the
     kind outright. So even a geometry tool call that got past the SDK would be refused.
- **Reporting and the fix loop:** for each problem it files a task assigned to the part's owner and
  sends that owner a message, e.g. "lid overlaps standoff_2 by 0.8 mm". When those tasks are `Done`,
  the host runs a full review again. It ends with `report_review(brief, passed, findings)`. A pass
  moves the brief to export and `Ready`; a fail keeps it in `Building`. After 3 failing rounds the
  brief goes to `Failed` with the findings, and the phone asks a human to steer.
- **Stored result:** a `review` table (`id`, `ws`, `brief`, `round`, `passed`, `findings` JSON,
  `at`), shown on the phone as a review card on the brief, next to the live activity feed.
- **Host requirements:** the `droid` CLI on PATH, Python 3.10+, and `FACTORY_API_KEY` in the host's
  keychain. Nobody else needs Factory.
- **Teammates who use Droid day to day** can join as a normal agent with
  `cadswarm setup --client droid`, which writes `.factory/mcp.json`. That is a bonus; the triggered
  reviewer is what meets the track.
- **README:** a "Where Droid runs" section: what starts it, what it checks, what it can and cannot
  touch, and where its work is shown (an eligibility rule).
- **Demo moment:** the swarm finishes the case, Droid catches the lid clipping a standoff and files a
  task, the owner agent fixes it, Droid re-checks and passes, the STL appears. All of it on the phone.
- **To verify while planning:** the exact ID format `restrict_tools` expects for a tool that comes
  from an MCP server named `cadswarm`.

## Keys and setup

- **Phone users and judges:** scan the QR. Nothing else.
- **Teammates with their own agent:** `cadswarm setup --client claude|codex|droid --invite <ticket>`
  in a folder, then run their agent as they already do. Their existing agent login is the only one.
- **The host owner, once:** `cadswarm-host init` asks "local or Maincloud?", detects existing
  `spacetime`, `claude` and `droid` logins, asks only for what is missing (`FACTORY_API_KEY`), stores them in the OS keychain, publishes the module (to the local node, or
  to Maincloud after `spacetime login`), and prints the invite QR. Local mode needs no SpacetimeDB
  account at all.
- No key is ever stored in the repo, the web app or a phone.

## Failure handling

| Situation | Behaviour |
|---|---|
| An agent edits an object it does not hold | `submit_op` rejects it; if it slips past, the worker rolls the transaction back. |
| An agent's expected revision is stale | Rejected with a conflict; the agent re-reads and retries. |
| FreeCAD op hangs or crashes | Worker timeout; the host restarts the worker from the last save and marks the op `Failed`. |
| An agent dies mid-task | Stale after 30 s; tasks wait for takeover; freed after 10 minutes. |
| The host loses Maincloud | The op in flight finishes locally; the host reconnects with backoff (TypeScript and Rust SDKs reconnect) and completes it; queued ops wait. |
| The phone drops | Its subscription catches up on reconnect. |
| A headless agent runs away | Killed after 15 minutes; its locks go stale and are reclaimable. |
| Free-tier database paused after a week with no activity | It resumes in under a second on the next connection; nothing to do for the demo. |
| Local mode: the host machine goes offline | Everyone loses access until it is back; nothing is lost while the disk survives. This is the stated reason to choose Maincloud. |
| Local mode: the host restarts | The quick tunnel URL changes; the host prints a new QR and phones re-scan (their stored identity is kept, so they stay members). iroh peers reconnect on their own, since the host's endpoint id is stable. |
| Local mode: an unknown iroh peer or a stranger on the tunnel URL | Gets only identity creation and `redeem_invite`; without a valid invite token it can do nothing, and the views return nothing. |

## Testing

- **Module rules:** the cases in `tests/test_core.py` (locks, fences, takeover, stale sweep, task
  claim, inbox ordering) ported to native Rust unit tests on the plain rule functions.
- **Executor:** the host runs ops against the real FreeCAD worker, as `tests/test_integration.py`
  does today.
- **End to end on a local `spacetime start`:** publish, start the host, redeem an invite from a
  script, submit a brief with a stub planner that creates two box parts, and check that the phone's
  views show both objects, previews and a `Ready` brief.
- **On site, before Saturday night:** a phone on cellular data and a laptop on venue wifi, against
  Maincloud, through the full brief flow. Personal hotspots are prohibited at the venue.

## Risks

1. **Headless Claude Code reliability.** Agents may stall or loop. Mitigations: the 15 minute kill,
   a small number of workers, and a plan template for enclosures that the planner fills in.
2. **Python on HTTP only.** `wait_for_changes` polls the `event` view (once a second, backing off
   to 3 s). Fine for a few agents; noted as a cost item for scale. A hand-rolled Python WebSocket
   client over the documented `v1.json.spacetimedb` protocol would remove the polling; not worth it
   before the deadline (the only PyPI SDK is a 2023 pre-1.0 client).
3. **Free-tier energy.** The 2,500 TeV credit is about 3 M calls, or 12.5 GB of egress, or 1 GB of
   storage, and the pricing page does not say what happens when it runs out. Heartbeats every 15 s
   for 5 agents are about 0.9 M calls a month. Egress is the bigger risk: every subscriber receives
   every changed row of the views it subscribes to, so preview meshes (up to 200 KB per object
   revision) go to every phone. Export bytes therefore live in their own table, `artifact_blob`,
   that nothing subscribes to; a download reads one row by hash. HTTP polling is capped at once a
   second (SQL is billed by bytes scanned and index seeks). Watch the Maincloud usage page during
   the event.
4. **The single FreeCAD worker** serialises ops. This is the stated scale limit; the answer for
   judges is per-part documents across hosts, coordinated through the same database, with files
   over the iroh mesh (P2).
5. **Event rule.** Code written before the event cannot be copied in; Pushin's iroh code is a
   reference for patterns only.
6. **Quick tunnels** are a free Cloudflare service with no uptime guarantee, meant for testing. Fine
   for a team's local mode; the stable answer is Maincloud mode. The exact WebSocket path the SDKs use
   must be confirmed before writing the gateway allowlist.
