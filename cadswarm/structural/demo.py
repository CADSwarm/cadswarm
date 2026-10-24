"""Reproducible, explicitly assumed cantilever: python -m cadswarm.structural.demo."""
import argparse
import json
from pathlib import Path


def request():
    return {"demo_shape": [24, 8, 8], "pitch_mm": 3, "solver": "scipy",
            "material": {"name": "Demo aluminum (illustrative)", "young_mpa": 70000, "poisson": .33,
                         "yield_mpa": 240, "density_kg_mm3": 2.7e-6},
            "loads": [{"side": "x_max", "force_n": [0, 0, -200]}], "supports": [{"side": "x_min"}],
            "constraints": {"max_stress_mpa": 80, "max_displacement_mm": .05, "safety_factor": 2,
                            "target_reduction": .1, "max_stress_ratio": 1.25, "max_displacement_ratio": 1.15}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New directory; existing results are never overwritten")
    parser.add_argument("--solver", choices=("scipy", "calculix"), default="scipy")
    parser.add_argument("--freecad", action="store_true", help="Use actual FreeCAD geometry and export STEP/FCStd")
    args = parser.parse_args()
    from .job import run
    config = request()
    config["solver"] = args.solver
    if args.freecad:
        import FreeCAD as App
        import Part
        source = Part.makeBox(72, 24, 24)
        config["brep"] = source.exportBrepToString()
    result = run(config, args.output)
    if args.freecad:
        from .freecad import reconstruct
        doc = App.newDocument("StructuralDemo")
        doc.addObject("Part::Feature", "Original").Shape = source
        doc.addObject("Part::Feature", "Optimized").Shape = reconstruct(source, result.original, result.optimized)
        doc.recompute()
        doc.saveAs(str(Path(args.output).resolve() / "comparison.FCStd"))
        App.closeDocument(doc.Name)
    print(json.dumps({k: result.report[k] for k in ("status", "original_volume_mm3", "optimized_volume_mm3",
                                                  "material_removed_percent", "baseline", "optimized")}, indent=2))


if __name__ == "__main__":
    main()
