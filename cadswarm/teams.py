"""The little notebook of teams this computer has joined, so any of them can be reopened later.

Each team keeps its own sign-in under the app folder (`panel.identity_path`), so reopening a previous
project needs no new invite code. Stdlib only: shared by the FreeCAD guide, the panel and their tests.
"""
import os
import time

from .client import load_json, save_json
from .paths import app_dir

LIMIT = 12  # how many teams to remember; older ones simply fall off the end


def teams_path():
    return os.path.join(app_dir(), "gui", "teams.json")


def recent_teams():
    """Teams this computer has joined before, most recently used first (bad rows are ignored)."""
    data = load_json(teams_path(), {}) or {}
    return [t for t in data.get("teams", []) if isinstance(t, dict) and t.get("project") and t.get("db")]


def remember_team(project, uri, db, host_data_dir="", host_mode=""):
    """Note a team so it can be reopened later. Reopening the same `db` moves it to the front."""
    if not (project and db):
        return
    teams = [t for t in recent_teams() if t.get("db") != db]
    teams.insert(0, {"project": project, "uri": uri or "", "db": db, "host_data_dir": host_data_dir or "",
                     "host_mode": host_mode or "", "at": time.time()})
    save_json(teams_path(), {"v": 1, "teams": teams[:LIMIT]})
