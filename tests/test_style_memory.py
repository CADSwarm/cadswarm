"""Team design style: what CadSwarm learns from how people and agents use the model, and how it gates it."""
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


def set_param(style, actor, alias, value, ok=True):
    return style.observe_op({"kind": "set_param", "args": {"alias": alias, "value": value, "_settings": {}},
                             "ok": ok, "error_code": "" if ok else "locked", "actor": actor})


def by_type(style, type_):
    return [r for r in style.rules() if r["type"] == type_]


def test_a_value_people_keep_using_becomes_an_active_convention(env):
    style, clock = env
    for i in range(6):
        set_param(style, ALICE if i % 2 else BOB, "wall", "2.4 mm")
        clock.t += DAY
    style.distil()
    [rule] = by_type(style, "param_convention")
    assert rule["status"] == "active" and rule["value"] == {"alias": "wall", "value": 2.4, "unit": "mm"}
    assert "Params.wall is 2.4 mm" in rule["statement"] and rule["evidence"]["people"] == 2


def test_a_one_off_value_stays_a_candidate(env):
    style, _ = env
    set_param(style, ALICE, "lid_gap", "0.3 mm")
    style.distil()
    [rule] = by_type(style, "param_convention")
    assert rule["status"] == "candidate"
    assert style.context("enclosure")["text"] == ""  # candidates never reach agents
    assert style.context()["pending"] == 1


def test_people_correcting_an_agent_is_learned_and_counts_double(env):
    style, clock = env
    for _ in range(3):
        set_param(style, AGENT, "wall", "1.6")
        set_param(style, ALICE, "wall", "2.4")
        clock.t += DAY
    style.distil()
    [rule] = by_type(style, "correction_pattern")
    assert rule["value"] == {"alias": "wall", "value": 2.4} and rule["evidence"]["n"] == 6
    assert rule["status"] == "active" and "from what agents set (1.6) to 2.4" in rule["statement"]
    # The values people replaced are not the team's convention: only 2.4 counts.
    [conv] = by_type(style, "param_convention")
    assert conv["value"]["value"] == 2.4 and conv["evidence"]["consistency"] == 1.0


def test_a_person_editing_an_agents_part_is_a_correction(env):
    style, _ = env
    r = style.observe_op({"kind": "set_props", "args": {"name": "Lid", "props": {"Height": 3}}, "ok": True,
                          "actor": ALICE, "objects": [{"name": "Lid", "type_id": "Part::Box"}],
                          "prior_actors": {"Lid": AGENT}})
    assert r["recorded"] == 2
    # An agent tidying its own part, or a person editing a person's part, is not.
    assert style.observe_op({"kind": "set_props", "args": {"name": "Lid"}, "ok": True, "actor": AGENT,
                             "prior_actors": {"Lid": AGENT}})["recorded"] == 1
    assert style.observe_op({"kind": "set_props", "args": {"name": "Lid"}, "ok": True, "actor": BOB,
                             "prior_actors": {"Lid": ALICE}})["recorded"] == 1
    style.distil()
    [rule] = by_type(style, "correction_pattern")
    assert rule["key"] == "set_props:Part::Box" and "setting Height to 3" in rule["statement"]


def test_queries_and_host_seeding_teach_nothing(env):
    style, _ = env
    assert style.observe_op({"kind": "get_object", "args": {}, "actor": ALICE})["recorded"] == 0
    for _ in range(6):
        set_param(style, {"id": 0, "name": "host", "kind": "host"}, "pcb_l", "51")
    style.distil()
    assert by_type(style, "param_convention") == []


def test_params_that_mirror_team_settings_are_not_style(env):
    style, clock = env
    for i in range(6):
        set_param(style, ALICE if i % 2 else BOB, "tol_clearance", "0.35 mm")
        clock.t += DAY
    style.distil()
    assert by_type(style, "param_convention") == []


def test_preferred_standard_parts(env):
    style, clock = env
    for size in ("M3", "M3", "M3", "M3", "M3", "M2.5"):
        style.observe_op({"kind": "insert_standard_part", "args": {"family": "heat_set_insert", "size": size},
                          "ok": True, "actor": AGENT})
        clock.t += DAY
    style.distil()
    [rule] = by_type(style, "preferred_component")
    assert rule["value"]["choice"] == "M3" and "used 5 of 6" in rule["statement"]


def test_review_problems_group_when_reworded_and_ignore_reviewer_failures(env):
    style, _ = env
    for i, problem in enumerate(["Lid clips the USB plug by 1.2 mm", "lid clips the usb plug by 0.4 mm",
                                 "the reviewer did not report a verdict"]):
        style.observe_brief({"brief": {"id": f"b_{i}", "text": "case", "board": "pico"},
                             "reviews": [{"round": 1, "passed": False,
                                          "findings_json": json.dumps([{"object": "Lid", "problem": problem}])}]})
    style.distil()
    [rule] = by_type(style, "recurring_problem")
    assert rule["scope"] == "board:pico" and rule["evidence"]["n"] == 4
    assert "reviewer" not in rule["statement"]


def test_dismiss_is_sticky_until_the_evidence_doubles(env):
    style, clock = env
    for _ in range(6):
        set_param(style, ALICE, "wall", "2.4")
        clock.t += DAY
    style.distil()
    [rule] = by_type(style, "param_convention")
    style.resolve(rule["id"], "dismiss", "alice")
    for _ in range(3):
        set_param(style, ALICE, "wall", "2.4")
        clock.t += DAY
    style.distil()
    assert style.rule(rule["id"])["status"] == "dismissed"
    for _ in range(4):
        set_param(style, ALICE, "wall", "2.4")
        clock.t += DAY
    style.distil()
    assert style.rule(rule["id"])["status"] == "active"


def test_confirm_pins_a_rule_and_taught_rules_lead_the_context(env):
    style, clock = env
    set_param(style, ALICE, "lid_gap", "0.3")
    style.distil()
    [rule] = by_type(style, "param_convention")
    style.resolve(rule["id"], "confirm", "alice")
    clock.t += 400 * DAY  # long gone stale, still confirmed
    style.distil()
    assert style.rule(rule["id"])["status"] == "confirmed"
    taught = style.teach("All fasteners are M3 with heat-set inserts.", "bob")
    assert taught["status"] == "confirmed" and taught["source"] == "taught"
    ctx = style.context("screw the lid down")
    assert ctx["text"].startswith("Team design style") and "[confirmed] All fasteners are M3" in ctx["text"]
    with pytest.raises(style_memory.StyleError):
        style.resolve(rule["id"], "maybe", "bob")
    with pytest.raises(style_memory.StyleError):
        style.teach("  ", "bob")


def test_teaching_the_same_rule_twice_keeps_one(env):
    style, _ = env
    first = style.teach("Lids are held by four M3 heat-set inserts.", "alice")
    assert style.teach("lids are held by four M3 heat-set inserts", "bob")["id"] == first["id"]
    style.resolve(first["id"], "dismiss", "bob")
    assert style.teach("Lids are held by four M3 heat-set inserts.", "worker1", proposed=True)["status"] == "dismissed"
    assert style.teach("Lids are held by four M3 heat-set inserts.", "alice")["status"] == "confirmed"
    assert style.context()["text"].count("Lids are held") == 1


def test_stale_learned_rules_decay_back_to_candidates(env):
    style, clock = env
    for _ in range(6):
        set_param(style, ALICE, "wall", "2.4")
        clock.t += DAY
    style.distil()
    [rule] = by_type(style, "param_convention")
    assert rule["status"] == "active"
    clock.t += 90 * DAY
    style.distil()
    assert style.rule(rule["id"])["status"] == "candidate"


def test_a_candidate_nothing_supports_any_more_goes_away(env):
    style, _ = env
    set_param(style, ALICE, "lid_gap", "0.3")
    style.distil()
    [rule] = by_type(style, "param_convention")
    style.db.execute("DELETE FROM style_events")  # its evidence is gone (e.g. the rule logic changed)
    style.distil()
    assert style.rule(rule["id"]) is None


def test_the_context_keeps_other_boards_out(env):
    style, _ = env
    style.observe_brief({"brief": {"id": "b_1", "board": "pico"}, "reviews": [{"round": 1, "findings_json":
                         json.dumps([{"problem": "standoffs too short"}])}]})
    style.observe_brief({"brief": {"id": "b_2", "board": "pico"}, "reviews": [{"round": 1, "findings_json":
                         json.dumps([{"problem": "standoffs too short"}])}]})
    style.distil()
    [rule] = by_type(style, "recurring_problem")
    style.resolve(rule["id"], "confirm", "alice")
    assert "standoffs" in style.context("case", "pico")["text"]
    assert style.context("case", "esp32")["text"] == ""


def test_distil_runs_after_enough_new_events(env):
    style, _ = env
    for _ in range(style_memory.DISTIL_EVERY - 1):
        set_param(style, ALICE, "wall", "2")
    assert not style.due()
    set_param(style, ALICE, "wall", "2")
    assert style.due()
    style.distil()
    assert not style.due()


def test_failed_ops_that_keep_failing_become_a_problem(env):
    style, _ = env
    for _ in range(3):
        style.observe_op({"kind": "boolean", "args": {"base": "A", "tool": "B"}, "ok": False,
                          "error_code": "recompute_error", "actor": AGENT})
    style.distil()
    [rule] = by_type(style, "recurring_problem")
    assert "boolean keeps failing with recompute_error" in rule["statement"]
