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
    env: Vec<(String, String)>,
}

impl WorkerDriver {
    pub fn new(python: PathBuf, script: PathBuf, doc: PathBuf, timeout: Duration) -> Self {
        WorkerDriver { python, script, doc, timeout, running: None, next_id: 0, env: vec![] }
    }

    /// An extra environment variable for the child (e.g. the helper learns FreeCAD's Python for FEA).
    pub fn with_env(mut self, key: &str, value: impl Into<String>) -> Self {
        self.env.push((key.to_string(), value.into()));
        self
    }

    fn start(&mut self) -> Result<(), WorkerError> {
        let bin_dir = self.python.parent().map(|p| p.to_path_buf()).unwrap_or_default();
        let mut child = Command::new(&self.python)
            .arg(&self.script)
            .arg(&self.doc)
            .env("CADSWARM_FREECAD_BIN", bin_dir)
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUNBUFFERED", "1")
            .envs(self.env.iter().map(|(k, v)| (k.as_str(), v.as_str())))
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

#[cfg(test)]
mod tests {
    use super::*;

    fn driver(timeout_ms: u64) -> WorkerDriver {
        let script = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests").join("fake_worker.py");
        let default = if cfg!(windows) { "python" } else { "python3" };
        let python = PathBuf::from(std::env::var("PYTHON").unwrap_or_else(|_| default.into()));
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
