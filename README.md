# CadSwarm

**Your team's agents (Claude Code, Codex) building one live FreeCAD model together.**

Each person runs their own agent CLI on their own machine. All of them edit the *same*
FreeCAD document through a shared hub, and humans watch and steer from a FreeCAD workbench.
They don't overwrite each other because:

- **Object locks.** You can only change objects you've locked, and anything you create is yours.
- **Transactional rollback.** An op (including an arbitrary agent Python script) that touches
  an object you don't hold is aborted inside FreeCAD, and nothing is saved.
- **A shared `Params` sheet.** Agents bind geometry to shared dimensions with FreeCAD expressions.
  Changing one recomputes every dependent part and tells its owner.
- **Heartbeats, stale detection and takeover.** If a teammate's agent dies, its half-built parts
  stay protected until someone takes over its locks and tasks.

The coordination design is adapted from Amoeba's multi-agent runtime (fenced leases,
heartbeat-held locks, takeover by compare-and-set, delivered/consumed message receipts,
and human steering ahead of peer requests), applied to CAD objects instead of git files.

```
 alice: Claude Code ─┐  MCP (stdio)          ┌────────────── hub (FastAPI + SQLite) ──────────────┐
 bob:   Codex  ──────┼──► cadswarm MCP ──HTTP──► participants · fences · locks · revs · tasks ·   │
 dana:  Claude Code ─┘   + prompt hook        │  messages · events · stale sweep · takeover        │
                                              │          │ JSON lines, one op at a time           │
 carol: FreeCAD GUI ──── CadSwarm workbench ──►  FreeCAD worker (FreeCAD's python): THE live       │
        (mirror + team panel)                 │  .FCStd · transactions · lock-checked rollback     │
                                              └────────────────────────────────────────────────────┘
```

## Quick start

Prereqs: FreeCAD 1.x (found automatically, or set `CADSWARM_FREECAD_PYTHON` to its
`bin/python.exe`) and Python 3.11+.

```powershell
cd cadswarm
python -m venv .venv; .venv\Scripts\pip install -e ".[test]"

# 1. One person runs the hub (prints the URL + workspace token to share)
.venv\Scripts\cadswarm hub --data .\team-model

# 2. Each person wires their own agent in their own folder
.venv\Scripts\cadswarm setup --client claude --hub http://HOST:8765 --token TOKEN --name alice-claude --role lead --dir ..\alice
.venv\Scripts\cadswarm setup --client codex  --hub http://HOST:8765 --token TOKEN --name bob-codex --dir ..\bob
#    then run `claude` / `codex` in that folder. (Codex: trust the project so .codex/config.toml loads.)

# 3. Humans: install the workbench, restart FreeCAD, pick "CadSwarm", then CadSwarm > Connect...
.venv\Scripts\cadswarm install-workbench
```

For teammates on other machines, run the hub on a reachable host (LAN, or a tunnel such
as Tailscale or ngrok). Each teammate needs this package and FreeCAD only if they run the
workbench; agents need just Python.

## What `cadswarm setup` writes

| Client | Files |
|---|---|
| Claude Code | `.mcp.json` (cadswarm server), `.claude/settings.json` (UserPromptSubmit hook, `enabledMcpjsonServers`, `mcp__cadswarm` allowed), `CLAUDE.md` section |
| Codex | `.codex/config.toml` (`[mcp_servers.cadswarm]` + `[hooks] UserPromptSubmit`, in a managed block), `AGENTS.md` section |
| both | `.cadswarm/config.json` (hub, token, name, role), and after first run `.cadswarm/identity.json` (bearer token, git-ignored) |

The prompt hook injects a team block before every prompt: who holds which parts, open tasks,
what changed since your last prompt, and unread messages. It fails open: if the hub is down,
the prompt goes through without it.

## Agent tools (MCP)

- **Awareness:** `model_status`, `get_object`, `wait_for_changes`
- **Locks:** `lock_objects`, `release_objects`
- **Modelling:** `create_primitive`, `create_extrusion`, `boolean`, `set_props`, `set_placement`,
  `delete_object`, `set_param`, `run_part_script`
- **Checks and output:** `check_interference`, `export_model` (STEP/STL)
- **Teamwork:** `list_tasks`, `create_task`, `claim_task`, `report_result`, `send_message`,
  `get_inbox`, `ack_messages`, `take_over`

## Rules the hub enforces

1. An op may only change objects you hold. Pre-reserve a name with `lock_objects` before creating it.
2. Ops can carry `expected_rev`. If someone changed the object since you read it, you get a 409.
3. A `Params` change that recomputes someone else's part bumps its revision and messages its owner.
4. No heartbeat for 30s → **stale**. Its locks are kept, its tasks become `awaiting_takeover`, and
   everyone gets a handoff notice. `take_over` moves its locks and tasks to the caller. It is a
   compare-and-set, so it only works on stale participants (a human may force it). After 10 minutes
   stale, the locks are freed and the tasks reopened.
5. Re-joining with your stored identity resumes you at a new **fence**, so an older process using
   that identity can't act any more. A participant whose work was taken over has to join fresh.
6. Names are unique among live participants. A fresh join under a stale participant's name
   (someone who lost their identity file) inherits that participant's own work.
7. Tasks and messages can be addressed to a teammate who hasn't joined yet. They're delivered when
   that name joins.
8. Human messages are `steer` messages and always come first in every inbox. Peer messages are
   requests, never permissions.

## Tests

```powershell
.venv\Scripts\python -m pytest        # 19 tests; the integration test drives real FreeCAD
```

See [demo/README.md](demo/README.md) for a three-agent demo script.

## Known limits (hackathon MVP)

- The FreeCAD mirror is read-only. Human geometry edits go through the panel (lock, param,
  takeover) or an agent, not direct GUI modelling.
- `run_part_script` runs agent Python in the worker with no sandbox. Use it only with trusted
  teammates; a timeout kills the worker and the hub restarts it from the last save.
- Locks are per object. Editing a shared upstream object (e.g. a sketch used by two features)
  requires its lock; there is no sub-object locking.
- Single hub, no TLS, shared workspace token. Put it behind a VPN or tunnel for remote teams.
