from types import SimpleNamespace

from cadswarm.reviewer import REVIEW_TOOLS, activity_for, review_prompt


def test_prompt_names_the_brief_the_board_the_bed_and_the_round():
    p = review_prompt({"id": "b_1", "text": "case for an ESP32"}, "ESP32-S3-DevKitC-1", [180, 180, 180], 2)
    assert "b_1" in p and "ESP32-S3-DevKitC-1" in p and "180" in p and "round 2" in p
    assert "report_review" in p


def test_tool_events_become_activity_with_targets():
    call = SimpleNamespace(name="check_interference", input={"names": ["Lid", "Base"]})
    kind, summary, targets = activity_for(call)
    assert kind == "tool_call" and "check_interference" in summary and targets == ["Lid", "Base"]


def test_the_reviewer_cannot_reach_geometry_tools():
    assert not any(t.endswith(("create_primitive", "set_props", "run_part_script", "delete_object", "lock_objects"))
                   for t in REVIEW_TOOLS)


def test_the_review_checks_the_team_style_when_there_is_one():
    brief = {"id": "b_1", "text": "case"}
    assert "Team design style" not in review_prompt(brief, "Pico", [180, 180, 180], 1)
    p = review_prompt(brief, "Pico", [180, 180, 180], 1, "Team design style:\n- [confirmed] Walls are 2.4 mm")
    assert "5. Team design style" in p and "Walls are 2.4 mm" in p and '"style: <the rule>' in p
    assert p.index("Walls are 2.4 mm") < p.index("report_review")
