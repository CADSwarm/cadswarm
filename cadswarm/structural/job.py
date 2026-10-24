"""Dedicated subprocess entry point. FreeCAD and solver never block the live CAD worker."""
import json
from pathlib import Path
import sys


def run(request, output):
    from .model import Material, Load, Support, Constraints
    from .geometry import Grid
    from .engine import optimize
    from .fem import ScipySolver
    from .calculix import CalculixSolver
    from .report import save_result
    import numpy as np

    source = None
    if "brep" in request:
        import FreeCAD  # noqa: F401. Initializes the Part module search paths
        import Part
        from .freecad import discretize, reconstruct, preserve_boxes
        source = Part.Shape()
        source.importBrepFromString(request["brep"])
        grid = discretize(source, request["pitch_mm"])
        preserve = preserve_boxes(grid, request.get("preserve_boxes", []))
    else:
        grid = Grid(np.ones(request["demo_shape"], dtype=bool), request["pitch_mm"])
        preserve = None
    material = Material(**request["material"])
    loads = [Load(**v) for v in request["loads"]]
    supports = [Support(**v) for v in request["supports"]]
    constraints = Constraints(**request["constraints"])
    backend = request["solver"]
    if backend not in ("scipy", "calculix"):
        raise ValueError("solver must be scipy or calculix")
    solver = CalculixSolver(evidence_dir=str(output) + "-solver") if backend == "calculix" else ScipySolver()
    result = optimize(grid, material, loads, supports, constraints, preserve=preserve, solver=solver,
                      progress=lambda message: print(message, flush=True),
                      cancel=lambda: bool(request.get("cancel_path") and Path(request["cancel_path"]).exists()))
    candidate = reconstruct(source, grid, result.optimized) if source is not None else None
    out = save_result(result, output)
    if candidate is not None:
        candidate.exportBrep(str(out / "optimized.brep"))
        candidate.exportStep(str(out / "optimized.step"))
        result.report["cad"] = {"original_volume_mm3": source.Volume, "optimized_volume_mm3": candidate.Volume,
                                "valid_single_solid": True, "step_exported": True}
        (out / "report.json").write_text(json.dumps(result.report, indent=2, allow_nan=False), encoding="utf-8")
    return result


def main():
    request_path, output = map(Path, sys.argv[1:3])
    try:
        run(json.loads(request_path.read_text(encoding="utf-8")), output)
    except Exception as exc:
        # A separate failure record cannot be mistaken for a passing simulation report.
        request_path.with_suffix(".error.json").write_text(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
