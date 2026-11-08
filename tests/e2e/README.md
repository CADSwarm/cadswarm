# Multiplayer end to end

Runs CADSwarm's team features for real on this computer: a local SpacetimeDB, a team hosted the way the setup
guide hosts one (`hosting.start_team`: publish the module, `cadswarm-host init`, `cadswarm-host run`), invites
minted by the host, and real clients.

| scenario | what joins | what it checks |
|---|---|---|
| `team` | two CADSwarm app windows (`python -m cadswarm.app`, each with its own `APPDATA` and `CADSWARM_ENGINE_HOME`) + a headless member | both join from the panel's own start-up rejoin; presence; chat sent from each panel reaches the other and the headless member; a part locked through the panel's Lock shows as held in both windows and a second lock is refused; an agent's pointer reaches both; a screenshot of each window |
| `build` | a person hands a request to the team (`@team ...`) | the host plans numbered steps (plan v2), spawns agent CLIs that build them through the cadswarm MCP server in dependency order, the parts are in the shared model, the reviewer passes it, the one-job agents leave the team |
| `remote` | the web app's own `api.ts` (the phone's code, `web/test/remote.live.test.ts`) | joins with an invite, sees the team, chats, runs an op on the host's FreeCAD model, locks the part, points at it, sends an `@all` command; a desktop-side member sees all of it |
| `handoff` | a person holding a part hands off now | the host starts a `handoff-*` agent that accepts first, takes the work, and leaves the team when done |

## Before the first run

```powershell
cargo build --release --manifest-path host\Cargo.toml
cargo build --release --target wasm32-unknown-unknown --manifest-path module\Cargo.toml
cd web; npm ci; cd ..
spacetime login            # the publisher becomes the team's host member
```

FreeCAD 1.1 must be installed for `team` (the app windows). `build` and `handoff` use the agent CLI you pick:
`--agent claude|codex|opencode` (signed in, non-interactive) or `--agent fake`, a scripted agent
(`fake_agent.py`) that speaks MCP to the real cadswarm MCP server, so the host's whole path runs without an
AI account and the same way every time.

## Run

```powershell
.venv\Scripts\python tests\e2e\team_e2e.py --scenario all --agent fake
.venv\Scripts\python tests\e2e\team_e2e.py --scenario build --agent claude --runs 3   # pass rate over 3 runs
```

Each run hosts a fresh database (`cadswarm-e2e-<random>`) on the SpacetimeDB at port 3000, starting one with a
private data folder under `--out` if none is running. Everything lands in `--out` (default
`%TEMP%\cadswarm-e2e-<time>`): `summary.json` (every check, the build timeline, step owners and times),
`runN/windows/*.png` (the app screenshots), `runN/host/.cadswarm-team/host.log`, each agent's `agent.log`.
The exit code is 0 only when every scenario of every run passed.

As pytest (skipped unless `CADSWARM_E2E=1`, so the normal suite stays fast):

```powershell
$env:CADSWARM_E2E = "1"; .venv\Scripts\python -m pytest -m e2e tests\test_e2e_team.py -q
$env:CADSWARM_E2E_AGENT = "claude"   # default: fake
```
