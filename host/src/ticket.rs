//! Pairing tickets: base32 JSON that a phone link or `cadswarm setup --invite` carries.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Ticket {
    pub v: u32,
    pub uri: String,
    pub db: String,
    pub token: String,
    pub kind: String,
    pub role: String,
    #[serde(default)]
    pub host_addr: String,
}

pub fn new_token() -> String {
    let mut b = [0u8; 32];
    getrandom::fill(&mut b).expect("system rng");
    hex::encode(b)
}

pub fn hash(token: &str) -> String {
    use sha2::{Digest, Sha256};
    hex::encode(Sha256::digest(token.trim().as_bytes()))
}

pub fn encode(t: &Ticket) -> String {
    data_encoding::BASE32_NOPAD.encode(&serde_json::to_vec(t).expect("ticket serializes"))
}

pub fn decode(s: &str) -> Result<Ticket> {
    let cleaned: String = s.trim().to_uppercase().chars().filter(|c| !c.is_whitespace()).collect();
    let bytes = data_encoding::BASE32_NOPAD.decode(cleaned.as_bytes()).context("ticket is not valid base32")?;
    serde_json::from_slice(&bytes).context("ticket payload is malformed")
}

pub fn phone_url(web_url: &str, ticket: &str) -> String {
    format!("{}/#t={ticket}", web_url.trim_end_matches('/'))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Ticket {
        Ticket {
            v: 1,
            uri: "http://127.0.0.1:3000".into(),
            db: "cadswarm".into(),
            token: "ab".repeat(32),
            kind: "human".into(),
            role: "member".into(),
            host_addr: String::new(),
        }
    }

    #[test]
    fn a_ticket_round_trips_and_is_url_safe() {
        let t = sample();
        let s = encode(&t);
        assert!(s.chars().all(|c| c.is_ascii_uppercase() || ('2'..='7').contains(&c)), "{s}");
        assert_eq!(decode(&s).unwrap(), t);
        assert_eq!(decode(&s.to_lowercase()).unwrap(), t, "decoding is case-insensitive");
    }

    #[test]
    fn tokens_are_64_hex_and_hash_matches_the_module() {
        let tok = new_token();
        assert_eq!(tok.len(), 64);
        assert!(tok.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
        assert_eq!(hash("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }

    #[test]
    fn the_phone_url_puts_the_ticket_in_the_fragment() {
        assert_eq!(phone_url("https://x.github.io/cadswarm/", "ABC"), "https://x.github.io/cadswarm/#t=ABC");
        assert_eq!(phone_url("http://localhost:5173", "ABC"), "http://localhost:5173/#t=ABC");
    }
}
