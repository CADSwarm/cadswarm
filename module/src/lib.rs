//! CadSwarm's shared world state: who is here, who holds which part, the op queue, tasks,
//! messages, live pointers and previews. Every rule lives in the `cadswarm_rules` crate;
//! reducers here are glue between those rules and the tables.

pub mod tables;
pub mod util;
pub mod views;

pub mod handoff;
pub mod library;
pub mod locks;
pub mod membership;
pub mod ops;
pub mod presence;
pub mod sweep;
pub mod tasks;
