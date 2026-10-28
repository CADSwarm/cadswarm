"""SFT rows, critic pairs, eval cases and a dataset that is byte-identical when nothing changed."""
import json

from cadswarm.training import dataset, prompts, render


def make_episode(ident, outcome, op_name="create_primitive", project="P", first_ms=1):
    step = {"index": 0, "op_id": "op_1", "at_ms": first_ms,
            "actor": {"id": 1, "name": "a", "kind": "human"},
            "op": op_name, "args": {"name": "Base", "kind": "box"}, "status": "done",
            "error_code": "", "result": {"result": {}, "created": ["Base"], "modified": [],
                                         "deleted": [], "recomputed": [], "broken": []},
            "state_before": {"objects": {}, "params": {}}, "state_confidence": "full"}
    return {"id": ident, "project": project, "attribution": "freestyle", "first_ms": first_ms,
            "request": {"text": "build it", "board": "", "plan_json": {}},
            "segments": [], "steps": [step], "corrections": [],
            "review": None, "feedback": [],
            "final": {"stl_sha256": None, "objects": {}, "params": {}, "surface_quality": None},
            "labels": {"outcome": outcome, "quality": "unreviewed", "step_labels": {"0": "good"}},
            "warnings": []}


def test_the_prompt_names_every_op_that_real_logs_contain():
    for op in ("create_extrusion", "boolean", "set_param", "run_part_script",
               "insert_component", "insert_standard_part", "insert_mesh"):
        assert op in prompts.SYSTEM


def test_the_prompt_is_bounded_and_deterministic():
    request = {"text": "x" * 9000, "plan_json": {"note": "y" * 9000}}
    summary = {"objects": [], "objects_total": 0,
               "params": {f"p{i:03d}": "1" for i in range(200)}}
    text = prompts.render_request(request, summary, [])
    assert len(text) < 9000 and "params_total" in text
    assert text == prompts.render_request(request, summary, [])


def test_sft_rows_skip_bad_and_degraded_steps():
    good, bad = make_episode("p/1", "accepted"), make_episode("p/2", "failed")
    bad["labels"]["step_labels"]["0"] = "bad"
    degraded = make_episode("p/3", "accepted")
    degraded["steps"][0]["state_confidence"] = "degraded"
    rows = render.sft_rows([good, bad, degraded], ["p/1", "p/2", "p/3"])
    assert len(rows) == 1 and rows[0]["meta"]["episode"] == "p/1"
    assert json.loads(rows[0]["messages"][2]["content"]) == {"op": "create_primitive",
                                                             "args": {"name": "Base", "kind": "box"}}


def test_held_out_episodes_never_become_training_rows():
    train, held_out = make_episode("p/1", "accepted"), make_episode("p/2", "accepted")
    held_out["corrections"] = [{"after_index": 0, "alias": "wall", "before": 3.0, "after": 2.4}]
    assert [r["meta"]["episode"] for r in render.sft_rows([train, held_out], ["p/1"])] == ["p/1"]
    assert render.pair_rows([train, held_out], ["p/1"]) == []


def test_cases_only_hold_out_the_test_split():
    cases = render.case_rows([make_episode("p/1", "accepted"), make_episode("p/2", "accepted")],
                             {"test": ["p/2"]})
    assert [c["id"] for c in cases] == ["p/2"] and cases[0]["reference"][0]["op"] == "create_primitive"


def write_project(raw, name, source, ops=()):
    directory = raw / name
    directory.mkdir(parents=True)
    (directory / "source.json").write_text(json.dumps(source), encoding="utf-8")
    (directory / "member.jsonl").write_text(
        json.dumps({"id": 1, "name": "alice", "kind": "human"}) + "\n", encoding="utf-8")
    rows = [{"id": "op_1", "by": 1, "kind": "create_primitive", "created_ms": 1, "status": "done",
             "error_code": "", "args_json": json.dumps({"name": "Base", "kind": "box"}),
             "result_json": json.dumps({"result": {}, "created": ["Base"], "modified": [],
                                        "deleted": [], "recomputed": [], "broken": []})}]
    (directory / "op.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in list(rows) + list(ops)), encoding="utf-8")


def test_build_writes_the_dataset_and_the_same_inputs_give_the_same_id(tmp_path):
    write_project(tmp_path / "raw", "p", {"project": "P", "status": "ok", "schema_sha256": "abc"})
    first = dataset.build(str(tmp_path / "raw"), str(tmp_path / "out"))
    second = dataset.build(str(tmp_path / "raw"), str(tmp_path / "out"))
    root = tmp_path / "out" / first["dataset_id"]
    assert (root / "sft.jsonl").exists() and (root / "dataset-card.md").exists()
    assert (root / "manifest.json").exists()
    assert first["dataset_id"] == second["dataset_id"]
    assert first["counts"]["sft_rows"] == second["counts"]["sft_rows"]


def test_an_unreadable_or_skipped_raw_project_is_recorded_not_fatal(tmp_path):
    raw = tmp_path / "raw"
    write_project(raw, "ok", {"project": "P", "status": "ok", "schema_sha256": "abc"})
    (raw / "junk").mkdir()
    write_project(raw, "paused", {"project": "Q", "status": "skipped: 503"})
    manifest = dataset.build(str(raw), str(tmp_path / "out"))
    assert manifest["counts"]["episodes"] == 1
    assert {item["project"] for item in manifest["skipped"]} == {"junk", "Q"}


def test_leftover_export_directories_are_ignored(tmp_path):
    raw = tmp_path / "raw"
    write_project(raw, "ok", {"project": "P", "status": "ok", "schema_sha256": "abc"})
    write_project(raw, "ok.old", {"project": "P", "status": "ok", "schema_sha256": "abc"})
    write_project(raw, "ok.new", {"project": "P", "status": "ok", "schema_sha256": "abc"})
    manifest = dataset.build(str(raw), str(tmp_path / "out"))
    assert manifest["counts"]["episodes"] == 1 and manifest["projects"]["P"]["episodes"] == 1
