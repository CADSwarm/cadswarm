//! Host settings, kept in <data_dir>/host.json (git-ignored: it holds the host's token).

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HostConfig {
    /// Geometry backend. New teams use FreeCAD; standalone remains available for existing projects.
    #[serde(default = "default_engine")]
    pub engine: String,
    /// "local" or "maincloud"
    pub mode: String,
    pub uri: String,
    pub db: String,
    pub token: String,
    pub data_dir: PathBuf,
    /// Python interpreter used to run the selected geometry worker. Reads legacy `freecad_python` keys.
    #[serde(alias = "freecad_python")]
    pub geometry_python: PathBuf,
    pub worker_script: PathBuf,
    /// Optional project document path. New standalone teams can share the user's .cadswarm project directly.
    #[serde(default)]
    pub model_path: Option<PathBuf>,
    pub web_url: String,
    /// The Python that has the `cadswarm` package installed (runs `cadswarm setup`).
    pub cadswarm_python: PathBuf,
    /// The agent CLI the host runs for briefs: "claude", "codex" or "opencode" (whatever the owner
    /// uses).
    #[serde(default = "default_agent_client")]
    pub agent_client: String,
    /// Optional full command line for any other agent CLI; the element "{prompt}" is replaced with the prompt.
    #[serde(default)]
    pub agent_command: Vec<String>,
}

fn default_agent_client() -> String {
    "claude".into()
}

pub const AGENT_CLIENTS: &[&str] = &["claude", "codex", "opencode"];

impl HostConfig {
    pub fn path(data_dir: &Path) -> PathBuf {
        data_dir.join("host.json")
    }
    pub fn load(data_dir: &Path) -> Result<Self> {
        let p = Self::path(data_dir);
        let text = std::fs::read_to_string(&p).with_context(|| format!("reading {} (run `cadswarm-host init` first)", p.display()))?;
        let config: Self = serde_json::from_str(&text)?;
        let expected_worker = match config.engine.as_str() {
            "freecad" => "freecad_worker.py",
            "standalone" => "standalone_worker.py",
            other => bail!("unsupported geometry engine {other:?}; use `freecad` or `standalone`"),
        };
        if config.worker_script.file_name().and_then(|n| n.to_str()) != Some(expected_worker) {
            bail!("host.json engine {:?} must use {expected_worker}; run `cadswarm-host init` again",
                  config.engine);
        }
        Ok(config)
    }
    pub fn save(&self) -> Result<()> {
        std::fs::create_dir_all(&self.data_dir)?;
        std::fs::write(Self::path(&self.data_dir), serde_json::to_string_pretty(self)?)?;
        Ok(())
    }
    pub fn doc_path(&self) -> PathBuf {
        self.model_path.clone().unwrap_or_else(|| {
            let filename = if self.engine == "standalone" { "model.cadswarm" } else { "model.FCStd" };
            self.data_dir.join(filename)
        })
    }
}

fn default_engine() -> String {
    "freecad".into()
}

/// Ask the Python package where the selected worker script is, so both languages agree.
pub fn python_says(python: &Path, expr: &str) -> Result<PathBuf> {
    let out = Command::new(python)
        // PYTHONSAFEPATH, not -I: installed launchers may rely on PYTHONPATH, while this still keeps a
        // cadswarm checkout in the working directory off sys.path.
        .env("PYTHONSAFEPATH", "1")
        .args(["-c", &format!("import cadswarm.config as c; print({expr})")])
        .output()
        .with_context(|| format!("running {}", python.display()))?;
    if !out.status.success() {
        bail!("{} failed: {}", expr, String::from_utf8_lossy(&out.stderr));
    }
    Ok(PathBuf::from(String::from_utf8_lossy(&out.stdout).trim()))
}

/// The CLI's own token: the identity that published the module, which `init` made the host.
pub fn spacetime_token() -> Result<String> {
    if let Ok(t) = std::env::var("CADSWARM_HOST_TOKEN") {
        return Ok(t);
    }
    let out = Command::new("spacetime").args(["login", "show", "--token"]).output().context("running spacetime")?;
    let text = String::from_utf8_lossy(&out.stdout);
    text.split_whitespace()
        .find(|w| w.matches('.').count() == 2 && w.len() > 40)
        .map(str::to_string)
        .context("no token in `spacetime login show --token`; set CADSWARM_HOST_TOKEN")
}
