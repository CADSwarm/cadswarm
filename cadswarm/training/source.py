"""Find a project's team database and read its tables, paged by primary key.

Reuses cadswarm.space.SpaceClient: the same HTTP client the app uses, so there is no second SQL
dialect and no second error type. A paused or unreachable database is reported, never raised.
"""
import json
import os
import re

from ..paths import project_layout
from ..space import SpaceClient

# The primary key of each table: unique, so `where key > last` never skips a duplicate.
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
            return json.load(f)
    except (OSError, ValueError) as e:
        raise SourceError(f"no readable host.json ({e})")


def quote(value):
    """A SQL literal. SpacetimeDB's dialect is SQLite-like; the only hazard is a quote in a key."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


class Source:
    def __init__(self, project, project_dir, uri, db, token, client=None, chunk=5000):
        self.project = project
        self.project_dir = os.path.abspath(project_dir) if project_dir else ""
        self.uri = uri
        self.db = db
        self.token = token
        self.chunk = chunk
        self.client = client or SpaceClient(uri, db, token)

    @classmethod
    def for_project(cls, project_dir, chunk=5000):
        host = read_host(project_dir)
        return cls(project_name(project_dir), project_dir, host["uri"], host["db"],
                   host.get("token", ""), chunk=chunk)

    def rows(self, table):
        """Every row of `table`, ordered by its primary key, one `limit`-sized page at a time."""
        key = PAGE_KEY[table]
        out, after = [], None
        while True:
            where = "" if after is None else f" where {key} > {quote(after)}"
            batch = self.client.sql(f"select * from {table}{where} order by {key} limit {self.chunk}")
            out += batch
            if len(batch) < self.chunk:
                return out
            after = batch[-1][key]
