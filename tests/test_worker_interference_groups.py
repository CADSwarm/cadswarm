"""Interference checks should not compare an App::Part aggregate with its child solids twice."""
import os
import subprocess

import pytest

from cadswarm.config import find_freecad_python
from cadswarm.worker_client import WorkerClient

try:
    FREECAD_PYTHON = find_freecad_python()
except RuntimeError:
    pytest.skip("FreeCAD not installed", allow_module_level=True)


def _make_grouped_model(path, profile):
    script = path.with_name("make_grouped_model.py")
    script.write_text(
        """
import FreeCAD as App, Part, sys
doc = App.newDocument('InterferenceGroups')
assembly = doc.addObject('App::Part', 'Assembly')
child = doc.addObject('Part::Feature', 'ChildA')
child.Shape = Part.makeBox(10, 10, 10)
assembly.addObject(child)
other = doc.addObject('Part::Feature', 'Other')
other.Shape = Part.makeBox(10, 10, 10, App.Vector(5, 0, 0))
for obj in (assembly, child, other):
    obj.Visibility = True
doc.recompute()
doc.saveAs(sys.argv[1])
""",
        encoding="utf-8",
    )
    env = dict(os.environ, FREECAD_USER_HOME=str(profile), PYTHONIOENCODING="utf-8")
    subprocess.run([FREECAD_PYTHON, str(script), str(path)], env=env, check=True, capture_output=True,
                   text=True, timeout=90)


def test_interference_omits_aggregate_by_default_but_keeps_explicit_names(tmp_path, monkeypatch):
    model = tmp_path / "grouped.FCStd"
    profile = tmp_path / "freecad-profile"
    profile.mkdir()
    _make_grouped_model(model, profile)
    monkeypatch.setenv("FREECAD_USER_HOME", str(profile))

    worker = WorkerClient(str(model), python=FREECAD_PYTHON)
    try:
        worker.start()
        result = worker.call("interference", {})["result"]
        assert result["checked"] == ["ChildA", "Other"]
        assert result["clashes"] == [{"a": "ChildA", "b": "Other", "volume": 500.0}]

        explicit = worker.call("interference", {"names": ["Assembly", "ChildA", "Other"]})["result"]
        assert explicit["checked"] == ["Assembly", "ChildA", "Other"]
        clashes = {(c["a"], c["b"]): c["volume"] for c in explicit["clashes"]}
        assert clashes == {("Assembly", "ChildA"): 1000.0, ("Assembly", "Other"): 500.0,
                           ("ChildA", "Other"): 500.0}
    finally:
        worker.stop()
