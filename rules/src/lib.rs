//! CadSwarm coordination rules, free of SpacetimeDB so they unit-test natively.
//! Ported from cadswarm/hub/core.py; the module reducers are thin glue over these.

use serde_json::Value;
use std::collections::BTreeMap;

pub const STALE_AFTER_MS: u64 = 30_000;
pub const ORPHAN_AFTER_MS: u64 = 600_000;
pub const ACTIVITY_KEEP: usize = 200;
pub const INVITE_MAX_TTL_SECS: u64 = 86_400;

pub const MUTATING: &[&str] = &[
    "create_primitive", "create_extrusion", "boolean", "set_property", "set_props",
    "set_expression", "set_placement", "delete", "set_param", "run_part_script",
    // Components library: place a library part or a generated standard part into the model.
    "insert_component", "insert_standard_part",
    // A mesh from project files (a lattice from lighten_part) as a Mesh::Feature.
    "insert_mesh",
];
/// Read-only ops the host answers: FreeCAD queries, board knowledge lookups, project file reads.
pub const QUERIES: &[&str] = &[
    "get_object", "interference", "export", "dependents", "printability",
    "lookup_board", "recall_designs",
    "list_files", "read_file", "file_history",
    "brep", "storage_status",
    "faces", "fea_status", "fea_reports",
    // Lighten jobs: reading their state and the cell library (the host's helper, lattice_jobs.py).
    "lattice_capabilities", "lattice_cells", "lattice_estimate", "lattice_status", "lattice_list",
    "lattice_result",
    // Components library reads (the host's helper; analyze_component is a FreeCAD query).
    "library_search", "library_recommend", "library_get", "library_export_pack", "standard_parts",
    "analyze_component", "suggest_settings",
    // The team's learned design style (the host's helper, cadswarm/style_memory.py).
    "style_context", "list_style",
];
/// Project-file writes (files/ and snapshots/ on the host): no FreeCAD object locks, but they change things.
pub const FILE_WRITES: &[&str] = &[
    "save_file", "make_folder", "move_file", "delete_file", "restore_file", "snapshot", "prune_storage",
    // FEA: a job copies the model and later writes its report into files/fea/.
    "fea_run", "fea_publish",
    // Lighten: a job copies the model and later writes its lattice into files/lattice/.
    "lattice_start", "lattice_cancel",
    // Components library: files under library/ on the host, mirrored into the `component` table.
    "library_add", "library_import_url", "library_import_pack", "library_update", "library_delete",
    // Style rules live in the host's project database: teaching, confirming and re-learning change them.
    "teach_style", "resolve_style", "learn_style",
];
/// Team events a completed (non-mutating) op may carry in its result as `_events` for complete_op to emit:
/// the host's helper can't call reducers, and an FEA report must pop up for every member.
pub const RESULT_EVENTS: &[&str] = &["fea_started", "fea_progress", "fea_report", "fea_failed",
    "lattice_started", "lattice_progress", "lattice_report", "lattice_failed"];
pub const MAX_RESULT_EVENTS: usize = 10;

pub fn is_result_event(kind: &str) -> bool { RESULT_EVENTS.contains(&kind) }

/// The (kind, data) pairs from an op result's `_events` that complete_op may emit; anything else is dropped.
pub fn result_events(result: &Value) -> Vec<(String, Value)> {
    result
        .get("_events")
        .and_then(Value::as_array)
        .map(|evs| {
            evs.iter()
                .filter_map(|e| {
                    let kind = e.get("kind")?.as_str()?;
                    let data = e.get("data").filter(|d| d.is_object())?;
                    is_result_event(kind).then(|| (kind.to_string(), data.clone()))
                })
                .take(MAX_RESULT_EVENTS)
                .collect()
        })
        .unwrap_or_default()
}

/// Large op results are parked as artifacts named `op-result:<op_id>` and dropped after this long.
pub const OP_RESULT_TTL_MS: u64 = 600_000;
pub const OP_RESULT_PREFIX: &str = "op-result:";

/// True once an `op-result:` artifact is strictly older than the TTL; other artifacts never expire.
pub fn op_result_expired(name: &str, created_ms: u64, now: u64) -> bool {
    name.starts_with(OP_RESULT_PREFIX) && now.saturating_sub(created_ms) > OP_RESULT_TTL_MS
}

pub fn is_mutating(kind: &str) -> bool { MUTATING.contains(&kind) }
pub fn is_query(kind: &str) -> bool { QUERIES.contains(&kind) }
pub fn is_file_write(kind: &str) -> bool { FILE_WRITES.contains(&kind) }
/// Anything a reviewer may not do: change geometry or change project files.
pub fn is_write(kind: &str) -> bool { is_mutating(kind) || is_file_write(kind) }
pub fn valid_kind(kind: &str) -> bool { matches!(kind, "human" | "agent" | "host" | "reviewer") }
pub fn valid_role(role: &str) -> bool { matches!(role, "lead" | "member") }

fn str_arg(args: &Value, key: &str) -> Option<String> {
    args.get(key).and_then(|v| v.as_str()).filter(|s| !s.is_empty()).map(str::to_string)
}

/// Argument names an op must hold locks on, beyond the object it creates (core.py TARGETS).
fn targets(kind: &str) -> &'static [&'static str] {
    match kind {
        "set_property" | "set_props" | "set_expression" | "set_placement" | "delete" => &["name"],
        "boolean" => &["base", "tool"],
        _ => &[],
    }
}

/// The object an op creates, if any (core.py CREATES).
pub fn created_name(kind: &str, args: &Value) -> Option<String> {
    match kind {
        "create_primitive" | "create_extrusion" | "boolean" | "run_part_script" | "insert_component"
        | "insert_standard_part" | "insert_mesh" => str_arg(args, "name"),
        _ => None,
    }
}

pub fn needed_locks(kind: &str, args: &Value, exists: impl Fn(&str) -> bool) -> Vec<String> {
    let mut out: Vec<String> = targets(kind).iter().filter_map(|k| str_arg(args, k)).collect();
    if kind == "set_param" {
        out.push("Params".into());
    }
    if kind == "run_part_script" {
        if let Some(n) = created_name(kind, args) {
            if exists(&n) {
                out.push(n);
            }
        }
    }
    out
}

#[derive(Debug, Clone, PartialEq)]
pub struct Holder {
    pub id: u64,
    pub name: String,
    pub status: String,
}

/// The checks `submit_op` runs before queueing (core.py Hub.op, minus the revision check).
pub fn check_op(
    kind: &str,
    args: &Value,
    mine: &[String],
    holder: impl Fn(&str) -> Option<Holder>,
    exists: impl Fn(&str) -> bool,
    caller: u64,
) -> Result<(), String> {
    if !is_mutating(kind) && !is_query(kind) && !is_file_write(kind) {
        return Err(format!("unknown_op: Unknown op {kind:?}"));
    }
    if !is_mutating(kind) {
        return Ok(());
    }
    let missing: Vec<String> =
        needed_locks(kind, args, &exists).into_iter().filter(|n| !mine.contains(n)).collect();
    if !missing.is_empty() {
        let held: Vec<String> = missing
            .iter()
            .map(|n| format!("{n}: {}", holder(n).map(|h| h.name).unwrap_or_else(|| "nobody".into())))
            .collect();
        return Err(format!("not_locked: Lock {missing:?} before {kind}. Current holders: {}", held.join(", ")));
    }
    if let Some(new) = created_name(kind, args) {
        if !mine.contains(&new) {
            if let Some(h) = holder(&new) {
                if h.id != caller {
                    return Err(format!("locked: The name {new} is reserved by {}", h.name));
                }
            }
        }
    }
    Ok(())
}

pub fn parse_expected_revs(json: &str) -> Result<BTreeMap<String, u32>, String> {
    let t = json.trim();
    if t.is_empty() || t == "null" {
        return Ok(BTreeMap::new());
    }
    serde_json::from_str(t).map_err(|e| format!("bad_args: expected_revs must be a JSON object of name -> rev: {e}"))
}

/// `current(name)` returns (current revision, name of whoever last changed it).
pub fn check_expected_revs(
    expected: &BTreeMap<String, u32>,
    current: impl Fn(&str) -> (u32, String),
) -> Result<(), String> {
    for (name, rev) in expected {
        let (cur, last) = current(name);
        if cur != *rev {
            return Err(format!(
                "stale_rev: {name} is at rev {cur}, not {rev} (last changed by {last}); re-read it first"
            ));
        }
    }
    Ok(())
}

#[derive(Debug, PartialEq)]
pub enum Join {
    Fresh,
    Inherit(u64),
}

/// `namesake` is a live member with the same name (case-insensitive): (id, status).
pub fn join_decision(name: &str, namesake: Option<(u64, &str)>) -> Result<Join, String> {
    match namesake {
        Some((_, "active")) => Err(format!(
            "name_in_use: {name:?} is already connected. Pick another name, or if that is your own crashed \
             session, wait {}s for it to go stale and join again to inherit its work.",
            STALE_AFTER_MS / 1000
        )),
        Some((id, "stale")) => Ok(Join::Inherit(id)),
        _ => Ok(Join::Fresh),
    }
}

pub fn check_name(name: &str) -> Result<(), String> {
    let ok = !name.is_empty()
        && name.len() <= 40
        && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_');
    if ok { Ok(()) } else { Err("bad_args: name must be 1-40 letters, digits, - or _".into()) }
}

/// Renaming a member to `name`: a valid name that no other live member holds (`holder` is the live member
/// with that name, compared without case). The same member may change only the case of its own name.
pub fn check_rename(name: &str, holder: Option<u64>, me: u64) -> Result<(), String> {
    check_name(name)?;
    match holder {
        Some(id) if id != me => Err(format!("name_in_use: {name:?} is already taken on this team. Pick another name.")),
        _ => Ok(()),
    }
}

pub fn check_take_over(
    taker: u64,
    target: u64,
    target_name: &str,
    target_status: &str,
    taker_kind: &str,
    force: bool,
) -> Result<(), String> {
    if taker == target {
        return Err("bad_args: You cannot take over yourself".into());
    }
    if target_status == "stale" || (force && taker_kind == "human") {
        return Ok(());
    }
    Err(format!(
        "not_available: {target_name} is {target_status}; only a stale participant can be taken over{}",
        if taker_kind == "human" { "" } else { " (a human may force it)" }
    ))
}

pub struct Presence {
    pub id: u64,
    pub kind: String,
    pub status: String,
    pub last_seen_ms: u64,
    pub stale_since_ms: u64,
    pub online: bool,
}

#[derive(Debug, Default)]
pub struct SweepPlan {
    pub to_stale: Vec<u64>,
    pub to_orphan: Vec<u64>,
}

/// Connected WebSocket clients (phone, host) never go stale; HTTP-only agents rely on heartbeats.
/// A connected member is never retired, however long ago it went quiet.
/// The reviewer is spawned by the host for one round at a time, so it is offline between rounds.
/// Retiring it would lose the team member the next round reuses (and `rejoin` after a retirement
/// mints a second member row), so a quiet reviewer stays `stale` and is never orphaned.
pub fn plan_sweep(now: u64, members: &[Presence]) -> SweepPlan {
    SweepPlan {
        to_stale: members
            .iter()
            .filter(|m| m.status == "active" && !m.online && now.saturating_sub(m.last_seen_ms) > STALE_AFTER_MS)
            .map(|m| m.id)
            .collect(),
        to_orphan: members
            .iter()
            .filter(|m| m.kind != "reviewer")
            .filter(|m| m.status == "stale" && !m.online && now.saturating_sub(m.stale_since_ms) > ORPHAN_AFTER_MS)
            .map(|m| m.id)
            .collect(),
    }
}

/// `expected_rev` 0 means "don't check".
pub fn check_claim(task_id: &str, status: &str, rev: u32, owner_name: &str, expected_rev: u32) -> Result<(), String> {
    if expected_rev != 0 && expected_rev != rev {
        return Err(format!("stale_rev: Task {task_id} is at rev {rev}; re-read it"));
    }
    if status != "open" {
        return Err(format!("not_claimable: Task {task_id} is {status} (owner {owner_name})"));
    }
    Ok(())
}

pub const BUSY: &str = "busy: finish the task you claimed before picking up another";

/// What `check_pickup` needs to know about a task queued for another helper.
#[derive(Clone, Copy)]
pub struct Pickup<'a> {
    pub status: &'a str,
    pub assignee: &'a str,
    pub caller: &'a str,
    /// The caller already owns a claimed task.
    pub caller_busy: bool,
    /// The assignee owns a claimed task of its own, or has gone stale.
    pub assignee_busy: bool,
    /// Someone other than the caller holds one of the task's parts.
    pub held_by: Option<&'a str>,
}

/// A free helper may take a task off a busy helper's queue: work the assignee hasn't started because it is
/// on something else, and whose parts nobody else holds.
pub fn check_pickup(task_id: &str, p: &Pickup) -> Result<(), String> {
    if p.status != "open" {
        return Err(format!("not_claimable: Task {task_id} is {}", p.status));
    }
    if p.assignee.is_empty() || p.assignee.eq_ignore_ascii_case(p.caller) {
        return Err(format!("bad_args: Task {task_id} isn't queued for another helper; claim_task it"));
    }
    if p.caller_busy {
        return Err(BUSY.into());
    }
    if !p.assignee_busy {
        return Err(format!("not_spare: {} isn't busy and will start task {task_id} itself", p.assignee));
    }
    if let Some(who) = p.held_by {
        return Err(format!("not_spare: {who} holds parts of task {task_id}"));
    }
    Ok(())
}

pub fn check_report(task_id: &str, current: &str, owner: u64, owner_name: &str, caller: u64, status: &str) -> Result<(), String> {
    if status != "done" && status != "blocked" {
        return Err("bad_args: status must be done or blocked".into());
    }
    if current == "cancelled" {
        return Err(format!("cancelled: Task {task_id} was cancelled; drop it"));
    }
    if owner != caller {
        return Err(format!("not_owner: Task {task_id} is owned by {owner_name}, not you"));
    }
    Ok(())
}

/// What `check_cancel_task` needs to know about a task a member wants to stop or remove.
#[derive(Clone, Copy)]
pub struct Closing<'a> {
    pub status: &'a str,
    pub owner: u64,
    pub created_by: u64,
    pub caller: u64,
    pub caller_kind: &'a str,
}

/// A person may stop any task; a helper only one it created or claimed. A finished task keeps its result.
pub fn check_cancel_task(task_id: &str, t: &Closing) -> Result<(), String> {
    if t.status == "done" {
        return Err(format!("already_done: Task {task_id} is done; its result is kept"));
    }
    if t.status == "cancelled" {
        return Err(format!("already_cancelled: Task {task_id} was already cancelled"));
    }
    if t.caller_kind != "human" && t.caller != t.created_by && t.caller != t.owner {
        return Err(format!("not_yours: Task {task_id} is not yours to stop"));
    }
    Ok(())
}

/// Deleting is for leftovers only: an open task nobody has started, so no work disappears.
pub fn check_delete_task(task_id: &str, t: &Closing) -> Result<(), String> {
    if t.status != "open" {
        return Err(format!("not_deletable: Task {task_id} is {}; stop it instead", t.status));
    }
    check_cancel_task(task_id, t)
}

/// Statuses a brief never leaves: its result (or its failure) is kept.
pub const CLOSED_BRIEFS: &[&str] = &["ready", "failed", "cancelled"];

/// A person may stop any brief; a helper only one it asked for. A closed brief stays closed.
pub fn check_cancel_brief(brief_id: &str, status: &str, author: u64, caller: u64, caller_kind: &str) -> Result<(), String> {
    if CLOSED_BRIEFS.contains(&status) {
        return Err(format!("already_closed: Brief {brief_id} is {status}"));
    }
    if caller_kind != "human" && caller != author {
        return Err(format!("not_yours: Brief {brief_id} was asked for by someone else"));
    }
    Ok(())
}

/// A person's words outrank peer requests in every inbox.
/// Kinds a member may send. "chat" is people talking (agents read it as background, never as an order);
/// "command" (stored as "steer") is a person's instruction for an AI helper and outranks everything.
pub const SEND_KINDS: &[&str] = &["message", "chat", "command", "steer", "handoff", "help", "result", "notice"];

/// The stored kind of a message, or an error for a kind nobody may send. `target_kind` is the member kind of
/// the addressee when it is one live member (None for "all" or someone who hasn't joined yet).
pub fn message_kind(sender_kind: &str, requested: &str, target_kind: Option<&str>) -> Result<String, String> {
    if !SEND_KINDS.contains(&requested) {
        return Err(format!("bad_args: kind must be one of {SEND_KINDS:?}"));
    }
    let mut kind = requested;
    if kind == "command" || (sender_kind == "human" && kind == "message") {
        kind = "steer"; // a person's instruction outranks peer requests in every inbox
    }
    if kind == "steer" && sender_kind != "human" {
        kind = "message"; // only people give orders; a helper's "command" is a request
    }
    if kind == "steer" && target_kind == Some("human") {
        kind = "chat"; // people can't be commanded: it's a chat message to them
    }
    Ok(kind.into())
}

/// `live` is the id of a live member with that name, if any.
pub fn address(to: &str, live: Option<u64>) -> String {
    match to.trim() {
        "" | "all" => "all".into(),
        name => match live {
            Some(id) => format!("member:{id}"),
            None => format!("@{}", name.to_lowercase()),
        },
    }
}

pub fn owners_to_notify(
    recomputed: &[String],
    actor: u64,
    holder: impl Fn(&str) -> Option<u64>,
) -> BTreeMap<u64, Vec<String>> {
    let mut out: BTreeMap<u64, Vec<String>> = BTreeMap::new();
    for n in recomputed {
        if let Some(owner) = holder(n) {
            if owner != actor {
                out.entry(owner).or_default().push(n.clone());
            }
        }
    }
    out
}

pub fn hash_token(token: &str) -> String {
    use sha2::{Digest, Sha256};
    hex::encode(Sha256::digest(token.trim().as_bytes()))
}

// ---------------------------------------------------------------------------
// Team precision settings (cadswarm/settings_spec.json is the one source of truth)
// ---------------------------------------------------------------------------

pub const SETTINGS_SPEC_JSON: &str = include_str!("../../cadswarm/settings_spec.json");
pub const MAX_PENDING_SUGGESTIONS: usize = 50;

#[derive(Debug, Clone, PartialEq)]
pub struct SettingSpec {
    pub key: String,
    pub kind: String, // num | choice
    pub unit: String,
    pub min: f64,
    pub max: f64,
    pub choices: Vec<String>,
    pub param: bool,
    /// The default as a number (num settings) or text (choice settings).
    pub default_num: f64,
    pub default_text: String,
}

pub fn setting_specs() -> Vec<SettingSpec> {
    let v: Value = serde_json::from_str(SETTINGS_SPEC_JSON).expect("settings_spec.json is valid JSON");
    v["settings"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|s| SettingSpec {
                    key: s["key"].as_str().unwrap_or_default().into(),
                    kind: s["kind"].as_str().unwrap_or("num").into(),
                    unit: s["unit"].as_str().unwrap_or_default().into(),
                    min: s["min"].as_f64().unwrap_or(f64::MIN),
                    max: s["max"].as_f64().unwrap_or(f64::MAX),
                    choices: s["choices"]
                        .as_array()
                        .map(|c| c.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
                        .unwrap_or_default(),
                    param: s["param"].as_bool().unwrap_or(false),
                    default_num: s["default"].as_f64().unwrap_or(0.0),
                    default_text: s["default"].as_str().unwrap_or_default().into(),
                })
                .collect()
        })
        .unwrap_or_default()
}

pub fn setting_spec(key: &str) -> Option<SettingSpec> {
    setting_specs().into_iter().find(|s| s.key == key)
}

/// Only people change team settings; AI helpers suggest them (`suggest_setting`) and a person accepts.
pub fn can_change_settings(member_kind: &str) -> bool {
    member_kind == "human"
}

/// Validates a value for `key`: numbers within [min, max], choices from the list. Returns the spec.
pub fn check_setting(key: &str, num: f64, text: &str) -> Result<SettingSpec, String> {
    let spec = setting_spec(key).ok_or_else(|| format!("bad_args: unknown setting {key:?}"))?;
    match spec.kind.as_str() {
        "choice" => {
            if !spec.choices.iter().any(|c| c == text) {
                return Err(format!("bad_args: {key} must be one of {:?}", spec.choices));
            }
        }
        _ => {
            if !num.is_finite() || num < spec.min || num > spec.max {
                return Err(format!("bad_args: {key} must be between {} and {} {}", spec.min, spec.max, spec.unit));
            }
        }
    }
    Ok(spec)
}

// ---------------------------------------------------------------------------
// Components library: library_* op results carry the rows the host's helper wants mirrored
// ---------------------------------------------------------------------------

pub const LIBRARY_KINDS: &[&str] =
    &["library_add", "library_import_url", "library_import_pack", "library_update", "library_delete"];
pub const MAX_COMPONENTS_PER_RESULT: usize = 200;

pub fn valid_component_id(id: &str) -> bool {
    id.len() > 2
        && id.len() <= 48
        && id.starts_with("c_")
        && id[2..].chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
}

#[derive(Debug, Default, PartialEq)]
pub struct ComponentChanges {
    pub upserts: Vec<Value>,
    pub deletes: Vec<String>,
}

/// `_components` (objects with a valid `id`) and `_component_deletes` (ids) from a library op's result.
/// Results of any other op kind never touch the library.
pub fn component_changes(kind: &str, result: &Value) -> ComponentChanges {
    if !LIBRARY_KINDS.contains(&kind) {
        return ComponentChanges::default();
    }
    let upserts = result
        .get("_components")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter(|c| c.get("id").and_then(Value::as_str).is_some_and(valid_component_id))
                .take(MAX_COMPONENTS_PER_RESULT)
                .cloned()
                .collect()
        })
        .unwrap_or_default();
    let deletes = result
        .get("_component_deletes")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .filter(|id| valid_component_id(id))
                .take(MAX_COMPONENTS_PER_RESULT)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    ComponentChanges { upserts, deletes }
}

/// The library component an insert op placed, for its use count.
pub fn inserted_component(kind: &str, args: &Value) -> Option<String> {
    (kind == "insert_component").then(|| str_arg(args, "component")).flatten().filter(|id| valid_component_id(id))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn h(id: u64, name: &str) -> Option<Holder> {
        Some(Holder { id, name: name.into(), status: "active".into() })
    }

    #[test]
    fn editing_someone_elses_part_needs_its_lock() {
        let args = json!({"name": "Plate", "props": {"Length": 5}});
        let err = check_op("set_props", &args, &[], |_| h(1, "alice-claude"), |_| true, 2).unwrap_err();
        assert!(err.starts_with("not_locked:"), "{err}");
        assert!(err.contains("alice-claude"), "{err}");
    }

    #[test]
    fn boolean_needs_base_and_tool_and_set_param_needs_params() {
        let b = needed_locks("boolean", &json!({"base": "A", "tool": "B", "name": "C"}), |_| true);
        assert_eq!(b, vec!["A".to_string(), "B".to_string()]);
        assert_eq!(needed_locks("set_param", &json!({"alias": "x"}), |_| true), vec!["Params".to_string()]);
    }

    #[test]
    fn a_script_replacing_an_existing_feature_needs_its_lock() {
        let args = json!({"name": "Bracket", "code": "..."});
        assert_eq!(needed_locks("run_part_script", &args, |n| n == "Bracket"), vec!["Bracket".to_string()]);
        assert!(needed_locks("run_part_script", &args, |_| false).is_empty());
    }

    #[test]
    fn a_reserved_name_cannot_be_created_by_someone_else() {
        let args = json!({"kind": "box", "name": "Lid"});
        let err = check_op("create_primitive", &args, &[], |_| h(1, "alice"), |_| false, 2).unwrap_err();
        assert!(err.starts_with("locked:"), "{err}");
        assert!(check_op("create_primitive", &args, &["Lid".into()], |_| h(1, "alice"), |_| false, 1).is_ok());
    }

    #[test]
    fn queries_need_no_locks_and_unknown_ops_are_refused() {
        assert!(check_op("interference", &json!({}), &[], |_| None, |_| true, 1).is_ok());
        assert!(check_op("lookup_board", &json!({"question": "pico holes"}), &[], |_| None, |_| true, 1).is_ok());
        for k in ["faces", "fea_status", "fea_reports", "fea_run", "fea_publish"] {
            assert!(check_op(k, &json!({}), &[], |_| None, |_| true, 1).is_ok(), "{k}");
        }
        assert!(is_write("fea_run") && is_write("fea_publish") && !is_write("fea_status") && !is_mutating("fea_run"));
        assert!(check_op("save_file", &json!({"path": "notes.md"}), &[], |_| None, |_| true, 1).is_ok());
        assert!(check_op("explode", &json!({}), &[], |_| None, |_| true, 1).unwrap_err().starts_with("unknown_op:"));
    }

    #[test]
    fn lighten_jobs_are_helper_reads_and_writes_and_a_mesh_insert_is_a_locked_change() {
        for k in ["lattice_capabilities", "lattice_cells", "lattice_estimate", "lattice_status", "lattice_list",
                  "lattice_result"] {
            assert!(is_query(k) && !is_write(k), "{k}");
        }
        assert!(is_file_write("lattice_start") && is_file_write("lattice_cancel") && !is_mutating("lattice_start"));
        assert!(is_mutating("insert_mesh"));
        assert_eq!(created_name("insert_mesh", &json!({"name": "Bracket_lattice"})), Some("Bracket_lattice".into()));
        let r = json!({"_events": [{"kind": "lattice_report", "data": {"job_id": "lj_1"}}]});
        assert_eq!(result_events(&r), vec![("lattice_report".to_string(), json!({"job_id": "lj_1"}))]);
    }

    #[test]
    fn reading_the_team_style_is_a_query_and_changing_it_is_a_write() {
        for k in ["style_context", "list_style", "teach_style", "resolve_style", "learn_style"] {
            assert!(check_op(k, &json!({}), &[], |_| None, |_| true, 1).is_ok(), "{k}");
        }
        assert!(!is_write("style_context") && !is_write("list_style"));
        assert!(is_write("teach_style") && is_write("resolve_style") && !is_mutating("teach_style"));
    }

    #[test]
    fn file_writes_and_geometry_changes_count_as_writes_but_reads_do_not() {
        assert!(is_write("save_file") && is_write("set_props") && is_write("snapshot"));
        assert!(!is_write("read_file") && !is_write("recall_designs") && !is_write("interference"));
        assert!(!is_mutating("save_file"), "file writes need no FreeCAD locks");
    }

    #[test]
    fn expected_rev_detects_a_concurrent_change() {
        let exp = parse_expected_revs(r#"{"Plate": 1}"#).unwrap();
        let err = check_expected_revs(&exp, |_| (2, "bob".into())).unwrap_err();
        assert!(err.starts_with("stale_rev:") && err.contains("rev 2"), "{err}");
        assert!(check_expected_revs(&exp, |_| (1, "bob".into())).is_ok());
        assert!(parse_expected_revs("").unwrap().is_empty());
        assert!(parse_expected_revs("null").unwrap().is_empty());
    }

    #[test]
    fn names_are_unique_among_live_members_and_a_stale_namesake_is_inherited() {
        assert!(join_decision("bob", Some((7, "active"))).unwrap_err().starts_with("name_in_use:"));
        assert_eq!(join_decision("bob", Some((7, "stale"))).unwrap(), Join::Inherit(7));
        assert_eq!(join_decision("bob", None).unwrap(), Join::Fresh);
        assert!(check_name("alice-claude").is_ok());
        assert!(check_name("").is_err() && check_name("has space").is_err());
    }

    #[test]
    fn takeover_is_compare_and_set_on_stale_and_humans_may_force() {
        assert!(check_take_over(1, 2, "bob", "active", "agent", false).unwrap_err().starts_with("not_available:"));
        assert!(check_take_over(1, 2, "bob", "active", "agent", true).is_err());
        assert!(check_take_over(1, 2, "bob", "active", "human", true).is_ok());
        assert!(check_take_over(1, 2, "bob", "stale", "agent", false).is_ok());
        assert!(check_take_over(1, 1, "me", "stale", "agent", false).unwrap_err().starts_with("bad_args:"));
    }

    #[test]
    fn quiet_members_go_stale_unless_connected_and_long_stale_offline_ones_are_orphaned() {
        let now = 1_000_000;
        let m = |id, status: &str, last_seen_ms, stale_since_ms, online| Presence {
            id, kind: "agent".into(), status: status.into(), last_seen_ms, stale_since_ms, online,
        };
        let plan = plan_sweep(now, &[
            m(1, "active", now - STALE_AFTER_MS - 1, 0, false),
            m(2, "active", now - STALE_AFTER_MS - 1, 0, true),
            m(3, "active", now - 1_000, 0, false),
            m(4, "stale", 0, now - ORPHAN_AFTER_MS - 1, false),
            m(5, "stale", 0, now - 1_000, false),
            m(6, "stale", 0, now - ORPHAN_AFTER_MS - 1, true), // connected again: never retired
        ]);
        assert_eq!(plan.to_stale, vec![1]);
        assert_eq!(plan.to_orphan, vec![4]);
    }

    #[test]
    fn a_quiet_reviewer_goes_stale_but_is_never_orphaned() {
        // The host spawns the reviewer for one round at a time. Between rounds it is offline for far
        // longer than ORPHAN_AFTER_MS, and the next round and the review card both need the member.
        let now = 1_000_000;
        let reviewer = |id, status: &str, last_seen_ms, stale_since_ms| Presence {
            id, kind: "reviewer".into(), status: status.into(), last_seen_ms, stale_since_ms, online: false,
        };
        // Quiet past STALE_AFTER_MS: it does go stale, exactly like any HTTP-only helper.
        let plan = plan_sweep(now, &[reviewer(9, "active", now - STALE_AFTER_MS - 1, 0)]);
        assert_eq!(plan.to_stale, vec![9]);
        assert!(plan.to_orphan.is_empty());
        // Quiet for longer than ORPHAN_AFTER_MS: still stale, never retired.
        let plan = plan_sweep(now, &[reviewer(9, "stale", 0, now - ORPHAN_AFTER_MS - 1)]);
        assert!(plan.to_stale.is_empty());
        assert!(plan.to_orphan.is_empty());
    }

    #[test]
    fn task_claim_and_report_rules() {
        assert!(check_claim("t_1", "open", 1, "", 99).unwrap_err().starts_with("stale_rev:"));
        assert!(check_claim("t_1", "open", 1, "", 1).is_ok());
        assert!(check_claim("t_1", "open", 1, "", 0).is_ok());
        assert!(check_claim("t_1", "claimed", 2, "bob", 0).unwrap_err().starts_with("not_claimable:"));
        assert!(check_report("t_1", "claimed", 2, "bob", 1, "done").unwrap_err().starts_with("not_owner:"));
        assert!(check_report("t_1", "claimed", 2, "bob", 2, "finished").unwrap_err().starts_with("bad_args:"));
        assert!(check_report("t_1", "claimed", 2, "bob", 2, "done").is_ok());
        assert!(check_report("t_1", "cancelled", 2, "bob", 2, "done").unwrap_err().starts_with("cancelled:"));
    }

    #[test]
    fn a_person_stops_any_task_and_a_helper_only_its_own_and_only_open_ones_are_deleted() {
        let open = Closing { status: "open", owner: 0, created_by: 7, caller: 3, caller_kind: "human" };
        assert!(check_cancel_task("t_1", &open).is_ok());
        // A helper that neither asked for it nor claimed it may not stop it.
        let stranger = Closing { caller: 9, caller_kind: "agent", ..open };
        assert!(check_cancel_task("t_1", &stranger).unwrap_err().starts_with("not_yours:"));
        assert!(check_cancel_task("t_1", &Closing { caller: 7, caller_kind: "agent", ..open }).is_ok());
        assert!(check_cancel_task("t_1", &Closing { caller: 9, caller_kind: "agent", owner: 9, ..open }).is_ok());
        // A finished task is history, not something to stop again.
        assert!(check_cancel_task("t_1", &Closing { status: "done", ..open }).unwrap_err().starts_with("already_done:"));
        assert!(check_cancel_task("t_1", &Closing { status: "cancelled", ..open })
            .unwrap_err().starts_with("already_cancelled:"));
        // Deleting only takes back work nobody started.
        assert!(check_delete_task("t_1", &open).is_ok());
        assert!(check_delete_task("t_1", &Closing { status: "claimed", owner: 3, ..open })
            .unwrap_err().starts_with("not_deletable:"));
        assert!(check_delete_task("t_1", &stranger).unwrap_err().starts_with("not_yours:"));
        // A brief can be stopped until it is closed; anyone on the team may stop a person's build.
        assert!(check_cancel_brief("b_1", "building", 2, 3, "human").is_ok());
        assert!(check_cancel_brief("b_1", "building", 2, 2, "agent").is_ok());
        assert!(check_cancel_brief("b_1", "building", 2, 3, "agent").unwrap_err().starts_with("not_yours:"));
        assert!(check_cancel_brief("b_1", "ready", 2, 3, "human").unwrap_err().starts_with("already_closed:"));
        assert!(check_cancel_brief("b_1", "cancelled", 2, 3, "human").unwrap_err().starts_with("already_closed:"));
    }

    #[test]
    fn a_free_helper_may_pick_up_only_a_busy_helpers_queued_work() {
        let ok = Pickup { status: "open", assignee: "bob", caller: "ann", caller_busy: false, assignee_busy: true, held_by: None };
        assert!(check_pickup("t_1", &ok).is_ok());
        assert!(check_pickup("t_1", &Pickup { status: "claimed", ..ok }).unwrap_err().starts_with("not_claimable:"));
        assert!(check_pickup("t_1", &Pickup { assignee: "", ..ok }).unwrap_err().starts_with("bad_args:"));
        assert!(check_pickup("t_1", &Pickup { assignee: "ANN", ..ok }).unwrap_err().starts_with("bad_args:"));
        assert!(check_pickup("t_1", &Pickup { caller_busy: true, ..ok }).unwrap_err().starts_with("busy:"));
        assert!(check_pickup("t_1", &Pickup { assignee_busy: false, ..ok }).unwrap_err().starts_with("not_spare:"));
        assert!(check_pickup("t_1", &Pickup { held_by: Some("bob"), ..ok }).unwrap_err().starts_with("not_spare:"));
    }

    #[test]
    fn a_humans_message_is_steering_and_unknown_names_are_queued() {
        assert_eq!(message_kind("human", "message", None).unwrap(), "steer");
        assert_eq!(message_kind("agent", "message", None).unwrap(), "message");
        assert_eq!(message_kind("human", "notice", None).unwrap(), "notice");
        assert_eq!(message_kind("human", "command", Some("agent")).unwrap(), "steer");
        assert_eq!(message_kind("agent", "command", Some("agent")).unwrap(), "message");
        assert_eq!(message_kind("human", "command", Some("human")).unwrap(), "chat");
        assert_eq!(message_kind("human", "chat", None).unwrap(), "chat");
        assert!(message_kind("human", "shout", None).is_err());
        assert_eq!(address("all", None), "all");
        assert_eq!(address("", None), "all");
        assert_eq!(address("Bob", Some(4)), "member:4");
        assert_eq!(address("Dana-Codex", None), "@dana-codex");
    }

    #[test]
    fn owners_of_recomputed_parts_are_notified_but_not_the_actor() {
        let recomputed = vec!["Mount".to_string(), "Lid".to_string(), "Free".to_string()];
        let out = owners_to_notify(&recomputed, 1, |n| match n { "Mount" => Some(2), "Lid" => Some(1), _ => None });
        assert_eq!(out.len(), 1);
        assert_eq!(out[&2], vec!["Mount".to_string()]);
    }

    #[test]
    fn invite_tokens_are_compared_by_sha256() {
        let a = hash_token("abc");
        assert_eq!(a, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        assert_eq!(hash_token(" abc\n"), a);
        assert!(valid_kind("reviewer") && !valid_kind("admin"));
        assert!(valid_role("lead") && !valid_role("owner"));
    }

    #[test]
    fn brep_and_storage_status_are_queries_and_prune_storage_is_a_write() {
        assert!(is_query("brep") && is_query("storage_status"));
        assert!(!is_write("brep") && !is_write("storage_status"));
        assert!(is_file_write("prune_storage") && is_write("prune_storage") && !is_mutating("prune_storage"));
        assert!(check_op("brep", &json!({}), &[], |_| None, |_| true, 1).is_ok());
        assert!(check_op("prune_storage", &json!({}), &[], |_| None, |_| true, 1).is_ok());
    }

    #[test]
    fn op_result_blobs_expire_after_ten_minutes_and_nothing_else_does() {
        let now = 10_000_000;
        let ten = OP_RESULT_TTL_MS;
        assert_eq!(ten, 600_000);
        assert!(!op_result_expired("op-result:o_1", now - ten, now), "exactly 10 min is not yet expired");
        assert!(op_result_expired("op-result:o_1", now - ten - 1, now));
        assert!(!op_result_expired("op-result:o_1", now, now));
        assert!(!op_result_expired("op-result:o_1", now + 5, now), "future created_ms never expires");
        assert!(!op_result_expired("model-1.stl", 0, now));
        assert!(!op_result_expired("Op-Result:o_1", 0, now), "prefix is case sensitive");
        assert!(!op_result_expired("", 0, now));
    }

    #[test]
    fn library_ops_are_known_and_inserting_reserves_the_new_name() {
        assert!(is_mutating("insert_component") && is_mutating("insert_standard_part"));
        assert!(is_file_write("library_add") && is_write("library_delete") && !is_mutating("library_add"));
        assert!(is_query("library_search") && is_query("analyze_component") && !is_write("library_recommend"));
        let args = json!({"component": "c_abc", "name": "Bearing1"});
        assert_eq!(created_name("insert_component", &args), Some("Bearing1".into()));
        let err = check_op("insert_component", &args, &[], |_| h(1, "alice"), |_| false, 2).unwrap_err();
        assert!(err.starts_with("locked:"), "{err}");
        assert!(check_op("insert_standard_part", &json!({"family": "iso4762", "name": "Bolt"}), &[], |_| None, |_| false, 1).is_ok());
        assert_eq!(inserted_component("insert_component", &args), Some("c_abc".into()));
        assert_eq!(inserted_component("insert_component", &json!({"component": "../x"})), None);
        assert_eq!(inserted_component("create_primitive", &args), None);
    }

    #[test]
    fn settings_come_from_the_shared_spec_and_are_range_checked() {
        let specs = setting_specs();
        assert!(specs.len() >= 10);
        assert!(specs.iter().any(|s| s.key == "tol_clearance" && s.param && s.unit == "mm"));
        assert!(check_setting("tol_clearance", 0.3, "").is_ok());
        let c = setting_spec("tol_clearance").unwrap();
        assert!(check_setting("tol_clearance", c.default_num, "").is_ok(), "the default is in range");
        assert_eq!(setting_spec("hole_clearance_class").unwrap().default_text, "normal");
        for s in &specs {
            let t = if s.kind == "choice" { s.default_text.clone() } else { String::new() };
            assert!(check_setting(&s.key, s.default_num, &t).is_ok(), "default of {} is valid", s.key);
            assert!(!s.param || s.kind == "num", "only numbers mirror into Params: {}", s.key);
        }
        assert!(check_setting("tol_clearance", 5.0, "").unwrap_err().starts_with("bad_args:"));
        assert!(check_setting("tol_clearance", f64::NAN, "").is_err());
        assert!(check_setting("tol_press_fit", -0.1, "").is_ok());
        assert!(check_setting("hole_clearance_class", 0.0, "close").is_ok());
        assert!(check_setting("hole_clearance_class", 0.0, "tight").is_err());
        assert!(check_setting("warp_speed", 1.0, "").unwrap_err().contains("unknown setting"));
        assert!(can_change_settings("human"));
        assert!(!can_change_settings("agent") && !can_change_settings("reviewer") && !can_change_settings("host"));
    }

    #[test]
    fn only_library_ops_may_change_library_rows() {
        let r = json!({"_components": [{"id": "c_1", "name": "Bracket"}, {"id": "bad id"}, {"name": "no id"}],
                       "_component_deletes": ["c_2", "../../etc"]});
        let c = component_changes("library_add", &r);
        assert_eq!(c.upserts.len(), 1);
        assert_eq!(c.upserts[0]["name"], "Bracket");
        assert_eq!(c.deletes, vec!["c_2".to_string()]);
        assert_eq!(component_changes("save_file", &r), ComponentChanges::default());
        assert!(valid_component_id("c_m3_bolt_12") && !valid_component_id("c_") && !valid_component_id("C_X"));
    }

    #[test]
    fn only_fea_events_ride_on_op_results() {
        let r = json!({"ok": true, "_events": [
            {"kind": "fea_report", "data": {"folder": "fea/x"}},
            {"kind": "op", "data": {"name": "forged"}},
            {"kind": "fea_failed", "data": "not an object"},
            {"data": {}}
        ]});
        let evs = result_events(&r);
        assert_eq!(evs, vec![("fea_report".to_string(), json!({"folder": "fea/x"}))]);
        assert!(result_events(&json!({"ok": true})).is_empty());
        let many = json!({"_events": (0..20).map(|i| json!({"kind": "fea_progress", "data": {"i": i}})).collect::<Vec<_>>()});
        assert_eq!(result_events(&many).len(), MAX_RESULT_EVENTS);
    }


    #[test]
    fn a_rename_needs_a_valid_name_nobody_else_holds() {
        assert!(check_rename("lid-builder", None, 7).is_ok());
        assert!(check_rename("Lid-Builder", Some(7), 7).is_ok()); // only the case of its own name changed
        let taken = check_rename("lid-builder", Some(3), 7).unwrap_err();
        assert!(taken.starts_with("name_in_use:"), "{taken}");
        assert!(check_rename("lid builder", None, 7).unwrap_err().starts_with("bad_args:"));
        assert!(check_rename("", None, 7).is_err());
    }
}
