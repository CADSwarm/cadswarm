//! Headless agents the host spawns for a brief, using the agent CLI the host owner brings. Each gets its own folder and
//! cadswarm identity (via `cadswarm setup --invite`), so its actions are attributed to it.

use crate::config::HostConfig;
use anyhow::{anyhow, bail, Context, Result};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

pub const AGENT_TIMEOUT: Duration = Duration::from_secs(15 * 60);

pub struct Agent {
    pub name: String,
    child: Child,
    started: Instant,
}

/// Where `exe` might be: $CADSWARM_{EXE}, then PATH, then the usual install folders, then (for
/// `claude` only) the binary bundled with the VS Code extension (newest version first).
pub fn cli_candidates(exe: &str, env: Option<String>, path_var: &str, home: &Path, appdata: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = env.into_iter().map(PathBuf::from).collect();
    let names: Vec<String> = if cfg!(windows) { vec![format!("{exe}.exe"), format!("{exe}.cmd")] } else { vec![exe.to_string()] };
    for dir in std::env::split_paths(path_var) {
        out.extend(names.iter().map(|n| dir.join(n)));
    }
    out.extend(names.iter().map(|n| home.join(".local").join("bin").join(n)));
    if !cfg!(windows) {
        for dir in [home.join(".claude").join("local"), home.join(".npm-global").join("bin"),
                    home.join(".volta").join("bin"), home.join(".bun").join("bin"),
                    PathBuf::from("/opt/homebrew/bin"), PathBuf::from("/usr/local/bin"), PathBuf::from("/usr/bin")] {
            out.extend(names.iter().map(|n| dir.join(n)));
        }
    }
    if cfg!(windows) && !appdata.as_os_str().is_empty() {
        out.push(appdata.join("npm").join(format!("{exe}.cmd")));
    }
    if exe == "opencode" {
        out.extend(names.iter().map(|n| home.join(".opencode").join("bin").join(n)));
        // The desktop app bundles a CLI under a different name, outside PATH.
        if let Some(local) = appdata.parent() {
            out.push(local.join("Local").join("Programs").join("@opencodedesktop").join("resources").join("opencode-cli.exe"));
        }
    }
    if exe == "claude" {
        let ext = home.join(".vscode").join("extensions");
        let mut bundled: Vec<PathBuf> = std::fs::read_dir(&ext)
            .map(|rd| {
                rd.flatten()
                    .map(|e| e.path())
                    .filter(|p| p.file_name().and_then(|n| n.to_str()).is_some_and(|n| n.starts_with("anthropic.claude-code-")))
                    .collect()
            })
            .unwrap_or_default();
        // Version-aware order: compare the dotted numbers, not the strings ("2.1.306" > "2.1.31").
        bundled.sort_by_key(|p| {
            let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("").to_string();
            name.trim_start_matches("anthropic.claude-code-")
                .split(['.', '-'])
                .map(|part| part.parse::<u64>().unwrap_or(0))
                .collect::<Vec<u64>>()
        });
        for dir in bundled.into_iter().rev() {
            out.extend(names.iter().map(|n| dir.join("resources").join("native-binary").join(n)));
        }
    }
    out
}

/// A file we can start: on Unix it must also have an execute bit (a stray non-executable copy
/// would otherwise win and fail with "permission denied").
fn is_runnable(p: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        p.metadata().map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0).unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        p.is_file()
    }
}

pub fn find_cli(exe: &str) -> Result<PathBuf> {
    let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")).map(PathBuf::from).unwrap_or_default();
    let appdata = std::env::var_os("APPDATA").map(PathBuf::from).unwrap_or_default();
    let path_var = std::env::var("PATH").unwrap_or_default();
    let env = std::env::var(format!("CADSWARM_{}", exe.to_uppercase())).ok();
    cli_candidates(exe, env, &path_var, &home, &appdata)
        .into_iter()
        .find(|p| is_runnable(p))
        .ok_or_else(|| anyhow!(format!("{exe} isn't installed (set CADSWARM_{} to its path)", exe.to_uppercase())))
}

pub fn agent_command(client: &str, override_cmd: &[String], dir: &Path, prompt: &str) -> Result<(String, Vec<String>)> {
    if let Some((program, rest)) = override_cmd.split_first() {
        let args = rest.iter().map(|a| if a == "{prompt}" { prompt.to_string() } else { a.clone() }).collect();
        return Ok((program.clone(), args));
    }
    let p = prompt.to_string();
    Ok(match client {
        // Headless runs don't prompt to trust the folder's .mcp.json, so name it explicitly.
        "claude" => ("claude".into(), vec!["-p".into(), p, "--max-turns".into(), "80".into(), "--allowedTools".into(),
                     "mcp__cadswarm".into(), "--mcp-config".into(), dir.join(".mcp.json").display().to_string()]),
        // Codex dropped --full-auto (it exits 2 with "unexpected argument"); -s workspace-write is the
        // sandbox that alias meant, and every version from 0.20 on takes it.
        "codex" => ("codex".into(), vec!["exec".into(), "--sandbox".into(), "workspace-write".into(),
                                          "--skip-git-repo-check".into(), p]),
        // --standalone: a private server per run, not whatever the person's desktop app is running.
        // The prompt goes in on stdin (see spawn): an npm .cmd shim would split a multi-line argument.
        "opencode" => ("opencode".into(), vec!["run".into(), "--standalone".into(), "--auto".into()]),
        other => bail!("unknown agent client {other:?}; use claude, codex or opencode, or set agent_command"),
    })
}

/// Find the CLI this host would run, before an invite is minted for an agent that cannot start.
pub fn preflight(cfg: &HostConfig) -> Result<()> {
    if !cfg.agent_command.is_empty() {
        if cfg.agent_command[0].trim().is_empty() {
            bail!("agent_command starts with an empty program name");
        }
        return Ok(());
    }
    let (program, _) = agent_command(&cfg.agent_client, &[], Path::new("."), "")?;
    find_cli(&program).map(|_| ())
}

/// The end of an agent's own log, for a failure message a person can act on.
pub fn log_tail(cfg: &HostConfig, name: &str, lines: usize) -> String {
    let path = cfg.data_dir.join("agents").join(name).join("agent.log");
    let Ok(text) = std::fs::read_to_string(&path) else { return String::new() };
    let tail: Vec<String> = text.lines().filter(|l| !l.trim().is_empty())
        .rev().take(lines).collect::<Vec<_>>().into_iter().rev()
        .map(|l| l.trim().chars().take(300).collect()).collect();
    if tail.is_empty() {
        String::new()
    } else {
        format!("\n\nIts log ended with:\n{}", tail.join("\n"))
    }
}

/// Where one start of an agent keeps its identity, config and log.
pub fn run_dir(cfg: &HostConfig, name: &str) -> PathBuf {
    cfg.data_dir.join("agents").join(name)
}

/// The agent folders on disk, so a host restart can find the leftovers of a brief it no longer holds in
/// memory (its runtimes are gone, the folders and member rows are not).
pub fn run_dirs(cfg: &HostConfig) -> Vec<String> {
    let Ok(rd) = std::fs::read_dir(cfg.data_dir.join("agents")) else { return Vec::new() };
    rd.flatten().filter(|e| e.path().is_dir())
        .filter_map(|e| e.file_name().into_string().ok())
        .collect()
}

/// Delete one finished agent's folder (its log, its sign-in). The host writes it again on the next start.
pub fn remove_run_dir(cfg: &HostConfig, name: &str) {
    let dir = run_dir(cfg, name);
    if let Err(e) = std::fs::remove_dir_all(&dir) {
        if e.kind() != std::io::ErrorKind::NotFound {
            eprintln!("[host] could not remove {}: {e}", dir.display());
        }
    }
}

/// Make the next start of this agent a fresh member. Its old identity was taken off the team, and
/// `rejoin` refuses a removed identity; the invite the next start gets is single-use, so setup must
/// actually redeem it. The log and config stay, for the failure message and the pinned model.
pub fn forget_identity(cfg: &HostConfig, name: &str) {
    let path = run_dir(cfg, name).join(".cadswarm").join("identity.json");
    if let Err(e) = std::fs::remove_file(&path) {
        if e.kind() != std::io::ErrorKind::NotFound {
            eprintln!("[host] could not clear {}: {e}", path.display());
        }
    }
}

impl Agent {
    /// `ticket` is an agent invite minted by the host.
    pub fn spawn(cfg: &HostConfig, name: &str, ticket: &str, prompt: &str) -> Result<Agent> {
        let dir: PathBuf = cfg.data_dir.join("agents").join(name);
        std::fs::create_dir_all(&dir)?;
        // PYTHONSAFEPATH, not -I: -I would also drop PYTHONPATH, which is how a guide-hosted team
        // (FreeCAD's own Python) finds the package; both keep a checkout in the cwd from being imported.
        let run_setup = |ephemeral: bool| {
            let mut args = vec!["-m", "cadswarm.cli", "setup"];
            if ephemeral {
                args.push("--ephemeral");
            }
            args.extend(["--client", cfg.agent_client.as_str(), "--invite", ticket, "--name", name, "--dir"]);
            Command::new(&cfg.cadswarm_python)
                .env("PYTHONSAFEPATH", "1")
                .args(&args)
                .arg(&dir)
                .output()
                .context("running cadswarm setup")
        };
        let mut setup = run_setup(true)?;
        // A CadSwarm package older than --ephemeral rejects the flag before redeeming the invite, so the
        // ticket is still unused: join without it (the agent then stays listed after its job).
        if !setup.status.success() && String::from_utf8_lossy(&setup.stderr).contains("unrecognized arguments: --ephemeral") {
            setup = run_setup(false)?;
        }
        if !setup.status.success() {
            bail!(
                "cadswarm setup for {name} failed: {}{}",
                String::from_utf8_lossy(&setup.stderr),
                String::from_utf8_lossy(&setup.stdout)
            );
        }
        let log = std::fs::File::create(dir.join("agent.log"))?;
        let (program, args) = agent_command(&cfg.agent_client, &cfg.agent_command, &dir, prompt)?;
        let exe = if cfg.agent_command.is_empty() { find_cli(&program)? } else { PathBuf::from(&program) };
        let mut cmd = Command::new(exe);
        cmd.args(&args)
            .current_dir(&dir)
            .stdin(if cfg.agent_client == "opencode" && cfg.agent_command.is_empty() {
                Stdio::piped()
            } else {
                Stdio::null()
            })
            .stdout(log.try_clone()?)
            .stderr(log);
        let mut child = cmd.spawn().with_context(|| format!("starting {program} for {name}"))?;
        if let Some(mut stdin) = child.stdin.take() {
            stdin.write_all(prompt.as_bytes()).context("sending the prompt to the agent")?;
        }
        Ok(Agent { name: name.into(), child, started: Instant::now() })
    }

    /// False once it exited or was killed for running past the timeout.
    pub fn alive(&mut self) -> bool {
        if self.started.elapsed() > AGENT_TIMEOUT {
            self.kill();
            return false;
        }
        matches!(self.child.try_wait(), Ok(None))
    }

    /// Stop it and everything it started. On Windows the MCP server is a grandchild process: killing
    /// only the CLI would leave it holding the helper's seat in the team.
    pub fn kill(&mut self) {
        if cfg!(windows) {
            let _ = Command::new("taskkill")
                .args(["/PID", &self.child.id().to_string(), "/T", "/F"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// The closed op vocabulary a plan's `ops` and the narrated op feed use (cadswarm/step_plan.py OP_VOCABULARY).
pub const OP_VOCABULARY: &str = "Create sketch, Extrude, Cut, Revolve, Fillet, Chamfer, Hole, Pattern, Mirror, Shell, \
    Insert component, Place standard part, Mate, Set color, Check interference, Check printability, Render, Save part";

/// The planner writes no geometry: it turns the brief into a numbered, dependency-ordered step plan
/// (plan_json version 2), one task per step, the way an engineer would sequence the build.
pub fn planner_prompt(brief_id: &str, text: &str, board: &str, params: &[(String, String)]) -> String {
    let list: Vec<String> = params.iter().map(|(k, v)| format!("{k} = {v}")).collect();
    let has_board = !params.is_empty() || !(board.is_empty() || board == "none given");
    let board_block = if has_board {
        format!(
            "Board: {board}. The shared Params sheet already holds its measured dimensions:\n{}\n\
             Where the design houses or mounts this board, build around those Params: the inner size clears the \
             board by the team's fits (e.g. inner length = pcb_l + 2*gap), a standoff or boss sits at each mounting \
             hole hole_N_x/hole_N_y (height standoff_h, bore hole_d), each port cutout is centred on its <port>_pos \
             and <port>_z, and a lid or cover keeps a clearance gap. The board itself is a library step \
             (insert_component), never modelled. Do not turn the design into an enclosure unless the person asked \
             for one.\n",
            if list.is_empty() { "(no Params seeded)".to_string() } else { list.join("\n") }
        )
    } else {
        "Board: none given. Do not assume an enclosure: design exactly what the person asked for.\n".to_string()
    };
    format!(
        "You are the PLANNER for CadSwarm brief {brief_id}.\n\
         The person asked: \"{text}\"\n\
         {board_block}\n\
         Plan the build as a numbered, dependency-ordered list of steps, the way a mechanical engineer sequences \
         a build. Do NOT build any geometry yourself.\n\
         RULES FOR STEPS\n\
         1. One step = ONE component, ONE library placement, ONE assembly action, or ONE check. Never two parts in \
            one step. kind is \"part\" (modelled), \"library\" (bought or standard), \"assembly\" (placing and mating \
            already-built components) or \"check\" (interference, printability).\n\
         2. Every step has a one-sentence engineering summary that names the component, its function and its key \
            features, e.g. \"The pitch link: a turnbuckle rod with a spherical rod end at each end; it pushes the \
            horn as the swashplate tilts.\"\n\
         3. Order like a machinist and assembler would: the fixed base or frame (the grounded part) first, then each \
            part after the part it attaches to (along the load path). A library step comes right after the part \
            that hosts it (holes exist before their screws). An assembly step comes after the components it \
            places, one per sub-group. A final check step comes last. Every depends_on names EARLIER steps only, \
            so the numbers n are a valid build order; independent steps share no dependency and can run in parallel.\n\
         4. Library first: every screw, nut, washer, insert, bearing, pin, magnet, board, motor or other bought part \
            is a library step. Call recommend_components (or search_components) for it and put the chosen library \
            id, or {{\"family\", \"size\", \"length\"}} of a standard part, in the step's `component`. Never plan to \
            model one.\n\
         5. Shared sizes go in Params (lock Params, set_param, release it) and every detail states geometry in \
            Params names. Gaps use the team's fits, which are Params too (tol_clearance, tol_slip_fit, tol_press_fit).\n\
         6. Optional but valuable: `params` (the step's key dimensions in mm), `targets` ({{\"bbox_mm\": [x, y, z], \
            \"tol_mm\": 0.5, \"holes\": 4, \"min_wall_mm\": 1.2}}), `ports` (named interfaces the part exposes for \
            mating, e.g. {{\"name\": \"mast_axis\", \"type\": \"axis\"}}). Assembly steps list `mates` over ports: \
            [{{\"type\": \"fixed\", \"a\": \"Base\"}}, {{\"type\": \"concentric\", \"a\": \"Base.mast_bore\", \"b\": \"Mast.mast_axis\"}}], \
            types fixed, coincident, concentric, distance, angle, parallel, revolute, slider (distance and angle \
            need a `value`). Exactly one part is fixed in the whole plan and the mates form a tree (no loops).\n\
         7. `ops` lists the CAD operations the step will run, each starting with one of: {OP_VOCABULARY} \
            (e.g. \"Create sketch SK_Mast\", \"Revolve Mast\", \"Save part\"). Parts have no colour property in the \
            shared model (every client colours a part by who holds it), so plan no \"Set color\" op.\n\
         WHAT TO CALL\n\
         a. For each step, in order, call create_task with title \"<n>. <name>\" (e.g. \"3. Pitch link\"), \
            brief = \"{brief_id}\", objects = the exact FreeCAD object names the step creates, and a detail with the \
            full build instructions: the sketch and features in order, every dimension bound to Params, \
            the library component and the hole it needs (hole_size), or for an assembly step which parts it places \
            and how. Pass assignee only if the person asked for a named teammate to build that step.\n\
         b. Then call set_brief_plan with brief_id \"{brief_id}\" and plan_json = \
            {{\"version\": 2, \"title\": \"<short design name>\", \"steps\": [{{\"n\": 1, \"id\": \"s01\", \"name\": ..., \
            \"kind\": ..., \"summary\": ..., \"task\": <the task id create_task returned>, \"objects\": [...], \
            \"depends_on\": [], \"ops\": [...], \"assignee\": \"\"}}, ...]}} (library steps add \"component\"). \
            If it answers bad_plan, fix every listed problem (create a missing task if needed) and call it again.\n\
         Stop after set_brief_plan succeeds."
    )
}

/// A worker builds one step at a time, in dependency order, from `next_step`.
pub fn worker_prompt(brief_id: &str, n: usize) -> String {
    format!(
        "You are WORKER {n} building CadSwarm brief {brief_id} step by step.\n\
         LOOP:\n\
         1. Call next_step(brief_id=\"{brief_id}\").\n\
            - {{\"done\": true}} or {{\"stuck\": true}}: stop.\n\
            - {{\"wait\": true}}: the remaining steps wait for steps others are building. Call wait_for_changes, \
              then next_step again.\n\
            - a step: claim_task its task (skip this if it says you already hold it). If the claim answers \
              not_ready or the task is taken, go back to 1.\n\
         2. lock_objects the step's objects. Read get_inbox and do any steer message first.\n\
         3. Build that ONE step completely before anything else, following its task detail and summary. \
            point_at the part as you go so the team sees where you are.\n\
            - kind \"part\": build it the way a CAD engineer would, one feature at a time: sketch or primitive, the \
              main feature (extrude, revolve, cut), then refinements (holes, fillets, patterns). Don't try to colour \
              it: the shared model has no colour property (clients colour parts by who holds them). \
              Bind every dimension to Params with expressions (e.g. \"Params.pcb_l + 2 * Params.gap\").\n\
            - kind \"library\": never model it. recommend_components or search_components for the step's \
              `component`, then insert_component or add_standard_part, placed where the detail says.\n\
            - kind \"assembly\": place the already-built components with set_placement and the constraint tools, \
              following the step's mates (the fixed part does not move). Run check_interference afterwards.\n\
            - kind \"check\": check_interference on all parts and check_printability; fix clashes in parts you hold, \
              message the owner of any other part.\n\
            - kind \"fix\": a reviewer's fix task: do exactly what its detail says.\n\
         4. Verify before you report: the part is a valid closed solid with volume above 0, its size is within the \
            step's targets (bbox_mm +- tol_mm, hole count, min wall) when it has any, its ports exist, and \
            check_interference against the parts already built (`built_objects` from next_step) shows no clash. \
            If verification fails, fix it, at most 3 attempts; from the second attempt try a different construction \
            instead of repeating the first. Still failing: report_result blocked with what fails. Never report a \
            failing step as done.\n\
         5. report_result done with evidence (object names, volumes, bounding boxes, the interference result), \
            release_objects for parts others need to mate against, then go back to 1."
    )
}

/// The reviewer is an ordinary spawned agent with a reviewer identity: the module lets it read and report
/// but refuses every write op from a `reviewer` member, so it cannot change the model it judges.
pub fn review_prompt(brief_id: &str, text: &str, board: &str, bed: [f64; 3], round: u32, style: &str) -> String {
    let style_check = if style.trim().is_empty() {
        String::new()
    } else {
        format!("\n5. Team design style: the rules below are how this team designs. Each part that breaks one is a \
                 problem; write its `problem` as \"style: <the rule> (<what the part does instead>)\".\n{style}\n")
    };
    format!(
        "You are the design REVIEWER for CadSwarm brief {brief_id} (review round {round}).\n\
         The person asked: \"{text}\". Board: {board}. Printer bed: {} x {} x {} mm.\n\
         build_progress(\"{brief_id}\") lists the numbered build steps; name a step by its number and name \
         (\"step 3, Pitch link\") in every finding.\n\
         Check, using the tools only (you cannot change geometry), step by step in build order:\n\
         0. Your arrow shows the team which part you are on: call point_at(\"<part>\") before you check it,\n\
            and point_at again when you move to the next one. It only moves your pointer, never the model.\n\
         1. model_status: read the Params sheet (the board's measured dimensions) and the parts.\n\
         2. check_interference on all parts: any clash between separate parts is a problem.\n\
         3. Fit: each step's part matches its summary and targets; library steps placed real library parts.\n\
            Where the design houses a board: the inner size must exceed pcb_l/pcb_w by at least 2*gap; standoffs\n\
            must sit at hole_N_x/hole_N_y; each port cutout must be centred on its <port>_pos and <port>_z.\n\
         4. check_printability: every part must fit the bed, be a closed solid, and have no wall under 1.2 mm.\n\
         {style_check}\
         For each problem: create_task titled \"Fix step <n>: <what>\", assigned to the part's owner (see locked_by\n\
         in model_status), with a precise fix, brief set to \"{brief_id}\", and send_message to that owner.\n\
         Finish by calling report_review(brief_id=\"{brief_id}\", round={round}, passed=<true only if no problems>,\n\
         findings=[{{\"step\": <n>, \"object\", \"problem\": \"step <n> (<name>): ...\", \"task_id\"}}]).",
        bed[0], bed[1], bed[2]
    )
}

/// The first line is `accept_handoff` on purpose: it hands over the locks and tasks atomically,
/// so the new agent owns the work before it reads anything else.
pub fn handoff_prompt(handoff_id: &str, context_json: &str) -> String {
    let ctx: serde_json::Value = serde_json::from_str(context_json).unwrap_or_default();
    let from = ctx["from"].as_str().unwrap_or("a teammate");
    let note = ctx["note"].as_str().filter(|n| !n.is_empty()).unwrap_or("(no note: infer it from their recent work)");
    format!(
        "You are picking up exactly where {from} left off in the shared CadSwarm FreeCAD model.\n\
         FIRST call accept_handoff(handoff_id=\"{handoff_id}\"): it gives you their locked parts and tasks.\n\
         Their note, which is your main instruction: {note}\n\
         Where they left off (JSON: what they were pointing at, the parts they hold, their in-progress tasks, \
         their last activity, ops and messages):\n{context_json}\n\n\
         Continue their work, do not start over: read model_status and get_object for the parts they hold, use their \
         recent ops to see what they were in the middle of, finish it binding dimensions to Params, run \
         check_interference, report_result on each of their tasks, and send_message to {from} with a short summary \
         of what you did. Stop when their tasks are done."
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_env_override_comes_first_and_the_newest_vscode_binary_before_older_ones() {
        let dir = std::env::temp_dir().join(format!("cadswarm-claude-test-{}", std::process::id()));
        let ext = dir.join(".vscode").join("extensions");
        std::fs::create_dir_all(ext.join("anthropic.claude-code-2.1.31-win32-x64")).unwrap();
        std::fs::create_dir_all(ext.join("anthropic.claude-code-2.1.306-win32-x64")).unwrap();
        let c = cli_candidates("claude", Some("X:/my/claude.exe".into()), "", &dir, Path::new("A:/appdata"));
        assert_eq!(c[0], PathBuf::from("X:/my/claude.exe"));
        let bundled: Vec<String> =
            c.iter().map(|p| p.display().to_string()).filter(|s| s.contains("native-binary")).collect();
        assert!(bundled[0].contains("2.1.306"), "newest extension first: {bundled:?}");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn each_client_gets_its_headless_command() {
        let dir = Path::new("D:/agents/w1");
        let (p, a) = agent_command("claude", &[], dir, "build it").unwrap();
        assert_eq!(p, "claude");
        assert!(a.contains(&"build it".to_string()) && a.contains(&"--mcp-config".to_string()));
        let (p, a) = agent_command("codex", &[], dir, "build it").unwrap();
        assert_eq!((p.as_str(), a[0].as_str(), a.last().unwrap().as_str()), ("codex", "exec", "build it"));
        // --full-auto was removed from the CLI: a fresh Codex answers it with exit 2, "unexpected argument".
        assert!(!a.contains(&"--full-auto".to_string()), "the removed flag would kill every agent: {a:?}");
        assert_eq!(&a[1..3], ["--sandbox", "workspace-write"], "{a:?}");
        let (p, a) = agent_command("opencode", &[], dir, "build it").unwrap();
        assert_eq!((p.as_str(), a[0].as_str(), a[1].as_str()), ("opencode", "run", "--standalone"));
        assert!(!a.contains(&"build it".to_string()), "the prompt goes in on stdin: {a:?}");
        assert!(agent_command("gpt", &[], dir, "x").is_err());
    }

    fn cfg(client: &str, command: Vec<String>, data: PathBuf) -> HostConfig {
        HostConfig {
            mode: "local".into(), uri: "http://127.0.0.1:3000".into(), db: "db".into(), token: "t".into(),
            data_dir: data, freecad_python: PathBuf::from("freecad"), worker_script: PathBuf::from("w.py"),
            web_url: String::new(), cadswarm_python: PathBuf::from("python"), agent_client: client.into(),
            agent_command: command,
        }
    }

    #[test]
    fn preflight_checks_the_command_before_a_start() {
        let dir = std::env::temp_dir().join(format!("cadswarm-preflight-{}", std::process::id()));
        // A custom command line is taken as given (a person's own wrapper script).
        assert!(preflight(&cfg("claude", vec!["my-wrapper".into(), "{prompt}".into()], dir.clone())).is_ok());
        // An empty program name or an unknown client can never start, so no invite should be spent.
        assert!(preflight(&cfg("claude", vec!["  ".into()], dir.clone())).is_err());
        assert!(preflight(&cfg("gpt", Vec::new(), dir)).is_err());
    }

    #[test]
    fn an_agents_log_tail_names_why_it_died() {
        let dir = std::env::temp_dir().join(format!("cadswarm-logtail-{}", std::process::id()));
        let agent = dir.join("agents").join("planner-1");
        std::fs::create_dir_all(&agent).unwrap();
        std::fs::write(agent.join("agent.log"), "first\n\nstarting\nworkspace has not been trusted\n").unwrap();
        let tail = log_tail(&cfg("claude", Vec::new(), dir.clone()), "planner-1", 2);
        assert!(tail.contains("trusted") && !tail.contains("first"), "{tail}");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_finished_agents_folder_is_deleted_and_the_others_stay() {
        // Every start of a one-job agent left a folder behind; a finished brief must not keep them.
        let dir = std::env::temp_dir().join(format!("cadswarm-runs-{}", std::process::id()));
        let c = cfg("opencode", Vec::new(), dir.clone());
        for name in ["worker1-f413f129", "worker2-f413f129"] {
            std::fs::create_dir_all(run_dir(&c, name)).unwrap();
            std::fs::write(run_dir(&c, name).join("agent.log"), "built the lid").unwrap();
        }
        assert_eq!(run_dirs(&c).len(), 2);
        remove_run_dir(&c, "worker1-f413f129");
        // Only that start is gone: the other keeps its log, and removing twice is not an error.
        assert_eq!(run_dirs(&c), vec!["worker2-f413f129".to_string()]);
        remove_run_dir(&c, "worker1-f413f129");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_retired_agent_forgets_its_sign_in_but_keeps_its_log() {
        // The next start must redeem its fresh invite: `rejoin` refuses an identity that was taken off
        // the team, and the invite is single-use. The log stays for the brief's failure message.
        let dir = std::env::temp_dir().join(format!("cadswarm-forget-{}", std::process::id()));
        let c = cfg("opencode", Vec::new(), dir.clone());
        let state = run_dir(&c, "planner-abc").join(".cadswarm");
        std::fs::create_dir_all(&state).unwrap();
        std::fs::write(state.join("identity.json"), "{\"token\": \"t\"}").unwrap();
        std::fs::write(run_dir(&c, "planner-abc").join("agent.log"), "workspace has not been trusted").unwrap();
        forget_identity(&c, "planner-abc");
        assert!(!state.join("identity.json").exists());
        assert!(run_dir(&c, "planner-abc").join("agent.log").exists());
        forget_identity(&c, "planner-abc");  // already gone: not an error
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn the_planner_writes_a_numbered_step_plan_with_one_task_per_step() {
        let p = planner_prompt("b_1", "a four-blade rotor head", "none given", &[]);
        assert!(p.contains("\"version\": 2") && p.contains("set_brief_plan") && p.contains("depends_on"));
        assert!(p.contains("ONE component") && p.contains("\"<n>. <name>\"") && p.contains("brief = \"b_1\""));
        assert!(p.contains("recommend_components") && p.contains("`component`") && p.contains("library"));
        assert!(p.contains("fixed base or frame") && p.contains("final check step"));
        assert!(p.contains("pitch link") && p.contains("bad_plan") && p.contains("Create sketch"));
        // Not every brief is an enclosure: without a board there is no board guidance.
        assert!(p.contains("Do not assume an enclosure") && !p.contains("hole_N_x"));
        assert!(!p.contains("{{") && !p.contains("}}"), "format braces must be unescaped in the output");
        let params = vec![("pcb_l".to_string(), "69".to_string()), ("hole_1_x".to_string(), "3.5".to_string())];
        let b = planner_prompt("b_2", "a case", "ESP32-S3 DevKitC-1", &params);
        assert!(b.contains("pcb_l = 69") && b.contains("hole_N_x") && b.contains("tol_clearance"));
        assert!(b.contains("ESP32-S3") && b.contains("insert_component"));
    }

    #[test]
    fn the_worker_loops_on_next_step_and_builds_one_step_completely() {
        let p = worker_prompt("b_1", 2);
        assert!(p.contains("WORKER 2") && p.contains("next_step(brief_id=\"b_1\")"));
        let order = ["next_step", "claim_task", "lock_objects", "Build that ONE step", "check_interference", "report_result"];
        let at: Vec<usize> = order.iter().map(|w| p.find(w).unwrap_or_else(|| panic!("missing {w}"))).collect();
        assert!(at.windows(2).all(|w| w[0] < w[1]), "the loop is in order: {at:?}");
        assert!(p.contains("wait_for_changes") && p.contains("\"done\": true") && p.contains("not_ready"));
        assert!(p.contains("add_standard_part") && p.contains("set_placement") && p.contains("Params."));
        assert!(p.contains("3 attempts") && p.contains("different construction") && p.contains("blocked"));
        assert!(!p.contains("list_tasks"), "workers no longer pick any open task");
    }

    #[test]
    fn the_reviewer_prompt_checks_fit_and_reports_a_verdict() {
        let p = review_prompt("b_x", "a case for my board", "esp32", [220.0, 220.0, 250.0], 2, "");
        assert!(p.contains("check_interference") && p.contains("check_printability"));
        assert!(p.contains("report_review") && p.contains("round=2") && p.contains("esp32"));
        assert!(p.contains("build_progress(\"b_x\")") && p.contains("Fix step <n>") && p.contains("\"step\": <n>"));
        assert!(!p.contains("Team design style"), "no style rules learned yet");
        let with_style = review_prompt("b_x", "a case", "esp32", [220.0, 220.0, 250.0], 1, "- walls are 2.4 mm");
        assert!(with_style.contains("walls are 2.4 mm"));
    }

    #[test]
    fn an_override_runs_any_cli_with_the_prompt_substituted() {
        let cmd = vec!["aider".to_string(), "--message".to_string(), "{prompt}".to_string(), "--yes".to_string()];
        let (p, a) = agent_command("claude", &cmd, Path::new("D:/x"), "make a lid").unwrap();
        assert_eq!(p, "aider");
        assert_eq!(a, vec!["--message", "make a lid", "--yes"]);
    }

    #[test]
    fn only_claude_looks_in_the_vscode_extension() {
        let c = cli_candidates("codex", None, "", Path::new("H:/home"), Path::new("A:/appdata"));
        assert!(c.iter().all(|p| !p.display().to_string().contains("native-binary")));
        assert!(c.iter().any(|p| p.display().to_string().contains("codex")));
    }

    #[test]
    fn opencode_also_looks_in_its_own_bin_and_the_desktop_app() {
        let c = cli_candidates("opencode", None, "", Path::new("H:/home"), Path::new("A:/AppData/Roaming"));
        let paths: Vec<String> = c.iter().map(|p| p.display().to_string().replace('\\', "/")).collect();
        assert!(paths.iter().any(|p| p.contains(".opencode") && p.contains("bin/opencode")));
        assert!(paths.iter().any(|p| p.contains("@opencodedesktop") && p.ends_with("opencode-cli.exe")));
    }

    #[test]
    fn the_handoff_prompt_leads_with_accepting_and_the_persons_note() {
        let ctx = r#"{"from":"carol","note":"make the lip 1 mm thinner","pointer":{"object":"Lid"},"locks":["Lid"],"tasks":[]}"#;
        let p = handoff_prompt("h_1", ctx);
        assert!(p.find("accept_handoff").unwrap() < p.find("make the lip 1 mm thinner").unwrap());
        assert!(p.contains("carol") && p.contains("Lid") && p.contains("h_1"));
    }
}
