//! Headless agents the host spawns for a brief, using the agent CLI the host owner brings. Each gets its own folder and
//! cadswarm identity (via `cadswarm setup --invite`), so its actions are attributed to it.

use crate::config::HostConfig;
use anyhow::{anyhow, bail, Context, Result};
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
        "codex" => ("codex".into(), vec!["exec".into(), "--full-auto".into(), "--skip-git-repo-check".into(), p]),
        "droid" => ("droid".into(), vec!["exec".into(), "--auto".into(), "low".into(), p]),
        other => bail!("unknown agent client {other:?}; use claude, codex or droid, or set agent_command"),
    })
}

impl Agent {
    /// `ticket` is an agent invite minted by the host.
    pub fn spawn(cfg: &HostConfig, name: &str, ticket: &str, prompt: &str) -> Result<Agent> {
        let dir: PathBuf = cfg.data_dir.join("agents").join(name);
        std::fs::create_dir_all(&dir)?;
        // PYTHONSAFEPATH, not -I: -I would also drop PYTHONPATH, which is how a guide-hosted team
        // (FreeCAD's own Python) finds the package; both keep a checkout in the cwd from being imported.
        let setup = Command::new(&cfg.cadswarm_python)
            .env("PYTHONSAFEPATH", "1")
            .args(["-m", "cadswarm.cli", "setup", "--ephemeral", "--client", cfg.agent_client.as_str(), "--invite", ticket, "--name", name, "--dir"])
            .arg(&dir)
            .output()
            .context("running cadswarm setup")?;
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
        let child = Command::new(exe)
            .args(&args)
            .current_dir(&dir)
            .stdin(Stdio::null())
            .stdout(log.try_clone()?)
            .stderr(log)
            .spawn()
            .with_context(|| format!("starting {program} for {name}"))?;
        Ok(Agent { name: name.into(), child, started: Instant::now() })
    }

    /// An already-spawned process (the reviewer runs `python -m cadswarm.reviewer`, not an agent CLI).
    pub fn from_child(name: &str, child: Child) -> Agent {
        Agent { name: name.into(), child, started: Instant::now() }
    }

    /// False once it exited or was killed for running past the timeout.
    pub fn alive(&mut self) -> bool {
        if self.started.elapsed() > AGENT_TIMEOUT {
            self.kill();
            return false;
        }
        matches!(self.child.try_wait(), Ok(None))
    }

    pub fn kill(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

pub fn planner_prompt(brief_id: &str, text: &str, board: &str, params: &[(String, String)]) -> String {
    let list: Vec<String> = params.iter().map(|(k, v)| format!("{k} = {v}")).collect();
    format!(
        "You are the PLANNER for CadSwarm brief {brief_id}.\n\
         The person asked: \"{text}\"\n\
         Board: {board}. The shared Params sheet already holds its measured dimensions:\n{params}\n\n\
         Plan the design as separate printable parts that different agents can build in parallel. \
         For an enclosure that is usually: Base (floor plus walls, with a cutout for each port), \
         Standoffs (one cylinder per mounting hole at hole_N_x/hole_N_y, height standoff_h, bore hole_d), \
         and Lid (top plate with a lip inside the walls, clearance gap). \
         For each part call create_task with a short title, objects = the exact FreeCAD object names it will create, \
         brief = \"{brief_id}\", and a detail that states the geometry in terms of Params names \
         (e.g. inner length = pcb_l + 2*gap). Add any extra Params you need with set_param first (lock Params, then release it). \
         Gaps use the team's fits, which are Params too (tol_clearance, tol_slip_fit, tol_press_fit). \
         For screws, inserts, bearings, magnets or any bought part, call recommend_components and name the chosen part \
         (id, or standard family and size) and the hole it needs (hole_size) in the task detail. \
         Finally call set_brief_plan with brief_id \"{brief_id}\" and plan_json = a JSON object \
         {{\"parts\": [{{\"name\": ..., \"task\": ..., \"summary\": ...}}]}}. \
         Do NOT build any geometry yourself. Stop after set_brief_plan.",
        params = list.join("\n")
    )
}

pub fn worker_prompt(brief_id: &str, n: usize) -> String {
    format!(
        "You are WORKER {n} building parts for CadSwarm brief {brief_id}.\n\
         Loop: call list_tasks; pick an OPEN task whose brief is \"{brief_id}\"; claim_task it; \
         lock_objects its object names; build exactly what its detail says, binding every dimension \
         to Params with expressions (e.g. \"Params.pcb_l + 2 * Params.gap\"); place the parts the detail names \
         with add_standard_part or insert_component instead of modelling them; run check_interference \
         and fix clashes with your own parts; report_result done with evidence (object names, volumes). \
         Read get_inbox between tasks and follow any steer messages. \
         When list_tasks shows no OPEN task for brief \"{brief_id}\", stop."
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
        let (p, a) = agent_command("droid", &[], dir, "build it").unwrap();
        assert_eq!((p.as_str(), a[0].as_str(), a.last().unwrap().as_str()), ("droid", "exec", "build it"));
        assert!(agent_command("gpt", &[], dir, "x").is_err());
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
    fn the_handoff_prompt_leads_with_accepting_and_the_persons_note() {
        let ctx = r#"{"from":"carol","note":"make the lip 1 mm thinner","pointer":{"object":"Lid"},"locks":["Lid"],"tasks":[]}"#;
        let p = handoff_prompt("h_1", ctx);
        assert!(p.find("accept_handoff").unwrap() < p.find("make the lip 1 mm thinner").unwrap());
        assert!(p.contains("carol") && p.contains("Lid") && p.contains("h_1"));
    }
}
