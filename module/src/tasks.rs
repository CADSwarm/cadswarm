use crate::tables::*;
use crate::util::*;
use cadswarm_rules as rules;
use serde_json::json;
use spacetimedb::{reducer, ReducerContext, Table};

fn task_by_id(ctx: &ReducerContext, ws: u64, id: &str) -> Result<Task, String> {
    ctx.db.task().id().find(&id.to_string()).filter(|t| t.ws == ws).ok_or_else(|| format!("unknown_task: No task {id}"))
}

#[reducer]
#[allow(clippy::too_many_arguments)]
pub fn create_task(ctx: &ReducerContext, fence: u32, task_id: String, title: String, detail: String, objects: Vec<String>, assignee: String, brief: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    if task_id.is_empty() || ctx.db.task().id().find(&task_id).is_some() {
        return Err("bad_args: task_id must be new and non-empty".into());
    }
    if title.trim().is_empty() {
        return Err("bad_args: title is empty".into());
    }
    let now = now_ms(ctx);
    ctx.db.task().insert(Task {
        id: task_id.clone(),
        ws: m.ws,
        title: title.clone(),
        detail,
        objects,
        status: "open".into(),
        owner: 0,
        created_by: m.id,
        rev: 1,
        result_json: String::new(),
        assignee: assignee.clone(),
        brief,
        created_ms: now,
        updated_ms: now,
    });
    if !assignee.is_empty() {
        let to = rules::address(&assignee, live_by_name(ctx, m.ws, &assignee).map(|x| x.id));
        system_message(
            ctx,
            m.ws,
            &to,
            format!("{} assigned you task {task_id}: {title}. Read it (list_tasks) and claim_task it when you start.", m.name),
            "help",
            &task_id,
        );
    }
    emit(ctx, m.ws, "task", m.id, json!({"task": task_id, "title": title, "assignee": assignee}));
    Ok(())
}

#[reducer]
pub fn claim_task(ctx: &ReducerContext, fence: u32, task_id: String, expected_rev: u32) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let t = task_by_id(ctx, m.ws, &task_id)?;
    rules::check_claim(&task_id, &t.status, t.rev, &name_of(ctx, t.owner), expected_rev)?;
    let rev = t.rev + 1;
    ctx.db.task().id().update(Task { status: "claimed".into(), owner: m.id, rev, updated_ms: now_ms(ctx), ..t });
    emit(ctx, m.ws, "task_claimed", m.id, json!({"task": task_id, "name": m.name}));
    Ok(())
}

#[reducer]
pub fn report_task(ctx: &ReducerContext, fence: u32, task_id: String, status: String, summary: String, evidence: Vec<String>) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let t = task_by_id(ctx, m.ws, &task_id)?;
    rules::check_report(&task_id, t.owner, &name_of(ctx, t.owner), m.id, &status)?;
    let now = now_ms(ctx);
    let result = json!({"summary": summary, "evidence": evidence, "by": m.name, "at": now});
    let (creator, rev) = (t.created_by, t.rev + 1);
    ctx.db.task().id().update(Task { status: status.clone(), result_json: result.to_string(), rev, updated_ms: now, ..t });
    system_message(
        ctx,
        m.ws,
        &format!("member:{creator}"),
        format!("{} reported task {task_id} {status}: {summary}", m.name),
        "result",
        &task_id,
    );
    emit(ctx, m.ws, "task_reported", m.id, json!({"task": task_id, "status": status, "name": m.name}));
    Ok(())
}

#[reducer]
pub fn send_message(ctx: &ReducerContext, fence: u32, message_id: String, to: String, body: String, kind: String, task_id: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    if body.trim().is_empty() {
        return Err("bad_args: body is empty".into());
    }
    if message_id.is_empty() || ctx.db.message().id().find(&message_id).is_some() {
        return Err("bad_args: message_id must be new and non-empty".into());
    }
    let target = live_by_name(ctx, m.ws, &to);
    let kind = rules::message_kind(&m.kind, if kind.is_empty() { "message" } else { &kind },
                                   target.as_ref().map(|x| x.kind.as_str()))?;
    let addr = rules::address(&to, target.as_ref().map(|x| x.id));
    let body: String = body.chars().take(4000).collect();
    let preview: String = body.chars().take(300).collect();
    ctx.db.message().insert(Message {
        id: message_id.clone(),
        ws: m.ws,
        from: m.id,
        to: addr.clone(),
        kind: kind.clone(),
        body,
        task_id,
        created_ms: now_ms(ctx),
    });
    let shown_to = if addr == "all" { "all".to_string() } else { to.clone() };
    emit(ctx, m.ws, "message", m.id, json!({"id": message_id, "name": m.name, "to": shown_to, "message_kind": kind, "body": preview}));
    Ok(())
}

fn addressed_to(msg: &Message, member: u64) -> bool {
    msg.to == "all" || msg.to == format!("member:{member}")
}

#[reducer]
pub fn mark_delivered(ctx: &ReducerContext, fence: u32, message_ids: Vec<String>) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let now = now_ms(ctx);
    for id in message_ids {
        let Some(msg) = ctx.db.message().id().find(&id) else { continue };
        if msg.ws != m.ws || !addressed_to(&msg, m.id) {
            continue;
        }
        let key = format!("{id}:{}", m.id);
        if ctx.db.receipt().key().find(&key).is_none() {
            ctx.db.receipt().insert(Receipt { key, ws: m.ws, message_id: id, member: m.id, state: "delivered".into(), at_ms: now });
        }
    }
    Ok(())
}

#[reducer]
pub fn ack_messages(ctx: &ReducerContext, fence: u32, message_ids: Vec<String>) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let now = now_ms(ctx);
    for id in message_ids {
        let key = format!("{id}:{}", m.id);
        let r = ctx
            .db
            .receipt()
            .key()
            .find(&key)
            .ok_or_else(|| format!("not_delivered: Message {id} was never delivered to you; read your inbox first"))?;
        ctx.db.receipt().key().update(Receipt { state: "consumed".into(), at_ms: now, ..r });
    }
    Ok(())
}
