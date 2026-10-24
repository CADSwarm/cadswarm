# CadSwarm — Devpost submission packet

Draft for Cal Hacks 13.0. Replace every `[TBD]` before submitting. Keep the description within
Devpost's field limits (the sections below are sized for the "built with a story" form).

## Before submitting

- [ ] Public code link: make the repository public, or add the Devpost judges as collaborators.
  The guide's `releases/latest/download` URLs and the release assets only work unauthenticated on a
  public repository.
- [ ] Web link: enable GitHub Pages with source "GitHub Actions" (private repos need a paid plan), or
  deploy `web/dist` to Vercel/Netlify and use that URL.
- [ ] Video: record the 2:45 demo using the beats below and upload it; paste the URL under "Try it
  out".
- [ ] Fill in the team list and the three `[TBD]` links.
- [ ] Verify the release: `v0.1.0` assets exist (five host platforms, `cadswarm_module.wasm`,
  `SHA256SUMS.txt`) and a fresh install downloads and checks them.

## Project name

CadSwarm

## Tagline

One live CAD model, built by your team's AI helpers — no CAD or coding experience needed.

## Inspiration

Every hardware team at a hackathon eventually needs the same thing: an enclosure, a mount, a
bracket. The board is chosen in an afternoon and the case is still a sketch at 3 a.m., because CAD
is a separate skill from the electronics. AI coding agents can already write the firmware; we
wanted them to build the physical part too, together, while a person watches and steers.

The hard part is not generating a shape. It is keeping several agents' hands off each other's work.
Two agents editing one FreeCAD document overwrite each other, a dead agent leaves half-built parts
locked, and a plausible-looking enclosure can still not fit the board or the printer. CadSwarm is
built around that coordination problem: the team's shared state is a database, and the rules that
protect it are enforced there rather than requested in a prompt.

## What it does

CadSwarm turns FreeCAD into a shared workspace for a small team of people and their AI helpers.

- **A beginner-friendly FreeCAD workbench.** A guide starts or joins a team with a single-use invite,
  detects the AI helper you already use (Claude Code, Codex, Factory Droid, or a CLI of your own),
  wires it to the team, and opens it with a first message. No CAD or coding experience required.
- **One live model, many builders.** A lead agent plans a brief into tasks; worker agents claim them
  and build parts concurrently in the same FreeCAD document. Humans watch the parts appear in their
  owner's color, chat to steer, and drag the camera — the agents see the same document through
  tools.
- **Coordination the database enforces.** Object locks, fenced leases, revisions, a shared `Params`
  sheet with FreeCAD expressions, transactional rollback inside FreeCAD, heartbeats, stale
  detection and takeover. An agent that touches a part it does not hold has its whole op aborted.
- **A Droid reviewer.** When the parts are built, a Factory Droid agent inspects the model with a
  restricted tool set: clashes, board fit (measured PCB, mounting holes, port cutouts) and
  printability. It files tasks with precise fixes, and cannot change geometry itself.
- **Hand off, not start over.** Someone leaving can hand their locks, tasks and a note to an AI; the
  new agent accepts the handoff and continues from the owner's recent ops.
- **Knowledge that accumulates.** On an Elastic project, agents look up board dimensions from
  ingested datasheets, recall similar finished designs, and read lessons distilled by a scheduled
  Workflow from previous reviewers' findings. An Agent Builder expert supplies a validated board
  spec when the local library does not know the board.
- **Files and output that are safe by default.** Sandboxed project files with history and no silent
  overwrites, snapshots, STEP/STL export, and a phone web app that shows the same live state.

## How we built it

**SpacetimeDB is the team.** A SpacetimeDB 2.13.0 module (Rust) holds every table and every rule:
members and fences, object locks, revisions, ops and receipts, briefs, tasks, messages, events,
reviews, handoffs, activity, artifacts. All tables are private; clients read through `v_*` views.
Reducers reject a stale fence, a write to an unlocked object, and a reviewer's write op. The module
also runs the stale sweep and stores exported bytes.

**A Rust host runs the physical side.** `cadswarm-host` subscribes to the module, drains queued ops
one at a time into one FreeCAD worker (FreeCAD's own Python), commits the result transactionally, and
orchestrates each brief: seed measured board parameters, start a planner, start up to three workers,
export, review, retry or fail. It spawns the agents with whatever CLI the host owner brought and
prints single-use invites.

**Python is the bridge and the guide.** The MCP server translates the existing agent tool shapes to
the SpacetimeDB HTTP API, so agents keep their tools. The FreeCAD workbench mirrors the database into
a real model, provides the team panel, and hosts the first-run guide. The host also runs a Python
helper for project files, storage and Elastic knowledge.

**A web app for the room and the phone.** Vite + TypeScript + three.js subscribes to the same views:
live panels for the brief, tree, tasks, activity, params and review, animated agent pointers, an STL
preview, pairing by invite, and handoff controls.

**Elastic for what the swarm knows.** An Elasticsearch Serverless project (9.6) holds board
datasheets, finished designs and distilled lessons. `semantic_text` plus the Jina embedding endpoint
and reranker give hybrid board search; Agent Builder exposes `board_docs`, `board_lessons` and
`design_memory` as tools; a scheduled Workflow counts reviewer problems with ES|QL and writes
per-board lessons. The host asks the Board expert only for boards the local library cannot vouch
for, parses its JSON, and validates the dimensions before trusting them.

**Factory Droid for review.** The reviewer uses `droid-sdk` with `restrict_tools` limited to
inspection and reporting, joins as a `reviewer` member, logs every tool call to `activity`, and
records a verdict the host uses to export, rebuild, or ask a person to steer.

**Packaging is part of the product.** The guide downloads `cadswarm-host`, the pinned SpacetimeDB
2.13.0 CLI, and the module on first use into `%APPDATA%\CadSwarm`, verifies SHA-256 against the
release's `SHA256SUMS.txt`, and refuses an asset with no published checksum. GitHub Actions builds
five host platforms plus the wasm and publishes the release when a `v*` tag is pushed.

## Challenges we ran into

- **Exactly-once work across three runtimes.** An op must not run twice because a cache still shows
  it queued, and a reducer error must not disappear. Every reducer call waits for the module's
  answer; the worker's commit is lock-checked and rolled back inside FreeCAD.
- **Liveness without a central scheduler.** Fenced leases and heartbeats let the host or a person
  take over a dead agent's locks and tasks without racing a live one.
- **FreeCAD as a database replica.** One worker process owns the only `.FCStd`. Making every op a
  transaction that saves before commit, and never leaves memory and disk disagreeing, took most of
  the integration debugging.
- **A reviewer that cannot make things worse.** We gave Droid an allowlist, but also made the module
  refuse write ops from its member kind, so a slipped tool still cannot change geometry. A reviewer
  that dies before reporting now fails the brief with a message instead of hanging it.
- **Bring your own AI.** CadSwarm ships no model. The host finds the CLI the owner already has,
  starts it headlessly with the right prompt and folder, and attributes its actions to its own team
  identity.
- **Making an installer out of a dev tree.** Pinning SpacetimeDB, extracting its archive, checksum
  verification, and first-use downloads that work on a machine with no Rust or Python installed.

## Accomplishments that we're proud of

- A phone brief becomes a printable STL built by multiple agents on one live FreeCAD model, on
  SpacetimeDB Maincloud, with the rules enforced server-side.
- A working beginner path: install FreeCAD, double-click one file, follow the guide, and join a team
  with an invite — including automatic first-use downloads with checksums.
- Elastic is live, not a diagram: 6 datasheet documents indexed with semantic + reranked search, an
  Agent Builder board expert with three tools, and a `distil-lessons` Workflow that ran end to end
  and wrote real lessons from a finished design.
- The Droid reviewer runs from the host, shows its work in the activity feed, and has a safe failure
  mode.
- Tests are part of the story: the Rust host suite, a Python suite covering the module adapter,
  guide, hosting, Elastic and the reviewer, and a web suite for the store, panels and viewer math.

## What we learned

- **Coordination beats generation.** The model can draw a plausible box; the value is in locks,
  revisions, shared parameters and a review loop that catches what a single agent cannot.
- **Put the rules where the state is.** Prompts are advisory; reducers are not. Moving the team's
  invariants into SpacetimeDB made every client — CLI, phone, reviewer — obey the same rules.
- **Review needs a different posture from building.** A restricted, inspect-only agent with its own
  identity is more useful and safer than asking a builder to grade its own work.
- **The last mile is product work.** Pinned versions, checksums, a guide, and a failure message in
  plain words are what turn a demo into something a stranger can run.

## What's next

- Hosted teams so nobody has to keep a laptop awake, and a print-queue handoff.
- More CAD operations and a richer review (tolerances, assembly order, fasteners).
- A larger measured board library, and reviewer lessons that feed back into the planner's prompts.
- Native packaging for macOS/Linux and a one-click installer for teammates.

## Built with

SpacetimeDB 2.13.0 (Rust module, Rust and TypeScript clients) · Rust · Python 3.11+ · FreeCAD 1.x ·
TypeScript · Vite · three.js · Elasticsearch Serverless 9.6 · Kibana · Elastic Agent Builder · ES|QL
· Jina embeddings and reranker · Factory Droid (`droid-sdk`) · Claude Code · OpenAI Codex · GitHub
Actions · Windows

## Try it out

- **Code:** [TBD — public GitHub repository URL]
- **Release / downloads:** [TBD — GitHub Releases `v0.1.0` URL]
- **Web app:** [TBD — Pages or Vercel URL] (append `?mock` to try it without a team)
- **Video:** [TBD — YouTube/Vimeo URL]
- **Run it:** install FreeCAD, download the repository ZIP, double-click `Install CadSwarm.cmd`,
  then follow the guide. The host needs an AI CLI (Claude Code, Codex, or Droid) that the user
  already has; CadSwarm ships no model and no keys.

## Team

[TBD — names, roles, links]

---

## Demo video script (target 2:45)

**0:00–0:15 — The problem.** A desk with an ESP32 and a tangle of wires. "Every hardware team needs
a case. CAD is the bottleneck, and one AI agent alone gets the details wrong." Show a finished
CadSwarm enclosure in FreeCAD.

**0:15–0:35 — Start a team.** Open the CadSwarm workbench, run the guide, name the project, show the
invite code and the detected AI helper. Say: "No CAD or coding experience: pick the helper you
already use."

**0:35–1:05 — Two agents build live.** FreeCAD with the team panel. Send a brief: "A pocket case for
a Seeed XIAO ESP32C3 with a lid and a USB cutout." Show the planner's tasks, then parts appearing in
owner colors as two agents build. Mention Params and board dimensions.

**1:05–1:30 — The database protects the work.** On the phone web app (or panel), steer: "make the
lid 20 mm tall". Then ask the other agent to change the base plate: it is refused, "locked by …",
and messages the owner. Change a shared `Params` value and show the dependent part recompute.

**1:30–1:55 — Review and handoff.** The last task completes; Droid's review appears: a clash and a
port cutout that is too low, with tasks for the owners. Fix them; the reviewer passes. Show "Hand off
now" as a person leaves, and the new agent accepting.

**1:55–2:15 — Knowledge.** In Kibana: the board datasheet chunks, a hybrid search for mounting
holes, the Board expert, and the `distil-lessons` Workflow's lessons. "Elastic remembers what the
team learned, so the next design starts smarter."

**2:15–2:35 — Output and installation.** Export STEP/STL and open the result. Show the guide's
first-use download with checksum verification, and the GitHub Actions release page. "One file into
FreeCAD; the pinned database and team server arrive on first use."

**2:35–2:45 — Close.** "CadSwarm: your team's AI helpers, one live model, a printable part." Show the
repo URL and the mock web link.
