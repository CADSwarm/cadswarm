"""Pairing tickets and client-side ids (same formats as the Rust host and the web app).

A ticket is base32 (RFC 4648, uppercase, no padding) of UTF-8 JSON
{"v": 1, "uri", "db", "token", "kind", "role", "host_addr"}. The raw token is 32 random bytes as
lowercase hex; the module stores only its sha256 hex. Stdlib only.
"""
import base64
import hashlib
import json
import secrets

ID_LEN = {"o_": 12, "t_": 8, "m_": 12, "b_": 8, "r_": 8, "h_": 8}


def new_token():
    return secrets.token_hex(32)


def hash_token(token):
    return hashlib.sha256(token.strip().encode()).hexdigest()


def new_id(prefix, hex_len=None):
    n = hex_len or ID_LEN.get(prefix, 12)
    return prefix + secrets.token_hex((n + 1) // 2)[:n]


def encode(t):
    raw = json.dumps(t, separators=(",", ":")).encode()
    return base64.b32encode(raw).decode().rstrip("=")


def decode(s):
    cleaned = "".join(s.split()).upper()
    cleaned += "=" * (-len(cleaned) % 8)
    return json.loads(base64.b32decode(cleaned))
