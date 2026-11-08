# CADSwarm

CADSwarm is an independent parametric CAD desktop app with collaborative AI agents. Its desktop UI and
model document are CADSwarm-owned; FreeCAD is not installed, launched, embedded, or required. The project
uses FreeCAD and Onshape as references for CAD behavior and workflows.

## Windows quick install

Paste this into PowerShell. The installer creates an isolated CADSwarm runtime, installs the desktop
dependencies, and adds Start Menu and desktop shortcuts. If Python 3.12 is missing, it installs the
current-user Python runtime with `winget`.

```powershell
irm https://raw.githubusercontent.com/SachchitBalamurugan/cadswarm/main/install/quick-install.ps1 | iex
```

For this development branch, set `$env:CADSWARM_REF="feat/standalone-app"` before running the command.

For a development checkout:

```powershell
python -m pip install -e ".[desktop]"
cadswarm-app
```

The standalone project format is `.cadswarm`. It stores the model history, shared parameters, and project
assets together. Exact solids can be exchanged as STEP; STL imports remain mesh references. The alpha CAD
workflow includes constrained sketches, a parametric part history, common part features, and undo/redo.

## Multiplayer and AI agents

From the desktop app, choose **Team → Host a local multiplayer team** to share the open project. CADSwarm
uses its existing SpacetimeDB team, invite, presence, chat, planning, lock, and agent handoff flow. Teammates
can join from the browser/phone view, and Claude Code, Codex, or OpenCode agents can work through the team
MCP server. Geometry changes go through the shared host so object locks apply. CADSwarm does not call an
OpenAI API. While a multiplayer host is running, the host desktop shows live geometry but is read-only;
make shared model edits through the team MCP tools so they acquire the correct object locks.

For solo MCP access, install the package's desktop extra and run `cadswarm-standalone-mcp`. Multiplayer
projects should use the team MCP server started by the host.

## Development

```powershell
python -m pip install -e ".[desktop,test]"
cadswarm-app
```

The Python package contains the model engine, local MCP server, and JSON-lines team worker. The Rust host
and SpacetimeDB module are in `host/` and `module/`; `web/` contains the browser and phone workspace.

See [the independent platform design](docs/superpowers/specs/2026-11-07-independent-platform.md) for the
architecture and deferred platform work. The previous FreeCAD workflow documentation is retained in
[the legacy guide](docs/legacy-freecad-guide.md) for reference only.
