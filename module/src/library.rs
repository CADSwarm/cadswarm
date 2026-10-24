//! The components library (rows mirrored from the host's library/ folder) and team precision settings.
//! People change settings; AI helpers suggest them and a person accepts (rules::can_change_settings).

use crate::tables::*;
use crate::util::*;
use cadswarm_rules as rules;
use serde_json::{json, Value};
use spacetimedb::{reducer, ReducerContext, Table};

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or_default().chars().take(2000).collect()
}

fn strings(v: &Value, k: &str) -> Vec<String> {
    v.get(k)
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|x| x.as_str().map(|t| t.chars().take(60).collect())).take(30).collect())
        .unwrap_or_default()
}

fn json_field(v: &Value, k: &str) -> String {
    match v.get(k) {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(t)) => t.clone(),
        Some(other) => other.to_string(),
    }
}

/// Upsert library rows and drop deleted ones from a library_* op's result (called by complete_op).
/// Returns (names added or updated, names removed) for the team event.
pub fn apply_component_changes(ctx: &ReducerContext, ws: u64, kind: &str, result: &Value) -> (Vec<String>, Vec<String>) {
    let changes = rules::component_changes(kind, result);
    let now = now_ms(ctx);
    let mut added = Vec::new();
    for c in &changes.upserts {
        let id = s(c, "id");
        let existing = ctx.db.component().id().find(&id).filter(|e| e.ws == ws);
        let row = Component {
            id: id.clone(),
            ws,
            name: s(c, "name"),
            category: s(c, "category"),
            tags: strings(c, "tags"),
            description: s(c, "description"),
            source: s(c, "source"),
            source_url: s(c, "source_url"),
            license: s(c, "license"),
            author: s(c, "author"),
            format: s(c, "format"),
            hash: s(c, "hash"),
            size: c.get("size").and_then(Value::as_u64).unwrap_or(0),
            version: c.get("version").and_then(Value::as_u64).unwrap_or(1) as u32,
            bbox: c
                .get("bbox")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(Value::as_f64).take(6).collect())
                .unwrap_or_default(),
            volume: c.get("volume").and_then(Value::as_f64).unwrap_or(0.0),
            features_json: json_field(c, "features"),
            std_json: json_field(c, "std"),
            suggest_json: json_field(c, "suggest"),
            uses: existing.as_ref().map(|e| e.uses).unwrap_or(0),
            created_ms: existing.as_ref().map(|e| e.created_ms).unwrap_or(now),
            updated_ms: now,
        };
        added.push(row.name.clone());
        if existing.is_some() {
            ctx.db.component().id().update(row);
        } else if ctx.db.component().id().find(&id).is_none() {
            ctx.db.component().insert(row);
        }
    }
    let mut removed = Vec::new();
    for id in &changes.deletes {
        if let Some(c) = ctx.db.component().id().find(id).filter(|c| c.ws == ws) {
            removed.push(c.name.clone());
            ctx.db.component().id().delete(id);
            ctx.db.component_preview().id().delete(id);
        }
    }
    (added, removed)
}

/// An insert op placed a library part: count it, so recommendations favour what the team uses.
pub fn count_use(ctx: &ReducerContext, ws: u64, kind: &str, args: &Value) {
    if let Some(id) = rules::inserted_component(kind, args) {
        if let Some(c) = ctx.db.component().id().find(&id).filter(|c| c.ws == ws) {
            let uses = c.uses + 1;
            ctx.db.component().id().update(Component { uses, ..c });
        }
    }
}

#[reducer]
pub fn put_component_preview(ctx: &ReducerContext, fence: u32, id: String, mesh: Vec<u8>) -> Result<(), String> {
    let m = me(ctx, fence)?;
    require_kind(&m, "host")?;
    if ctx.db.component().id().find(&id).filter(|c| c.ws == m.ws).is_none() {
        return Err(format!("not_found: No component {id}"));
    }
    let row = ComponentPreview { id: id.clone(), ws: m.ws, mesh };
    if ctx.db.component_preview().id().find(&id).is_some() {
        ctx.db.component_preview().id().update(row);
    } else {
        ctx.db.component_preview().insert(row);
    }
    Ok(())
}

fn setting_key(ws: u64, setting: &str) -> String {
    format!("{ws}:{setting}")
}

/// Store a validated value and, for settings geometry binds to, queue a set_param so Params.<key> follows.
fn apply_setting(ctx: &ReducerContext, m: &Member, setting: &str, num: f64, text: &str) -> Result<(), String> {
    let spec = rules::check_setting(setting, num, text)?;
    let now = now_ms(ctx);
    let key = setting_key(m.ws, setting);
    let row = ProjectSetting {
        key: key.clone(),
        ws: m.ws,
        setting: setting.into(),
        num,
        text: text.into(),
        set_by: m.id,
        updated_ms: now,
    };
    if ctx.db.project_setting().key().find(&key).is_some() {
        ctx.db.project_setting().key().update(row);
    } else {
        ctx.db.project_setting().insert(row);
    }
    if spec.param {
        queue_param(ctx, m, setting, num, now);
    }
    let value = if spec.kind == "choice" { json!(text) } else { json!(num) };
    emit(ctx, m.ws, "setting_changed", m.id, json!({"name": m.name, "setting": setting, "value": value, "unit": spec.unit}));
    Ok(())
}

fn queue_param(ctx: &ReducerContext, m: &Member, alias: &str, value: f64, now: u64) {
    // Ops are never deleted, so the row count keeps the id unique within this timestamp.
    let id = format!("o_setting_{now}_{}", ctx.db.op().count());
    ctx.db.op().insert(Op {
        id,
        ws: m.ws,
        by: m.id,
        fence: m.fence,
        kind: "set_param".into(),
        args_json: json!({"alias": alias, "value": value, "note": "team setting"}).to_string(),
        expected_revs_json: String::new(),
        allowed: vec!["Params".into()],
        mutating: true,
        status: "queued".into(),
        result_json: String::new(),
        error_code: String::new(),
        error: String::new(),
        created_ms: now,
        started_ms: 0,
        finished_ms: 0,
    });
}

fn require_person(m: &Member) -> Result<(), String> {
    if rules::can_change_settings(&m.kind) {
        Ok(())
    } else {
        Err("not_allowed: only a person changes team settings; use suggest_setting and a teammate will accept it".into())
    }
}

#[reducer]
pub fn set_setting(ctx: &ReducerContext, fence: u32, setting: String, num: f64, text: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    require_person(&m)?;
    apply_setting(ctx, &m, &setting, num, &text)
}

#[reducer]
pub fn reset_setting(ctx: &ReducerContext, fence: u32, setting: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    require_person(&m)?;
    let spec = rules::setting_spec(&setting).ok_or_else(|| format!("bad_args: unknown setting {setting:?}"))?;
    let key = setting_key(m.ws, &setting);
    if ctx.db.project_setting().key().find(&key).is_none() {
        return Ok(());
    }
    ctx.db.project_setting().key().delete(&key);
    let now = now_ms(ctx);
    if spec.param {
        queue_param(ctx, &m, &setting, spec.default_num, now);
    }
    let value = if spec.kind == "choice" { json!(spec.default_text) } else { json!(spec.default_num) };
    emit(ctx, m.ws, "setting_changed", m.id, json!({"name": m.name, "setting": setting, "value": value, "unit": spec.unit, "reset": true}));
    Ok(())
}

#[reducer]
#[allow(clippy::too_many_arguments)]
pub fn suggest_setting(
    ctx: &ReducerContext,
    fence: u32,
    suggestion_id: String,
    setting: String,
    num: f64,
    text: String,
    reason: String,
    component: String,
) -> Result<(), String> {
    let m = me(ctx, fence)?;
    rules::check_setting(&setting, num, &text)?;
    if reason.trim().is_empty() {
        return Err("bad_args: say why (the person accepting it reads this)".into());
    }
    let reason: String = reason.chars().take(500).collect();
    let now = now_ms(ctx);
    let pending: Vec<SettingSuggestion> =
        ctx.db.setting_suggestion().ws().filter(&m.ws).filter(|x| x.status == "pending").collect();
    // The same proposal again refreshes its reason instead of piling up.
    if let Some(same) = pending.iter().find(|x| x.setting == setting && x.num == num && x.text == text) {
        ctx.db.setting_suggestion().id().update(SettingSuggestion { reason, component, by: m.id, created_ms: now, ..same.clone() });
        return Ok(());
    }
    if pending.len() >= rules::MAX_PENDING_SUGGESTIONS {
        return Err("bad_args: too many suggestions are waiting; ask a teammate to review them first".into());
    }
    if suggestion_id.is_empty() || ctx.db.setting_suggestion().id().find(&suggestion_id).is_some() {
        return Err("bad_args: suggestion_id must be new and non-empty".into());
    }
    ctx.db.setting_suggestion().insert(SettingSuggestion {
        id: suggestion_id.clone(),
        ws: m.ws,
        setting: setting.clone(),
        num,
        text: text.clone(),
        reason: reason.clone(),
        component: component.clone(),
        by: m.id,
        status: "pending".into(),
        created_ms: now,
        resolved_by: 0,
        resolved_ms: 0,
    });
    let value = if text.is_empty() { json!(num) } else { json!(text) };
    emit(ctx, m.ws, "setting_suggested", m.id,
         json!({"name": m.name, "id": suggestion_id, "setting": setting, "value": value, "reason": reason, "component": component}));
    Ok(())
}

#[reducer]
pub fn resolve_suggestion(ctx: &ReducerContext, fence: u32, suggestion_id: String, accept: bool) -> Result<(), String> {
    let m = me(ctx, fence)?;
    require_person(&m)?;
    let sg = ctx
        .db
        .setting_suggestion()
        .id()
        .find(&suggestion_id)
        .filter(|x| x.ws == m.ws)
        .ok_or_else(|| format!("not_found: No suggestion {suggestion_id}"))?;
    if sg.status != "pending" {
        return Err(format!("bad_args: suggestion {suggestion_id} is already {}", sg.status));
    }
    if accept {
        apply_setting(ctx, &m, &sg.setting, sg.num, &sg.text)?;
    }
    let status = if accept { "accepted" } else { "dismissed" };
    ctx.db.setting_suggestion().id().update(SettingSuggestion {
        status: status.into(),
        resolved_by: m.id,
        resolved_ms: now_ms(ctx),
        ..sg
    });
    Ok(())
}
