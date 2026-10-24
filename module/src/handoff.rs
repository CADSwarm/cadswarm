//! Handing a person's work to an AI. The snapshot records where they left off so the agent
//! that accepts can continue from exactly there.

use crate::membership::{take_over_inner, transfer_work};
use crate::tables::*;
use crate::util::*;
use serde_json::json;
use spacetimedb::{reducer, ReducerContext, Table};

fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Where `from` left off: note, pointer, locks, in-progress tasks, recent activity, ops, messages.
pub fn snapshot(ctx: &ReducerContext, from: &Member, note: &str) -> String {
    let ws = from.ws;
    let pointer = ctx.db.pointer().member().find(&from.id).map(|p| {
        json!({"object": p.object, "point": p.point, "mode": p.mode, "label": p.label})
    });
    let tasks: Vec<_> = ctx
        .db
        .task()
        .ws()
        .filter(&ws)
        .filter(|t| t.owner == from.id && (t.status == "claimed" || t.status == "awaiting_takeover"))
        .map(|t| json!({"id": t.id, "title": t.title, "detail": t.detail, "status": t.status, "objects": t.objects}))
        .collect();
    let mut acts: Vec<Activity> = ctx.db.activity().member().filter(&from.id).collect();
    acts.sort_by_key(|a| a.id);
    let recent_activity: Vec<String> = acts.iter().rev().take(15).rev().map(|a| a.summary.clone()).collect();
    let mut ops: Vec<Op> = ctx.db.op().ws().filter(&ws).filter(|o| o.by == from.id).collect();
    ops.sort_by_key(|o| o.created_ms);
    let recent_ops: Vec<_> = ops
        .iter()
        .rev()
        .take(10)
        .rev()
        .map(|o| json!({"kind": o.kind, "args": clip(&o.args_json, 300), "status": o.status, "error": o.error}))
        .collect();
    let mut msgs: Vec<Message> = ctx.db.message().ws().filter(&ws).filter(|m| m.from == from.id).collect();
    msgs.sort_by_key(|m| m.created_ms);
    let recent_messages: Vec<_> =
        msgs.iter().rev().take(5).rev().map(|m| json!({"to": m.to, "body": clip(&m.body, 300)})).collect();
    json!({
        "from": from.name, "note": note, "pointer": pointer, "locks": locks_of(ctx, from.id),
        "tasks": tasks, "recent_activity": recent_activity, "recent_ops": recent_ops,
        "recent_messages": recent_messages,
    })
    .to_string()
}

fn handoff_by_id(ctx: &ReducerContext, ws: u64, id: &str) -> Result<Handoff, String> {
    ctx.db.handoff().id().find(&id.to_string()).filter(|h| h.ws == ws).ok_or_else(|| format!("not_found: No handoff {id}"))
}

#[reducer]
pub fn request_handoff(ctx: &ReducerContext, fence: u32, handoff_id: String, note: String, trigger: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    require_kind(&m, "human")?;
    if trigger != "now" && trigger != "on_leave" {
        return Err("bad_args: trigger must be now or on_leave".into());
    }
    if handoff_id.is_empty() || ctx.db.handoff().id().find(&handoff_id).is_some() {
        return Err("bad_args: handoff_id must be new and non-empty".into());
    }
    let now = now_ms(ctx);
    if trigger == "on_leave" {
        let armed: Vec<Handoff> =
            ctx.db.handoff().ws().filter(&m.ws).filter(|h| h.from == m.id && h.status == "waiting").collect();
        for h in armed {
            ctx.db.handoff().id().update(Handoff { status: "cancelled".into(), updated_ms: now, ..h });
        }
    }
    let note = clip(&note, 1000);
    let (status, context_json) =
        if trigger == "now" { ("open", snapshot(ctx, &m, &note)) } else { ("waiting", String::new()) };
    ctx.db.handoff().insert(Handoff {
        id: handoff_id.clone(),
        ws: m.ws,
        from: m.id,
        note: note.clone(),
        trigger: trigger.clone(),
        status: status.into(),
        taker: 0,
        context_json,
        created_ms: now,
        updated_ms: now,
    });
    emit(ctx, m.ws, "handoff", m.id, json!({"id": handoff_id, "name": m.name, "trigger": trigger, "status": status, "note": note}));
    Ok(())
}

#[reducer]
pub fn cancel_handoff(ctx: &ReducerContext, fence: u32, handoff_id: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let h = handoff_by_id(ctx, m.ws, &handoff_id)?;
    if h.from != m.id {
        return Err("not_owner: only the person who asked can cancel a handoff".into());
    }
    if h.status != "waiting" && h.status != "open" {
        return Err(format!("not_available: this handoff is already {}", h.status));
    }
    ctx.db.handoff().id().update(Handoff { status: "cancelled".into(), updated_ms: now_ms(ctx), ..h });
    Ok(())
}

/// Called by `sweep` when `person` goes stale: their armed handoff opens with a fresh snapshot.
pub fn open_waiting_for(ctx: &ReducerContext, person: &Member) {
    let armed: Vec<Handoff> =
        ctx.db.handoff().ws().filter(&person.ws).filter(|h| h.from == person.id && h.status == "waiting").collect();
    for h in armed {
        let context_json = snapshot(ctx, person, &h.note);
        emit(ctx, h.ws, "handoff", person.id, json!({"id": h.id, "name": person.name, "trigger": "on_leave", "status": "open"}));
        ctx.db.handoff().id().update(Handoff { status: "open".into(), context_json, updated_ms: now_ms(ctx), ..h });
    }
}

#[reducer]
pub fn accept_handoff(ctx: &ReducerContext, fence: u32, handoff_id: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    require_kind(&m, "agent")?;
    let h = handoff_by_id(ctx, m.ws, &handoff_id)?;
    if h.status != "open" {
        return Err(format!("not_available: this handoff is {}", h.status));
    }
    let from = ctx.db.member().id().find(&h.from).ok_or_else(|| "unknown_participant: the person is gone".to_string())?;
    // A person who left is retired like any stale takeover; one who is still here keeps their seat.
    let (locks, tasks) = if from.status == "stale" { take_over_inner(ctx, &m, &from) } else { transfer_work(ctx, &m, &from) };
    if let Some(p) = ctx.db.pointer().member().find(&from.id) {
        set_pointer(ctx, m.ws, m.id, &p.object, p.point.clone(), "pointing", &format!("continuing {}'s work", from.name), "");
    }
    ctx.db.handoff().id().update(Handoff { status: "accepted".into(), taker: m.id, updated_ms: now_ms(ctx), ..h });
    system_message(
        ctx,
        m.ws,
        &format!("member:{}", from.id),
        format!("{} picked up where you left off (parts {:?}, tasks {:?}).", m.name, locks, tasks),
        "notice",
        "",
    );
    emit(ctx, m.ws, "handoff_accepted", m.id, json!({"id": handoff_id, "from": from.name, "taker": m.name, "locks": locks, "tasks": tasks}));
    Ok(())
}
