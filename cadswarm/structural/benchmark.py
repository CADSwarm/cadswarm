"""Record analytical bending convergence; writes a new evidence JSON file."""
import argparse
import json
from pathlib import Path
import platform
import numpy as np
import scipy

from .geometry import Grid
from .model import Material, Load, Support
from .fem import ScipySolver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    material = Material("Benchmark aluminum", 70000, .3, 240, 2.7e-6)
    length, width, height, force = 48, 8, 8, 100
    inertia = width * height ** 3 / 12
    shear = material.young_mpa / (2*(1+material.poisson))
    theory = force*length**3/(3*material.young_mpa*inertia) + force*length/((5/6)*shear*width*height)
    rows = []
    for pitch in (4, 2, 1):
        shape = (int(length/pitch), int(width/pitch), int(height/pitch))
        result = ScipySolver().solve(Grid(np.ones(shape, bool), pitch), material,
                                     [Load("x_max", (0, 0, -force))], [Support("x_min")])
        rows.append({"pitch_mm": pitch, "elements": int(np.prod(shape)),
                     "max_displacement_mm": result.max_displacement_mm,
                     "relative_error_percent": abs(result.max_displacement_mm/theory-1)*100,
                     "max_stress_mpa": result.max_stress_mpa, "residual": result.residual})
    evidence = {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
                "reference": "Timoshenko beam, delta=FL^3/(3EI)+FL/(kGA), k=5/6; L=48, b=h=8 mm, F=100 N",
                "reference_displacement_mm": theory, "meshes": rows,
                "note": "Notch/clamp stress maxima need local refinement; this study checks global bending deflection only."}
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as f:
        json.dump(evidence, f, indent=2, allow_nan=False)
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
