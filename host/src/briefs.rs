//! A brief's life: new → planning → building → (reviewing) → ready, driven by the host.

use crate::agents::Agent;
use crate::boards::{load_boards, seed_params, Board};
use crate::config::HostConfig;
use crate::space::Space;
use anyhow::Context;
use serde_json::json;
use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::process::Command;

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
    review_round: u32,
    reviewed_round: u32,
    spawned: usize,
}

pub struct Orchestrator {
    cfg: HostConfig,
    boards: BTreeMap<String, Board>,
    runs: HashMap<String, Runtime>,
    handoffs: HashMap<String, Agent>,
    reviewer_enabled: bool,
}

fn op_id() -> String {
    format!("o_{}", &crate::ticket::new_token()[..12])
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
        // The reviewer only runs once someone has created its member folder (`init-reviewer`).
        let reviewer_enabled =
            cfg.data_dir.join("agents").join("reviewer").join(".cadswarm").join("config.json").exists();
        Orchestrator { cfg, boards, runs: HashMap::new(), handoffs: HashMap::new(), reviewer_enabled }
    }

    pub fn tick(&mut self, space: &Space) {
        // A person handed work to an agent: one agent per open handoff, until it accepts and finishes.
        // An agent that dies before accepting leaves the handoff open; dropping it here makes the
        // next tick spawn a fresh one.
        for h in space.open_handoffs() {
            if self.handoffs.contains_key(&h.id) {
                continue;
            }
            let name = format!("handoff-{}", h.id.trim_start_matches("h_"));
            let started = crate::mint_invite(space, &self.cfg, "agent", "member").and_then(|ticket| {
                Agent::spawn(&self.cfg, &name, &ticket, &crate::agents::handoff_prompt(&h.id, &h.context_json))
            });
            match started {
                Ok(agent) => {
                    eprintln!("[host] handoff {}: started {name}", h.id);
                    self.handoffs.insert(h.id.clone(), agent);
                }
                Err(e) => eprintln!("[host] handoff {}: {e:#}", h.id),
            }
        }
        self.handoffs.retain(|_, a| a.alive());

        for b in space.briefs() {
            if b.status == "ready" || b.status == "failed" {
                continue;
            }
            if let Err(e) = self.step(space, &b) {
                eprintln!("[host] brief {}: {e:#}", b.id);
            }
        }
    }

    fn step(&mut self, space: &Space, b: &crate::space::BriefRow) -> anyhow::Result<()> {
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

        // A reviewer that exits without a verdict would leave the brief in `reviewing` forever.
        if b.status == "reviewing" && rt.review_round > 0 && !state.reviewer_alive && review.map(|(r, _)| r < rt.review_round).unwrap_or(true) {
            eprintln!("[host] brief {}: reviewer round {} exited without a verdict", b.id, rt.review_round);
            space.set_brief(&b.id, "failed", &json!({"error": "the reviewer exited without a verdict"}).to_string())?;
            return Ok(());
        }

        match next_action(&state) {
            Action::Wait => {}
            Action::Seed => {
                let params = self.boards.get(&b.board).map(seed_params).unwrap_or_default();
                if !params.is_empty() {
                    space.lock_objects(vec!["Params".into()])?;
                    for (alias, value) in &params {
                        let id = op_id();
                        space.submit_op(&id, "set_param", json!({"alias": alias, "value": value}).to_string())?;
                        rt.seed_ops.push(id);
                    }
                    rt.seeding = true;
                }
                space.set_brief(&b.id, "planning", "")?;
            }
            Action::Plan => {
                let params = self.boards.get(&b.board).map(seed_params).unwrap_or_default();
                let label = self.boards.get(&b.board).map(|x| x.label.clone()).unwrap_or_else(|| {
                    if b.board.is_empty() { "none given".into() } else { b.board.clone() }
                });
                let ticket = crate::mint_invite(space, &self.cfg, "agent", "lead")?;
                let name = format!("planner-{}", &b.id[2..]);
                eprintln!("[host] brief {}: starting {name}", b.id);
                rt.planner =
                    Some(Agent::spawn(&self.cfg, &name, &ticket, &crate::agents::planner_prompt(&b.id, &b.text, &label, &params))?);
            }
            Action::Build(n) => {
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
                let round = review.map(|(r, _)| r + 1).unwrap_or(1);
                let dir = self.cfg.data_dir.join("agents").join("reviewer");
                std::fs::create_dir_all(&dir)?;
                let log = std::fs::File::create(dir.join(format!("review-{}-{round}.log", b.id.trim_start_matches("b_"))))?;
                eprintln!("[host] brief {}: starting reviewer round {round}", b.id);
                let child = Command::new(&self.cfg.cadswarm_python)
                    .args(["-m", "cadswarm.reviewer", "--brief", &b.id, "--round", &round.to_string(), "--dir"])
                    .arg(&dir)
                    .stdout(log.try_clone()?)
                    .stderr(log)
                    .spawn()
                    .context("starting cadswarm.reviewer")?;
                rt.reviewer = Some(Agent::from_child("reviewer", child));
                rt.review_round = round;
                space.set_brief(&b.id, "reviewing", "")?;
            }
            Action::Export => {
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
}
