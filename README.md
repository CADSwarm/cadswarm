# CadSwarm

**Build 3D designs together with your team's AI helpers — no CAD or coding experience needed.**

Each teammate's AI helper (Claude Code or Codex) builds part of one shared 3D model, live.
You watch the parts appear in FreeCAD and chat with the helpers to steer them.

## Get started (Windows, about 5 minutes)

1. **Install FreeCAD** (free) from [freecad.org/downloads](https://www.freecad.org/downloads.php).
   Default options are fine.
2. **Download CadSwarm**: on this page click the green **Code** button, then **Download ZIP**.
   Unzip it anywhere, for example your Downloads folder.
3. **Double-click `Install CadSwarm.cmd`** in the unzipped folder. It puts CadSwarm into FreeCAD,
   offers a desktop shortcut, and opens FreeCAD.
4. **Follow the guide that opens in FreeCAD.**
   - *First person on the team:* choose **Start a new team project**, name it, and copy the
     **invite code** to your teammates.
   - *Everyone else:* choose **Join a teammate's project** and paste the invite code.
   - Then pick your AI helper. The guide connects it, opens it for you, gives you a first
     message to paste, and tells you when it has joined the team.

That's it. Next time, open the **CadSwarm** shortcut (or pick *CadSwarm* in FreeCAD's workbench
list) and you're back with your team. If you hosted the team, its server starts again when you open CadSwarm (it doesn't start on its
own after a reboot until you do).

**Good to know**
- You need an AI helper installed: [Claude Code](https://docs.anthropic.com/en/docs/claude-code/setup)
  or [Codex](https://github.com/openai/codex). The guide detects which ones you have and links to
  install instructions.
- Teammates must be on the same Wi-Fi or network as the person who started the team, and that
  computer needs to stay on while you work. For remote teams, see *Hosting for remote teams* below.
- The first setup downloads about 70 MB of helper software into `%APPDATA%\CadSwarm`. Nothing is
  installed into FreeCAD itself.
- When you start a team, the guide asks **where to save the project** (default
  `Documents\CadSwarm\<project name>`). If you pick a folder that already has other things in it,
  the project gets its own subfolder there, so your files are never mixed in or overwritten.

## Your project folder

```
Phone stand/
  model.FCStd          the live shared model (only the team server writes it)
  files/               notes, plans, BOMs and scripts the AI helpers saved, in subfolders that
    parts/base/notes.md  grow as the project does
    docs/bom.csv
  exports/             3D files (STEP/STL), each with a new name, never overwritten or auto-deleted
  snapshots/           saved versions of the model ("2026-10-23 20.32 first draft.FCStd"), never auto-deleted
  AI helpers/          each helper's own workspace on this computer (holds its sign-in; not shared)
  .cadswarm-team/      the team server's data, plus older versions of files (the last 20 of each)
  README.txt           explains the above to anyone browsing the folder
```

In FreeCAD, **Team tools > Project files...** lists what the team saved (version, who, when) and
lets anyone save a copy. The host also gets **Open project folder**.

### Saving is safe by design

The team server enforces these rules; it doesn't just ask the AI helpers to follow them:

- **Sandboxed.** Helpers can only write inside `files/`. Paths with `..`, absolute or drive paths,
  hidden or Windows-reserved names, and symlinks or junctions that point outside are refused. File
  types are on an allow-list, with size limits per file: 5 MB for text (notes, scripts, CSV), 100 MB
  for binary CAD and media files (STEP, STL, 3MF, FCStd, DXF, PDF, images).
- **No silent overwrites.** Saving onto an existing file is refused unless the helper asks for
  `new_name` (saves as `notes-2.md`) or `replace` with the version it last read. If someone changed
  the file since then, including a person editing it by hand, the save is refused.
- **Old versions are kept, within limits.** Replaced, moved and deleted files go to history and can be
  restored: the last 20 versions of each file are kept (and everything from the last day). A deleted
  file always keeps its latest version. Older versions beyond that are removed for good. If your file is
  replaced by a teammate, you're told.
- **Atomic writes.** A crash never leaves half a file.

### Disk space and the project limit

Big projects are welcome, but the host's computer is protected and a project can't get stuck:

- **Disk guard.** Before anything that adds data (saving a file, a snapshot, an export), the team server
  checks free space on the drive. It refuses if less than 2 GB would be left (or 10% of the drive, if
  that is smaller) and says so in plain words (`disk_full`).
- **Project limit: 20 GB by default**, counting files, older versions, snapshots and exports together.
  The host can change it in **Team tools > Storage and free up space...** (0 means no project limit; the
  disk guard still applies), or with the environment variable `CADSWARM_PROJECT_LIMIT_GB` when starting the
  hub. History retention (20 versions, 24 hours) is changed in the same place.
- **Free up space.** The same dialog shows what is using the room and lets a person trim older versions,
  delete old snapshots (always keeping the newest few) and old exports. Snapshots and exports are never
  deleted automatically, and AI helpers can look (`storage_status`) but can't delete: they're told to ask
  a person.
- Binary files up to 100 MB are accepted over HTTP; a 100 MB save briefly needs about 500 MB of RAM on the
  host while it is decoded.
- **Model scripts can't write files.** `run_part_script` can't `open()` files, can't import
  `os`/`shutil`/`subprocess`, and can't call `doc.saveAs`, `shape.exportStep` and the like. This
  guards against accidents and naive misuse; in-process Python can't be fully sandboxed, so only
  team up with people you trust.

---

## How it works

All the AI helpers edit the *same* FreeCAD document through a small team server, and humans watch
and steer from the CadSwarm workbench. They don't overwrite each other because:

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

## For developers: command line

The guide above wraps these commands; they still work for scripting and remote hosting.

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

# 3. Install this checkout into FreeCAD as the CadSwarm add-on (same as the double-click installer)
.venv\Scripts\cadswarm install-workbench
```

For teammates on other machines, run the hub on a reachable host (LAN, or a tunnel such
as Tailscale or ngrok). Each teammate needs this package and FreeCAD only if they run the
workbench; agents need just Python.

## Hosting for remote teams

Invite codes made by the FreeCAD guide contain the host's local network address, so they work on
the same Wi-Fi. For teammates elsewhere, put everyone on a private network such as
[Tailscale](https://tailscale.com) (free for small teams), or run the hub on a reachable server.
Then make an invite code with an address they can reach:

```powershell
python cadswarm_launch.py hub --data .\team-model --token MY-TEAM-CODE        # on the host
python cadswarm_launch.py invite --hub http://100.64.1.5:8765 --token MY-TEAM-CODE --project "Phone stand"
```

Teammates paste that code into the guide's **Join a teammate's project** page as usual.

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
- **Checks and output:** `check_interference`, `export_model` (STEP/STL), `save_snapshot`, `storage_status`
- **Project files:** `list_files`, `read_file`, `save_file`, `make_folder`, `move_file`,
  `delete_file`, `file_history`, `restore_file`
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
.venv\Scripts\python -m pytest        # 169 tests; the integration test drives real FreeCAD
```

See [demo/README.md](demo/README.md) for a three-agent demo script.

## Known limits (hackathon MVP)

- The FreeCAD mirror is read-only. Human geometry edits go through the panel (lock, param,
  takeover) or an agent, not direct GUI modelling.
- `run_part_script` runs agent Python inside the worker. Its file-write guard (see "Model scripts can't
  write files") only stops accidents, not a determined script, so use it only with trusted teammates;
  a timeout kills the worker and the hub restarts it from the last save.
- Locks are per object. Editing a shared upstream object (e.g. a sketch used by two features)
  requires its lock; there is no sub-object locking.
- Single hub, no TLS, shared workspace token. Put it behind a VPN or tunnel for remote teams.
