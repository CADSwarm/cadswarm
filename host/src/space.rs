//! The only file that touches the generated SpacetimeDB bindings.

use crate::commit::Completion;
use crate::config::HostConfig;
use crate::module_bindings::*;
use anyhow::{anyhow, Result};
use spacetimedb_sdk::{DbContext, Table};
use spacetimedb_sdk::error::InternalError;
use std::sync::mpsc;
use std::time::{Duration, Instant};

type Outcome = std::result::Result<std::result::Result<(), String>, InternalError>;
const REDUCER_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Debug, Clone)]
pub struct OpRow {
    pub id: String,
    pub by: u64,
    pub kind: String,
    pub args_json: String,
    pub allowed: Vec<String>,
    pub mutating: bool,
    pub status: String,
    pub result_json: String,
    pub error: String,
    pub created_ms: u64,
}

#[derive(Debug, Clone)]
pub struct BriefRow {
    pub id: String,
    pub text: String,
    pub board: String,
    pub status: String,
}

#[derive(Debug, Clone)]
pub struct TaskRow {
    pub id: String,
    pub status: String,
    pub objects: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct ReviewRow {
    pub round: u32,
    pub passed: bool,
}

#[derive(Debug, Clone)]
pub struct HandoffRow {
    pub id: String,
    pub context_json: String,
}

pub struct Space {
    conn: DbConnection,
}

const QUERIES: &[&str] = &[
    "SELECT * FROM v_me",
    "SELECT * FROM v_workspace",
    "SELECT * FROM v_op",
    "SELECT * FROM v_brief",
    "SELECT * FROM v_task",
    "SELECT * FROM v_review",
    "SELECT * FROM v_member",
    "SELECT * FROM v_handoff",
    "SELECT * FROM v_param",
];

impl Space {
    /// Connect as the running host: rejoin at a new fence, superseding any older host process.
    pub fn connect(cfg: &HostConfig) -> Result<Space> {
        Self::open(cfg, true)
    }

    /// Connect alongside a running host (e.g. `cadswarm-host invite`) and reuse its fence, so the
    /// running host is not superseded.
    pub fn attach(cfg: &HostConfig) -> Result<Space> {
        Self::open(cfg, false)
    }

    fn open(cfg: &HostConfig, rejoin: bool) -> Result<Space> {
        let conn = DbConnection::builder()
            .with_uri(cfg.uri.as_str())
            .with_database_name(cfg.db.as_str())
            .with_token(Some(cfg.token.clone()))
            .on_connect(|_, identity, _| eprintln!("[host] connected as {identity:?}"))
            .on_connect_error(|_, e| eprintln!("[host] connect error: {e:?}"))
            .on_disconnect(|_, e| eprintln!("[host] disconnected: {e:?}"))
            .build()
            .map_err(|e| anyhow!("connecting to {} / {}: {e:?}", cfg.uri, cfg.db))?;
        conn.run_threaded();
        conn.subscription_builder()
            .on_error(|_, e| eprintln!("[host] subscription error: {e:?}"))
            .subscribe(QUERIES);
        let space = Space { conn };
        // The subscription must be applied before rejoin, so `v_me` shows the new fence.
        space.wait_for(Duration::from_secs(20), |s| s.me().is_some())?;
        if !rejoin {
            return Ok(space);
        }
        let before = space.fence();
        space.rejoin("host")?;
        space.wait_for(Duration::from_secs(20), |s| s.fence() > before)?;
        Ok(space)
    }

    pub fn wait_for(&self, timeout: Duration, ok: impl Fn(&Space) -> bool) -> Result<()> {
        let t = Instant::now();
        while t.elapsed() < timeout {
            if ok(self) {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        Err(anyhow!("timed out waiting for SpacetimeDB"))
    }

    fn me(&self) -> Option<Member> {
        self.conn.db.v_me().iter().next()
    }

    pub fn fence(&self) -> u32 {
        self.me().map(|m| m.fence).unwrap_or(0)
    }

    pub fn bed(&self) -> [f64; 3] {
        self.conn
            .db
            .v_workspace()
            .iter()
            .next()
            .map(|w| [w.bed_x_mm, w.bed_y_mm, w.bed_z_mm])
            .unwrap_or([180.0, 180.0, 180.0])
    }

    fn op_row(o: Op) -> OpRow {
        OpRow {
            id: o.id,
            by: o.by,
            kind: o.kind,
            args_json: o.args_json,
            allowed: o.allowed,
            mutating: o.mutating,
            status: o.status,
            result_json: o.result_json,
            error: o.error,
            created_ms: o.created_ms,
        }
    }

    /// Oldest first: ops run in submission order.
    pub fn queued_ops(&self) -> Vec<OpRow> {
        let mut v: Vec<OpRow> =
            self.conn.db.v_op().iter().filter(|o| o.status == "queued").map(Self::op_row).collect();
        v.sort_by_key(|o| (o.created_ms, o.id.clone()));
        v
    }

    pub fn op(&self, id: &str) -> Option<OpRow> {
        self.conn.db.v_op().iter().find(|o| o.id == id).map(Self::op_row)
    }

    pub fn briefs(&self) -> Vec<BriefRow> {
        self.conn
            .db
            .v_brief()
            .iter()
            .map(|b| BriefRow { id: b.id, text: b.text, board: b.board, status: b.status })
            .collect()
    }

    pub fn tasks_for(&self, brief: &str) -> Vec<TaskRow> {
        self.conn
            .db
            .v_task()
            .iter()
            .filter(|t| t.brief == brief)
            .map(|t| TaskRow { id: t.id, status: t.status, objects: t.objects })
            .collect()
    }

    pub fn latest_review(&self, brief: &str) -> Option<ReviewRow> {
        self.conn
            .db
            .v_review()
            .iter()
            .filter(|r| r.brief == brief)
            .max_by_key(|r| r.round)
            .map(|r| ReviewRow { round: r.round, passed: r.passed })
    }

    /// Handoffs a person made and nobody picked up yet. An agent that dies before accepting
    /// leaves its handoff here, so the next tick spawns a fresh one.
    pub fn open_handoffs(&self) -> Vec<HandoffRow> {
        self.conn
            .db
            .v_handoff()
            .iter()
            .filter(|h| h.status == "open")
            .map(|h| HandoffRow { id: h.id, context_json: h.context_json })
            .collect()
    }

    /// Every reducer call waits for the module's answer (the SDK applies the transaction to the
    /// cache before the callback runs). Without this the loop could see an op it just ran still
    /// `queued` in the cache and run it twice, and reducer errors would vanish.
    fn call(
        &self,
        what: &str,
        send: impl FnOnce(Box<dyn FnOnce(&ReducerEventContext, Outcome) + Send>) -> spacetimedb_sdk::Result<()>,
    ) -> Result<()> {
        let (tx, rx) = mpsc::channel();
        send(Box::new(move |_, outcome| {
            let _ = tx.send(outcome);
        }))
        .map_err(|e| anyhow!("{what}: {e:?}"))?;
        match rx.recv_timeout(REDUCER_TIMEOUT) {
            Ok(Ok(Ok(()))) => Ok(()),
            Ok(Ok(Err(msg))) => Err(anyhow!("{what}: {msg}")),
            Ok(Err(internal)) => Err(anyhow!("{what}: internal error: {internal}")),
            Err(_) => Err(anyhow!("{what}: no answer from SpacetimeDB within {}s", REDUCER_TIMEOUT.as_secs())),
        }
    }

    pub fn rejoin(&self, client: &str) -> Result<()> {
        let client = client.to_string();
        self.call("rejoin", |cb| self.conn.reducers.rejoin_then(client, cb))
    }

    pub fn start_op(&self, id: &str) -> Result<()> {
        let (fence, id) = (self.fence(), id.to_string());
        self.call("start_op", |cb| self.conn.reducers.start_op_then(fence, id, cb))
    }

    pub fn complete_op(&self, id: &str, c: &Completion) -> Result<()> {
        let (fence, id, c) = (self.fence(), id.to_string(), c.clone());
        self.call("complete_op", |cb| {
            self.conn.reducers.complete_op_then(
                fence,
                id,
                c.ok,
                c.result_json,
                c.created,
                c.modified,
                c.deleted,
                c.recomputed,
                c.broken,
                c.error_code,
                c.error,
                cb,
            )
        })
    }

    pub fn sync_objects(&self, objects_json: String, params_json: String) -> Result<()> {
        let fence = self.fence();
        self.call("sync_objects", |cb| self.conn.reducers.sync_objects_then(fence, objects_json, params_json, cb))
    }

    pub fn put_preview(&self, object: &str, mesh: Vec<u8>) -> Result<()> {
        let (fence, object) = (self.fence(), object.to_string());
        self.call("put_preview", |cb| self.conn.reducers.put_preview_then(fence, object, mesh, cb))
    }

    pub fn put_artifact(&self, hash: &str, name: &str, mime: &str, bytes: Vec<u8>, brief: &str) -> Result<()> {
        let fence = self.fence();
        let (hash, name, mime, brief) = (hash.to_string(), name.to_string(), mime.to_string(), brief.to_string());
        self.call("put_artifact", |cb| self.conn.reducers.put_artifact_then(fence, hash, name, mime, bytes, brief, cb))
    }

    pub fn submit_op(&self, id: &str, kind: &str, args_json: String) -> Result<()> {
        let (fence, id, kind) = (self.fence(), id.to_string(), kind.to_string());
        self.call("submit_op", |cb| self.conn.reducers.submit_op_then(fence, id, kind, args_json, String::new(), cb))
    }

    pub fn lock_objects(&self, names: Vec<String>) -> Result<()> {
        let fence = self.fence();
        self.call("lock_objects", |cb| self.conn.reducers.lock_objects_then(fence, names, cb))
    }

    pub fn release_objects(&self, names: Vec<String>) -> Result<()> {
        let fence = self.fence();
        self.call("release_objects", |cb| self.conn.reducers.release_objects_then(fence, names, false, cb))
    }

    pub fn create_invite(&self, token_hash: String, kind: &str, role: &str, ttl_secs: u64) -> Result<()> {
        let (fence, kind, role) = (self.fence(), kind.to_string(), role.to_string());
        self.call("create_invite", |cb| self.conn.reducers.create_invite_then(fence, token_hash, kind, role, ttl_secs, cb))
    }

    pub fn set_brief(&self, id: &str, status: &str, plan_json: &str) -> Result<()> {
        let fence = self.fence();
        let (id, status, plan_json) = (id.to_string(), status.to_string(), plan_json.to_string());
        self.call("set_brief", |cb| self.conn.reducers.set_brief_then(fence, id, status, plan_json, cb))
    }

    pub fn set_brief_stl(&self, id: &str, hash: &str) -> Result<()> {
        let (fence, id, hash) = (self.fence(), id.to_string(), hash.to_string());
        self.call("set_brief_stl", |cb| self.conn.reducers.set_brief_stl_then(fence, id, hash, cb))
    }

    /// (name, kind) of a member of this workspace, from the `v_member` cache.
    pub fn member_name_kind(&self, id: u64) -> (String, String) {
        self.conn
            .db
            .v_member()
            .iter()
            .find(|m| m.id == id)
            .map(|m| (m.name, m.kind))
            .unwrap_or_else(|| ("?".into(), "agent".into()))
    }

    /// The planner's plan JSON (which may also carry the seeded board's datasheet sources).
    pub fn brief_plan(&self, id: &str) -> String {
        self.conn.db.v_brief().iter().find(|b| b.id == id).map(|b| b.plan_json).unwrap_or_default()
    }

    /// Every review round of a brief, oldest first, as JSON for the design index (plan 05).
    pub fn reviews_json(&self, brief: &str) -> serde_json::Value {
        let mut rows: Vec<_> = self.conn.db.v_review().iter().filter(|r| r.brief == brief).collect();
        rows.sort_by_key(|r| r.round);
        serde_json::Value::Array(
            rows.into_iter()
                .map(|r| serde_json::json!({"round": r.round, "passed": r.passed, "findings_json": r.findings_json}))
                .collect(),
        )
    }

    /// The shared Params as `{alias: number}`, for the design index.
    pub fn params_map(&self) -> serde_json::Value {
        serde_json::Value::Object(self.conn.db.v_param().iter().map(|p| (p.alias, serde_json::json!(p.num))).collect())
    }
}
