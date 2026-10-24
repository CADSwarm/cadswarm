"""Live end-to-end check of the components library and team settings: the published module on a local
SpacetimeDB, a running `cadswarm-host` (FreeCAD worker + helper) and two members talking through SpaceHub.

    spacetime start                                   # another terminal
    powershell -File scripts/publish-local.ps1        # fresh database
    cadswarm-host --data <dir> init --mode local --db cadswarm --python <python with cadswarm>
    cadswarm-host --data <dir> run                    # another terminal
    python module/tests/library_e2e.py                # from the repo root; needs FreeCAD to make its STEP

CADSWARM_URI / CADSWARM_DB pick the server and database, as in smoke.py (whose helpers mint the invites)."""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke  # noqa: E402

from cadswarm.client import HubRequestError  # noqa: E402
from cadswarm.space_hub import SpaceHub  # noqa: E402
from cadswarm.worker_client import WorkerClient  # noqa: E402

CFG = {"uri": smoke.URI, "db": smoke.DB}


def hub_for(client, name):
    me = client.me()
    return SpaceHub({**CFG, "name": name}, {"participant_id": me["id"], "token": client.token, "fence": me["fence"]})


def step(msg):
    print(f"e2e: {msg}", flush=True)


def wait(what, test, secs=60):
    t0 = time.time()
    while time.time() - t0 < secs:
        v = test()
        if v:
            return v
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


def make_step(tmp):
    """A 40x30x10 block with a 22 mm bearing bore and two M3 normal clearance holes, as STEP."""
    w = WorkerClient(os.path.join(tmp, "src.FCStd"))
    w.start()
    try:
        w.call("create_extrusion", {"name": "Block", "points": [[0, 0], [40, 0], [40, 30], [0, 30]], "height": 10,
                                    "holes": [{"x": 20, "y": 15, "r": 11}, {"x": 5, "y": 5, "r": 1.7},
                                              {"x": 35, "y": 5, "r": 1.7}]})
        path = os.path.join(tmp, "bearing block.step")
        w.call("export", {"path": path, "names": ["Block"]})
        return path
    finally:
        w.stop()


def main():
    host = smoke.Client(smoke.owner_token())
    host.me()
    carol = hub_for(smoke.join(host, "carol", kind="human"), "carol")
    alice = hub_for(smoke.join(host, "alice-claude"), "alice-claude")

    tmp = tempfile.mkdtemp()
    path = make_step(tmp)
    step(f"made {os.path.basename(path)} ({os.path.getsize(path)} bytes)")

    r = carol.post("/library/upload", {"local_path": path, "name": "Bearing block", "category": "structural",
                                       "tags": ["608", "block"], "license": "our own design"})
    cid = r["components"][0]["id"]
    step(f"uploaded -> {cid}; implied settings: {json.dumps(r.get('suggest'))}")
    lib = {c["id"]: c for c in carol.get("/library")["components"]}
    c = lib[cid]
    size = [round(c["bbox"][i + 3] - c["bbox"][i], 3) for i in range(3)]
    assert size == [40, 30, 10], size
    holes = {h["d"]: h["count"] for h in c["features"]["holes"]}
    assert holes == {3.4: 2, 22.0: 1}, holes
    step(f"FreeCAD measured it: {size} mm, holes {holes}, volume {c['volume']}")
    mesh = wait("the preview", lambda: _preview(carol, cid))
    step(f"preview STL: {len(mesh)} bytes")

    rec = alice.post("/library/recommend", {"need": "an 8 mm axle that spins"})["recommendations"][0]
    assert rec["family"] == "ball_bearing" and rec["size"] == "608", rec
    step(f"recommended {rec['label']}: hole {rec['hole']['diameter']} mm; suggests {[s['setting'] for s in rec['suggest']]}")
    found = alice.post("/library/search", {"query": "block 608"})["results"]
    assert found[0]["id"] == cid, found

    r = alice.post("/library/insert", {"family": "ball_bearing", "size": "608",
                                       "placement": {"base": [20, 15, 1.5]}})
    bearing = r["created"][0]
    r = alice.post("/library/insert", {"id": cid})
    block = r["created"][0]
    model = carol.get("/model")
    objs = {o["name"]: o for o in model["objects"]}
    assert all(abs(a - b) < 0.01 for a, b in zip(objs[bearing]["bbox"], [9, 4, 1.5, 31, 26, 8.5])), objs[bearing]
    assert objs[bearing]["locked_by"] == "alice-claude" and objs[block]["locked_by"] == "alice-claude"
    step(f"placed {bearing} and {block} (held by alice-claude)")
    clash = alice.post("/interference", {"names": [bearing, block]})
    step(f"clash check bearing vs block bore: {clash.get('clashes')}")
    assert next(x for x in carol.get("/library")["components"] if x["id"] == cid)["uses"] == 1

    try:
        alice.post("/settings", {"setting": "tol_press_fit", "value": 0})
        raise AssertionError("an AI helper changed a setting")
    except HubRequestError as e:
        assert e.code == "not_allowed", e
    sg = alice.post("/settings/suggest", {"setting": "tol_press_fit", "value": 0.0, "component": cid,
                                          "reason": "the 608 seat is printed; printed holes come out undersize"})
    pending = carol.get("/settings")["suggestions"]
    assert [p["id"] for p in pending] == [sg["id"]] and pending[0]["component_name"] == "Bearing block", pending
    carol.post(f"/settings/suggestions/{sg['id']}", {"accept": True})
    params = wait("Params.tol_press_fit", lambda: (carol.get("/model")["params"].get("tol_press_fit") is not None)
                  and carol.get("/model")["params"])
    assert float(params["tol_press_fit"]) == 0.0, params
    step(f"helper suggested, carol accepted -> Params.tol_press_fit = {params['tol_press_fit']}")
    hole = alice.post("/library/hole", {"family": "ball_bearing", "size": "608"})
    assert hole["diameter"] == 22.0, hole
    step(f"hole for a 608 now: {hole['diameter']} mm ({hole['why']})")

    carol.post("/settings", {"setting": "mesh_deviation", "value": 0.05})
    ctx = alice.get("/context")["context"]
    assert "tol_press_fit=0.0" in ctx and "Bearing block" in ctx, ctx
    step("agent context carries the team settings and the library")

    out = carol.post("/library/export-pack", {"name": "Bearings"})
    step(f"exported pack {out['file']} ({out['parts']} part, {out['size']} bytes)")
    again = carol.post("/library/import-pack", {"path": out["file"]})
    assert again["duplicates"] == ["Bearing block"] and not again.get("_components"), again
    try:
        alice.post(f"/library/{cid}/delete", {})
        raise AssertionError("an AI helper deleted a library part")
    except HubRequestError as e:
        assert e.code == "humans_only", e
    carol.post(f"/library/{cid}/delete", {})
    assert cid not in {c["id"] for c in carol.get("/library")["components"]}
    step("people remove parts; helpers can't")
    print("e2e: all checks passed")


def _preview(hub, cid):
    try:
        return hub.get(f"/library/{cid}/preview")
    except HubRequestError:
        return None


if __name__ == "__main__":
    main()
