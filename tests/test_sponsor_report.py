"""The FreeCAD Sponsors tab: the same three services as the web panel, from the snapshot the panel already has."""
from types import SimpleNamespace

from cadswarm.gui.sponsors import cards, evidence_line


def session(**over):
    base = dict(
        model={"seq": 12, "params": {"wall": 2.4}, "objects": [{"name": "Base"}, {"name": "Lid"}],
               "participants": [
                   {"id": 1, "name": "carol", "kind": "human", "client": "freecad", "status": "active", "locks": []},
                   {"id": 2, "name": "ann-claude", "kind": "agent", "client": "claude", "status": "active",
                    "locks": ["Base"]},
                   {"id": 3, "name": "dee-droid", "kind": "reviewer", "client": "droid", "status": "away",
                    "locks": []},
               ]},
        tasks=[{"id": "t_1", "status": "open", "created_by_name": "dee-droid"},
               {"id": "t_2", "status": "done", "created_by_name": "dee-droid"},
               {"id": "t_3", "status": "done", "created_by_name": "carol"}],
        briefs=[{"id": "b_old", "board": "", "at": 1},
                {"id": "b_new", "board": "esp32-s3-devkitc-1", "at": 5}],
        reviews=[
            {"round": 2, "passed": False, "findings": ["The wall is 1.1 mm.", "The cutout is 1.8 mm high."]},
            {"round": 1, "passed": False, "findings": ["The reviewer did not report a verdict."]},
        ],
    )
    base.update(over)
    return SimpleNamespace(**base)


def card(cards_, name):
    return next(c for c in cards_ if c["name"] == name)


def test_counts_the_team_state_spacetimedb_holds():
    space = card(cards(session(), "cadswarm-demo"), "SpacetimeDB")
    assert ("database", "cadswarm-demo") in space["facts"]
    assert ("members", "2 active · 3 joined") in space["facts"]
    assert ("model", "2 parts · 1 params · 1 held") in space["facts"]
    assert ("work", "1 open tasks · 2/3 done") in space["facts"]
    assert ("events", "seq 12 · the Activity tab is the live feed") in space["facts"]


def test_factory_is_the_reviewer_with_its_newest_round():
    factory = card(cards(session()), "Factory.ai")
    assert ("agent", "dee-droid · droid · away") in factory["facts"]
    assert ("last review", "round 2 · 2 findings") in factory["facts"]
    assert ("tasks filed", "2") in factory["facts"]
    assert factory["list"]["rows"] == ["The wall is 1.1 mm.", "The cutout is 1.8 mm high."]


def test_elastic_gets_the_newest_brief_its_board_and_the_findings():
    elastic = card(cards(session()), "Elastic")
    assert ("board", "esp32-s3-devkitc-1") in elastic["facts"]
    assert ("design memory", "b_new · 2 problems") in elastic["facts"]
    assert elastic["list"]["rows"] == ["esp32-s3-devkitc-1::The wall is 1.1 mm.",
                                       "esp32-s3-devkitc-1::The cutout is 1.8 mm high."]


def test_says_so_when_nothing_is_set_up_yet():
    empty = session(model={"participants": [], "objects": [], "params": {}}, tasks=[], briefs=[], reviews=[])
    factory = card(cards(empty), "Factory.ai")
    assert ("agent", "not set up") in factory["facts"]
    assert ("last review", "no review yet") in factory["facts"]
    assert factory["list"] is None
    assert card(cards(empty), "Elastic")["facts"][1] == ("design memory", "no designs yet")


def test_the_stamped_evidence_stays_labelled_checked():
    line = evidence_line()
    assert line.startswith("checked ") and "Elastic 6 board docs" in line and "Droid 0.249.0" in line
