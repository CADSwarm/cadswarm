"""One project's tables become JSONL files, and a hostile source is recorded rather than fatal."""
import json
import sqlite3

from cadswarm.training import export


class FakeSource:
    def __init__(self, project="Test Project", project_dir=None):
        self.project = project
        self.project_dir = project_dir or ""
        self.uri = "http://127.0.0.1:3000"
        self.db = "cadswarm-test"
        self.tables = {"member": [{"id": 1, "name": "alice", "kind": "human"}],
                       "op": [{"id": "op_1", "kind": "create_primitive", "created_ms": 1}]}

    def rows(self, table):
        return self.tables.get(table, [])


class PausedSource(FakeSource):
    def rows(self, table):
        raise export.SpaceError(503, "database is paused")


def test_export_writes_one_file_per_table_and_a_source_record(tmp_path):
    info = export.export_source(FakeSource(), str(tmp_path))
    raw = tmp_path / "raw" / "test-project"
    assert json.loads((raw / "op.jsonl").read_text(encoding="utf-8").splitlines()[0])["id"] == "op_1"
    assert (raw / "member.jsonl").exists()
    assert info["status"] == "ok" and info["tables"]["op"] == 1
    assert (raw / "source.json").exists()


def test_the_token_never_reaches_disk(tmp_path):
    source = FakeSource()
    source.token = "eyJhbGciOiJSUzI1NiJ9.secret"
    export.export_source(source, str(tmp_path))
    written = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*") if p.is_file())
    assert "eyJhbGciOiJSUzI1NiJ9" not in written


def test_a_paused_database_is_recorded_as_skipped(tmp_path):
    info = export.export_source(PausedSource(), str(tmp_path))
    assert info["status"].startswith("skipped:") and "paused" in info["status"]
    assert (tmp_path / "raw" / "test-project" / "source.json").exists()


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
