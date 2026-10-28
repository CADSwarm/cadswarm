"""One project -> raw/<project>/*.jsonl and source.json.

The export is a cache: the dataset build hashes it, so re-running over unchanged tables produces
the same bytes and the same dataset id. A source that is paused, unreachable or missing is
recorded in source.json and never aborts the run.
"""
import hashlib
import json
import os
import sqlite3

from ..client import save_json
from ..paths import project_layout
from ..space import SpaceError
from . import redact
from .source import Source, SourceError, project_name, slug

TABLES = ("member", "cad_object", "param", "op", "task", "brief", "review", "team_event")
SQLITE_TABLES = ("style_events", "style_rules")
REDACTED_FIELDS = {"text", "detail", "title", "summary", "body", "note", "problem", "statement"}

__all__ = ["TABLES", "export_source", "export_project", "SpaceError"]


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(_redacted(row), ensure_ascii=False, sort_keys=True) + "\n")


def _redacted(row):
    """Free-text fields only: ids, hashes and numbers are what make records joinable."""
    out = dict(row)
    for key, value in out.items():
        if key in REDACTED_FIELDS:
            out[key] = redact.text(value)
    return out


def _read_sqlite(path, table):
    if not os.path.exists(path):
        return []
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(f"select * from {table}")]
    except sqlite3.DatabaseError:
        return []
    finally:
        con.close()


def _schema_hash(tables):
    shape = {name: sorted(rows[0]) if rows else [] for name, rows in sorted(tables.items())}
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()


def export_source(source, out_dir):
    """Write every table and return the source record. Never raises for a bad database."""
    root = os.path.join(out_dir, "raw", slug(source.project))
    os.makedirs(root, exist_ok=True)
    tables, status = {}, "ok"
    try:
        for table in TABLES:
            tables[table] = source.rows(table)
        if source.project_dir:
            db = project_layout(source.project_dir)["db"]
            for table in SQLITE_TABLES:
                tables[table] = _read_sqlite(db, table)
    except (SourceError, SpaceError) as e:
        status = f"skipped: {e}"
        tables = {}
    counts = {name: len(rows) for name, rows in tables.items()}
    for name, rows in tables.items():
        _write_jsonl(os.path.join(root, name + ".jsonl"), rows)
    info = {"project": source.project, "mode": "local", "db": source.db, "uri": source.uri,
            "status": status, "tables": counts, "schema_sha256": _schema_hash(tables)}
    save_json(os.path.join(root, "source.json"), info)
    return info


def export_project(project_dir, out_dir):
    """Read `host.json`, export, and record a missing project instead of raising."""
    name = project_name(project_dir)
    try:
        source = Source.for_project(project_dir)
    except SourceError as e:
        info = {"project": name, "mode": "unknown", "db": "", "uri": "", "status": f"skipped: {e}",
                "tables": {}, "schema_sha256": ""}
        root = os.path.join(out_dir, "raw", slug(name))
        os.makedirs(root, exist_ok=True)
        save_json(os.path.join(root, "source.json"), info)
        return info
    return export_source(source, out_dir)
