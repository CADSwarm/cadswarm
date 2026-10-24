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
];
/// Read-only ops the host answers: FreeCAD queries, Elastic lookups, project file reads.
pub const QUERIES: &[&str] = &[
    "get_object", "interference", "export", "dependents", "printability",
    "lookup_board", "recall_designs",
    "list_files", "read_file", "file_history",
    "brep", "storage_status",
    "faces", "fea_status", "fea_reports",
];
/// Project-file writes (files/ and snapshots/ on the host): no FreeCAD object locks, but they change things.
pub const FILE_WRITES: &[&str] = &[
    "save_file", "make_folder", "move_file", "delete_file", "restore_file", "snapshot", "prune_storage",
    // FEA: a job copies the model and later writes its report into files/fea/.
    "fea_run", "fea_publish",
];
/// Team events a completed (non-mutating) op may carry in its result as `_events` for complete_op to emit:
/// the host's helper can't call reducers, and an FEA report must pop up for every member.
pub const RESULT_EVENTS: &[&str] = &["fea_started", "fea_progress", "fea_report", "fea_failed"];
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
        "create_primitive" | "create_extrusion" | "boolean" | "run_part_script" => str_arg(args, "name"),
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
pub fn plan_sweep(now: u64, members: &[Presence]) -> SweepPlan {
    SweepPlan {
        to_stale: members
            .iter()
            .filter(|m| m.status == "active" && !m.online && now.saturating_sub(m.last_seen_ms) > STALE_AFTER_MS)
            .map(|m| m.id)
            .collect(),
        to_orphan: members
            .iter()
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

pub fn check_report(task_id: &str, owner: u64, owner_name: &str, caller: u64, status: &str) -> Result<(), String> {
    if status != "done" && status != "blocked" {
        return Err("bad_args: status must be done or blocked".into());
    }
    if owner != caller {
        return Err(format!("not_owner: Task {task_id} is owned by {owner_name}, not you"));
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
            id, status: status.into(), last_seen_ms, stale_since_ms, online,
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
    fn task_claim_and_report_rules() {
        assert!(check_claim("t_1", "open", 1, "", 99).unwrap_err().starts_with("stale_rev:"));
        assert!(check_claim("t_1", "open", 1, "", 1).is_ok());
        assert!(check_claim("t_1", "open", 1, "", 0).is_ok());
        assert!(check_claim("t_1", "claimed", 2, "bob", 0).unwrap_err().starts_with("not_claimable:"));
        assert!(check_report("t_1", 2, "bob", 1, "done").unwrap_err().starts_with("not_owner:"));
        assert!(check_report("t_1", 2, "bob", 2, "finished").unwrap_err().starts_with("bad_args:"));
        assert!(check_report("t_1", 2, "bob", 2, "done").is_ok());
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
