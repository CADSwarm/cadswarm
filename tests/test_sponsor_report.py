"""The FreeCAD Sponsors tab: the same five services as the web panel, from the snapshot the panel already has."""
from types import SimpleNamespace

from cadswarm.gui.sponsors import cards, evidence_line


def session(**over):
    base = dict(
        model={"seq": 12, "params": {"wall": 2.4},
               "objects": [{"name": "Base"}, {"name": "Lid"}, {"name": "Params"}],
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
            {"brief": "b_new", "round": 2, "passed": False,
             "findings": ["The wall is 1.1 mm.", "The cutout is 1.8 mm high."]},
            {"brief": "b_new", "round": 1, "passed": False, "findings": ["The reviewer did not report a verdict."]},
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
    assert ("pointer", "own arrow, in its own colour") in factory["facts"]
    assert factory["list"]["rows"] == ["The wall is 1.1 mm.", "The cutout is 1.8 mm high."]


def test_elastic_gets_the_newest_brief_its_board_and_the_findings():
    elastic = card(cards(session()), "Elastic")
    assert ("board", "esp32-s3-devkitc-1") in elastic["facts"]
    assert ("design memory", "b_new · 2 problems") in elastic["facts"]
    assert elastic["list"]["rows"] == ["esp32-s3-devkitc-1::The wall is 1.1 mm.",
                                       "esp32-s3-devkitc-1::The cutout is 1.8 mm high."]


def test_the_newest_brief_does_not_borrow_another_briefs_review():
    # An old brief's round 3 must not be shown as the new brief's verdict.
    s = session(reviews=[
        {"brief": "b_old", "round": 3, "passed": True, "findings": []},
        {"brief": "b_new", "round": 1, "passed": False, "findings": ["The lid clips."]},
    ])
    factory = card(cards(s), "Factory.ai")
    assert ("last review", "round 1 · 1 findings") in factory["facts"]
    assert factory["list"]["rows"] == ["The lid clips."]


def test_says_so_when_nothing_is_set_up_yet():
    empty = session(model={"participants": [], "objects": [], "params": {}}, tasks=[], briefs=[], reviews=[])
    factory = card(cards(empty), "Factory.ai")
    assert ("agent", "not set up") in factory["facts"]
    assert ("last review", "no review yet") in factory["facts"]
    assert ("pointer", "none until the reviewer joins") in factory["facts"]
    assert factory["list"] is None
    assert card(cards(empty), "Elastic")["facts"][1] == ("design memory", "no designs yet")


def test_the_stamped_evidence_stays_labelled_checked():
    line = evidence_line()
    assert line.startswith("checked ") and "Elastic 6 board docs" in line and "Droid 0.249.0" in line


def test_the_window_is_five_cards_in_the_same_order_as_the_web():
    assert [c["id"] for c in cards(session())] == ["spacetime", "anthropic", "creao", "factory", "elastic"]


def test_anthropic_is_the_claude_helpers_and_their_finished_work():
    s = session()
    s.model["participants"] += [
        {"id": 7, "name": "lid-creao", "kind": "agent", "client": "creao", "status": "stale", "locks": []},
    ]
    s.tasks += [
        {"id": "t_7", "status": "done", "owner": 2, "assignee": "ann-claude", "created_by_name": "carol"},
        {"id": "t_8", "status": "open", "owner": 2, "assignee": "ann-claude", "created_by_name": "carol"},
        {"id": "t_9", "status": "done", "owner": 7, "assignee": "lid-creao", "created_by_name": "carol"},
    ]
    ann = card(cards(s), "Anthropic")
    assert ("agents", "ann-claude · active") in ann["facts"]
    assert ("tasks done", "1/2") in ann["facts"]
    assert ("live trace", "the web panel") in ann["facts"]
    assert "trace" not in ann


def test_creao_shows_stale_and_its_mode():
    s = session()
    s.model["participants"] += [
        {"id": 7, "name": "lid-creao", "kind": "agent", "client": "creao", "status": "stale", "locks": []},
    ]
    s.tasks += [{"id": "t_9", "status": "done", "owner": 7, "assignee": "lid-creao", "created_by_name": "carol"}]
    creao = card(cards(s), "Creao AI")
    assert ("helper", "lid-creao · stale") in creao["facts"]
    assert ("mode", "local bridge · one JSON tool call per turn") in creao["facts"]
    assert ("tasks done", "1/1") in creao["facts"]


def test_both_cards_stay_when_neither_helper_has_joined():
    s = session(model={"seq": 1, "params": {}, "objects": [], "participants": []}, tasks=[], briefs=[], reviews=[])
    names = [c["name"] for c in cards(s)]
    assert names == ["SpacetimeDB", "Anthropic", "Creao AI", "Factory.ai", "Elastic"]
    assert card(cards(s), "Anthropic")["facts"][0] == ("agents", "not joined")
    assert card(cards(s), "Creao AI")["facts"][0] == ("helper", "not joined")


def test_the_spacetime_card_says_where_the_database_lives_and_the_sign_in():
    space = card(cards(session(), "cadswarm-demo", "SpacetimeDB Maincloud", "signed in as c200641aabd1"), "SpacetimeDB")
    assert ("server", "SpacetimeDB Maincloud") in space["facts"]
    assert ("sign-in", "signed in as c200641aabd1") in space["facts"]


def test_the_spacetime_card_stays_quiet_when_the_panel_does_not_know_the_server():
    # A snapshot with no server/sign-in still renders the counts it always did.
    space = card(cards(session(), "cadswarm-demo"), "SpacetimeDB")
    assert [k for k, _ in space["facts"]][:1] == ["database"]

