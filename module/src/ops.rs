use crate::tables::*;
use crate::util::*;
use cadswarm_rules as rules;
use serde_json::{json, Map, Value};
use spacetimedb::{reducer, ReducerContext, Table};

fn parse_obj(s: &str) -> Result<Value, String> {
    if s.trim().is_empty() {
        return Ok(json!({}));
    }
    serde_json::from_str(s).map_err(|e| format!("bad_args: args_json is not JSON: {e}"))
}

#[reducer]
pub fn submit_op(ctx: &ReducerContext, fence: u32, op_id: String, kind: String, args_json: String, expected_revs_json: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let mutating = rules::is_mutating(&kind);
    if m.kind == "reviewer" && rules::is_write(&kind) {
        return Err("not_allowed: the reviewer can only inspect".into());
    }
    if op_id.is_empty() || ctx.db.op().id().find(&op_id).is_some() {
        return Err("bad_args: op_id must be new and non-empty".into());
    }
    let args = parse_obj(&args_json)?;
    let mine = locks_of(ctx, m.id);
    let ws = m.ws;
    rules::check_op(
        &kind,
        &args,
        &mine,
        |n| holder(ctx, ws, n),
        |n| ctx.db.cad_object().key().find(&lock_key(ws, n)).is_some(),
        m.id,
    )?;
    let expected = rules::parse_expected_revs(&expected_revs_json)?;
    rules::check_expected_revs(&expected, |n| object_rev(ctx, ws, n))?;
    let now = now_ms(ctx);
    ctx.db.op().insert(Op {
        id: op_id.clone(),
        ws,
        by: m.id,
        fence,
        kind: kind.clone(),
        args_json,
        expected_revs_json,
        allowed: mine,
        mutating,
        status: "queued".into(),
        result_json: String::new(),
        error_code: String::new(),
        error: String::new(),
        created_ms: now,
        started_ms: 0,
        finished_ms: 0,
    });
    let mut targets = rules::needed_locks(&kind, &args, |n| ctx.db.cad_object().key().find(&lock_key(ws, n)).is_some());
    if let Some(n) = rules::created_name(&kind, &args) {
        targets.push(n);
    }
    let mode = if mutating { "editing" } else { "inspecting" };
    let first = targets.first().cloned().unwrap_or_default();
    set_pointer(ctx, ws, m.id, &first, vec![], mode, &kind.replace('_', " "), &op_id);
    Ok(())
}

fn host_op(ctx: &ReducerContext, fence: u32, op_id: &str, want: &str) -> Result<(Member, Op), String> {
    let m = me(ctx, fence)?;
    require_kind(&m, "host")?;
    let op = ctx.db.op().id().find(&op_id.to_string()).ok_or_else(|| format!("not_found: No op {op_id}"))?;
    if op.status != want {
        return Err(format!("bad_args: op {op_id} is {}, not {want}", op.status));
    }
    Ok((m, op))
}

#[reducer]
pub fn start_op(ctx: &ReducerContext, fence: u32, op_id: String) -> Result<(), String> {
    let (_, op) = host_op(ctx, fence, &op_id, "queued")?;
    ctx.db.op().id().update(Op { status: "running".into(), started_ms: now_ms(ctx), ..op });
    Ok(())
}

#[reducer]
#[allow(clippy::too_many_arguments)]
pub fn complete_op(
    ctx: &ReducerContext,
    fence: u32,
    op_id: String,
    ok: bool,
    result_json: String,
    created: Vec<String>,
    modified: Vec<String>,
    deleted: Vec<String>,
    recomputed: Vec<String>,
    broken: Vec<String>,
    error_code: String,
    error: String,
) -> Result<(), String> {
    let (_, op) = host_op(ctx, fence, &op_id, "running")?;
    let now = now_ms(ctx);
    let (ws, by) = (op.ws, op.by);
    let actor = name_of(ctx, by);
    // The op is finished: rest the pointer; the agent's next tool call moves it again.
    set_pointer(ctx, ws, by, "", vec![], "idle", "", "");

    if !ok {
        let status = if error_code == "lock_violation" { "rejected" } else { "failed" };
        emit(ctx, ws, "op_failed", by, json!({"name": actor, "op": op.kind, "code": error_code, "error": error}));
        ctx.db.op().id().update(Op { status: status.into(), error_code, error, finished_ms: now, ..op });
        return Ok(());
    }
    let worker_result: Value = serde_json::from_str(&result_json).unwrap_or(Value::Null);
    if !op.mutating {
        ctx.db.op().id().update(Op { status: "done".into(), result_json, finished_ms: now, ..op });
        return Ok(());
    }

    // core.py Hub._commit
    for n in &created {
        bump_object(ctx, ws, n, by, now, true);
        set_lock(ctx, ws, n, by); // the creator owns it
    }
    for n in modified.iter().chain(recomputed.iter()) {
        bump_object(ctx, ws, n, by, now, false);
    }
    for n in &deleted {
        let key = lock_key(ws, n);
        ctx.db.cad_object().key().delete(&key);
        ctx.db.cad_lock().key().delete(&key);
        ctx.db.preview().key().delete(&key);
    }
    let notified = rules::owners_to_notify(&recomputed, by, |n| holder(ctx, ws, n).map(|h| h.id));
    let args: Value = serde_json::from_str(&op.args_json).unwrap_or(Value::Null);
    let what = if op.kind == "set_param" {
        format!("Params.{}={}", args["alias"].as_str().unwrap_or("?"), args["value"])
    } else {
        format!("{} on {:?}", op.kind, if modified.is_empty() { &created } else { &modified })
    };
    let mut notified_names = Map::new();
    for (owner, objs) in &notified {
        system_message(
            ctx,
            ws,
            &format!("member:{owner}"),
            format!("{actor}'s change ({what}) recomputed your objects {objs:?}. Check they still fit (check_interference)."),
            "notice",
            "",
        );
        notified_names.insert(name_of(ctx, *owner), json!(objs));
    }
    let mut revs = Map::new();
    for n in created.iter().chain(modified.iter()).chain(recomputed.iter()) {
        revs.insert(n.clone(), json!(object_rev(ctx, ws, n).0));
    }
    let seq = emit(
        ctx,
        ws,
        "op",
        by,
        json!({"name": actor, "op": op.kind, "created": created, "modified": modified,
               "deleted": deleted, "recomputed": recomputed, "broken": broken}),
    );
    let full = json!({
        "result": worker_result, "created": created, "modified": modified, "deleted": deleted,
        "recomputed": recomputed, "broken": broken, "revs": revs, "seq": seq, "notified": notified_names,
    });
    ctx.db.op().id().update(Op { status: "done".into(), result_json: full.to_string(), finished_ms: now, ..op });
    Ok(())
}

fn bump_object(ctx: &ReducerContext, ws: u64, name: &str, actor: u64, now: u64, created: bool) {
    let key = lock_key(ws, name);
    match ctx.db.cad_object().key().find(&key) {
        Some(o) if !created => {
            let rev = o.rev + 1;
            ctx.db.cad_object().key().update(CadObject { rev, last_actor: actor, updated_ms: now, ..o });
        }
        Some(o) => {
            ctx.db.cad_object().key().update(CadObject { rev: 1, last_actor: actor, updated_ms: now, ..o });
        }
        None => {
            ctx.db.cad_object().insert(blank_object(key, ws, name, actor, now));
        }
    }
}

fn blank_object(key: String, ws: u64, name: &str, actor: u64, now: u64) -> CadObject {
    CadObject {
        key,
        ws,
        name: name.into(),
        label: name.into(),
        type_id: String::new(),
        rev: 1,
        placement_json: String::new(),
        bbox: vec![],
        volume: 0.0,
        visible: true,
        broken: false,
        uses: vec![],
        used_by: vec![],
        expressions_json: String::new(),
        last_actor: actor,
        updated_ms: now,
    }
}

fn strings(v: &Value) -> Vec<String> {
    v.as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()).unwrap_or_default()
}

/// Upsert object details and params from the worker's `status` query. Revisions are untouched:
/// only `complete_op` moves them.
#[reducer]
pub fn sync_objects(ctx: &ReducerContext, fence: u32, objects_json: String, params_json: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    require_kind(&m, "host")?;
    let ws = m.ws;
    let now = now_ms(ctx);
    let objects: Vec<Value> = serde_json::from_str(&objects_json).map_err(|e| format!("bad_args: objects_json: {e}"))?;
    let mut seen = Vec::new();
    for o in &objects {
        let Some(name) = o["name"].as_str() else { continue };
        seen.push(name.to_string());
        let key = lock_key(ws, name);
        let base = ctx.db.cad_object().key().find(&key).unwrap_or_else(|| blank_object(key.clone(), ws, name, 0, now));
        let row = CadObject {
            label: o["label"].as_str().unwrap_or(name).into(),
            type_id: o["type"].as_str().unwrap_or("").into(),
            placement_json: if o["placement"].is_null() { String::new() } else { o["placement"].to_string() },
            bbox: o["bbox"].as_array().map(|a| a.iter().filter_map(|x| x.as_f64()).collect()).unwrap_or_default(),
            volume: o["volume"].as_f64().unwrap_or(0.0),
            visible: o["visible"].as_bool().unwrap_or(true),
            broken: o["broken"].as_bool().unwrap_or(false),
            uses: strings(&o["uses"]),
            used_by: strings(&o["used_by"]),
            expressions_json: if o["expressions"].is_null() { String::new() } else { o["expressions"].to_string() },
            ..base
        };
        if ctx.db.cad_object().key().find(&key).is_some() {
            ctx.db.cad_object().key().update(row);
        } else {
            ctx.db.cad_object().insert(row);
        }
    }
    let gone: Vec<CadObject> = ctx.db.cad_object().ws().filter(&ws).filter(|o| !seen.contains(&o.name)).collect();
    for o in gone {
        ctx.db.preview().key().delete(&o.key);
        ctx.db.cad_object().key().delete(&o.key);
    }

    let params: Map<String, Value> = serde_json::from_str(&params_json).map_err(|e| format!("bad_args: params_json: {e}"))?;
    for (alias, v) in &params {
        let key = lock_key(ws, alias);
        let row = Param {
            key: key.clone(),
            ws,
            alias: alias.clone(),
            value: v.as_str().map(str::to_string).unwrap_or_else(|| v.to_string()),
            num: v.as_f64().unwrap_or(0.0),
            updated_ms: now,
        };
        if ctx.db.param().key().find(&key).is_some() {
            ctx.db.param().key().update(row);
        } else {
            ctx.db.param().insert(row);
        }
    }
    let stale: Vec<Param> = ctx.db.param().ws().filter(&ws).filter(|p| !params.contains_key(&p.alias)).collect();
    for p in stale {
        ctx.db.param().key().delete(&p.key);
    }
    Ok(())
}

#[reducer]
pub fn put_preview(ctx: &ReducerContext, fence: u32, object: String, mesh: Vec<u8>) -> Result<(), String> {
    let m = me(ctx, fence)?;
    require_kind(&m, "host")?;
    let key = lock_key(m.ws, &object);
    let rev = object_rev(ctx, m.ws, &object).0;
    let row = Preview { key: key.clone(), ws: m.ws, object, rev, mesh };
    if ctx.db.preview().key().find(&key).is_some() {
        ctx.db.preview().key().update(row);
    } else {
        ctx.db.preview().insert(row);
    }
    Ok(())
}

#[reducer]
pub fn put_artifact(ctx: &ReducerContext, fence: u32, hash: String, name: String, mime: String, bytes: Vec<u8>, brief: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    require_kind(&m, "host")?;
    let size = bytes.len() as u64;
    let row = Artifact { hash: hash.clone(), ws: m.ws, name, mime, size, brief, created_ms: now_ms(ctx) };
    if ctx.db.artifact().hash().find(&hash).is_some() {
        ctx.db.artifact().hash().update(row);
    } else {
        ctx.db.artifact().insert(row);
    }
    if !bytes.is_empty() {
        let blob = ArtifactBlob { hash: hash.clone(), ws: m.ws, bytes };
        if ctx.db.artifact_blob().hash().find(&hash).is_some() {
            ctx.db.artifact_blob().hash().update(blob);
        } else {
            ctx.db.artifact_blob().insert(blob);
        }
    }
    Ok(())
}
