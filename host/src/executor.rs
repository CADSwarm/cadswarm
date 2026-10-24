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
    "board_spec", "index_design",
    // The team's learned design style (cadswarm/style_memory.py).
    "style_context", "list_style", "teach_style", "resolve_style", "learn_style",
    "fea_run", "fea_status", "fea_publish", "fea_reports",
    "library_add", "library_import_url", "library_import_pack", "library_update", "library_delete",
    "library_search", "library_recommend", "library_get", "library_export_pack", "standard_parts",
    "suggest_settings",
];
pub const MAX_INLINE_RESULT: usize = 32 * 1024;
/// The artifact holding the team-wide style block that every agent's per-turn context shows.
pub const STYLE_PROFILE: &str = "style-profile";

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

/// What v_op keeps of a spilled result: the blob's hash, plus the library rows complete_op must still see.
pub fn spilled_result(result_json: &str, hash: &str) -> String {
    let full: Value = serde_json::from_str(result_json).unwrap_or(Value::Null);
    let mut kept = json!({"blob": hash});
    for k in ["_components", "_component_deletes"] {
        if let Some(v) = full.get(k) {
            kept[k] = v.clone();
        }
    }
    kept.to_string()
}

/// Removes the base64 preview STL from a worker analysis and decodes it.
pub fn take_mesh(analysis: &mut Value) -> Option<Vec<u8>> {
    let b64 = analysis.as_object_mut()?.remove("mesh")?;
    base64::engine::general_purpose::STANDARD.decode(b64.as_str()?).ok()
}

/// A library write's helper reply names parts FreeCAD should measure (`_analyze`). Measure each in the
/// worker, let the helper merge the results (`library_finalize`), and return the reply carrying the
/// finished rows. Preview meshes are collected for put_component_preview once the op is complete.
pub fn analyze_library_parts(
    worker: &mut WorkerDriver,
    helper: &mut WorkerDriver,
    settings: &Value,
    reply: Result<Value, WorkerError>,
    previews: &mut Vec<(String, Vec<u8>)>,
) -> Result<Value, WorkerError> {
    let mut v = reply?;
    let todo = match v["result"].as_object_mut().and_then(|r| r.remove("_analyze")) {
        Some(Value::Array(items)) if !items.is_empty() => items,
        _ => return Ok(v),
    };
    let mut items = Vec::new();
    for t in todo {
        let id = t["id"].as_str().unwrap_or_default().to_string();
        let args = json!({"path": t["path"], "format": t["format"], "_settings": settings});
        let analysis = match worker.call("analyze_component", &args, None) {
            Ok(r) => {
                let mut a = r["result"].clone();
                if let Some(mesh) = take_mesh(&mut a) {
                    previews.push((id.clone(), mesh));
                }
                a
            }
            Err(e) => {
                eprintln!("[host] analyze_component {id} failed: {e}");
                json!({"error": e.message})
            }
        };
        items.push(json!({"id": id, "analysis": analysis}));
    }
    match helper.call("library_finalize", &json!({"items": items, "_settings": settings}), None) {
        Ok(f) => {
            for k in ["_components", "components", "suggest"] {
                if let Some(x) = f["result"].get(k) {
                    v["result"][k] = x.clone();
                }
            }
        }
        Err(e) => eprintln!("[host] library_finalize failed, rows keep no measurements: {e}"),
    }
    Ok(v)
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
    if !args.is_object() {
        args = json!({});
    }
    let prior_actors = op.mutating.then(|| space.last_actors(&op_targets(&args)));
    // Every op sees the team's precision settings (mesh deviation, clash volume, fits...).
    let settings = space.settings_rows();
    args["_settings"] = settings.clone();
    let mut previews: Vec<(String, Vec<u8>)> = Vec::new();
    let mut helper = helper;
    let style_args = json!({"kind": op.kind, "args": strip_settings(&args)});
    let reply = match op.kind.as_str() {
        k if is_helper_kind(k) => match helper.as_deref_mut() {
            Some(h) => {
                let (name, kind) = space.member_name_kind(op.by);
                let reply = h.call(k, &helper_args(args, op.by, &name, &kind), None);
                analyze_library_parts(worker, h, &settings, reply, &mut previews)
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
        done.result_json = spilled_result(&done.result_json, &hash);
    }
    space.complete_op(&op.id, &done)?;
    // complete_op has created the library rows; their previews can go in now.
    for (id, mesh) in previews {
        if let Err(e) = space.put_component_preview(&id, mesh) {
            eprintln!("[host] preview for {id} failed: {e:#}");
        }
    }
    eprintln!(
        "[host] op {} {} -> {}",
        op.id,
        op.kind,
        if done.ok { "done".to_string() } else { format!("{}: {}", done.error_code, done.error) }
    );
    // A style op (teach, confirm, learn) changes what every agent's context should say.
    if let Ok(result) = serde_json::from_str::<Value>(&done.result_json) {
        publish_style_profile(space, &result);
    }
    let mut objects = Value::Array(vec![]);
    if done.ok && op.mutating {
        // The op is already committed; a refresh failure only leaves object details stale.
        match refresh(space, worker, &touched(&done)) {
            Ok(all) => objects = touched_objects(&all, &touched(&done)),
            Err(e) => eprintln!("[host] refresh after op {} failed: {e:#}", op.id),
        }
    }
    if let (Some(h), Some(prior)) = (helper, prior_actors) {
        let (name, kind) = space.member_name_kind(op.by);
        let mut obs = style_args;
        obs["ok"] = json!(done.ok);
        obs["error_code"] = json!(done.error_code);
        obs["actor"] = json!({"id": op.by, "name": name, "kind": kind});
        obs["objects"] = objects;
        obs["prior_actors"] = prior;
        observe(space, h, "observe_op", &obs);
    }
    Ok(())
}

/// The objects an op names: what it edits, combines or creates.
pub fn op_targets(args: &Value) -> Vec<String> {
    ["name", "base", "tool"].iter().filter_map(|k| args[*k].as_str().map(str::to_string)).collect()
}

fn strip_settings(args: &Value) -> Value {
    let mut a = args.clone();
    if let Some(o) = a.as_object_mut() {
        o.retain(|k, _| !k.starts_with('_'));
    }
    a
}

/// The worker's summaries of the touched objects, in the shape the style memory reads.
pub fn touched_objects(all: &Value, names: &[String]) -> Value {
    let list = all.as_array().cloned().unwrap_or_default();
    Value::Array(
        list.iter()
            .filter(|o| o["name"].as_str().is_some_and(|n| names.iter().any(|t| t == n)))
            .map(|o| json!({"name": o["name"], "type_id": o["type"], "bbox": o["bbox"], "volume": o["volume"],
                            "expressions": o["expressions"]}))
            .collect(),
    )
}

/// Tell the helper's style memory what happened. Best effort: learning must never fail the work.
pub fn observe(space: &Space, helper: &mut WorkerDriver, kind: &str, args: &Value) {
    match helper.call(kind, args, None) {
        Ok(r) => publish_style_profile(space, &r["result"]),
        Err(e) => eprintln!("[host] {kind}: {e}"),
    }
}

/// When the style was re-learned, refresh the artifact the hub reads into every agent's context.
pub fn publish_style_profile(space: &Space, result: &Value) {
    let Some(text) = result.get("profile").and_then(Value::as_str) else { return };
    // An empty blob would leave the old profile in place, so "nothing to follow" is one space.
    let bytes = if text.is_empty() { b" ".to_vec() } else { text.as_bytes().to_vec() };
    if let Err(e) = space.put_artifact(STYLE_PROFILE, STYLE_PROFILE, "text/plain", bytes, "") {
        eprintln!("[host] publishing the style profile failed: {e:#}");
    }
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
/// Returns every object's summary from the worker (the style memory reads the touched ones).
pub fn refresh(space: &Space, worker: &mut WorkerDriver, names: &[String]) -> Result<Value> {
    let status = worker.call("status", &json!({}), None).map_err(|e| anyhow::anyhow!("status: {e}"))?;
    let objects = status["result"]["objects"].clone();
    space.sync_objects(objects.to_string(), status["result"]["params"].to_string())?;
    let names: Vec<Value> = if names.is_empty() {
        status["result"]["objects"].as_array().cloned().unwrap_or_default().iter().map(|o| o["name"].clone()).collect()
    } else {
        names.iter().map(|n| json!(n)).collect()
    };
    if names.is_empty() {
        return Ok(objects);
    }
    // The team's mesh_deviation setting (default 0.2 mm) sets the preview tolerance.
    let args = json!({"names": names, "max_bytes": 200_000, "_settings": space.settings_rows()});
    let meshes = match worker.call("mesh", &args, None) {
        Ok(m) => m,
        Err(e) => {
            eprintln!("[host] mesh query failed, skipping previews: {e}");
            return Ok(objects);
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
    Ok(objects)
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
        assert!(is_helper_kind("board_spec") && is_helper_kind("index_design") && is_helper_kind("recall_designs"));
        assert!(is_helper_kind("fea_run") && is_helper_kind("fea_publish") && !is_helper_kind("faces"));
        assert!(!is_helper_kind("create_primitive") && !is_helper_kind("brep") && !is_helper_kind("export"));
    }

    #[test]
    fn style_ops_go_to_the_helper_and_observing_is_not_an_op() {
        for k in ["style_context", "list_style", "teach_style", "resolve_style", "learn_style"] {
            assert!(is_helper_kind(k), "{k}");
        }
        // The host reports ops to the style memory itself; nobody may submit an observation.
        assert!(!is_helper_kind("observe_op") && !is_helper_kind("observe_brief"));
    }

    #[test]
    fn the_style_memory_sees_the_objects_an_op_touched_and_names() {
        let all = json!([{"name": "Lid", "type": "Part::Box", "bbox": [0, 0, 0, 1, 1, 1], "volume": 1.0,
                          "expressions": {"Height": "Params.lid_h"}},
                         {"name": "Base", "type": "Part::Box"}]);
        let got = touched_objects(&all, &["Lid".to_string()]);
        assert_eq!(got, json!([{"name": "Lid", "type_id": "Part::Box", "bbox": [0, 0, 0, 1, 1, 1], "volume": 1.0,
                                "expressions": {"Height": "Params.lid_h"}}]));
        assert_eq!(op_targets(&json!({"kind": "cut", "name": "Shell", "base": "Box", "tool": "Hole"})),
                   vec!["Shell", "Box", "Hole"]);
        assert_eq!(strip_settings(&json!({"alias": "wall", "_settings": [1]})), json!({"alias": "wall"}));
    }

    #[test]
    fn library_ops_go_to_the_helper_but_measuring_and_placing_parts_go_to_freecad() {
        for k in ["library_add", "library_import_url", "library_import_pack", "library_delete", "library_search",
                  "library_recommend", "library_get", "library_export_pack", "standard_parts", "suggest_settings"] {
            assert!(is_helper_kind(k), "{k}");
        }
        for k in ["analyze_component", "insert_component", "insert_standard_part"] {
            assert!(!is_helper_kind(k), "{k}");
        }
    }

    #[test]
    fn a_spilled_library_result_keeps_its_rows_for_complete_op() {
        let big = json!({"_components": [{"id": "c_a"}], "_component_deletes": ["c_b"], "x": "a".repeat(40_000)});
        let kept: Value = serde_json::from_str(&spilled_result(&big.to_string(), "h1")).unwrap();
        assert_eq!(kept, json!({"blob": "h1", "_components": [{"id": "c_a"}], "_component_deletes": ["c_b"]}));
        let plain: Value = serde_json::from_str(&spilled_result(&json!({"x": 1}).to_string(), "h2")).unwrap();
        assert_eq!(plain, json!({"blob": "h2"}));
    }

    #[test]
    fn the_preview_mesh_is_taken_out_of_an_analysis() {
        let mesh = base64::engine::general_purpose::STANDARD.encode(b"stl");
        let mut a = json!({"bbox": [0, 0, 0, 1, 1, 1], "mesh": mesh});
        assert_eq!(take_mesh(&mut a), Some(b"stl".to_vec()));
        assert!(a.get("mesh").is_none() && a.get("bbox").is_some());
        assert_eq!(take_mesh(&mut json!({"bbox": []})), None);
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
