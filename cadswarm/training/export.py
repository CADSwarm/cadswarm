"""One project -> raw/<project>/*.jsonl and source.json.

The export is a cache: the dataset build hashes it, so re-exporting unchanged tables must produce
the same bytes. A source that is paused, unreachable, missing or broken is recorded in source.json
and never aborts the caller's loop. The directory is swapped in whole, so a failed re-export can
never leave old tables beside a new status.
"""
import hashlib
import json
import os
import shutil
import sqlite3

from ..client import save_json
from ..paths import project_layout
from ..space import SpaceError
from . import redact
from .source import Source, SourceError, project_name, slug

TABLES = ("member", "cad_object", "param", "op", "task", "brief", "review", "team_event")
SQLITE_TABLES = ("style_events", "style_rules")
# Columns that are prose, and columns that are a JSON document. Ids and hashes are neither: they
# are what makes records joinable, so they are never rewritten.
TEXT_FIELDS = {"text", "detail", "title", "summary", "body", "note", "problem", "statement",
               "template", "status_text", "brief", "evidence"}
JSON_FIELDS = {"args_json", "result_json", "data_json", "plan_json", "findings_json", "data",
               "props_json", "expressions_json", "placement_json"}

__all__ = ["TABLES", "export_source", "export_project", "SpaceError"]


def _redact_json(raw):
    """Redact the string leaves of a JSON document and re-serialise it.

    A regex over the raw text can stop inside a JSON escape and leave invalid JSON; parsing
    first keeps the document readable by everything downstream.
    """
    if not isinstance(raw, str):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return redact.text(raw)
    return json.dumps(_redact_leaves(value), sort_keys=True, ensure_ascii=False)


def _redact_leaves(value):
    if isinstance(value, str):
        return redact.text(value)
    if isinstance(value, list):
        return [_redact_leaves(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_leaves(item) for key, item in value.items()}
    return value


def _redacted(row):
    out = dict(row)
    for key, value in out.items():
        if key in JSON_FIELDS:
            out[key] = _redact_json(value)
        elif key in TEXT_FIELDS:
            out[key] = redact.text(value)
    return out


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(_redacted(row), ensure_ascii=False, sort_keys=True) + "\n")


def _read_sqlite(path, table):
    """Rows of one style table, `[]` when it is absent. A broken database raises on purpose."""
    if not os.path.exists(path):
        return []
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(f"select * from {table} order by id")]
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return []
        raise
    finally:
        con.close()


def _schema_hash(tables):
    shape = {name: sorted(rows[0]) if rows else [] for name, rows in sorted(tables.items())}
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()


def _publish(root, tables, info):
    """Write a complete directory beside the old one and swap it in."""
    staging, old = root + ".new", root + ".old"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    for name, rows in tables.items():
        _write_jsonl(os.path.join(staging, name + ".jsonl"), rows)
    save_json(os.path.join(staging, "source.json"), info)
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(root):
        os.replace(root, old)
    try:
        os.replace(staging, root)
    except OSError:
        if os.path.exists(old) and not os.path.exists(root):
            os.replace(old, root)
        raise
    shutil.rmtree(old, ignore_errors=True)


def export_source(source, out_dir):
    """Write every table and return the source record. Never raises for a bad database."""
    tables, warnings, status = {}, [], "ok"
    try:
        for table in TABLES:
            tables[table] = source.rows(table)
        if source.project_dir:
            db = project_layout(source.project_dir)["db"]
            for table in SQLITE_TABLES:
                try:
                    tables[table] = _read_sqlite(db, table)
                except sqlite3.DatabaseError as e:
                    tables[table] = []
                    warnings.append(f"{table}: {e}")
    except Exception as e:  # one hostile database must not stop the other projects
        status = f"skipped: {type(e).__name__}: {e}"
        tables = {}
    info = {"project": source.project, "mode": "local", "db": source.db, "uri": source.uri,
            "status": status, "tables": {name: len(rows) for name, rows in tables.items()},
            "schema_sha256": _schema_hash(tables), "warnings": warnings}
    _publish(os.path.join(out_dir, "raw", slug(source.project)), tables, info)
    return info


def export_project(project_dir, out_dir):
    """Read `host.json`, export, and record a missing or broken project instead of raising."""
    name = project_name(project_dir)
    try:
        source = Source.for_project(project_dir)
    except Exception as e:
        info = {"project": name, "mode": "unknown", "db": "", "uri": "",
                "status": f"skipped: {type(e).__name__}: {e}", "tables": {},
                "schema_sha256": "", "warnings": []}
        _publish(os.path.join(out_dir, "raw", slug(name)), {}, info)
        return info
    return export_source(source, out_dir)
