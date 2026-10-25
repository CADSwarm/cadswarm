import os

import pytest

from cadswarm import helper
from cadswarm.knowledge import ElasticError


class FakeElastic:
    def __init__(self):
        self.calls = []

    def search(self, index, query, k=5, filters=None):
        self.calls.append((index, query, k, filters))
        return [{"score": 1.0, "source": {"board_id": "raspberry-pi-pico", "source_url": "https://d/p.pdf",
                                          "title": "Pico datasheet"}, "chunks": ["holes are 47 x 11.4 mm apart"]}]

    def lessons(self, board, k=5):
        return [{"board_id": board, "problem": "clips usb plug", "count": 2,
                 "lesson": "Seen 2 time(s) on raspberry-pi-pico: clips usb plug. Design to avoid it."}]

    def board_spec(self, board, question=""):
        return {"label": "Pico", "pcb": [51.0, 21.0, 1.0], "sources": {"pcb": "https://d/p.pdf"}}

    def index_design(self, doc):
        self.indexed = doc


def project(tmp_path, elastic):
    p = helper.Project(tmp_path)
    p.elastic = elastic
    return p


def test_lookup_returns_passages_with_their_sources(tmp_path):
    r = helper.handle(project(tmp_path, FakeElastic()), {"id": 1, "op": "lookup_board", "args": {"question": "pico holes"}})
    assert r["ok"] and r["result"]["answers"][0]["source_url"] == "https://d/p.pdf"
    assert "47 x 11.4" in r["result"]["answers"][0]["text"]


def test_a_board_alone_is_enough_to_look_up_or_recall(tmp_path):
    p = project(tmp_path, FakeElastic())
    assert helper.handle(p, {"id": 1, "op": "lookup_board", "args": {"question": "", "board": "raspberry-pi-pico"}})["ok"]
    assert helper.handle(p, {"id": 2, "op": "recall_designs", "args": {"query": "", "board": "raspberry-pi-pico"}})["ok"]
    assert helper.handle(p, {"id": 3, "op": "lookup_board", "args": {"question": "", "board": ""}})["code"] == "bad_args"


def test_unknown_ops_and_failures_are_errors_not_crashes(tmp_path):
    assert helper.handle(project(tmp_path, FakeElastic()), {"id": 1, "op": "nope", "args": {}})["code"] == "unknown_op"

    class Broken(FakeElastic):
        def search(self, *a, **k):
            raise RuntimeError("down")
    r = helper.handle(project(tmp_path, Broken()), {"id": 2, "op": "recall_designs", "args": {"query": "x"}})
    assert r["ok"] is False and "down" in r["error"]


def test_index_design_builds_the_document(tmp_path):
    fake = FakeElastic()
    r = helper.handle(project(tmp_path, fake), {"id": 1, "op": "index_design", "args": {
        "brief": {"id": "b_1", "text": "case", "board": "raspberry-pi-pico", "status": "ready"},
        "plan": {"parts": []}, "reviews": [], "params": {"wall": 2.0}}})
    assert r["ok"] and fake.indexed["brief_id"] == "b_1"


def test_recall_designs_filters_by_board_and_reads_lessons(tmp_path):
    fake = FakeElastic()
    r = helper.handle(project(tmp_path, fake), {"id": 1, "op": "recall_designs",
                                                "args": {"query": "pico case", "board": "raspberry-pi-pico"}})
    assert r["ok"]
    assert fake.calls == [("cadswarm-designs", "pico case", 3, {"board_id": "raspberry-pi-pico"}),
                          ("cadswarm-fea", "pico case", 3, None)]  # and the team's past simulations
    assert "simulations" in r["result"]
    assert r["result"]["designs"][0]["board_id"] == "raspberry-pi-pico"
    assert r["result"]["lessons"] == ["Seen 2 time(s) on raspberry-pi-pico: clips usb plug. Design to avoid it."]


def test_recall_without_a_board_skips_lessons(tmp_path):
    r = helper.handle(project(tmp_path, FakeElastic()), {"id": 1, "op": "recall_designs", "args": {"query": "pico case"}})
    assert r["ok"] and r["result"]["lessons"] == []


def test_missing_args_are_bad_args(tmp_path):
    p = project(tmp_path, FakeElastic())
    for op, args in (("lookup_board", {}), ("recall_designs", {}), ("board_spec", {}), ("index_design", {})):
        assert helper.handle(p, {"id": 1, "op": op, "args": args})["code"] == "bad_args"


def test_elastic_errors_are_reported_with_their_own_code(tmp_path):
    class Busy(FakeElastic):
        def search(self, *a, **k):
            raise ElasticError(503, '{"error": {"reason": "busy"}}')

    r = helper.handle(project(tmp_path, Busy()), {"id": 1, "op": "lookup_board", "args": {"question": "x"}})
    assert r["ok"] is False and r["code"] == "elastic_error" and "busy" in r["error"]


def test_unconfigured_host_reports_not_configured_but_files_still_work(tmp_path, monkeypatch):
    for var in ("ELASTIC_URL", "KIBANA_URL", "ELASTIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    p = helper.Project(tmp_path)
    assert helper.handle(p, {"id": 1, "op": "lookup_board", "args": {"question": "x"}})["code"] == "not_configured"
    assert helper.handle(p, {"id": 2, "op": "list_files", "args": {}})["ok"] is True


def test_board_spec_passes_through(tmp_path):
    r = helper.handle(project(tmp_path, FakeElastic()), {"id": 1, "op": "board_spec", "args": {"board": "raspberry-pi-pico"}})
    assert r["ok"] and r["result"]["pcb"] == [51.0, 21.0, 1.0]


def test_an_unsourced_board_spec_is_reported_as_unverified(tmp_path):
    class Unsourced(FakeElastic):
        def board_spec(self, board, question=""):
            raise ValueError("pcb has no source_url")
    r = helper.handle(project(tmp_path, Unsourced()), {"id": 9, "op": "board_spec", "args": {"board": "mystery-board"}})
    assert r["ok"] is False and r["code"] == "spec_unverified" and "source_url" in r["error"]


# ------------------------------------------------------------- team design style

HUMAN = {"id": 1, "name": "alice", "kind": "human"}
AGENT = {"id": 9, "name": "worker1", "kind": "agent"}


def test_style_learns_locally_without_elastic(tmp_path, monkeypatch):
    for k in ("ELASTIC_URL", "KIBANA_URL", "ELASTIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    p = helper.Project(tmp_path)
    r = helper.handle(p, {"id": 1, "op": "observe_op", "args": {
        "kind": "set_param", "args": {"alias": "wall", "value": "2.4 mm"}, "ok": True, "actor": HUMAN}})
    assert r["ok"] and r["result"]["recorded"] == 1
    r = helper.handle(p, {"id": 2, "op": "learn_style", "args": {}})
    assert r["ok"] and r["result"]["rules"]["candidate"] == 1 and "profile" in r["result"]
    rules = helper.handle(p, {"id": 3, "op": "list_style", "args": {}})["result"]["rules"]
    assert rules[0]["type"] == "param_convention"


def test_people_teach_style_and_agents_only_propose_it(tmp_path):
    p = project(tmp_path, FakeElastic())
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


def test_a_finished_brief_relearns_and_mirrors_to_elastic(tmp_path):
    class Mirror(FakeElastic):
        def __init__(self):
            super().__init__()
            self.bulk = []

        def es(self, method, path, body=None, timeout=None):
            self.bulk.append(path)

        def converse(self, text, timeout=180, agent_id=None):
            return "{}"
    fake = Mirror()
    p = project(tmp_path, fake)
    r = helper.handle(p, {"id": 1, "op": "observe_brief", "args": {
        "brief": {"id": "b_1", "text": "case", "board": "pico"},
        "reviews": [{"round": 1, "findings_json": '[{"object": "Lid", "problem": "lid too thin"}]'}]}})
    assert r["ok"] and r["result"]["problems"] == 1 and r["result"]["distilled"]["candidate"] == 1
    import time
    for _ in range(50):
        if fake.bulk:
            break
        time.sleep(0.02)
    assert fake.bulk == ["/_bulk"]


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
