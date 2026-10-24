"""Invite codes: one string a host copies to teammates (address + team code + project name).

    cadswarm-1:eyJoIjoiaHR0cDovLzE5Mi4xNjguMS4yMDo4NzY1IiwidCI6Ii4uLiIsInAiOiJQaG9uZSBzdGFuZCJ9

Stdlib only. Lenient on input: quotes, line breaks, zero-width characters and chat text around the code are ignored.
"""
import base64
import json
import re

PREFIX = "cadswarm-1:"


class InviteError(ValueError):
    pass


def make_invite(hub_url, token, project=""):
    payload = json.dumps({"h": hub_url.rstrip("/"), "t": token, "p": project}, separators=(",", ":"))
    return PREFIX + base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


_INVISIBLE = re.compile("[​-‏⁠﻿­]")  # zero-width and soft-hyphen chars chat apps add
_QUOTES = re.compile("[\"'<>`‘’‚‛“”„«»]")
_B64_RUN = re.compile(r"[A-Za-z0-9_=-]*")


def _decode(body):
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    return data["h"], data["t"], data


def read_invite(text):
    """Returns {"hub", "token", "project"}; raises InviteError with a plain-language message."""
    s = _QUOTES.sub("", _INVISIBLE.sub("", text or ""))
    if not s.strip():
        raise InviteError("Paste the invite code your teammate sent you.")
    i = s.find(PREFIX)
    if i < 0:
        raise InviteError("That doesn't look like a CadSwarm invite code. It should start with “cadswarm-1:”. "
                          "Ask your teammate to copy it again with the Copy button.")
    # The code may be line-wrapped (whitespace inside) or followed by chat text ("... thanks!").
    # Collect code-looking chunks; a chunk ending in punctuation ends the code. Then use the longest
    # run of chunks that decodes, so trailing words that merely look like base64 are dropped.
    chunks = []
    for chunk in s[i + len(PREFIX):].split():
        run = _B64_RUN.match(chunk).group()
        if run:
            chunks.append(run)
        if run != chunk or not run:
            break
    result = None
    for n in range(len(chunks), 0, -1):
        try:
            result = _decode("".join(chunks[:n]).rstrip("="))
            break
        except Exception:
            continue
    if result is None:
        raise InviteError("That invite code is incomplete — part of it may have been cut off. "
                          "Ask your teammate to send it again.")
    hub, token, data = result
    if not str(hub).startswith(("http://", "https://")) or not token:
        raise InviteError("That invite code is damaged. Ask your teammate to send it again.")
    return {"hub": hub, "token": token, "project": data.get("p", "")}
