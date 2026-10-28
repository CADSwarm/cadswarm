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


def test_for_project_reads_uri_and_db_from_host_json(tmp_path):
    (tmp_path / "host.json").write_text(json.dumps({"uri": "http://127.0.0.1:3000", "db": "cadswarm-x",
                                                    "token": "secret"}), encoding="utf-8")
    got = source.Source.for_project(str(tmp_path))
    assert (got.uri, got.db, got.project) == ("http://127.0.0.1:3000", "cadswarm-x",
                                              os.path.basename(str(tmp_path)))
