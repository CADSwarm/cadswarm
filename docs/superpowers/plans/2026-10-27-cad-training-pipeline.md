# CAD Training Pipeline (offline corpus + eval) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn this team's op logs into a versioned dataset with builder (SFT) and critic (pairwise) renderings, plus a replay evaluation harness, without changing the running app.

**Architecture:** A new stdlib-only `cadswarm/training/` package reads the team database through the existing `SpaceClient`, reconstructs the symbolic model state by replaying the op log, segments it into per-task episodes, labels them from reviews/corrections/outcomes, and renders SFT rows, critic pairs and eval cases into a content-addressed dataset directory. Evaluation boots the existing FreeCAD worker through `WorkerClient` on a scratch document and scores policies against a reference replay and a random baseline.

**Tech Stack:** Python 3.11 stdlib only for the offline units; `pytest` for tests; the existing `cadswarm.space.SpaceClient` (SpacetimeDB SQL) and `cadswarm.worker_client.WorkerClient` (FreeCAD JSON-lines worker); SQLite via stdlib for `style_events`.

## Global Constraints

- Nothing in `cadswarm/training/` imports `FreeCAD`, `torch`, `transformers` or `peft` at module import time.
- The package ships in the repo but never in the FreeCAD addon: `install/install.ps1` gains `cadswarm\training` to its `/XD` list, `install/install.sh` gains `--exclude=cadswarm/training`.
- Datasets never enter git: they live under `cadswarm-data/` (already in `.gitignore`).
- Every output is deterministic: sort before iterating, no wall clock in content, dataset id derived from content hashes.
- A paused/unreachable database is recorded as `skipped: <reason>` and never aborts a run.
- Whole-suite command is `python -m pytest`; new tests live in `tests/test_training_*.py`.
- Branch is `feat/cad-train`. Commit after every task.

## File Structure

| File | Responsibility |
|---|---|
| `cadswarm/training/__init__.py` | package docstring, version constant |
| `cadswarm/training/redact.py` | strip paths and e-mails before anything is written |
| `cadswarm/training/source.py` | `host.json` → `Source`; paged table reads; slug |
| `cadswarm/training/export.py` | one project → `raw/<project>/*.jsonl` + `source.json` |
| `cadswarm/training/state.py` | symbolic replay of ops; conformance against `cad_object` |
| `cadswarm/training/labels.py` | episode outcome, per-step labels, critic pairs |
| `cadswarm/training/episodes.py` | segmentation into per-task and freestyle episodes |
| `cadswarm/training/split.py` | project split with a temporal fallback |
| `cadswarm/training/prompts.py` | the canonical op contract and the state renderer |
| `cadswarm/training/render.py` | episodes → `sft.jsonl`, `pairs.jsonl`, `eval/cases.jsonl`, card |
| `cadswarm/training/dataset.py` | build orchestration, manifest, content id |
| `cadswarm/training/critic.py` | heuristic critic (the floor a learned critic must beat) |
| `cadswarm/training/metrics.py` | Jaccard, bbox IoU, volume ratio, aggregation |
| `cadswarm/training/eval_replay.py` | replay a policy against a real FreeCAD worker |
| `cadswarm/training/cli.py`, `__main__.py` | `python -m cadswarm.training export|build|eval` |

Tests: `tests/test_training_{redact,source,export,state,episodes,labels,render,metrics,replay,package}.py`.

---

### Task 1: Package skeleton, redaction, and whole-table reads

**Files:**
- Create: `cadswarm/training/__init__.py`, `cadswarm/training/redact.py`, `cadswarm/training/source.py`
- Test: `tests/test_training_redact.py`, `tests/test_training_source.py`

**Interfaces:**
- Produces: `redact.text(str) -> str`; `source.PAGE_KEY: dict[str,str]`; `source.slug(name) -> str`;
  `source.project_name(dir) -> str`; `source.read_host(dir) -> dict` (raises `SourceError`);
  `source.sort_key(value) -> tuple`; `source.Source(project, project_dir, uri, db, token,
  client=None)` with `.rows(table) -> list[dict]`;
  `source.Source.for_project(dir) -> Source`.

**Why there is no paging.** A live probe of the Lexus database shows SpacetimeDB rejects
`ORDER BY` (`Unsupported: SELECT * FROM op ORDER BY id`) while accepting `LIMIT`. Without a
server-side order, a page is an arbitrary subset, so `where key > last` silently skips rows. One
whole-table read, sorted locally by the primary key, is the only correct shape. 678 ops came back
in a single body today; the export layer is the place to solve size if it ever matters.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_training_redact.py
"""Nothing machine-specific or private reaches a dataset file."""
from cadswarm.training import redact


def test_windows_backslash_paths_are_replaced():
    assert redact.text(r"model at C:\Users\waves\Documents\CadSwarm\Lexus IS350") == "model at <path>"


def test_windows_forward_slash_paths_are_replaced():
    assert redact.text("open C:/Users/waves/x.FCStd") == "open <path>"


def test_posix_home_paths_are_replaced():
    assert redact.text("read /home/waves/My Documents/x.FCStd") == "read <path>"


def test_emails_are_replaced():
    assert redact.text("ask waves@example.com about it") == "ask <email> about it"


def test_hex_tokens_are_replaced():
    assert redact.text("invite " + "a" * 64) == "invite <token>"


def test_uppercase_hex_tokens_are_replaced():
    assert redact.text("token " + "A" * 64) == "token <token>"


def test_urls_are_not_mistaken_for_drive_paths():
    assert redact.text("see https://example.com/a/b now") == "see https://example.com/a/b now"


def test_plain_text_is_untouched():
    assert redact.text("wall thickness 2.4 mm") == "wall thickness 2.4 mm"
```

```python
# tests/test_training_source.py
"""Reading a team database through the same client the app uses, ordered locally."""
import json
import os
import re

import pytest

from cadswarm.training import source


class FakeClient:
    """Returns rows exactly as given, so the local sort is the unit under test."""

    def __init__(self, tables):
        self.tables = tables
        self.queries = []

    def sql(self, query):
        self.queries.append(query)
        return list(self.tables.get(re.search(r"from (\w+)", query).group(1), []))


def make(tables):
    return source.Source("P", "C:/p", "http://x", "db", "tok", client=FakeClient(tables))


def test_rows_come_back_sorted_by_primary_key():
    client = FakeClient({"op": [{"id": "op_b"}, {"id": "op_a"}]})
    got = source.Source("P", "C:/p", "http://x", "db", "tok", client=client).rows("op")
    assert [row["id"] for row in got] == ["op_a", "op_b"]


def test_each_table_is_one_whole_read_with_no_order_by():
    client = FakeClient({"task": [{"id": "t1"}]})
    source.Source("P", "C:/p", "http://x", "db", "tok", client=client).rows("task")
    assert client.queries == ["select * from task"]


def test_integer_keys_sort_numerically_not_lexically():
    client = FakeClient({"member": [{"id": 10}, {"id": 2}]})
    got = source.Source("P", "C:/p", "http://x", "db", "tok", client=client).rows("member")
    assert [row["id"] for row in got] == [2, 10]


def test_an_unknown_table_is_a_source_error():
    with pytest.raises(source.SourceError):
        make({}).rows("agent_turn")


def test_a_missing_host_file_is_a_source_error(tmp_path):
    with pytest.raises(source.SourceError):
        source.read_host(str(tmp_path))


def test_a_malformed_host_file_is_a_source_error(tmp_path):
    for payload in ('["not", "an", "object"]', '{"db": "x"}'):
        (tmp_path / "host.json").write_text(payload, encoding="utf-8")
        with pytest.raises(source.SourceError):
            source.Source.for_project(str(tmp_path))


def test_a_table_without_its_primary_key_fails_loudly():
    client = FakeClient({"op": [{"kind": "create_primitive"}]})
    with pytest.raises(source.SourceError):
        source.Source("P", "C:/p", "http://x", "db", "tok", client=client).rows("op")


def test_for_project_reads_uri_and_db_from_host_json(tmp_path):
    (tmp_path / "host.json").write_text(json.dumps({"uri": "http://127.0.0.1:3000", "db": "cadswarm-x",
                                                    "token": "secret"}), encoding="utf-8")
    got = source.Source.for_project(str(tmp_path))
    assert (got.uri, got.db, got.project) == ("http://127.0.0.1:3000", "cadswarm-x",
                                              os.path.basename(str(tmp_path)))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_training_redact.py tests/test_training_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cadswarm.training'`.

- [ ] **Step 3: Write the implementation**

```python
# cadswarm/training/__init__.py
"""Turning how this team builds into a dataset, and training on it.

Offline only: nothing here runs inside FreeCAD's Python and nothing in the app's runtime path
imports it. See docs/superpowers/specs/2026-10-27-cad-training-pipeline-design.md.
"""

SCHEMA_VERSION = 1
```

```python
# cadswarm/training/redact.py
"""Strip machine-specific and private strings before a record is written to a dataset.

Paths are matched to the end of the line: over-redaction is the safe direction, and a folder name
with spaces ("Lexus IS350") would otherwise be only half replaced.
"""
import re

PATTERNS = [
    # `\b` keeps `https://host/path` from being read as drive `s:/`.
    (re.compile(r"\b[A-Za-z]:[\\/][^\n\"']*"), "<path>"),
    (re.compile(r"\\\\[^\n\"']+"), "<path>"),
    (re.compile(r"/(?:home|Users)/[^\n\"']*"), "<path>"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<email>"),
    (re.compile(r"\b[0-9a-fA-F]{64}\b"), "<token>"),
]


def text(value):
    """The same string with paths, e-mails and 64-hex tokens replaced. Non-strings pass through."""
    if not isinstance(value, str):
        return value
    for pattern, replacement in PATTERNS:
        value = pattern.sub(replacement, value)
    return value
```

```python
# cadswarm/training/source.py
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_training_redact.py tests/test_training_source.py -v`
Expected: PASS (19 passed).

- [ ] **Step 5: Commit**

```bash
git add cadswarm/training tests/test_training_redact.py tests/test_training_source.py
git commit -m "feat(training): whole-table reads and redaction"
```

---

---

### Task 2: Export a project's tables to raw JSONL

**Files:**
- Create: `cadswarm/training/export.py`
- Test: `tests/test_training_export.py`

**Interfaces:**
- Consumes: `source.Source.rows`, `source.slug`, `source.project_name`, `source.SourceError`.
- Produces: `export.TABLES: tuple[str,...]`; `export.export_source(source, out_dir) -> dict`;
  `export.export_project(project_dir, out_dir) -> dict`. Both return the `source.json` content:
  `{project, mode, db, uri, status, tables: {name: count}, schema_sha256, warnings}`.

**Why the directory is swapped whole.** A dataset build hashes this cache, so the directory must
never be a mix of two runs. All rows are fetched into memory first, written into a sibling
`<dir>.new`, and only then swapped in; a failed re-export leaves the previous complete directory
(or nothing), never stale tables beside a fresh `source.json`. JSON fields are redacted by
parsing, walking strings and re-serialising, because a regex over raw JSON can stop inside an
escape and leave invalid JSON.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_training_export.py
"""One project's tables become JSONL files, and a hostile source is recorded rather than fatal."""
import json
import sqlite3

import pytest

from cadswarm.training import export


class FakeSource:
    def __init__(self, project="Test Project", project_dir=None):
        self.project = project
        self.project_dir = project_dir or ""
        self.uri = "http://127.0.0.1:3000"
        self.db = "cadswarm-test"
        self.token = "eyJhbGciOiJSUzI1NiJ9.secret"
        self.tables = {"member": [{"id": 1, "name": "alice", "kind": "human"}],
                       "op": [{"id": "op_1", "kind": "create_primitive", "created_ms": 1}]}

    def rows(self, table):
        return self.tables.get(table, [])


class PausedSource(FakeSource):
    def rows(self, table):
        raise export.SpaceError(503, "database is paused")


def raw(tmp_path, name="test-project"):
    return tmp_path / "raw" / name


def test_export_writes_one_file_per_table_and_a_source_record(tmp_path):
    info = export.export_source(FakeSource(), str(tmp_path))
    assert json.loads((raw(tmp_path) / "op.jsonl").read_text(encoding="utf-8").splitlines()[0])["id"] == "op_1"
    assert (raw(tmp_path) / "member.jsonl").exists()
    assert info["status"] == "ok" and info["tables"]["op"] == 1
    assert (raw(tmp_path) / "source.json").exists()


def test_the_token_never_reaches_disk(tmp_path):
    export.export_source(FakeSource(), str(tmp_path))
    written = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*") if p.is_file())
    assert "eyJhbGciOiJSUzI1NiJ9" not in written


def test_paths_and_emails_inside_json_fields_are_redacted(tmp_path):
    source = FakeSource()
    source.tables["op"] = [{"id": "op_1", "args_json": json.dumps(
        {"code": r"FreeCAD.open(r'C:\Users\waves\Lexus IS350\model.FCStd')",
         "note": "ask waves@example.com"})}]
    export.export_source(source, str(tmp_path))
    line = (raw(tmp_path) / "op.jsonl").read_text(encoding="utf-8")
    assert "<path>" in line and "<email>" in line
    assert json.loads(line)["args_json"]  # still valid JSON
    assert "waves@example.com" not in line


def test_a_paused_database_is_recorded_as_skipped(tmp_path):
    (raw(tmp_path) / "op.jsonl").parent.mkdir(parents=True)
    (raw(tmp_path) / "op.jsonl").write_text('{"id": "stale"}\n', encoding="utf-8")
    info = export.export_source(PausedSource(), str(tmp_path))
    assert info["status"].startswith("skipped:") and "paused" in info["status"]
    assert (raw(tmp_path) / "source.json").exists()
    assert not (raw(tmp_path) / "op.jsonl").exists()   # a stale table never sits beside a skip


def test_two_exports_of_the_same_rows_are_byte_identical(tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    export.export_source(FakeSource(), str(first))
    export.export_source(FakeSource(), str(second))
    for name in ("op.jsonl", "member.jsonl", "source.json"):
        assert (first / "raw" / "test-project" / name).read_bytes() == \
               (second / "raw" / "test-project" / name).read_bytes()


def test_style_events_are_read_from_the_project_sqlite(tmp_path):
    project = tmp_path / "proj"
    team = project / ".cadswarm-team"
    team.mkdir(parents=True)
    con = sqlite3.connect(team / "hub.sqlite")
    con.execute("create table style_events (id integer primary key, at real, kind text, data text)")
    con.execute("insert into style_events (at, kind, data) values (1.0, 'correction', '{}')")
    con.commit()
    con.close()
    info = export.export_source(FakeSource(project_dir=str(project)), str(tmp_path / "out"))
    assert info["tables"]["style_events"] == 1


def test_a_broken_style_database_is_a_warning_not_a_silent_empty(tmp_path):
    project = tmp_path / "proj"
    (project / ".cadswarm-team").mkdir(parents=True)
    (project / ".cadswarm-team" / "hub.sqlite").write_text("not a database", encoding="utf-8")
    info = export.export_source(FakeSource(project_dir=str(project)), str(tmp_path / "out"))
    assert info["status"] == "ok" and any("style" in w for w in info["warnings"])


def test_export_project_records_a_project_it_cannot_read(tmp_path):
    info = export.export_project(str(tmp_path / "nothing-here"), str(tmp_path / "out"))
    assert info["status"].startswith("skipped:") and info["tables"] == {}


def test_export_project_exports_through_for_project(tmp_path, monkeypatch):
    monkeypatch.setattr(export.Source, "for_project", classmethod(lambda cls, path: FakeSource()))
    info = export.export_project(str(tmp_path), str(tmp_path / "out"))
    assert info["status"] == "ok" and info["tables"]["op"] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_training_export.py -v`
Expected: FAIL — `AttributeError: module 'cadswarm.training.export' has no attribute 'export_source'`.

- [ ] **Step 3: Write the implementation**

```python
# cadswarm/training/export.py
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_training_export.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add cadswarm/training/export.py tests/test_training_export.py
git commit -m "feat(training): export a project's tables to raw JSONL"
```

---

### Task 3: Symbolic state replay and the conformance oracle

**Files:**
- Create: `cadswarm/training/state.py`
- Test: `tests/test_training_state.py`

**Interfaces:**
- Produces: `state.PARAMS`; `state.empty() -> dict`; `state.clone(state) -> dict`;
  `state.type_of(op, args) -> str|None`; `state.touched(step) -> list[str]`;
  `state.apply(state, step) -> list[str]` (returns unknown names);
  `state.replay(steps) -> (list[dict] before_states, dict final, list[int] degraded_indexes)`;
  `state.summary(state, max_objects=120) -> dict`;
  `state.conformance(state, cad_objects, params) -> dict`.

**Two facts measured on the live database, which shape this module.**

1. `cad_object` always contains the shared `Params` spreadsheet — the worker creates it when it
   opens a document, before any op is logged. A replay starting truly empty therefore flags every
   `set_param` as touching an unknown object (every one reports `modified: ["Params"]`, verified on
   two real rows), which would mark a whole op class `degraded` and silently drop it from SFT.
2. `insert_mesh` creates a `Mesh::Feature`, not a `Part::Feature`. An unknown op must not be
   stamped with a type the log never reported.

So `empty()` seeds `Params`, `type_of` returns `None` rather than guessing, and conformance skips
the type comparison where the replay has no type.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_training_state.py
"""The model state after every step, from the op log alone. Nothing is invented."""
from cadswarm.training import state


def step(op, args, created=(), modified=(), deleted=(), recomputed=(), status="done",
         volume=None, name=None):
    payload = {"volume": volume, "name": name} if volume is not None else {}
    return {"op": op, "args": args, "status": status,
            "result": {"result": payload, "created": list(created), "modified": list(modified),
                       "deleted": list(deleted), "recomputed": list(recomputed), "broken": []}}


def test_the_params_sheet_exists_before_the_first_op():
    assert state.empty()["objects"]["Params"]["type"] == "Spreadsheet::Sheet"


def test_set_param_is_not_degraded():
    s = state.empty()
    unknown = state.apply(s, step("set_param", {"alias": "wall", "value": "2.4 mm"},
                                  modified=["Params"]))
    assert unknown == [] and s["objects"]["Params"]["rev"] == 2
    assert s["params"]["wall"] == {"value": "2.4 mm", "num": 2.4}


def test_created_objects_are_typed_from_the_op():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "Base"}, created=["Base"]))
    assert s["objects"]["Base"]["type"] == "Part::Box"
    state.apply(s, step("create_extrusion", {"name": "Hood"}, created=["Hood"]))
    assert s["objects"]["Hood"]["type"] == "Part::Feature"


def test_a_capitalised_kind_is_still_recognised():
    s = state.empty()
    state.apply(s, step("boolean", {"kind": "Cut", "name": "C"}, created=["C"]))
    assert s["objects"]["C"]["type"] == "Part::Cut"


def test_insert_mesh_is_a_mesh_feature():
    s = state.empty()
    state.apply(s, step("insert_mesh", {"name": "Lattice"}, created=["Lattice"]))
    assert s["objects"]["Lattice"]["type"] == "Mesh::Feature"


def test_an_unknown_op_gets_no_invented_type():
    s = state.empty()
    state.apply(s, step("teleport_part", {"name": "X"}, created=["X"]))
    assert s["objects"]["X"]["type"] is None and s["objects"]["X"]["known"] is False


def test_a_boolean_removes_the_inputs_it_reports_deleted():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("create_primitive", {"kind": "box", "name": "B"}, created=["B"]))
    state.apply(s, step("boolean", {"kind": "cut", "name": "C"}, created=["C"], deleted=["A", "B"]))
    assert sorted(s["objects"]) == ["C", "Params"]
    assert s["objects"]["C"]["type"] == "Part::Cut"


def test_a_failed_op_changes_nothing():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("create_primitive", {"kind": "box", "name": "B"}, created=["B"], status="failed"))
    assert "B" not in s["objects"]


def test_a_reported_volume_goes_only_to_the_object_it_names():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("create_primitive", {"kind": "box", "name": "C"}, created=["C"]))
    state.apply(s, step("run_part_script", {"name": "A"}, modified=["A"], recomputed=["C"],
                        volume=12.5, name="A"))
    assert s["objects"]["A"]["volume"] == 12.5 and s["objects"]["A"]["known"] is True
    assert s["objects"]["C"]["volume"] is None   # nothing was reported for C


def test_a_new_volume_replaces_the_stale_one():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("run_part_script", {"name": "A"}, modified=["A"], volume=7.5, name="A"))
    state.apply(s, step("run_part_script", {"name": "A"}, modified=["A"], volume=12.5, name="A"))
    assert s["objects"]["A"]["volume"] == 12.5


def test_a_swept_result_marks_the_step_degraded():
    swept = step("create_primitive", {"kind": "box", "name": "A"}, created=["A"])
    swept["result"] = {"blob": "abc"}
    _, _, degraded = state.replay([swept])
    assert degraded == [0]


def test_a_swept_result_inside_the_step_shape_is_degraded_too():
    """The dataset build wraps the worker payload under "result": the blob sits one level down."""
    swept = step("run_part_script", {"name": "A"}, modified=["A"])
    swept["result"] = {"result": {"blob": "abc"}, "created": [], "modified": ["A"],
                       "deleted": [], "recomputed": [], "broken": []}
    _, _, degraded = state.replay([swept])
    assert degraded == [0]


def test_insert_mesh_volume_mm3_is_kept():
    mesh = step("insert_mesh", {"name": "M"}, created=["M"], volume=3.0, name="M")
    mesh["result"]["result"] = {"name": "M", "volume_mm3": 30.0}
    s = state.empty()
    state.apply(s, mesh)
    assert s["objects"]["M"]["volume"] == 30.0


def test_a_script_volume_is_compared_even_though_its_shape_is_unknown():
    s = state.empty()
    state.apply(s, step("run_part_script", {"name": "A"}, created=["A"], volume=100.0, name="A"))
    report = state.conformance(s, [{"name": "Params", "type_id": "Spreadsheet::Sheet", "volume": 0.0},
                                   {"name": "A", "type_id": "Part::Feature", "volume": 120.0}], [])
    assert report["volume_mismatch"] == ["A"]


def test_replay_returns_the_state_before_each_step_and_flags_unknown_objects():
    steps = [step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]),
             step("set_props", {"name": "Ghost"}, modified=["Ghost"])]
    before, final, degraded = state.replay(steps)
    assert before[1]["objects"]["A"]["type"] == "Part::Box"
    assert "Ghost" not in final["objects"]
    assert degraded == [1]


def test_summary_is_bounded_and_sorted():
    s = state.empty()
    for i in range(130):
        state.apply(s, step("create_primitive", {"kind": "box", "name": f"o{i:03d}"},
                            created=[f"o{i:03d}"]))
    got = state.summary(s, max_objects=120)
    assert len(got["objects"]) == 120 and got["objects_total"] == 131
    assert [o["name"] for o in got["objects"]] == sorted(o["name"] for o in got["objects"])


def test_conformance_reports_missing_extra_and_type_mismatch():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("create_primitive", {"kind": "box", "name": "B"}, created=["B"]))
    report = state.conformance(s, [{"name": "Params", "type_id": "Spreadsheet::Sheet", "volume": 0.0},
                                   {"name": "A", "type_id": "Part::Box", "volume": 0.0},
                                   {"name": "C", "type_id": "Part::Cylinder", "volume": 0.0}], [])
    assert report["missing"] == ["C"] and report["extra"] == ["B"]
    assert report["fraction"] == 0.5


def test_conformance_does_not_convict_a_type_the_replay_lacks():
    s = state.empty()
    state.apply(s, step("teleport_part", {"name": "X"}, created=["X"]))
    report = state.conformance(s, [{"name": "Params", "type_id": "Spreadsheet::Sheet", "volume": 0.0},
                                   {"name": "X", "type_id": "Part::Box", "volume": 0.0}], [])
    assert report["type_mismatch"] == [] and report["unknown_type"] == ["X"]


def test_conformance_flags_a_volume_outside_one_percent():
    s = state.empty()
    state.apply(s, step("create_primitive", {"kind": "box", "name": "A"}, created=["A"]))
    state.apply(s, step("run_part_script", {"name": "A"}, modified=["A"], volume=100.0, name="A"))
    report = state.conformance(s, [{"name": "Params", "type_id": "Spreadsheet::Sheet", "volume": 0.0},
                                   {"name": "A", "type_id": "Part::Feature", "volume": 120.0}], [])
    assert report["volume_mismatch"] == ["A"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_training_state.py -v`
Expected: FAIL — `AttributeError: module 'cadswarm.training.state' has no attribute 'PARAMS'`.

- [ ] **Step 3: Write the implementation**

```python
# cadswarm/training/state.py
"""The object table after every step, reconstructed from the op log alone.

A value the worker never reported stays `known: False` and `None`: the replay is useful precisely
because it does not guess. A failed op is a no-op, because the worker rolls it back. The shared
`Params` sheet is seeded because the worker creates it before the first op is logged.
"""
import re

PARAMS = "Params"
PARAMS_TYPE = "Spreadsheet::Sheet"
MESH_TYPE = "Mesh::Feature"
PRIMITIVE_TYPE = {"box": "Part::Box", "cylinder": "Part::Cylinder", "sphere": "Part::Sphere",
                  "cone": "Part::Cone", "torus": "Part::Torus"}
BOOLEAN_TYPE = {"cut": "Part::Cut", "fuse": "Part::Fuse", "common": "Part::Common"}
FEATURE_OPS = {"create_extrusion", "run_part_script", "insert_component", "insert_standard_part"}
FIELDS = ("created", "modified", "deleted", "recomputed", "broken")


def empty():
    return {"objects": {PARAMS: {"type": PARAMS_TYPE, "rev": 1, "known": True, "volume": None}},
            "params": {}}


def clone(state):
    return {"objects": {k: dict(v) for k, v in state["objects"].items()},
            "params": {k: dict(v) for k, v in state["params"].items()}}


def number(value):
    """The number in "2.4 mm" or 2.4, or None (the same idea as style_memory.number)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def type_of(op, args):
    """The type this op creates, or None when the log does not say. Never a guess."""
    kind = str(args.get("kind") or "").lower()
    if op == "create_primitive":
        return PRIMITIVE_TYPE.get(kind)
    if op == "boolean":
        return BOOLEAN_TYPE.get(kind)
    if op == "insert_mesh":
        return MESH_TYPE
    if op in FEATURE_OPS:
        return "Part::Feature"
    return None


def touched(step):
    result = step.get("result") or {}
    return [name for field in FIELDS for name in (result.get(field) or []) if isinstance(name, str)]


def apply(state, step):
    """Apply one op in place. Returns the names this op touched but the replay never created."""
    if step.get("status") != "done":
        return []
    result = step.get("result") or {}
    args = step.get("args") or {}
    created = result.get("created") or []
    payload = result.get("result") or {}
    named = payload.get("name")
    volume = None
    if len(created) == 1 and named in (None, created[0]):
        volume = payload.get("volume", payload.get("volume_mm3"))
    unknown = []

    for name in result.get("deleted") or []:
        if state["objects"].pop(name, None) is None:
            unknown.append(name)

    for name in created:
        created_type = PARAMS_TYPE if name == PARAMS else type_of(step.get("op"), args)
        state["objects"][name] = {
            "type": created_type, "rev": 0,
            # A script or a component's shape is only described by a snapshot the worker never took.
            "known": created_type is not None and step.get("op") != "run_part_script",
            "volume": volume if name != PARAMS else None,
        }

    changed = list(dict.fromkeys((result.get("modified") or []) + (result.get("recomputed") or [])))
    reported = payload.get("name")
    for name in changed:
        if name not in state["objects"]:
            unknown.append(name)
            continue
        obj = state["objects"][name]
        obj["rev"] = obj.get("rev", 0) + 1
        # The payload describes one object. A recomputed dependent gets a new rev, not this volume.
        if name == reported and isinstance(payload.get("volume"), (int, float)):
            obj["volume"] = payload["volume"]
            obj["known"] = True

    if step.get("op") == "set_param" and args.get("alias"):
        state["params"][args["alias"]] = {"value": args.get("value"), "num": number(args.get("value"))}
    return unknown


def _swept(step):
    """A done step whose result payload was spilled: the change happened, its detail is gone."""
    result = step.get("result") or {}
    inner = result.get("result") if isinstance(result.get("result"), dict) else {}
    return step.get("status") == "done" and ("blob" in result or "blob" in inner)


def replay(steps):
    state = empty()
    before, degraded = [], []
    for index, step in enumerate(steps):
        before.append(clone(state))
        if apply(state, step) or _swept(step):
            degraded.append(index)
    return before, state, degraded


def summary(state, max_objects=120):
    """The compact, deterministic view a policy sees: sorted, bounded, no unknown values faked."""
    objects = state["objects"]
    names = sorted(objects)[:max_objects]
    return {"objects": [{"name": n, "type": objects[n]["type"], "rev": objects[n]["rev"],
                         "known": objects[n]["known"], "volume": objects[n].get("volume")}
                        for n in names],
            "objects_total": len(objects),
            "params": {k: state["params"][k]["value"] for k in sorted(state["params"])}}


def conformance(state, cad_objects, params):
    """How far the replay's final table is from the live object table. The pipeline's oracle."""
    live = {row["name"]: row for row in cad_objects if row.get("name")}
    mine = state["objects"]
    missing = sorted(set(live) - set(mine))
    extra = sorted(set(mine) - set(live))
    type_bad = sorted(name for name, row in live.items()
                      if name in mine and mine[name]["type"] is not None
                      and row.get("type_id") != mine[name]["type"])
    unknown_type = sorted(name for name in mine if name in live and mine[name].get("type") is None)
    volume_bad = []
    for name in sorted(live):
        row, got = live[name], mine.get(name) or {}
        live_volume = row.get("volume") or 0
        if not got.get("volume") or not live_volume:
            continue
        if abs(got["volume"] - live_volume) > 0.01 * live_volume:
            volume_bad.append(name)
    total = len(live) + len(extra)
    matched = total - len(missing) - len(extra) - len(type_bad)
    return {"fraction": round(matched / total, 4) if total else 1.0, "missing": missing,
            "extra": extra, "type_mismatch": type_bad, "volume_mismatch": volume_bad,
            "unknown_type": unknown_type}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_training_state.py -v`
Expected: PASS (19 passed).

- [ ] **Step 5: Commit**

```bash
git add cadswarm/training/state.py tests/test_training_state.py
git commit -m "feat(training): symbolic op-log replay and conformance oracle"
```

---

### Task 4: Labels — outcome, step quality, critic pairs

**Files:**
- Create: `cadswarm/training/labels.py`
- Test: `tests/test_training_labels.py`

**Interfaces:**
- Produces: `labels.outcome(brief, tasks) -> str` (`accepted|failed|blocked|abandoned`);
  `labels.quality(review, corrections) -> str` (`reviewed_pass|reviewed_fail|human_corrected|unreviewed`);
  `labels.step_label(step, corrected_indexes, outcome) -> str` (`good|neutral|bad|corrected`);
  `labels.attach(episode) -> None` (fills `episode["labels"]["step_labels"]`);
  `labels.pairs(episode) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_training_labels.py
"""The quality signals that already exist in the records: brief outcome, reviews, corrections."""
from cadswarm.training import labels


def step(index, op="set_props", status="done", broken=()):
    return {"index": index, "op": op, "args": {}, "status": status,
            "result": {"result": {}, "broken": list(broken)}}


def test_a_ready_brief_is_accepted_and_a_failed_one_is_not():
    assert labels.outcome({"status": "ready"}, []) == "accepted"
    assert labels.outcome({"status": "failed"}, []) == "failed"
    assert labels.outcome({"status": "new"}, []) == "abandoned"


def test_a_blocked_task_makes_the_episode_blocked():
    assert labels.outcome({"status": "building"}, [{"status": "blocked"}]) == "blocked"


def test_reviews_and_corrections_outrank_no_signal():
    assert labels.quality({"passed": True}, []) == "reviewed_pass"
    assert labels.quality({"passed": False}, []) == "reviewed_fail"
    assert labels.quality({"passed": True}, [{"after_index": 1}]) == "human_corrected"
    assert labels.quality(None, []) == "unreviewed"


def test_an_empty_or_partial_review_row_is_unreviewed():
    assert labels.quality({}, []) == "unreviewed"
    assert labels.quality({"findings_json": "[]"}, []) == "unreviewed"


def test_step_labels_prefer_bad_then_corrected_then_outcome():
    assert labels.step_label(step(1, status="failed"), set(), "accepted") == "bad"
    assert labels.step_label(step(2, broken=["X"]), set(), "accepted") == "bad"
    assert labels.step_label(step(3), {3}, "accepted") == "corrected"
    assert labels.step_label(step(4), set(), "accepted") == "good"
    assert labels.step_label(step(5), set(), "abandoned") == "neutral"


def test_attach_labels_every_step():
    episode = {"id": "e", "steps": [step(0), step(1, status="failed")], "corrections": [],
               "labels": {"outcome": "accepted", "quality": "unreviewed"}}
    labels.attach(episode)
    assert episode["labels"]["step_labels"] == {"0": "good", "1": "bad"}


def test_only_a_param_correction_makes_a_pair():
    episode = {"id": "e", "steps": [step(0), step(1)],
               "corrections": [
                   {"after_index": 1, "alias": "wall", "before": 3.0, "after": 2.4, "target": "Params.wall"},
                   {"after_index": 1, "alias": None, "edit": "set_props", "target": "Hood",
                    "props": {"height": 2.5}}]}
    [pair] = labels.pairs(episode)
    assert pair["signal"] == "correction"
    assert pair["chosen"] == {"op": "set_param", "args": {"alias": "wall", "value": 2.4}}
    assert pair["rejected"] == {"op": "set_param", "args": {"alias": "wall", "value": 3.0}}


def test_a_correction_that_changed_nothing_is_not_a_pair():
    episode = {"id": "e", "steps": [step(0)],
               "corrections": [{"after_index": 0, "alias": "wall", "before": 2.4, "after": 2.4}]}
    assert labels.pairs(episode) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_training_labels.py -v`
Expected: FAIL — `AttributeError: module 'cadswarm.training.labels' has no attribute 'outcome'`.

- [ ] **Step 3: Write the implementation**

```python
# cadswarm/training/labels.py
"""What a step and a build are worth, from records the app already writes.

`outcome` comes from the brief, `quality` from the review and from human corrections, and a step
is `bad` when it failed or broke something. A param correction is the only record that keeps both
sides of a decision, so it is the only true preference pair in the back-catalogue.
"""


def outcome(brief, tasks):
    status = (brief or {}).get("status", "")
    if status == "ready":
        return "accepted"
    if status == "failed":
        return "failed"
    if status == "cancelled":
        return "abandoned"
    if any((t or {}).get("status") == "blocked" for t in tasks or []):
        return "blocked"
    return "abandoned"


def quality(review, corrections):
    if corrections:
        return "human_corrected"
    if review is not None and "passed" in review:
        return "reviewed_pass" if review["passed"] else "reviewed_fail"
    return "unreviewed"


def step_label(step, corrected_indexes, outcome_):
    if step.get("status") != "done" or (step.get("result") or {}).get("broken"):
        return "bad"
    if step.get("index") in corrected_indexes:
        return "corrected"
    return "good" if outcome_ == "accepted" else "neutral"


def attach(episode):
    """Fill the per-step labels. The episode must already carry `labels.outcome`."""
    corrected = {c["after_index"] for c in episode.get("corrections") or [] if "after_index" in c}
    outcome_ = (episode.get("labels") or {}).get("outcome", "abandoned")
    episode.setdefault("labels", {})["outcome"] = outcome_
    episode["labels"]["step_labels"] = {
        str(step["index"]): step_label(step, corrected, outcome_) for step in episode.get("steps") or []}


def pairs(episode):
    """(chosen, rejected) pairs this episode proves. Only a param correction has both sides."""
    steps = {step["index"]: step for step in episode.get("steps") or []}
    out = []
    for correction in episode.get("corrections") or []:
        alias, before, after = correction.get("alias"), correction.get("before"), correction.get("after")
        if not alias or before is None or after is None or before == after:
            continue
        out.append({"signal": "correction", "episode": episode["id"],
                    "state": (steps.get(correction.get("after_index")) or {}).get("state_before"),
                    "chosen": {"op": "set_param", "args": {"alias": alias, "value": after}},
                    "rejected": {"op": "set_param", "args": {"alias": alias, "value": before}}})
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_training_labels.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add cadswarm/training/labels.py tests/test_training_labels.py
git commit -m "feat(training): outcome, step and preference labels"
```

---

### Task 5: Segmentation into episodes, and the project split

**Files:**
- Create: `cadswarm/training/episodes.py`, `cadswarm/training/split.py`
- Test: `tests/test_training_episodes.py`

**Interfaces:**
- Consumes: `state.replay`, `labels.outcome`, `labels.quality`, `labels.attach`.
- Produces: `episodes.build_episodes(raw, project) -> list[dict]` where `raw` is
  `{member: [...], op: [...], task: [...], brief: [...], review: [...], style_events: [...]}`;
  each episode has `id, project, attribution, request, segments, steps, corrections, review,
  feedback, final, labels, warnings, first_ms`.
- Produces: `split.assign(episodes) -> {mode, train, val, test, projects}` (lists of episode ids).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_training_episodes.py
"""A task's ops become one episode; unrelated ops fall into a freestyle stretch."""
from cadswarm.training import episodes, split

T0 = 1_800_000_000_000


def op(ident, actor, at, created=(), modified=(), deleted=(), reason="create_primitive", task=""):
    import json
    return {"id": ident, "by": actor, "kind": reason, "created_ms": T0 + at, "status": "done",
            "error_code": "",
            "args_json": json.dumps({"name": (created or modified or deleted or ["?"])[0], "kind": "box"}),
            "result_json": json.dumps({"result": {}, "created": list(created), "modified": list(modified),
                                       "deleted": list(deleted), "recomputed": [], "broken": [], "seq": 1}),
            **({"task": task} if task else {})}


def raw():
    return {
        "member": [{"id": 1, "name": "alice", "kind": "human"},
                   {"id": 2, "name": "lucky", "kind": "agent"}],
        "task": [{"id": "t1", "title": "Body", "detail": "", "objects": ["Body", "Wheels"],
                  "status": "done", "brief": "b1", "created_ms": T0, "updated_ms": T0 + 60_000}],
        "brief": [{"id": "b1", "text": "build a car", "board": "", "status": "ready",
                   "plan_json": "{}", "stl_hash": "abc", "created_ms": T0, "updated_ms": T0 + 60_000}],
        "review": [], "style_events": [],
        "op": [op("op_1", 2, 1_000, created=["Body"]),
               op("op_2", 2, 2_000, modified=["Body"]),
               op("op_3", 1, 30 * 60_000, created=["Spoiler"]),   # 30 min later: new freestyle stretch
               op("op_4", 2, 5_000, created=["Wheels"], task="t1")],
    }


def test_ops_are_attributed_by_object_and_time_then_by_explicit_task():
    got = {e["id"].split("/")[-1]: e for e in episodes.build_episodes(raw(), "P")}
    assert got["t1"]["attribution"] == "object_window"
    assert [s["op_id"] for s in got["t1"]["steps"]] == ["op_1", "op_2", "op_4"]


def test_an_idle_gap_starts_a_new_freestyle_episode():
    free = [e for e in episodes.build_episodes(raw(), "P") if e["attribution"] == "freestyle"]
    assert len(free) == 1 and [s["op_id"] for s in free[0]["steps"]] == ["op_3"]


def test_every_op_lands_in_exactly_one_episode():
    seen = [s["op_id"] for e in episodes.build_episodes(raw(), "P") for s in e["steps"]]
    assert sorted(seen) == sorted(o["id"] for o in raw()["op"])


def test_an_explicit_task_column_beats_object_matching():
    data = raw()
    data["op"][2]["task"] = "t1"
    got = {e["id"].split("/")[-1]: e for e in episodes.build_episodes(data, "P")}
    assert got["t1"]["attribution"] == "task_linked" and len(got["t1"]["steps"]) == 4


def test_steps_carry_the_state_before_them_and_a_label():
    episode = episodes.build_episodes(raw(), "P")[0]
    first = episode["steps"][0]
    assert episode["id"].endswith("/t1")
    assert first["state_before"] == {"objects": {}, "params": {}}
    assert first["state_before"] == {"objects": {}, "params": {}}
    assert episode["labels"]["outcome"] == "accepted"
    assert episode["labels"]["step_labels"]["0"] == "good"
    assert episode["final"]["stl_sha256"] == "abc"


def test_split_holds_out_a_whole_project_when_there_are_three():
    eps = [{"id": "a/1", "project": "A", "first_ms": 1}, {"id": "b/1", "project": "B", "first_ms": 2},
           {"id": "c/1", "project": "C", "first_ms": 3}]
    got = split.assign(eps)
    assert got["mode"] == "project" and got["test"] == ["c/1"] and got["val"] == ["b/1"]
    assert got["train"] == ["a/1"]


def test_split_falls_back_to_time_with_one_project():
    eps = [{"id": f"a/{i}", "project": "A", "first_ms": i} for i in range(10)]
    got = split.assign(eps)
    assert got["mode"] == "temporal" and not set(got["train"]) & set(got["test"])
    assert got["test"] == ["a/8", "a/9"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_training_episodes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cadswarm.training.episodes'`.

- [ ] **Step 3: Write the implementation**

```python
# cadswarm/training/episodes.py
"""One build = one episode: a task's steps with its brief, corrections and verdicts.

Attribution is explicit when the op carries a task id (only new sessions do), otherwise the task
whose objects the op touched inside that task's lifetime wins, and everything else is a
freestyle stretch per actor, split on a 15-minute idle gap. An op never lands twice.
"""
import json

from . import labels
from .state import replay

IDLE_GAP_MS = 15 * 60 * 1000
TASK_SLACK_MS = 5 * 60 * 1000
CORRECTION_WINDOW_MS = 60 * 1000
FIELDS = ("created", "modified", "deleted", "recomputed", "broken")


def _json(value, default):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _result(op):
    return _json(op.get("result_json"), {})


def _args(op):
    return _json(op.get("args_json"), {})


def names(op):
    result = _result(op)
    return [n for field in FIELDS for n in (result.get(field) or []) if isinstance(n, str)]


def step_names(step):
    """The object names a finished step touched, from its already-parsed result."""
    result = step.get("result") or {}
    return [n for field in FIELDS for n in (result.get(field) or []) if isinstance(n, str)]


def _actors(members):
    return {m["id"]: {"id": m["id"], "name": m.get("name", str(m["id"])),
                      "kind": m.get("kind", "unknown")} for m in members}


def _match_task(op, tasks):
    explicit = op.get("task") or _args(op).get("task")
    if explicit and explicit in tasks:
        return explicit
    touched, at = set(names(op)), op.get("created_ms", 0)
    best = None
    for task in tasks.values():
        objects = set(task.get("objects") or [])
        if not touched & objects:
            continue
        if not (task.get("created_ms", 0) - TASK_SLACK_MS <= at
                <= task.get("updated_ms", 0) + TASK_SLACK_MS):
            continue
        rank = (len(objects), task.get("created_ms", 0), task["id"])
        if best is None or rank < best[0]:
            best = (rank, task["id"])
    return best[1] if best else None


def _step(op, actors, index):
    result = _result(op)
    return {"index": index, "team_seq": result.get("seq"), "op_id": op.get("id"),
            "at_ms": op.get("created_ms", 0),
            "actor": actors.get(op.get("by"), {"id": op.get("by"), "name": "?", "kind": "unknown"}),
            "op": op.get("kind"), "args": _args(op),
            "result": {key: result.get(key) for key in ("result",) + FIELDS},
            "status": op.get("status"), "error_code": op.get("error_code") or ""}


step_from_op = _step


def replay_project(raw):
    """(before, final, degraded) for the project's whole op log, in order.

    The dataset build uses this for conformance: the replay must end where the live model is.
    """
    ordered = sorted(raw.get("op") or [], key=lambda o: (o.get("created_ms", 0), o.get("id", "")))
    return replay([step_from_op(op, {}, i) for i, op in enumerate(ordered)])


def _corrections(events):
    out = []
    for event in events or []:
        if event.get("kind") != "correction":
            continue
        data = _json(event.get("data"), {})
        out.append({"at_ms": int(float(event.get("at") or 0) * 1000),
                    "target": str(data.get("target") or ""), "alias": data.get("alias"),
                    "before": data.get("before"), "after": data.get("after"),
                    "edit": data.get("edit") or "", "props": data.get("props") or {},
                    "actor_kind": event.get("actor_kind") or "",
                    "evidence": f"style_events#{event.get('id')}"})
    return sorted(out, key=lambda c: c["at_ms"])


def _corrected_step(correction, steps):
    """The latest agent step before the correction that touched the same subject."""
    for step in reversed(steps):
        if step["at_ms"] > correction["at_ms"]:
            continue
        if correction["at_ms"] - step["at_ms"] > CORRECTION_WINDOW_MS:
            break
        if step["actor"]["kind"] != "agent":
            continue
        if (correction["alias"] and step["op"] == "set_param"
                and step["args"].get("alias") == correction["alias"]):
            return step["index"]
        if correction["target"] in set(step_names(step)):
            return step["index"]
    return None


def _episode(project, key, group, actors, tasks, briefs, reviews, corrections, feedback):
    steps = [_step(op, actors, i) for i, op in enumerate(group["ops"])]
    before, final, degraded = replay(steps)
    for position, step in enumerate(steps):
        step["state_before"] = before[position]
        step["state_confidence"] = "degraded" if position in degraded else "full"

    task = tasks.get(key[1]) if key[0] == "task" else None
    brief = briefs.get((task or {}).get("brief") or "") or {}
    found = []
    for correction in corrections:
        if not (steps[0]["at_ms"] - CORRECTION_WINDOW_MS <= correction["at_ms"]
                <= steps[-1]["at_ms"] + CORRECTION_WINDOW_MS):
            continue
        index = _corrected_step(correction, steps)
        if index is not None:
            found.append({**correction, "after_index": index})
    review = (reviews.get(brief.get("id")) or [{}])[-1] if brief else {}
    episode = {
        "schema_version": 1,
        "id": f"{project}/{key[1]}",
        "project": project,
        "attribution": group["attribution"],
        "request": {"text": brief.get("text", ""), "board": brief.get("board", ""),
                    "plan_json": _json(brief.get("plan_json"), {})},
        "segments": [{"task_id": task["id"], "title": task.get("title", ""),
                      "detail": task.get("detail", ""), "objects": task.get("objects") or [],
                      "assignee_kind": task.get("assignee") or task.get("status", ""),
                      "status": task.get("status", ""),
                      "result": _json(task.get("result_json"), {})}] if task else [],
        "steps": steps,
        "corrections": found,
        "review": {k: review.get(k) for k in ("round", "passed", "findings_json")} if review else None,
        "feedback": [f for f in feedback
                     if steps[0]["at_ms"] - CORRECTION_WINDOW_MS <= f["at_ms"]
                     <= steps[-1]["at_ms"] + CORRECTION_WINDOW_MS],
        "final": {"stl_sha256": brief.get("stl_hash") or None, "objects": final["objects"],
                  "params": final["params"], "surface_quality": None},
        "labels": {"outcome": labels.outcome(brief, [task] if task else []),
                   "quality": labels.quality(review or None, found)},
        "warnings": [f"step {i}: unknown object names" for i in degraded],
        "first_ms": steps[0]["at_ms"],
    }
    labels.attach(episode)
    return episode


def build_episodes(raw, project):
    """Every op of one project, grouped into episodes. Input order never matters."""
    actors = _actors(raw.get("member") or [])
    ops = sorted(raw.get("op") or [], key=lambda o: (o.get("created_ms", 0), o.get("id", "")))
    tasks = {t["id"]: t for t in raw.get("task") or []}
    briefs = {b["id"]: b for b in raw.get("brief") or []}
    reviews = {}
    for review in raw.get("review") or []:
        reviews.setdefault(review.get("brief"), []).append(review)
    corrections = _corrections(raw.get("style_events"))
    feedback = [{"at_ms": int(float(e.get("at") or 0) * 1000),
                 "verdict": _json(e.get("data"), {}).get("verdict"),
                 "subject": _json(e.get("data"), {}).get("subject")}
                for e in raw.get("style_events") or [] if e.get("kind") == "feedback"]

    groups, order, last_free = {}, [], {}
    for op in ops:
        actor, at = op.get("by"), op.get("created_ms", 0)
        task_id = _match_task(op, tasks)
        if task_id:
            key = ("task", task_id)
            attribution = "task_linked" if (op.get("task") or _args(op).get("task")) else "object_window"
        else:
            previous = last_free.get(actor)
            key = previous[1] if previous and at - previous[0] <= IDLE_GAP_MS else ("free", f"{actor}-{at}")
            last_free[actor] = (at, key)
            attribution = "freestyle"
        if key not in groups:
            order.append(key)
            groups[key] = {"ops": [], "attribution": attribution}
        groups[key]["ops"].append(op)
        if attribution == "task_linked":
            groups[key]["attribution"] = "task_linked"

    return [_episode(project, key, groups[key], actors, tasks, briefs, reviews, corrections, feedback)
            for key in order]
```

```python
# cadswarm/training/split.py
"""Split by project, never by step: one document per project means steps leak across a step split."""

TEST_SHARE = 0.2
VAL_SHARE = 0.1


def assign(episodes):
    by_project = {}
    for episode in episodes:
        by_project.setdefault(episode["project"], []).append(episode["id"])
    projects = sorted(by_project)
    if len(projects) >= 3:
        val, test = projects[-2], projects[-1]
        return {"mode": "project", "projects": {"val": val, "test": test},
                "train": [i for p in projects if p not in (val, test) for i in by_project[p]],
                "val": by_project[val], "test": by_project[test]}
    ordered = sorted(episodes, key=lambda e: (e["first_ms"], e["id"]))
    n = len(ordered)
    cut_test = max(n - max(1, round(n * TEST_SHARE)), 0)
    cut_val = max(cut_test - max(1, round(n * VAL_SHARE)), 0)
    return {"mode": "temporal", "projects": {},
            "train": [e["id"] for e in ordered[:cut_val]],
            "val": [e["id"] for e in ordered[cut_val:cut_test]],
            "test": [e["id"] for e in ordered[cut_test:]]}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_training_episodes.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add cadswarm/training/episodes.py cadswarm/training/split.py tests/test_training_episodes.py
git commit -m "feat(training): episode segmentation and project split"
```

---

### Task 6: Prompt contract, renderings, and the dataset build

**Files:**
- Create: `cadswarm/training/prompts.py`, `cadswarm/training/render.py`, `cadswarm/training/dataset.py`
- Test: `tests/test_training_render.py`

**Interfaces:**
- Consumes: `episodes.build_episodes`, `split.assign`, `state.replay`, `state.conformance`, `labels.pairs`.
- Produces: `prompts.SYSTEM: str`; `prompts.render_request(request, summary, previous) -> str`;
  `prompts.render_assistant(op, args) -> str`;
  `render.sft_rows(episodes) -> list[dict]`; `render.pair_rows(episodes) -> list[dict]`;
  `render.case_rows(episodes, split) -> list[dict]`; `render.card(project) -> str`;
  `dataset.build(raw_dir, out_root) -> dict` (the manifest).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_training_render.py
"""SFT rows, critic pairs, eval cases and a dataset that is byte-identical when nothing changed."""
import json

from cadswarm.training import dataset, prompts, render
from cadswarm.training import episodes as episodes_mod


def make_episode(ident, outcome, op_name="create_primitive"):
    step = {"index": 0, "op_id": "op_1", "at_ms": 1, "actor": {"id": 1, "name": "a", "kind": "human"},
            "op": op_name, "args": {"name": "Base", "kind": "box"}, "status": "done",
            "error_code": "", "result": {"result": {}, "created": ["Base"], "modified": [],
                                         "deleted": [], "recomputed": [], "broken": []},
            "state_before": {"objects": {}, "params": {}}, "state_confidence": "full"}
    return {"id": ident, "project": "P", "attribution": "freestyle", "first_ms": 1,
            "request": {"text": "build it", "board": "", "plan_json": {}},
            "segments": [], "steps": [step], "corrections": [],
            "review": None, "feedback": [],
            "final": {"stl_sha256": None, "objects": {}, "params": {}, "surface_quality": None},
            "labels": {"outcome": outcome, "quality": "unreviewed", "step_labels": {"0": "good"}},
            "warnings": []}


def test_the_prompt_is_one_stable_contract():
    assert "create_extrusion" in prompts.SYSTEM and '"op"' in prompts.SYSTEM
    text = prompts.render_request({"text": "make a lid", "plan_json": {}},
                                  {"objects": [], "objects_total": 0, "params": {}}, [])
    assert "make a lid" in text and text == prompts.render_request(
        {"text": "make a lid", "plan_json": {}}, {"objects": [], "objects_total": 0, "params": {}}, [])


def test_sft_rows_skip_bad_and_degraded_steps():
    good, bad = make_episode("p/1", "accepted"), make_episode("p/2", "failed")
    bad["labels"]["step_labels"]["0"] = "bad"
    degraded = make_episode("p/3", "accepted")
    degraded["steps"][0]["state_confidence"] = "degraded"
    rows = render.sft_rows([good, bad, degraded])
    assert len(rows) == 1 and rows[0]["meta"]["episode"] == "p/1"
    assert json.loads(rows[0]["messages"][2]["content"]) == {"op": "create_primitive",
                                                             "args": {"name": "Base", "kind": "box"}}


def test_cases_only_hold_out_the_test_split():
    cases = render.case_rows([make_episode("p/1", "accepted"), make_episode("p/2", "accepted")],
                             {"test": ["p/2"]})
    assert [c["id"] for c in cases] == ["p/2"] and cases[0]["reference"][0]["op"] == "create_primitive"


def test_build_writes_the_dataset_and_the_same_inputs_give_the_same_id(tmp_path):
    raw = tmp_path / "raw" / "p"
    raw.mkdir(parents=True)
    (raw / "member.jsonl").write_text(json.dumps({"id": 1, "name": "alice", "kind": "human"}) + "\n",
                                      encoding="utf-8")
    (raw / "op.jsonl").write_text(json.dumps({
        "id": "op_1", "by": 1, "kind": "create_primitive", "created_ms": 1, "status": "done",
        "error_code": "", "args_json": json.dumps({"name": "Base", "kind": "box"}),
        "result_json": json.dumps({"result": {}, "created": ["Base"], "modified": [], "deleted": [],
                                   "recomputed": [], "broken": []})}) + "\n", encoding="utf-8")
    (raw / "source.json").write_text(json.dumps({"project": "P", "status": "ok"}), encoding="utf-8")
    first = dataset.build(str(tmp_path / "raw"), str(tmp_path / "out"))
    second = dataset.build(str(tmp_path / "raw"), str(tmp_path / "out"))
    root = tmp_path / "out" / first["dataset_id"]
    assert (root / "sft.jsonl").exists() and (root / "dataset-card.md").exists()
    assert (root / "manifest.json").exists()
    assert first["dataset_id"] == second["dataset_id"]
    assert first["counts"]["sft_rows"] == second["counts"]["sft_rows"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_training_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cadswarm.training.prompts'`.

- [ ] **Step 3: Write the implementation**

```python
# cadswarm/training/prompts.py
"""The one op contract and the one state renderer.

Training and inference must show the model the same thing, so both render through here. The
assistant side is plain JSON rather than a provider's tool-call object: the dataset stays
portable and any backend can be trained on it.
"""
import json

SYSTEM = """You are the builder for a CAD team. You see one request, its plan, the current model
state and the last few operations. Reply with exactly one op as JSON and nothing else:

{"op": "<name>", "args": {...}}

Ops:
  create_primitive  {"kind": "box|cylinder|sphere|cone|torus", "name": str, "props": {}, "placement": {}}
  create_extrusion  {"name": str, "points": [[x, y], ...], "height": number, "holes": [{"x","y","r"}]}
  boolean           {"kind": "cut|fuse|common", "name": str, "base": str, "tool": str}
  set_props         {"name": str, "props": {"Height": number, ...}}
  set_placement     {"name": str, "placement": {"base": [x, y, z], "rotation": [ax, ay, az]}}
  set_param         {"alias": str, "value": str, "note": str}
  delete            {"name": str}
  run_part_script   {"code": str, "name": str}

Address objects by their exact name. Params are the team spreadsheet: prefer set_param over a
number hard-coded in an op. Never name an object that is not in the model state.
"""


def render_request(request, summary, previous=()):
    """Deterministic: sorted keys, no clock, bounded lists."""
    recent = [{"op": step.get("op"), "args": step.get("args")} for step in list(previous)[-8:]]
    return ("Request: " + str(request.get("text") or "") + "\n"
            + "Plan: " + json.dumps(request.get("plan_json") or {}, sort_keys=True, ensure_ascii=False) + "\n"
            + "Model: " + json.dumps(summary, sort_keys=True, ensure_ascii=False) + "\n"
            + "Recent ops: " + json.dumps(recent, sort_keys=True, ensure_ascii=False))


def render_assistant(op, args):
    return json.dumps({"op": op, "args": args}, sort_keys=True, ensure_ascii=False)
```

```python
# cadswarm/training/render.py
"""Episodes -> the files a trainer and an eval harness read."""
import json

from . import labels, prompts
from .state import summary


def _previous(steps, index):
    return [{"op": s["op"], "args": s["args"]} for s in steps[max(0, index - 8):index]]


def sft_rows(episodes):
    """One row per usable step. A degraded step or a bad label is never an answer to imitate."""
    rows = []
    for episode in episodes:
        step_labels = episode["labels"]["step_labels"]
        for step in episode["steps"]:
            if step_labels.get(str(step["index"])) not in ("good", "neutral"):
                continue
            if step["state_confidence"] != "full":
                continue
            rows.append({"messages": [
                {"role": "system", "content": prompts.SYSTEM},
                {"role": "user", "content": prompts.render_request(
                    episode["request"], summary(step["state_before"]),
                    _previous(episode["steps"], step["index"]))},
                {"role": "assistant", "content": prompts.render_assistant(step["op"], step["args"])}],
                "meta": {"episode": episode["id"], "index": step["index"],
                         "actor_kind": step["actor"]["kind"],
                         "quality": episode["labels"]["quality"]}})
    return rows


def pair_rows(episodes):
    """Step pairs from corrections, plus episode pairs from accepted vs failed builds."""
    rows = [pair for episode in episodes for pair in labels.pairs(episode)]
    accepted = [e for e in episodes if e["labels"]["outcome"] == "accepted"]
    failed = [e for e in episodes if e["labels"]["outcome"] in ("failed", "blocked")]
    for winner, loser in zip(accepted, failed):
        rows.append({"signal": "outcome", "episode": winner["id"],
                     "chosen": {"objects": winner["final"]["objects"]},
                     "rejected": {"objects": loser["final"]["objects"]}})
    return rows


def case_rows(episodes, split):
    """Held-out briefs for replay: the reference sequence is the ceiling, the initial state is empty."""
    test = set(split["test"])
    return [{"id": e["id"], "request": e["request"],
             "state": {"objects": {}, "params": {}},
             "reference": [{"op": s["op"], "args": s["args"]} for s in e["steps"]],
             "final": e["final"], "stl_sha256": e["final"]["stl_sha256"]}
            for e in episodes if e["id"] in test]


def card(manifest):
    """The dataset card: counts, provenance, and the biases a reader needs to distrust nothing."""
    lines = [f"# Dataset {manifest['dataset_id']}", "",
             f"Schema {manifest['schema_version']} · built from {len(manifest['projects'])} project(s).", ""]
    lines += ["| Project | Episodes | Steps |", "|---|---|---|"]
    for name, counts in manifest["projects"].items():
        lines.append(f"| {name} | {counts['episodes']} | {counts['steps']} |")
    lines += ["", "## Split", "", f"Mode: {manifest['split']['mode']}. "
              f"Train {len(manifest['split']['train'])}, val {len(manifest['split']['val'])}, "
              f"test {len(manifest['split']['test'])} episodes.", "", "## Counts", ""]
    lines += [f"- {name}: {value}" for name, value in manifest["counts"].items()]
    lines += ["", "## Biases", ""]
    lines += [f"- {item}" for item in manifest["biases"]]
    lines += ["", "## Conformance", "", "How closely the replayed state matches the live model table.", ""]
    lines += [f"- {name}: {report['fraction']}" for name, report in manifest["conformance"].items()]
    return "\n".join(lines) + "\n"


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
```

```python
# cadswarm/training/dataset.py
"""Build one content-addressed dataset directory from an export directory.

The id is a hash of the files, so the same inputs always produce the same directory name and a
training run can name exactly what it saw. The card and the manifest are the last two files
written; neither is part of the id.
"""
import hashlib
import json
import os
import shutil
import subprocess

from ..client import save_json
from . import SCHEMA_VERSION, render, split as split_mod
from . import episodes as episodes_mod
from . import state

TABLES = ("member", "cad_object", "param", "op", "task", "brief", "review", "team_event",
          "style_events")


def _read_raw(raw_dir, project_dir):
    out = {}
    for name in TABLES:
        path = os.path.join(raw_dir, project_dir, name + ".jsonl")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            out[name] = [json.loads(line) for line in f if line.strip()]
    with open(os.path.join(raw_dir, project_dir, "source.json"), encoding="utf-8") as f:
        out["source"] = json.load(f)
    return out


def _hash_tree(root):
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            digest.update(os.path.relpath(path, root).replace("\\", "/").encode())
            with open(path, "rb") as f:
                digest.update(f.read())
    return digest.hexdigest()


def _git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def build(raw_dir, out_root):
    """Read every exported project, build episodes, render, and write the dataset."""
    projects, all_episodes, conformance = {}, [], {}
    names = sorted(d for d in os.listdir(raw_dir)
                   if os.path.isdir(os.path.join(raw_dir, d)))
    for directory in names:
        raw = _read_raw(raw_dir, directory)
        source = raw.get("source") or {}
        if source.get("status") != "ok":
            continue
        project = source.get("project") or directory
        found = episodes_mod.build_episodes(raw, project)
        if not found:
            continue
        all_episodes += found
        projects[project] = {"episodes": len(found),
                             "steps": sum(len(e["steps"]) for e in found),
                             "source": source}
        _, final, _ = episodes_mod.replay_project(raw)
        conformance[project] = state.conformance(final, raw.get("cad_object") or [],
                                                 raw.get("param") or [])

    assignment = split_mod.assign(all_episodes)
    sft = render.sft_rows(all_episodes)
    pairs = render.pair_rows(all_episodes)
    cases = render.case_rows(all_episodes, assignment)
    biases = []
    if all_episodes:
        top = max(projects.items(), key=lambda kv: kv[1]["steps"])
        share = top[1]["steps"] / max(sum(p["steps"] for p in projects.values()), 1)
        biases.append(f"{top[0]} is {share:.0%} of all steps")
        actors = [s["actor"]["kind"] for e in all_episodes for s in e["steps"]]
        biases.append(f"{actors.count('agent')} agent steps vs {actors.count('human')} human steps")
    if assignment["mode"] == "temporal":
        biases.append("temporal split: steps from one project appear in train and test")

    # The id covers the content, so it is computed from a staging copy before the manifest exists.
    staging = os.path.join(out_root, ".staging")
    os.makedirs(staging, exist_ok=True)
    os.makedirs(os.path.join(staging, "episodes"), exist_ok=True)
    os.makedirs(os.path.join(staging, "eval"), exist_ok=True)
    for episode in all_episodes:
        save_json(os.path.join(staging, "episodes", episode["id"].replace("/", "__") + ".json"),
                  episode)
    render.write_jsonl(os.path.join(staging, "sft.jsonl"), sft)
    render.write_jsonl(os.path.join(staging, "pairs.jsonl"), pairs)
    render.write_jsonl(os.path.join(staging, "eval", "cases.jsonl"), cases)
    save_json(os.path.join(staging, "split.json"), assignment)
    save_json(os.path.join(staging, "conformance.json"), conformance)
    dataset_id = _hash_tree(staging)[:12]

    final_dir = os.path.join(out_root, dataset_id)
    os.makedirs(final_dir, exist_ok=True)
    for name in ("episodes", "eval"):
        os.makedirs(os.path.join(final_dir, name), exist_ok=True)
    for entry in sorted(os.listdir(staging)):
        src = os.path.join(staging, entry)
        dst = os.path.join(final_dir, entry)
        if os.path.isdir(src):
            for inner in sorted(os.listdir(src)):
                os.replace(os.path.join(src, inner), os.path.join(dst, inner))
        else:
            os.replace(src, dst)
    shutil.rmtree(staging, ignore_errors=True)

    manifest = {"dataset_id": dataset_id, "schema_version": SCHEMA_VERSION,
                "git_commit": _git_commit(), "projects": {k: {"episodes": v["episodes"],
                                                              "steps": v["steps"]}
                                                          for k, v in projects.items()},
                "counts": {"episodes": len(all_episodes), "sft_rows": len(sft),
                           "pairs": len(pairs), "eval_cases": len(cases)},
                "split": assignment, "conformance": conformance, "biases": biases}
    with open(os.path.join(final_dir, "dataset-card.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render.card(manifest))
    save_json(os.path.join(final_dir, "manifest.json"), manifest)
    manifest["path"] = final_dir
    return manifest
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_training_render.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add cadswarm/training/prompts.py cadswarm/training/render.py cadswarm/training/dataset.py tests/test_training_render.py
git commit -m "feat(training): render SFT rows, critic pairs and a dataset card"
```

---

### Task 7: Metrics and the heuristic critic

**Files:**
- Create: `cadswarm/training/metrics.py`, `cadswarm/training/critic.py`
- Test: `tests/test_training_metrics.py`

**Interfaces:**
- Produces: `metrics.jaccard(a, b) -> float`; `metrics.bbox_iou(a, b) -> float`;
  `metrics.volume_ratio(a, b) -> float`; `metrics.geometry(reference, candidate) -> dict`;
  `metrics.aggregate(rows) -> dict`; `critic.heuristic(final, printable=None) -> float`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_training_metrics.py
"""Geometry and completion metrics, and the cheap critic score the learned one must beat."""
from cadswarm.training import critic, metrics


def test_jaccard_handles_empty_sets():
    assert metrics.jaccard(set(), set()) == 1.0
    assert metrics.jaccard({"a"}, {"b"}) == 0.0
    assert metrics.jaccard({"a", "b"}, {"b", "c"}) == 1 / 3


def test_bbox_iou_is_one_for_identical_boxes():
    assert metrics.bbox_iou([0, 0, 0, 10, 10, 10], [0, 0, 0, 10, 10, 10]) == 1.0
    assert metrics.bbox_iou([0, 0, 0, 10, 10, 10], [0, 0, 0, 5, 10, 10]) == 0.5


def test_volume_ratio_is_symmetric_log_error():
    assert metrics.volume_ratio(10, 10) == 1.0
    assert metrics.volume_ratio(10, 20) == metrics.volume_ratio(20, 10)
    assert metrics.volume_ratio(0, 10) == 0.0


def test_geometry_compares_names_boxes_and_volumes():
    reference = {"objects": {"A": {"bbox": [0, 0, 0, 1, 1, 1], "volume": 1.0}}}
    candidate = {"objects": {"A": {"bbox": [0, 0, 0, 1, 1, 2], "volume": 2.0}}}
    got = metrics.geometry(reference, candidate)
    assert got["names"] == 1.0 and got["bbox"] == 0.5 and got["volume"] < 1.0


def test_aggregate_averages_and_ignores_missing_values():
    got = metrics.aggregate([{"completion": 1.0, "geometry": {"bbox": 1.0}},
                             {"completion": 0.0, "geometry": {"bbox": 0.0}}])
    assert got["completion"] == 0.5 and got["bbox"] == 0.5


def test_the_heuristic_critic_rewards_a_printable_known_solid():
    solid = {"objects": {"A": {"known": True, "volume": 1.0}}}
    assert critic.heuristic(solid, printable=True) > critic.heuristic(solid, printable=False)
    assert critic.heuristic({"objects": {}}) == 0.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_training_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cadswarm.training.metrics'`.

- [ ] **Step 3: Write the implementation**

```python
# cadswarm/training/metrics.py
"""Comparing a replayed model with the reference one, without heavy dependencies."""


def jaccard(a, b):
    a, b = set(a), set(b)
    if not a and not b:
        return 1.0
    return round(len(a & b) / len(a | b), 4)


def bbox_iou(a, b):
    """Axis-aligned IoU on [x0, y0, z0, x1, y1, z1]."""
    if not a or not b or len(a) < 6 or len(b) < 6:
        return 0.0
    inter = [max(0.0, min(a[3 + i], b[3 + i]) - max(a[i], b[i])) for i in range(3)]
    volume = inter[0] * inter[1] * inter[2]
    size_a = max(0.0, (a[3] - a[0]) * (a[4] - a[1]) * (a[5] - a[2]))
    size_b = max(0.0, (b[3] - b[0]) * (b[4] - b[1]) * (b[5] - b[2]))
    union = size_a + size_b - volume
    return round(volume / union, 4) if union > 0 else 0.0


def volume_ratio(a, b):
    """1.0 for equal volumes, falling off symmetrically."""
    a, b = float(a or 0), float(b or 0)
    if a <= 0 or b <= 0:
        return 0.0
    return round(min(a, b) / max(a, b), 4)


def _objects(view):
    return (view or {}).get("objects") or {}


def geometry(reference, candidate):
    """Name overlap plus per-name box and volume agreement.

    A box is only compared where both sides recorded one: the symbolic replay has volumes but no
    bounding boxes, so `bbox` is None rather than a zero that would read as a bad build.
    """
    ref, got = _objects(reference), _objects(candidate)
    shared = sorted(set(ref) & set(got))
    boxes = [bbox_iou((ref[n] or {}).get("bbox"), (got[n] or {}).get("bbox")) for n in shared
             if (ref[n] or {}).get("bbox") and (got[n] or {}).get("bbox")]
    volumes = [volume_ratio((ref[n] or {}).get("volume"), (got[n] or {}).get("volume"))
               for n in shared]
    return {"names": jaccard(ref, got),
            "bbox": round(sum(boxes) / len(boxes), 4) if boxes else None,
            "volume": round(sum(volumes) / len(volumes), 4) if volumes else 0.0}


def aggregate(rows):
    """Averages the scalar metrics across cases; nested geometry dicts are flattened by key."""
    out, counts = {}, {}
    for row in rows:
        for key, value in row.items():
            values = value if isinstance(value, dict) else {key: value}
            for inner, number in values.items():
                if not isinstance(number, (int, float)) or isinstance(number, bool):
                    continue
                out[inner] = out.get(inner, 0.0) + number
                counts[inner] = counts.get(inner, 0) + 1
    return {key: round(total / counts[key], 4) for key, total in out.items() if counts[key]}
```

```python
# cadswarm/training/critic.py
"""The floor a learned critic must beat: cheap, explainable, and honest about what it cannot see.

A model that scores worse than this on held-out projects is not worth shipping, so it exists to
make every critic run comparable from the first day, before any pairs exist.
"""


def heuristic(final, printable=None):
    """0..1 from solidity, printability and (when recorded) curved-surface share."""
    objects = (final or {}).get("objects") or {}
    if not objects:
        return 0.0
    known = sum(1 for o in objects.values() if o.get("known"))
    curved = (final or {}).get("surface_quality") or {}
    score = 0.4 * known / len(objects)
    score += 0.4 * (1.0 if printable else 0.0) if printable is not None else 0.2
    share = curved.get("curved_share") if isinstance(curved, dict) else None
    score += 0.2 * (0.5 if share is None else min(float(share) / 0.2, 1.0))
    return round(min(score, 1.0), 3)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_training_metrics.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add cadswarm/training/metrics.py cadswarm/training/critic.py tests/test_training_metrics.py
git commit -m "feat(training): geometry metrics and the heuristic critic"
```

---

### Task 8: Replay evaluation against a real FreeCAD worker

**Files:**
- Create: `cadswarm/training/eval_replay.py`
- Test: `tests/test_training_replay.py`

**Interfaces:**
- Consumes: `..worker_client.WorkerClient`, `metrics.geometry`, `metrics.jaccard`.
- Produces: `eval_replay.ScriptedPolicy(ops)`, `eval_replay.RandomPolicy(seed)`, each with
  `.next_op(seen) -> dict|None`; `eval_replay.replay(case, policy, python=None, script=None,
  workdir=None, max_steps=40, client=None) -> dict`; `eval_replay.report(results) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_training_replay.py
"""Scoring a policy by running it, through the same JSON-lines worker the app uses."""
from cadswarm.training import eval_replay


class FakeWorker:
    """Accepts create_primitive, refuses anything named Bad, and describes one box."""

    def __init__(self):
        self.calls = []

    def call(self, op, args=None, allowed=None):
        self.calls.append((op, args))
        if op == "create_primitive":
            if (args or {}).get("name") == "Bad":
                raise eval_replay.WorkerError("lock_violation", "not yours")
            return {"ok": True, "result": {"name": args["name"]}, "created": [args["name"]],
                    "modified": [], "deleted": [], "recomputed": []}
        if op == "status":
            return {"ok": True, "result": {"objects": [{"name": "Base", "bbox": [0, 0, 0, 1, 1, 1],
                                                        "volume": 1.0}], "params": {}}}
        if op == "printability":
            return {"ok": True, "result": {"ok": True, "objects": []}}
        raise AssertionError(op)

    def stop(self):
        pass


def case(ops):
    return {"id": "p/1", "request": {"text": "x", "plan_json": {}},
            "state": {"objects": {}, "params": {}},
            "reference": ops, "final": {"objects": {"Base": {"bbox": [0, 0, 0, 1, 1, 1],
                                                             "volume": 1.0}}},
            "stl_sha256": None}


def box(name):
    return {"op": "create_primitive", "args": {"kind": "box", "name": name}}


def test_a_scripted_policy_reproduces_the_reference_score():
    ops = [box("Base")]
    got = eval_replay.replay(case(ops), eval_replay.ScriptedPolicy(ops), client=FakeWorker())
    assert got["valid_op_rate"] == 1.0 and got["completion"] == 1.0
    assert got["geometry"]["bbox"] == 1.0 and got["printable"] is True


def test_a_refused_op_counts_against_the_valid_rate():
    ops = [box("Bad")]
    got = eval_replay.replay(case(ops), eval_replay.ScriptedPolicy(ops), client=FakeWorker())
    assert got["valid_op_rate"] == 0.0 and got["rejection_rate"] == 1.0


def test_the_random_baseline_is_deterministic_and_stops_at_the_cap():
    one = eval_replay.replay(case([box("Base")]), eval_replay.RandomPolicy(seed=4), client=FakeWorker(),
                             max_steps=3)
    two = eval_replay.replay(case([box("Base")]), eval_replay.RandomPolicy(seed=4), client=FakeWorker(),
                             max_steps=3)
    assert one["steps"] == two["steps"] == 3
    assert [o["args"] for o in one["ops"]] == [o["args"] for o in two["ops"]]


def test_a_policy_that_returns_nothing_stops():
    got = eval_replay.replay(case([]), eval_replay.ScriptedPolicy([]), client=FakeWorker())
    assert got["steps"] == 0 and got["valid_op_rate"] == 0.0


def test_report_aggregates_and_names_the_policy():
    rows = [eval_replay.replay(case([box("Base")]), eval_replay.ScriptedPolicy([box("Base")]),
                               client=FakeWorker())]
    got = eval_replay.report(rows, policy="reference")
    assert got["policy"] == "reference" and got["cases"] == 1 and got["completion"] == 1.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_training_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cadswarm.training.eval_replay'`.

- [ ] **Step 3: Write the implementation**

```python
# cadswarm/training/eval_replay.py
"""Score a policy by building in a real FreeCAD document.

A fresh scratch document per case, the same WorkerClient the app uses, and the worker's own
error codes surfaced rather than swallowed. The reference policy is the ceiling; the random
policy is the floor; `base` and `trained` are what the numbers are actually about.
"""
import os
import random
import tempfile

from ..worker_client import WorkerClient, WorkerError
from . import metrics

__all__ = ["WorkerError", "ScriptedPolicy", "RandomPolicy", "replay", "report"]


class ScriptedPolicy:
    """Replays a fixed op sequence: the reference case, or any recorded build."""
    name = "scripted"

    def __init__(self, ops):
        self.ops = [dict(op) for op in ops]

    def next_op(self, seen):
        return self.ops[len(seen)] if len(seen) < len(self.ops) else None


class RandomPolicy:
    """A deterministic floor: valid-shaped primitives with unrelated names."""
    name = "random"

    def __init__(self, seed, max_steps=8):
        self.random = random.Random(seed)
        self.max_steps = max_steps

    def next_op(self, seen):
        if len(seen) >= self.max_steps:
            return None
        kind = self.random.choice(["box", "cylinder", "sphere"])
        return {"op": "create_primitive",
                "args": {"kind": kind, "name": f"R{self.random.randrange(1000)}"}}


def _op_view(objects):
    return {"objects": {o.get("name"): {"bbox": o.get("bbox"), "volume": o.get("volume"),
                                        "known": True} for o in objects or []}}


def replay(case, policy, python=None, script=None, workdir=None, max_steps=40, client=None):
    """Run `policy` on a scratch document and score it against the case's reference model."""
    root = workdir or tempfile.mkdtemp(prefix="cadswarm-eval-")
    os.makedirs(root, exist_ok=True)
    worker = client or WorkerClient(os.path.join(root, "eval.FCStd"), python=python, script=script)
    seen = []
    try:
        for _ in range(max_steps):
            op = policy.next_op(seen)
            if op is None:
                break
            try:
                worker.call(op["op"], op.get("args") or {}, allowed=op.get("allowed"))
                seen.append({"op": op["op"], "args": op.get("args"), "ok": True, "code": ""})
            except WorkerError as e:
                seen.append({"op": op["op"], "args": op.get("args"), "ok": False, "code": e.code})
        final = worker.call("status").get("result") or {}
        printable = bool((worker.call("printability", {}).get("result") or {}).get("ok"))
    finally:
        worker.stop()

    attempted = len(seen)
    valid = sum(1 for step in seen if step["ok"])
    rejected = sum(1 for step in seen if step["code"] == "lock_violation")
    reference = (case.get("final") or {}).get("objects") or {}
    candidate = _op_view(final.get("objects"))
    return {"case": case["id"], "steps": attempted,
            "valid_op_rate": round(valid / attempted, 4) if attempted else 0.0,
            "rejection_rate": round(rejected / attempted, 4) if attempted else 0.0,
            "completion": metrics.jaccard(reference.keys(),
                                          [o.get("name") for o in final.get("objects") or []]),
            "geometry": metrics.geometry({"objects": reference}, candidate),
            "printable": printable, "ops": seen}


def report(results, policy):
    """One comparable row set per policy, with the case count that produced it."""
    summary = metrics.aggregate(results)
    summary.update({"policy": policy, "cases": len(results),
                    "printable_rate": round(sum(1 for r in results if r["printable"]) / len(results), 4)
                    if results else 0.0})
    return summary
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_training_replay.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add cadswarm/training/eval_replay.py tests/test_training_replay.py
git commit -m "feat(training): replay evaluation against the FreeCAD worker"
```

---

### Task 9: CLI, packaging isolation, docs, and a live run

**Files:**
- Create: `cadswarm/training/cli.py`, `cadswarm/training/__main__.py`
- Modify: `install/install.ps1` (the `/XD` list), `install/install.sh` (the `tar --exclude` list), `README.md` (test count)
- Test: `tests/test_training_package.py`

**Interfaces:**
- Produces: `cli.main(argv=None) -> int` with subcommands `export`, `build`, `eval`;
  `python -m cadswarm.training <subcommand>`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_training_package.py
"""Training code must not reach a user's FreeCAD, and must not drag torch into the app."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def test_the_windows_installer_excludes_training():
    assert "cadswarm\\training" in read(os.path.join("install", "install.ps1"))


def test_the_posix_installer_excludes_training():
    assert "cadswarm/training" in read(os.path.join("install", "install.sh"))


def test_importing_the_package_loads_no_heavy_dependency():
    code = ("import sys, cadswarm.training, cadswarm.training.eval_replay;"
            "print(sorted(m for m in ('FreeCAD', 'torch', 'transformers', 'peft') if m in sys.modules))")
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert got.stdout.strip() == "[]", got.stderr


def test_the_cli_has_the_three_subcommands():
    from cadswarm.training import cli
    assert {"export", "build", "eval"} <= cli.SUBCOMMANDS
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_training_package.py -v`
Expected: FAIL — `AttributeError: module 'cadswarm.training.cli' has no attribute 'SUBCOMMANDS'`.

- [ ] **Step 3: Write the implementation**

```python
# cadswarm/training/cli.py
"""python -m cadswarm.training export|build|eval

export  one or more project folders -> raw JSONL
build   a raw directory -> a content-addressed dataset
eval    a dataset's held-out cases -> a scorecard
"""
import argparse
import json
import os
import sys

from . import dataset, eval_replay, export

SUBCOMMANDS = ("export", "build", "eval")


def _export(args):
    infos = [export.export_project(path, args.out) for path in args.project]
    for info in infos:
        print(f"{info['project']}: {info['status']} {info['tables']}")
    return 0 if any(i["status"] == "ok" for i in infos) else 1


def _build(args):
    manifest = dataset.build(args.raw, args.out)
    print(json.dumps({k: manifest[k] for k in ("dataset_id", "counts", "split")}, indent=2))
    return 0


def _eval(args):
    root = os.path.join(args.dataset, "eval", "cases.jsonl")
    with open(root, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    policies = {"reference": lambda c: eval_replay.ScriptedPolicy(c["reference"]),
                "random": lambda c: eval_replay.RandomPolicy(seed=1)}
    policy = policies[args.policy]
    rows = [eval_replay.replay(case, policy(case), python=args.python, script=args.script)
            for case in cases]
    report = eval_replay.report(rows, policy=args.policy)
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m cadswarm.training")
    sub = parser.add_subparsers(dest="command", required=True)
    p_export = sub.add_parser("export", help="project folders -> raw JSONL")
    p_export.add_argument("--project", action="append", required=True)
    p_export.add_argument("--out", required=True)
    p_export.set_defaults(run=_export)
    p_build = sub.add_parser("build", help="raw JSONL -> a dataset")
    p_build.add_argument("--raw", required=True)
    p_build.add_argument("--out", required=True)
    p_build.set_defaults(run=_build)
    p_eval = sub.add_parser("eval", help="replay a dataset's held-out cases")
    p_eval.add_argument("--dataset", required=True)
    p_eval.add_argument("--policy", choices=("reference", "random"), default="random")
    p_eval.add_argument("--python", default=None)
    p_eval.add_argument("--script", default=None)
    p_eval.add_argument("--out", default=None)
    p_eval.set_defaults(run=_eval)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
```

```python
# cadswarm/training/__main__.py
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

Then edit `install/install.ps1` line 83 to add `cadswarm\training` to the `/XD` list, and
`install/install.sh` line 209 to add `--exclude=cadswarm/training` to the `tar` call. Both lists
already exclude `cadswarm-data`, which is where datasets live.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `python -m pytest tests/test_training_package.py -v`
Expected: PASS (4 passed).

Run: `python -m pytest`
Expected: the whole suite passes; note the new total for the README step.

- [ ] **Step 5: Update README test counts and commit**

Update the test counts in `README.md` to the number the full run printed, then:

```bash
git add cadswarm/training install/install.ps1 install/install.sh README.md tests/test_training_package.py
git commit -m "feat(training): CLI, addon exclusion, and test counts"
```

- [ ] **Step 6: Live check on the real project (not in CI)**

Run:

```bash
python -m cadswarm.training export --project "C:\Users\waves\Documents\CadSwarm\Lexus IS350" --out cadswarm-data
python -m cadswarm.training build --raw cadswarm-data\raw --out cadswarm-data\datasets
```

Expected: `source.json` says `ok` with 606 ops; the build prints a dataset id, `sft_rows` > 0,
and `conformance` for `Lexus IS350` at **≥ 0.95**. Every entry in `missing`, `extra`,
`type_mismatch` and `volume_mismatch` is explained in a comment in the dataset card before this
phase counts as done — a low fraction is a bug in `state.py`, not a number to accept.

Then run the eval floor and ceiling on the same dataset:

```bash
python -m cadswarm.training eval --dataset cadswarm-data\datasets\<id> --policy reference
python -m cadswarm.training eval --dataset cadswarm-data\datasets\<id> --policy random
```

Expected: `reference` completion and bbox near 1.0; `random` far below. If both are zero, the
worker never started — check `--python` and `--script`.

---

## Plans 2 and 3

This plan stops at a dataset plus a working replay harness, which is testable software on its own.
Two follow-ups get their own plans once this lands:

- **Plan 2 — capture (spec §7):** additive `op.task`/`op.brief`, the `agent_turn` table, per-step
  geometry JSON, and the migration rehearsal on a copy of the live DB. It is separate because it
  changes a live database contract and must be planned against the module, not the trainer.
- **Plan 3 — training (spec §8):** `train.py` with the QLoRA path, `--check` preflight, and the
  first builder and critic runs. It is separate because its exact shape depends on the counts the
  first real dataset reports (606 ops today, which is below the §8 gate).

```
```