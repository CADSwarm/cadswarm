"""Invite codes: one string a teammate copies to join (a SpacetimeDB pairing ticket + project name).

    cadswarm-2:eyJ2IjoxLCJ1cmkiOiJodHRwczovL21haW5jbG91ZC5zcGFjZXRpbWVkYi5jb20iLC...

The body is base64url of the ticket JSON {"v", "uri", "db", "token", "kind", "role", "host_addr"}
plus "p" (the project name). Every reader also accepts the bare base32 ticket (`cadswarm-host
invite` prints it) and phone links with `#t=<ticket>`. Invites work once.

Stdlib only. Lenient on input: quotes, line breaks, zero-width characters and chat text around the code are ignored.
"""
import base64
import json
import re

from . import ticket as _ticket

PREFIX = "cadswarm-2:"
OLD_PREFIXES = ("cadswarm-1:",)


class InviteError(ValueError):
    pass


def make_invite(ticket, project=""):
    """`ticket` is the decoded pairing ticket dict; returns a single-line `cadswarm-2:` code."""
    payload = json.dumps({**ticket, "p": project}, separators=(",", ":"))
    return PREFIX + base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


_INVISIBLE = re.compile("[​-‏⁠﻿­]")  # zero-width and soft-hyphen chars chat apps add
_QUOTES = re.compile("[\"'<>`‘’‚‛“”„«»]")
_B64_RUN = re.compile(r"[A-Za-z0-9_=-]*")
_B32_RUN = re.compile(r"[A-Za-z2-7]*")
_B32_MIN = 40  # a real ticket is well over 100 characters; this keeps ordinary words out


def _decode_v2(body):
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def _decode_b32(body):
    return _ticket.decode(body)


def _chunks(text, run_re):
    """Code-looking chunks from the start of `text`: the code may be line-wrapped (whitespace inside)
    or followed by chat text ("... thanks!"). A chunk ending in punctuation ends the code."""
    chunks = []
    for chunk in text.split():
        run = run_re.match(chunk).group()
        if run:
            chunks.append(run)
        if run != chunk or not run:
            break
    return chunks


def _longest(chunks, decode):
    """Use the longest run of chunks that decodes, so trailing words that merely look like code are dropped."""
    for n in range(len(chunks), 0, -1):
        try:
            data = decode("".join(chunks[:n]).rstrip("="))
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def _find_base32(s):
    """A bare base32 ticket somewhere in the text (or after a phone link's `#t=`)."""
    i = s.find("#t=")
    if i >= 0:
        return _longest(_chunks(s[i + 3:], _B32_RUN), _decode_b32), True
    words = s.split()
    for k, w in enumerate(words):
        if len(_B32_RUN.match(w).group()) >= _B32_MIN:
            return _longest(_chunks(" ".join(words[k:]), _B32_RUN), _decode_b32), True
    return None, False


def read_invite(text):
    """Returns the ticket dict plus "project"; raises InviteError with a plain-language message."""
    s = _QUOTES.sub("", _INVISIBLE.sub("", text or ""))
    if not s.strip():
        raise InviteError("Paste the invite code your teammate sent you.")
    i = s.find(PREFIX)
    if i >= 0:
        data, found = _longest(_chunks(s[i + len(PREFIX):], _B64_RUN), _decode_v2), True
    elif any(p in s for p in OLD_PREFIXES):
        raise InviteError("This invite is from an older CadSwarm. Ask your teammate for a new one.")
    else:
        data, found = _find_base32(s)
    if not found:
        raise InviteError(f"That doesn't look like a CadSwarm invite code. It should start with “{PREFIX}”. "
                          "Ask your teammate to copy it again with the Copy button.")
    if data is None:
        raise InviteError("That invite code is incomplete: part of it may have been cut off. "
                          "Ask your teammate to send it again.")
    uri, db, token = data.get("uri"), data.get("db"), data.get("token")
    if not isinstance(uri, str) or not uri.startswith(("http://", "https://")) or not db or not token:
        raise InviteError("That invite code is damaged. Ask your teammate to send it again.")
    out = {k: v for k, v in data.items() if k != "p"}
    out["uri"] = uri.rstrip("/")
    out["project"] = data.get("p", "") or ""
    return out
