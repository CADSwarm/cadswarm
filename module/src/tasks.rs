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
    if !brief.is_empty() {
        if let Some(b) = ctx.db.brief().id().find(&brief).filter(|b| b.ws == m.ws) {
            if b.status == "cancelled" {
                return Err(format!("cancelled: Brief {brief} was stopped; no new tasks"));
            }
        }
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
    take_task(ctx, &m, t);
    Ok(())
}

/// Claim `t` for `m`. If it was queued for another helper, it becomes `m`'s and that helper is told to skip it.
fn take_task(ctx: &ReducerContext, m: &Member, t: Task) {
    let from = if t.assignee.is_empty() || t.assignee.eq_ignore_ascii_case(&m.name) { String::new() } else { t.assignee.clone() };
    if !from.is_empty() {
        let to = rules::address(&from, live_by_name(ctx, m.ws, &from).map(|x| x.id));
        system_message(
            ctx,
            m.ws,
            &to,
            format!("{} was free and took task {} off your queue: {}. Skip it; they'll report it when done.", m.name, t.id, t.title),
            "notice",
            &t.id,
        );
    }
    let (task_id, rev) = (t.id.clone(), t.rev + 1);
    let assignee = if from.is_empty() { t.assignee.clone() } else { m.name.clone() };
    ctx.db.task().id().update(Task { status: "claimed".into(), owner: m.id, assignee, rev, updated_ms: now_ms(ctx), ..t });
    emit(ctx, m.ws, "task_claimed", m.id, json!({"task": task_id, "name": m.name, "from": from}));
}

fn owns_claimed(ctx: &ReducerContext, ws: u64, member: u64) -> bool {
    ctx.db.task().ws().filter(&ws).any(|t| t.owner == member && t.status == "claimed")
}

/// Rebalancing: a free agent takes a task queued for a helper that is busy with another one (or has gone
/// stale), and that helper is told. An empty `task_id` picks for you: the oldest task of the longest queue.
#[reducer]
pub fn pick_up_task(ctx: &ReducerContext, fence: u32, task_id: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    require_kind(&m, "agent")?;
    let caller_busy = owns_claimed(ctx, m.ws, m.id);
    let check = |t: &Task| {
        let assignee_busy = live_by_name(ctx, m.ws, &t.assignee)
            .is_some_and(|a| a.status == "stale" || owns_claimed(ctx, m.ws, a.id));
        let held_by = t.objects.iter().find_map(|o| holder(ctx, m.ws, o).filter(|h| h.id != m.id).map(|h| h.name));
        rules::check_pickup(&t.id, &rules::Pickup {
            status: &t.status,
            assignee: &t.assignee,
            caller: &m.name,
            caller_busy,
            assignee_busy,
            held_by: held_by.as_deref(),
        })
    };
    let t = if task_id.is_empty() {
        if caller_busy {
            return Err(rules::BUSY.into());
        }
        let spare: Vec<Task> = ctx.db.task().ws().filter(&m.ws).filter(|t| t.status == "open" && check(t).is_ok()).collect();
        let queue = |name: &str| spare.iter().filter(|t| t.assignee.eq_ignore_ascii_case(name)).count();
        spare
            .iter()
            .min_by_key(|t| (std::cmp::Reverse(queue(&t.assignee)), t.created_ms))
            .cloned()
            .ok_or_else(|| "no_spare_work: no busy helper has work waiting".to_string())?
    } else {
        let t = task_by_id(ctx, m.ws, &task_id)?;
        check(&t)?;
        t
    };
    take_task(ctx, &m, t);
    Ok(())
}

#[reducer]
pub fn report_task(ctx: &ReducerContext, fence: u32, task_id: String, status: String, summary: String, evidence: Vec<String>) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let t = task_by_id(ctx, m.ws, &task_id)?;
    rules::check_report(&task_id, &t.status, t.owner, &name_of(ctx, t.owner), m.id, &status)?;
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

/// Stop a task. The person who asked for it, the helper that claimed it, or any person on the team may.
/// Its result is kept (a cancelled task never becomes done again).
#[reducer]
pub fn cancel_task(ctx: &ReducerContext, fence: u32, task_id: String, reason: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let t = task_by_id(ctx, m.ws, &task_id)?;
    rules::check_cancel_task(&task_id, &rules::Closing {
        status: &t.status,
        owner: t.owner,
        created_by: t.created_by,
        caller: m.id,
        caller_kind: &m.kind,
    })?;
    let now = now_ms(ctx);
    let reason: String = reason.trim().chars().take(500).collect();
    let why = if reason.is_empty() { String::new() } else { format!(": {reason}") };
    let result = json!({"summary": reason, "by": m.name, "at": now, "cancelled": true});
    let (creator, owner, assignee, rev, title) = (t.created_by, t.owner, t.assignee.clone(), t.rev + 1, t.title.clone());
    ctx.db.task().id().update(Task {
        status: "cancelled".into(), result_json: result.to_string(), rev, updated_ms: now, ..t
    });
    let said = format!("{} stopped task {task_id} ({title}){why}", m.name);
    if creator != m.id {
        system_message(ctx, m.ws, &format!("member:{creator}"), said.clone(), "notice", &task_id);
    }
    if owner != 0 && owner != m.id && owner != creator {
        system_message(ctx, m.ws, &format!("member:{owner}"), format!("{said}. Drop it."), "notice", &task_id);
    } else if owner == 0 && !assignee.is_empty() && !assignee.eq_ignore_ascii_case(&m.name) {
        let to = rules::address(&assignee, live_by_name(ctx, m.ws, &assignee).map(|x| x.id));
        if to != "all" {
            system_message(ctx, m.ws, &to, format!("{said}. You can skip it."), "notice", &task_id);
        }
    }
    emit(ctx, m.ws, "task_cancelled", m.id, json!({"task": task_id, "name": m.name, "reason": reason}));
    Ok(())
}

/// Remove a leftover open task. Nothing was started, so nothing is lost; a claimed task is stopped instead.
#[reducer]
pub fn delete_task(ctx: &ReducerContext, fence: u32, task_id: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let t = task_by_id(ctx, m.ws, &task_id)?;
    rules::check_delete_task(&task_id, &rules::Closing {
        status: &t.status,
        owner: t.owner,
        created_by: t.created_by,
        caller: m.id,
        caller_kind: &m.kind,
    })?;
    let (title, assignee) = (t.title.clone(), t.assignee.clone());
    ctx.db.task().id().delete(&task_id);
    if !assignee.is_empty() && !assignee.eq_ignore_ascii_case(&m.name) {
        let to = rules::address(&assignee, live_by_name(ctx, m.ws, &assignee).map(|x| x.id));
        if to != "all" {
            system_message(ctx, m.ws, &to,
                format!("{} removed task {task_id} ({title}). Skip it if you see it.", m.name), "notice", &task_id);
        }
    }
    emit(ctx, m.ws, "task_deleted", m.id, json!({"task": task_id, "title": title, "name": m.name}));
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
