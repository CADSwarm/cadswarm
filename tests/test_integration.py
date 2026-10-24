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

    # Bob cannot touch Alice's plate — neither directly nor sneakily from a script.
    s, r = op(bob, "set_props", name="Plate", props={"Length": 10})
    assert s == 409 and r["code"] == "not_locked" and r["heldBy"]["Plate"]["name"] == "alice-claude"
    s, r = op(bob, "run_part_script", code="doc.getObject('Plate').Height = 50")
    assert s == 409 and r["code"] == "lock_violation"
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
