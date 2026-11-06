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
