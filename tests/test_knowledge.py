"""The project's own knowledge: board docs, finished designs and simulations, all in SQLite."""
import json

import pytest

from cadswarm.knowledge import (Knowledge, KnowledgeError, design_doc, design_problems, fea_doc,
                                review_findings, validate_spec)


def store(tmp_path):
    return Knowledge(tmp_path / "internal" / "knowledge.db", root=tmp_path)


def design(brief_id="b_1", board="raspberry-pi-pico", problems=(), text="case for a pico", at=1000):
    reviews = [{"round": 1, "passed": not problems,
                "findings_json": json.dumps([{"object": "Base", "problem": p} for p in problems])}]
    doc = design_doc({"id": brief_id, "text": text, "board": board, "status": "ready"},
                     {"parts": [{"name": "Base"}]}, reviews, {"wall": 2.0})
    doc["finished_at"] = at
    return doc


@pytest.fixture
def library(tmp_path, monkeypatch):
    folder = tmp_path / "boards"
    folder.mkdir()
    (folder / "boards.json").write_text(json.dumps({
        "raspberry-pi-pico": {"label": "Raspberry Pi Pico", "pcb": [51.0, 21.0, 1.0],
                              "holes": [[2.0, 4.8], [49.0, 4.8]], "hole_d": 2.1,
                              "clear_top": 4.0, "clear_bottom": 2.0,
                              "ports": [{"name": "usb", "side": "-x", "center": [10.5, 1.4],
                                         "size": [8.5, 3.2]}]},
        "esp32-s3-devkitc-1": {"label": "ESP32-S3-DevKitC-1", "pcb": [62.7, 25.4, 1.6],
                               "holes": [], "hole_d": 0, "clear_top": 9.0, "clear_bottom": 3.0},
    }), encoding="utf-8")
    (folder / "sources.json").write_text(json.dumps([
        {"board_id": "raspberry-pi-pico", "title": "Pico datasheet", "type": "datasheet",
         "url": "https://d/pico.pdf"}]), encoding="utf-8")
    monkeypatch.setenv("CADSWARM_BOARDS", str(folder / "boards.json"))
    return folder


def test_a_finished_design_is_searchable_and_filterable(tmp_path):
    k = store(tmp_path)
    k.index_design(design(problems=["Lid clips the USB plug"], at=1000))
    k.index_design(design("b_2", board="esp32-s3-devkitc-1", text="case for an esp32", at=2000))
    hits = k.search("cadswarm-designs", "usb plug", 5)
    assert hits[0]["source"]["brief_id"] == "b_1" and "USB plug" in hits[0]["chunks"][0]
    assert [h["source"]["brief_id"] for h in
            k.search("cadswarm-designs", "case", 5, {"board_id": "esp32-s3-devkitc-1"})] == ["b_2"]
    assert k.search("cadswarm-designs", "case", 5, {"board_id": "no-such-board"}) == []


def test_reindexing_a_brief_updates_it_in_place(tmp_path):
    k = store(tmp_path)
    k.index_design(design(problems=["lid too thin"]))
    k.index_design(design(problems=["lid too heavy"]))
    hits = k.search("cadswarm-designs", "case")
    assert len(hits) == 1 and hits[0]["source"]["problems"] == ["lid too heavy"]


def test_lessons_are_the_recurring_problems_for_that_board(tmp_path):
    k = store(tmp_path)
    k.index_design(design("b_1", problems=["clips usb plug"]))
    k.index_design(design("b_2", problems=["clips usb plug", "lid wall 1.0 mm"]))
    k.index_design(design("b_3", board="esp32-s3-devkitc-1", problems=["clips usb plug"]))
    lessons = k.lessons("raspberry-pi-pico", 5)
    assert lessons[0]["problem"] == "clips usb plug" and lessons[0]["count"] == 2
    assert "Seen 2 time(s) on raspberry-pi-pico" in lessons[0]["lesson"]
    assert [x["problem"] for x in lessons] == ["clips usb plug", "lid wall 1.0 mm"]


def test_reviewer_failures_are_not_lessons(tmp_path):
    k = store(tmp_path)
    k.index_design(design(problems=["the reviewer did not report a verdict"]))
    assert k.lessons("raspberry-pi-pico", 5) == []


def test_a_simulation_is_searchable_and_teaches_its_problems(tmp_path):
    k = store(tmp_path)
    k.index_fea(fea_doc({
        "id": "fr_1", "title": "Lid load", "analysis": "static", "verdict": "warning",
        "headline": "The lid bends 1.2 mm", "summary": "Ribs are too shallow", "published_at": 1_700_000_000,
        "inputs": {"material": {"name": "PLA"}}, "model": {"target": ["Lid"]},
        "results": {"safety_factor": 1.4, "max_von_mises_mpa": 31.0, "max_displacement_mm": 1.2},
        "improvements": [{"severity": "warning", "text": "thicken the lid ribs"}]}))
    [hit] = k.search("cadswarm-fea", "lid ribs")
    assert hit["source"]["report_id"] == "fr_1" and hit["source"]["verdict"] == "warning"
    assert hit["source"]["problems"] == ["thicken the lid ribs"]


def test_a_long_datasheet_answers_with_the_passage_not_its_header(tmp_path):
    k = store(tmp_path)
    text = "XIAO ESP32C3 datasheet. " * 200 + "The mounting holes are 47 x 11.4 mm apart. " + "footer " * 100
    k.index_board({"doc_id": "doc:x", "board_id": "xiao", "title": "XIAO datasheet",
                   "source_url": "https://d/xiao.pdf", "text": text})
    [hit] = k.search("cadswarm-boards", "mounting holes", 5, {"board_id": "xiao"})
    assert "47 x 11.4" in hit["chunks"][0] and len(hit["chunks"][0]) <= 1500


def test_the_library_seeds_searchable_board_docs(tmp_path, library):
    k = store(tmp_path)  # nothing ingested: the first open seeds the library
    hits = k.search("cadswarm-boards", "mounting holes", 5, {"board_id": "raspberry-pi-pico"})
    assert [h["source"]["board_id"] for h in hits] == ["raspberry-pi-pico"]
    assert "51.0 x 21.0" in hits[0]["chunks"][0]
    assert hits[0]["source"]["source_url"] == "https://d/pico.pdf"
    k.close()
    k2 = store(tmp_path)  # seeded once, not appended on every open
    assert len(k2.search("cadswarm-boards", "esp32", 10, {"board_id": "esp32-s3-devkitc-1"})) == 1


def test_board_spec_comes_from_the_library_with_its_datasheet_links(tmp_path, library):
    spec = store(tmp_path).board_spec("raspberry-pi-pico")
    assert spec["label"] == "Raspberry Pi Pico" and spec["pcb"] == [51.0, 21.0, 1.0]
    assert spec["sources"]["pcb"] == spec["sources"]["holes"] == "https://d/pico.pdf"


def test_an_unknown_board_lists_what_the_library_does_have(tmp_path, library):
    with pytest.raises(KnowledgeError) as err:
        store(tmp_path).board_spec("mystery-board")
    assert err.value.code == "unknown_board" and "raspberry-pi-pico" in str(err.value)


def test_an_unreadable_library_says_which_file_is_broken(tmp_path, library):
    (library / "boards.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(KnowledgeError) as err:
        store(tmp_path).board_spec("raspberry-pi-pico")
    assert err.value.code == "library_unreadable" and "boards.json" in str(err.value)


def test_a_design_doc_summarises_the_brief_and_its_reviews():
    d = design_doc({"id": "b_1", "text": "case for a pico", "board": "raspberry-pi-pico", "status": "ready"},
                   {"parts": [{"name": "Base"}, {"name": "Lid"}]},
                   [{"round": 1, "passed": False, "findings_json": '[{"object":"Lid","problem":"clips usb plug"}]'},
                    {"round": 2, "passed": True, "findings_json": "[]"}],
                   {"wall": 2.0, "gap": 0.5})
    assert d["brief_id"] == "b_1" and d["outcome"] == "ready" and d["problems"] == ["clips usb plug"]
    assert "clips usb plug" in d["text"] and "Base" in d["text"] and d["params"]["gap"] == 0.5


def test_a_no_verdict_finding_is_about_the_reviewer_not_the_design():
    # The round the reviewer crashed in says nothing about the model; it must not become a lesson.
    d = design_doc({"id": "b_1", "text": "case", "board": "raspberry-pi-pico", "status": "ready"},
                   {"parts": [{"name": "Base"}]},
                   [{"round": 1, "passed": False,
                     "findings_json": '[{"object":"","problem":"the reviewer did not report a verdict"}]'},
                    {"round": 2, "passed": False,
                     "findings_json": '[{"object":"Base","problem":"the wall is 1.1 mm"}]'}],
                   {})
    assert d["problems"] == ["the wall is 1.1 mm"]
    assert "did not report a verdict" not in d["text"]
    assert design_problems([{"round": 1, "findings_json": '[{"problem": "the reviewer exited without a verdict"}]'}]) == []


def test_a_retried_review_replaces_its_earlier_attempt():
    reviews = [{"round": 3, "passed": False, "at_ms": 10,
                "findings_json": '[{"object":"Base","problem":"the old wording of the clash"}]'},
               {"round": 3, "passed": False, "at_ms": 20,
                "findings_json": '[{"object":"Base","problem":"the new wording of the clash"}]'}]
    assert [f["problem"] for f in review_findings(reviews)] == ["the new wording of the clash"]


def good():
    return {"label": "Pico", "pcb": [51, 21, 1], "holes": [[2, 4.8], [49, 4.8]], "hole_d": 2.1,
            "clear_top": 4, "clear_bottom": 2,
            "ports": [{"name": "usb", "side": "-x", "center": [10.5, 1.4], "size": [8.5, 3.2]}],
            "sources": {"pcb": "https://d/pico.pdf", "holes": "https://d/pico.pdf", "ports": "https://d/pico.pdf"}}


def test_a_sourced_spec_passes_and_keeps_its_sources():
    s = validate_spec(good())
    assert s["pcb"] == [51.0, 21.0, 1.0] and s["sources"]["holes"] == "https://d/pico.pdf"
    assert s["verified"] is False


def test_unsourced_or_implausible_specs_are_refused():
    bad = good(); bad["sources"].pop("pcb")
    with pytest.raises(ValueError, match="pcb"):
        validate_spec(bad)
    bad = good(); bad["pcb"] = [5000, 21, 1]
    with pytest.raises(ValueError, match="pcb"):
        validate_spec(bad)
    bad = good(); bad["holes"] = [[80, 4]]
    with pytest.raises(ValueError, match="hole"):
        validate_spec(bad)
    bad = good(); bad["hole_d"] = 0  # holes without a bore would seed solid standoffs
    with pytest.raises(ValueError, match="hole_d"):
        validate_spec(bad)
    bad = good(); bad["ports"] = [{"side": "-x"}]  # a malformed port is a refusal, not a crash
    with pytest.raises(ValueError, match="port"):
        validate_spec(bad)
