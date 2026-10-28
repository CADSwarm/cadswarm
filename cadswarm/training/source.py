"""Find a project's team database and read its tables.

Reuses cadswarm.space.SpaceClient: the same HTTP client the app uses, so there is no second SQL
dialect and no second error type. A missing project raises SourceError; a database that is down
surfaces the client's SpaceError, which the export layer records instead of aborting.
"""
import json
import os
import re

from ..paths import project_layout
from ..space import SpaceClient

# The primary key of each table: unique, so a local sort is total and stable.
PAGE_KEY = {"member": "id", "cad_object": "key", "param": "key", "op": "id", "task": "id",
            "brief": "id", "review": "id", "team_event": "seq"}


class SourceError(Exception):
    pass


def slug(name):
    """A folder-safe name: "Lexus IS350" -> "lexus-is350"."""
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-") or "project"


def project_name(project_dir):
    return os.path.basename(os.path.abspath(project_dir))


def read_host(project_dir):
    path = os.path.join(project_layout(project_dir)["root"], "host.json")
    try:
        with open(path, encoding="utf-8") as f:
            host = json.load(f)
    except (OSError, ValueError) as e:
        raise SourceError(f"no readable host.json ({e})") from e
    if not isinstance(host, dict) or not host.get("uri") or not host.get("db"):
        raise SourceError("host.json has no team uri/db")
    return host


def sort_key(value):
    """Numbers before strings, so an integer primary key sorts numerically rather than as text."""
    if isinstance(value, bool):
        return (0, int(value))
    if isinstance(value, (int, float)):
        return (0, value)
    return (1, str(value or ""))


class Source:
    def __init__(self, project, project_dir, uri, db, token, client=None):
        self.project = project
        self.project_dir = os.path.abspath(project_dir) if project_dir else ""
        self.uri = uri
        self.db = db
        self.token = token
        self.client = client if client is not None else SpaceClient(uri, db, token)

    @classmethod
    def for_project(cls, project_dir):
        host = read_host(project_dir)
        return cls(project_name(project_dir), project_dir, host["uri"], host["db"],
                   host.get("token", ""))

    def rows(self, table):
        """Every row of `table`, sorted by its primary key.

        SpacetimeDB's SQL rejects ORDER BY, so paging by a key is unsafe: a page is an arbitrary
        subset and `where key > last` would silently skip rows. One whole-table read it is.
        """
        if table not in PAGE_KEY:
            raise SourceError(f"unknown table {table!r}")
        key = PAGE_KEY[table]
        rows = self.client.sql(f"select * from {table}")
        if rows and key not in rows[0]:
            raise SourceError(f"table {table!r} has no {key!r} column")
        return sorted(rows, key=lambda row: sort_key(row[key]))
