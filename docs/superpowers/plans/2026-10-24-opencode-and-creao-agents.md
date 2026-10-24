# Plan: OpenCode as a first-class endpoint, Creao AI as an agent option

Date: 2026-10-24
Status: implemented
Branch: `feat/agents-opencode-creao` (cut from `feat/panel-tabs-workload-shifting`)

## What "endpoint" and "agent option" mean here

A CadSwarm **client** (the string stored on `member.client`, shown in the team list and used as
`endpoint`) is one of: `phone`, `workbench`, or an AI helper CLI. Adding a helper CLI means five
things, and today all five are keyed off one dict and two argv functions:

| Surface | File |
| --- | --- |
| Pick/validate the client, write its MCP config | `cadswarm/agent_setup.py` (`CLIENTS`, `setup_*`, `writers`) |
| `cadswarm setup --client …` | `cadswarm/cli.py` |
| Wake the helper on team events | `cadswarm/runner.py` (`agent_command`) |
| Spawn brief agents (planner/worker) | `host/src/agents.rs` (`agent_command`), `host/src/config.rs` (`AGENT_CLIENTS`) |
| Picker UI + human words | `cadswarm/gui/wizard.py`, `cadswarm/gui/panel.py` (`CLIENT_WORDS`) |

The module stores `client` as a free-form `String`, so **no schema change, no bindings
regeneration, no republish** is needed for either client.

## Decisions

1. **OpenCode = full first-class client** (`opencode`), at the same depth as `codex`/`droid`:
   auto-detected, config written by `cadswarm setup`, listed in both pickers, spawnable headless by
   the runner and the host.
2. **Creao AI = first-class client behind a local bridge** (`creao`). It has no CLI to spawn, so
   `cadswarm/creao_agent.py` runs locally, talks to `developer.creao.ai/v1/*`, and executes
   CadSwarm tools **in-process** through the existing MCP server object. "Auto-detected" means: a
   `CREAO_API_KEY` is present (env or `cadswarm.env`) → the Creao card is available and ready.
3. **One tool contract.** The Creao bridge calls `mcp_server.mcp.list_tools()` /
   `mcp.call_tool(name, args)`, not a parallel tool table. Tool behaviour, activity logging and
   fencing stay identical to every other helper.
4. **No secrets in the repo or helper folders.** `CREAO_API_KEY` is read from the environment at
   call time and never written into `.cadswarm/` files. Only non-secret ids are cached (agent id,
   conversation id, model).
5. **OpenCode stays opt-in.** It is never the default helper; `init`, the wizard and the picker keep
   `claude` as the default. Nothing auto-selects OpenCode.
6. **Creao conversation is per helper.** One `conversation_id` per helper folder, stored beside the
   agent id and reused across tasks (cheaper, and the agent accumulates its own team context). It is
   cleared by `--fresh` and by a new `cadswarm creao reset`. It is not shared between helpers.

## Spike result: can a Creao agent be given an MCP server directly?

**No.** Checked 2026-10-24 against `docs.creao.ai` (llms.txt, Skills & Connectors, Create Agent,
Update Agent, Skills) and the Developer API OpenAPI schemas:

- Connectors are a **fixed OAuth catalog** of official services (Gmail, Google Sheets, Slack,
  GitHub, Notion, Linear…). The page ends with "If you need an integration that isn't listed, let us
  know in the `#feature-requests` channel on Discord." There is no custom MCP server, URL, or stdio
  connection.
- `CreateAgentRequest` and `UpdateAgentRequest` are both `additionalProperties: false`. The only
  fields are `input`, `model`, `conversation_id`, `webhook_url` (create) and `name`, `description`,
  `version`, `skill_content`, `config_content`, `config`, `dashboard_template`, `write_autonomy`,
  `release_note`, `default_model_id`, `default_thinking_level` (update). No MCP/connector/tool field.
- What *is* supported: **custom skills** (`POST /v1/skills`, `source: "content"`, with a `files`
  map for `scripts/`) are installed account-wide and run inside the Creao sandbox, which has
  outbound network and `process.env` secrets.

Therefore the bridge stays. One design improvement comes out of this: instead of carrying the tool
protocol only in the prompt, the setup installs an account-level Creao skill named
`cadswarm-tools` whose `SKILL.md` defines the JSON tool protocol (and a `scripts/validate_turn.py`
that rejects a malformed turn before it reaches us). Reinstalling upserts by name, so it is
idempotent across helpers. Also set `write_autonomy: full_auto` on the created agent: a `/v1` run
cannot collect approvals, so `approval_required` would silently drop guarded writes.

Deferred, not in this change: if Creao ever ships custom MCP servers, or if we expose an
authenticated HTTP MCP endpoint reachable from maincloud, the sandbox script path would replace the
bridge. Recorded here so the bridge is understood as a workaround, not the intended end state.

## Contract (announce on the session bus before merging)

- New `member.client` values: `opencode`, `creao`. Both are `kind=agent` members; no module change.
- `cadswarm setup --client` accepts `claude | codex | droid | opencode | creao`.
- New subcommand `cadswarm creao reset` clears a helper's cached conversation id.
- `cadswarm-host init --agent` accepts the same five; `scripts/demo-up.ps1 -Agent` too.
- New files written into a helper folder:
  - opencode: `opencode.json`, `AGENTS.md`
  - creao: `.cadswarm/creao.json` (agent id, conversation id, model — no key), `AGENTS.md`,
    `start-helper.*`
- One account-level Creao skill, `cadswarm-tools` (upserted by `POST /v1/skills`), shared by every
  Creao helper on that account.
- New env vars (documented in `.env.example`): `CREAO_API_KEY`, `CREAO_AGENT_ID` (optional pin),
  `CREAO_MODEL` (optional), `CREAO_BASE_URL` (optional, for tests/proxies),
  `CADSWARM_OPENCODE` (optional explicit binary path).

---

## Phase 1 — OpenCode

### T1. `cadswarm/agent_setup.py`: the client and its config

- `CLIENTS["opencode"] = {"label": "OpenCode", "exe": "opencode",
  "install_url": "https://opencode.ai/docs/"}`.
- `setup_opencode(project, python)` writes/merges `opencode.json`:
  ```json
  {
    "$schema": "https://opencode.ai/config.json",
    "mcp": {"cadswarm": {"type": "local",
      "command": ["<python>", "<LAUNCHER>", "mcp"],
      "environment": {"CADSWARM_PROJECT": "<project>"},
      "enabled": true, "timeout": 120000}}
  }
  ```
  Merge rule like `setup_claude`: validate first, replace only our `mcp.cadswarm` entry, keep every
  other key and MCP server the person already has. Timeout 120 s because FEA/lattice calls block.
- Write the shared helper block into `AGENTS.md` (`AGENT_DOC`), which OpenCode reads natively — no
  prompt hook exists for OpenCode, so the block carries the "who holds what / ask for updates"
  instructions that the Claude hook and Codex hook inject.
- `writers["opencode"] = setup_opencode`.
- `find_agent_cli("opencode")` extra candidates: `%LOCALAPPDATA%\Programs\@opencodedesktop\resources\opencode-cli.exe`,
  `~/.opencode/bin/opencode(.exe)`, `~/.bun/bin/opencode`, `%APPDATA%\npm\opencode.cmd`.
  Keep the existing PATH-first order; `CADSWARM_OPENCODE` overrides (host side already reads
  `CADSWARM_<EXE>`).

**Tests** (`tests/test_setup_space.py`): an opencode folder gets `opencode.json` with the launcher
argv and `CADSWARM_PROJECT`; a pre-existing `mcp.other` and an unrelated key survive; a second run is
idempotent; `AGENTS.md` gets exactly one CadSwarm block.

### T2. `cadswarm/cli.py`

- `--client` choices gain `opencode`, `creao`.
- Next-step text: opencode → "open this folder and run `opencode`; it loads `opencode.json` and
  joins the team on start."

### T3. `cadswarm/runner.py`

- `agent_command("opencode", …)`:
  `[exe, "run", "--auto"]` + `["--continue"]` when resuming + `["--model", $CADSWARM_HELPER_MODEL]`
  when set, prompt passed the same way the other clients get it.
- **Spike first (30 min):** confirm whether `opencode run` accepts the prompt on stdin. If it does
  (like `codex exec … -` and `claude -p`), use stdin and reuse the existing `stdin_text` plumbing —
  that keeps multi-line prompts safe through npm's `.cmd` shim on Windows. If not, pass one argv
  string and add a Windows note (no `.cmd` mangling issues observed only for single-line prompts).

**Tests** (`tests/test_runner.py`): argv for opencode, with and without resume/model; stdin text is
`None`/prompt as decided by the spike.

### T4. Host (Rust)

- `host/src/config.rs`: `AGENT_CLIENTS` gains `"opencode"`.
- `host/src/agents.rs::agent_command`: `"opencode" => ("opencode", vec!["run", "--auto", prompt])`.
- Update the `bail!` text ("use claude, codex, droid or opencode, or set agent_command").
- `host/src/main.rs` `--agent` help text; no other change (brief prompts are client-agnostic).
- `scripts/demo-up.ps1`: `[ValidateSet("claude","codex","droid","opencode","creao")]`.
- `cadswarm/hosting.py::start_team` default stays `claude`; the value is already passed through.

**Tests:** `cargo test` for the new mapping (there is already a test module in `agents.rs`);
`tests/test_demo_scripts.py` for the new allowed values.

### T5. UI

- `panel.py`: `CLIENT_WORDS["opencode"] = "OpenCode helper"`.
- `wizard.py::page_agent`: the card appears automatically from `CLIENTS`; add the "not installed"
  help text for OpenCode (same pattern as Codex).
- `panel.py::NewHelperDialog`: no change needed (iterates `CLIENTS` and `find_agent_cli`).
- Launchers: `write_launcher` must not treat the prompt as a project path for OpenCode. Add a small
  `launcher_argv(client, first, resume)` helper: opencode uses `--prompt "<text>"` (TUI flag), other
  clients keep today's behaviour.

### T6. Docs

- README "Bring your own agent": add `opencode` to the `init --agent` line and the env-var sentence
  (`CADSWARM_OPENCODE`).
- `.env.example`: `# CADSWARM_OPENCODE=`.
- `docs/superpowers/specs/…` decision log line; no module/bindings notes needed.

**Acceptance (Phase 1):** on a machine with OpenCode installed, `cadswarm setup --client opencode`
joins, `opencode` in that folder sees the `cadswarm` MCP tools, a task mentioned from the phone wakes
that helper through `runner.py`, and `cadswarm-host init --agent opencode` builds a brief. The
default helper is still `claude` everywhere (`init`, `start_team`, wizard, picker).

---

## Phase 2 — Creao AI

### T7. `cadswarm/creao.py` — thin API client (no cover for the network in tests)

- `class Creao`: `key` from `CREAO_API_KEY`, base URL `CREAO_BASE_URL` or
  `https://developer.creao.ai`.
- `create_agent(description) -> agent_id` (`POST /v1/agents`, then poll the creation run).
- `run(agent_id, input, conversation_id=None, model=None) -> (run_id, conversation_id)`
  (`POST /v1/runs`).
- `wait(run_id, timeout) -> result dict` polling `GET /v1/runs/{run_id}` with 0.5–2 s backoff.
- Error handling by stable codes (`error.code`), `429` honours `Retry-After`, terminal states
  `completed | failed | cancelled`, timeouts surface as a clear message with the run id.
- Uses `urllib` (already the project's only HTTP dependency); no new package.

**Tests** (`tests/test_creao.py`): a stub transport (injectable `opener`) covering create→poll,
run→poll, 429-then-success, failed/cancelled terminals, missing key message.

### T8. `cadswarm/creao_agent.py` — the local bridge

- Boots exactly like `mcp_server.main()`: `S = Session()`, `S.connect()`, heartbeat thread.
- Tool catalogue: `await mcp.list_tools()` (name, description, JSON schema) — same tools, same
  descriptions the Claude/Codex/OpenCode helpers see.
- Protocol (also published as the `cadswarm-tools` Creao skill, see T9; the run input repeats the
  one-line contract so a missing or stale skill cannot break the loop):
  - reply with `{"tool": "<name>", "args": {…}}` to act, or `{"done": true, "summary": "…"}` to stop;
  - one tool per turn, never invent tools, never ask questions (headless);
  - the prompt already carries the role (planner/worker/handoff) and the wake text.
- Loop: run → parse → `await mcp.call_tool(name, args)` → feed the JSON result back with the same
  `conversation_id`; max 60 turns and a wall-clock cap (`AGENT_TIMEOUT` equivalent, 15 min); an
  unparsable reply gets one repair instruction, then fails with a clear message.
- Every tool call is already logged to `activity` by `mcp_server.call()`; the bridge also logs each
  turn to `helper.log`.
- CLI: `python -m cadswarm.creao_agent --dir FOLDER` with the prompt on stdin (matches how
  `runner.py` and `agents.rs` hand prompts to CLIs).

**Tests** (`tests/test_creao_agent.py`): fake `Creao` + fake tool registration: a two-turn
tool-then-done session; unknown tool is reported as an error result rather than executed; bad JSON
gets exactly one repair attempt; turn cap and timeout end with a failing status.

### T9. `cadswarm/agent_setup.py` for `creao`

- `CLIENTS["creao"] = {"label": "Creao AI", "exe": None,
  "install_url": "https://developer.creao.ai/"}`.
- Readiness is not a binary: `agent_ready(client)` new helper = `find_agent_cli` for CLI clients,
  `creao_configured()` for creao (key present). Use it in `panel.py` where `find_agent_cli` gates
  auto-start, and in both pickers.
- `setup_creao(project, python)` writes nothing MCP-shaped; it writes/merges `AGENTS.md`, and on
  first use resolves the Creao agent id:
  - `CREAO_AGENT_ID` if set → store it;
  - else `.cadswarm/creao.json` if already stored → reuse;
  - else `POST /v1/agents` with a fixed description ("a CAD build agent that uses the CadSwarm
    tools") and store the returned id.
  Network/credential failures must fail **before** the invite is redeemed where possible (validate
  the key with a cheap authenticated call first), so a bad key never burns a single-use invite.
- Then, once per account (idempotent upsert, safe to repeat):
  - `POST /v1/skills` `{source: "content", content: <SKILL.md>, files: {"scripts/validate_turn.py": …}}`
    under the fixed name `cadswarm-tools` — the JSON tool protocol plus a turn validator;
  - `PATCH /v1/agents/{id}` with `write_autonomy: "full_auto"` (a `/v1` run cannot collect
    approvals, so `approval_required` would drop guarded writes without telling anyone).
  Both are best-effort with a logged warning: a team whose account can't install skills still runs
  on the prompt-level protocol.
- `.cadswarm/creao.json` holds `{agent_id, conversation_id, model}` — no key, ever. The
  conversation id is per helper folder and reused across tasks; `cadswarm setup --fresh` and the new
  `cadswarm creao reset` clear it.
- Implemented as **one Creao agent per computer/account, shared by every helper** (`creao.load_shared`
  / `save_shared` in `paths.app_dir()`, agent id only). The first setup creates it, later setups
  reuse it, and an older folder's id is migrated up. `CREAO_AGENT_ID` still pins one explicitly.
- `write_launcher` for creao writes a script that runs the **runner**
  (`<python> <LAUNCHER> runner --dir <project>`), because a Creao helper is always consumed
  headlessly; no terminal CLI to open.

**Tests** (`tests/test_setup_space.py`): config.json records `client: "creao"`; no `.mcp.json`
written; `creao.json` holds `agent_id` and `conversation_id` but no key; a second setup reuses the
id and does not call `create_agent` twice; a failed key check happens before `redeem`.

### T10. `cadswarm/runner.py`

- `agent_command("creao", …)` → `[python, "-m", "cadswarm.creao_agent"]`, prompt on stdin, resume
  ignored (Creao keeps context server-side via its own conversation id, stored in
  `.cadswarm/creao.json` next to the agent id).
- `wake()` uses `agent_ready` instead of `find_agent_cli`, with the status text
  "Creao AI needs CREAO_API_KEY on this computer".

### T11. Host (Rust)

- `AGENT_CLIENTS` gains `"creao"`; `agents.rs::agent_command` returns
  `(cfg.cadswarm_python, ["-m", "cadswarm.creao_agent"])` with the prompt supplied on argv/stdin
  the same way the other clients are.
- `cadswarm setup --client creao` runs first (identity), then the bridge — the existing spawn
  sequence already does this; no new plumbing.
- The **reviewer stays Droid** (`init-reviewer` writes a Droid MCP config). Note in docs that Creao
  is a builder/planner option, not a replacement for the reviewer in this change.
- `agents.rs` needs the child's cwd to be the agent folder (already true).

**Tests:** `cargo test` mapping; a Rust unit test that the creao argv contains `-m
cadswarm.creao_agent`.

### T12. UI

- `panel.py`: `CLIENT_WORDS["creao"] = "Creao AI helper"`.
- `NewHelperDialog`: list Creao only when `creao_configured()`; when it is, show it selected with the
  note "uses your CREAO_API_KEY".
- `wizard.py`: the Creao card shows "Ready — found your Creao key" / "Add CREAO_API_KEY to
  cadswarm.env" instead of the "How to install" link; the finish step must not promise a terminal
  the way `open_agent_terminal` does — it starts the runner.
- Web mock (`web/src/mock.ts`) may use a `creao` client in one helper row for visual parity; no web
  API change.

### T13. Docs and keys

- `.env.example`: `CREAO_API_KEY=`, optional `CREAO_AGENT_ID=`, `CREAO_MODEL=`, `CREAO_BASE_URL=`,
  with the "server-side only, never in the browser" warning.
- README: a "Creao AI" subsection under "Bring your own agent" covering the key, the agent id the
  host creates, credit usage, and the data-egress note (prompts and tool results leave the machine).
- `docs/devpost-submission.md` sponsor table: add Creao under the agent integrations row.
- `docs/publishing-spacetimedb.md`: one line that this change needs no republish.

**Acceptance (Phase 2):** with `CREAO_API_KEY` set and an invite, a Creao helper appears in the
picker, joins as `client=creao`, and completes a two-task brief through planner and worker roles
reusing one `conversation_id`; killing the key produces a readable failure, not a hang; `pytest` and
`cargo test` are green without network access.

---

## Sequencing and commits

1. Spike: `opencode run` stdin vs argv (timeboxed, recorded in this file).
2. Phase 1 tasks T1–T6 as one commit series: `feat(agents): OpenCode as a first-class AI helper`.
3. Phase 2 tasks T7–T9 (client + bridge, unit-tested) — `feat(agents): Creao AI client and bridge`.
4. Phase 2 tasks T10–T13 (wiring, UI, docs) — `feat(agents): Creao AI in the helper picker`.
5. Full `pytest -q`, `cargo test`, and one live smoke run per client before the PR.

## Risks and mitigations

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| Creao has no custom MCP and no native tool call (verified) | The JSON tool protocol is prompt-enforced and can drift | Account-level `cadswarm-tools` skill + validator script narrows it; one repair attempt, turn cap, every failure attributable in `helper.log`; Creao is additive, the CLI helpers stay the default path |
| Creao-headed helpers share one account-wide agent | Two helpers on the same account also share one Creao skill install (fine) but not the conversation id (per helper) | Agent id is cacheable; if a team wants distinct Creao agents, `CREAO_AGENT_ID` pins one per helper |
| OpenCode prompt transport on Windows `.cmd` | Multi-line prompts mangle as argv | Prefer stdin; fall back to a temp prompt file passed with `--file`/`--prompt` if the spike shows neither works |
| OpenCode MCP tool permission names | A headless run must not stall on approvals | `run --auto` for headless; a `permission` block only after verifying the real tool ids with `opencode debug config` |
| Creao key leaks into a helper folder | Single-use invites and team identity live there | Never persist the key; `.cadswarm/.gitignore` already covers the folder |
| Credit burn / rate limits | A brief could spend credits silently | Cache the agent id, log `usage.credits` per run into activity, surface `429`/insufficient-credit as a failed task with the code |
| Reviewer semantics | Droid is the reviewer today | Out of scope; documented explicitly |

## Open questions

1. Creao `model`: pin `CREAO_MODEL` for every helper (predictable cost) or let the account default
   decide? Current plan: unset by default, passed through when present.
2. Does the phone/web UI need to show `opencode`/`creao` distinctly from "AI helper", or is
   `CLIENT_WORDS` enough?
3. Should `cadswarm creao reset` also clear the agent id (a fresh Creao agent per helper), or only
   the conversation?
