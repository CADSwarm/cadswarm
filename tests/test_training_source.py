"""Reading a team database through the same client the app uses, paged by primary key."""
import json
import os
import re

import pytest

from cadswarm.training import source


class FakeClient:
    """Answers the subset of SQL `Source.rows` produces, so paging is tested without a server."""

    def __init__(self, tables):
        self.tables = tables
        self.queries = []

    def sql(self, query):
        self.queries.append(query)
        table = re.search(r"from (\w+)", query).group(1)
        rows = list(self.tables.get(table, []))
        after = re.search(r"where (\w+) > '((?:[^']|'')*)'", query)
        if after:
            key, value = after.groups()
            rows = [r for r in rows if str(r[key]) > value.replace("''", "'")]  # '' is a quote in SQL
        limit = re.search(r"limit (\d+)", query)
        return rows[: int(limit.group(1))] if limit else rows


def test_rows_are_paged_by_primary_key_and_come_back_complete():
    rows = [{"id": f"op_{i:02d}", "created_ms": 5} for i in range(5)]
    client = FakeClient({"op": rows})
    got = source.Source("P", "C:/p", "http://x", "db", "tok", client=client, chunk=2).rows("op")
    assert [r["id"] for r in got] == [r["id"] for r in rows]
    assert len(client.queries) == 3 and all("limit 2" in q for q in client.queries)


def test_single_short_page_stops_after_one_query():
    client = FakeClient({"task": [{"id": "t1"}]})
    source.Source("P", "C:/p", "http://x", "db", "tok", client=client).rows("task")
    assert len(client.queries) == 1


def test_quotes_in_a_key_do_not_break_paging():
    client = FakeClient({"cad_object": [{"key": "1:it's"}]})
    source.Source("P", "C:/p", "http://x", "db", "tok", client=client, chunk=1).rows("cad_object")
    assert len(client.queries) == 2 and "it''s" in client.queries[1]


def test_a_missing_host_file_is_a_source_error(tmp_path):
    with pytest.raises(source.SourceError):
        source.read_host(str(tmp_path))


def test_for_project_reads_uri_and_db_from_host_json(tmp_path):
    (tmp_path / "host.json").write_text(json.dumps({"uri": "http://127.0.0.1:3000", "db": "cadswarm-x",
                                                    "token": "secret"}), encoding="utf-8")
    got = source.Source.for_project(str(tmp_path))
    assert (got.uri, got.db, got.project) == ("http://127.0.0.1:3000", "cadswarm-x", os.path.basename(str(tmp_path)))
