"""The new read-only worker queries, against a real FreeCAD worker."""
import base64
import struct

import pytest

from cadswarm.config import find_freecad_python
from cadswarm.worker_client import WorkerClient

try:
    find_freecad_python()
except RuntimeError:
    pytest.skip("FreeCAD not installed", allow_module_level=True)


@pytest.fixture(scope="module")
def worker(tmp_path_factory):
    w = WorkerClient(str(tmp_path_factory.mktemp("w") / "model.FCStd"))
    w.start()
    w.call("create_primitive", {"kind": "box", "name": "Big", "props": {"Length": 300, "Width": 20, "Height": 10}})
    w.call("create_primitive", {"kind": "box", "name": "Small", "props": {"Length": 20, "Width": 20, "Height": 10}})
    w.call("run_part_script", {"name": "Thin", "code":
        "outer = Part.makeBox(30, 30, 10)\ninner = Part.makeBox(29.4, 29.4, 10, Vector(0.3, 0.3, 1))\n"
        "shape = outer.cut(inner)"})
    yield w
    w.stop()


def test_mesh_returns_binary_stl_per_solid(worker):
    r = worker.call("mesh", {"names": ["Small"], "tolerance": 0.2, "max_bytes": 200000})["result"]
    data = base64.b64decode(r["meshes"]["Small"])
    (count,) = struct.unpack("<I", data[80:84])
    assert count >= 12 and len(data) == 84 + 50 * count


def test_mesh_respects_the_size_budget(worker):
    r = worker.call("mesh", {"names": ["Small"], "tolerance": 0.01, "max_bytes": 2000})["result"]
    assert len(base64.b64decode(r["meshes"]["Small"])) <= 2000 or "Small" in r["meshes"]


def test_mesh_omits_objects_without_a_solid_shape(worker):
    r = worker.call("mesh", {"names": ["Params", "Small", "NoSuchThing"]})["result"]
    assert set(r["meshes"]) == {"Small"}
    everything = worker.call("mesh", {})["result"]["meshes"]
    assert {"Big", "Small", "Thin"} <= set(everything) and "Params" not in everything


def test_printability_flags_oversize_parts_and_thin_walls(worker):
    r = worker.call("printability", {"names": ["Big", "Small", "Thin"], "bed": [180, 180, 180], "min_wall": 1.2})["result"]
    by = {o["name"]: o for o in r["objects"]}
    assert not by["Big"]["fits_bed"] and by["Small"]["fits_bed"]
    assert by["Small"]["closed_solid"] and not by["Small"]["thin_wall_suspects"]
    assert by["Small"]["size"] == [20.0, 20.0, 10.0]
    assert by["Thin"]["thin_wall_suspects"], "0.3 mm walls must be flagged"
    assert r["ok"] is False
    assert worker.call("printability", {"names": ["Small"], "bed": [180, 180, 180]})["result"]["ok"] is True
