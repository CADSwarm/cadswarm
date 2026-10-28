"""SFT rows, critic pairs, eval cases and a dataset that is byte-identical when nothing changed."""
import json

from cadswarm.training import dataset, prompts, render
from cadswarm.training import episodes as episodes_mod


def make_episode(ident, outcome, op_name="create_primitive"):
    step = {"index": 0, "op_id": "op_1", "at_ms": 1, "actor": {"id": 1, "name": "a", "kind": "human"},
            "op": op_name, "args": {"name": "Base", "kind": "box"}, "status": "done",
            "error_code": "", "result": {"result": {}, "created": ["Base"], "modified": [],
                                         "deleted": [], "recomputed": [], "broken": []},
            "state_before": {"objects": {}, "params": {}}, "state_confidence": "full"}
    return {"id": ident, "project": "P", "attribution": "freestyle", "first_ms": 1,
            "request": {"text": "build it", "board": "", "plan_json": {}},
            "segments": [], "steps": [step], "corrections": [],
            "review": None, "feedback": [],
            "final": {"stl_sha256": None, "objects": {}, "params": {}, "surface_quality": None},
            "labels": {"outcome": outcome, "quality": "unreviewed", "step_labels": {"0": "good"}},
            "warnings": []}


def test_the_prompt_is_one_stable_contract():
    assert "create_extrusion" in prompts.SYSTEM and '"op"' in prompts.SYSTEM
    text = prompts.render_request({"text": "make a lid", "plan_json": {}},
                                  {"objects": [], "objects_total": 0, "params": {}}, [])
    assert "make a lid" in text and text == prompts.render_request(
        {"text": "make a lid", "plan_json": {}}, {"objects": [], "objects_total": 0, "params": {}}, [])


def test_sft_rows_skip_bad_and_degraded_steps():
    good, bad = make_episode("p/1", "accepted"), make_episode("p/2", "failed")
    bad["labels"]["step_labels"]["0"] = "bad"
    degraded = make_episode("p/3", "accepted")
    degraded["steps"][0]["state_confidence"] = "degraded"
    rows = render.sft_rows([good, bad, degraded])
    assert len(rows) == 1 and rows[0]["meta"]["episode"] == "p/1"
    assert json.loads(rows[0]["messages"][2]["content"]) == {"op": "create_primitive",
                                                             "args": {"name": "Base", "kind": "box"}}


def test_cases_only_hold_out_the_test_split():
    cases = render.case_rows([make_episode("p/1", "accepted"), make_episode("p/2", "accepted")],
                             {"test": ["p/2"]})
    assert [c["id"] for c in cases] == ["p/2"] and cases[0]["reference"][0]["op"] == "create_primitive"


def test_build_writes_the_dataset_and_the_same_inputs_give_the_same_id(tmp_path):
    raw = tmp_path / "raw" / "p"
    raw.mkdir(parents=True)
    (raw / "member.jsonl").write_text(json.dumps({"id": 1, "name": "alice", "kind": "human"}) + "\n",
                                      encoding="utf-8")
    (raw / "op.jsonl").write_text(json.dumps({
        "id": "op_1", "by": 1, "kind": "create_primitive", "created_ms": 1, "status": "done",
        "error_code": "", "args_json": json.dumps({"name": "Base", "kind": "box"}),
        "result_json": json.dumps({"result": {}, "created": ["Base"], "modified": [], "deleted": [],
                                   "recomputed": [], "broken": []})}) + "\n", encoding="utf-8")
    (raw / "source.json").write_text(json.dumps({"project": "P", "status": "ok"}), encoding="utf-8")
    first = dataset.build(str(tmp_path / "raw"), str(tmp_path / "out"))
    second = dataset.build(str(tmp_path / "raw"), str(tmp_path / "out"))
    root = tmp_path / "out" / first["dataset_id"]
    assert (root / "sft.jsonl").exists() and (root / "dataset-card.md").exists()
    assert (root / "manifest.json").exists()
    assert first["dataset_id"] == second["dataset_id"]
    assert first["counts"]["sft_rows"] == second["counts"]["sft_rows"]
