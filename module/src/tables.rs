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
