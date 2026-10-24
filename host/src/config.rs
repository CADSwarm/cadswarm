//! Host settings, kept in <data_dir>/host.json (git-ignored: it holds the host's token).

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HostConfig {
    /// "local" or "maincloud"
    pub mode: String,
    pub uri: String,
    pub db: String,
    pub token: String,
    pub data_dir: PathBuf,
    pub freecad_python: PathBuf,
    pub worker_script: PathBuf,
    pub web_url: String,
    /// The Python that has the `cadswarm` package installed (runs `cadswarm setup`).
    pub cadswarm_python: PathBuf,
}

impl HostConfig {
    pub fn path(data_dir: &Path) -> PathBuf {
        data_dir.join("host.json")
    }
    pub fn load(data_dir: &Path) -> Result<Self> {
        let p = Self::path(data_dir);
        let text = std::fs::read_to_string(&p).with_context(|| format!("reading {} (run `cadswarm-host init` first)", p.display()))?;
        Ok(serde_json::from_str(&text)?)
    }
    pub fn save(&self) -> Result<()> {
        std::fs::create_dir_all(&self.data_dir)?;
        std::fs::write(Self::path(&self.data_dir), serde_json::to_string_pretty(self)?)?;
        Ok(())
    }
    pub fn doc_path(&self) -> PathBuf {
        self.data_dir.join("model.FCStd")
    }
}

/// Ask the Python package where FreeCAD and the worker script are, so both languages agree.
pub fn python_says(python: &Path, expr: &str) -> Result<PathBuf> {
    let out = Command::new(python)
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
