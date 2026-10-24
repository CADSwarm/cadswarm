//! A brief's life: new → planning → building → (reviewing) → ready, driven by the host.

use crate::agents::Agent;
use crate::boards::{load_boards, seed_params, Board};
use crate::config::HostConfig;
use crate::space::Space;
use crate::worker::WorkerDriver;
use anyhow::Context;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, Instant};

/// A handoff whose agent failed to start waits this long before the next try (each try mints an invite).
const HANDOFF_RETRY: Duration = Duration::from_secs(30);
/// The reviewer's verdict reaches the host through the subscription, a moment after the reviewer exits.
const VERDICT_GRACE: Duration = Duration::from_secs(10);

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
    /// Stopped by a person. It never becomes done afterwards, so a brief with nothing done and
    /// every task stopped has nothing left to build.
    pub cancelled: usize,
    /// Reported blocked by a helper: it wants a person's decision. Nothing can claim it meanwhile,
    /// but its own helper may report it done after that decision, so it holds the brief open.
    pub blocked: usize,
    /// The planner has written a plan. A building brief always has one; if its task rows vanish
    /// the cache is not merely waiting for the first subscription update.
    pub planned: bool,
    pub exporting: bool,
    pub reviewer_enabled: bool,
    pub reviewer_alive: bool,
    /// The newest verdict from the subscription, with its round and whether it passed.
    pub review: Option<(u32, bool)>,
    pub reviewed_round: u32,
    /// The brief is `reviewing` but this host holds no reviewer run for it: it restarted mid-review,
    /// or a round from a previous run already left a verdict. Either way the review must be asked again.
    pub review_orphaned: bool,
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
        "building" if s.open + s.active == 0 => {
            if s.exporting {
                Action::Wait
            } else if s.done > 0 {
                // Finished parts are delivered even when another task is blocked: the export skips
                // parts no done task promised, and the blocked task keeps its question on the board.
                // Failing here would throw those parts away for a decision that is still open.
                if s.reviewer_enabled {
                    Action::Review
                } else {
                    Action::Export
                }
            } else if s.cancelled > 0 && s.blocked == 0 {
                Action::Fail("every task was stopped; a person needs to steer".into())
            } else if s.planned && s.blocked == 0 {
                // The plan is written but its rows are gone: the last task was removed.
                Action::Fail("every task was removed; a person needs to steer".into())
            } else {
                // Nothing can be built right now, but this is not a stuck brief: a blocked task is
                // waiting for a person's answer and its own helper may report it done afterwards
                // (only a cancelled task is refused), and the subscription may still be delivering
                // the first task rows. The stuck task stays visible on the board meanwhile.
                Action::Wait
            }
        }
        "building" if s.open > 0 && s.workers_alive == 0 => Action::Build(s.open.min(MAX_WORKERS)),
        "reviewing" if s.reviewer_alive || s.exporting => Action::Wait,
        // No reviewer of ours is running this round: a host restarted, or the only verdict is from a
        // previous run. Judge that verdict if it is final, otherwise run the round again. (Building
        // first would spawn a worker with nothing to claim.)
        "reviewing" if s.review_orphaned => match s.review {
            Some((_, true)) => Action::Export,
            Some((round, false)) if round >= MAX_REVIEW_ROUNDS => {
                Action::Fail(format!("review failed {round} times; a person needs to steer"))
            }
            // The reviewer files its fix tasks before reporting a failed verdict. After a restart
            // those tasks are still open: build them before spending another review round.
            Some((_, false)) if s.open > 0 => Action::Build(s.open.min(MAX_WORKERS)),
            // The same fix tasks, still claimed by the workers the restart killed. The sweep hands
            // them back as open once their member goes stale, so wait for that instead of reviewing
            // a model that has not changed yet.
            Some((_, false)) if s.active > 0 => Action::Wait,
            _ if s.reviewer_enabled => Action::Review,
            _ => Action::Export,
        },
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

/// The names an export should use, or why it must not run. Parts from stopped or blocked tasks may
/// be absent on purpose; a part a done task promised but the model does not have is a broken model.
pub fn export_names(tasks: &[crate::space::TaskRow], present: &[String]) -> Result<Vec<String>, String> {
    let mut missing: Vec<String> = tasks.iter().filter(|t| t.status == "done")
        .flat_map(|t| t.objects.clone())
        .filter(|n| !present.contains(n))
        .collect();
    missing.sort();
    missing.dedup();
    if !missing.is_empty() {
        Err(format!("the model is missing {}", missing.join(", ")))
    } else if present.is_empty() {
        Err("nothing to export: no part was built".into())
    } else {
        Ok(present.to_vec())
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
    reviewer_exited: Option<Instant>,
    spawned: usize,
}

pub struct Orchestrator {
    cfg: HostConfig,
    boards: BTreeMap<String, Board>,
    runs: HashMap<String, Runtime>,
    handoffs: HashMap<String, Agent>,
    handoff_failed: HashMap<String, Instant>,
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
    // The style memory learns from every finished brief, Elastic or not.
    crate::executor::observe(space, helper, "observe_brief", &args);
    if let Err(e) = helper.call("index_design", &args, None) {
        eprintln!("[host] index_design for brief {}: {e}", b.id);
    }
}

/// The team's learned design style for this request, as a prompt paragraph ("" when nothing is learned yet).
fn style_block(helper: Option<&mut WorkerDriver>, text: &str, board: &str) -> String {
    let Some(h) = helper else { return String::new() };
    match h.call("style_context", &json!({"query": text, "board": board}), None) {
        Ok(r) => r["result"]["text"].as_str().filter(|t| !t.is_empty()).map(|t| format!("

{t}")).unwrap_or_default(),
        Err(e) => {
            eprintln!("[host] style_context: {e}");
            String::new()
        }
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
        // The reviewer only runs once someone has created its member folder (`init-reviewer`).
        let reviewer_enabled =
            cfg.data_dir.join("agents").join("reviewer").join(".cadswarm").join("config.json").exists();
        Orchestrator { cfg, boards, runs: HashMap::new(), handoffs: HashMap::new(), handoff_failed: HashMap::new(), reviewer_enabled }
    }

    pub fn tick(&mut self, space: &Space, mut helper: Option<&mut WorkerDriver>) {
        // A person handed work to an agent: one agent per open handoff, until it accepts and finishes.
        // An agent that dies before accepting leaves the handoff open; dropping it here makes the
        // next tick spawn a fresh one.
        for h in space.open_handoffs() {
            if self.handoffs.contains_key(&h.id)
                || self.handoff_failed.get(&h.id).is_some_and(|t| t.elapsed() < HANDOFF_RETRY)
            {
                continue;
            }
            let name = format!("handoff-{}", h.id.trim_start_matches("h_"));
            let style = style_block(helper.as_deref_mut(), &h.context_json, "");
            let started = crate::mint_invite(space, &self.cfg, "agent", "member").and_then(|ticket| {
                let prompt = format!("{}{style}", crate::agents::handoff_prompt(&h.id, &h.context_json));
                Agent::spawn(&self.cfg, &name, &ticket, &prompt)
            });
            match started {
                Ok(agent) => {
                    eprintln!("[host] handoff {}: started {name}", h.id);
                    self.handoff_failed.remove(&h.id);
                    self.handoffs.insert(h.id.clone(), agent);
                }
                Err(e) => {
                    eprintln!("[host] handoff {}: {e:#} (trying again in {}s)", h.id, HANDOFF_RETRY.as_secs());
                    self.handoff_failed.insert(h.id.clone(), Instant::now());
                }
            }
        }
        self.handoffs.retain(|_, a| a.alive());

        for b in space.briefs() {
            if b.status == "ready" || b.status == "failed" || b.status == "cancelled" {
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
            cancelled: tasks.iter().filter(|t| t.status == "cancelled").count(),
            blocked: tasks.iter().filter(|t| t.status == "blocked").count(),
            planned: !space.brief_plan(&b.id).is_empty(),
            exporting: rt.export_op.is_some(),
            reviewer_enabled: self.reviewer_enabled,
            reviewer_alive: rt.reviewer.as_mut().map(|a| a.alive()).unwrap_or(false),
            review,
            reviewed_round: rt.reviewed_round,
            review_orphaned: rt.review_round == 0 && rt.reviewer.is_none(),
        };

        // A reviewer that exits without a verdict would leave the brief in `reviewing` forever.
        let no_verdict = review.map(|(r, _)| r < rt.review_round).unwrap_or(true);
        if b.status == "reviewing" && rt.review_round > 0 && !state.reviewer_alive && no_verdict
            && rt.reviewer_exited.get_or_insert_with(Instant::now).elapsed() >= VERDICT_GRACE
        {
            eprintln!("[host] brief {}: reviewer round {} exited without a verdict", b.id, rt.review_round);
            space.set_brief(&b.id, "failed", &json!({"error": "the reviewer exited without a verdict"}).to_string())?;
            return Ok(());
        }

        let action = next_action(&state);
        match action {
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
                let style = style_block(helper.as_deref_mut(), &b.text, &b.board);
                let prompt = format!(
                    "{}\n\nWhat earlier CadSwarm designs taught us (designs and lessons, JSON): {memory}\n\
                     Apply the lessons in your plan.{style}{}",
                    crate::agents::planner_prompt(&b.id, &b.text, &label, &params),
                    if style.is_empty() { "" } else { "\nWrite these style rules into each task's detail where they apply." }
                );
                let ticket = crate::mint_invite(space, &self.cfg, "agent", "lead")?;
                let name = format!("planner-{}", &b.id[2..]);
                eprintln!("[host] brief {}: starting {name}", b.id);
                rt.planner = Some(Agent::spawn(&self.cfg, &name, &ticket, &prompt)?);
            }
            Action::Build(n) => {
                keep_sources(&self.boards, space, b)?;
                let style = style_block(helper.as_deref_mut(), &b.text, &b.board);
                for _ in 0..n {
                    rt.spawned += 1;
                    let ticket = crate::mint_invite(space, &self.cfg, "agent", "member")?;
                    let name = format!("worker{}-{}", rt.spawned, &b.id[2..]);
                    eprintln!("[host] brief {}: starting {name}", b.id);
                    rt.workers.push(Agent::spawn(&self.cfg, &name, &ticket, &format!("{}{style}", crate::agents::worker_prompt(&b.id, rt.spawned)))?);
                }
                if let Some((round, false)) = state.review {
                    rt.reviewed_round = round;
                }
                space.set_brief(&b.id, "building", "")?;
            }
            Action::Review => {
                let round = review.map(|(r, _)| r.saturating_add(1)).unwrap_or(1);
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
                rt.reviewer_exited = None;
                space.set_brief(&b.id, "reviewing", "")?;
            }
            Action::Export => {
                keep_sources(&self.boards, space, b)?;
                // Task rows keep their object names after a task is stopped, and a reopened task
                // can name a part that was never built. The worker treats `names` as a whitelist
                // and fails the whole op on one missing part, so export only what exists.
                let wanted: Vec<String> = tasks.iter().flat_map(|t| t.objects.clone()).collect();
                let objects = space.objects_present(&wanted);
                match export_names(&tasks, &objects) {
                    Ok(objects) => {
                        let id = op_id();
                        space.submit_op(&id, "export", json!({"format": "stl", "names": objects, "brief": b.id}).to_string())?;
                        rt.export_op = Some(id);
                    }
                    Err(why) => {
                        space.set_brief(&b.id, "failed", &json!({"error": why}).to_string())?;
                    }
                }
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
    fn a_brief_whose_every_task_was_stopped_fails_instead_of_hanging() {
        assert!(matches!(next_action(&BriefState { cancelled: 2, ..st("building") }), Action::Fail(_)));
        // A blocked task is not a dead brief: its helper can still report it done after a person
        // answers, so wait for that instead of closing the brief and throwing the answer away.
        assert_eq!(next_action(&BriefState { blocked: 2, ..st("building") }), Action::Wait);
        assert_eq!(next_action(&BriefState { planned: true, blocked: 1, ..st("building") }), Action::Wait);
        // One stopped task and one stuck one: the stuck one can still finish, so wait for it.
        assert_eq!(next_action(&BriefState { cancelled: 1, blocked: 1, ..st("building") }), Action::Wait);
        // The last task was deleted after the plan was written: no rows are left to count.
        assert!(matches!(next_action(&BriefState { planned: true, ..st("building") }), Action::Fail(_)));
        // A brief whose task rows have not arrived from the subscription yet must not be failed.
        assert_eq!(next_action(&st("building")), Action::Wait);
        // A cancelled task does not hide finished work: the build still reviews and exports.
        assert_eq!(next_action(&BriefState { done: 1, cancelled: 1, ..st("building") }), Action::Export);
        // Nor does a blocked one: its part is skipped on purpose and its question stays on the board.
        assert_eq!(next_action(&BriefState { done: 1, blocked: 1, ..st("building") }), Action::Export);
    }

    #[test]
    fn reviews_pass_to_export_fail_back_to_building_and_give_up_after_three() {
        assert_eq!(next_action(&BriefState { review: Some((1, true)), ..st("reviewing") }), Action::Export);
        assert_eq!(next_action(&BriefState { review: Some((1, false)), open: 1, ..st("reviewing") }), Action::Build(1));
        assert!(matches!(next_action(&BriefState { review: Some((3, false)), ..st("reviewing") }), Action::Fail(_)));
        assert_eq!(next_action(&BriefState { reviewer_alive: true, ..st("reviewing") }), Action::Wait);
    }

    #[test]
    fn a_review_without_a_run_of_ours_is_asked_again_instead_of_building() {
        // The host restarted mid-review: no run of ours, no verdict yet.
        assert_eq!(next_action(&BriefState { review_orphaned: true, reviewer_enabled: true, ..st("reviewing") }),
                   Action::Review);
        // A failed round from a previous host with no fix tasks asks for the next round.
        assert_eq!(next_action(&BriefState { review: Some((1, false)), review_orphaned: true, reviewer_enabled: true,
            ..st("reviewing") }), Action::Review);
        // The reviewer files its fixes before its verdict: after a restart those tasks are built,
        // not skipped for another review of the unchanged model.
        assert_eq!(next_action(&BriefState { review: Some((1, false)), review_orphaned: true, reviewer_enabled: true,
            open: 2, ..st("reviewing") }), Action::Build(2));
        // ...and while the sweep has not yet reopened them (their dead workers still hold the
        // claims) waiting is right: a review now would spend a round on the unchanged model.
        assert_eq!(next_action(&BriefState { review: Some((1, false)), review_orphaned: true, reviewer_enabled: true,
            active: 2, ..st("reviewing") }), Action::Wait);
        // A passed round from a previous host still goes straight to export.
        assert_eq!(next_action(&BriefState { review: Some((2, true)), review_orphaned: true, reviewer_enabled: true,
            ..st("reviewing") }), Action::Export);
        // A restart does not hand back a review budget that was already spent.
        assert!(matches!(next_action(&BriefState { review: Some((3, false)), review_orphaned: true,
            reviewer_enabled: true, ..st("reviewing") }), Action::Fail(_)));
        // No reviewer set up: export rather than wait forever.
        assert_eq!(next_action(&BriefState { review_orphaned: true, ..st("reviewing") }), Action::Export);
        // A run this host started is not orphaned, so a failed round still goes back to building.
        assert_eq!(next_action(&BriefState { review: Some((1, false)), open: 1, ..st("reviewing") }), Action::Build(1));
    }

    fn task(id: &str, status: &str, objects: &[&str]) -> crate::space::TaskRow {
        crate::space::TaskRow {
            id: id.into(),
            status: status.into(),
            objects: objects.iter().map(|s| s.to_string()).collect(),
        }
    }

    #[test]
    fn export_skips_stopped_and_blocked_parts_but_not_a_finished_one() {
        let present = vec!["Base".to_string()];
        let tasks = vec![
            task("t1", "done", &["Base"]),
            task("t2", "cancelled", &["Lid"]),
            task("t3", "blocked", &["Clip"]),
        ];
        assert_eq!(export_names(&tasks, &present), Ok(vec!["Base".to_string()]));
        // A done task's part must exist, or the brief fails with the names.
        let broken = vec![task("t1", "done", &["Base", "Lid"])];
        assert_eq!(export_names(&broken, &present), Err("the model is missing Lid".into()));
        // Every task stopped: nothing to export.
        let stopped = vec![task("t2", "cancelled", &["Lid"])];
        assert_eq!(export_names(&stopped, &[]), Err("nothing to export: no part was built".into()));
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
