# CadSwarm Host Implementation Plan (Person B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `cadswarm-host`, the Rust process on the FreeCAD machine that runs queued ops in the FreeCAD worker, keeps SpacetimeDB's object rows and previews current, exports STLs, and turns a brief into a planner agent plus worker agents.

**Architecture:** One binary with three commands (`init`, `invite`, `run`). `run` keeps a SpacetimeDB connection (Rust SDK, subscription cache on a background thread) and a single main loop that ticks every 100 ms, reading only the local cache: it executes the oldest queued op, then advances each brief's state machine. Everything that decides something (worker response → reducer args, brief transitions, board → params, tickets) is a pure function with unit tests. Only `space.rs` touches the generated bindings.

**Tech Stack:** Rust stable, `spacetimedb-sdk` (version matching the CLI), `serde_json`, `anyhow`, `clap`, `base64`, `sha2`, `hex`, `data-encoding`, `getrandom`, `qrcode`.

Read first: `docs/superpowers/plans/2026-10-23-cadswarm-00-overview.md` (contract) and `cadswarm/worker/freecad_worker.py` (the worker protocol you drive).

## Global Constraints

- The worker protocol is JSON lines: request `{"id", "op", "args", "allowed"}`, response `{"ok": true, "result", "created", "modified", "deleted", "recomputed", "broken"}` or `{"ok": false, "code", "error"}`. First line on start is `{"ready": true}`.
- Worker timeout 120 s; on timeout kill it and restart on the next call (it reloads the last save).
- The worker saves `model.FCStd` before committing each transaction (fix on branch `fix/post-fddfa28-hardening`); a save failure comes back as an op error and the host completes the op as `failed`, never as `done`.
- The host's member kind is `host`; it calls `rejoin` on start and then uses its fence from `v_me` on every reducer.
- Previews: binary STL ≤ 200 KB per object. Artifacts: bytes stored only if ≤ 5 MB.
- Headless agents: at most 3 workers per brief, each killed after 15 minutes.
- Tickets: base32 (RFC 4648, uppercase, no padding) of `{"v":1,"uri","db","token","kind","role","host_addr":""}`; the module stores `sha256(token)` hex.
- No secrets in the repo: `host-data/` is git-ignored.

## File structure

| File | Responsibility |
|---|---|
| `host/Cargo.toml` | Crate |
| `host/src/main.rs` | CLI: `init`, `invite`, `run` |
| `host/src/config.rs` | `HostConfig` load/save, discovery of FreeCAD and the worker script |
| `host/src/ticket.rs` | Mint tokens, encode tickets, phone URL |
| `host/src/worker.rs` | `WorkerDriver`: spawn, call, timeout, restart |
| `host/src/commit.rs` | Pure: worker response → `complete_op` arguments |
| `host/src/space.rs` | The only file using `module_bindings`: connect, cache reads, reducer calls |
| `host/src/executor.rs` | Run one op end to end; refresh objects and previews |
| `host/src/boards.rs` | Load `boards/boards.json`; board → seed params (pure) |
| `host/src/briefs.rs` | Brief state machine (pure) + runtime driving it |
| `host/src/agents.rs` | Set up and spawn headless Claude Code planner/workers |
| `host/tests/fake_worker.py` | Stand-in FreeCAD worker for tests |
| `.gitignore` | Add `host-data/` and `host/target/` |

---

### Task 1: Crate, config and tickets

**Files:**
- Create: `host/Cargo.toml`, `host/src/main.rs`, `host/src/config.rs`, `host/src/ticket.rs`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `HostConfig { mode, uri, db, token, data_dir, freecad_python, worker_script, web_url, cadswarm_python }` with `HostConfig::load(&Path) -> Result<Self>` / `save(&self, &Path) -> Result<()>`; `ticket::new_token() -> String` (64 lowercase hex), `ticket::hash(&str) -> String`, `ticket::encode(&Ticket) -> String`, `ticket::decode(&str) -> Result<Ticket>`, `ticket::phone_url(web_url, &str) -> String`.

- [ ] **Step 1: Create the crate**

```powershell
git switch -c feat/host
cargo new host --bin --name cadswarm-host
cd host
cargo add serde --features derive
cargo add serde_json anyhow base64 sha2 hex data-encoding qrcode
cargo add getrandom@0.2
cargo add clap --features derive
cd ..
```
Do not add `spacetimedb-sdk` yet (Task 4 does, after the bindings exist).

Append to `.gitignore`:
```
host-data/
host/target/
```

- [ ] **Step 2: Write the failing ticket tests**

`host/src/ticket.rs`:
```rust
//! Pairing tickets: base32 JSON that a phone link or `cadswarm setup --invite` carries.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Ticket {
    pub v: u32,
    pub uri: String,
    pub db: String,
    pub token: String,
    pub kind: String,
    pub role: String,
    #[serde(default)]
    pub host_addr: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Ticket {
        Ticket {
            v: 1,
            uri: "http://127.0.0.1:3000".into(),
            db: "cadswarm".into(),
            token: "ab".repeat(32),
            kind: "human".into(),
            role: "member".into(),
            host_addr: String::new(),
        }
    }

    #[test]
    fn a_ticket_round_trips_and_is_url_safe() {
        let t = sample();
        let s = encode(&t);
        assert!(s.chars().all(|c| c.is_ascii_uppercase() || ('2'..='7').contains(&c)), "{s}");
        assert_eq!(decode(&s).unwrap(), t);
        assert_eq!(decode(&s.to_lowercase()).unwrap(), t, "decoding is case-insensitive");
    }

    #[test]
    fn tokens_are_64_hex_and_hash_matches_the_module() {
        let tok = new_token();
        assert_eq!(tok.len(), 64);
        assert!(tok.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
        assert_eq!(hash("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }

    #[test]
    fn the_phone_url_puts_the_ticket_in_the_fragment() {
        assert_eq!(phone_url("https://x.github.io/cadswarm/", "ABC"), "https://x.github.io/cadswarm/#t=ABC");
        assert_eq!(phone_url("http://localhost:5173", "ABC"), "http://localhost:5173/#t=ABC");
    }
}
```
Add `mod ticket;` to `host/src/main.rs` (leave the template's `main` for now).

- [ ] **Step 3: Run to see them fail**

Run: `cargo test --manifest-path host/Cargo.toml`
Expected: compile errors (`cannot find function encode`).

- [ ] **Step 4: Implement**

Add above the tests in `host/src/ticket.rs`:
```rust
pub fn new_token() -> String {
    let mut b = [0u8; 32];
    getrandom::getrandom(&mut b).expect("system rng");
    hex::encode(b)
}

pub fn hash(token: &str) -> String {
    use sha2::{Digest, Sha256};
    hex::encode(Sha256::digest(token.trim().as_bytes()))
}

pub fn encode(t: &Ticket) -> String {
    data_encoding::BASE32_NOPAD.encode(&serde_json::to_vec(t).expect("ticket serializes"))
}

pub fn decode(s: &str) -> Result<Ticket> {
    let cleaned: String = s.trim().to_uppercase().chars().filter(|c| !c.is_whitespace()).collect();
    let bytes = data_encoding::BASE32_NOPAD.decode(cleaned.as_bytes()).context("ticket is not valid base32")?;
    serde_json::from_slice(&bytes).context("ticket payload is malformed")
}

pub fn phone_url(web_url: &str, ticket: &str) -> String {
    format!("{}/#t={ticket}", web_url.trim_end_matches('/'))
}
```

- [ ] **Step 5: Write `host/src/config.rs`**

```rust
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
```
Add `mod config;` to `main.rs`.

- [ ] **Step 6: Run the tests**

Run: `cargo test --manifest-path host/Cargo.toml`
Expected: `3 passed`.

- [ ] **Step 7: Commit**

```powershell
git add host .gitignore
git commit -m "feat(host): crate, config and pairing tickets"
```

---

### Task 2: The FreeCAD worker driver

**Files:**
- Create: `host/src/worker.rs`, `host/tests/fake_worker.py`

**Interfaces:**
- Produces: `WorkerDriver::new(python: PathBuf, script: PathBuf, doc: PathBuf, timeout: Duration) -> Self`; `call(&mut self, op: &str, args: &Value, allowed: Option<&[String]>) -> Result<Value, WorkerError>` returning the whole response object on `ok: true`; `WorkerError { code: String, message: String }`.

- [ ] **Step 1: Write the fake worker `host/tests/fake_worker.py`**

```python
"""Stand-in for cadswarm/worker/freecad_worker.py: same JSON-lines protocol, no FreeCAD."""
import json
import sys
import time

print(json.dumps({"ready": True, "doc": sys.argv[1] if len(sys.argv) > 1 else ""}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    op, args = req["op"], req.get("args") or {}
    if op == "sleep":
        time.sleep(args.get("seconds", 5))
    if op == "die":
        sys.exit(3)
    if op == "fail":
        resp = {"ok": False, "code": "lock_violation", "error": "would change ['Plate']"}
    else:
        resp = {"ok": True, "result": {"echo": op, "allowed": req.get("allowed")},
                "created": [args["name"]] if "name" in args else [], "modified": [], "deleted": [],
                "recomputed": [], "broken": []}
    resp["id"] = req["id"]
    print(json.dumps(resp), flush=True)
```

- [ ] **Step 2: Write the failing tests**

`host/src/worker.rs`:
```rust
//! Supervises the FreeCAD worker subprocess (port of cadswarm/hub/worker_client.py).
//! One request at a time; a hung or crashed worker is killed and restarted from its last save.

use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError};
use std::time::Duration;

#[derive(Debug, Clone, PartialEq)]
pub struct WorkerError {
    pub code: String,
    pub message: String,
}

impl std::fmt::Display for WorkerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

fn err(code: &str, message: impl Into<String>) -> WorkerError {
    WorkerError { code: code.into(), message: message.into() }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn driver(timeout_ms: u64) -> WorkerDriver {
        let script = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests").join("fake_worker.py");
        let python = PathBuf::from(std::env::var("PYTHON").unwrap_or_else(|_| "python".into()));
        WorkerDriver::new(python, script, PathBuf::from("doc.FCStd"), Duration::from_millis(timeout_ms))
    }

    #[test]
    fn a_call_returns_the_whole_response_and_passes_allowed() {
        let mut w = driver(5_000);
        let r = w.call("create_primitive", &json!({"name": "Box"}), Some(&["Box".to_string()])).unwrap();
        assert_eq!(r["created"], json!(["Box"]));
        assert_eq!(r["result"]["allowed"], json!(["Box"]));
    }

    #[test]
    fn a_worker_refusal_is_a_typed_error() {
        let mut w = driver(5_000);
        let e = w.call("fail", &json!({}), None).unwrap_err();
        assert_eq!(e.code, "lock_violation");
    }

    #[test]
    fn a_hung_worker_times_out_and_the_next_call_restarts_it() {
        let mut w = driver(500);
        let e = w.call("sleep", &json!({"seconds": 5}), None).unwrap_err();
        assert_eq!(e.code, "timeout");
        assert!(w.call("ping", &json!({}), None).is_ok());
    }

    #[test]
    fn a_crashed_worker_is_reported_then_restarted() {
        let mut w = driver(5_000);
        assert_eq!(w.call("die", &json!({}), None).unwrap_err().code, "worker_died");
        assert!(w.call("ping", &json!({}), None).is_ok());
    }
}
```
Add `mod worker;` to `main.rs`.

- [ ] **Step 3: Run to see them fail**

Run: `cargo test --manifest-path host/Cargo.toml worker`
Expected: compile error (`cannot find type WorkerDriver`).

- [ ] **Step 4: Implement**

Add above the tests in `host/src/worker.rs`:
```rust
struct Running {
    child: Child,
    stdin: ChildStdin,
    lines: Receiver<String>,
}

pub struct WorkerDriver {
    python: PathBuf,
    script: PathBuf,
    doc: PathBuf,
    timeout: Duration,
    running: Option<Running>,
    next_id: u64,
}

impl WorkerDriver {
    pub fn new(python: PathBuf, script: PathBuf, doc: PathBuf, timeout: Duration) -> Self {
        WorkerDriver { python, script, doc, timeout, running: None, next_id: 0 }
    }

    fn start(&mut self) -> Result<(), WorkerError> {
        let bin_dir = self.python.parent().map(|p| p.to_path_buf()).unwrap_or_default();
        let mut child = Command::new(&self.python)
            .arg(&self.script)
            .arg(&self.doc)
            .env("CADSWARM_FREECAD_BIN", bin_dir)
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUNBUFFERED", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|e| err("worker_start", format!("could not start {}: {e}", self.python.display())))?;
        let stdin = child.stdin.take().expect("piped stdin");
        let stdout = child.stdout.take().expect("piped stdout");
        let (tx, rx) = channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                match line {
                    Ok(l) => {
                        if tx.send(l).is_err() {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
        });
        // FreeCAD can take a while to import on first start.
        let hello = rx
            .recv_timeout(self.timeout.max(Duration::from_secs(60)))
            .map_err(|_| err("worker_start", "FreeCAD worker did not say ready"))?;
        let v: Value = serde_json::from_str(&hello).map_err(|e| err("worker_start", format!("bad hello {hello:?}: {e}")))?;
        if v["ready"] != json!(true) {
            return Err(err("worker_start", format!("FreeCAD worker did not start: {hello}")));
        }
        self.running = Some(Running { child, stdin, lines: rx });
        Ok(())
    }

    fn kill(&mut self) {
        if let Some(mut r) = self.running.take() {
            let _ = r.child.kill();
            let _ = r.child.wait();
        }
    }

    pub fn call(&mut self, op: &str, args: &Value, allowed: Option<&[String]>) -> Result<Value, WorkerError> {
        let alive = match self.running.as_mut() {
            Some(r) => matches!(r.child.try_wait(), Ok(None)),
            None => false,
        };
        if !alive {
            self.kill();
            self.start()?;
        }
        self.next_id += 1;
        let req = json!({"id": self.next_id, "op": op, "args": args, "allowed": allowed});
        let r = self.running.as_mut().expect("started");
        if writeln!(r.stdin, "{req}").and_then(|_| r.stdin.flush()).is_err() {
            self.kill();
            return Err(err("worker_died", "FreeCAD worker connection failed; the op was rolled back"));
        }
        let line = match r.lines.recv_timeout(self.timeout) {
            Ok(l) => l,
            Err(RecvTimeoutError::Timeout) => {
                self.kill();
                return Err(err("timeout", format!("FreeCAD worker exceeded {}s; the op was rolled back", self.timeout.as_secs())));
            }
            Err(RecvTimeoutError::Disconnected) => {
                self.kill();
                return Err(err("worker_died", "FreeCAD worker exited; the op was rolled back"));
            }
        };
        let resp: Value = serde_json::from_str(&line).map_err(|e| err("op_error", format!("bad worker reply: {e}")))?;
        if resp["ok"] == json!(true) {
            Ok(resp)
        } else {
            Err(err(
                resp["code"].as_str().unwrap_or("op_error"),
                resp["error"].as_str().unwrap_or("unknown error").to_string(),
            ))
        }
    }
}

impl Drop for WorkerDriver {
    fn drop(&mut self) {
        self.kill();
    }
}
```

- [ ] **Step 5: Run the tests**

Run: `cargo test --manifest-path host/Cargo.toml worker`
Expected: `4 passed`. (Set `$env:PYTHON` if `python` is not on PATH.)

- [ ] **Step 6: Commit**

```powershell
git add host/src/worker.rs host/tests/fake_worker.py host/src/main.rs
git commit -m "feat(host): FreeCAD worker driver with timeout and restart"
```

---

### Task 3: Pure op commit mapping and board seeding

**Files:**
- Create: `host/src/commit.rs`, `host/src/boards.rs`

**Interfaces:**
- Produces: `struct Completion { ok, result_json, created, modified, deleted, recomputed, broken, error_code, error }` and `fn completion(Result<Value, WorkerError>) -> Completion`; `fn touched(&Completion) -> Vec<String>` (created + modified + recomputed, deduplicated); `struct Board`, `fn load_boards(&Path) -> Result<BTreeMap<String, Board>>`, `fn seed_params(&Board) -> Vec<(String, String)>`.

- [ ] **Step 1: Write the failing tests**

`host/src/commit.rs`:
```rust
//! Turning a worker reply into the arguments of the `complete_op` reducer.

use crate::worker::WorkerError;
use serde_json::Value;

#[derive(Debug, Clone, PartialEq)]
pub struct Completion {
    pub ok: bool,
    pub result_json: String,
    pub created: Vec<String>,
    pub modified: Vec<String>,
    pub deleted: Vec<String>,
    pub recomputed: Vec<String>,
    pub broken: Vec<String>,
    pub error_code: String,
    pub error: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_successful_reply_maps_every_list_and_the_result() {
        let c = completion(Ok(json!({"ok": true, "result": {"name": "Box"}, "created": ["Box"],
            "modified": [], "deleted": [], "recomputed": ["Lid"], "broken": []})));
        assert!(c.ok);
        assert_eq!(c.created, vec!["Box"]);
        assert_eq!(c.recomputed, vec!["Lid"]);
        assert_eq!(serde_json::from_str::<Value>(&c.result_json).unwrap(), json!({"name": "Box"}));
        assert_eq!(touched(&c), vec!["Box".to_string(), "Lid".to_string()]);
    }

    #[test]
    fn an_error_maps_code_and_message() {
        let c = completion(Err(WorkerError { code: "lock_violation".into(), message: "nope".into() }));
        assert!(!c.ok);
        assert_eq!((c.error_code.as_str(), c.error.as_str()), ("lock_violation", "nope"));
    }
}
```
`host/src/boards.rs`:
```rust
//! The board library (boards/boards.json) and the Params a brief starts from.

use anyhow::Result;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::path::Path;

#[derive(Debug, Clone, Deserialize)]
pub struct Port {
    pub name: String,
    pub side: String,
    pub center: [f64; 2],
    pub size: [f64; 2],
}

#[derive(Debug, Clone, Deserialize)]
pub struct Board {
    pub label: String,
    pub pcb: [f64; 3],
    pub holes: Vec<[f64; 2]>,
    pub hole_d: f64,
    pub clear_top: f64,
    pub clear_bottom: f64,
    #[serde(default)]
    pub ports: Vec<Port>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pi4() -> Board {
        Board {
            label: "Raspberry Pi 4".into(),
            pcb: [85.0, 56.0, 1.4],
            holes: vec![[3.5, 3.5], [61.5, 3.5]],
            hole_d: 2.7,
            clear_top: 17.0,
            clear_bottom: 3.0,
            ports: vec![Port { name: "usb_c".into(), side: "-y".into(), center: [11.2, 2.0], size: [9.5, 3.5] }],
        }
    }

    #[test]
    fn a_board_seeds_measured_params_plus_defaults() {
        let p: BTreeMap<String, String> = seed_params(&pi4()).into_iter().collect();
        assert_eq!(p["pcb_l"], "85 mm");
        assert_eq!(p["hole_2_x"], "61.5 mm");
        assert_eq!(p["usb_c_pos"], "11.2 mm");
        assert_eq!(p["usb_c_w"], "9.5 mm");
        assert_eq!(p["wall"], "2 mm");
        assert_eq!(p["gap"], "0.5 mm");
        assert_eq!(p["standoff_h"], "4 mm");
    }
}
```
Add `mod commit; mod boards;` to `main.rs`.

- [ ] **Step 2: Run to see them fail**

Run: `cargo test --manifest-path host/Cargo.toml`
Expected: compile errors (`cannot find function completion`, `seed_params`).

- [ ] **Step 3: Implement**

Add to `host/src/commit.rs` above the tests:
```rust
fn list(v: &Value, key: &str) -> Vec<String> {
    v[key].as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()).unwrap_or_default()
}

pub fn completion(reply: Result<Value, WorkerError>) -> Completion {
    match reply {
        Ok(v) => Completion {
            ok: true,
            result_json: v["result"].to_string(),
            created: list(&v, "created"),
            modified: list(&v, "modified"),
            deleted: list(&v, "deleted"),
            recomputed: list(&v, "recomputed"),
            broken: list(&v, "broken"),
            error_code: String::new(),
            error: String::new(),
        },
        Err(e) => Completion {
            ok: false,
            result_json: String::new(),
            created: vec![],
            modified: vec![],
            deleted: vec![],
            recomputed: vec![],
            broken: vec![],
            error_code: e.code,
            error: e.message,
        },
    }
}

pub fn touched(c: &Completion) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for n in c.created.iter().chain(c.modified.iter()).chain(c.recomputed.iter()) {
        if !out.contains(n) {
            out.push(n.clone());
        }
    }
    out
}
```
Add to `host/src/boards.rs` above the tests:
```rust
pub fn load_boards(path: &Path) -> Result<BTreeMap<String, Board>> {
    Ok(serde_json::from_str(&std::fs::read_to_string(path)?)?)
}

fn mm(v: f64) -> String {
    format!("{} mm", (v * 1000.0).round() / 1000.0)
}

pub fn seed_params(b: &Board) -> Vec<(String, String)> {
    let mut p = vec![
        ("pcb_l".to_string(), mm(b.pcb[0])),
        ("pcb_w".to_string(), mm(b.pcb[1])),
        ("pcb_t".to_string(), mm(b.pcb[2])),
        ("hole_d".to_string(), mm(b.hole_d)),
        ("clear_top".to_string(), mm(b.clear_top)),
        ("clear_bottom".to_string(), mm(b.clear_bottom)),
    ];
    for (i, h) in b.holes.iter().enumerate() {
        p.push((format!("hole_{}_x", i + 1), mm(h[0])));
        p.push((format!("hole_{}_y", i + 1), mm(h[1])));
    }
    for port in &b.ports {
        p.push((format!("{}_pos", port.name), mm(port.center[0])));
        p.push((format!("{}_z", port.name), mm(port.center[1])));
        p.push((format!("{}_w", port.name), mm(port.size[0])));
        p.push((format!("{}_h", port.name), mm(port.size[1])));
    }
    p.push(("wall".into(), mm(2.0)));
    p.push(("gap".into(), mm(0.5)));
    p.push(("standoff_h".into(), mm(4.0)));
    p
}
```

- [ ] **Step 4: Run the tests**

Run: `cargo test --manifest-path host/Cargo.toml`
Expected: all pass (`3` ticket + `4` worker + `2` commit + `1` boards).

- [ ] **Step 5: Commit**

```powershell
git add host/src/commit.rs host/src/boards.rs host/src/main.rs
git commit -m "feat(host): worker reply mapping and board params seeding"
```

---

### Task 4: The SpacetimeDB connection (needs M0 bindings)

**Files:**
- Create: `host/src/space.rs`
- Modify: `host/Cargo.toml`, `host/src/main.rs`

**Interfaces:**
- Consumes: `host/src/module_bindings/` (generated by A; on `main` after M0).
- Produces: `Space::connect(&HostConfig) -> Result<Space>`; cache reads `fence() -> u32`, `queued_ops() -> Vec<OpRow>`, `op(&str) -> Option<OpRow>`, `briefs() -> Vec<BriefRow>`, `tasks_for(&str) -> Vec<TaskRow>`, `latest_review(&str) -> Option<ReviewRow>`, `bed() -> [f64; 3]`; reducer calls `start_op`, `complete_op(&str, &Completion)`, `sync_objects`, `put_preview`, `put_artifact`, `submit_op`, `lock_objects`, `release_objects`, `create_invite`, `set_brief`, `set_brief_stl`. `OpRow`, `BriefRow`, `TaskRow`, `ReviewRow` are plain structs defined here so the rest of the host never imports generated types.

- [ ] **Step 1: Pull the bindings and add the SDK**

```powershell
git fetch origin; git rebase origin/main
spacetime --version
cargo add --manifest-path host/Cargo.toml spacetimedb-sdk@=2.13.0
```
`spacetime --version` must print 2.13.x and `rustc --version` 1.97 or later (plan 00 Global Constraints: SpacetimeDB is pinned to 2.13.0 everywhere). If the CLI prints something else, run `spacetime version install 2.13.0; spacetime version use 2.13.0` and regenerate the bindings before going on; if rustc is older, run `rustup update`.

- [ ] **Step 2: Read the generated API before writing code**

Open `host/src/module_bindings/mod.rs`. Confirm and write down: the connection builder's database method (`with_database_name` in 2.x docs, `with_module_name` in 1.x), how a view is accessed (`conn.db.v_op()`), the row type name for `v_op` (expected `Op`), and a reducer's call shape (expected `conn.reducers.start_op(fence, op_id)` returning `Result<()>`). Wherever the code below differs, change only `space.rs`.

- [ ] **Step 3: Write `host/src/space.rs`**

```rust
//! The only file that touches the generated SpacetimeDB bindings.

use crate::commit::Completion;
use crate::config::HostConfig;
use crate::module_bindings::*;
use anyhow::{anyhow, Result};
use spacetimedb_sdk::{DbContext, Table};
use std::time::{Duration, Instant};

#[derive(Debug, Clone)]
pub struct OpRow {
    pub id: String,
    pub by: u64,
    pub kind: String,
    pub args_json: String,
    pub allowed: Vec<String>,
    pub mutating: bool,
    pub status: String,
    pub result_json: String,
    pub error: String,
    pub created_ms: u64,
}

#[derive(Debug, Clone)]
pub struct BriefRow {
    pub id: String,
    pub text: String,
    pub board: String,
    pub status: String,
}

#[derive(Debug, Clone)]
pub struct TaskRow {
    pub id: String,
    pub status: String,
    pub objects: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct ReviewRow {
    pub round: u32,
    pub passed: bool,
}

pub struct Space {
    conn: DbConnection,
}

const QUERIES: &[&str] = &[
    "SELECT * FROM v_me",
    "SELECT * FROM v_workspace",
    "SELECT * FROM v_op",
    "SELECT * FROM v_brief",
    "SELECT * FROM v_task",
    "SELECT * FROM v_review",
    "SELECT * FROM v_member",
];

impl Space {
    pub fn connect(cfg: &HostConfig) -> Result<Space> {
        let conn = DbConnection::builder()
            .with_uri(cfg.uri.as_str())
            .with_database_name(cfg.db.as_str())
            .with_token(Some(cfg.token.clone()))
            .on_connect(|_, identity, _| eprintln!("[host] connected as {identity:?}"))
            .on_connect_error(|_, e| eprintln!("[host] connect error: {e:?}"))
            .on_disconnect(|_, e| eprintln!("[host] disconnected: {e:?}"))
            .build()
            .map_err(|e| anyhow!("connecting to {} / {}: {e:?}", cfg.uri, cfg.db))?;
        conn.run_threaded();
        conn.subscription_builder()
            .on_error(|_, e| eprintln!("[host] subscription error: {e:?}"))
            .subscribe(QUERIES);
        let space = Space { conn };
        space.conn.reducers.rejoin("host".to_string()).map_err(|e| anyhow!("rejoin: {e:?}"))?;
        space.wait_for(Duration::from_secs(20), |s| s.me().is_some())?;
        Ok(space)
    }

    pub fn wait_for(&self, timeout: Duration, ok: impl Fn(&Space) -> bool) -> Result<()> {
        let t = Instant::now();
        while t.elapsed() < timeout {
            if ok(self) {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        Err(anyhow!("timed out waiting for SpacetimeDB"))
    }

    fn me(&self) -> Option<Member> {
        self.conn.db.v_me().iter().next()
    }

    pub fn fence(&self) -> u32 {
        self.me().map(|m| m.fence).unwrap_or(0)
    }

    pub fn bed(&self) -> [f64; 3] {
        self.conn
            .db
            .v_workspace()
            .iter()
            .next()
            .map(|w| [w.bed_x_mm, w.bed_y_mm, w.bed_z_mm])
            .unwrap_or([180.0, 180.0, 180.0])
    }

    fn op_row(o: Op) -> OpRow {
        OpRow {
            id: o.id,
            by: o.by,
            kind: o.kind,
            args_json: o.args_json,
            allowed: o.allowed,
            mutating: o.mutating,
            status: o.status,
            result_json: o.result_json,
            error: o.error,
            created_ms: o.created_ms,
        }
    }

    /// Oldest first: ops run in submission order.
    pub fn queued_ops(&self) -> Vec<OpRow> {
        let mut v: Vec<OpRow> =
            self.conn.db.v_op().iter().filter(|o| o.status == "queued").map(Self::op_row).collect();
        v.sort_by_key(|o| (o.created_ms, o.id.clone()));
        v
    }

    pub fn op(&self, id: &str) -> Option<OpRow> {
        self.conn.db.v_op().iter().find(|o| o.id == id).map(Self::op_row)
    }

    pub fn briefs(&self) -> Vec<BriefRow> {
        self.conn
            .db
            .v_brief()
            .iter()
            .map(|b| BriefRow { id: b.id, text: b.text, board: b.board, status: b.status })
            .collect()
    }

    pub fn tasks_for(&self, brief: &str) -> Vec<TaskRow> {
        self.conn
            .db
            .v_task()
            .iter()
            .filter(|t| t.brief == brief)
            .map(|t| TaskRow { id: t.id, status: t.status, objects: t.objects })
            .collect()
    }

    pub fn latest_review(&self, brief: &str) -> Option<ReviewRow> {
        self.conn
            .db
            .v_review()
            .iter()
            .filter(|r| r.brief == brief)
            .max_by_key(|r| r.round)
            .map(|r| ReviewRow { round: r.round, passed: r.passed })
    }

    fn sent<T, E: std::fmt::Debug>(what: &str, r: std::result::Result<T, E>) -> Result<()> {
        r.map(|_| ()).map_err(|e| anyhow!("{what}: {e:?}"))
    }

    pub fn start_op(&self, id: &str) -> Result<()> {
        Self::sent("start_op", self.conn.reducers.start_op(self.fence(), id.to_string()))
    }

    pub fn complete_op(&self, id: &str, c: &Completion) -> Result<()> {
        Self::sent(
            "complete_op",
            self.conn.reducers.complete_op(
                self.fence(),
                id.to_string(),
                c.ok,
                c.result_json.clone(),
                c.created.clone(),
                c.modified.clone(),
                c.deleted.clone(),
                c.recomputed.clone(),
                c.broken.clone(),
                c.error_code.clone(),
                c.error.clone(),
            ),
        )
    }

    pub fn sync_objects(&self, objects_json: String, params_json: String) -> Result<()> {
        Self::sent("sync_objects", self.conn.reducers.sync_objects(self.fence(), objects_json, params_json))
    }

    pub fn put_preview(&self, object: &str, mesh: Vec<u8>) -> Result<()> {
        Self::sent("put_preview", self.conn.reducers.put_preview(self.fence(), object.to_string(), mesh))
    }

    pub fn put_artifact(&self, hash: &str, name: &str, mime: &str, bytes: Vec<u8>, brief: &str) -> Result<()> {
        Self::sent(
            "put_artifact",
            self.conn.reducers.put_artifact(self.fence(), hash.into(), name.into(), mime.into(), bytes, brief.into()),
        )
    }

    pub fn submit_op(&self, id: &str, kind: &str, args_json: String) -> Result<()> {
        Self::sent(
            "submit_op",
            self.conn.reducers.submit_op(self.fence(), id.into(), kind.into(), args_json, String::new()),
        )
    }

    pub fn lock_objects(&self, names: Vec<String>) -> Result<()> {
        Self::sent("lock_objects", self.conn.reducers.lock_objects(self.fence(), names))
    }

    pub fn release_objects(&self, names: Vec<String>) -> Result<()> {
        Self::sent("release_objects", self.conn.reducers.release_objects(self.fence(), names, false))
    }

    pub fn create_invite(&self, token_hash: String, kind: &str, role: &str, ttl_secs: u64) -> Result<()> {
        Self::sent(
            "create_invite",
            self.conn.reducers.create_invite(self.fence(), token_hash, kind.into(), role.into(), ttl_secs),
        )
    }

    pub fn set_brief(&self, id: &str, status: &str, plan_json: &str) -> Result<()> {
        Self::sent("set_brief", self.conn.reducers.set_brief(self.fence(), id.into(), status.into(), plan_json.into()))
    }

    pub fn set_brief_stl(&self, id: &str, hash: &str) -> Result<()> {
        Self::sent("set_brief_stl", self.conn.reducers.set_brief_stl(self.fence(), id.into(), hash.into()))
    }
}
```
Add `mod module_bindings; mod space;` to `main.rs`.

- [ ] **Step 4: Build**

Run: `cargo build --manifest-path host/Cargo.toml`
Expected: builds. Every error here is a name mismatch with the generated code; fix it in `space.rs` using what you wrote down in Step 2.

- [ ] **Step 5: Commit**

```powershell
git add host
git commit -m "feat(host): SpacetimeDB connection, cache reads and reducer calls"
```

---

### Task 5: Init, invite and the executor loop (M1)

**Files:**
- Create: `host/src/executor.rs`
- Modify: `host/src/main.rs`

**Interfaces:**
- Consumes: `WorkerDriver`, `Space`, `commit::{completion, touched}`, `ticket`.
- Produces: `executor::run_op(&Space, &mut WorkerDriver, &HostConfig, &OpRow) -> Result<()>`; `executor::refresh(&Space, &mut WorkerDriver, names: &[String]) -> Result<()>`; commands `cadswarm-host init`, `cadswarm-host invite`, `cadswarm-host run`.

- [ ] **Step 1: Write `host/src/executor.rs`**

```rust
//! Runs one queued op in FreeCAD and writes the outcome back to SpacetimeDB.

use crate::commit::{completion, touched};
use crate::config::HostConfig;
use crate::space::{OpRow, Space};
use crate::worker::{WorkerDriver, WorkerError};
use anyhow::Result;
use base64::Engine;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

const MAX_INLINE_ARTIFACT: usize = 5 * 1024 * 1024;

pub fn run_op(space: &Space, worker: &mut WorkerDriver, cfg: &HostConfig, op: &OpRow) -> Result<()> {
    space.start_op(&op.id)?;
    let mut args: Value = serde_json::from_str(&op.args_json).unwrap_or(json!({}));
    let reply = match op.kind.as_str() {
        "export" => export(space, worker, cfg, op, &args),
        "printability" => {
            if args.get("bed").is_none() {
                args["bed"] = json!(space.bed());
            }
            worker.call("printability", &args, None).map(|r| r)
        }
        _ if op.mutating => worker.call(&op.kind, &args, Some(&op.allowed)),
        _ => worker.call(&op.kind, &args, None),
    };
    let done = completion(reply);
    space.complete_op(&op.id, &done)?;
    if done.ok && op.mutating {
        refresh(space, worker, &touched(&done))?;
    }
    Ok(())
}

fn export(space: &Space, worker: &mut WorkerDriver, cfg: &HostConfig, op: &OpRow, args: &Value) -> Result<Value, WorkerError> {
    let fmt = args["format"].as_str().unwrap_or("stl").to_lowercase();
    if fmt != "stl" && fmt != "step" {
        return Err(WorkerError { code: "bad_args".into(), message: "format must be stl or step".into() });
    }
    let dir = cfg.data_dir.join("exports");
    std::fs::create_dir_all(&dir).ok();
    let name = format!("model-{}.{fmt}", op.id);
    let path = dir.join(&name);
    let reply = worker.call("export", &json!({"path": path, "names": args["names"]}), None)?;
    let bytes = std::fs::read(&path)
        .map_err(|e| WorkerError { code: "op_error".into(), message: format!("export file missing: {e}") })?;
    let hash = hex::encode(Sha256::digest(&bytes));
    let size = bytes.len();
    let mime = if fmt == "stl" { "model/stl" } else { "model/step" };
    let inline = if size <= MAX_INLINE_ARTIFACT { bytes } else { vec![] };
    let brief = args["brief"].as_str().unwrap_or("");
    space
        .put_artifact(&hash, &name, mime, inline, brief)
        .map_err(|e| WorkerError { code: "op_error".into(), message: e.to_string() })?;
    Ok(json!({"ok": true, "result": {"hash": hash, "name": name, "size": size,
              "objects": reply["result"]["objects"]}}))
}

/// Push object details, params and previews for `names` (or everything when empty).
pub fn refresh(space: &Space, worker: &mut WorkerDriver, names: &[String]) -> Result<()> {
    let status = worker.call("status", &json!({}), None).map_err(|e| anyhow::anyhow!("status: {e}"))?;
    space.sync_objects(status["result"]["objects"].to_string(), status["result"]["params"].to_string())?;
    let names: Vec<Value> = if names.is_empty() {
        status["result"]["objects"].as_array().cloned().unwrap_or_default().iter().map(|o| o["name"].clone()).collect()
    } else {
        names.iter().map(|n| json!(n)).collect()
    };
    let meshes = worker
        .call("mesh", &json!({"names": names, "tolerance": 0.2, "max_bytes": 200_000}), None)
        .map_err(|e| anyhow::anyhow!("mesh: {e}"))?;
    if let Some(map) = meshes["result"]["meshes"].as_object() {
        for (name, b64) in map {
            if let Some(s) = b64.as_str() {
                if let Ok(bytes) = base64::engine::general_purpose::STANDARD.decode(s) {
                    space.put_preview(name, bytes)?;
                }
            }
        }
    }
    Ok(())
}
```

- [ ] **Step 2: Replace `host/src/main.rs`**

```rust
//! cadswarm-host: runs the shared FreeCAD model for a CadSwarm workspace.

mod agents;
mod boards;
mod briefs;
mod commit;
mod config;
mod executor;
mod module_bindings;
mod space;
mod ticket;
mod worker;

use anyhow::Result;
use clap::{Parser, Subcommand};
use config::HostConfig;
use std::path::PathBuf;
use std::time::Duration;

#[derive(Parser)]
#[command(name = "cadswarm-host")]
struct Cli {
    /// Folder for host.json, the model, exports and agent folders (git-ignored).
    #[arg(long, default_value = "host-data", global = true)]
    data: PathBuf,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Write host.json. The module must already be published (scripts/publish-*.ps1).
    Init {
        #[arg(long, default_value = "local")]
        mode: String,
        #[arg(long, default_value = "cadswarm")]
        db: String,
        #[arg(long, default_value = "http://localhost:5173")]
        web_url: String,
        /// Python with the cadswarm package installed.
        #[arg(long, default_value = "python")]
        python: PathBuf,
    },
    /// Mint an invite and print its ticket, phone link and QR code.
    Invite {
        #[arg(long, default_value = "human")]
        kind: String,
        #[arg(long, default_value = "member")]
        role: String,
    },
    /// Run the executor and brief orchestration until stopped.
    Run,
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.cmd {
        Cmd::Init { mode, db, web_url, python } => {
            let uri = if mode == "maincloud" { "https://maincloud.spacetimedb.com" } else { "http://127.0.0.1:3000" };
            let cfg = HostConfig {
                mode,
                uri: uri.into(),
                db,
                token: config::spacetime_token()?,
                data_dir: std::fs::canonicalize(&cli.data).unwrap_or(cli.data.clone()),
                freecad_python: config::python_says(&python, "c.find_freecad_python()")?,
                worker_script: config::python_says(&python, "c.worker_script()")?,
                web_url,
                cadswarm_python: python,
            };
            cfg.save()?;
            println!("wrote {}", HostConfig::path(&cfg.data_dir).display());
        }
        Cmd::Invite { kind, role } => {
            let cfg = HostConfig::load(&cli.data)?;
            let space = space::Space::connect(&cfg)?;
            let t = mint_invite(&space, &cfg, &kind, &role)?;
            let url = ticket::phone_url(&cfg.web_url, &t);
            println!("ticket: {t}\nphone:  {url}\n");
            let qr = qrcode::QrCode::new(url.as_bytes())?;
            println!("{}", qr.render::<qrcode::render::unicode::Dense1x2>().quiet_zone(true).build());
        }
        Cmd::Run => run(&HostConfig::load(&cli.data)?)?,
    }
    Ok(())
}

pub fn mint_invite(space: &space::Space, cfg: &HostConfig, kind: &str, role: &str) -> Result<String> {
    let token = ticket::new_token();
    space.create_invite(ticket::hash(&token), kind, role, 86_400)?;
    Ok(ticket::encode(&ticket::Ticket {
        v: 1,
        uri: cfg.uri.clone(),
        db: cfg.db.clone(),
        token,
        kind: kind.into(),
        role: role.into(),
        host_addr: String::new(),
    }))
}

fn run(cfg: &HostConfig) -> Result<()> {
    let space = space::Space::connect(cfg)?;
    let mut worker = worker::WorkerDriver::new(
        cfg.freecad_python.clone(),
        cfg.worker_script.clone(),
        cfg.doc_path(),
        Duration::from_secs(120),
    );
    executor::refresh(&space, &mut worker, &[])?;
    let mut orchestrator = briefs::Orchestrator::new(cfg.clone());
    println!("cadswarm-host running on {} / {} (fence {})", cfg.uri, cfg.db, space.fence());
    loop {
        if let Some(op) = space.queued_ops().into_iter().next() {
            if let Err(e) = executor::run_op(&space, &mut worker, cfg, &op) {
                eprintln!("[host] op {} failed to run: {e:#}", op.id);
            }
            continue;
        }
        orchestrator.tick(&space);
        std::thread::sleep(Duration::from_millis(100));
    }
}
```
Create `host/src/briefs.rs` and `host/src/agents.rs` with placeholders that compile until Task 6:
```rust
// host/src/briefs.rs
use crate::config::HostConfig;
use crate::space::Space;

pub struct Orchestrator {
    _cfg: HostConfig,
}

impl Orchestrator {
    pub fn new(cfg: HostConfig) -> Self {
        Orchestrator { _cfg: cfg }
    }
    pub fn tick(&mut self, _space: &Space) {}
}
```
```rust
// host/src/agents.rs
//! Headless Claude Code agents (Task 6).
```

- [ ] **Step 3: Build and run against A's local database**

```powershell
cargo build --manifest-path host/Cargo.toml
pip install -e .
cargo run --manifest-path host/Cargo.toml -- init --mode local --db cadswarm
cargo run --manifest-path host/Cargo.toml -- run
```
Expected: `cadswarm-host running on http://127.0.0.1:3000 / cadswarm (fence 2)` and no errors. The `mesh` call fails until C ships the worker's `mesh` query; until then comment out the mesh block in `refresh`, and restore it when C's PR lands.

- [ ] **Step 4: M1 check with C**

With C's `cadswarm setup --invite` working: `cargo run -- invite --kind agent` → give C the ticket → C's scratch agent calls `create_primitive`. Expected in the host log: no errors; in `spacetime sql --server local cadswarm "SELECT name, rev FROM cad_object"`: `Box | 1`.

- [ ] **Step 5: Commit**

```powershell
git add host
git commit -m "feat(host): init, invite and the op executor loop"
```

---

### Task 6: Brief orchestration and headless agents (M2)

**Files:**
- Modify: `host/src/briefs.rs`, `host/src/agents.rs`

**Interfaces:**
- Consumes: `Space`, `boards::{load_boards, seed_params}`, `mint_invite`, C's `cadswarm setup --invite --client claude --name --dir`, C's MCP tools `create_task(..., brief)` and `set_brief_plan(brief_id, plan_json)`.
- Produces: `briefs::next_action(&BriefState) -> Action` (pure, tested); `Orchestrator::tick(&Space)`; `agents::Agent::spawn(...)`, `Agent::alive()`, `Agent::kill()`.

- [ ] **Step 1: Write the failing state machine tests**

Replace `host/src/briefs.rs` with:
```rust
//! A brief's life: new → planning → building → (reviewing) → ready, driven by the host.

use crate::agents::Agent;
use crate::boards::{load_boards, seed_params, Board};
use crate::config::HostConfig;
use crate::space::Space;
use serde_json::json;
use std::collections::{BTreeMap, HashMap};

pub const MAX_WORKERS: usize = 3;
pub const MAX_REVIEW_ROUNDS: u32 = 3;

#[derive(Debug, Clone, Default)]
pub struct BriefState {
    pub status: String,
    pub seeding: bool,
    pub planner_alive: bool,
    pub workers_alive: usize,
    pub open: usize,
    pub active: usize,
    pub done: usize,
    pub exporting: bool,
    pub reviewer_enabled: bool,
    pub reviewer_alive: bool,
    pub review: Option<(u32, bool)>,
    pub reviewed_round: u32,
}

#[derive(Debug, PartialEq)]
pub enum Action {
    Wait,
    Seed,
    Plan,
    Build(usize),
    Review,
    Export,
    Fail(String),
}

pub fn next_action(s: &BriefState) -> Action {
    match s.status.as_str() {
        "new" if !s.seeding => Action::Seed,
        "new" => Action::Wait,
        "planning" if s.seeding || s.planner_alive => Action::Wait,
        "planning" if s.open + s.active + s.done == 0 => Action::Plan,
        "planning" => Action::Build(s.open.min(MAX_WORKERS)),
        "building" if s.open + s.active == 0 && s.done > 0 => {
            if s.exporting {
                Action::Wait
            } else if s.reviewer_enabled {
                Action::Review
            } else {
                Action::Export
            }
        }
        "building" if s.open > 0 && s.workers_alive == 0 => Action::Build(s.open.min(MAX_WORKERS)),
        "reviewing" if s.reviewer_alive || s.exporting => Action::Wait,
        "reviewing" => match s.review {
            Some((_, true)) => Action::Export,
            Some((round, false)) if round >= MAX_REVIEW_ROUNDS => {
                Action::Fail(format!("review failed {round} times; a person needs to steer"))
            }
            Some((round, false)) if round > s.reviewed_round => Action::Build(s.open.min(MAX_WORKERS).max(1)),
            _ => Action::Wait,
        },
        _ => Action::Wait,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn st(status: &str) -> BriefState {
        BriefState { status: status.into(), ..Default::default() }
    }

    #[test]
    fn a_new_brief_is_seeded_once() {
        assert_eq!(next_action(&st("new")), Action::Seed);
        assert_eq!(next_action(&BriefState { seeding: true, ..st("new") }), Action::Wait);
    }

    #[test]
    fn planning_starts_the_planner_then_builds_with_at_most_three_workers() {
        assert_eq!(next_action(&st("planning")), Action::Plan);
        assert_eq!(next_action(&BriefState { planner_alive: true, ..st("planning") }), Action::Wait);
        assert_eq!(next_action(&BriefState { open: 5, ..st("planning") }), Action::Build(3));
    }

    #[test]
    fn building_restarts_workers_if_they_all_died_and_exports_when_done() {
        assert_eq!(next_action(&BriefState { open: 2, ..st("building") }), Action::Build(2));
        assert_eq!(next_action(&BriefState { open: 2, workers_alive: 1, ..st("building") }), Action::Wait);
        assert_eq!(next_action(&BriefState { done: 3, ..st("building") }), Action::Export);
        assert_eq!(next_action(&BriefState { done: 3, reviewer_enabled: true, ..st("building") }), Action::Review);
        assert_eq!(next_action(&BriefState { done: 3, exporting: true, ..st("building") }), Action::Wait);
    }

    #[test]
    fn reviews_pass_to_export_fail_back_to_building_and_give_up_after_three() {
        assert_eq!(next_action(&BriefState { review: Some((1, true)), ..st("reviewing") }), Action::Export);
        assert_eq!(next_action(&BriefState { review: Some((1, false)), open: 1, ..st("reviewing") }), Action::Build(1));
        assert!(matches!(next_action(&BriefState { review: Some((3, false)), ..st("reviewing") }), Action::Fail(_)));
        assert_eq!(next_action(&BriefState { reviewer_alive: true, ..st("reviewing") }), Action::Wait);
    }
}
```

- [ ] **Step 2: Run to see them fail, then pass**

Run: `cargo test --manifest-path host/Cargo.toml briefs`
Expected first: compile errors from `crate::agents::Agent` (not yet written). Write Step 3, then rerun; expected: `4 passed`.

- [ ] **Step 3: Write `host/src/agents.rs`**

```rust
//! Headless Claude Code agents the host spawns for a brief. Each gets its own folder and
//! cadswarm identity (via `cadswarm setup --invite`), so its actions are attributed to it.

use crate::config::HostConfig;
use anyhow::{bail, Context, Result};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

pub const AGENT_TIMEOUT: Duration = Duration::from_secs(15 * 60);

pub struct Agent {
    pub name: String,
    child: Child,
    started: Instant,
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
            bail!("cadswarm setup for {name} failed: {}", String::from_utf8_lossy(&setup.stderr));
        }
        let log = std::fs::File::create(dir.join("agent.log"))?;
        let child = Command::new(if cfg!(windows) { "claude.cmd" } else { "claude" })
            .args(["-p", prompt, "--max-turns", "80", "--allowedTools", "mcp__cadswarm"])
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
```

- [ ] **Step 4: Implement the `Orchestrator`**

Append to `host/src/briefs.rs` (above `#[cfg(test)]`):
```rust
#[derive(Default)]
struct Runtime {
    seed_ops: Vec<String>,
    seeding: bool,
    planner: Option<Agent>,
    workers: Vec<Agent>,
    export_op: Option<String>,
    reviewer: Option<Agent>,
    reviewed_round: u32,
    spawned: usize,
}

pub struct Orchestrator {
    cfg: HostConfig,
    boards: BTreeMap<String, Board>,
    runs: HashMap<String, Runtime>,
    reviewer_enabled: bool,
}

fn op_id() -> String {
    format!("o_{}", &crate::ticket::new_token()[..12])
}

impl Orchestrator {
    pub fn new(cfg: HostConfig) -> Self {
        let path = std::env::current_dir().unwrap_or_default().join("boards").join("boards.json");
        let boards = load_boards(&path).unwrap_or_else(|e| {
            eprintln!("[host] no board library at {}: {e}", path.display());
            BTreeMap::new()
        });
        Orchestrator { cfg, boards, runs: HashMap::new(), reviewer_enabled: false }
    }

    pub fn tick(&mut self, space: &Space) {
        for b in space.briefs() {
            if b.status == "ready" || b.status == "failed" {
                continue;
            }
            if let Err(e) = self.step(space, &b) {
                eprintln!("[host] brief {}: {e:#}", b.id);
            }
        }
    }

    fn step(&mut self, space: &Space, b: &crate::space::BriefRow) -> anyhow::Result<()> {
        let tasks = space.tasks_for(&b.id);
        let review = space.latest_review(&b.id).map(|r| (r.round, r.passed));
        let rt = self.runs.entry(b.id.clone()).or_default();

        // Seed ops finished? Release Params so agents can add their own.
        if rt.seeding && rt.seed_ops.iter().all(|id| space.op(id).map(|o| o.status != "queued" && o.status != "running").unwrap_or(false)) {
            rt.seeding = false;
            space.release_objects(vec!["Params".into()])?;
        }
        // Export finished?
        if let Some(id) = rt.export_op.clone() {
            if let Some(op) = space.op(&id) {
                match op.status.as_str() {
                    "done" => {
                        let hash = serde_json::from_str::<serde_json::Value>(&op.result_json)?["hash"]
                            .as_str()
                            .unwrap_or("")
                            .to_string();
                        space.set_brief_stl(&b.id, &hash)?;
                        rt.export_op = None;
                    }
                    "failed" | "rejected" => {
                        space.set_brief(&b.id, "failed", "")?;
                        rt.export_op = None;
                    }
                    _ => {}
                }
            }
        }

        let state = BriefState {
            status: b.status.clone(),
            seeding: rt.seeding,
            planner_alive: rt.planner.as_mut().map(|a| a.alive()).unwrap_or(false),
            workers_alive: rt.workers.iter_mut().filter(|w| w.alive()).count(),
            open: tasks.iter().filter(|t| t.status == "open").count(),
            active: tasks.iter().filter(|t| t.status == "claimed" || t.status == "awaiting_takeover").count(),
            done: tasks.iter().filter(|t| t.status == "done").count(),
            exporting: rt.export_op.is_some(),
            reviewer_enabled: self.reviewer_enabled,
            reviewer_alive: rt.reviewer.as_mut().map(|a| a.alive()).unwrap_or(false),
            review,
            reviewed_round: rt.reviewed_round,
        };

        match next_action(&state) {
            Action::Wait => {}
            Action::Seed => {
                let params = self.boards.get(&b.board).map(seed_params).unwrap_or_default();
                space.lock_objects(vec!["Params".into()])?;
                for (alias, value) in &params {
                    let id = op_id();
                    space.submit_op(&id, "set_param", json!({"alias": alias, "value": value}).to_string())?;
                    rt.seed_ops.push(id);
                }
                rt.seeding = true;
                space.set_brief(&b.id, "planning", "")?;
            }
            Action::Plan => {
                let params = self.boards.get(&b.board).map(seed_params).unwrap_or_default();
                let label = self.boards.get(&b.board).map(|x| x.label.clone()).unwrap_or_else(|| "none given".into());
                let ticket = crate::mint_invite(space, &self.cfg, "agent", "lead")?;
                let name = format!("planner-{}", &b.id[2..]);
                rt.planner = Some(Agent::spawn(&self.cfg, &name, &ticket, &crate::agents::planner_prompt(&b.id, &b.text, &label, &params))?);
            }
            Action::Build(n) => {
                for _ in 0..n {
                    rt.spawned += 1;
                    let ticket = crate::mint_invite(space, &self.cfg, "agent", "member")?;
                    let name = format!("worker{}-{}", rt.spawned, &b.id[2..]);
                    rt.workers.push(Agent::spawn(&self.cfg, &name, &ticket, &crate::agents::worker_prompt(&b.id, rt.spawned))?);
                }
                if let Some((round, false)) = state.review {
                    rt.reviewed_round = round;
                }
                space.set_brief(&b.id, "building", "")?;
            }
            Action::Review => {
                // P1: the Droid reviewer is started here (python -m cadswarm.reviewer --brief <id>).
                space.set_brief(&b.id, "reviewing", "")?;
            }
            Action::Export => {
                let objects: Vec<String> = tasks.iter().flat_map(|t| t.objects.clone()).collect();
                let id = op_id();
                space.submit_op(&id, "export", json!({"format": "stl", "names": objects, "brief": b.id}).to_string())?;
                rt.export_op = Some(id);
            }
            Action::Fail(why) => {
                eprintln!("[host] brief {} failed: {why}", b.id);
                space.set_brief(&b.id, "failed", &json!({"error": why}).to_string())?;
            }
        }
        Ok(())
    }
}
```
Remove the placeholder `Orchestrator` from Task 5.

- [ ] **Step 5: Run the tests and build**

Run: `cargo test --manifest-path host/Cargo.toml` then `cargo build --manifest-path host/Cargo.toml`
Expected: all tests pass; builds.

- [ ] **Step 6: M2 end-to-end run**

Prerequisites: `claude` CLI installed and logged in on this machine (`npm install -g @anthropic-ai/claude-code`, then `claude` once to sign in); C's `--invite` setup and `set_brief_plan` tool merged; `boards/boards.json` present. Run the host, open the web app (D) or submit a brief from a script: from the repo root, `python -c "from cadswarm.space import demo_brief; demo_brief('esp32-s3-devkitc-1')"` (C provides it). Expected: host log shows seed ops, `planner-…` spawned, then up to three `worker…` agents, then the export; the brief ends `ready` with an `stl_hash`. Agent logs are in `host-data/agents/<name>/agent.log`.

- [ ] **Step 7: Commit**

```powershell
git add host
git commit -m "feat(host): brief orchestration with headless planner and worker agents"
```

---

### Task 7 (P1): Start the Droid reviewer

**Files:**
- Modify: `host/src/briefs.rs`, `host/src/main.rs`

**Interfaces:**
- Consumes: C's `python -m cadswarm.reviewer --brief <id> --round <n> --dir <reviewer folder>`, and a reviewer member set up by `cadswarm-host init-reviewer`.
- Produces: `Action::Review` spawns the reviewer; `reviewer_enabled` is true when `host-data/agents/reviewer/.cadswarm/config.json` exists.

- [ ] **Step 1: Add an `init-reviewer` command to `main.rs`**

Add to `enum Cmd`:
```rust
    /// Create the reviewer member (once) and its folder.
    InitReviewer,
```
and to the `match`:
```rust
        Cmd::InitReviewer => {
            let cfg = HostConfig::load(&cli.data)?;
            let space = space::Space::connect(&cfg)?;
            let ticket = mint_invite(&space, &cfg, "reviewer", "member")?;
            let dir = cfg.data_dir.join("agents").join("reviewer");
            std::fs::create_dir_all(&dir)?;
            let ok = std::process::Command::new(&cfg.cadswarm_python)
                .args(["-m", "cadswarm.cli", "setup", "--client", "droid", "--invite", &ticket, "--name", "reviewer", "--dir"])
                .arg(&dir)
                .status()?;
            anyhow::ensure!(ok.success(), "cadswarm setup for the reviewer failed");
            println!("reviewer ready in {}", dir.display());
        }
```

- [ ] **Step 2: Enable and spawn the reviewer in `briefs.rs`**

In `Orchestrator::new`, replace `reviewer_enabled: false` with:
```rust
reviewer_enabled: cfg.data_dir.join("agents").join("reviewer").join(".cadswarm").join("config.json").exists(),
```
and replace the `Action::Review` arm with:
```rust
            Action::Review => {
                let round = state.review.map(|(r, _)| r + 1).unwrap_or(1);
                let dir = self.cfg.data_dir.join("agents").join("reviewer");
                let log = std::fs::File::create(dir.join(format!("review-{}-{round}.log", b.id)))?;
                let child = std::process::Command::new(&self.cfg.cadswarm_python)
                    .args(["-m", "cadswarm.reviewer", "--brief", &b.id, "--round", &round.to_string(), "--dir"])
                    .arg(&dir)
                    .stdout(log.try_clone()?)
                    .stderr(log)
                    .spawn()?;
                rt.reviewer = Some(Agent::from_child("reviewer", child));
                space.set_brief(&b.id, "reviewing", "")?;
            }
```
Add to `impl Agent` in `agents.rs`:
```rust
    pub fn from_child(name: &str, child: Child) -> Agent {
        Agent { name: name.into(), child, started: Instant::now() }
    }
```

- [ ] **Step 3: Test the loop**

Run the host with `init-reviewer` done and C's reviewer merged; submit a brief. Expected: after the workers finish, the brief goes `reviewing`; a `review` row appears; on a pass the STL exports; on a fail the brief returns to `building` and a worker is spawned to fix the reviewer's tasks.

- [ ] **Step 4: Commit**

```powershell
git add host
git commit -m "feat(host): start the Droid reviewer when a brief's parts are built"
```

---

### Task 8 (P1): Spawn an agent for each open handoff

**Files:**
- Modify: `host/src/space.rs`, `host/src/agents.rs`, `host/src/briefs.rs`

**Interfaces:**
- Consumes: `v_handoff` (contract), C's MCP tool `accept_handoff(handoff_id)`.
- Produces: `Space::open_handoffs() -> Vec<HandoffRow { id, context_json }>`; `agents::handoff_prompt(id, context_json) -> String`; the orchestrator spawns one `handoff-<id>` agent per open handoff.

- [ ] **Step 1: Write the failing prompt test**

Append to the tests module in `host/src/agents.rs` (create `#[cfg(test)] mod tests { use super::*; ... }` if absent):
```rust
    #[test]
    fn the_handoff_prompt_leads_with_accepting_and_the_persons_note() {
        let ctx = r#"{"from":"carol","note":"make the lip 1 mm thinner","pointer":{"object":"Lid"},"locks":["Lid"],"tasks":[]}"#;
        let p = handoff_prompt("h_1", ctx);
        assert!(p.find("accept_handoff").unwrap() < p.find("make the lip 1 mm thinner").unwrap());
        assert!(p.contains("carol") && p.contains("Lid") && p.contains("h_1"));
    }
```

- [ ] **Step 2: Run to see it fail**

Run: `cargo test --manifest-path host/Cargo.toml handoff`
Expected: `cannot find function handoff_prompt`.

- [ ] **Step 3: Implement the prompt in `host/src/agents.rs`**

```rust
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
```

- [ ] **Step 4: Read open handoffs in `host/src/space.rs`**

```rust
#[derive(Debug, Clone)]
pub struct HandoffRow {
    pub id: String,
    pub context_json: String,
}
```
Add `"SELECT * FROM v_handoff"` to `QUERIES`, and to `impl Space`:
```rust
    pub fn open_handoffs(&self) -> Vec<HandoffRow> {
        self.conn
            .db
            .v_handoff()
            .iter()
            .filter(|h| h.status == "open")
            .map(|h| HandoffRow { id: h.id, context_json: h.context_json })
            .collect()
    }
```

- [ ] **Step 5: Spawn from the orchestrator in `host/src/briefs.rs`**

Add a field `handoffs: HashMap<String, Agent>` to `Orchestrator` (initialise with `HashMap::new()`), and at the start of `tick`:
```rust
        for h in space.open_handoffs() {
            if self.handoffs.contains_key(&h.id) {
                continue;
            }
            match crate::mint_invite(space, &self.cfg, "agent", "member").and_then(|ticket| {
                Agent::spawn(&self.cfg, &format!("handoff-{}", &h.id[2..]), &ticket,
                             &crate::agents::handoff_prompt(&h.id, &h.context_json))
            }) {
                Ok(agent) => { self.handoffs.insert(h.id.clone(), agent); }
                Err(e) => eprintln!("[host] handoff {}: {e:#}", h.id),
            }
        }
        self.handoffs.retain(|_, a| a.alive());
```
An agent that dies before accepting leaves the handoff `open`; `retain` drops it and the next tick spawns a fresh one.

- [ ] **Step 6: Run the tests, then try it**

Run: `cargo test --manifest-path host/Cargo.toml`
Expected: all pass. Then with the web app (D's Task 7): lock a part on the phone, tap it, "Hand off now" with a note. Expected in the host log: `handoff-…` spawned; in the app the agent's pointer starts on your part and the handoff card says it picked up where you left off.

- [ ] **Step 7: Commit**

```powershell
git add host
git commit -m "feat(host): spawn an agent to continue a person's handed-off work"
```
