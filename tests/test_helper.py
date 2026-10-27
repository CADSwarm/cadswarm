import json
import os

import pytest

from cadswarm import helper
from cadswarm.knowledge import KnowledgeError, design_doc


def project(tmp_path):
    return helper.Project(tmp_path)


def seed_board(p, text="The mounting holes are 47 x 11.4 mm apart.", board="raspberry-pi-pico",
               url="https://d/p.pdf", title="Pico datasheet"):
    p.knowledge.index_board({"doc_id": f"doc:{board}", "board_id": board, "title": title,
                             "source_url": url, "source_type": "datasheet", "text": text, "content": text})


def seed_design(p, brief_id="b_1", request="case for a pico", board="raspberry-pi-pico", problems=(), at=1000):
    reviews = [{"round": 1, "passed": not problems,
                "findings_json": json.dumps([{"object": "Base", "problem": x} for x in problems])}]
    doc = design_doc({"id": brief_id, "text": request, "board": board, "status": "ready"},
                     {"parts": [{"name": "Base"}]}, reviews, {"wall": 2.0})
    doc["finished_at"] = at
    p.knowledge.index_design(doc)
    return doc


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A copy of the local board library, so specs come from the project's own files, not a service."""
    folder = tmp_path / "lib"
    folder.mkdir()
    (folder / "boards.json").write_text(json.dumps({
        "raspberry-pi-pico": {"label": "Raspberry Pi Pico", "pcb": [51.0, 21.0, 1.0],
                              "holes": [[2.0, 4.8], [49.0, 4.8]], "hole_d": 2.1,
                              "clear_top": 4.0, "clear_bottom": 2.0, "verified": True,
                              "ports": [{"name": "usb", "side": "-x", "center": [10.5, 1.4],
                                         "size": [8.5, 3.2]}]},
    }), encoding="utf-8")
    (folder / "sources.json").write_text(json.dumps([
        {"board_id": "raspberry-pi-pico", "title": "Pico datasheet", "type": "datasheet",
         "url": "https://d/pico.pdf"}]), encoding="utf-8")
    monkeypatch.setenv("CADSWARM_BOARDS", str(folder / "boards.json"))
    return folder


def test_lookup_returns_passages_with_their_sources(tmp_path):
    p = project(tmp_path)
    seed_board(p)
    r = helper.handle(p, {"id": 1, "op": "lookup_board", "args": {"question": "pico holes"}})
    assert r["ok"]
    answer = next(a for a in r["result"]["answers"] if a["source_url"] == "https://d/p.pdf")
    assert "47 x 11.4" in answer["text"]


def test_a_board_alone_is_enough_to_look_up_or_recall(tmp_path):
    p = project(tmp_path)
    seed_board(p)
    seed_design(p)
    assert helper.handle(p, {"id": 1, "op": "lookup_board", "args": {"question": "", "board": "raspberry-pi-pico"}})["ok"]
    assert helper.handle(p, {"id": 2, "op": "recall_designs", "args": {"query": "", "board": "raspberry-pi-pico"}})["ok"]
    assert helper.handle(p, {"id": 3, "op": "lookup_board", "args": {"question": "", "board": ""}})["code"] == "bad_args"


def test_unknown_ops_and_failures_are_errors_not_crashes(tmp_path):
    assert helper.handle(project(tmp_path), {"id": 1, "op": "nope", "args": {}})["code"] == "unknown_op"

    class Broken:
        def search(self, *a, **k):
            raise RuntimeError("down")

    p = project(tmp_path)
    p.knowledge = Broken()
    r = helper.handle(p, {"id": 2, "op": "recall_designs", "args": {"query": "x"}})
    assert r["ok"] is False and "down" in r["error"]


def test_index_design_builds_the_document(tmp_path):
    p = project(tmp_path)
    r = helper.handle(p, {"id": 1, "op": "index_design", "args": {
        "brief": {"id": "b_1", "text": "case", "board": "raspberry-pi-pico", "status": "ready"},
        "plan": {"parts": []}, "reviews": [], "params": {"wall": 2.0}}})
    assert r["ok"] and r["result"]["indexed"] == "b_1"
    [hit] = p.knowledge.search("cadswarm-designs", "case")
    assert hit["source"]["brief_id"] == "b_1"


def test_recall_designs_filters_by_board_and_reads_lessons(tmp_path):
    p = project(tmp_path)
    seed_design(p, "b_1", problems=["clips usb plug"])
    seed_design(p, "b_2", board="esp32-s3-devkitc-1", request="case for an esp32")
    r = helper.handle(p, {"id": 1, "op": "recall_designs",
                          "args": {"query": "pico case", "board": "raspberry-pi-pico"}})
    assert r["ok"]
    assert [d["brief_id"] for d in r["result"]["designs"]] == ["b_1"]
    assert r["result"]["lessons"] == ["Seen 1 time(s) on raspberry-pi-pico: clips usb plug. Design to avoid it."]
    assert r["result"]["simulations"] == []


def test_recall_designs_reads_past_simulations(tmp_path):
    from cadswarm.knowledge import fea_doc
    p = project(tmp_path)
    p.knowledge.index_fea(fea_doc({
        "id": "fr_1", "title": "Lid load", "analysis": "static", "verdict": "warning",
        "headline": "The lid bends 1.2 mm", "summary": "", "published_at": 1_700_000_000,
        "inputs": {"material": {"name": "PLA"}}, "model": {"target": ["Lid"]},
        "results": {"safety_factor": 1.4, "max_von_mises_mpa": 31.0, "max_displacement_mm": 1.2},
        "improvements": [{"severity": "warning", "text": "thicken the lid ribs"}]}))
    r = helper.handle(p, {"id": 1, "op": "recall_designs", "args": {"query": "lid"}})
    assert r["ok"] and r["result"]["simulations"][0]["title"] == "Lid load"
    assert r["result"]["simulations"][0]["problems"] == ["thicken the lid ribs"]


def test_recall_without_a_board_skips_lessons(tmp_path):
    p = project(tmp_path)
    seed_design(p, problems=["clips usb plug"])
    r = helper.handle(p, {"id": 1, "op": "recall_designs", "args": {"query": "pico case"}})
    assert r["ok"] and r["result"]["lessons"] == []


def test_a_recall_leaves_one_activity_row_per_design_with_its_own_problems(tmp_path):
    class Logged:
        def __init__(self):
            self.rows = []

        def log_activity(self, kind, summary, targets=None):
            self.rows.append((kind, summary, targets))

    p = project(tmp_path)
    seed_design(p, "b_1", request="A podium from last year", problems=[], at=1000)
    seed_design(p, "b_2", request="Build The Perch: a clip-on articulated arm",
                problems=["missing OTG cutout", "cradle 8.0 mm", "lid wall 1.0 mm"], at=2000)
    space = Logged()
    p.host_space = space
    r = helper.handle(p, {"id": 1, "op": "recall_designs", "args": {"query": "design"}})
    assert r["ok"]
    first, second = [row[1] for row in space.rows]
    assert all(row[0] == "recall" and row[2] == [] for row in space.rows)
    assert "#1" in first and "clip-on articulated arm" in first and "3 problems" in first
    assert "#2" in second and "A podium" in second and "0 problems" in second


def test_a_quote_in_the_query_is_not_a_design_head(tmp_path):
    class Logged:
        def __init__(self):
            self.rows = []

        def log_activity(self, kind, summary, targets=None):
            self.rows.append(summary)

    p = project(tmp_path)
    seed_design(p, request="A shelf bracket")
    space = Logged()
    p.host_space = space
    query = '#1 "clip-on articulated arm" · 3 problems · 2 lessons'
    r = helper.handle(p, {"id": 1, "op": "recall_designs", "args": {"query": query}})
    assert r["ok"]
    assert '#1 "clip-on articulated arm"' not in space.rows[0]
    assert "#1 'clip-on articulated arm'" in space.rows[0]
    assert '#1 "A shelf bracket"' in space.rows[0]


def test_a_helpers_recall_is_not_logged_as_the_hosts(tmp_path):
    class Logged:
        def __init__(self):
            self.rows = []

        def log_activity(self, *a, **k):
            self.rows.append(a)

    p = project(tmp_path)
    seed_design(p)
    space = Logged()
    p.host_space = space
    r = helper.handle(p, {"id": 1, "op": "recall_designs",
                          "args": {"query": "x", "_actor": {"id": 2, "name": "lid-builder", "kind": "agent"}}})
    assert r["ok"] and space.rows == []


def test_the_host_identity_is_read_from_host_json(tmp_path, monkeypatch):
    (tmp_path / "host.json").write_text(json.dumps(
        {"uri": "https://cloud", "db": "team-db", "token": "t_host"}), encoding="utf-8")
    seen = {}

    class FakeHub:
        def __init__(self, cfg, identity=None, timeout=None):
            seen["cfg"], seen["timeout"], self.identity = cfg, timeout, dict(identity or {})

        def me(self):
            return {"id": 7, "name": "host", "fence": 4}

    monkeypatch.setattr("cadswarm.space_hub.SpaceHub", FakeHub)
    p = helper.Project(tmp_path)
    hub = p.host_space
    assert isinstance(hub, FakeHub)
    assert seen["cfg"] == {"uri": "https://cloud", "db": "team-db", "token": "t_host"}
    assert seen["timeout"] == 5  # a dead hub must not spend the table's 25 s recall deadline
    assert hub.identity == {"token": "t_host", "participant_id": 7, "fence": 4, "name": "host"}


def test_no_host_json_or_no_member_leaves_recall_without_a_row(tmp_path, monkeypatch):
    assert helper.Project(tmp_path).host_space is None  # a checkout or a test, not a host data dir
    (tmp_path / "host.json").write_text(json.dumps({"uri": "u", "db": "d", "token": "t"}), encoding="utf-8")

    class NoMember:
        def __init__(self, cfg, identity=None, timeout=None):
            self.identity = dict(identity or {})

        def me(self):
            raise RuntimeError("no member")

    monkeypatch.setattr("cadswarm.space_hub.SpaceHub", NoMember)
    assert helper.Project(tmp_path).host_space is None


def test_a_recall_without_a_host_still_answers(tmp_path):
    p = project(tmp_path)
    seed_design(p)
    r = helper.handle(p, {"id": 1, "op": "recall_designs", "args": {"query": "x"}})
    assert r["ok"] and r["result"]["designs"]


def test_missing_args_are_bad_args(tmp_path):
    p = project(tmp_path)
    for op, args in (("lookup_board", {}), ("recall_designs", {}), ("board_spec", {}), ("index_design", {})):
        assert helper.handle(p, {"id": 1, "op": op, "args": args})["code"] == "bad_args"


def test_knowledge_lives_in_the_project_folder(tmp_path):
    p = project(tmp_path)
    seed_board(p)
    assert helper.handle(p, {"id": 1, "op": "lookup_board", "args": {"question": "holes"}})["ok"]
    assert os.path.isfile(os.path.join(tmp_path, ".cadswarm-team", "knowledge.db"))


def test_the_board_library_is_searchable_without_any_setup(tmp_path, library):
    p = project(tmp_path)
    r = helper.handle(p, {"id": 1, "op": "lookup_board",
                          "args": {"question": "mounting holes", "board": "raspberry-pi-pico"}})
    assert r["ok"]
    assert r["result"]["answers"][0]["board_id"] == "raspberry-pi-pico"
    assert "mounting holes" in r["result"]["answers"][0]["text"].lower()


def test_board_spec_comes_from_the_local_library(tmp_path, library):
    r = helper.handle(project(tmp_path), {"id": 1, "op": "board_spec", "args": {"board": "raspberry-pi-pico"}})
    assert r["ok"] and r["result"]["pcb"] == [51.0, 21.0, 1.0]
    assert r["result"]["sources"]["pcb"] == "https://d/pico.pdf"  # every measured number keeps a link


def test_an_unknown_board_is_reported_as_unverified(tmp_path, library):
    r = helper.handle(project(tmp_path), {"id": 9, "op": "board_spec", "args": {"board": "mystery-board"}})
    assert r["ok"] is False and r["code"] == "spec_unverified" and "No local spec" in r["error"]


def test_an_unreadable_library_is_reported_not_guessed(tmp_path, library):
    (library / "boards.json").write_text("{not json", encoding="utf-8")
    r = helper.handle(project(tmp_path), {"id": 9, "op": "board_spec", "args": {"board": "raspberry-pi-pico"}})
    assert r["ok"] is False and r["code"] == "spec_unverified" and "unreadable" in r["error"]


# ------------------------------------------------------------- team design style

HUMAN = {"id": 1, "name": "alice", "kind": "human"}
AGENT = {"id": 9, "name": "worker1", "kind": "agent"}


def test_style_learns_locally(tmp_path):
    p = helper.Project(tmp_path)
    r = helper.handle(p, {"id": 1, "op": "observe_op", "args": {
        "kind": "set_param", "args": {"alias": "wall", "value": "2.4 mm"}, "ok": True, "actor": HUMAN}})
    assert r["ok"] and r["result"]["recorded"] == 1
    r = helper.handle(p, {"id": 2, "op": "learn_style", "args": {}})
    assert r["ok"] and r["result"]["rules"]["candidate"] == 1 and "profile" in r["result"]
    rules = helper.handle(p, {"id": 3, "op": "list_style", "args": {}})["result"]["rules"]
    assert rules[0]["type"] == "param_convention"


def test_people_teach_style_and_agents_only_propose_it(tmp_path):
    p = project(tmp_path)
    r = helper.handle(p, {"id": 1, "op": "teach_style", "args": {"statement": "Use M2 inserts", "_actor": AGENT}})
    assert r["ok"] and r["result"]["rule"]["status"] == "candidate" and "M2" not in r["result"]["profile"]
    r = helper.handle(p, {"id": 2, "op": "teach_style", "args": {"statement": "Use M3 everywhere", "_actor": HUMAN}})
    assert r["ok"] and r["result"]["rule"]["status"] == "confirmed" and "Use M3 everywhere" in r["result"]["profile"]
    rid = r["result"]["rule"]["id"]
    r = helper.handle(p, {"id": 3, "op": "resolve_style", "args": {"rule_id": rid, "decision": "dismiss",
                                                                    "_actor": AGENT}})
    assert r["code"] == "humans_only" and "Learned style" in r["error"]
    r = helper.handle(p, {"id": 4, "op": "resolve_style", "args": {"rule_id": rid, "decision": "nope", "_actor": HUMAN}})
    assert r["code"] == "bad_args"
    ctx = helper.handle(p, {"id": 5, "op": "style_context", "args": {"query": "lid screws"}})["result"]
    assert "[confirmed] Use M3 everywhere" in ctx["text"]


def test_a_finished_brief_relearns_locally(tmp_path):
    p = project(tmp_path)
    r = helper.handle(p, {"id": 1, "op": "observe_brief", "args": {
        "brief": {"id": "b_1", "text": "case", "board": "pico"},
        "reviews": [{"round": 1, "findings_json": '[{"object": "Lid", "problem": "lid too thin"}]'}]}})
    assert r["ok"] and r["result"]["problems"] == 1 and r["result"]["distilled"]["candidate"] == 1


# --------------------------------------------------------------- lighten jobs (fake runner)

def _lighten_project(tmp_path, monkeypatch, runner):
    from cadswarm import lattice_jobs
    monkeypatch.setattr(lattice_jobs, "freecad_python", lambda: "freecad-python")
    p = helper.Project(tmp_path)
    (tmp_path / "model.FCStd").write_bytes(b"model")
    p.lattice = lattice_jobs.LatticeJobs(p, runner=runner)
    return p


EVENTS = []


def _wait(p, jid):
    import time
    deadline = time.time() + 30   # generous: the full suite can keep the machine busy
    while time.time() < deadline:
        r = helper.handle(p, {"id": 1, "op": "lattice_status", "args": {"job_id": jid}})
        assert r["ok"], r
        s = r["result"]
        EVENTS.extend(e["kind"] for e in s.get("_events", []))
        if s["status"] in ("done", "failed", "cancelled"):
            return s
        time.sleep(0.02)
    raise AssertionError(s)


def fake_lighten(python, job, work, on_progress, cancelled):
    import os
    on_progress({"phase": "simulate", "text": "Solving the solid part", "fraction": 0.2})
    on_progress({"phase": "mesh", "text": "Meshing", "fraction": 0.9})
    os.makedirs(job["out_dir"], exist_ok=True)
    for n in ("result.stl", "result.3mf", "preview.stl"):
        with open(os.path.join(job["out_dir"], n), "wb") as f:
            f.write(b"\0" * 84)
    with open(os.path.join(job["out_dir"], "report.md"), "w") as f:
        f.write("# report")
    return {"ok": True, "report": {
        "status": "passed", "mass": {"solid_g": 10, "lattice_g": 6, "weight_saving_pct": 40.0},
        "thickness_mm": {"min": 0.8, "max": 1.6, "mean": 1.0}, "verify": {"stiffness_retained": 0.91},
        "mesh": {"closed": True}, "warnings": [{"code": "closed_skin", "text": "A closed skin traps powder"}],
        "notes": [], "files": {"stl": "result.stl", "report": "report.md"}}}


def test_a_lighten_job_runs_publishes_its_files_and_announces_itself(tmp_path, monkeypatch):
    p = _lighten_project(tmp_path, monkeypatch, fake_lighten)
    r = helper.handle(p, {"id": 1, "op": "lattice_start", "args": {
        "target": "Bracket", "cell": "octet", "stiffness_target_pct": 90, "fixed": ["xmin"],
        "loads": [{"type": "force", "faces": ["xmax"], "newtons": 100}], "material": "pla", "_actor": AGENT}})
    assert r["ok"] and r["result"]["status"] in ("queued", "preparing") and r["result"]["recipe"]["stiffness_target"] == 0.9
    assert r["result"]["_events"][0]["kind"] == "lattice_started"
    EVENTS.extend(e["kind"] for e in r["result"]["_events"])  # a fast job may already have progressed
    done = _wait(p, r["result"]["job_id"])
    assert done["status"] == "done" and done["weight_saving_pct"] == 40.0 and done["verdict"] == "passed"
    assert done["warnings"] == ["A closed skin traps powder"] and "lattice_apply" in done["next_step"]
    assert [e["phase"] for e in done["log"]] == ["simulate", "mesh"]
    assert done["log"][-1]["text"] == "Meshing" and done["started_at"] and done["elapsed_s"] is not None
    assert (tmp_path / done["folder"] / "result.stl").exists() and (tmp_path / done["folder"] / "report.json").exists()
    EVENTS.extend(e["kind"] for e in helper.handle(p, {"id": 2, "op": "lattice_list", "args": {}})["result"]
                  .get("_events", []))
    assert "lattice_report" in EVENTS and "lattice_progress" in EVENTS
    res = helper.handle(p, {"id": 3, "op": "lattice_result", "args": {"job_id": r["result"]["job_id"]}})["result"]
    assert res["file"].startswith("lattice/") and res["file"].endswith("/result.stl") and res["target"] == "Bracket"


def test_a_lattice_that_cannot_be_published_keeps_its_files(tmp_path, monkeypatch):
    from cadswarm.storage import FileError

    p = _lighten_project(tmp_path, monkeypatch, fake_lighten)
    real_save = p.files.save

    def save(path, author, **kwargs):  # a 600 MB STL meets the project's 512 MB binary limit
        if path.endswith("result.stl"):
            raise FileError(413, "too_big", "Binary files like this can be at most 512 MB (this one is 600 MB).")
        return real_save(path, author, **kwargs)

    monkeypatch.setattr(p.files, "save", save)
    r = helper.handle(p, {"id": 1, "op": "lattice_start", "args": {"target": "Bracket"}})
    done = _wait(p, r["result"]["job_id"])
    assert done["status"] == "failed" and done["code"] == "publish_failed"
    assert "Nothing was deleted" in done["error"] and done["out_dir"] in done["error"]
    assert any(name.startswith("result.stl (") for name in done["out_files"])
    assert os.path.exists(os.path.join(done["out_dir"], "result.stl"))


def test_lighten_requests_are_checked_before_anything_runs(tmp_path, monkeypatch):
    p = _lighten_project(tmp_path, monkeypatch, fake_lighten)
    bad = helper.handle(p, {"id": 1, "op": "lattice_start", "args": {"target": "B", "cell": "kelvin"}})
    assert bad["code"] == "bad_cell"
    bad = helper.handle(p, {"id": 2, "op": "lattice_start", "args": {"target": "B", "skin_mm": 0,
                                                                      "open_faces": ["+z"]}})
    assert bad["code"] == "open_face_without_skin"
    bad = helper.handle(p, {"id": 3, "op": "lattice_start", "args": {"target": "B", "fea_job_id": "fea_none"}})
    assert bad["code"] == "not_found"
    # A partial load case is completed with FEA's flagged placeholders.
    r = helper.handle(p, {"id": 4, "op": "lattice_start", "args": {"target": "B", "fixed": ["xmin"]}})
    done = _wait(p, r["result"]["job_id"])
    assert any("No load given" in x["text"] for x in done["placeholders"])


def test_only_the_owner_or_a_person_cancels_a_lighten_job(tmp_path, monkeypatch):
    import threading
    gate = threading.Event()

    def slow(python, job, work, on_progress, cancelled):
        while not cancelled():
            gate.wait(0.01)
        return {"ok": False, "code": "cancelled", "error": "Cancelled."}
    p = _lighten_project(tmp_path, monkeypatch, slow)
    jid = helper.handle(p, {"id": 1, "op": "lattice_start", "args": {"target": "B", "_actor": AGENT}})["result"]["job_id"]
    other = {"id": 7, "name": "other", "kind": "agent"}
    assert helper.handle(p, {"id": 2, "op": "lattice_cancel", "args": {"job_id": jid, "_actor": other}})["code"] == "not_owner"
    helper.handle(p, {"id": 3, "op": "lattice_cancel", "args": {"job_id": jid, "_actor": HUMAN}})
    assert _wait(p, jid)["status"] == "cancelled"


def test_a_restarted_server_fails_interrupted_lighten_jobs(tmp_path, monkeypatch):
    from cadswarm import lattice_jobs
    p = _lighten_project(tmp_path, monkeypatch, fake_lighten)
    p.db.execute("INSERT INTO lattice_jobs (id, status, target, name, request) VALUES ('lj_dead', 'generating', "
                 "'B', 'B_lattice', '{}')")
    jobs = lattice_jobs.LatticeJobs(p, runner=fake_lighten)
    s = jobs.status("lj_dead")
    assert s["status"] == "failed" and s["code"] == "host_restarted"


def test_lattice_cells_and_estimates_need_no_job(tmp_path, monkeypatch):
    pytest.importorskip("scipy")
    p = _lighten_project(tmp_path, monkeypatch, fake_lighten)
    cells = helper.handle(p, {"id": 1, "op": "lattice_cells", "args": {"target": "stiffness", "value": 0.08}})["result"]
    assert len(cells["cells"]) == 8 and cells["cells"][0]["printable"]
    est = helper.handle(p, {"id": 2, "op": "lattice_estimate", "args": {
        "target": "B", "cell": "schwarzP", "voxel_mm": 0.4, "_bbox": [0, 0, 0, 40, 20, 10]}})["result"]
    assert est["build_voxels"] == 125000 and {w["code"] for w in est["warnings"]} >= {"closed_skin"}
