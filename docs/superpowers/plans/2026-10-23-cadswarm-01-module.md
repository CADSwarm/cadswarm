# CadSwarm SpacetimeDB Module Implementation Plan (Person A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port every coordination rule in `cadswarm/hub/core.py` into a SpacetimeDB module (tables, member-only views, reducers), publish it, and hand everyone generated bindings by M0.

**Architecture:** Two crates. `rules/` is plain Rust with no SpacetimeDB dependency, holding every decision (lock checks, join, takeover, stale sweep, task and message rules) with native unit tests ported from `tests/test_core.py`. `module/` is the SpacetimeDB module: tables, views and reducers that are thin glue over `rules`. An end-to-end Python smoke test drives the published module over HTTP.

**Tech Stack:** SpacetimeDB 2.x CLI and Rust module crate, `serde_json`, `sha2`, `hex`, Python 3 stdlib for the smoke test.

Read first: `docs/superpowers/plans/2026-10-23-cadswarm-00-overview.md` (the contract). Every name below must match it.

## Global Constraints

- All tables private; clients read only `v_*` views.
- Client-read column types: `u64`, `u32`, `f64`, `f32`, `bool`, `String`, `Vec<String>`, `Vec<u8>`, `Vec<f64>`, `Vec<f32>`; no `Option`, enums or `Timestamp` columns (except `member.identity`).
- Times are `u64` ms since the Unix epoch.
- Reducer errors are `Err("<code>: <message>")`.
- Every reducer except `init`, `on_connect`, `on_disconnect`, `redeem_invite`, `rejoin`, `sweep` takes `fence: u32` first.
- Stale after 30 s, orphaned after 600 s, activity kept to 200 rows per member, invite TTL ≤ 86 400 s.

## File structure

| File | Responsibility |
|---|---|
| `rules/Cargo.toml`, `rules/src/lib.rs` | Pure decisions + tests |
| `module/Cargo.toml` | Module crate (scaffolded by `spacetime init`) |
| `module/src/lib.rs` | `mod` declarations only |
| `module/src/tables.rs` | All table structs |
| `module/src/views.rs` | The 17 `v_*` views |
| `module/src/util.rs` | Shared helpers: time, caller lookup, fence check, locks, events, system messages, pointers |
| `module/src/membership.rs` | init, lifecycle, invites, redeem, rejoin, heartbeat, leave, revoke, take_over, context cursor |
| `module/src/locks.rs` | lock_objects, release_objects |
| `module/src/ops.rs` | submit_op, start_op, complete_op, sync_objects, put_preview, put_artifact |
| `module/src/tasks.rs` | tasks, messages, receipts |
| `module/src/presence.rs` | activity, pointers, briefs, reviews, bed |
| `module/src/sweep.rs` | Scheduled sweep |
| `module/src/handoff.rs` | Handoffs to an AI and their snapshot |
| `module/tests/smoke.py` | End-to-end test against a local server |
| `scripts/publish-local.ps1` | Publish + regenerate bindings |

---

### Task 1: Toolchain and scaffold

**Files:**
- Create: `module/` (via `spacetime init`), `rules/Cargo.toml`, `rules/src/lib.rs`

**Interfaces:**
- Produces: a module that builds with `spacetime build`; a `cadswarm-rules` crate the module depends on by path.

- [ ] **Step 0: Work in your own worktree**

Other people and sessions share the main checkout, so never switch its branch. From the main checkout:
```powershell
git fetch origin
git worktree add ..\cadswarm-module -b feat/module origin/main
cd ..\cadswarm-module
```
Every command in this plan runs from `..\cadswarm-module`.

- [ ] **Step 1: Install the SpacetimeDB CLI and the wasm target**

Windows (PowerShell):
```powershell
iwr https://windows.spacetimedb.com -useb | iex
rustup target add wasm32-unknown-unknown
spacetime --version
```
macOS/Linux: `curl -sSf https://install.spacetimedb.com | sh`.
Expected: a 2.x version string.

- [ ] **Step 2: Scaffold the module**

```powershell
spacetime init --help
spacetime init --lang rust module
```
If `init` asks for a project name or template, use `cadswarm` and the basic Rust server template. Open `module/src/lib.rs` and note the exact attribute syntax the template uses for tables (`#[spacetimedb::table(accessor = ...)]` in 2.x docs; if the template uses `name = ...`, use that spelling everywhere this plan says `accessor =`) and for `ctx.sender()` (method in 2.x; if the template uses the field `ctx.sender`, use that everywhere).

- [ ] **Step 3: Create the rules crate**

`rules/Cargo.toml`:
```toml
[package]
name = "cadswarm-rules"
version = "0.1.0"
edition = "2021"

[dependencies]
serde_json = "1"
sha2 = "0.10"
hex = "0.4"
```
`rules/src/lib.rs`:
```rust
//! CadSwarm coordination rules, free of SpacetimeDB so they unit-test natively.
//! Ported from cadswarm/hub/core.py; the module's reducers are thin glue over these.
```

- [ ] **Step 4: Add dependencies to the module**

Append to `module/Cargo.toml` under `[dependencies]` (keep what the template put there):
```toml
cadswarm-rules = { path = "../rules" }
serde_json = "1"
```

- [ ] **Step 5: Build both**

```powershell
cargo test --manifest-path rules/Cargo.toml
spacetime build --module-path module
```
Expected: `0 passed` for rules; the module builds (the template's example reducer is still there). If `spacetime build` does not accept `--module-path`, run `spacetime build --help` and use the path flag it lists, or run it from inside `module/`.

- [ ] **Step 6: Commit**

```powershell
git add rules module
git commit -m "feat(module): scaffold SpacetimeDB module and rules crate"
```

---

### Task 2: Pure rules with ported tests

**Files:**
- Modify: `rules/src/lib.rs`

**Interfaces:**
- Produces (used by the module tasks): `STALE_AFTER_MS`, `ORPHAN_AFTER_MS`, `ACTIVITY_KEEP`, `INVITE_MAX_TTL_SECS`, `is_mutating(&str) -> bool`, `is_query(&str) -> bool`, `is_file_write(&str) -> bool`, `is_write(&str) -> bool`, `created_name(&str, &Value) -> Option<String>`, `needed_locks(&str, &Value, impl Fn(&str) -> bool) -> Vec<String>`, `struct Holder { id: u64, name: String, status: String }`, `check_op(kind, args, mine, holder, exists, caller) -> Result<(), String>`, `parse_expected_revs(&str) -> Result<BTreeMap<String, u32>, String>`, `check_expected_revs(&BTreeMap<String,u32>, impl Fn(&str) -> (u32, String)) -> Result<(), String>`, `enum Join { Fresh, Inherit(u64) }`, `join_decision(&str, Option<(u64, &str)>) -> Result<Join, String>`, `check_name(&str) -> Result<(), String>`, `check_take_over(...) -> Result<(), String>`, `struct Presence`, `struct SweepPlan`, `plan_sweep(u64, &[Presence]) -> SweepPlan`, `check_claim(...)`, `check_report(...)`, `message_kind(&str, &str) -> String`, `address(&str, Option<u64>) -> String`, `owners_to_notify(&[String], u64, impl Fn(&str) -> Option<u64>) -> BTreeMap<u64, Vec<String>>`, `hash_token(&str) -> String`, `valid_kind`, `valid_role`.

- [ ] **Step 1: Write the failing tests**

Append to `rules/src/lib.rs`:
```rust
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
    fn quiet_members_go_stale_unless_connected_and_long_stale_ones_are_orphaned() {
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
        assert_eq!(message_kind("human", "message"), "steer");
        assert_eq!(message_kind("agent", "message"), "message");
        assert_eq!(message_kind("human", "notice"), "notice");
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
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cargo test --manifest-path rules/Cargo.toml`
Expected: compile errors (`cannot find function check_op`, etc.).

- [ ] **Step 3: Implement the rules**

Insert above the `#[cfg(test)]` block in `rules/src/lib.rs`:
```rust
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
];
/// Project-file writes (files/ and snapshots/ on the host): no FreeCAD object locks, but they change things.
pub const FILE_WRITES: &[&str] = &["save_file", "make_folder", "move_file", "delete_file", "restore_file", "snapshot"];

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
pub fn plan_sweep(now: u64, members: &[Presence]) -> SweepPlan {
    SweepPlan {
        to_stale: members
            .iter()
            .filter(|m| m.status == "active" && !m.online && now.saturating_sub(m.last_seen_ms) > STALE_AFTER_MS)
            .map(|m| m.id)
            .collect(),
        to_orphan: members
            .iter()
            .filter(|m| m.status == "stale" && now.saturating_sub(m.stale_since_ms) > ORPHAN_AFTER_MS)
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
pub fn message_kind(sender_kind: &str, requested: &str) -> String {
    if sender_kind == "human" && requested == "message" { "steer".into() } else { requested.into() }
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cargo test --manifest-path rules/Cargo.toml`
Expected: `test result: ok. 14 passed`.

- [ ] **Step 5: Commit**

```powershell
git add rules
git commit -m "feat(rules): coordination rules ported from the hub, with tests"
```

---

### Task 3: Tables, views and shared helpers

**Files:**
- Create: `module/src/tables.rs`, `module/src/views.rs`, `module/src/util.rs`
- Modify: `module/src/lib.rs` (replace the template entirely)

**Interfaces:**
- Consumes: `cadswarm_rules::Holder`.
- Produces: the table structs and accessors named in the contract; helpers `now_ms`, `latest_member`, `me`, `require_kind`, `name_of`, `live_by_name`, `lock_key`, `holder`, `locks_of`, `set_lock`, `emit`, `system_message`, `set_pointer`, `object_rev`.

- [ ] **Step 1: Replace `module/src/lib.rs`**

```rust
//! CadSwarm's shared world state: who is here, who holds which part, the op queue, tasks,
//! messages, live pointers and previews. Every rule lives in the `cadswarm_rules` crate;
//! reducers here are glue between those rules and the tables.

pub mod tables;
pub mod util;
pub mod views;

pub mod handoff;
pub mod locks;
pub mod membership;
pub mod ops;
pub mod presence;
pub mod sweep;
pub mod tasks;
```

- [ ] **Step 2: Write `module/src/tables.rs`**

```rust
use spacetimedb::{table, Identity, ScheduleAt};

#[table(accessor = workspace)]
#[derive(Clone)]
pub struct Workspace {
    #[primary_key]
    pub id: u64,
    pub name: String,
    pub owner: Identity,
    pub bed_x_mm: f64,
    pub bed_y_mm: f64,
    pub bed_z_mm: f64,
}

#[table(accessor = member)]
#[derive(Clone)]
pub struct Member {
    #[primary_key]
    #[auto_inc]
    pub id: u64,
    #[index(btree)]
    pub ws: u64,
    #[index(btree)]
    pub identity: Identity,
    pub name: String,
    pub kind: String,
    pub client: String,
    pub role: String,
    pub status: String,
    pub status_text: String,
    pub fence: u32,
    pub last_seen_ms: u64,
    pub joined_ms: u64,
    pub stale_since_ms: u64,
    pub taken_over_by: u64,
    pub endpoint_id: String,
    pub online: bool,
    pub context_seq: u64,
}

#[table(accessor = invite)]
#[derive(Clone)]
pub struct Invite {
    #[primary_key]
    pub token_hash: String,
    #[index(btree)]
    pub ws: u64,
    pub kind: String,
    pub role: String,
    pub created_by: u64,
    pub expires_ms: u64,
    pub used_by: u64,
}

#[table(accessor = cad_lock)]
#[derive(Clone)]
pub struct CadLock {
    #[primary_key]
    pub key: String,
    #[index(btree)]
    pub ws: u64,
    pub object: String,
    #[index(btree)]
    pub holder: u64,
    pub acquired_ms: u64,
}

#[table(accessor = cad_object)]
#[derive(Clone)]
pub struct CadObject {
    #[primary_key]
    pub key: String,
    #[index(btree)]
    pub ws: u64,
    pub name: String,
    pub label: String,
    pub type_id: String,
    pub rev: u32,
    pub placement_json: String,
    pub bbox: Vec<f64>,
    pub volume: f64,
    pub visible: bool,
    pub broken: bool,
    pub uses: Vec<String>,
    pub used_by: Vec<String>,
    pub expressions_json: String,
    pub last_actor: u64,
    pub updated_ms: u64,
}

#[table(accessor = param)]
#[derive(Clone)]
pub struct Param {
    #[primary_key]
    pub key: String,
    #[index(btree)]
    pub ws: u64,
    pub alias: String,
    pub value: String,
    pub num: f64,
    pub updated_ms: u64,
}

#[table(accessor = op)]
#[derive(Clone)]
pub struct Op {
    #[primary_key]
    pub id: String,
    #[index(btree)]
    pub ws: u64,
    pub by: u64,
    pub fence: u32,
    pub kind: String,
    pub args_json: String,
    pub expected_revs_json: String,
    pub allowed: Vec<String>,
    pub mutating: bool,
    pub status: String,
    pub result_json: String,
    pub error_code: String,
    pub error: String,
    pub created_ms: u64,
    pub started_ms: u64,
    pub finished_ms: u64,
}

#[table(accessor = task)]
#[derive(Clone)]
pub struct Task {
    #[primary_key]
    pub id: String,
    #[index(btree)]
    pub ws: u64,
    pub title: String,
    pub detail: String,
    pub objects: Vec<String>,
    pub status: String,
    pub owner: u64,
    pub created_by: u64,
    pub rev: u32,
    pub result_json: String,
    pub assignee: String,
    pub brief: String,
    pub created_ms: u64,
    pub updated_ms: u64,
}

#[table(accessor = message)]
#[derive(Clone)]
pub struct Message {
    #[primary_key]
    pub id: String,
    #[index(btree)]
    pub ws: u64,
    pub from: u64,
    pub to: String,
    pub kind: String,
    pub body: String,
    pub task_id: String,
    pub created_ms: u64,
}

#[table(accessor = receipt)]
#[derive(Clone)]
pub struct Receipt {
    #[primary_key]
    pub key: String,
    #[index(btree)]
    pub ws: u64,
    pub message_id: String,
    pub member: u64,
    pub state: String,
    pub at_ms: u64,
}

#[table(accessor = team_event)]
#[derive(Clone)]
pub struct TeamEvent {
    #[primary_key]
    #[auto_inc]
    pub seq: u64,
    #[index(btree)]
    pub ws: u64,
    pub kind: String,
    pub actor: u64,
    pub data_json: String,
    pub at_ms: u64,
}

#[table(accessor = activity)]
#[derive(Clone)]
pub struct Activity {
    #[primary_key]
    #[auto_inc]
    pub id: u64,
    #[index(btree)]
    pub ws: u64,
    #[index(btree)]
    pub member: u64,
    pub kind: String,
    pub summary: String,
    pub at_ms: u64,
}

#[table(accessor = pointer)]
#[derive(Clone)]
pub struct Pointer {
    #[primary_key]
    pub member: u64,
    #[index(btree)]
    pub ws: u64,
    pub object: String,
    pub point: Vec<f32>,
    pub mode: String,
    pub label: String,
    pub op: String,
    pub seq: u32,
    pub at_ms: u64,
}

#[table(accessor = brief)]
#[derive(Clone)]
pub struct Brief {
    #[primary_key]
    pub id: String,
    #[index(btree)]
    pub ws: u64,
    pub by: u64,
    pub text: String,
    pub board: String,
    pub status: String,
    pub plan_json: String,
    pub stl_hash: String,
    pub created_ms: u64,
    pub updated_ms: u64,
}

#[table(accessor = handoff)]
#[derive(Clone)]
pub struct Handoff {
    #[primary_key]
    pub id: String,
    #[index(btree)]
    pub ws: u64,
    pub from: u64,
    pub note: String,
    pub trigger: String,
    pub status: String,
    pub taker: u64,
    pub context_json: String,
    pub created_ms: u64,
    pub updated_ms: u64,
}

#[table(accessor = review)]
#[derive(Clone)]
pub struct Review {
    #[primary_key]
    pub id: String,
    #[index(btree)]
    pub ws: u64,
    pub brief: String,
    pub round: u32,
    pub passed: bool,
    pub findings_json: String,
    pub at_ms: u64,
}

#[table(accessor = preview)]
#[derive(Clone)]
pub struct Preview {
    #[primary_key]
    pub key: String,
    #[index(btree)]
    pub ws: u64,
    pub object: String,
    pub rev: u32,
    pub mesh: Vec<u8>,
}

#[table(accessor = artifact)]
#[derive(Clone)]
pub struct Artifact {
    #[primary_key]
    pub hash: String,
    #[index(btree)]
    pub ws: u64,
    pub name: String,
    pub mime: String,
    pub size: u64,
    pub bytes: Vec<u8>,
    pub brief: String,
    pub created_ms: u64,
}

#[table(accessor = sweep_schedule, scheduled(crate::sweep::sweep))]
pub struct SweepSchedule {
    #[primary_key]
    #[auto_inc]
    pub scheduled_id: u64,
    pub scheduled_at: ScheduleAt,
}
```
If the build later reports "conflicting implementations of trait `Clone`", the table macro already derives it: delete every `#[derive(Clone)]` line in this file.

- [ ] **Step 3: Write `module/src/util.rs`**

```rust
use crate::tables::*;
use cadswarm_rules::Holder;
use serde_json::Value;
use spacetimedb::{Identity, ReducerContext, Table};

pub fn now_ms(ctx: &ReducerContext) -> u64 {
    (ctx.timestamp.to_micros_since_unix_epoch() / 1000) as u64
}

/// The caller's most recent member row (re-joins after a takeover create a new row).
pub fn latest_member(ctx: &ReducerContext, who: Identity) -> Option<Member> {
    ctx.db.member().identity().filter(&who).max_by_key(|m| m.id)
}

/// The acting member, refusing superseded processes (core.py Hub.authenticate).
pub fn me(ctx: &ReducerContext, fence: u32) -> Result<Member, String> {
    let m = latest_member(ctx, ctx.sender())
        .ok_or_else(|| "not_member: join with an invite first".to_string())?;
    match m.status.as_str() {
        "taken_over" => {
            return Err(format!(
                "taken_over: Your work was taken over by {}. Call rejoin to continue as a new participant.",
                name_of(ctx, m.taken_over_by)
            ))
        }
        "left" => return Err("left: This participant left the session; call rejoin".into()),
        _ => {}
    }
    if fence != m.fence {
        return Err("fence_stale: This process was superseded by a newer one using the same identity; stop acting.".into());
    }
    Ok(m)
}

pub fn require_kind(m: &Member, kind: &str) -> Result<(), String> {
    if m.kind == kind { Ok(()) } else { Err(format!("not_allowed: only a {kind} may do this")) }
}

pub fn name_of(ctx: &ReducerContext, id: u64) -> String {
    if id == 0 {
        return "hub".into();
    }
    ctx.db.member().id().find(&id).map(|m| m.name).unwrap_or_else(|| format!("member {id}"))
}

pub fn live_by_name(ctx: &ReducerContext, ws: u64, name: &str) -> Option<Member> {
    ctx.db
        .member()
        .ws()
        .filter(&ws)
        .filter(|m| (m.status == "active" || m.status == "stale") && m.name.eq_ignore_ascii_case(name))
        .max_by_key(|m| m.id)
}

pub fn lock_key(ws: u64, obj: &str) -> String {
    format!("{ws}:{obj}")
}

pub fn holder(ctx: &ReducerContext, ws: u64, obj: &str) -> Option<Holder> {
    let l = ctx.db.cad_lock().key().find(&lock_key(ws, obj))?;
    let m = ctx.db.member().id().find(&l.holder);
    Some(Holder {
        id: l.holder,
        name: m.as_ref().map(|m| m.name.clone()).unwrap_or_default(),
        status: m.map(|m| m.status).unwrap_or_else(|| "unknown".into()),
    })
}

pub fn locks_of(ctx: &ReducerContext, member: u64) -> Vec<String> {
    let mut v: Vec<String> = ctx.db.cad_lock().holder().filter(&member).map(|l| l.object).collect();
    v.sort();
    v
}

pub fn set_lock(ctx: &ReducerContext, ws: u64, obj: &str, holder: u64) {
    let key = lock_key(ws, obj);
    let row = CadLock { key: key.clone(), ws, object: obj.to_string(), holder, acquired_ms: now_ms(ctx) };
    if ctx.db.cad_lock().key().find(&key).is_some() {
        ctx.db.cad_lock().key().update(row);
    } else {
        ctx.db.cad_lock().insert(row);
    }
}

/// (revision, name of last actor) of an object; (0, "nobody") if it does not exist.
pub fn object_rev(ctx: &ReducerContext, ws: u64, name: &str) -> (u32, String) {
    match ctx.db.cad_object().key().find(&lock_key(ws, name)) {
        Some(o) => (o.rev, name_of(ctx, o.last_actor)),
        None => (0, "nobody".into()),
    }
}

pub fn emit(ctx: &ReducerContext, ws: u64, kind: &str, actor: u64, data: Value) -> u64 {
    ctx.db
        .team_event()
        .insert(TeamEvent { seq: 0, ws, kind: kind.into(), actor, data_json: data.to_string(), at_ms: now_ms(ctx) })
        .seq
}

pub fn system_message(ctx: &ReducerContext, ws: u64, to: &str, body: String, kind: &str, task_id: &str) {
    // Messages are never deleted, so the row count makes the id unique within this timestamp.
    let id = format!("m_sys_{}_{}", now_ms(ctx), ctx.db.message().count());
    ctx.db.message().insert(Message {
        id,
        ws,
        from: 0,
        to: to.into(),
        kind: kind.into(),
        body,
        task_id: task_id.into(),
        created_ms: now_ms(ctx),
    });
}

/// Server-side pointer moves bump `seq`, so clients can drop out-of-order updates.
pub fn set_pointer(
    ctx: &ReducerContext,
    ws: u64,
    member: u64,
    object: &str,
    point: Vec<f32>,
    mode: &str,
    label: &str,
    op: &str,
) {
    let label: String = label.chars().take(60).collect();
    match ctx.db.pointer().member().find(&member) {
        Some(p) => {
            ctx.db.pointer().member().update(Pointer {
                object: object.into(),
                point,
                mode: mode.into(),
                label,
                op: op.into(),
                seq: p.seq + 1,
                at_ms: now_ms(ctx),
                ..p
            });
        }
        None => {
            ctx.db.pointer().insert(Pointer {
                member,
                ws,
                object: object.into(),
                point,
                mode: mode.into(),
                label,
                op: op.into(),
                seq: 1,
                at_ms: now_ms(ctx),
            });
        }
    }
}
```

- [ ] **Step 4: Write `module/src/views.rs`**

```rust
use crate::tables::*;
use spacetimedb::{view, ViewContext};

/// The caller's workspace if they are a live member; views return nothing to anyone else.
fn caller_ws(ctx: &ViewContext) -> Option<u64> {
    ctx.db
        .member()
        .identity()
        .filter(&ctx.sender())
        .filter(|m| m.status == "active" || m.status == "stale")
        .max_by_key(|m| m.id)
        .map(|m| m.ws)
}

#[view(accessor = v_me, public)]
pub fn v_me(ctx: &ViewContext) -> Vec<Member> {
    ctx.db.member().identity().filter(&ctx.sender()).max_by_key(|m| m.id).into_iter().collect()
}

#[view(accessor = v_workspace, public)]
pub fn v_workspace(ctx: &ViewContext) -> Vec<Workspace> {
    caller_ws(ctx).and_then(|ws| ctx.db.workspace().id().find(&ws)).into_iter().collect()
}

macro_rules! ws_view {
    ($name:ident, $table:ident, $row:ty) => {
        #[view(accessor = $name, public)]
        pub fn $name(ctx: &ViewContext) -> Vec<$row> {
            match caller_ws(ctx) {
                Some(ws) => ctx.db.$table().ws().filter(&ws).collect(),
                None => Vec::new(),
            }
        }
    };
}

ws_view!(v_member, member, Member);
ws_view!(v_lock, cad_lock, CadLock);
ws_view!(v_object, cad_object, CadObject);
ws_view!(v_param, param, Param);
ws_view!(v_op, op, Op);
ws_view!(v_task, task, Task);
ws_view!(v_message, message, Message);
ws_view!(v_receipt, receipt, Receipt);
ws_view!(v_event, team_event, TeamEvent);
ws_view!(v_activity, activity, Activity);
ws_view!(v_pointer, pointer, Pointer);
ws_view!(v_brief, brief, Brief);
ws_view!(v_review, review, Review);
ws_view!(v_preview, preview, Preview);
ws_view!(v_artifact, artifact, Artifact);
ws_view!(v_handoff, handoff, Handoff);
```
If the `#[view]` attribute does not accept being generated by `macro_rules!`, write the 15 functions out by hand in the same shape.

- [ ] **Step 5: Create empty reducer files so the crate builds**

Create each of `module/src/handoff.rs`, `locks.rs`, `membership.rs`, `ops.rs`, `presence.rs`, `tasks.rs` containing only:
```rust
//! Reducers: see the module plan.
```
and `module/src/sweep.rs`:
```rust
use crate::tables::SweepSchedule;
use spacetimedb::{reducer, ReducerContext};

#[reducer]
pub fn sweep(ctx: &ReducerContext, _row: SweepSchedule) -> Result<(), String> {
    if ctx.sender() != ctx.identity() {
        return Err("not_allowed: sweep is scheduled, not called".into());
    }
    Ok(())
}
```

- [ ] **Step 6: Build**

Run: `spacetime build --module-path module`
Expected: builds. Fix any accessor syntax differences noted in Task 1 Step 2 here, once, and keep them consistent in later tasks.

- [ ] **Step 7: Commit**

```powershell
git add module
git commit -m "feat(module): tables, member-only views and shared helpers"
```

---

### Task 4: Membership, invites and takeover

**Files:**
- Modify: `module/src/membership.rs`

**Interfaces:**
- Consumes: `cadswarm_rules::{hash_token, check_name, join_decision, Join, check_take_over, valid_kind, valid_role, INVITE_MAX_TTL_SECS}`, util helpers.
- Produces: reducers `init`, `on_connect`, `on_disconnect`, `redeem_invite`, `rejoin`, `heartbeat`, `set_context_seq`, `leave`, `create_invite`, `revoke_member`, `take_over`; `pub fn leave_inner(ctx, &Member)` and `pub fn resume_tasks(ctx, member_id)` used by `sweep`.

- [ ] **Step 1: Write `module/src/membership.rs`**

```rust
use crate::tables::*;
use crate::util::*;
use cadswarm_rules as rules;
use serde_json::json;
use spacetimedb::{reducer, Identity, ReducerContext, ScheduleAt, Table};
use std::time::Duration;

#[reducer(init)]
pub fn init(ctx: &ReducerContext) {
    let now = now_ms(ctx);
    ctx.db.workspace().insert(Workspace {
        id: 1,
        name: "cadswarm".into(),
        owner: ctx.sender(),
        bed_x_mm: 180.0,
        bed_y_mm: 180.0,
        bed_z_mm: 180.0,
    });
    ctx.db.member().insert(new_member(1, ctx.sender(), "host", "host", "host", "lead", "", now));
    ctx.db.sweep_schedule().insert(SweepSchedule {
        scheduled_id: 0,
        scheduled_at: ScheduleAt::Interval(Duration::from_secs(5).into()),
    });
}

fn new_member(ws: u64, identity: Identity, name: &str, kind: &str, client: &str, role: &str, endpoint_id: &str, now: u64) -> Member {
    Member {
        id: 0,
        ws,
        identity,
        name: name.into(),
        kind: kind.into(),
        client: client.into(),
        role: role.into(),
        status: "active".into(),
        status_text: String::new(),
        fence: 1,
        last_seen_ms: now,
        joined_ms: now,
        stale_since_ms: 0,
        taken_over_by: 0,
        endpoint_id: endpoint_id.into(),
        online: false,
        context_seq: 0,
    }
}

#[reducer(client_connected)]
pub fn on_connect(ctx: &ReducerContext) {
    if let Some(m) = latest_member(ctx, ctx.sender()) {
        ctx.db.member().id().update(Member { online: true, last_seen_ms: now_ms(ctx), ..m });
    }
}

#[reducer(client_disconnected)]
pub fn on_disconnect(ctx: &ReducerContext) {
    if let Some(m) = latest_member(ctx, ctx.sender()) {
        let (ws, id) = (m.ws, m.id);
        ctx.db.member().id().update(Member { online: false, last_seen_ms: now_ms(ctx), ..m });
        // Keep where they were (object, point, label): a handoff snapshot needs it.
        let (object, point, label) = ctx
            .db
            .pointer()
            .member()
            .find(&id)
            .map(|p| (p.object, p.point, p.label))
            .unwrap_or_default();
        set_pointer(ctx, ws, id, &object, point, "idle", &label, "");
    }
}

#[reducer]
pub fn create_invite(ctx: &ReducerContext, fence: u32, token_hash: String, kind: String, role: String, ttl_secs: u64) -> Result<(), String> {
    let m = me(ctx, fence)?;
    if !rules::valid_kind(&kind) || !rules::valid_role(&role) {
        return Err("bad_args: kind must be human|agent|host|reviewer and role lead|member".into());
    }
    if (kind == "host" || kind == "reviewer") && m.kind != "host" {
        return Err("not_allowed: only a host may invite a host or reviewer".into());
    }
    if token_hash.len() != 64 {
        return Err("bad_args: token_hash must be a sha256 hex digest".into());
    }
    let ttl = ttl_secs.clamp(60, rules::INVITE_MAX_TTL_SECS);
    ctx.db.invite().insert(Invite {
        token_hash,
        ws: m.ws,
        kind,
        role,
        created_by: m.id,
        expires_ms: now_ms(ctx) + ttl * 1000,
        used_by: 0,
    });
    Ok(())
}

#[reducer]
pub fn redeem_invite(ctx: &ReducerContext, token: String, name: String, client: String, endpoint_id: String) -> Result<(), String> {
    let hash = rules::hash_token(&token);
    let inv = ctx.db.invite().token_hash().find(&hash).ok_or_else(|| "bad_invite: unknown invite".to_string())?;
    if inv.used_by != 0 {
        return Err("bad_invite: this invite was already used".into());
    }
    if now_ms(ctx) > inv.expires_ms {
        return Err("bad_invite: this invite expired".into());
    }
    if let Some(m) = latest_member(ctx, ctx.sender()) {
        if m.status == "active" || m.status == "stale" {
            return Err("already_member: this identity is already a member; call rejoin".into());
        }
    }
    let member = join_as(ctx, inv.ws, &name, &inv.kind, &client, &inv.role, &endpoint_id)?;
    ctx.db.invite().token_hash().update(Invite { used_by: member.id, ..inv });
    Ok(())
}

/// Creates the member row, delivers mail queued for the name, and inherits a stale namesake's work.
fn join_as(ctx: &ReducerContext, ws: u64, name: &str, kind: &str, client: &str, role: &str, endpoint_id: &str) -> Result<Member, String> {
    rules::check_name(name)?;
    let namesake = live_by_name(ctx, ws, name);
    let decision = rules::join_decision(name, namesake.as_ref().map(|m| (m.id, m.status.as_str())))?;
    let member = ctx.db.member().insert(new_member(ws, ctx.sender(), name, kind, client, role, endpoint_id, now_ms(ctx)));
    emit(ctx, ws, "joined", member.id, json!({"name": name, "participant_kind": kind, "client": client, "role": role}));
    let queued = format!("@{}", name.to_lowercase());
    let waiting: Vec<Message> = ctx.db.message().ws().filter(&ws).filter(|m| m.to == queued).collect();
    for m in waiting {
        ctx.db.message().id().update(Message { to: format!("member:{}", member.id), ..m });
    }
    if let rules::Join::Inherit(old) = decision {
        if let Some(target) = ctx.db.member().id().find(&old) {
            take_over_inner(ctx, &member, &target);
        }
    }
    Ok(member)
}

#[reducer]
pub fn rejoin(ctx: &ReducerContext, client: String) -> Result<(), String> {
    let m = latest_member(ctx, ctx.sender()).ok_or_else(|| "not_member: join with an invite first".to_string())?;
    if m.status == "left" || m.status == "taken_over" {
        let (ws, name, kind, role, endpoint) = (m.ws, m.name.clone(), m.kind.clone(), m.role.clone(), m.endpoint_id.clone());
        join_as(ctx, ws, &name, &kind, &client, &role, &endpoint)?;
        return Ok(());
    }
    let was_stale = m.status == "stale";
    let (ws, id, name, fence) = (m.ws, m.id, m.name.clone(), m.fence + 1);
    let client = if client.is_empty() { m.client.clone() } else { client };
    ctx.db.member().id().update(Member {
        fence,
        status: "active".into(),
        last_seen_ms: now_ms(ctx),
        stale_since_ms: 0,
        client,
        ..m
    });
    if was_stale {
        resume_tasks(ctx, id);
    }
    emit(ctx, ws, "rejoined", id, json!({"name": name, "fence": fence}));
    Ok(())
}

pub fn resume_tasks(ctx: &ReducerContext, member: u64) {
    let now = now_ms(ctx);
    let ws = ctx.db.member().id().find(&member).map(|m| m.ws).unwrap_or(1);
    let waiting: Vec<Task> = ctx
        .db
        .task()
        .ws()
        .filter(&ws)
        .filter(|t| t.owner == member && t.status == "awaiting_takeover")
        .collect();
    for t in waiting {
        let rev = t.rev + 1;
        ctx.db.task().id().update(Task { status: "claimed".into(), rev, updated_ms: now, ..t });
    }
}

#[reducer]
pub fn heartbeat(ctx: &ReducerContext, fence: u32, status_text: String) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let was_stale = m.status == "stale";
    let (ws, id, name) = (m.ws, m.id, m.name.clone());
    let status_text = if status_text.is_empty() { m.status_text.clone() } else { status_text };
    ctx.db.member().id().update(Member {
        last_seen_ms: now_ms(ctx),
        status: "active".into(),
        stale_since_ms: 0,
        status_text,
        ..m
    });
    if was_stale {
        resume_tasks(ctx, id);
        emit(ctx, ws, "resumed", id, json!({"name": name}));
    }
    Ok(())
}

#[reducer]
pub fn set_context_seq(ctx: &ReducerContext, fence: u32, seq: u64) -> Result<(), String> {
    let m = me(ctx, fence)?;
    ctx.db.member().id().update(Member { context_seq: seq, ..m });
    Ok(())
}

pub fn leave_inner(ctx: &ReducerContext, m: &Member) {
    let now = now_ms(ctx);
    let released = locks_of(ctx, m.id);
    for obj in &released {
        ctx.db.cad_lock().key().delete(&lock_key(m.ws, obj));
    }
    let owned: Vec<Task> = ctx
        .db
        .task()
        .ws()
        .filter(&m.ws)
        .filter(|t| t.owner == m.id && (t.status == "claimed" || t.status == "awaiting_takeover"))
        .collect();
    for t in owned {
        let rev = t.rev + 1;
        ctx.db.task().id().update(Task { status: "open".into(), owner: 0, rev, updated_ms: now, ..t });
    }
    ctx.db.member().id().update(Member { status: "left".into(), ..m.clone() });
    set_pointer(ctx, m.ws, m.id, "", vec![], "idle", "", "");
    emit(ctx, m.ws, "left", m.id, json!({"name": m.name, "released": released}));
}

#[reducer]
pub fn leave(ctx: &ReducerContext, fence: u32) -> Result<(), String> {
    let m = me(ctx, fence)?;
    leave_inner(ctx, &m);
    Ok(())
}

#[reducer]
pub fn revoke_member(ctx: &ReducerContext, fence: u32, member_id: u64) -> Result<(), String> {
    let m = me(ctx, fence)?;
    if m.kind != "human" && m.kind != "host" {
        return Err("not_allowed: only a person or the host may remove a member".into());
    }
    let target = ctx
        .db
        .member()
        .id()
        .find(&member_id)
        .filter(|t| t.ws == m.ws)
        .ok_or_else(|| format!("unknown_participant: No member {member_id}"))?;
    emit(ctx, m.ws, "revoked", m.id, json!({"name": target.name, "by": m.name}));
    leave_inner(ctx, &target);
    Ok(())
}

pub fn take_over_inner(ctx: &ReducerContext, taker: &Member, target: &Member) -> (Vec<String>, Vec<String>) {
    let (locks, moved_ids) = transfer_work(ctx, taker, target);
    ctx.db.member().id().update(Member { status: "taken_over".into(), taken_over_by: taker.id, ..target.clone() });
    emit(
        ctx,
        target.ws,
        "taken_over",
        taker.id,
        json!({"target": target.name, "taker": taker.name, "locks": locks, "tasks": moved_ids}),
    );
    system_message(
        ctx,
        target.ws,
        "all",
        format!("{} took over {}'s work: locks {:?}, tasks {:?}.", taker.name, target.name, locks, moved_ids),
        "handoff",
        "",
    );
    (locks, moved_ids)
}

/// Move a member's locks and in-progress tasks to another member, leaving their status alone
/// (a takeover then retires the target; a deliberate handoff does not).
pub fn transfer_work(ctx: &ReducerContext, taker: &Member, target: &Member) -> (Vec<String>, Vec<String>) {
    let now = now_ms(ctx);
    let locks = locks_of(ctx, target.id);
    for obj in &locks {
        set_lock(ctx, target.ws, obj, taker.id);
    }
    let moved: Vec<Task> = ctx
        .db
        .task()
        .ws()
        .filter(&target.ws)
        .filter(|t| t.owner == target.id && (t.status == "claimed" || t.status == "awaiting_takeover"))
        .collect();
    let moved_ids: Vec<String> = moved.iter().map(|t| t.id.clone()).collect();
    for t in moved {
        let rev = t.rev + 1;
        ctx.db.task().id().update(Task { owner: taker.id, status: "claimed".into(), rev, updated_ms: now, ..t });
    }
    (locks, moved_ids)
}

#[reducer]
pub fn take_over(ctx: &ReducerContext, fence: u32, target: String, force: bool) -> Result<(), String> {
    let m = me(ctx, fence)?;
    let t = ctx
        .db
        .member()
        .ws()
        .filter(&m.ws)
        .filter(|x| x.name.eq_ignore_ascii_case(&target) && x.status != "left")
        .max_by_key(|x| x.id)
        .ok_or_else(|| format!("unknown_participant: No participant {target:?}"))?;
    rules::check_take_over(m.id, t.id, &t.name, &t.status, &m.kind, force)?;
    take_over_inner(ctx, &m, &t);
    Ok(())
}
```

- [ ] **Step 2: Build**

Run: `spacetime build --module-path module`
Expected: builds.

- [ ] **Step 3: Commit**

```powershell
git add module/src/membership.rs
git commit -m "feat(module): membership, invites, rejoin fences and takeover"
```

---

### Task 5: Locks and the stale sweep

**Files:**
- Modify: `module/src/locks.rs`, `module/src/sweep.rs`

**Interfaces:**
- Consumes: `membership::{leave_inner}`, `rules::{plan_sweep, Presence, ACTIVITY_KEEP}`.
- Produces: reducers `lock_objects`, `release_objects`, `sweep`.

- [ ] **Step 1: Write `module/src/locks.rs`**

```rust
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
```

- [ ] **Step 2: Replace `module/src/sweep.rs`**

```rust
use crate::membership::leave_inner;
use crate::tables::*;
use crate::util::*;
use cadswarm_rules as rules;
use serde_json::json;
use spacetimedb::{reducer, ReducerContext, Table};

/// Mark quiet members stale, free the work of long-gone ones, expire invites, prune activity
/// (core.py Hub.sweep). Runs every 5 s from `sweep_schedule`.
#[reducer]
pub fn sweep(ctx: &ReducerContext, _row: SweepSchedule) -> Result<(), String> {
    if ctx.sender() != ctx.identity() {
        return Err("not_allowed: sweep is scheduled, not called".into());
    }
    let now = now_ms(ctx);
    let members: Vec<Member> = ctx.db.member().iter().collect();
    let presence: Vec<rules::Presence> = members
        .iter()
        .map(|m| rules::Presence {
            id: m.id,
            status: m.status.clone(),
            last_seen_ms: m.last_seen_ms,
            stale_since_ms: m.stale_since_ms,
            online: m.online,
        })
        .collect();
    let plan = rules::plan_sweep(now, &presence);

    for id in plan.to_stale {
        let Some(m) = ctx.db.member().id().find(&id) else { continue };
        ctx.db.member().id().update(Member { status: "stale".into(), stale_since_ms: now, ..m.clone() });
        let claimed: Vec<Task> =
            ctx.db.task().ws().filter(&m.ws).filter(|t| t.owner == id && t.status == "claimed").collect();
        let titles: Vec<String> = claimed.iter().map(|t| t.title.clone()).collect();
        for t in claimed {
            let rev = t.rev + 1;
            ctx.db.task().id().update(Task { status: "awaiting_takeover".into(), rev, updated_ms: now, ..t });
        }
        let locks = locks_of(ctx, id);
        // A person who armed "if I leave, let an AI continue" gets their handoff opened now,
        // while their pointer still says where they were.
        crate::handoff::open_waiting_for(ctx, &m);
        if let Some(p) = ctx.db.pointer().member().find(&id) {
            set_pointer(ctx, m.ws, id, &p.object, p.point.clone(), "idle", &p.label, "");
        }
        emit(ctx, m.ws, "stale", id, json!({"name": m.name, "locks": locks, "tasks": titles}));
        if !locks.is_empty() || !titles.is_empty() {
            system_message(
                ctx,
                m.ws,
                "all",
                format!(
                    "{} went quiet holding {:?} and tasks {:?}. Its parts stay locked; call take_over('{}') to continue its work.",
                    m.name, locks, titles, m.name
                ),
                "handoff",
                "",
            );
        }
    }
    for id in plan.to_orphan {
        if let Some(m) = ctx.db.member().id().find(&id) {
            leave_inner(ctx, &m);
        }
    }

    let expired: Vec<Invite> = ctx.db.invite().iter().filter(|i| i.used_by == 0 && i.expires_ms < now).collect();
    for i in expired {
        ctx.db.invite().token_hash().delete(&i.token_hash);
    }

    for m in &members {
        let mut rows: Vec<Activity> = ctx.db.activity().member().filter(&m.id).collect();
        if rows.len() > rules::ACTIVITY_KEEP {
            rows.sort_by_key(|a| std::cmp::Reverse(a.id));
            for a in rows.into_iter().skip(rules::ACTIVITY_KEEP) {
                ctx.db.activity().id().delete(&a.id);
            }
        }
    }
    Ok(())
}
```

- [ ] **Step 3: Build**

Run: `spacetime build --module-path module`
Expected: builds. If `ctx.identity()` does not exist, look in the generated docs (`cargo doc --open --manifest-path module/Cargo.toml`) for the ReducerContext method returning the module's own identity and use it.

- [ ] **Step 4: Commit**

```powershell
git add module/src/locks.rs module/src/sweep.rs
git commit -m "feat(module): locks and the stale/orphan sweep"
```

---

### Task 6: The op queue

**Files:**
- Modify: `module/src/ops.rs`

**Interfaces:**
- Consumes: `rules::{check_op, parse_expected_revs, check_expected_revs, is_mutating, needed_locks, created_name, owners_to_notify}`.
- Produces: reducers `submit_op`, `start_op`, `complete_op`, `sync_objects`, `put_preview`, `put_artifact`.

- [ ] **Step 1: Write `module/src/ops.rs`**

```rust
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
    set_pointer(ctx, ws, by, "", vec![], "pointing", "", "");

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
    let row = Artifact { hash: hash.clone(), ws: m.ws, name, mime, size, bytes, brief, created_ms: now_ms(ctx) };
    if ctx.db.artifact().hash().find(&hash).is_some() {
        ctx.db.artifact().hash().update(row);
    } else {
        ctx.db.artifact().insert(row);
    }
    Ok(())
}
```

- [ ] **Step 2: Build**

Run: `spacetime build --module-path module`
Expected: builds.

- [ ] **Step 3: Commit**

```powershell
git add module/src/ops.rs
git commit -m "feat(module): op queue with lock and revision pre-checks and commit"
```

---

### Task 7: Tasks, messages, presence, briefs and reviews

**Files:**
- Modify: `module/src/tasks.rs`, `module/src/presence.rs`

**Interfaces:**
- Consumes: `rules::{check_claim, check_report, message_kind, address}`.
- Produces: reducers `create_task`, `claim_task`, `report_task`, `send_message`, `mark_delivered`, `ack_messages`, `log_activity`, `move_pointer`, `submit_brief`, `set_brief`, `set_brief_stl`, `report_review`, `set_bed`.

- [ ] **Step 1: Write `module/src/tasks.rs`**

```rust
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
    let kind = rules::message_kind(&m.kind, if kind.is_empty() { "message" } else { &kind });
    let addr = rules::address(&to, live_by_name(ctx, m.ws, &to).map(|x| x.id));
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
```

- [ ] **Step 2: Write `module/src/presence.rs`**

```rust
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
```

- [ ] **Step 3: Build**

Run: `spacetime build --module-path module`
Expected: builds with no warnings about unused reducers.

- [ ] **Step 4: Commit**

```powershell
git add module/src/tasks.rs module/src/presence.rs
git commit -m "feat(module): tasks, messages, activity, pointers, briefs and reviews"
```

---

### Task 7b: Handoffs (an AI picks up where a person left off)

**Files:**
- Modify: `module/src/handoff.rs`

**Interfaces:**
- Consumes: `membership::{take_over_inner, transfer_work}`, util helpers.
- Produces: reducers `request_handoff`, `cancel_handoff`, `accept_handoff`; `pub fn open_waiting_for(ctx, &Member)` (called by `sweep` when a person goes stale).

- [ ] **Step 1: Write `module/src/handoff.rs`**

```rust
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
```

- [ ] **Step 2: Build**

Run: `spacetime build --module-path module`
Expected: builds.

- [ ] **Step 3: Commit**

```powershell
git add module/src
git commit -m "feat(module): hand a person's work to an AI with a snapshot of where they left off"
```

---

### Task 8: Publish locally, smoke test end to end, generate bindings

**Files:**
- Create: `module/tests/smoke.py`, `scripts/publish-local.ps1`
- Create (generated): `host/src/module_bindings/`, `web/src/module_bindings/`

**Interfaces:**
- Produces: a running local database `cadswarm` and committed bindings (M0).

- [ ] **Step 1: Write the publish script `scripts/publish-local.ps1`**

```powershell
# Publish the module to the local SpacetimeDB (wiping data) and regenerate client bindings.
# Run `spacetime start` in another terminal first.
$ErrorActionPreference = "Stop"
spacetime publish --server local --module-path module --delete-data --yes cadswarm
spacetime generate --lang rust --out-dir host/src/module_bindings --module-path module
spacetime generate --lang typescript --out-dir web/src/module_bindings --module-path module
Write-Host "published cadswarm and regenerated bindings"
```
Before first use run `spacetime publish --help` and `spacetime server list`: if the clear-data flag is spelled differently (`-c`, `--clear-database`) or the local server is not named `local`, fix the script to match.

- [ ] **Step 2: Start the server and publish**

Terminal 1: `spacetime start`
Terminal 2: `powershell -File scripts/publish-local.ps1`
Expected: "published cadswarm and regenerated bindings"; `spacetime logs --server local cadswarm` shows no panics.

- [ ] **Step 3: Write the smoke test `module/tests/smoke.py`**

```python
"""End-to-end check of the published module over SpacetimeDB's HTTP API (stdlib only).

Run against a local server after `scripts/publish-local.ps1`:
    python module/tests/smoke.py
The CLI's own identity published the module, so it is the `host` member. Its token comes from
`spacetime login show --token`, or CADSWARM_OWNER_TOKEN if set.
"""
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import urllib.error
import urllib.request

URI = os.environ.get("CADSWARM_URI", "http://127.0.0.1:3000")
DB = os.environ.get("CADSWARM_DB", "cadswarm")


def http(method, path, token=None, body=None, raw=None):
    data = raw.encode() if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(URI + path, data=data, method=method)
    req.add_header("content-type", "application/json" if raw is None else "text/plain")
    if token:
        req.add_header("authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text.strip() else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class Client:
    def __init__(self, token):
        self.token = token
        self.fence = None

    def call(self, reducer, *args, expect_error=None):
        status, body = http("POST", f"/v1/database/{DB}/call/{reducer}", self.token, list(args))
        if expect_error:
            assert status >= 400 and expect_error in str(body), f"{reducer}: wanted {expect_error}, got {status} {body}"
            return body
        assert status < 400, f"{reducer}{args!r} failed: {status} {body}"

    def sql(self, query):
        status, body = http("POST", f"/v1/database/{DB}/sql", self.token, raw=query)
        assert status < 400, f"sql {query!r} failed: {status} {body}"
        out = []
        for table in body:
            names = [el["name"]["some"] if isinstance(el["name"], dict) else el["name"]
                     for el in table["schema"]["elements"]]
            out += [dict(zip(names, row)) for row in table["rows"]]
        return out

    def me(self):
        rows = self.sql("SELECT * FROM v_me")
        assert rows, "v_me is empty"
        self.fence = rows[0]["fence"]
        return rows[0]


def owner_token():
    if os.environ.get("CADSWARM_OWNER_TOKEN"):
        return os.environ["CADSWARM_OWNER_TOKEN"]
    out = subprocess.run(["spacetime", "login", "show", "--token"], capture_output=True, text=True).stdout
    m = re.search(r"([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)", out)
    assert m, f"could not find a token in `spacetime login show --token` output:\n{out}"
    return m.group(1)


def new_identity():
    status, body = http("POST", "/v1/identity")
    assert status < 400, body
    return Client(body["token"])


def invite(host, kind="agent", role="member"):
    token = secrets.token_hex(32)
    host.call("create_invite", host.fence, hashlib.sha256(token.encode()).hexdigest(), kind, role, 3600)
    return token


def join(host, name, kind="agent"):
    c = new_identity()
    c.call("redeem_invite", invite(host, kind), name, "smoke", "")
    c.me()
    return c


def main():
    host = Client(owner_token())
    me = host.me()
    assert me["kind"] == "host", me

    alice = join(host, "alice-claude")
    bob = join(host, "bob-codex")
    carol = join(host, "carol", kind="human")

    # A stranger sees nothing and can do nothing.
    eve = new_identity()
    assert eve.sql("SELECT * FROM v_member") == []
    eve.call("heartbeat", 1, "", expect_error="not_member")

    # Locks and the op queue.
    alice.call("lock_objects", alice.fence, ["Plate"])
    alice.call("submit_op", alice.fence, "o_smoke000001", "create_primitive",
               json.dumps({"kind": "box", "name": "Plate"}), "")
    bob.call("submit_op", bob.fence, "o_smoke000002", "set_props",
             json.dumps({"name": "Plate", "props": {"Length": 5}}), "", expect_error="not_locked")
    bob.call("lock_objects", bob.fence, ["Plate"], expect_error="locked")

    host.call("start_op", host.fence, "o_smoke000001")
    host.call("complete_op", host.fence, "o_smoke000001", True, json.dumps({"name": "Plate"}),
              ["Plate"], [], [], [], [], "", "")
    op = [o for o in alice.sql("SELECT * FROM v_op") if o["id"] == "o_smoke000001"][0]
    assert op["status"] == "done" and json.loads(op["result_json"])["revs"] == {"Plate": 1}, op
    plate = [o for o in alice.sql("SELECT * FROM v_object") if o["name"] == "Plate"][0]
    assert plate["rev"] == 1

    alice.call("submit_op", alice.fence, "o_smoke000003", "set_props",
               json.dumps({"name": "Plate", "props": {"Length": 2}}), json.dumps({"Plate": 2}),
               expect_error="stale_rev")

    # Tasks and messages.
    alice.call("create_task", alice.fence, "t_smoke001", "motor mount", "", [], "bob-codex", "")
    bob.call("claim_task", bob.fence, "t_smoke001", 1)
    alice.call("claim_task", alice.fence, "t_smoke001", 0, expect_error="not_claimable")
    alice.call("report_task", alice.fence, "t_smoke001", "done", "x", [], expect_error="not_owner")
    bob.call("report_task", bob.fence, "t_smoke001", "done", "mount built", ["Mount vol 300"])
    carol.call("send_message", carol.fence, "m_smoke0000001", "bob-codex", "use M4 bolts", "message", "")
    msgs = [m for m in bob.sql("SELECT * FROM v_message") if m["id"] == "m_smoke0000001"]
    assert msgs and msgs[0]["kind"] == "steer", msgs
    bob.call("ack_messages", bob.fence, ["m_smoke0000001"], expect_error="not_delivered")
    bob.call("mark_delivered", bob.fence, ["m_smoke0000001"])
    bob.call("ack_messages", bob.fence, ["m_smoke0000001"])

    # A newer process with the same identity supersedes the old fence.
    old = alice.fence
    alice.call("rejoin", "smoke")
    alice.me()
    assert alice.fence == old + 1
    alice.call("heartbeat", old, "", expect_error="fence_stale")
    alice.call("heartbeat", alice.fence, "still here")

    # Carol hands her work to an AI; the agent picks it up where she left off.
    carol.call("lock_objects", carol.fence, ["Lid"])
    carol.call("move_pointer", carol.fence, "Lid", [1.0, 2.0, 3.0], "inspecting", "looking at Lid")
    carol.call("request_handoff", carol.fence, "h_smoke001", "make the lip 1 mm thinner", "now")
    bob.call("request_handoff", bob.fence, "h_smoke002", "x", "now", expect_error="not_allowed")
    h = [x for x in carol.sql("SELECT * FROM v_handoff") if x["id"] == "h_smoke001"][0]
    snap = json.loads(h["context_json"])
    assert h["status"] == "open" and snap["note"] == "make the lip 1 mm thinner"
    assert snap["locks"] == ["Lid"] and snap["pointer"]["object"] == "Lid", snap
    bob.call("accept_handoff", bob.fence, "h_smoke001")
    lid = [l for l in carol.sql("SELECT * FROM v_lock") if l["object"] == "Lid"][0]
    assert lid["holder"] == bob.me()["id"], lid
    assert carol.me()["status"] == "active", "a deliberate handoff keeps the person's seat"
    bob.call("accept_handoff", bob.fence, "h_smoke001", expect_error="not_available")

    # A reviewer cannot change geometry.
    rev = join(host, "reviewer", kind="reviewer")
    rev.call("submit_op", rev.fence, "o_smoke000004", "delete", json.dumps({"name": "Plate"}), "",
             expect_error="not_allowed")

    print("smoke: all checks passed")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"smoke: FAILED: {e}")
        sys.exit(1)
```

- [ ] **Step 4: Run it**

Run: `python module/tests/smoke.py`
Expected: `smoke: all checks passed`.
If `spacetime login show --token` prints nothing usable, run `spacetime login show --help` to find how 2.x prints the token, and set `CADSWARM_OWNER_TOKEN` instead. If the SQL response's `name` field is a plain string rather than `{"some": ...}`, the helper already handles both. If a reducer fails with a 4xx/5xx whose body is not your `Err` text, print `body` and adjust `expect_error` matching. Fix module bugs until it passes; rerun `scripts/publish-local.ps1` after each fix (it wipes data, so the smoke test always starts clean).

- [ ] **Step 5: Commit, open the PR, announce M0**

```powershell
git add module/tests/smoke.py scripts/publish-local.ps1 host/src/module_bindings web/src/module_bindings
git commit -m "feat(module): local publish script, end-to-end smoke test, generated bindings"
git push -u origin feat/module
gh pr create --base main --title "SpacetimeDB module + bindings (M0)" --body "Smoke test passes locally. Bindings in host/src/module_bindings and web/src/module_bindings."
```
Tell the team in your chat: "M0: module live at http://<your LAN IP>:3000, db cadswarm; bindings on main after merge." Teammates on the same wifi can use your server until they run their own.

---

### Task 9: Maincloud deployment

**Files:**
- Create: `scripts/publish-maincloud.ps1`

**Interfaces:**
- Produces: the demo database `cadswarm-<team>` on Maincloud.

- [ ] **Step 1: Write `scripts/publish-maincloud.ps1`**

```powershell
# Publish the module to Maincloud. First time: `spacetime login` (browser sign-in).
# Pass -Clear to wipe the database (only before the demo dry run, never after).
param([string]$Name = "cadswarm-calhacks", [switch]$Clear)
$ErrorActionPreference = "Stop"
$args = @("publish", "--server", "maincloud", "--module-path", "module", "--yes", $Name)
if ($Clear) { $args = @("publish", "--server", "maincloud", "--module-path", "module", "--delete-data", "--yes", $Name) }
spacetime @args
Write-Host "published $Name to https://maincloud.spacetimedb.com"
```

- [ ] **Step 2: Publish and smoke test against Maincloud**

```powershell
spacetime login
powershell -File scripts/publish-maincloud.ps1 -Name cadswarm-calhacks
$env:CADSWARM_URI = "https://maincloud.spacetimedb.com"; $env:CADSWARM_DB = "cadswarm-calhacks"
python module/tests/smoke.py
```
Expected: `smoke: all checks passed`. The smoke test leaves test members in the database; republish with `-Clear` before the demo dry run.

- [ ] **Step 3: Commit**

```powershell
git add scripts/publish-maincloud.ps1
git commit -m "chore: Maincloud publish script"
```

After this task, A picks up the local-mode reach plan (gateway, iroh tunnel, quick tunnel), which gets its own plan once M1 is green.
