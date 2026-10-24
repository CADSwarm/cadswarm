//! Turning a worker reply into the arguments of the `complete_op` reducer.

use crate::worker::WorkerError;
use serde_json::Value;

#[derive(Debug, Clone, PartialEq)]
pub struct Completion {
    pub ok: bool,
    pub result_json: String,
    pub created: Vec<String>,
    pub modified: Vec<String>,
    pub deleted: Vec<String>,
    pub recomputed: Vec<String>,
    pub broken: Vec<String>,
    pub error_code: String,
    pub error: String,
}

fn list(v: &Value, key: &str) -> Vec<String> {
    v[key].as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()).unwrap_or_default()
}

pub fn completion(reply: Result<Value, WorkerError>) -> Completion {
    match reply {
        Ok(v) => Completion {
            ok: true,
            result_json: v["result"].to_string(),
            created: list(&v, "created"),
            modified: list(&v, "modified"),
            deleted: list(&v, "deleted"),
            recomputed: list(&v, "recomputed"),
            broken: list(&v, "broken"),
            error_code: String::new(),
            error: String::new(),
        },
        Err(e) => Completion {
            ok: false,
            result_json: String::new(),
            created: vec![],
            modified: vec![],
            deleted: vec![],
            recomputed: vec![],
            broken: vec![],
            error_code: e.code,
            error: e.message,
        },
    }
}

pub fn touched(c: &Completion) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for n in c.created.iter().chain(c.modified.iter()).chain(c.recomputed.iter()) {
        if !out.contains(n) {
            out.push(n.clone());
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_successful_reply_maps_every_list_and_the_result() {
        let c = completion(Ok(json!({"ok": true, "result": {"name": "Box"}, "created": ["Box"],
            "modified": [], "deleted": [], "recomputed": ["Lid"], "broken": []})));
        assert!(c.ok);
        assert_eq!(c.created, vec!["Box"]);
        assert_eq!(c.recomputed, vec!["Lid"]);
        assert_eq!(serde_json::from_str::<Value>(&c.result_json).unwrap(), json!({"name": "Box"}));
        assert_eq!(touched(&c), vec!["Box".to_string(), "Lid".to_string()]);
    }

    #[test]
    fn an_error_maps_code_and_message() {
        let c = completion(Err(WorkerError { code: "lock_violation".into(), message: "nope".into() }));
        assert!(!c.ok);
        assert_eq!((c.error_code.as_str(), c.error.as_str()), ("lock_violation", "nope"));
    }
}
