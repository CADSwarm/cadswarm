//! The board library (boards/boards.json) and the Params a brief starts from.

use anyhow::Result;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::path::Path;

#[derive(Debug, Clone, Deserialize)]
pub struct Port {
    pub name: String,
    pub side: String,
    pub center: [f64; 2],
    pub size: [f64; 2],
}

#[derive(Debug, Clone, Deserialize)]
pub struct Board {
    pub label: String,
    pub pcb: [f64; 3],
    pub holes: Vec<[f64; 2]>,
    pub hole_d: f64,
    pub clear_top: f64,
    pub clear_bottom: f64,
    #[serde(default)]
    pub ports: Vec<Port>,
    /// A person checked these numbers against the datasheet.
    #[serde(default)]
    pub verified: bool,
    /// URLs backing `pcb`, `holes` and `ports`, when the board was looked up from a datasheet.
    #[serde(default)]
    pub sources: serde_json::Value,
}

impl Board {
    /// The datasheet links worth showing in the app, when a looked-up board has any.
    pub fn datasheet_sources(&self) -> Option<&serde_json::Value> {
        self.sources.as_object().filter(|o| !o.is_empty()).map(|_| &self.sources)
    }
}

pub fn load_boards(path: &Path) -> Result<BTreeMap<String, Board>> {
    Ok(serde_json::from_str(&std::fs::read_to_string(path)?)?)
}

fn mm(v: f64) -> String {
    format!("{} mm", (v * 1000.0).round() / 1000.0)
}

pub fn seed_params(b: &Board) -> Vec<(String, String)> {
    let mut p = vec![
        ("pcb_l".to_string(), mm(b.pcb[0])),
        ("pcb_w".to_string(), mm(b.pcb[1])),
        ("pcb_t".to_string(), mm(b.pcb[2])),
        ("hole_d".to_string(), mm(b.hole_d)),
        ("clear_top".to_string(), mm(b.clear_top)),
        ("clear_bottom".to_string(), mm(b.clear_bottom)),
    ];
    for (i, h) in b.holes.iter().enumerate() {
        p.push((format!("hole_{}_x", i + 1), mm(h[0])));
        p.push((format!("hole_{}_y", i + 1), mm(h[1])));
    }
    for port in &b.ports {
        p.push((format!("{}_pos", port.name), mm(port.center[0])));
        p.push((format!("{}_z", port.name), mm(port.center[1])));
        p.push((format!("{}_w", port.name), mm(port.size[0])));
        p.push((format!("{}_h", port.name), mm(port.size[1])));
    }
    p.push(("wall".into(), mm(2.0)));
    p.push(("gap".into(), mm(0.5)));
    p.push(("standoff_h".into(), mm(4.0)));
    p
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pi4() -> Board {
        Board {
            label: "Raspberry Pi 4".into(),
            pcb: [85.0, 56.0, 1.4],
            holes: vec![[3.5, 3.5], [61.5, 3.5]],
            hole_d: 2.7,
            clear_top: 17.0,
            clear_bottom: 3.0,
            ports: vec![Port { name: "usb_c".into(), side: "-y".into(), center: [11.2, 2.0], size: [9.5, 3.5] }],
            verified: true,
            sources: serde_json::json!({"pcb": "https://datasheets.raspberrypi.com/rpi4/product-brief.pdf"}),
        }
    }

    #[test]
    fn a_board_seeds_measured_params_plus_defaults() {
        let p: BTreeMap<String, String> = seed_params(&pi4()).into_iter().collect();
        assert_eq!(p["pcb_l"], "85 mm");
        assert_eq!(p["hole_2_x"], "61.5 mm");
        assert_eq!(p["usb_c_pos"], "11.2 mm");
        assert_eq!(p["usb_c_w"], "9.5 mm");
        assert_eq!(p["wall"], "2 mm");
        assert_eq!(p["gap"], "0.5 mm");
        assert_eq!(p["standoff_h"], "4 mm");
    }

    #[test]
    fn a_looked_up_spec_deserializes_into_a_board() {
        // The shape `Knowledge.board_spec` returns through the helper.
        let spec = serde_json::json!({
            "label": "Seeed XIAO ESP32C3", "pcb": [21.0, 17.5, 1.0],
            "holes": [], "hole_d": 0.0, "clear_top": 10.0, "clear_bottom": 3.0, "ports": [],
            "verified": false,
            "sources": {"pcb": "https://files.seeedstudio.com/wiki/XIAO/xiao.pdf",
                        "holes": "https://files.seeedstudio.com/wiki/XIAO/xiao.pdf",
                        "ports": "https://files.seeedstudio.com/wiki/XIAO/xiao.pdf"}
        });
        let b: Board = serde_json::from_value(spec).unwrap();
        assert_eq!(b.label, "Seeed XIAO ESP32C3");
        assert_eq!(b.pcb, [21.0, 17.5, 1.0]);
        assert!(!b.verified);
        assert_eq!(b.datasheet_sources().unwrap()["ports"], "https://files.seeedstudio.com/wiki/XIAO/xiao.pdf");
    }

    #[test]
    fn library_entries_without_verified_or_sources_still_load() {
        let b: Board = serde_json::from_str(
            r#"{"label":"Pi Pico","pcb":[51,21,1],"holes":[],"hole_d":2.1,"clear_top":4,"clear_bottom":2}"#,
        )
        .unwrap();
        assert!(!b.verified);
        assert!(b.datasheet_sources().is_none());
    }
}
