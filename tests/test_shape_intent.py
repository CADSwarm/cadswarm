"""Shape intent: a car is judged on form, a bracket on function."""
from cadswarm.shape_intent import assess, classify, required


def test_a_car_body_is_aesthetic_and_a_table_is_mechanical():
    assert classify("model a 2021 Lexus IS 350 F Sport exterior")["intent"] == "aesthetic"
    assert classify("a workbench table for the shop")["intent"] == "mechanical"


def test_a_part_name_is_evidence_and_a_tie_defaults_to_mechanical():
    assert classify("", names=["BodyShell", "Roof"])["intent"] == "aesthetic"
    assert classify("a frame")["intent"] == "mechanical"
    assert classify("")["intent"] == "mechanical"
    assert classify("")["required"] is None


def test_the_stronger_vocabulary_wins_and_reports_what_matched():
    got = classify("a car bracket for the bumper")
    assert got["intent"] == "aesthetic"
    assert "car" in got["matched"] and "bumper" in got["matched"]
    assert classify("a table bracket")["intent"] == "mechanical"


def test_the_ceiling_scales_with_the_face_count():
    assert required("mechanical", 200) is None
    assert required("aesthetic", 0) == {"max_planar_share": 0.6, "min_curved_faces": 2}
    assert required("aesthetic", 400)["min_curved_faces"] == 40
    # A six-face glass panel with four curved faces is fine; the floor must not demand a car body.
    assert required("aesthetic", 6)["min_curved_faces"] == 2


def test_a_box_fails_an_aesthetic_bar_and_passes_a_mechanical_one():
    box = {"faces": 6, "planar": 6, "curved": 0, "valid": True}
    bad = assess(box, "aesthetic")
    assert bad["verdict"] == "fails" and bad["planar_share"] == 1.0
    assert "block model" in bad["advice"][0]
    assert assess(box, "mechanical")["verdict"] == "not required"


def test_a_lofted_shell_passes_and_an_invalid_solid_does_not():
    shell = {"faces": 120, "planar": 20, "curved": 100, "valid": True}
    assert assess(shell, "aesthetic")["verdict"] == "pass"
    broken = {"faces": 120, "planar": 20, "curved": 100, "valid": False}
    assert assess(broken, "aesthetic")["verdict"] == "fails"
