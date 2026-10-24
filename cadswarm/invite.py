"""Invite codes: one string a host copies to teammates (address + team code + project name).

    cadswarm-1:eyJoIjoiaHR0cDovLzE5Mi4xNjguMS4yMDo4NzY1IiwidCI6Ii4uLiIsInAiOiJQaG9uZSBzdGFuZCJ9

Stdlib only. Lenient on input: whitespace, quotes and line breaks from chat apps are ignored.
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


def read_invite(text):
    """Returns {"hub", "token", "project"}; raises InviteError with a plain-language message."""
    s = re.sub(r"[\s\"'<>`]", "", text or "")
    if not s:
        raise InviteError("Paste the invite code your teammate sent you.")
    i = s.find(PREFIX)
    if i < 0:
        raise InviteError("That doesn't look like a CadSwarm invite code. It should start with “cadswarm-1:”. "
                          "Ask your teammate to copy it again with the Copy button.")
    body = s[i + len(PREFIX):]
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        hub, token = data["h"], data["t"]
    except Exception:
        raise InviteError("That invite code is incomplete — part of it may have been cut off. "
                          "Ask your teammate to send it again.")
    if not str(hub).startswith(("http://", "https://")) or not token:
        raise InviteError("That invite code is damaged. Ask your teammate to send it again.")
    return {"hub": hub, "token": token, "project": data.get("p", "")}
