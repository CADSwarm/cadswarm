"""Lighten jobs end to end in real FreeCAD: snapshot, runner subprocess, published files, cancel, errors."""
import json
import os
import time

import pytest

from cadswarm.config import find_freecad_python
from cadswarm.worker_client import WorkerClient

try:
    find_freecad_python()
except RuntimeError:
    pytest.skip("FreeCAD not installed", allow_module_level=True)

from cadswarm import helper  # noqa: E402
from cadswarm.lattice_jobs import LatticeJobs  # noqa: E402

PERSON = {"id": 1, "name": "alice", "kind": "human"}
AGENT = {"id": 9, "name": "worker1", "kind": "agent"}


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("lighten")
    w = WorkerClient(str(root / "model.FCStd"))
    w.start()
    # A 40 x 12 x 12 mm cantilever bracket with a mounting hole near its fixed end.
    w.call("create_extrusion", {"name": "Bracket", "points": [[0, 0], [40, 0], [40, 12], [0, 12]], "height": 12,
                                "holes": [{"x": 5, "y": 6, "r": 1.7}]})
    w.call("create_primitive", {"kind": "box", "name": "Sheet", "props": {"Length": 10, "Width": 10, "Height": 0.2}})
    w.stop()
    p = helper.Project(str(root))
    p.lattice = LatticeJobs(p)
    return p


def wait(jobs, jid, limit=900):
    t = time.time()
    while time.time() - t < limit:
        s = jobs.status(jid)
        if s["status"] in ("done", "failed", "cancelled"):
            return s
        time.sleep(2)
    raise AssertionError(f"{jid} still {s['status']} after {limit}s")


def test_a_graded_lighten_job_verifies_and_publishes_print_files(project):
    jobs = project.lattice
    s = jobs.start({"target": "Bracket", "cell": "gyroid", "cell_size_mm": 5, "min_thickness_mm": 0.8,
                    "skin_mm": 1.0, "stiffness_target_pct": 70, "voxel_mm": 0.4, "material": "aluminium_6061_t6",
                    "fixed": ["xmin"], "loads": [{"type": "force", "faces": ["xmax"], "newtons": 300,
                                                  "direction": [0, 0, -1]}]}, AGENT)
    assert s["status"] in ("queued", "preparing") and s["job_id"].startswith("lj_")
    done = wait(jobs, s["job_id"])
    assert done["status"] == "done", done
    assert done["verdict"] in ("passed", "no_passing_design")
    if done["verdict"] == "passed":
        assert done["verify"]["stiffness_retained"] >= 0.7
    assert 0 < done["weight_saving_pct"] < 100
    assert done["mesh"]["closed"] and done["mesh"]["oriented"]
    bbox = done["mesh"]["bbox"]
    assert abs(bbox[3] - 40) < 0.6 and abs(bbox[4] - 12) < 0.6 and abs(bbox[5] - 12) < 0.6
    folder = os.path.join(project.layout["root"], done["folder"])
    for name in ("result.stl", "result.3mf", "preview.stl", "report.json", "report.md"):
        assert os.path.getsize(os.path.join(folder, name)) > 0, name
    rep = json.load(open(os.path.join(folder, "report.json"), encoding="utf-8"))
    assert rep["source"]["name"] == "Bracket" and rep["recipe"]["cell"] == "gyroid"
    events = jobs.with_events({})["_events"]
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "lattice_started" and "lattice_report" in kinds and "lattice_progress" in kinds
    applied = jobs.result_file(s["job_id"])
    assert applied["file"].endswith("/result.stl") and applied["target"] == "Bracket"


def test_cancel_stops_a_running_job_and_leaves_no_process(project):
    jobs = project.lattice
    s = jobs.start({"target": "Bracket", "cell": "octet", "voxel_mm": 0.3, "fixed": ["xmin"],
                    "loads": [{"type": "force", "faces": ["xmax"], "newtons": 300, "direction": [0, 0, -1]}]}, AGENT)
    t = time.time()
    while jobs.status(s["job_id"])["status"] == "queued" and time.time() - t < 60:
        time.sleep(0.5)
    with pytest.raises(Exception):
        jobs.cancel(s["job_id"], {"id": 5, "name": "other", "kind": "agent"})  # not the owner
    jobs.cancel(s["job_id"], PERSON)  # a person may cancel anyone's job
    assert wait(jobs, s["job_id"], 120)["status"] == "cancelled"


def test_bad_parts_and_bad_requests_fail_with_a_reason(project):
    jobs = project.lattice
    with pytest.raises(Exception) as e:
        jobs.start({"target": "Bracket", "cell": "kelvin"}, AGENT)
    assert getattr(e.value, "code", "") == "bad_cell"
    s = jobs.start({"target": "Nope", "grading": "uniform", "thickness_mm": 1.0}, AGENT)
    done = wait(jobs, s["job_id"], 300)
    assert done["status"] == "failed" and "Nope" in done["error"]


def test_without_a_load_case_the_lattice_is_uniform_and_unverified(project):
    jobs = project.lattice
    s = jobs.start({"target": "Bracket", "cell": "bcc", "cell_size_mm": 5, "grading": "uniform",
                    "thickness_mm": 1.2, "voxel_mm": 0.4, "material": "pla"}, PERSON)
    done = wait(jobs, s["job_id"], 600)
    assert done["status"] == "done" and done["verdict"] == "not_verified" and done["verify"] is None
    assert done["thickness_mm"]["min"] == done["thickness_mm"]["max"] == 1.2


def test_a_lattice_mesh_goes_into_the_model_next_to_its_source(tmp_path):
    import numpy as np
    from cadswarm.lattice import mc, voxelize
    files = tmp_path / "files" / "lattice"
    files.mkdir(parents=True)
    g = voxelize.grid_for((-6, -6, -6), (6, 6, 6), 0.5)
    sphere = mc.contour(np.linalg.norm(g.points(), axis=-1) - 5.0, g.origin, g.pitch)
    mc.write_stl(sphere, files / "ball.stl")
    open_mesh = mc.Mesh(sphere.vertices, sphere.faces[:-10])
    mc.write_stl(open_mesh, files / "open.stl")
    w = WorkerClient(str(tmp_path / "model.FCStd"))
    w.start()
    try:
        r = w.call("insert_mesh", {"name": "Ball_lattice", "file": "lattice/ball.stl", "source": "Ball"})
        assert r["ok"], r
        assert r["result"]["triangles"] == len(sphere.faces) and abs(r["result"]["volume_mm3"] - 516) < 15
        objs = {o["name"]: o for o in w.call("status", {})["result"]["objects"]}
        assert objs["Ball_lattice"]["type"] == "Mesh::Feature" and objs["Ball_lattice"]["triangles"] > 0
        assert abs(objs["Ball_lattice"]["bbox"][3] - 5) < 0.3
        preview = w.call("mesh", {"names": ["Ball_lattice"], "max_bytes": 20_000})["result"]["meshes"]
        assert "Ball_lattice" in preview
        from cadswarm.worker_client import WorkerError
        for args, code in (({"name": "Ball_lattice", "file": "lattice/ball.stl"}, "exists"),
                           ({"name": "X", "file": "../model.FCStd"}, "bad_args"),
                           ({"name": "Leaky", "file": "lattice/open.stl"}, "not_solid")):
            with pytest.raises(WorkerError) as e:
                w.call("insert_mesh", args)
            assert e.value.code == code, (args, e.value)
    finally:
        w.stop()
