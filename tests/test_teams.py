"""The notebook of teams this computer has joined, so any previous project can be reopened."""
from cadswarm import teams


def use_tmp(monkeypatch, tmp_path):
    # app_dir() prefers %APPDATA% on every platform, so pointing it at a temp folder isolates the notebook.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_remember_and_list_most_recent_first(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    assert teams.recent_teams() == []  # nothing yet, no error
    teams.remember_team("Phone stand", "http://a", "db_a", host_data_dir="/host/a", host_mode="local")
    teams.remember_team("Plant pot", "http://b", "db_b")
    got = teams.recent_teams()
    assert [t["project"] for t in got] == ["Plant pot", "Phone stand"]
    assert got[1] == {"project": "Phone stand", "uri": "http://a", "db": "db_a",
                      "host_data_dir": "/host/a", "host_mode": "local", "at": got[1]["at"]}


def test_reopening_a_team_moves_it_to_the_front(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    teams.remember_team("Phone stand", "http://a", "db_a")
    teams.remember_team("Plant pot", "http://b", "db_b")
    teams.remember_team("Phone stand v2", "http://a", "db_a")  # same team, renamed project
    got = teams.recent_teams()
    assert [t["project"] for t in got] == ["Phone stand v2", "Plant pot"]  # no duplicate db
    assert got[0]["uri"] == "http://a"


def test_incomplete_rows_are_ignored_and_the_list_is_capped(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    teams.remember_team("", "http://x", "db_x")        # no project: not worth remembering
    teams.remember_team("No db", "http://x", "")       # no team id: unreopenable
    assert teams.recent_teams() == []
    for i in range(teams.LIMIT + 4):
        teams.remember_team(f"Project {i}", "http://x", f"db_{i}")
    got = teams.recent_teams()
    assert len(got) == teams.LIMIT and got[0]["project"] == f"Project {teams.LIMIT + 3}"
