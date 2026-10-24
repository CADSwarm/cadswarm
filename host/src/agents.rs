//! Headless Claude Code agents the host spawns for a brief. Each gets its own folder and
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

/// Where `claude` might be: $CADSWARM_CLAUDE, then PATH, then the usual install folders, then the
/// binary bundled with the VS Code extension (newest version first).
pub fn claude_candidates(env: Option<String>, path_var: &str, home: &Path, appdata: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = env.into_iter().map(PathBuf::from).collect();
    let names: &[&str] = if cfg!(windows) { &["claude.exe", "claude.cmd"] } else { &["claude"] };
    for dir in std::env::split_paths(path_var) {
        out.extend(names.iter().map(|n| dir.join(n)));
    }
    out.extend(names.iter().map(|n| home.join(".local").join("bin").join(n)));
    out.push(appdata.join("npm").join("claude.cmd"));
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
    out
}

pub fn find_claude() -> Result<PathBuf> {
    let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")).map(PathBuf::from).unwrap_or_default();
    let appdata = std::env::var_os("APPDATA").map(PathBuf::from).unwrap_or_default();
    let path_var = std::env::var("PATH").unwrap_or_default();
    claude_candidates(std::env::var("CADSWARM_CLAUDE").ok(), &path_var, &home, &appdata)
        .into_iter()
        .find(|p| p.is_file())
        .ok_or_else(|| anyhow!("Claude Code isn't installed (set CADSWARM_CLAUDE to its path)"))
}

impl Agent {
    /// `ticket` is an agent invite minted by the host.
    pub fn spawn(cfg: &HostConfig, name: &str, ticket: &str, prompt: &str) -> Result<Agent> {
        let dir: PathBuf = cfg.data_dir.join("agents").join(name);
        std::fs::create_dir_all(&dir)?;
        let setup = Command::new(&cfg.cadswarm_python)
            .args(["-m", "cadswarm.cli", "setup", "--client", "claude", "--invite", ticket, "--name", name, "--dir"])
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
        // Headless runs don't prompt to trust the folder's .mcp.json, so name it explicitly.
        let child = Command::new(find_claude()?)
            .args(["-p", prompt, "--max-turns", "80", "--allowedTools", "mcp__cadswarm", "--mcp-config"])
            .arg(dir.join(".mcp.json"))
            .current_dir(&dir)
            .stdin(Stdio::null())
            .stdout(log.try_clone()?)
            .stderr(log)
            .spawn()
            .with_context(|| format!("starting claude for {name}"))?;
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
         to Params with expressions (e.g. \"Params.pcb_l + 2 * Params.gap\"); run check_interference \
         and fix clashes with your own parts; report_result done with evidence (object names, volumes). \
         Read get_inbox between tasks and follow any steer messages. \
         When list_tasks shows no OPEN task for brief \"{brief_id}\", stop."
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
        let c = claude_candidates(Some("X:/my/claude.exe".into()), "", &dir, Path::new("A:/appdata"));
        assert_eq!(c[0], PathBuf::from("X:/my/claude.exe"));
        let bundled: Vec<String> =
            c.iter().map(|p| p.display().to_string()).filter(|s| s.contains("native-binary")).collect();
        assert!(bundled[0].contains("2.1.306"), "newest extension first: {bundled:?}");
        std::fs::remove_dir_all(&dir).ok();
    }
}
