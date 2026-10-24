//! A brief's life: new → planning → building → (reviewing) → ready, driven by the host.

use crate::agents::Agent;
use crate::boards::{load_boards, seed_params, Board};
use crate::config::HostConfig;
use crate::space::Space;
use crate::worker::WorkerDriver;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;

pub const MAX_WORKERS: usize = 3;
pub const MAX_REVIEW_ROUNDS: u32 = 3;

#[derive(Debug, Clone, Default)]
pub struct BriefState {
    pub status: String,
    pub seeding: bool,
    pub planner_alive: bool,
    pub workers_alive: usize,
    pub open: usize,
    pub active: usize,
    pub done: usize,
    pub exporting: bool,
    pub reviewer_enabled: bool,
    pub reviewer_alive: bool,
    pub review: Option<(u32, bool)>,
    pub reviewed_round: u32,
}

#[derive(Debug, PartialEq)]
pub enum Action {
    Wait,
    Seed,
    Plan,
    Build(usize),
    Review,
    Export,
    Fail(String),
}

pub fn next_action(s: &BriefState) -> Action {
    match s.status.as_str() {
        "new" if !s.seeding => Action::Seed,
        "new" => Action::Wait,
        "planning" if s.seeding || s.planner_alive => Action::Wait,
        "planning" if s.open + s.active + s.done == 0 => Action::Plan,
        "planning" => Action::Build(s.open.min(MAX_WORKERS)),
        "building" if s.open + s.active == 0 && s.done > 0 => {
            if s.exporting {
                Action::Wait
            } else if s.reviewer_enabled {
                Action::Review
            } else {
                Action::Export
            }
        }
        "building" if s.open > 0 && s.workers_alive == 0 => Action::Build(s.open.min(MAX_WORKERS)),
        "reviewing" if s.reviewer_alive || s.exporting => Action::Wait,
        "reviewing" => match s.review {
            Some((_, true)) => Action::Export,
            Some((round, false)) if round >= MAX_REVIEW_ROUNDS => {
                Action::Fail(format!("review failed {round} times; a person needs to steer"))
            }
            Some((round, false)) if round > s.reviewed_round => Action::Build(s.open.min(MAX_WORKERS).max(1)),
            _ => Action::Wait,
        },
        _ => Action::Wait,
    }
}

/// Where the board library might be: $CADSWARM_BOARDS, the project folder, the working
/// directory, then the repo the binary was built in (host/target/<profile>/cadswarm-host).
fn board_library_candidates(cfg: &HostConfig) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = std::env::var_os("CADSWARM_BOARDS").map(PathBuf::from).into_iter().collect();
    out.push(cfg.data_dir.join("boards.json"));
    if let Ok(cwd) = std::env::current_dir() {
        out.push(cwd.join("boards").join("boards.json"));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(repo) = exe.ancestors().nth(4) {
            out.push(repo.join("boards").join("boards.json"));
        }
    }
    out
}

#[derive(Default)]
struct Runtime {
    seed_ops: Vec<String>,
    seeding: bool,
    planner: Option<Agent>,
    workers: Vec<Agent>,
    export_op: Option<String>,
    reviewer: Option<Agent>,
    reviewed_round: u32,
    spawned: usize,
}

pub struct Orchestrator {
    cfg: HostConfig,
    boards: BTreeMap<String, Board>,
    runs: HashMap<String, Runtime>,
    reviewer_enabled: bool,
}

fn op_id() -> String {
    format!("o_{}", &crate::ticket::new_token()[..12])
}

/// A plan JSON with the seeded board's datasheet links put back, when it has none of its own.
/// The planner overwrites `plan_json` when it saves; the app still needs `sources` (plan 05).
fn with_sources(plan_json: &str, sources: &Value) -> Option<String> {
    if !sources.as_object().is_some_and(|o| !o.is_empty()) {
        return None;
    }
    let mut plan: Value = serde_json::from_str(plan_json).ok()?;
    let obj = plan.as_object_mut()?;
    if obj.get("sources").and_then(Value::as_object).is_some_and(|o| !o.is_empty()) {
        return None;
    }
    obj.insert("sources".into(), sources.clone());
    Some(plan.to_string())
}

/// Ask the helper's Elastic expert for a board the local library does not vouch for (plan 05).
fn looked_up_board(
    boards: &mut BTreeMap<String, Board>,
    b: &crate::space::BriefRow,
    helper: Option<&mut WorkerDriver>,
) -> Option<Board> {
    if b.board.is_empty() {
        return None;
    }
    let reply = match helper?.call("board_spec", &json!({"board": b.board, "question": b.text}), None) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("[host] board_spec for {}: {e}", b.board);
            return None;
        }
    };
    let mut board = match serde_json::from_value::<Board>(reply["result"].clone()) {
        Ok(bd) => bd,
        Err(e) => {
            eprintln!("[host] board_spec for {} was unusable: {e}", b.board);
            return None;
        }
    };
    // The helper validated this spec (source URLs and plausible dimensions), so trust it for the
    // rest of the session instead of asking again for the next brief on this board.
    board.verified = true;
    eprintln!("[host] board_spec for {}: {} ({} x {} mm)", b.board, board.label, board.pcb[0], board.pcb[1]);
    boards.insert(b.board.clone(), board.clone());
    Some(board)
}

/// Put the board's datasheet links back into a plan the planner saved without them.
fn keep_sources(boards: &BTreeMap<String, Board>, space: &Space, b: &crate::space::BriefRow) -> anyhow::Result<()> {
    let Some(sources) = boards.get(&b.board).and_then(Board::datasheet_sources) else { return Ok(()) };
    if let Some(merged) = with_sources(&space.brief_plan(&b.id), sources) {
        space.set_brief(&b.id, "", &merged)?;
    }
    Ok(())
}

/// Index a finished brief so planners can recall it and the scheduled Workflow can learn lessons.
/// Best effort: a missing Elastic project must not fail the build.
fn index_design(space: &Space, helper: &mut WorkerDriver, b: &crate::space::BriefRow) {
    let plan = serde_json::from_str::<Value>(&space.brief_plan(&b.id)).unwrap_or_default();
    let args = json!({
        "brief": {"id": b.id, "text": b.text, "board": b.board, "status": "ready"},
        "plan": plan,
        "reviews": space.reviews_json(&b.id),
        "params": space.params_map(),
    });
    if let Err(e) = helper.call("index_design", &args, None) {
        eprintln!("[host] index_design for brief {}: {e}", b.id);
    }
}

impl Orchestrator {
    pub fn new(cfg: HostConfig) -> Self {
        let candidates = board_library_candidates(&cfg);
        let boards = candidates
            .iter()
            .find(|p| p.is_file())
            .and_then(|p| match load_boards(p) {
                Ok(b) => {
                    eprintln!("[host] {} boards from {}", b.len(), p.display());
                    Some(b)
                }
                Err(e) => {
                    eprintln!("[host] board library {} is unreadable: {e}", p.display());
                    None
                }
            })
            .unwrap_or_else(|| {
                eprintln!("[host] no board library found; briefs start without measured params");
                BTreeMap::new()
            });
        Orchestrator { cfg, boards, runs: HashMap::new(), reviewer_enabled: false }
    }

    pub fn tick(&mut self, space: &Space, mut helper: Option<&mut WorkerDriver>) {
        for b in space.briefs() {
            if b.status == "ready" || b.status == "failed" {
                continue;
            }
            if let Err(e) = self.step(space, &b, helper.as_deref_mut()) {
                eprintln!("[host] brief {}: {e:#}", b.id);
            }
        }
    }

    fn step(&mut self, space: &Space, b: &crate::space::BriefRow, mut helper: Option<&mut WorkerDriver>) -> anyhow::Result<()> {
        let tasks = space.tasks_for(&b.id);
        let review = space.latest_review(&b.id).map(|r| (r.round, r.passed));
        let rt = self.runs.entry(b.id.clone()).or_default();

        // Seed ops finished? Release Params so agents can add their own.
        if rt.seeding
            && rt.seed_ops.iter().all(|id| space.op(id).map(|o| o.status != "queued" && o.status != "running").unwrap_or(false))
        {
            rt.seeding = false;
            space.release_objects(vec!["Params".into()])?;
        }
        // Export finished?
        if let Some(id) = rt.export_op.clone() {
            if let Some(op) = space.op(&id) {
                match op.status.as_str() {
                    "done" => {
                        let hash = serde_json::from_str::<serde_json::Value>(&op.result_json)?["hash"]
                            .as_str()
                            .unwrap_or("")
                            .to_string();
                        space.set_brief_stl(&b.id, &hash)?;
                        if let Some(h) = helper.as_deref_mut() {
                            index_design(space, h, b);
                        }
                        rt.export_op = None;
                    }
                    "failed" | "rejected" => {
                        space.set_brief(&b.id, "failed", "")?;
                        rt.export_op = None;
                    }
                    _ => {}
                }
            }
        }

        let state = BriefState {
            status: b.status.clone(),
            seeding: rt.seeding,
            planner_alive: rt.planner.as_mut().map(|a| a.alive()).unwrap_or(false),
            workers_alive: rt.workers.iter_mut().map(|w| w.alive()).filter(|alive| *alive).count(),
            open: tasks.iter().filter(|t| t.status == "open").count(),
            active: tasks.iter().filter(|t| t.status == "claimed" || t.status == "awaiting_takeover").count(),
            done: tasks.iter().filter(|t| t.status == "done").count(),
            exporting: rt.export_op.is_some(),
            reviewer_enabled: self.reviewer_enabled,
            reviewer_alive: rt.reviewer.as_mut().map(|a| a.alive()).unwrap_or(false),
            review,
            reviewed_round: rt.reviewed_round,
        };

        match next_action(&state) {
            Action::Wait => {}
            Action::Seed => {
                let local = self.boards.get(&b.board).cloned();
                let board = match local {
                    Some(bd) if bd.verified => Some(bd),
                    other => looked_up_board(&mut self.boards, b, helper.as_deref_mut()).or(other),
                };
                let params = board.as_ref().map(seed_params).unwrap_or_default();
                if !params.is_empty() {
                    space.lock_objects(vec!["Params".into()])?;
                    for (alias, value) in &params {
                        let id = op_id();
                        space.submit_op(&id, "set_param", json!({"alias": alias, "value": value}).to_string())?;
                        rt.seed_ops.push(id);
                    }
                    rt.seeding = true;
                }
                // The app shows "from datasheet" links from the plan JSON.
                let plan = board
                    .as_ref()
                    .and_then(Board::datasheet_sources)
                    .map(|s| json!({"sources": s}).to_string())
                    .unwrap_or_default();
                space.set_brief(&b.id, "planning", &plan)?;
            }
            Action::Plan => {
                let params = self.boards.get(&b.board).map(seed_params).unwrap_or_default();
                let label = self.boards.get(&b.board).map(|x| x.label.clone()).unwrap_or_else(|| {
                    if b.board.is_empty() { "none given".into() } else { b.board.clone() }
                });
                let memory = match helper.as_deref_mut() {
                    Some(h) => match h.call("recall_designs", &json!({"query": b.text, "board": b.board}), None) {
                        Ok(r) => r["result"].to_string(),
                        Err(e) => {
                            eprintln!("[host] recall_designs for brief {}: {e}", b.id);
                            "{}".into()
                        }
                    },
                    None => "{}".into(),
                };
                let prompt = format!(
                    "{}\n\nWhat earlier CadSwarm designs taught us (designs and lessons, JSON): {memory}\n\
                     Apply the lessons in your plan.",
                    crate::agents::planner_prompt(&b.id, &b.text, &label, &params)
                );
                let ticket = crate::mint_invite(space, &self.cfg, "agent", "lead")?;
                let name = format!("planner-{}", &b.id[2..]);
                eprintln!("[host] brief {}: starting {name}", b.id);
                rt.planner = Some(Agent::spawn(&self.cfg, &name, &ticket, &prompt)?);
            }
            Action::Build(n) => {
                keep_sources(&self.boards, space, b)?;
                for _ in 0..n {
                    rt.spawned += 1;
                    let ticket = crate::mint_invite(space, &self.cfg, "agent", "member")?;
                    let name = format!("worker{}-{}", rt.spawned, &b.id[2..]);
                    eprintln!("[host] brief {}: starting {name}", b.id);
                    rt.workers.push(Agent::spawn(&self.cfg, &name, &ticket, &crate::agents::worker_prompt(&b.id, rt.spawned))?);
                }
                if let Some((round, false)) = state.review {
                    rt.reviewed_round = round;
                }
                space.set_brief(&b.id, "building", "")?;
            }
            Action::Review => {
                // P1: the Droid reviewer is started here (python -m cadswarm.reviewer --brief <id>).
                space.set_brief(&b.id, "reviewing", "")?;
            }
            Action::Export => {
                keep_sources(&self.boards, space, b)?;
                let objects: Vec<String> = tasks.iter().flat_map(|t| t.objects.clone()).collect();
                let id = op_id();
                space.submit_op(&id, "export", json!({"format": "stl", "names": objects, "brief": b.id}).to_string())?;
                rt.export_op = Some(id);
            }
            Action::Fail(why) => {
                eprintln!("[host] brief {} failed: {why}", b.id);
                space.set_brief(&b.id, "failed", &json!({"error": why}).to_string())?;
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn st(status: &str) -> BriefState {
        BriefState { status: status.into(), ..Default::default() }
    }

    #[test]
    fn a_new_brief_is_seeded_once() {
        assert_eq!(next_action(&st("new")), Action::Seed);
        assert_eq!(next_action(&BriefState { seeding: true, ..st("new") }), Action::Wait);
    }

    #[test]
    fn planning_starts_the_planner_then_builds_with_at_most_three_workers() {
        assert_eq!(next_action(&st("planning")), Action::Plan);
        assert_eq!(next_action(&BriefState { planner_alive: true, ..st("planning") }), Action::Wait);
        assert_eq!(next_action(&BriefState { open: 5, ..st("planning") }), Action::Build(3));
    }

    #[test]
    fn building_restarts_workers_if_they_all_died_and_exports_when_done() {
        assert_eq!(next_action(&BriefState { open: 2, ..st("building") }), Action::Build(2));
        assert_eq!(next_action(&BriefState { open: 2, workers_alive: 1, ..st("building") }), Action::Wait);
        assert_eq!(next_action(&BriefState { done: 3, ..st("building") }), Action::Export);
        assert_eq!(next_action(&BriefState { done: 3, reviewer_enabled: true, ..st("building") }), Action::Review);
        assert_eq!(next_action(&BriefState { done: 3, exporting: true, ..st("building") }), Action::Wait);
    }

    #[test]
    fn reviews_pass_to_export_fail_back_to_building_and_give_up_after_three() {
        assert_eq!(next_action(&BriefState { review: Some((1, true)), ..st("reviewing") }), Action::Export);
        assert_eq!(next_action(&BriefState { review: Some((1, false)), open: 1, ..st("reviewing") }), Action::Build(1));
        assert!(matches!(next_action(&BriefState { review: Some((3, false)), ..st("reviewing") }), Action::Fail(_)));
        assert_eq!(next_action(&BriefState { reviewer_alive: true, ..st("reviewing") }), Action::Wait);
    }

    #[test]
    fn a_planners_plan_keeps_the_seeded_datasheet_links() {
        let sources = json!({"pcb": "https://x/b.pdf", "holes": "https://x/b.pdf"});
        let plan = r#"{"parts":[{"name":"Base"}]}"#;
        let merged: serde_json::Value = serde_json::from_str(&with_sources(plan, &sources).unwrap()).unwrap();
        assert_eq!(merged["parts"][0]["name"], "Base");
        assert_eq!(merged["sources"]["holes"], "https://x/b.pdf");
        // Already sourced: no rewrite. No sources or no plan: nothing to merge.
        assert!(with_sources(&merged.to_string(), &sources).is_none());
        assert!(with_sources(plan, &json!({})).is_none());
        assert!(with_sources(plan, &json!(null)).is_none());
        assert!(with_sources("", &sources).is_none());
        assert!(with_sources("[1,2]", &sources).is_none());
    }
}
