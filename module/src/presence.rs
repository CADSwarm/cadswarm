use crate::tables::*;
use crate::util::*;
use serde_json::json;
use spacetimedb::{reducer, ReducerContext, Table};

const BRIEF_STATUSES: &[&str] = &["new", "planning", "building", "reviewing", "ready", "failed"];

#[reducer]
pub fn log_activity(ctx: &ReducerContext, fence: u32, kind: String, summary: String, target: Vec<String>) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let summary: String = summary.chars().take(300).collect();
    ctx.db.activity().insert(Activity { id: 0, ws: m.ws, member: m.id, kind, summary: summary.clone(), at_ms: now_ms(ctx) });
    if let Some(first) = target.first() {
        set_pointer(ctx, m.ws, m.id, first, vec![], "pointing", &summary, "");
    }
    Ok(())
}

#[reducer]
pub fn move_pointer(ctx: &ReducerContext, fence: u32, object: String, point: Vec<f32>, mode: String, label: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    if !matches!(mode.as_str(), "pointing" | "inspecting" | "editing" | "idle") {
        return Err("bad_args: mode must be pointing|inspecting|editing|idle".into());
    }
    if !(point.is_empty() || point.len() == 3) {
        return Err("bad_args: point must be empty or [x, y, z]".into());
    }
    set_pointer(ctx, m.ws, m.id, &object, point, &mode, &label, "");
    Ok(())
}

#[reducer]
pub fn submit_brief(ctx: &ReducerContext, fence: u32, brief_id: String, text: String, board: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    if brief_id.is_empty() || ctx.db.brief().id().find(&brief_id).is_some() {
        return Err("bad_args: brief_id must be new and non-empty".into());
    }
    if text.trim().is_empty() {
        return Err("bad_args: describe what to build".into());
    }
    let now = now_ms(ctx);
    let text: String = text.chars().take(2000).collect();
    ctx.db.brief().insert(Brief {
        id: brief_id.clone(),
        ws: m.ws,
        by: m.id,
        text: text.clone(),
        board,
        status: "new".into(),
        plan_json: String::new(),
        stl_hash: String::new(),
        created_ms: now,
        updated_ms: now,
    });
    emit(ctx, m.ws, "brief", m.id, json!({"brief": brief_id, "text": text}));
    Ok(())
}

#[reducer]
pub fn set_brief(ctx: &ReducerContext, fence: u32, brief_id: String, status: String, plan_json: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let b = ctx.db.brief().id().find(&brief_id).filter(|b| b.ws == m.ws).ok_or_else(|| format!("not_found: No brief {brief_id}"))?;
    if !status.is_empty() && !BRIEF_STATUSES.contains(&status.as_str()) {
        return Err("bad_args: unknown brief status".into());
    }
    let status = if status.is_empty() { b.status.clone() } else { status };
    let plan_json = if plan_json.is_empty() { b.plan_json.clone() } else { plan_json };
    ctx.db.brief().id().update(Brief { status, plan_json, updated_ms: now_ms(ctx), ..b });
    Ok(())
}

#[reducer]
pub fn set_brief_stl(ctx: &ReducerContext, fence: u32, brief_id: String, hash: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    require_kind(&m, "host")?;
    let b = ctx.db.brief().id().find(&brief_id).ok_or_else(|| format!("not_found: No brief {brief_id}"))?;
    ctx.db.brief().id().update(Brief { stl_hash: hash, status: "ready".into(), updated_ms: now_ms(ctx), ..b });
    Ok(())
}

#[reducer]
pub fn report_review(ctx: &ReducerContext, fence: u32, review_id: String, brief_id: String, round: u32, passed: bool, findings_json: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    require_kind(&m, "reviewer")?;
    if review_id.is_empty() || ctx.db.review().id().find(&review_id).is_some() {
        return Err("bad_args: review_id must be new and non-empty".into());
    }
    ctx.db.review().insert(Review { id: review_id, ws: m.ws, brief: brief_id.clone(), round, passed, findings_json, at_ms: now_ms(ctx) });
    emit(ctx, m.ws, "review", m.id, json!({"brief": brief_id, "round": round, "passed": passed}));
    Ok(())
}

#[reducer]
pub fn set_bed(ctx: &ReducerContext, fence: u32, x_mm: f64, y_mm: f64, z_mm: f64) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let w = ctx.db.workspace().id().find(&m.ws).ok_or_else(|| "not_found: workspace".to_string())?;
    if x_mm <= 0.0 || y_mm <= 0.0 || z_mm <= 0.0 {
        return Err("bad_args: bed dimensions must be positive".into());
    }
    ctx.db.workspace().id().update(Workspace { bed_x_mm: x_mm, bed_y_mm: y_mm, bed_z_mm: z_mm, ..w });
    Ok(())
}
