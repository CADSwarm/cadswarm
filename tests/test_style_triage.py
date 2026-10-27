"""The learned-style triage inbox: decisions, recommendations, expiry and bulk resolution."""
import json
import sqlite3

import pytest

from cadswarm import style_memory
from cadswarm.style_memory import StyleMemory

DAY = 86400.0
ALICE = {"id": 1, "name": "alice", "kind": "human"}
BOB = {"id": 2, "name": "bob", "kind": "human"}
AGENT = {"id": 9, "name": "worker1-ab", "kind": "agent"}


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    db = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    clock = Clock()
    return StyleMemory(db, clock), clock


def set_param(style, actor, alias, value):
    return style.observe_op({"kind": "set_param", "args": {"alias": alias, "value": value, "_settings": {}},
                             "ok": True, "actor": actor})


def section(box, kind):
    return next((s for s in box["sections"] if s["kind"] == kind), None)


def decision_for(box, subject):
    for s in box["sections"]:
        for d in s["decisions"]:
            if d["subject"] == subject:
                return d
    return None


def all_decisions(box):
    return [d for s in box["sections"] for d in s["decisions"]]


def test_recommend_is_deterministic_per_kind():
    rec = style_memory.recommend
    # Corrections count double; two corrections are already a strong signal.
    assert rec("correction_pattern", {"n": 4}, 0.4) == "confirm"
    assert rec("correction_pattern", {"n": 2}, 0.3) == "look"
    # A review finding seen in two designs is a lesson; one design is a one-off.
    assert rec("recurring_problem", {"n": 4}, 0.3) == "confirm"
    assert rec("recurring_problem", {"n": 2}, 0.3) == "dismiss"
    # A shared size needs both volume and confidence.
    assert rec("param_convention", {"n": 6}, 0.55) == "confirm"
    assert rec("param_convention", {"n": 3}, 0.5) == "look"
    assert rec("param_convention", {"n": 1}, 0.15) == "dismiss"
    # A preferred part is a rule only once it is actually preferred.
    assert rec("preferred_component", {"n": 5, "consistency": 0.83}, 0.4) == "confirm"
    assert rec("preferred_component", {"n": 6, "consistency": 0.5}, 0.4) == "look"
    assert rec("preferred_component", {"n": 1, "consistency": 1.0}, 0.2) == "dismiss"
    # A habit needs the same volume as a size.
    assert rec("modelling_practice", {"n": 5}, 0.5) == "confirm"
    assert rec("modelling_practice", {"n": 2}, 0.3) == "look"
    # A helper's proposal is never classified for a person.
    assert rec("proposed", {"n": 0}, 0.5, source="proposed") == "look"


def test_a_param_convention_and_its_correction_are_one_decision(env):
    style, clock = env
    for _ in range(2):
        set_param(style, AGENT, "wall", "1.6")
        set_param(style, ALICE, "wall", "2.4")
        clock.t += DAY
    style.distil()
    box = style.inbox()
    [d] = all_decisions(box)
    assert d["subject"] == "param:wall"
    assert len(d["ids"]) == 2 and d["recommendation"] == "confirm"
    assert "Params.wall" in d["title"]
    # Both facts are visible on the card, not hidden behind the stronger one.
    assert any("Params.wall" in s for s in d["also"]) and d["also"]
    shared = section(box, "param_convention")
    assert shared["label"] == "Shared sizes" and shared["count"] == 1
    assert section(box, "correction_pattern") is None
    assert box["rules"] == 2 and box["decisions"] == 1 and box["ready"] == 1


def test_resolving_a_decision_resolves_every_rule_in_it(env):
    style, clock = env
    for _ in range(2):
        set_param(style, AGENT, "wall", "1.6")
        set_param(style, ALICE, "wall", "2.4")
        clock.t += DAY
    style.distil()
    [d] = all_decisions(style.inbox())
    rules = style.resolve_many(d["ids"], "confirm", "alice")
    assert {r["status"] for r in rules} == {"confirmed"}
    assert style.inbox()["sections"] == [] and style.inbox()["used"]
    assert style.context("enclosure")["text"].count("Params.wall") == 2


def test_undecided_candidates_expire_after_two_weeks_and_can_come_back(env):
    style, clock = env
    set_param(style, ALICE, "lid_gap", "0.3")
    style.distil()
    [rule] = style.rules(("candidate",))
    clock.t += 15 * DAY
    style.distil()
    assert style.rule(rule["id"])["status"] == "expired"
    box = style.inbox()
    assert box["sections"] == [] and box["rules"] == 0
    [gone] = box["history"]
    assert gone["status"] == "expired" and gone["ids"] == [rule["id"]]
    # A person can still make it a rule from the history.
    back = style.resolve_many([rule["id"]], "confirm", "alice")
    assert back[0]["status"] == "confirmed"
    # Or new evidence can bring it back on its own once it doubles.
    style.resolve_many([rule["id"]], "dismiss", "alice")
    for _ in range(2):
        set_param(style, ALICE, "lid_gap", "0.3")
        clock.t += DAY
    style.distil()
    assert style.rule(rule["id"])["status"] != "dismissed"


def test_inbox_sections_counts_and_history(env):
    style, clock = env
    set_param(style, ALICE, "wall", "2.4")
    for i in range(2):
        style.observe_brief({"brief": {"id": f"b_{i}", "text": "case"}, "reviews": [{"round": 1, "findings_json":
                             json.dumps([{"problem": "Lid clips the USB plug"}])}]})
    style.teach("Lids use four M3 inserts", "worker1", proposed=True)
    style.distil()
    box = style.inbox()
    assert box["rules"] == 3 and box["decisions"] == 3
    assert section(box, "recurring_problem")["count"] == 1
    assert section(box, "proposed")["label"] == "Helper proposals"
    [proposal] = section(box, "proposed")["decisions"]
    assert proposal["recommendation"] == "look"
    assert "proposed by worker1" in proposal["detail"]
    assert box["ready"] + box["weak"] + len([d for d in all_decisions(box) if d["recommendation"] == "look"]) == 3


def test_a_used_rule_is_grouped_and_kept_out_of_the_pending_counts(env):
    style, clock = env
    for _ in range(6):
        set_param(style, ALICE, "wall", "2.4")
        clock.t += DAY
    style.distil()
    [rule] = style.rules(("active",))
    style.resolve_many([rule["id"]], "confirm", "alice")
    box = style.inbox()
    assert box["rules"] == 0 and box["sections"] == []
    assert box["ready"] == 0 and box["weak"] == 0  # an answered rule is not a pending recommendation
    [used] = box["used"]
    assert used["status"] == "confirmed" and used["ids"] == [rule["id"]]
    assert "Params.wall" in used["title"]
