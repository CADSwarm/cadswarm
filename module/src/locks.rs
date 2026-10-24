use crate::tables::*;
use crate::util::*;
use serde_json::json;
use spacetimedb::{reducer, ReducerContext};

#[reducer]
pub fn lock_objects(ctx: &ReducerContext, fence: u32, names: Vec<String>) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let mut names = names;
    names.sort();
    names.dedup();
    names.retain(|n| !n.is_empty());
    if names.is_empty() {
        return Err("bad_args: names is empty".into());
    }
    let conflicts: Vec<String> = names
        .iter()
        .filter_map(|n| holder(ctx, m.ws, n).filter(|h| h.id != m.id).map(|h| format!("{n} by {} ({})", h.name, h.status)))
        .collect();
    if !conflicts.is_empty() {
        return Err(format!(
            "locked: Already locked: {}. Message the holder, or take_over a stale one.",
            conflicts.join(", ")
        ));
    }
    for n in &names {
        set_lock(ctx, m.ws, n, m.id);
    }
    set_pointer(ctx, m.ws, m.id, &names[0], vec![], "pointing", &format!("locking {}", names.join(", ")), "");
    emit(ctx, m.ws, "locked", m.id, json!({"name": m.name, "objects": names}));
    Ok(())
}

#[reducer]
pub fn release_objects(ctx: &ReducerContext, fence: u32, names: Vec<String>, force: bool) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let mut names = names;
    names.sort();
    names.dedup();
    let mut done = Vec::new();
    for n in names {
        if let Some(h) = holder(ctx, m.ws, &n) {
            if h.id == m.id || (force && m.kind == "human") {
                ctx.db.cad_lock().key().delete(&lock_key(m.ws, &n));
                if h.id != m.id {
                    system_message(ctx, m.ws, &format!("member:{}", h.id),
                        format!("A human ({}) released your lock on {n}.", m.name), "notice", "");
                }
                done.push(n);
            }
        }
    }
    if !done.is_empty() {
        emit(ctx, m.ws, "released", m.id, json!({"name": m.name, "objects": done}));
    }
    Ok(())
}
