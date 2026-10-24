# CADSwarm

**Build 3D designs together with your team's AI helpers — no CAD or coding experience needed.**

Each teammate's AI helper (Claude Code, Codex, Droid, or the CLI you already use) builds part of one
shared 3D model, live.
You watch the parts appear in FreeCAD, give the helpers tasks and instructions, and change parts
yourself when that's quicker.

## Get started (about 5 minutes)

**Quick install:** paste one line into a terminal. It downloads CadSwarm, finds FreeCAD (and offers to
install the official FreeCAD if you don't have it), puts CadSwarm into FreeCAD, offers a shortcut, and opens
FreeCAD.

| | Paste this |
|---|---|
| **Windows** (PowerShell) | `irm https://raw.githubusercontent.com/SachchitBalamurugan/cadswarm/main/install/quick-install.ps1 \| iex` |
| **macOS** (Terminal) | `curl -fsSL https://raw.githubusercontent.com/SachchitBalamurugan/cadswarm/main/install/quick-install.sh \| bash` |
| **Linux** (terminal) | `curl -fsSL https://raw.githubusercontent.com/SachchitBalamurugan/cadswarm/main/install/quick-install.sh \| bash` |

Or install by hand:

1. **Install FreeCAD** (free) from [freecad.org/downloads](https://www.freecad.org/downloads.php).
   Default options are fine. On Linux, use the official AppImage: the snap, Flatpak and distro packages
   have no Python of their own, so with them you can join a team but not start one (the installer offers to
   fetch the official FreeCAD for you).
2. **Download CadSwarm**: on this page click the green **Code** button, then **Download ZIP**.
   Unzip it anywhere, for example your Downloads folder.
3. **Run the installer** in the unzipped folder:
   - Windows: double-click **`Install CadSwarm.cmd`**.
   - macOS: double-click **`Install CadSwarm.command`** (if macOS says it can't be opened, right-click it,
     choose **Open**, then **Open** again).
   - Linux: run `bash "Install CadSwarm.sh"` (or right-click it and choose **Run as a Program**).
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
- You need an AI helper installed: [Claude Code](https://code.claude.com/docs/en/setup),
  [Codex](https://learn.chatgpt.com/docs/codex/cli), or
  [Factory Droid](https://docs.factory.ai/cli/getting-started/quickstart). The guide detects which ones
  you have and links to install instructions.
- The first time you open an AI helper in its folder it may ask whether you trust the folder, and
  may ask to approve the "cadswarm" tools. Say yes to both.
- **Whoever starts a team** makes one extra download, about 50 MB: CadSwarm's team server plus the
  [SpacetimeDB](https://spacetimedb.com/) database program (pinned to 2.13.0). The guide fetches them
  into CadSwarm's app folder (`%APPDATA%\CadSwarm` on Windows, `~/Library/Application Support/CadSwarm`
  on macOS, `~/.local/share/CadSwarm` on Linux) the first time you start a team, checks the SHA-256 of each file, and
  needs nothing installed by hand. Teammates who join don't need them. (If your computer already has
  both, the guide uses those instead.)
- The team's database runs on SpacetimeDB's cloud (Maincloud) by default, so teammates can join from
  anywhere. The advanced "keep it on this computer" option is for offline or LAN-only demos; then
  everyone must share a network and Windows may ask to allow access through its firewall.
- Keep the hosting computer on while your team works. The host program keeps running even if you close
  FreeCAD.
- The first setup downloads about 70 MB of helper software into CadSwarm's app folder, plus the team
  server download above if you host. Nothing is installed into FreeCAD itself. On Linux, an official FreeCAD
  the installer fetched for you is unpacked there too (`FreeCAD-<version>`, about 3 GB).
- Installer options (macOS/Linux, after `| bash -s --` or on `install/install.sh`): `--yes` (no questions;
  never downloads FreeCAD), `--get-freecad`, `--no-shortcut`, `--no-launch`. `CADSWARM_FREECAD` picks a
  specific FreeCAD; `CADSWARM_REF` makes the quick install use another branch or tag.
- When you start a team, the guide asks **where to save the project** (default
  `Documents\CadSwarm\<project name>`). If you pick a folder that already has other things in it,
  the project gets its own subfolder there, so your files are never mixed in or overwritten.

## Working with your team in FreeCAD

The **CadSwarm Team** panel on the right (wide, with large text and buttons) has four tabs, so talking and
giving orders never get mixed up:

- **Who's here** (top) shows every person and AI helper. A helper that is busy shows a turning
  **◐ Working on "…"**; one with nothing to do shows **Ready for instructions**.
- **Tasks**: click **＋ New task…**, say what kind of task it is (build a part, change one, check strength,
  fix clashes), describe it, and pick who does it: a helper, "whichever helper is free first", or yourself.
  Leave **Start right away** ticked and the helper is told to begin. For a task that's already on the
  list, use **Ask to start**, **I'll do it** or **Mark done**.
- **Tell a helper**: instructions for one AI helper (or all of them). A helper does these before anything
  else. Each one shows **⏳ not read yet**, **👀 read — working on it** or **✓ done**. The quick buttons fill in
  common ones (check for clashes, run a strength check, give me a status, pause, let go of a part).
  Helpers read new instructions when they finish a step, and between tasks they wait for new ones, so you
  don't have to go back to their window. Pick **🧠 The whole team — plan it and build it** instead of a
  helper to hand over a whole request ("a stand for a 7 mm thick phone"): the team server plans it into
  tasks and starts AI helpers itself (using the agent CLI on the hosting computer), and the list shows its
  progress (*planning*, *building (1/3 parts done)*, *done*).
- **Helpers start themselves.** When the guide connects an AI helper and you click **Open**, it starts
  with its first instructions already sent; nothing to paste. Opening it again later sends a short
  "carry on" instead.
- **Team chat**: people talking to each other. Helpers can read it, and their replies show up here marked
  *(AI helper)*, but they never take chat as an order.
- **Activity**: what happened, written by CadSwarm (parts added, tasks finished, files saved, reports).

**Changing a part yourself.** Select the part in the 3D view and click **✎ Edit a part…**. While the
editor is open, the part is held by you, so no helper can change it at the same time. Type a new size
or position, or move it with FreeCAD's own **Edit ▸ Placement** / **Transform** and the editor picks up the
move. **Save to team** sends the change to everyone; **Done** hands the part back. A part held by a helper
offers **Ask … to let go** (or, if it has gone quiet, **Free it anyway**). If you move a part in the 3D view
without opening the editor, CadSwarm puts it back and explains, so your view never silently differs
from your team's.

## Your project folder

```
Phone stand/
  model.FCStd          the live shared model (only the team server writes it)
  files/               notes, plans, BOMs and scripts the AI helpers saved, in subfolders that
    parts/base/notes.md  grow as the project does
    docs/bom.csv
    fea/2026-10-23 21.05 shelf bracket/   simulation reports: report.html, report.json, plot.png
  exports/             3D files (STEP/STL), each with a new name, never overwritten or auto-deleted
  snapshots/           saved versions of the model ("2026-10-23 20.32 first draft.FCStd"), never auto-deleted
  library/             the team's components library: one folder per part (part.step + meta.json)
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
- **Model scripts run in a sandbox.** When a helper runs a `run_part_script`, CadSwarm starts a separate,
  throw-away FreeCAD process for it, never the live model's own process. The script works on a read-only
  copy of the model and can hand back only a shape and some data. Anything else it does to the copy is
  discarded, so it can't change other people's parts. How tightly that process is locked down depends on
  your computer (the model server shows the active level at `/health`, under `script_sandbox`):
  - **Windows 10/11, normal case (`appcontainer`):** the script process runs in a Windows AppContainer, the
    same mechanism browsers use for web pages. It has no network, can't read or write your documents or
    profile, can't start other programs, and can only write inside a scratch folder that is deleted
    afterwards. It is also capped at about 2 GB of memory and 60 seconds. The first start gives that
    container read access to your FreeCAD folder, which can take a few seconds.
  - **Windows fallback (`lowintegrity`):** if AppContainers can't be created, scripts run with a
    low-privilege copy of your account. They still can't write anywhere but the scratch folder or start
    programs, but they **can** read your files and use the network. Use this only with people you trust.
  - **macOS / Linux (`process-limits`):** scripts run in their own process with memory, time and
    open-file limits, but with **no** file or network isolation, so they run as you. Use this only with
    people you trust.
  - **`CADSWARM_SCRIPT_SANDBOX=off`** switches all of this off for debugging and prints a loud warning.
  Settings: `CADSWARM_SCRIPT_TIMEOUT` (seconds) and `CADSWARM_SCRIPT_MEMORY_MB`. A script that runs
  too long or uses too much memory is stopped and reports an error; the model server keeps going.

### Disk space and the project limit

Big projects are welcome, but the host's computer is protected and a project can't get stuck:

- **Disk guard.** Before anything that adds data (saving a file, a snapshot, an export), the team server
  checks free space on the drive. It refuses if less than 2 GB would be left (or 10% of the drive, if
  that is smaller) and says so in plain words (`disk_full`).
- **Project limit: 20 GB by default**, counting files, older versions, snapshots and exports together.
  The host can change it in **Team tools > Storage and free up space...** (0 means no project limit; the
  disk guard still applies), or with the environment variable `CADSWARM_PROJECT_LIMIT_GB` when starting the
  team's server. History retention (20 versions, 24 hours) is changed in the same place.
- **Free up space.** The same dialog shows what is using the room and lets a person trim older versions,
  delete old snapshots (always keeping the newest few) and old exports. Snapshots and exports are never
  deleted automatically, and AI helpers can look (`storage_status`) but can't delete: they're told to ask
  a person.
- Binary files up to 100 MB are accepted over HTTP; a 100 MB save briefly needs about 500 MB of RAM on the
  host while it is decoded.

### Strength checks (FEA)

Ask your AI helper things like *"will this bracket hold 10 kg?"*, *"what's the natural frequency of the
arm?"* or *"how hot does the motor mount get?"*. It recognises a simulation request and asks you, in one
message, which analysis to run (it recommends **linear static**: stress, deflection and safety factor;
**modal** and **steady-state thermal** are also available), the material, which faces are held still and
the loads. If you don't know, it uses clearly marked placeholders (generic S235 steel, 1000 N on the top
face, bottom face fixed...) and the report says so in a yellow box.

The analysis runs FreeCAD's own FEM tools (Gmsh mesher + CalculiX solver, both in the standard FreeCAD
download) on a **copy** of the part. If this FreeCAD has no Gmsh (some Linux packages and snaps), the team
server installs it once by itself: the official Gmsh 4.15.2 build from PyPI, checked against a fixed SHA-256,
unpacked into `%APPDATA%\CadSwarm\tools` (`~/.local/share/CadSwarm/tools` elsewhere), never into FreeCAD or the
system. Set `CADSWARM_NO_AUTO_INSTALL=1` to turn that off. The solve runs in a separate process: the shared model, its locks and everyone else's
work are untouched, and one long solve never blocks the team. When it finishes, the helper adds a plain-words
summary and its own points of improvement to the automatic checks (safety factor, stress peaks at supports or
sharp corners, coarse mesh, no convergence check, resonance, temperature limits, placeholders...) and
publishes the report. It **pops up in every teammate's FreeCAD**, can be exported as a self-contained
**HTML** page or as **JSON** data, and is kept in `files/fea/`. **Team tools > Simulation (FEA) reports...**
lists past reports. If a helper forgets to publish, the hub publishes the automatic checks after 5 minutes.

FEA here is a design aid for linear-elastic behaviour with idealised supports, not a certification.

### Components library and precision settings

The **Library** tab (Team panel in FreeCAD, and the web app) is the team's parts shelf, like Onshape's:

- **Your team's parts.** **Upload a part…** adds a STEP, FreeCAD or STL file; **Add from a link…** adds one
  from a direct download link (a vendor's CAD file, a model on GitHub). FreeCAD measures each part (size,
  volume, holes) and makes a preview. Parts live with the project on the hosting computer, in `library/`.
- **Standard parts**, built to their real dimensions: socket and button head screws (ISO 4762 / 7380), hex
  nuts, washers, heat-set inserts, ball bearings (608 and friends), dowel pins and disc magnets.
- **Place in model** puts the part in the shared model (you hold it) and offers to position it.
  **Which part should I use?** describes a need ("an 8 mm axle that spins") and suggests parts, each with the
  hole the mating part needs.
- **More ▾ › Export the library as a pack…** saves the team's parts (and settings) as a `.zip` in the project
  files; **Import a pack…** brings them into another project. A pack's settings arrive as suggestions only.

AI helpers use the same library: they check it before modelling a screw or a bearing
(`recommend_components`, `search_components`), place parts (`add_standard_part`, `insert_component`), size
mating holes with the team's fits (`hole_size`), and can add a part they found online
(`import_component_from_url`, with its license).

**Precision settings** (Team tools, or the button in *Change the model yourself*) hold the team's
clearances and fits (general clearance, slip fit, press fit), bolt-hole class (ISO 273 close / normal /
loose), how screws hold in printed parts (heat-set inserts, tapped, self-tapping), printing values (XY
compensation, thinnest wall, layer height), mesh precision for STL exports and previews, the smallest clash
the clash check reports, and the part editor's decimals and arrow step. Fits are also shared sizes
(`Params.tol_clearance`, `Params.tol_press_fit`...), so geometry bound to them updates when they change.

Only people change settings. When a part implies a different value (a press-fit bearing seat, holes so small
a 0.2 mm mesh would facet them), an AI helper **suggests** it with a reason; the suggestion waits at the top
of Settings for someone to **Accept** or **Dismiss**.

### Learned design style

CadSwarm learns how your team designs and teaches it to every AI helper. The host notes each change to the
shared model: who made it, whether a person or a helper, and what changed. It also notes every finished
brief. From that it works out rules:

- **Shared sizes the team settles on.** For example, "Params.wall is 2.4 mm".
- **What people keep correcting** after a helper. This is the strongest signal, so it counts double.
- **Preferred parts.** For example, "M3 heat-set inserts".
- **Modelling habits.** For example, binding sizes to Params.
- **Problems reviews keep finding** for a board.

A rule backed by enough consistent, recent evidence from more than one person or day is **active**. Planners,
workers, handed-off helpers and the reviewer get it in their prompts, and every helper turn shows it as
"Team design style". Weaker rules, and rules a helper proposes (`teach_style`), wait under **Team tools ›
Learned design style…** for a person to **Confirm** or **Dismiss**.

- **Confirmed rules** are always followed and never fade.
- **Dismissed rules** stay away until their evidence doubles.
- **Unused learned rules** fade after about a month.

You can also type a rule yourself. It is confirmed at once. The reviewer flags parts that break a rule as
`style:` findings, and those findings feed the next round of learning.

Rules live in the project database on the hosting computer, so learning works with or without Elastic.
`python -m cadswarm.style_memory show --project <folder>` lists them. With Elastic configured, the rules
are also mirrored and an LLM rewords them: see [ELASTIC.md](ELASTIC.md).

---

## How it works

All the AI helpers edit the *same* FreeCAD document through the team's SpacetimeDB database, and humans
watch and steer from the CadSwarm workbench. They don't overwrite each other because:

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
 alice: Claude Code ─┐  MCP (stdio)          ┌──── SpacetimeDB: the team's database ────┐
 bob:   Codex  ──────┼──► cadswarm MCP ──────► members · fences · locks · revisions ·    │
 dana:  Droid  ──────┘   + prompt hook       │ tasks · messages · events · stale sweep   │
                                             └─────────────────▲─────────────────────────┘
 carol: FreeCAD GUI ─── CadSwarm workbench ────────────────────┤ ops, one at a time
        (mirror + team panel)                                 │
                                         cadswarm-host (Rust) on the host's computer
                                                               │ JSON lines
                                                               ▼
                                         FreeCAD worker (FreeCAD's python): THE live .FCStd,
                                         transactions · lock-checked rollback
```

## Where Droid runs

CadSwarm uses the Factory Droid SDK (`cadswarm/reviewer.py`) as the swarm's design reviewer.

- **What starts it:** `cadswarm-host`, when every task of a brief is done (SpacetimeDB subscription), and again after the owners fix what it found. Nobody prompts it.
- **What it checks:** collisions between parts, that the board actually fits (measured board dimensions, mounting holes, port cutouts), and printability (print bed size, closed solids, walls under 1.2 mm).
- **What it can touch:** only inspection and reporting tools (`restrict_tools`). It joins as a `reviewer` member, and the SpacetimeDB module refuses every write op from that kind (`submit_op`), so it cannot change geometry even if a tool slipped through.
- **Where you see its work:** every tool call and finding is written to the `activity` table and shown live in the app, and its verdict is stored in `review` and shown as a review card on the brief.

Setup, once per team (needs `pip install droid-sdk` or `pip install -e ".[reviewer]"`, the `droid` CLI, and `FACTORY_API_KEY`):

```powershell
host\target\release\cadswarm-host --data .\team-model init-reviewer
```

That mints a reviewer invite and writes the reviewer's folder under `team-model\agents\reviewer`. Until that folder exists briefs skip review and export directly, so a team without Droid still works. If the reviewer exits without a verdict (for example Droid is not logged in) the brief fails with that message instead of waiting forever, and its log is `team-model\agents\reviewer\review-<brief>-<round>.log`.

People can also hand work to an agent mid-design: while holding a part, "Hand off now" with a note spawns one agent per open handoff. It calls `accept_handoff` first (which transfers the person's locks and tasks), reads their note and recent ops, and continues instead of starting over.

## For developers: command line

The guide wraps these commands; they still work for scripting and CI.

Prereqs: FreeCAD 1.x (found automatically, or set `CADSWARM_FREECAD_PYTHON` to its
`bin/python.exe`) and Python 3.11+. When you host, the guide downloads the
[SpacetimeDB CLI](https://spacetimedb.com/install) 2.13.0, `cadswarm-host` and the module into
`%APPDATA%\CadSwarm` on first use; from a checkout you can also build them yourself with
`cargo build --release` in `host/` and `cargo build --release --target wasm32-unknown-unknown` in
`module/`.

Optional services read keys from the environment, never from the repo. Copy `.env.example` to
`cadswarm.env` next to the checkout (for example `Projects\cadswarm.env`, shared by every worktree) or
to a git-ignored `.env` inside one checkout, and run commands through the loader, which sets the
variables for that process only:

```powershell
powershell -File scripts\with-env.ps1 python -m pytest -q
powershell -File scripts\with-env.ps1 host\target\release\cadswarm-host.exe --data .\team run
```

`-EnvFile <path>` or `CADSWARM_ENV_FILE` picks a specific file. `.env.example` lists every variable:
Elastic (`ELASTIC_URL`, `KIBANA_URL`, `ELASTIC_API_KEY` for board knowledge and design memory),
Factory's reviewer (`FACTORY_API_KEY`), the optional Cartesia voice key, agent API-key fallbacks, and
developer overrides. SpacetimeDB publishing uses the CLI's stored login (`spacetime login`), not an
environment variable.

```powershell
cd cadswarm
python -m venv .venv; .venv\Scripts\pip install -e ".[test]"

# 1. Host: publish the module, then write this computer's team settings and run the host.
#    The guide does all three for you; from a terminal, publish with:
#    spacetime publish --server maincloud --yes=all --delete-data=never --bin-path
#      module\target\wasm32-unknown-unknown\release\cadswarm_module.wasm  cadswarm-my-team-1a2b
host\target\release\cadswarm-host --data .\team-model init --mode maincloud --db cadswarm-my-team-1a2b --python "path\to\FreeCAD\bin\python.exe"
host\target\release\cadswarm-host --data .\team-model run

# 2. Host: mint a single-use invite for each person or agent (also prints a phone link and QR code).
host\target\release\cadswarm-host --data .\team-model invite --kind human --role lead

# 3. Each person: join with the invite and wire their own AI helper in their own folder.
.venv\Scripts\cadswarm setup --client claude --invite "cadswarm-2:..." --name alice --dir ..\alice
.venv\Scripts\cadswarm setup --client codex  --invite "cadswarm-2:..." --name bob   --dir ..\bob
#    then run `claude` / `codex` / `droid` in that folder. (Codex: trust the project so
#    .codex/config.toml loads, then type `/hooks` in Codex and trust the CadSwarm hook.)

# 4. Install this checkout into FreeCAD as the CadSwarm add-on (same as the double-click installer).
.venv\Scripts\cadswarm install-workbench
```

Republishing an existing team's database after `module/` or `rules/` changes (owner login, keeping
the data, bindings, restarting the host): see [docs/publishing-spacetimedb.md](docs/publishing-spacetimedb.md).

### Packaging a release

The guide's first-use download needs three assets per platform. `scripts/package-release.ps1` builds
and checksums them into `dist\` (add `rustup target add wasm32-unknown-unknown` once):

```powershell
pwsh scripts\package-release.ps1                            # this computer
pwsh scripts\package-release.ps1 -Target aarch64-apple-darwin
pwsh scripts\package-release.ps1 -ModuleOnly                 # cadswarm_module.wasm only
```

Push a `v*` tag (`git tag v0.1.0; git push origin v0.1.0`) and `.github/workflows/release.yml`
builds all five host platforms plus the module on GitHub, writes `SHA256SUMS.txt`, and publishes the
release the guide resolves as `releases/latest/download`. To test the download path without a
release, point `CADSWARM_RELEASE_BASE` at a local folder (`file:///C:/path/to/dist`) holding the same
files.

## Where the team's data lives

- **The database is on SpacetimeDB.** Maincloud is the default: nothing to run, teammates join from
  anywhere, and SpacetimeDB handles TLS and sign-in. The advanced local option runs `spacetime start`
  on the host's computer for offline demos; then everyone must be on the same network.
- **The live model stays on the host's computer.** `cadswarm-host` executes ops against it and serves
  files, exports, snapshots and storage, so the host must stay on while the team works.
- **Invite codes are `cadswarm-2:` codes.** Each one works once and expires after a day. Make a new one
  for each teammate from the guide's invite page or the team panel's **Invite** button. They carry the
  database address and can be pasted as text, as a bare ticket, or as a phone link (`#t=`).

## What `cadswarm setup` writes

| Client | Files |
|---|---|
| Claude Code | `.mcp.json` (cadswarm server), `.claude/settings.json` (UserPromptSubmit hook, `enabledMcpjsonServers`, `mcp__cadswarm` allowed), `CLAUDE.md` section |
| Codex | `.codex/config.toml` (`[mcp_servers.cadswarm]` + `[hooks] UserPromptSubmit`, in a managed block), `AGENTS.md` section. Codex skips hooks you haven't reviewed: in Codex, type `/hooks` and trust the CadSwarm hook (again after updating CadSwarm, because Codex re-checks it when it changes). Until then your helper gets no team block before prompts and falls back to calling `model_status`/`get_inbox` itself |
| Droid | `.factory/mcp.json` (cadswarm server), `AGENTS.md` section |
| all | `.cadswarm/config.json` (uri, db, name, client, role), and after first run `.cadswarm/identity.json` (SpacetimeDB token, git-ignored) |

The prompt hook (Claude Code and Codex) injects a team block before every prompt: who holds which
parts, open tasks, what changed since your last prompt, and unread messages. It fails open: if the
team's database is unreachable, the prompt goes through without it. Droid gets the same context
through the MCP tools instead.

## Agent tools (MCP)

- **Awareness:** `model_status`, `get_object`, `wait_for_changes`
- **Locks:** `lock_objects`, `release_objects`
- **Modelling:** `create_primitive`, `create_extrusion`, `boolean`, `set_props`, `set_placement`,
  `delete_object`, `set_param`, `run_part_script`
- **Checks and output:** `check_interference`, `export_model` (STEP/STL), `save_snapshot`, `storage_status`
- **Simulation (FEA):** `list_faces`, `run_fea` (static / modal / thermal), `fea_status`, `publish_fea_report`
- **Components library:** `search_components`, `recommend_components`, `get_component`, `insert_component`,
  `add_standard_part`, `hole_size`, `import_component_from_url`, `add_component_from_file`
- **Team settings:** `get_settings`, `suggest_setting` (people accept suggestions; helpers never change settings)
- **Team design style:** `team_style`, `style_rules`, `teach_style` (a helper's rule waits for a person)
- **Project files:** `list_files`, `read_file`, `save_file`, `make_folder`, `move_file`,
  `delete_file`, `file_history`, `restore_file`
- **Teamwork:** `list_tasks`, `create_task`, `claim_task`, `report_result`, `send_message`,
  `get_inbox`, `ack_messages`, `take_over`

Optional, when an Elastic project is configured on the host: board datasheets, design memory and
reviewer lessons are available to the same agents, and an Agent Builder expert can supply a board
spec the local library lacks. See [ELASTIC.md](ELASTIC.md).

## Rules the team enforces

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
8. A person's instructions to a helper are `steer` messages (shown as COMMAND) and always come first
   in every inbox; the sender sees sent → seen (delivered) → done (acknowledged). Team chat is `chat`,
   last in the inbox and never treated as an order. Only people can give commands; a person's
   "command" to another person is delivered as chat. Peer messages are requests, never permissions.

## Tests

```powershell
.venv\Scripts\python -m pytest        # the integration and sandbox tests drive real FreeCAD
```

See [demo/README.md](demo/README.md) for a three-agent demo script.

## Known limits (hackathon MVP)

- Hand edits go through **✎ Edit a part**: sizes of primitives (box, cylinder, sphere, cone, torus) and
  position/rotation of any part. Sketch-based or fused parts can be moved but not reshaped by hand; ask a
  helper for those.
- `run_part_script` is sandboxed (see "Model scripts run in a sandbox"), but how well depends on the
  platform: strong on Windows with AppContainers, much weaker on macOS/Linux and in the Windows fallback.
  Each script also pays about half a second to start its own FreeCAD process. The sandbox protects your
  files and network, not the model: a script can still return any shape, so review what teammates' helpers
  add. Scripts can't edit other objects; they can only create or replace one shape.
- Locks are per object. Editing a shared upstream object (e.g. a sketch used by two features)
  requires its lock; there is no sub-object locking.
- Maincloud handles TLS and sign-in, and every person has their own identity; invites are single-use
  and expire after a day. The advanced local mode has no TLS, so use it only with people you trust on
  your own network.
- The host's computer serves the model, files, exports and snapshots. If it goes offline, teammates can
  still read the team's state from SpacetimeDB, but the model stops changing until the host is back.

## Structural optimization

The FreeCAD workbench includes **Optimize Part…** for simulation-guided material removal from supported grid-aligned solids. It runs real CalculiX or explicitly selected SciPy analysis, preserves the original, and shares a new part only after acceptance. See [installation, usage and limits](docs/structural-optimization.md) and the [reproducible numerical demonstration](demo/structural/README.md).
