//! Brief orchestration (Task 6). Placeholder so `run` compiles until then.

use crate::config::HostConfig;
use crate::space::Space;

pub struct Orchestrator {
    _cfg: HostConfig,
}

impl Orchestrator {
    pub fn new(cfg: HostConfig) -> Self {
        Orchestrator { _cfg: cfg }
    }
    pub fn tick(&mut self, _space: &Space) {}
}
