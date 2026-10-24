"""Invoked in FreeCAD's actual Python by test_structural_freecad.py."""
import json
import math
from pathlib import Path
import sys

import FreeCAD as App
import Part
import Mesh
import numpy as np

from cadswarm.structural.demo import request
from cadswarm.structural.freecad import discretize, reconstruct
from cadswarm.structural.job import run
from cadswarm.structural.model import StructuralError

root = Path(sys.argv[1])
source = Part.makeBox(48, 16, 16, App.Vector(10, 20, 30))
grid = discretize(source, 4)
assert grid.origin == (10, 20, 30)
assert grid.volume_mm3 == source.Volume

# A square mounting hole and its surfaces must survive exact reconstruction.
hole = Part.makeBox(4, 4, 16, App.Vector(18, 24, 30))
with_hole = source.cut(hole)
hole_grid = discretize(with_hole, 4)
mask = hole_grid.mask.copy()
mask[7, 2, 2] = False
rebuilt = reconstruct(with_hole, hole_grid, hole_grid.with_mask(mask))
assert rebuilt.isValid() and len(rebuilt.Solids) == 1
assert rebuilt.common(hole).Volume < 1e-8
assert math.isclose(rebuilt.Volume, hole_grid.with_mask(mask).volume_mm3, rel_tol=1e-10)
assert math.isclose(with_hole.Volume, hole_grid.volume_mm3, rel_tol=1e-10)

curved = source.cut(Part.makeCylinder(2, 16, App.Vector(20, 28, 30)))
try:
    discretize(curved, 4)
except StructuralError:
    pass
else:
    raise AssertionError("Curved mounting hole was silently approximated")

config = request()
config.update(brep=source.exportBrepToString(), pitch_mm=4, solver=sys.argv[2])
config["constraints"]["target_reduction"] = .02
result = run(config, root / "result")
assert result.improved
optimized = Part.Shape()
optimized.read(str(root / "result" / "optimized.brep"))
step = Part.read(str(root / "result" / "optimized.step"))
assert step.isValid() and len(step.Solids) == 1
assert abs(step.Volume - optimized.Volume) < 1e-5
assert Mesh.Mesh(str(root / "result" / "optimized.stl")).isSolid()
assert math.isclose(optimized.Volume, result.optimized.volume_mm3, rel_tol=1e-10)
assert optimized.Volume < source.Volume
assert math.isclose(source.Volume, 48 * 16 * 16, rel_tol=1e-10)

doc = App.newDocument("OptimizationTest")
doc.addObject("Part::Feature", "Original").Shape = source
doc.addObject("Part::Feature", "Optimized").Shape = optimized
doc.recompute()
path = str(root / "comparison.FCStd")
doc.saveAs(path)
App.closeDocument(doc.Name)
doc = App.openDocument(path)
assert math.isclose(doc.Original.Shape.Volume, source.Volume, rel_tol=1e-10)
assert doc.Optimized.Shape.isValid()
assert math.isclose(doc.Optimized.Shape.Volume, optimized.Volume, rel_tol=1e-10)
App.closeDocument(doc.Name)
(root / "verified.json").write_text(json.dumps({"freecad_version": App.Version()[:3], "solver": config["solver"],
    "original_volume_mm3": source.Volume, "optimized_volume_mm3": optimized.Volume,
    "step_roundtrip": True, "stl_closed": True, "fcstd_roundtrip": True,
    "mounting_hole_preserved": True, "curved_boundary_rejected": True}), encoding="utf-8")
