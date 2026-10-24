//! Runs one queued op in FreeCAD (or the Python helper) and writes the outcome back to SpacetimeDB.

use crate::commit::{completion, touched};
use crate::config::HostConfig;
use crate::space::{OpRow, Space};
use crate::worker::{WorkerDriver, WorkerError};
use anyhow::Result;
use base64::Engine;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

const MAX_INLINE_ARTIFACT: usize = 5 * 1024 * 1024;

/// Ops the Python helper answers (plan 00 op kinds). Everything else goes to the FreeCAD worker.
pub const HELPER_KINDS: &[&str] = &[
    "list_files", "read_file", "file_history", "save_file", "make_folder", "move_file", "delete_file",
    "restore_file", "snapshot", "storage_status", "prune_storage", "lookup_board", "recall_designs",
];
pub const MAX_INLINE_RESULT: usize = 32 * 1024;

pub fn is_helper_kind(kind: &str) -> bool {
    HELPER_KINDS.contains(&kind)
}

/// The args the helper gets: the op's own args plus who asked (file authorship, human-only pruning).
pub fn helper_args(mut args: Value, by: u64, name: &str, kind: &str) -> Value {
    if !args.is_object() {
        args = json!({});
    }
    args["_actor"] = json!({"id": by, "name": name, "kind": kind});
    args
}

/// Some((hash, bytes)) when the result is too large to travel in v_op.
pub fn spill(result_json: &str) -> Option<(String, Vec<u8>)> {
    (result_json.len() > MAX_INLINE_RESULT)
        .then(|| (hex::encode(Sha256::digest(result_json.as_bytes())), result_json.as_bytes().to_vec()))
}

/// What helper ops answer while `cadswarm.config.helper_script()` does not exist (plan 03 Task 5).
/// Delete this fallback once the helper is on main.
pub fn helper_unavailable() -> WorkerError {
    WorkerError {
        code: "helper_unavailable".into(),
        message: "the host's Python helper is not running, so file and knowledge ops are unavailable".into(),
    }
}

pub fn run_op(
    space: &Space,
    worker: &mut WorkerDriver,
    helper: Option<&mut WorkerDriver>,
    cfg: &HostConfig,
    op: &OpRow,
) -> Result<()> {
    space.start_op(&op.id)?;
    let mut args: Value = serde_json::from_str(&op.args_json).unwrap_or(json!({}));
    let reply = match op.kind.as_str() {
        k if is_helper_kind(k) => match helper {
            Some(h) => {
                let (name, kind) = space.member_name_kind(op.by);
                h.call(k, &helper_args(args, op.by, &name, &kind), None)
            }
            None => Err(helper_unavailable()),
        },
        "export" => export(space, worker, cfg, op, &args),
        "printability" => {
            if args.get("bed").is_none() {
                args["bed"] = json!(space.bed());
            }
            worker.call("printability", &args, None)
        }
        _ if op.mutating => worker.call(&op.kind, &args, Some(&op.allowed)),
        _ => worker.call(&op.kind, &args, None),
    };
    let mut done = completion(reply);
    if let Some((hash, bytes)) = spill(&done.result_json) {
        space.put_artifact(&hash, &format!("op-result:{}", op.id), "application/json", bytes, "")?;
        done.result_json = json!({"blob": hash}).to_string();
    }
    space.complete_op(&op.id, &done)?;
    eprintln!(
        "[host] op {} {} -> {}",
        op.id,
        op.kind,
        if done.ok { "done".to_string() } else { format!("{}: {}", done.error_code, done.error) }
    );
    if done.ok && op.mutating {
        // The op is already committed; a refresh failure only leaves object details stale.
        if let Err(e) = refresh(space, worker, &touched(&done)) {
            eprintln!("[host] refresh after op {} failed: {e:#}", op.id);
        }
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
/// A failing `mesh` query (the worker may not have it yet) is logged and previews are skipped.
pub fn refresh(space: &Space, worker: &mut WorkerDriver, names: &[String]) -> Result<()> {
    let status = worker.call("status", &json!({}), None).map_err(|e| anyhow::anyhow!("status: {e}"))?;
    space.sync_objects(status["result"]["objects"].to_string(), status["result"]["params"].to_string())?;
    let names: Vec<Value> = if names.is_empty() {
        status["result"]["objects"].as_array().cloned().unwrap_or_default().iter().map(|o| o["name"].clone()).collect()
    } else {
        names.iter().map(|n| json!(n)).collect()
    };
    if names.is_empty() {
        return Ok(());
    }
    let meshes = match worker.call("mesh", &json!({"names": names, "tolerance": 0.2, "max_bytes": 200_000}), None) {
        Ok(m) => m,
        Err(e) => {
            eprintln!("[host] mesh query failed, skipping previews: {e}");
            return Ok(());
        }
    };
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

/// Tell the FreeCAD guide on this computer that hosting is up (plan 03 Task 6 reads it).
pub fn write_status(cfg: &HostConfig, worker: &mut WorkerDriver) -> Result<()> {
    let ping = worker.call("ping", &json!({}), None).unwrap_or(json!({}));
    let dir = cfg.data_dir.join(".cadswarm-team");
    std::fs::create_dir_all(&dir)?;
    let status = json!({"pid": std::process::id(), "uri": cfg.uri, "db": cfg.db, "ready": true,
                        "freecad": ping["result"]["freecad"], "script_sandbox": ping["result"]["script_sandbox"]});
    std::fs::write(dir.join("host-status.json"), serde_json::to_string_pretty(&status)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commit::completion;
    use serde_json::json;

    #[test]
    fn file_and_knowledge_ops_go_to_the_helper() {
        assert!(is_helper_kind("save_file") && is_helper_kind("prune_storage") && is_helper_kind("lookup_board"));
        assert!(!is_helper_kind("create_primitive") && !is_helper_kind("brep") && !is_helper_kind("export"));
    }

    #[test]
    fn helper_args_carry_the_actor() {
        let a = helper_args(json!({"path": "notes.md"}), 7, "carol", "human");
        assert_eq!(a["path"], "notes.md");
        assert_eq!(a["_actor"], json!({"id": 7, "name": "carol", "kind": "human"}));
    }

    #[test]
    fn only_large_results_spill() {
        assert!(spill("{\"ok\":true}").is_none());
        let big = format!("{{\"x\":\"{}\"}}", "a".repeat(40_000));
        let (hash, bytes) = spill(&big).unwrap();
        assert_eq!(hash.len(), 64);
        assert_eq!(bytes, big.as_bytes());
    }

    #[test]
    fn without_a_helper_helper_ops_fail_with_helper_unavailable() {
        let c = completion(Err(helper_unavailable()));
        assert!(!c.ok);
        assert_eq!(c.error_code, "helper_unavailable");
    }
}
