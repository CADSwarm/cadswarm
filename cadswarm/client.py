"""Stdlib-only pieces shared by the MCP server, the prompt hook and the FreeCAD workbench
(which runs in FreeCAD's own Python, without our venv): the error every caller catches,
JSON files, and finding a set-up project folder.

A project folder that has been set up holds two files:
  .cadswarm/config.json    {uri, db, name, client, role}                  written by `cadswarm setup`
  .cadswarm/identity.json  {token, participant_id, fence, name, role}     written on join
Re-joining with the stored identity resumes the same participant at fence+1,
which makes any older process using that identity unable to act.
"""
import json
import os


class HubRequestError(Exception):
    """A plain-language failure from the team (`code` is e.g. "bad_invite" or "unreachable")."""

    def __init__(self, status, body):
        self.status, self.body = status, body if isinstance(body, dict) else {"error": str(body)}
        super().__init__(self.body.get("error", f"HTTP {status}"))

    @property
    def code(self):
        return self.body.get("code")


def state_dir(project_dir):
    return os.path.join(project_dir, ".cadswarm")


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
    os.replace(tmp, path)


def find_project_dir(start):
    """Walk up from `start` to the folder holding .cadswarm/config.json."""
    d = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(d, ".cadswarm", "config.json")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
