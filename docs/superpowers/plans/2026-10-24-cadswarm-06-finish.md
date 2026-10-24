# CadSwarm Finish Plan (plan 06): from v0.1.0 to a judged submission

> **For the executing session:** a director session (Opus or similar) runs this plan. **All code and
> doc changes are written by DeepSeek** through the `cloud-swarm` MCP (`swarm_edit_files` for edits,
> `swarm_delegate` for drafting, `swarm_fanout` for audits) with model `deepseek-ai/DeepSeek-V4.1-Flash`.
> The director only chooses the next step, writes the instruction, runs the commands, reviews diffs and
> merges. No headless `claude -p` runs and no Claude subagents. Follow the **Execution protocol**
> below for every step. Tasks use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge everything that's built, fix the known bugs, prove the phone brief end to end (M2)
on Maincloud, make the demo one command to start, then submit on Devpost with a recorded video and a
printed part. Local-mode reach and voice come after that, and only if time is left.

**State at the time of writing (Sat 2026-10-24):**
- `main` = `f648109`. It has the module (SpacetimeDB 2.13.0), host executor and brief orchestration,
  Python on SpacetimeDB (SpaceHub, helper, invites), the web app, bring-your-own agent CLI
  (`agent_client` / `agent_command`), and Elastic (plan 05).
- `release/v0.1.0` (`f813273`, 18 ahead of main) adds:
  - the FreeCAD guide on SpacetimeDB, which downloads the team server on first use
  - `cadswarm/reviewer.py`, `cadswarm-host init-reviewer`, and the host starting the reviewer
  - the host spawning an agent per open handoff, and the web handoff card
  - a Pages deploy workflow and a release workflow
  - a SpacetimeDB README, `.env.example`, and `scripts/with-env.ps1`
- `fix/release-review` (`1d36ed6`, off `release/v0.1.0`) has 3d-cad-b6's review fixes:
  `PYTHONSAFEPATH` instead of `-I`, reviewer fence adoption, a handoff retry backoff, a reviewer grace
  window, the handoff card arm/reload fix, and others. It passes host 26/26, pytest 286 + 3 skipped,
  and web 25/25.
- Teammate branches not on main:
  - `feat/fea-simulation` (Anegs, 44 ahead; FEA plus a larger team panel; already merged web, guide and elastic in)
  - `UI-change-dash` (Sachchit, 3 ahead)
  - `feature/structural-optimization` (swapnilbotu, 1 ahead)
- Live demo stack on the director's laptop:
  - Maincloud db `cadswarm-calhacks-ca8c`
  - host data dir `C:\Users\waves\Documents\Projects\cadswarm-demo`
  - web on `vite preview :4173` behind a Cloudflare quick tunnel
- M1 is verified live: a Claude agent created a Box through the Maincloud host. **M2, a brief from the
  phone that ends `ready` with an STL, has never been run.**
- Devpost packet: `docs/devpost-submission.md` and a pre-submission checklist on branch
  `docs/spacetime-design`.

**Deadline:** Devpost submission **Sun 2026-10-25 9:00 AM PT**. Code freeze Sun 06:00 PT.

---

## Execution protocol (applies to every task)

1. **Worktree, never the shared checkout.** Never switch the branch of `C:\Users\waves\Documents\Projects\3D CAD`.
   For each task: `git -C "C:\Users\waves\Documents\Projects\3D CAD" fetch origin` then
   `git -C "C:\Users\waves\Documents\Projects\3D CAD" worktree add -b <branch> C:\Users\waves\Documents\Projects\cadswarm-<short> origin/main`.
   Remove the worktree after merge (`git worktree remove <path>`).
2. **Instruct DeepSeek precisely.** For each file change call `mcp__cloud-swarm__swarm_edit_files` with
   `root` = the worktree, `files` = the exact paths, `model` = `deepseek-ai/DeepSeek-V4.1-Flash`, and an
   `instruction` that quotes the plan step's code and says "change nothing else". For a new file, use
   `mcp__cloud-swarm__swarm_delegate` to draft it (paste the spec, ask for the whole file), then write
   it with the Write tool, or create an empty file and run `swarm_edit_files` on it.
   Use `dry_run: true` first when the step touches more than one function.
3. **Test-first where the step says so.** Add the failing test, run it, and see it fail for the
   expected reason. Then apply the change and see it pass.
4. **Run the suites before every commit:**
   - Python: `C:\Users\waves\Documents\Projects\cadswarm-python\.venv\Scripts\python -m pytest -q --ignore=tests/test_integration.py`, run **from the worktree root**, so the worktree's `cadswarm` is imported
   - Host: `cargo test --manifest-path host/Cargo.toml`
   - Rules: `cargo test --manifest-path rules/Cargo.toml`
   - Module build: `C:\Users\waves\AppData\Local\SpacetimeDB\spacetime.exe build --module-path module`
   - Web: `npm --prefix web ci` once, then `npm --prefix web test -- --run` and `npm --prefix web run build`
5. **Review the diff yourself** (`git diff`) before committing. Reject anything outside the step's scope.
6. **Commit messages** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
   PR descriptions end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
7. **Never disturb the live demo stack.** Never publish to `cadswarm-calhacks-ca8c` with
   `--delete-data`, and never stop a process you didn't start. Local tests use their own server, e.g.
   `spacetime start --listen-addr 127.0.0.1:3300` and
   `scripts/publish-local.ps1 -Server http://127.0.0.1:3300 -Db cadswarm-t<task>`, then stop only that PID.
8. **No secrets in commits.** Keys live in the secrets file loaded by `scripts/with-env.ps1` (see
   `.env.example`). Before every push, run `git diff origin/main --stat` and grep the diff for `ApiKey`, `sk-`, `token`.
9. **Report** at the end of each task: branch, commits, test counts, deviations, open risks.

---

## Priority order and timeline (PT)

| Order | Task | Target done | Blocks |
|---|---|---|---|
| 0 | T0 Repo visibility: release downloads must be public | Sat 12:00 | every guide-hosted team |
| 1 | T1 Integrate release + review fixes into main | Sat 12:00 | everything |
| 2 | T2 Known bugs from the review (4): **done by 3d-cad-b6 on `fix/release-review` `adf90d2`; verify in T1** | — | M2 |
| 3 | T3 Web polish: pointers and step labels | Sat 13:30 | demo video |
| 4 | T4 One-command demo stack | Sat 15:00 | M2, video |
| 5 | T5 M2 rehearsal on Maincloud (people run it) | Sat 17:00 | video, print |
| 6 | T6 Teammate branches merged | Sat 20:00 | freeze |
| 7 | T7 Submission: README, Devpost, video, print, booths | Sun 08:30 | — |
| 8 | T8 Local-mode gateway behind one quick tunnel (P2) | only if T1–T6 are done by Sat 22:00 | — |
| 9 | T9 Voice with Creao + Cartesia (stretch) | only if Creao confirms and T8 is done or skipped | — |

Out of scope for this event (state it in the Devpost "what's next"): iroh transport for laptops,
`cadswarm-host tunnel` client mode, the build123d and Onshape backends.

---

### T0: Release downloads must be reachable without a GitHub login

**Blocker (found by 3d-cad-b6).** The FreeCAD guide's first "Start a team" calls `hosting.ensure_tools`,
which downloads `https://github.com/SachchitBalamurugan/cadswarm/releases/latest/download/SHA256SUMS.txt`
and the binaries. The repo is **private**, so anyone not logged in gets 404 and the guide fails with
"Couldn't check the CadSwarm downloads". Devpost judging also needs a public repo link.

**Decision for the repo owner (Sachchit):** make the repository public (recommended: judges need it anyway).
The alternative is publishing the release files to a public location and pointing `ensure_tools` at it.

If making it public:
- [x] **Scan the full history for secrets first.** Run `gitleaks detect --source . --log-opts="--all"` in a fresh clone (install with `winget install gitleaks`). If gitleaks is not available, use DeepSeek `swarm_fanout` over `git log --all -p` chunks with the instruction "list any API key, token, password, private key or .env content; quote the commit and line". Also grep for `ELASTIC_API_KEY=`, `FACTORY_API_KEY=`, `CARTESIA`, `ApiKey `, `-----BEGIN`, and `trycloudflare.com/#t=` (phone links carry live single-use invite tokens).
  **Done 2026-10-24 without gitleaks** (not installed): `git log --all -p` over all 228 commits (1.88 MiB pack) searched for every pattern above plus `BEGIN *PRIVATE KEY`, `sk-`, `ghp_`, `password=`, `token=` and long `ApiKey` values. Every hit is placeholder or prose: `.env.example` keys are empty, one doc line says `<base64 api key>`, and the `trycloudflare` hits are regexes, a config `allowedHosts`, and plan text. No private key, live token, invite URL or tracked `.env` file. The only tracked env file in history is `.env.example`.
- [ ] If anything is found: rotate the key at its provider **first**, then decide with the owner whether to rewrite history (`git filter-repo`) or accept the rotated key in history.
- [ ] The owner flips visibility (Settings → General → Danger Zone). Verify with `curl -sI https://github.com/SachchitBalamurugan/cadswarm/releases/latest/download/SHA256SUMS.txt` while logged out: expect `302` then `200`.

**Acceptance:** an anonymous `curl` of `SHA256SUMS.txt` succeeds, and the guide's first start passes `ensure_tools` on a clean machine.

---

### T1: Integrate `release/v0.1.0` and `fix/release-review` into main

> Updated: `fix/release-review` is now at `adf90d2`. On top of the 1d36ed6 fixes it adds:
> - T2's four bug fixes
> - macOS and Linux support (FreeCAD discovery, `install/install.sh`, `Install CadSwarm.command`)
> - `.github/workflows/test.yml`, whose run 40080333498 passed all 9 jobs
> Merge that head. Then check that each T2 acceptance test exists, and pick up the tests T2 names if they're missing.

**Owner:** director. **Branch:** `release/v0.1.0`, then a PR into `main`.

- [ ] **Step 1: Fold the review fixes in.** In a worktree on `release/v0.1.0`, run
  `git merge --no-ff origin/fix/release-review -m "merge: review fixes from 3d-cad-b6"`. Expected: no conflicts, because fix/release-review branched from release/v0.1.0's head.
- [ ] **Step 2: Merge main into the release branch.** `git merge origin/main`. Expected conflicts, if any, in:
  - `host/src/agents.rs`: main has `cli_candidates` / `find_cli` / `agent_command` and `agent_client`; release has `handoff_prompt`, `Agent::from_child` and PYTHONSAFEPATH.
  - `host/src/briefs.rs`: main has Elastic `looked_up_board` / `index_design` / `keep_sources` and `tick(space, helper)`; release has `open_handoffs` spawning, the reviewer and the grace window.
  - `host/src/config.rs`, `host/src/main.rs`, `host/src/space.rs`
  - `README.md`, `cadswarm/space_hub.py`
  Resolution rule: **keep both sides' features.** Specifically:
  - `tick` takes `helper: Option<&mut WorkerDriver>` (main). The open-handoff loop (release) runs at the start of `tick`, before the brief loop.
  - `Agent::spawn` uses `agent_command(&cfg.agent_client, &cfg.agent_command, &dir, prompt)` and `find_cli` (main), and sets the env `PYTHONSAFEPATH=1` on the `cadswarm setup` command instead of passing `-I` (release fix).
  - `config::python_says` uses `PYTHONSAFEPATH=1` (release fix), not `-I`.
  - `Agent::from_child` and `handoff_prompt` (release) stay.
  - `QUERIES` in `space.rs` is the union: `v_param` from main and `v_handoff` from release.
  Resolve by hand, then use `swarm_edit_files` only for mechanical follow-ups that the compiler reports.
- [ ] **Step 3: Run every suite** (Execution protocol 4). Expected: host ≥ 29 tests (26 + main's 3 BYO agent tests, minus duplicates), pytest ≥ 290 passed, web 25/25, the module builds.
- [ ] **Step 4: PR and merge.**
  `gh pr create --base main --head release/v0.1.0 --title "v0.1.0: FreeCAD guide on SpacetimeDB, Droid reviewer, AI handoff, Pages + release CI"`, with a body that lists the features and test counts. Merge with `gh pr merge --merge`. Then tag: `git tag v0.1.0 origin/main && git push origin v0.1.0`, only if the release workflow triggers on tags (check `.github/workflows/release.yml`'s `on:`). If it triggers on `release` or `workflow_dispatch`, run it with `gh workflow run release.yml`.
- [ ] **Step 5: Rebuild the live host** from main:
  `cargo build --release --manifest-path host/Cargo.toml` in a main worktree. Restart the demo host **from T4's script**, not by hand.

**Acceptance:** main contains all of release/v0.1.0 and fix/release-review; all suites are green; the release workflow run is green.

---

### T2: Fix the four bugs left open by the review

**Owner:** DeepSeek via the director. **Branch:** `fix/review-leftovers` off main (after T1).

#### T2a: A host restart during `reviewing` leaves the brief stuck

`host/src/briefs.rs`. On a fresh runtime `rt.review_round == 0`, so the watchdog (`rt.review_round > 0 && …`) never fires, and `next_action` returns `Wait` for `reviewing` with `review == None`.

- [ ] Add a field to `BriefState`: `pub reviewer_started: bool,` (set from `rt.review_round > 0` where the state is built).
- [ ] In `next_action`, insert this arm **before** `"reviewing" if s.reviewer_alive || s.exporting => Action::Wait,`:
```rust
        // After a host restart nothing is reviewing this brief: start the reviewer again (or export
        // if the reviewer was turned off).
        "reviewing" if !s.reviewer_started && !s.reviewer_alive && !s.exporting && s.review.is_none() => {
            if s.reviewer_enabled { Action::Review } else { Action::Export }
        }
```
- [ ] Test in `briefs.rs` `mod tests`:
```rust
    #[test]
    fn a_restarted_host_resumes_a_brief_stuck_in_reviewing() {
        let st = |enabled| BriefState { status: "reviewing".into(), reviewer_enabled: enabled, ..Default::default() };
        assert_eq!(next_action(&st(true)), Action::Review);
        assert_eq!(next_action(&st(false)), Action::Export);
        // A reviewer started this session is left alone (the watchdog handles it).
        assert_eq!(next_action(&BriefState { reviewer_started: true, ..st(true) }), Action::Wait);
    }
```

#### T2b: `init-reviewer` mints an invite even when the reviewer is already set up

`host/src/main.rs`, arm `Cmd::InitReviewer`.
- [ ] Add `#[arg(long)] fresh: bool` to the `InitReviewer` variant (it becomes `InitReviewer { fresh: bool }`), described as "Forget the existing reviewer identity and join again".
- [ ] At the top of the arm, after computing `dir`: if `dir.join(".cadswarm").join("identity.json").exists() && !fresh`, then `println!("reviewer already set up in {} (use --fresh to redo)", dir.display());` and return `Ok(())` **before** calling `mint_invite`. With `--fresh`, pass `--fresh` through to `cadswarm.cli setup`.
- [ ] Run the same `cadswarm.cli setup` command with env `PYTHONSAFEPATH=1` (consistent with T1).
- [ ] No unit test (it's I/O); verify by running `init-reviewer` twice against a local test db: the second run prints "already set up", and no new invite row appears (`spacetime sql … "SELECT COUNT(*) FROM invite"` as the owner).

#### T2c: `validate_spec` accepts `hole_d = 0`, which seeds solid standoffs

`cadswarm/knowledge.py`, `validate_spec`.
- [ ] After the holes loop, add:
```python
    hole_d = float(raw.get("hole_d", 0))
    if holes and not 0.5 <= hole_d <= 8:
        raise ValueError(f"hole_d {raw.get('hole_d')} mm looks wrong for mounting holes")
```
  and use `hole_d` in the returned dict.
- [ ] Test in `tests/test_knowledge.py`: a spec with two holes and `hole_d: 0` raises `ValueError` matching `hole_d`. The same spec with `hole_d: 2.7` validates and returns `hole_d == 2.7`. Base it on an existing valid spec fixture in that file.

#### T2d: `lookup_board` fails with `bad_args` when an agent passes only a board

`cadswarm/helper.py` `lookup_board` requires `question`, while `SpaceHub.lookup_board` sends `""`.
- [ ] In `helper.py` `lookup_board`, replace `question = _need(a, "question")` with:
```python
    question = (a.get("question") or "").strip() or (
        f"{a['board']} dimensions, mounting holes and ports" if a.get("board") else "")
    if not question:
        raise HelperError("bad_args", "lookup_board needs a question or a board")
```
  `HelperError` is whatever error class `_need` raises; reuse it.
- [ ] Test in `tests/test_helper.py`: `lookup_board` with only `board` calls the fake Elastic search with a question containing the board id. With neither, it returns code `bad_args`.

**Acceptance:** all suites green; PR `fix/review-leftovers` merged.

---

### T3: Web polish (agent pointers and step labels)

**Owner:** DeepSeek via the director. **Branch:** `fix/web-polish` off main.

#### T3a: An agent's pointer stays on screen after its op finishes

Root cause: the module's `complete_op` sets the actor's pointer to mode `"pointing"` with an empty object and label (`module/src/ops.rs` line ~103: `set_pointer(ctx, ws, by, "", vec![], "pointing", "", "")`). The web store's `activePointers()` keeps every non-idle pointer of an active member, so the glyph lingers. The fix is on both sides:
- [x] **Module** `module/src/ops.rs`: change that call's mode `"pointing"` to `"idle"`. The agent's next tool call moves the pointer again through `log_activity` / `submit_op`. No schema change: republish **without** `--delete-data`.
- [x] **Web** `web/src/store.ts` `activePointers()`: also drop pointers that have nowhere to point:
```ts
  activePointers() {
    return this.state.pointers.filter(p =>
      p.mode !== 'idle' && (p.object !== '' || p.point.length === 3) &&
      this.member(p.member)?.status === 'active');
  }
```
- [x] **Web** `web/src/mock.ts`: once the brief is `ready`, the mock keeps calling `build(...)` (the final `else` branch). Change that branch so it no longer creates `editing` pointers. Advance a counter and do nothing visible, or re-emit only `activity`. The pointers set to `idle` at the `ready` step must stay idle.
- [x] Test `web/test/store.test.ts` (new `it`): a pointer `{mode: 'pointing', object: '', point: []}` of an active member is not in `activePointers()`; `{mode: 'editing', object: 'Lid', point: []}` is.
- [x] **Deploy order:** merge, then publish the module to Maincloud **without** `--delete-data`:
  `spacetime publish --server maincloud --module-path module --yes cadswarm-calhacks-ca8c`. Run `spacetime publish --help` first to confirm a non-breaking publish of the same db keeps data. If it prompts about breaking changes, stop and ask a person.

#### T3b: Step labels truncate on phones

`web/src/style.css` `.steps li` uses `white-space: nowrap; text-overflow: ellipsis` at 10.5 px, and `web/src/ui/panels.ts` renders `STEP_LABEL` = Sent / Plan / Build / Review / Ready (uppercased by CSS).
- [x] In `style.css`, change `.steps li` to wrap instead of truncating: replace `overflow: hidden; text-overflow: ellipsis; white-space: nowrap;` with `overflow-wrap: anywhere; white-space: normal;`, and drop the letter-spacing to `.02em`.
- [x] Keep the single label set (`STEP_LABEL`), and add the full status as an accessible name: in `panels.ts`, the `<li>` already has `title="${st}"`; add `aria-label="${st}"`. The build bubble lives in `ask.ts`, so the labels and the full names were added there; `panels.ts` only exports the status list.
- [x] Visual check: build, serve `vite preview`, open `/?mock` at 360×740 (headless Edge plus CDP device metrics, as before), and confirm all five labels are fully visible in the `reviewing` state. Save the screenshot to the scratchpad. **Screenshots were unavailable** (the browser pane refuses to capture a hidden tab and headless Edge returned blank frames), so the check was done in the live page: the five `<li>`s were cloned into a 300 px and then a 180 px wide container. At 300 px each label is one line (57 px, `scrollWidth` equals `clientWidth`). At 180 px each wraps to two lines (33 px wide, 34 px tall) with `scrollHeight` equal to `clientHeight`, so nothing is clipped, and every label carries its full `title` and `aria-label`.

**Acceptance:** web tests green (≥ 26), screenshot shows full labels, the module is republished, and no lingering pointer after `ready` in `?mock`.

---

### T4: One-command demo stack

**Owner:** DeepSeek via the director. **Branch:** `feat/demo-scripts` off main.

Create `scripts/demo-up.ps1` and `scripts/demo-down.ps1`, both runnable with Windows PowerShell 5.1 and pwsh 7.

`scripts/demo-up.ps1` parameters (with defaults):
```powershell
param(
  [string]$Data = "$env:USERPROFILE\Documents\Projects\cadswarm-demo",
  [string]$Db = "cadswarm-calhacks-ca8c",
  [string]$Python = "",            # default: .venv\Scripts\python.exe next to this repo, else `python`
  [ValidateSet("claude","codex","droid")][string]$Agent = "claude",
  [int]$WebPort = 4173,
  [string]$WebUrl = "",            # set to the GitHub Pages URL to skip the web tunnel
  [switch]$SkipBuild
)
```
Behaviour, in order. Every external tool comes from `Get-Command` or a known path, and the script fails with a plain message if one is missing:
1. `$ErrorActionPreference = 'Stop'`, but wrap native calls in a helper that sets `'Continue'` and checks `$LASTEXITCODE` (the PS 5.1 stderr lesson from `publish-local.ps1`).
2. Resolve the tools:
   - `spacetime`: `$env:SPACETIME_BIN`, then PATH, then `$env:LOCALAPPDATA\SpacetimeDB\spacetime.exe`
   - `cloudflared`: PATH, then `${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe`, then `$env:ProgramFiles\cloudflared\cloudflared.exe`
   - `cadswarm-host`: `host\target\release\cadswarm-host.exe` under the repo root
   - `node` / `npm`
3. Unless `-SkipBuild`: `cargo build --release --manifest-path host/Cargo.toml`, then `npm --prefix web ci` (only if `web/node_modules` is missing) and `npm --prefix web run build`.
4. Web URL. If `-WebUrl` is empty:
   - start `npx --prefix web vite preview --config web/vite.config.ts web --port $WebPort --strictPort --host 127.0.0.1` as a hidden background process
   - start `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$WebPort`, with its stderr redirected to `$Data\.cadswarm-team\tunnel.log`
   - poll that log (up to 60 s) for the regex `https://[a-z0-9-]+\.trycloudflare\.com`, and use the match as `$WebUrl`
   - check that `Invoke-WebRequest "$WebUrl/boards.json"` returns 200 (retry for 30 s)
5. `& cadswarm-host --data $Data init --mode maincloud --db $Db --web-url $WebUrl --python $Python --agent $Agent`, run with env `PATH` prefixed by the spacetime folder (init reads the CLI token). This rewrites host.json each run, which is fine.
6. Start `cadswarm-host --data $Data run` hidden, with stdout and stderr to `$Data\.cadswarm-team\host.log`. Wait up to 90 s until `$Data\.cadswarm-team\host-status.json` exists with `"ready": true` and a `pid` that is alive.
7. `& cadswarm-host --data $Data invite --kind human --role lead`, capture the `phone:` line, and print it in a box with the instruction "Open on your phone (single-use)". The `invite` command prints a QR code too: let it through.
8. Write `$Data\.cadswarm-team\demo-pids.json` with `{ "preview": <pid>, "tunnel": <pid>, "host": <pid> }`.

`scripts/demo-down.ps1 -Data <same default>` reads `demo-pids.json`. For each pid, it stops the process only if its command line (via `Get-CimInstance Win32_Process`) contains `vite`, `cloudflared` or `cadswarm-host` respectively, then deletes the file. It never kills by name.

- [ ] DeepSeek drafts both scripts from this spec (`swarm_delegate`, paste the whole section). The director reviews them line by line for the stop-by-PID rule and the native-call wrapper.
- [ ] Add `tests/test_demo_scripts.py`, which skips unless Windows and `powershell.exe` exist. It runs `powershell -NoProfile -File scripts/demo-down.ps1 -Data <tmp>` against a `demo-pids.json` naming a `python -c "import time; time.sleep(60)"` process, asserts that process **survives** (its command line has none of the names), and asserts the file is removed.
- [ ] Manual check by the director: `scripts/demo-down.ps1` (stops the hand-started stack only if it was started by the script; otherwise stop the old stack's PIDs by hand after confirming each command line), then `scripts/demo-up.ps1`. Expected: a phone link is printed; opening it pairs; the 3D view shows the Box from M1.
- [ ] Document both scripts in README "Run the demo" (T7a).

**Acceptance:** one command brings up a working stack from a cold laptop; `demo-down` leaves unrelated processes alone.

---

### T5: M2 rehearsal: a brief from the phone ends `ready` with an STL (people run this)

**Owner:** a person at the laptop, with the director watching logs. This step runs real agents through
the host's brief orchestration. That is the product working as designed: the host launches the agent CLI
the team brings. Agents run on the team's own account.

Preconditions:
- T1 to T4 are merged, and the stack is up via `scripts/demo-up.ps1 -Agent claude`.
- To keep cost down, set host.json `agent_command` to run Sonnet (edit `$Data\host.json`, then restart with `demo-down` / `demo-up -SkipBuild`). demo-up's `init` resets `agent_command` to empty, so add a `-AgentCommand` passthrough to `demo-up.ps1` in T4 if this is used often:
```json
"agent_command": ["<full path to claude.exe>", "-p", "{prompt}", "--model", "sonnet", "--max-turns", "80",
                  "--allowedTools", "mcp__cadswarm", "--mcp-config", ".mcp.json"]
```
  `.mcp.json` resolves against the agent folder, which is the process working directory.
- Optional: Elastic env vars are set in the secrets file (see ELASTIC.md), so unknown boards are looked up.
- Optional: `cadswarm-host --data $Data init-reviewer` was run once, with `droid` and `FACTORY_API_KEY` present, for the review round.

Steps:
- [ ] 1. On the phone, pick board `esp32-s3-devkitc-1` and send: "A case for my ESP32-S3 DevKitC with a USB-C cutout, four standoffs and a snap-on lid".
- [ ] 2. Watch `$Data\.cadswarm-team\host.log`. Expect, in order:
  - seed `set_param` ops
  - `starting planner-<id>`
  - the planner's `create_task` calls (in `$Data\agents\planner-<id>\agent.log`)
  - up to three `starting worker…` lines
  - ops running
  - (reviewer round if enabled)
  - the export
  - brief `ready`
- [ ] 3. On the phone: parts appear with lock tints and agent pointers; then "Download STL" saves a file that opens in a slicer.
- [ ] 4. Change `wall` in Dimensions on the phone. Expected: parts update, and Activity shows the param change.
- [ ] 5. Record the timings (brief to ready) and every failure in `docs/m2-rehearsal.md` (DeepSeek drafts it from the director's notes).

If a step fails, the director files it as a bug task in this plan (T5-fix-N) with the log lines, and DeepSeek fixes it under the Execution protocol. Rerun until one clean pass. **This clean pass is also the take for the demo video (T7c).**

**Acceptance:** one brief reaches `ready` with a downloadable STL on Maincloud, driven from a phone on cellular data.

---

### T6: Merge the teammates' branches

**Owner:** director, with each branch's author. One PR per branch, merged in this order, each rebased or merged onto the latest main first and tested with the full suites.

1. **`feature/structural-optimization`** (1 commit ahead: persistent optimization workbench and desktop demo). PR it, run the suites (the structural tests need FreeCAD; `tests/structural_freecad_check.py` already prefers the checkout), and merge.
2. **`feat/fea-simulation`** (Anegs, 44 ahead; FEA simulations, "Give the team work from FreeCAD; a larger team panel").
   - Ask Anegs to merge `origin/main` into it after T1 and to fix any conflicts in `cadswarm/gui/panel.py` and `cadswarm/gui/wizard.py`, which T1 also changes.
   - The director reviews for three things:
     - (a) no use of the deleted `cadswarm.hub` or `HubClient`; the panel talks through `SpaceHub`
     - (b) any new op kinds are in `rules::QUERIES` / `MUTATING` / `FILE_WRITES`, or the module refuses them
     - (c) large results (FEA reports, meshes) either go through the op-result blob spill (> 32 KB) or are saved as project files, never in `args_json`
   - Merge once green.
3. **`UI-change-dash`** (Sachchit, 3 ahead, last commit "Describe your changes"). Ask Sachchit what it contains and whether it supersedes `feat/fea-simulation`'s web changes. Merge or close accordingly.

**Acceptance:** main contains every branch the team wants shipped; all suites are green; open PR list is empty or explained.

---

### T7: Submission

**Owner:** director (docs via DeepSeek); people for the video, the print and the booths.

#### T7a: README is the front door
- [ ] On main after T1, check `README.md` with a DeepSeek audit (`swarm_delegate`, agent mode, root = a main worktree). Prompt: "List every command or claim in README.md that does not match the code: cli.py subcommands, cadswarm-host subcommands and flags in host/src/main.rs, scripts/, ELASTIC.md, demo/README.md. Quote line numbers."
- [ ] Fix every finding with `swarm_edit_files`. The README must contain:
  - the one-paragraph pitch
  - an architecture diagram (module, host, helper, agents, phone)
  - "Run the demo" (T4 scripts)
  - "Bring your own agent" (`--agent`, `agent_command`)
  - "Where Droid runs" (Factory's eligibility rule; plan 03 Task 9 Step 6 text)
  - Elastic as core, linking ELASTIC.md
  - known limits (single FreeCAD worker per host; Maincloud vs local; quick tunnel URL changes)
  - the test counts taken from the latest run
- [ ] Also fix `ELASTIC.md` line 3, which calls Elastic "Optional", to say Elastic is core and the app degrades gracefully without keys. Fix its ingest command, which points at `..\cadswarm-guide\scripts\with-env.ps1`, to point at `scripts\with-env.ps1` in this repo.

#### T7b: Devpost packet
- [ ] `docs/devpost-submission.md` on branch `docs/spacetime-design`: update the numbers (tests, boards indexed, M2 timing from T5) and the links (release, repo, Pages or tunnel URL). Each track write-up must name the exact evidence a judge can check:

  | Track | What the judge checks |
  |---|---|
  | Spacetime | The module and views; that the whole world state lives in SpacetimeDB; the Maincloud db name |
  | Anthropic | Claude Code agents through MCP; the planner/worker prompts |
  | Factory | Droid reviewer, plus "Where Droid runs" |
  | Elastic | JINA hybrid search, Agent Builder agent, Workflow |
  | Best Hardware | The printed part |
  | Most Technically Complex | Fences, locks, handoff, spill, sandbox |

- [ ] Follow the existing pre-submission checklist (commit `562d54c`) item by item. Tick each item in the file with evidence (a link or a command output).

#### T7c: Demo video (people)
- [ ] Use the script in `docs/devpost-submission.md` (2:45 beats). Record T5's clean pass with phone-screen capture plus the desktop (FreeCAD and host log). Keep it under 3:00. Upload it unlisted to YouTube and paste the link into Devpost.

#### T7d: Print a part at the venue (people)
- [ ] From T5's STL, slice it (0.2 mm layer, PLA, 15% infill, no supports if the lid prints upside down), print the lid and base, and test-fit the real board. Bring it to judging. Photograph it for Devpost.

#### T7e: Booth confirmations (people), then update the plan
- [ ] Elastic: does the prize require agents to reach `cadswarm-board-expert` over Agent Builder's MCP endpoint? If yes, add the Agent Builder MCP server to the agent config that `cadswarm setup` writes (follow-up task T7e-1).
- [ ] Creao: does a Creao-built voice pipeline count as core work? This gates T9.
- [ ] Spacetime / Factory: confirm the eligibility rules match the README sections.

**Acceptance:** Devpost submitted by Sun 08:30 PT with video, repo link, track write-ups and photos.

---

### T8 (P2, only if time): Local-mode gateway behind one quick tunnel

**Why:** Maincloud covers the demo. Local mode (free, self-hosted) needs strangers kept away from the
node's admin routes, and phones need HTTPS/WSS. iroh and `cadswarm-host tunnel` stay out of scope.

**Design (std only, no async runtime, no new crates):** a new subcommand `cadswarm-host gateway`,
started by `run` when `cfg.mode == "local"`, listening on `127.0.0.1:<gateway_port>` (new HostConfig
field `gateway_port`, default `4180`, `#[serde(default)]`). One Cloudflare quick tunnel points at it.
It does two jobs on one origin, so the ticket `uri` and the web app share one HTTPS URL:
- **`/v1/...`**: an allowlisted reverse proxy to SpacetimeDB at `127.0.0.1:3000`
- **everything else**: static files from `web/dist` (the built app), with `index.html` for `/`

New file `host/src/gateway.rs`:
```rust
//! Local-mode gateway: one origin for the web app and an allowlisted SpacetimeDB API (plan 06 T8).

/// May this request reach SpacetimeDB? Spec "The host gateway": only this database's subscribe,
/// call and sql routes, plus identity routes. Publishing, deleting, logs and other databases are refused.
pub fn allowed(method: &str, path: &str, db: &str) -> bool {
    let path = path.split('?').next().unwrap_or(path);
    let base = format!("/v1/database/{db}");
    match method {
        "GET" => path == format!("{base}/subscribe"),
        "POST" => {
            path == format!("{base}/sql")
                || path.strip_prefix(&format!("{base}/call/")).is_some_and(|r| !r.is_empty() && !r.contains('/'))
                || path == "/v1/identity"
                || path.starts_with("/v1/identity/")
        }
        _ => false,
    }
}

/// Force one request per upstream connection, so a keep-alive connection can't smuggle a second,
/// unchecked request. WebSocket upgrades keep their `Connection: Upgrade`.
pub fn rewrite_head(head: &str) -> String { /* replace or insert `Connection: close` unless
   the request carries `Upgrade: websocket`; keep every other header line byte-for-byte */ }

/// Per-client token bucket keyed by `CF-Connecting-IP` (the tunnel's client address), 20 req/s, burst 40.
pub struct RateLimit { /* HashMap<String, (f64 tokens, Instant)> */ }
```
Proxy loop (`pub fn serve(cfg: &HostConfig) -> Result<()>`), using `std::net::TcpListener` and one thread per connection:
1. Read the request head (up to `\r\n\r\n`, max 16 KB; reply 431 if larger), and parse the method and path.
2. If the path starts with `/v1/`:
   - If `!allowed(..)`, reply `403 Forbidden` with body `not allowed through the CadSwarm gateway`.
   - If the rate limit is exceeded, reply 429.
   - Otherwise connect to `127.0.0.1:3000`, write `rewrite_head(head)` and any body bytes already read, then pipe both directions with two threads (`std::io::copy` on `try_clone`d streams) until either side closes. This handles WebSocket upgrades transparently.
3. Otherwise, serve a file from `<repo>/web/dist` (path from `HostConfig` or env `CADSWARM_WEB_DIST`). Reject `..` segments. Set `Content-Type` by extension (`.html .js .css .svg .json .png .wasm`). Fall back to `index.html` for `/`.

Tests (`#[cfg(test)]` in gateway.rs, written first):
```rust
#[test]
fn only_this_databases_client_routes_pass() {
    assert!(allowed("GET", "/v1/database/cadswarm/subscribe", "cadswarm"));
    assert!(allowed("POST", "/v1/database/cadswarm/call/lock_objects", "cadswarm"));
    assert!(allowed("POST", "/v1/database/cadswarm/sql", "cadswarm"));
    assert!(allowed("POST", "/v1/identity", "cadswarm"));
    assert!(allowed("POST", "/v1/identity/websocket-token", "cadswarm"));
    assert!(!allowed("POST", "/v1/database/cadswarm", "cadswarm"));            // publish
    assert!(!allowed("DELETE", "/v1/database/cadswarm", "cadswarm"));
    assert!(!allowed("GET", "/v1/database/cadswarm/logs", "cadswarm"));
    assert!(!allowed("POST", "/v1/database/other/sql", "cadswarm"));
    assert!(!allowed("POST", "/v1/database/cadswarm/call/a/b", "cadswarm"));
}
#[test]
fn keep_alive_requests_are_closed_but_upgrades_kept() { /* rewrite_head cases */ }
#[test]
fn the_rate_limit_refuses_bursts_per_client() { /* 41st immediate call from one ip refused; other ip fine */ }
```
Wiring:
- `host/src/main.rs` adds `Cmd::Gateway`, and `run` spawns `gateway::serve` on a thread when `cfg.mode == "local"`.
- `scripts/demo-up.ps1` gains `-Mode local`: start `spacetime start --listen-addr 127.0.0.1:3000` if nothing answers there; publish with `scripts/publish-local.ps1` **without** delete; `init --mode local`; tunnel to the **gateway port** instead of the preview port; and `-WebUrl` = the tunnel URL.
- `init` in local mode must put the **tunnel URL** in tickets as `uri` (new optional `--public-uri` flag stored in host.json as `public_uri`; `mint_invite` uses `public_uri` when set, else `uri`).

Acceptance: with `-Mode local`, a phone on cellular pairs through the one tunnel URL and sees the model; `curl -X POST <tunnel>/v1/database/cadswarm` returns 403.

---

### T9 (stretch): Voice with Creao + Cartesia

Gate: Creao confirms at the booth (T7e), T1–T7 are done, and T8 is done or explicitly skipped.

Contract our side provides, so the Creao-built pipeline only has to wire voice to it:
- **Where it runs:** in the phone web app, a "Talk" button on the Build tab.
- **Session:** the web app opens a Cartesia Managed Agent session with a short-lived access token (≤ 3600 s, grants `{agent: true}`). The token is minted on the **host machine**, where the Cartesia key lives, by a new `cadswarm-host voice-token` command. demo-up appends it to the phone link as `#vt=<token>`, kept only in memory, never stored.
- **Client tools** (`client_tool_call` → the web app runs them through `api.ts` → `client_tool_result`):

  | Tool | Arguments | Effect |
  |---|---|---|
  | `submit_brief` | `{text, board}` | `api.submitBrief` |
  | `brief_status` | `{}` | latest brief status, tasks done/total, last 3 activity lines |
  | `read_param` | `{alias}` | from the store |
  | `set_param` | `{alias, value}` | `api.setParam` |
  | `steer` | `{to, text}` | `send_message` with kind `steer` |
  | `hand_off` | `{note}` | `api.requestHandoff(note, 'now')` |

- **Spoken reads:** the agent reads measurements from `read_param` / `brief_status`. Speech can't be injected into a call, so all speech is the agent's reply to a tool result.

The Creao agent builds the UI and pipeline against this contract. The director reviews the PR like any other.

---

## Self-review checklist (director, before starting)

- Every task names its branch, files, exact changes, tests and acceptance.
- No task needs a decision that isn't written here, except T6's questions to teammates and T7e's booth answers. Those are listed as questions for people.
- Secrets never leave the host machine; every script resolves tools by path and stops processes by PID only.
