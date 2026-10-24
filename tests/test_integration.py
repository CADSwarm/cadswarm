"""End to end: real FastAPI hub + real FreeCAD worker, two agents over HTTP."""
import pytest
from fastapi.testclient import TestClient

from cadswarm.config import find_freecad_python
from cadswarm.hub.app import create_app
from cadswarm.hub.core import Hub
from cadswarm.hub.worker_client import WorkerClient

try:
    find_freecad_python()
except RuntimeError:
    pytest.skip("FreeCAD not installed", allow_module_level=True)


@pytest.fixture(scope="module")
def hub_env(tmp_path_factory):
    data = tmp_path_factory.mktemp("hub")
    worker = WorkerClient(str(data / "model.FCStd"))
    worker.start()
    hub = Hub(worker, str(data / "hub.sqlite"), "secret")
    with TestClient(create_app(hub, str(data), sweep_seconds=3600)) as http:
        yield http, hub, data
    worker.stop()


def agent(http, name, client):
    ident = http.post("/join", json={"workspace_token": "secret", "name": name, "client": client}).json()
    headers = {"x-participant": ident["participant_id"], "authorization": f"Bearer {ident['token']}",
               "x-fence": str(ident["fence"])}

    def call(method, path, body=None):
        r = http.request(method, path, json=body, headers=headers)
        return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content
    return call


def op(call, op_name, **args):
    return call("POST", "/ops", {"op": op_name, "args": args})


def test_two_agents_build_one_model(hub_env):
    http, hub, data = hub_env
    lead = agent(http, "alice-claude", "claude")
    bob = agent(http, "bob-codex", "codex")

    # The lead defines the shared interface.
    assert lead("POST", "/locks", {"names": ["Params"]})[0] == 200
    s, r = op(lead, "set_param", alias="hole_pitch", value="40 mm")
    assert s == 200 and r["modified"] == ["Params"]

    # Alice builds the plate; Bob builds a post bound to the shared parameter.
    s, r = op(lead, "create_primitive", kind="box", name="Plate", props={"Length": 100, "Width": 60, "Height": 5})
    assert s == 200 and r["created"] == ["Plate"]
    s, r = op(bob, "create_primitive", kind="cylinder", name="Post", props={"Radius": 4, "Height": 30},
              expressions={".Placement.Base.x": "Params.hole_pitch", ".Placement.Base.z": "5 mm"})
    assert s == 200, r

    # Bob cannot touch Alice's plate: not directly, and not from a script (which runs on a read-only snapshot).
    s, r = op(bob, "set_props", name="Plate", props={"Length": 10})
    assert s == 409 and r["code"] == "not_locked" and r["heldBy"]["Plate"]["name"] == "alice-claude"
    s, r = op(bob, "run_part_script", code="doc.getObject('Plate').Height = 50")
    assert s == 200 and r["modified"] == [] and r["created"] == []  # scripts only see a snapshot: edits vanish
    s, r = op(bob, "run_part_script", name="Plate", code="shape = Part.makeBox(1, 1, 1)")
    assert s == 409 and r["code"] == "not_locked"  # nor can a script swap out someone else's part
    s, model = lead("GET", "/model")
    plate = next(o for o in model["objects"] if o["name"] == "Plate")
    assert plate["bbox"][5] == 5.0 and plate["locked_by"] == "alice-claude"  # rolled back

    # Changing the interface moves Bob's part and tells Bob.
    s, r = op(lead, "set_param", alias="hole_pitch", value="55 mm")
    assert r["recomputed"] == ["Post"] and r["notified"] == {"bob-codex": ["Post"]}
    s, model = bob("GET", "/model")
    post = next(o for o in model["objects"] if o["name"] == "Post")
    assert post["bbox"][0] == pytest.approx(51.0) and post["rev"] == 2
    s, inbox = bob("GET", "/inbox")
    assert any("hole_pitch" in m["body"] for m in inbox["messages"])

    # A scripted part, an interference check, and a STEP export.
    s, r = op(bob, "run_part_script", name="Bracket",
              code="shape = Part.makeBox(10, 10, 10, Vector(params['hole_pitch'] - 5, -5, 0))")
    assert s == 200 and r["created"] == ["Bracket"]
    s, r = bob("POST", "/interference", {})
    pairs = {tuple(sorted((c["a"], c["b"]))) for c in r["clashes"]}
    assert ("Bracket", "Plate") in pairs
    s, r = lead("POST", "/export", {"format": "step"})
    assert s == 200 and set(r["objects"]) >= {"Plate", "Post", "Bracket"}
    s, blob = lead("GET", r["url"])
    assert s == 200 and b"ISO-10303-21" in blob

    # Geometry for the FreeCAD workbench mirror.
    s, r = lead("POST", "/brep", {"names": ["Post"]})
    assert "CASCADE Topology" in r["objects"]["Post"]["brep"] and \
        r["objects"]["Post"]["locked_by"] == "bob-codex"

    s, ctx = lead("GET", "/context")
    assert "bob-codex" in ctx["context"] and "Bracket" in ctx["context"]


def test_worker_restart_keeps_committed_work(hub_env):
    http, hub, data = hub_env
    a = agent(http, "carol-claude", "claude")
    s, r = op(a, "create_primitive", kind="sphere", name="Ball", props={"Radius": 3},
              placement={"base": [0, 200, 0]})
    assert s == 200
    hub.worker.proc.kill()
    hub.worker.proc.wait()
    s, model = a("GET", "/model")  # transparently restarts from the saved FCStd
    assert s == 200 and "Ball" in {o["name"] for o in model["objects"]}


def test_part_scripts_cannot_touch_files(hub_env, tmp_path):
    http, hub, data = hub_env
    a = agent(http, "erin-claude", "claude")
    target = tmp_path / "victim.txt"
    target.write_text("precious")
    attempts = [
        f"open(r'{target}', 'w').write('gone')",
        "import os\nos.remove('x')",
        "import shutil",
        "import subprocess",
        f"shape = Part.makeBox(1,1,1)\nshape.exportStep(r'{tmp_path / 'x.step'}')",
        f"doc.saveAs(r'{tmp_path / 'x.FCStd'}')",
        "__import__('os').system('echo hi')",
        f"import random\nrandom._os.makedirs(r'{tmp_path / 'made'}')",
        f"f = doc.saveAs\nf(r'{tmp_path / 'y.FCStd'}')",
    ]
    for code in attempts:
        s, r = op(a, "run_part_script", name="Evil", code=code)
        assert s >= 400, (code, r)
    assert target.read_text() == "precious" and not (tmp_path / "x.step").exists()
    assert not (tmp_path / "made").exists() and not (tmp_path / "y.FCStd").exists()
    s, r = op(a, "run_part_script", name="Gear",  # ordinary geometry scripts still work
              code="import math\nshape = Part.makeCylinder(10, 5)\nresult = {'pi': round(math.pi, 2)}")
    assert s == 200 and r["result"]["result"] == {"pi": 3.14}


def test_project_files_snapshots_and_exports_over_http(hub_env):
    http, hub, data = hub_env
    a = agent(http, "fay-claude", "claude")
    b = agent(http, "gus-codex", "codex")
    s, r = a("POST", "/project/files/save", {"path": "parts/base/notes.md", "text": "# Base"})
    assert s == 200 and r["version"] == 1
    s, r = b("POST", "/project/files/save", {"path": "parts/base/notes.md", "text": "overwrite!"})
    assert s == 409 and r["code"] == "file_exists"
    s, r = b("POST", "/project/files/read", {"path": "parts/base/notes.md"})
    assert r["text"] == "# Base"
    s, r = b("POST", "/project/files/save", {"path": "parts/base/notes.md", "text": "# Base v2",
                                            "if_exists": "replace", "expected_version": r["version"]})
    assert s == 200 and r["version"] == 2
    s, r = a("GET", "/project/files")
    assert "parts/base/notes.md" in [f["path"] for f in r["files"]]
    s, blob = a("GET", "/project/files/download?path=parts/base/notes.md")
    assert s == 200 and blob == b"# Base v2"
    s, r = a("POST", "/project/files/save", {"path": "../escape.md", "text": "x"})
    assert s == 400 and not (data / "escape.md").exists()
    s, snap1 = a("POST", "/project/snapshot", {"label": "before lid"})
    s, snap2 = a("POST", "/project/snapshot", {"label": "before lid"})
    assert s == 200 and snap1["file"] != snap2["file"] and (data / snap2["file"]).exists()
    s, e1 = a("POST", "/export", {"format": "step", "label": "Phone stand"})
    s, e2 = a("POST", "/export", {"format": "step", "label": "Phone stand"})
    assert e1["file"] != e2["file"] and e1["file"].startswith("Phone stand ")
    s, blob = a("GET", e2["url"])
    assert s == 200 and b"ISO-10303-21" in blob


def test_fea_on_a_copy_matches_beam_theory(hub_env):
    """Steel cantilever 100 x 20 x 10 mm, fixed at x=0, 1000 N down at the tip: δ = FL³/3EI ≈ 0.952 mm."""
    import os
    http, hub, data = hub_env
    a = agent(http, "hana-claude", "claude")
    b = agent(http, "ivan-codex", "codex")
    s, r = op(a, "create_primitive", kind="box", name="Cantilever", props={"Length": 100, "Width": 20, "Height": 10},
              placement={"base": [0, 500, 0]})
    assert s == 200
    s, faces = a("POST", "/fea/faces", {"names": ["Cantilever"], "select": ["xmin", "xmax", "cyl"]})
    assert s == 422 and faces["code"] == "bad_args" and "cyl" in faces["error"]  # a box has no round faces: refused with a reason
    s, faces = a("POST", "/fea/faces", {"names": ["Cantilever"], "select": ["xmin", "xmax"]})
    assert s == 200 and faces["face_count"] == 6 and len(faces["selected"]["xmin"]) == 1
    mtime, objects = os.path.getmtime(hub.worker.doc_path), {o["name"] for o in hub.model()["objects"]}

    body = {"target": "Cantilever", "material": "steel_s235", "fixed": ["xmin"], "mesh": {"max_size_mm": 3},
            "loads": [{"type": "force", "faces": ["xmax"], "newtons": 1000, "direction": [0, 0, -1]}]}
    s, job = a("POST", "/fea/run", {**body, "wait": 0})
    assert s == 200 and job["status"] in ("queued", "running", "done")
    s, r = op(b, "create_primitive", kind="sphere", name="Marble", props={"Radius": 2},
              placement={"base": [0, -300, 0]})
    assert s == 200  # the solve runs in its own process: teammates keep working meanwhile
    s, job = a("GET", f"/fea/jobs/{job['job_id']}?wait=55")
    if job.get("code") in ("missing_tool", "missing_mesher"):
        pytest.skip(job["error"])
    assert job["status"] == "done", job
    res = job["results"]
    assert res["max_displacement_mm"] == pytest.approx(0.952, rel=0.15)
    assert res["max_displacement_vector"][2] < 0  # it bends down, the way it was pushed
    assert res["max_von_mises_mpa"] == pytest.approx(300, rel=0.25)
    assert abs(res["reactions"][0]["fz"] - 1000) < 50
    assert os.path.getmtime(hub.worker.doc_path) > mtime  # only because Marble was added...
    after = {o["name"] for o in hub.model()["objects"]}
    assert after == objects | {"Marble"} and not any(n.startswith(("Fem", "Mesh", "Analysis")) for n in after)

    s, pub = a("POST", f"/fea/jobs/{job['job_id']}/publish", {"summary": "Bends 1 mm.", "improvements": ["Thicker"]})
    assert s == 200 and pub["files"]["html"].endswith("report.html")
    s, page = a("POST", "/project/files/read", {"path": pub["files"]["html"]})
    assert "Bends 1 mm." in page["text"]

    s, job = a("POST", "/fea/run", {"target": "Cantilever", "analysis": "modal", "material": "steel_s235",
                                    "fixed": ["xmin"], "mesh": {"max_size_mm": 3}, "wait": 55})
    first = [m["hz"] for m in job["results"]["modes"] if m["hz"] >= 1][0]
    assert first == pytest.approx(836, rel=0.15)  # (1.875²/2π)·√(EI/ρAL⁴)
