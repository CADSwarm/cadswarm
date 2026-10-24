# CADSwarm structural analysis

Improvement found; awaiting acceptance

Solver: CalculiX C3D8 (2x2x2 integration)

| Measurement | Original | Optimized |
|---|---:|---:|
| Volume (mm³) | 41472 | 36126 |
| Mass (kg) | 0.1119744 | 0.0975402 |
| Peak integration-point stress (MPa) | 5.8168499 | 6.2757244 |
| Maximum displacement (mm) | 0.013818719 | 0.014767224 |

Material removed: 12.8906%. Target reached: True.
Effective stress limit: 80 MPa; displacement limit: 0.05 mm.
Protected cells preserved: True (744 cells).
Baseline constraint failures: none.

## Inputs

```json
{
  "material": {
    "name": "Demo aluminum (illustrative)",
    "young_mpa": 70000,
    "poisson": 0.33,
    "yield_mpa": 240,
    "density_kg_mm3": 2.7e-06
  },
  "loads": [
    {
      "side": "x_max",
      "force_n": [
        0,
        0,
        -200
      ]
    }
  ],
  "supports": [
    {
      "side": "x_min"
    }
  ],
  "constraints": {
    "max_stress_mpa": 80,
    "max_displacement_mm": 0.05,
    "safety_factor": 2,
    "target_reduction": 0.1,
    "max_stress_ratio": 1.25,
    "max_displacement_ratio": 1.15,
    "skin_cells": 1,
    "max_trials": 24
  },
  "grid": {
    "shape": [
      24,
      8,
      8
    ],
    "pitch_mm": 3,
    "origin": [
      0.0,
      0.0,
      0.0
    ]
  }
}
```

## Candidate audit

| Trial | Solver completed | Accepted | Outcome |
|---|---|---|---|
| 1 | True | True | constraints passed |
| 2 | True | True | constraints passed |
| 3 | True | True | constraints passed |

## Interpretation and limitations

- One combined static load case; homogeneous isotropic linear elasticity and small displacement.
- Exact grid-aligned solids only; curved and oblique boundaries are rejected.
- Full integration linear bricks can be too stiff in bending; perform mesh convergence for each part.
- Stress is the peak at eight integration points, not a resolved notch or singular boundary stress.
- No buckling, fatigue, contact, plasticity, thermal or additive-manufacturing anisotropy analysis.
- Internal cavities may be enclosed; printability and machining access are not evaluated.
- Passing these finite-mesh checks is not structural certification or a guarantee of safety.

`stress.svg` shows the central z cell layer on one shared MPa scale. White cells are voids.
The heatmap is a visualization of the numerical results, not evidence of structural safety.
