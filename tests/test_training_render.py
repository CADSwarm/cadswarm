"""SFT rows, critic pairs, eval cases and a dataset that is byte-identical when nothing changed."""
import json
import os

from cadswarm.training import dataset, prompts, render


def make_step(index, op_name="create_primitive", args=None, status="done", created=("Base",)):
    return {"index": index, "op_id": f"op_{index + 1}", "at_ms": 1 + index,
            "actor": {"id": 1, "name": "a", "kind": "human"},
            "op": op_name, "args": {"name": "Base", "kind": "box"} if args is None else args,
            "status": status, "error_code": "",
            "result": {"result": {}, "created": list(created), "modified": [],
                       "deleted": [], "recomputed": [], "broken": []},
            "state_before": {"objects": {}, "params": {}}, "state_confidence": "full"}


def make_episode(ident, outcome, op_name="create_primitive", project="P", first_ms=1,
                 steps=None, objects=None):
    steps = steps or [make_step(0, op_name)]
    objects = {"Base": {"bbox": None, "volume": 1.0}} if objects is None else objects
    return {"id": ident, "project": project, "attribution": "freestyle", "first_ms": first_ms,
            "request": {"text": "build it", "board": "", "plan_json": {}},
            "segments": [], "steps": steps, "corrections": [],
            "review": None, "feedback": [],
            "final": {"stl_sha256": None, "objects": objects, "params": {},
                      "surface_quality": None},
            "labels": {"outcome": outcome, "quality": "unreviewed",
                       "step_labels": {str(s["index"]): "good" for s in steps}},
            "warnings": []}


def test_the_prompt_names_every_op_that_real_logs_contain():
    for op in ("create_extrusion", "boolean", "set_param", "run_part_script",
               "insert_component", "insert_standard_part", "insert_mesh"):
        assert op in prompts.SYSTEM


def test_every_build_op_is_the_prompt_contract():
    assert len(prompts.BUILD_OPS) == 13
    for op in prompts.BUILD_OPS:
        assert op in prompts.SYSTEM
    for op in ("brep", "style_inbox", "get_object", "surface_quality", "add_reference"):
        assert op not in prompts.BUILD_OPS and op not in prompts.SYSTEM


def test_the_prompt_is_bounded_and_deterministic():
    request = {"text": "x" * 9000, "plan_json": {"note": "y" * 9000}}
    summary = {"objects": [], "objects_total": 0,
               "params": {f"p{i:03d}": "1" for i in range(200)}}
    previous = [{"op": "run_part_script", "args": {"code": "z" * 50_000}}] * 8
    text = prompts.render_request(request, summary, previous)
    assert len(text) < 9000 and "params_total" in text
    assert text == prompts.render_request(request, summary, previous)


def test_sft_rows_skip_bad_and_degraded_steps():
    good, bad = make_episode("p/1", "accepted"), make_episode("p/2", "failed")
    bad["labels"]["step_labels"]["0"] = "bad"
    degraded = make_episode("p/3", "accepted")
    degraded["steps"][0]["state_confidence"] = "degraded"
    rows = render.sft_rows([good, bad, degraded], ["p/1", "p/2", "p/3"])
    assert len(rows) == 1 and rows[0]["meta"]["episode"] == "p/1"
    assert json.loads(rows[0]["messages"][2]["content"]) == {"op": "create_primitive",
                                                             "args": {"name": "Base", "kind": "box"}}


def test_sft_rows_only_contain_ops_the_prompt_defines():
    steps = [make_step(0, "brep", {"name": "Base"}, created=()),
             make_step(1, "create_primitive"),
             make_step(2, "run_part_script", {"name": "Script", "code": "x" * 9000},
                       created=["Script"])]
    dropped = []
    rows = render.sft_rows([make_episode("p/1", "accepted", steps=steps)], ["p/1"],
                           dropped=dropped)
    assert [json.loads(row["messages"][2]["content"])["op"] for row in rows] == ["create_primitive"]
    for row in rows:
        op = json.loads(row["messages"][2]["content"])["op"]
        assert op in prompts.SYSTEM
        assert len(row["messages"][2]["content"]) <= prompts.MAX_TARGET
    assert {item["code"] for item in dropped} == {"non_build_op", "target_too_long"}


def test_held_out_episodes_never_become_training_rows():
    train, held_out = make_episode("p/1", "accepted"), make_episode("p/2", "accepted")
    held_out["corrections"] = [{"after_index": 0, "alias": "wall", "before": 3.0, "after": 2.4}]
    assert [r["meta"]["episode"] for r in render.sft_rows([train, held_out], ["p/1"])] == ["p/1"]
    assert render.pair_rows([train, held_out], ["p/1"]) == []


def test_cases_only_hold_out_the_test_split():
    cases = render.case_rows([make_episode("p/1", "accepted"), make_episode("p/2", "accepted")],
                             {"test": ["p/2"]})
    assert [c["id"] for c in cases] == ["p/2"] and cases[0]["reference"][0]["op"] == "create_primitive"


def test_cases_exclude_ops_the_worker_cannot_run_and_cases_without_objects():
    query = make_episode("p/1", "accepted",
                         steps=[make_step(0, "brep", {"name": "Base"}, created=())])
    mixed = make_episode("p/2", "accepted",
                         steps=[make_step(0, "brep", {"name": "Base"}, created=()),
                                make_step(1, "create_primitive")])
    empty = make_episode("p/3", "accepted", objects={})
    dropped = []
    cases = render.case_rows([query, mixed, empty], {"test": ["p/1", "p/2", "p/3"]},
                             dropped=dropped)
    assert [c["id"] for c in cases] == ["p/2"]
    assert [op["op"] for op in cases[0]["reference"]] == ["create_primitive"]
    assert cases[0]["state"] == mixed["steps"][0]["state_before"]
    assert {d["id"]: d["code"] for d in dropped} == {"p/1": "no_build_op", "p/3": "no_object"}


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
    assert first["drops"]["sft"] == {} and "sft_dropped" in first["counts"]


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
    assert {"ok.old", "ok.new"} <= {item["project"] for item in manifest["skipped"]}


def test_one_broken_project_does_not_stop_the_build(tmp_path):
    raw = tmp_path / "raw"
    write_project(raw, "ok", {"project": "P", "status": "ok", "schema_sha256": "abc"})
    write_project(raw, "broken", {"project": "Q", "status": "ok", "schema_sha256": "abc"})
    (raw / "broken" / "task.jsonl").write_text(json.dumps({"title": "no id"}) + "\n",
                                               encoding="utf-8")
    manifest = dataset.build(str(raw), str(tmp_path / "out"))
    assert manifest["counts"]["episodes"] == 1 and "P" in manifest["projects"]
    assert any(item["project"] == "Q" and "unusable" in item["reason"]
               for item in manifest["skipped"])


def test_the_dataset_is_published_in_one_step(tmp_path, monkeypatch):
    write_project(tmp_path / "raw", "p", {"project": "P", "status": "ok", "schema_sha256": "abc"})
    calls = []
    real = dataset.os.replace

    def tracked(src, dst):
        calls.append((src, dst))
        real(src, dst)

    monkeypatch.setattr(dataset.os, "replace", tracked)
    manifest = dataset.build(str(tmp_path / "raw"), str(tmp_path / "out"))
    assert len(calls) == 1
    src, dst = calls[0]
    assert os.path.basename(src).startswith(".build-") and dst.endswith(manifest["dataset_id"])
    assert not [name for name in os.listdir(tmp_path / "out") if name.startswith(".build-")]
    root = tmp_path / "out" / manifest["dataset_id"]
    assert (root / "manifest.json").exists() and (root / "dataset-card.md").exists()


def test_source_warnings_reach_the_manifest_and_the_card(tmp_path):
    write_project(tmp_path / "raw", "p", {"project": "P", "status": "ok", "schema_sha256": "abc",
                                          "warnings": ["op rows were swept"]})
    manifest = dataset.build(str(tmp_path / "raw"), str(tmp_path / "out"))
    assert manifest["raw"]["P"]["warnings"] == ["op rows were swept"]
    card = (tmp_path / "out" / manifest["dataset_id"] / "dataset-card.md").read_text(
        encoding="utf-8")
    assert "op rows were swept" in card
