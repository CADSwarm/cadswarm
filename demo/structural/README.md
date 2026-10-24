# Recorded structural optimization demonstration

For the installed FreeCAD application, see the later [visible desktop verification](desktop-verification.md), including mouse-driven CalculiX runs, reject/accept, saved geometry checks and the latest regression results.

Actual execution on Windows with FreeCAD 1.1.4, bundled Python 3.11, NumPy 1.26.4, SciPy 1.16.3 and CalculiX 2.22. The official portable FreeCAD archive was SHA-256 checked before use. Executables and generated CAD files are not vendored. Reproduce with `python -m cadswarm.structural.demo --freecad --solver calculix --output <new-directory>` using FreeCAD's Python and the environment described in [setup](../../docs/structural-optimization.md).

The model is an exact 72 × 24 × 24 mm rectangular cantilever, 3 mm grid pitch (1,536 original C3D8 elements), fully fixed on x-min and carrying a uniform total force of -200 N along z on x-max. Illustrative aluminum properties: E = 70,000 MPa, ν = 0.33, yield = 240 MPa, density = 2.7e-6 kg/mm³. Limits: 80 MPa, 0.05 mm, factor of safety 2, maximum stress ratio 1.25 and displacement ratio 1.15. Target removal: 10%.

| Actual CalculiX result | Original | Optimized |
|---|---:|---:|
| CAD and FEM volume (mm³) | 41,472 | 36,126 |
| Mass (kg) | 0.1119744 | 0.0975402 |
| Peak integration-point von Mises stress (MPa) | 5.816849939 | 6.275724354 |
| Maximum displacement (mm) | 0.01381871861 | 0.01476722402 |
| Applied-load-direction reaction (N) | 200.00001995 | 199.99999816 |

**Material removed: 12.890625%.** Three cavity candidates were freshly solved by CalculiX and passed the specified checks. The discrete cavity widths exceeded the 10% target; the actual removal is reported rather than rounded to the request. All 744 protected cells remain. The source CAD shape remained unchanged. BREP/STEP reconstruction produced one valid solid with exactly 36,126 mm³ volume. `comparison.FCStd` was created by the demo.

The independent SciPy solver gave stress 5.816850059 → 6.275724493 MPa and displacement 0.01381871732 → 0.01476722018 mm on the same geometry. Agreement checks the two implementations; it is not independent mesh-convergence evidence because both use the same C3D8 discretization.

Machine-readable evidence: [complete CalculiX report and candidate audit](calculix-report.json), [readable report](calculix-report.md), [baseline solver log](calculix-baseline.log), [stress visualization](stress.svg).

## Analytical benchmark

The separate 48 × 8 × 8 mm cantilever with a 100 N tip-face force, E = 70,000 MPa and ν = 0.3 has a Timoshenko tip-deflection reference of **0.1576285714 mm** (`FL³/(3EI) + FL/(kGA)`, `k=5/6`). Actual SciPy results:

| Grid pitch (mm) | Elements | Maximum displacement (mm) | Relative reference error |
|---|---:|---:|---:|
| 4 | 48 | 0.1366875187 | 13.2851% |
| 2 | 384 | 0.1510155831 | 4.1953% |
| 1 | 3,072 | 0.1553860414 | 1.4227% |

[Full benchmark data](benchmark.json) records Python/library versions, residuals and stress maxima. Refinement reduces the deflection error as expected. Peak clamp stress increases under refinement and is **not claimed converged**. The demo cavity's local stress convergence and manufactured-part performance remain unverified.

## Verification scope

Final repository regression run: **246 tests passed in 127.92 seconds**, with real FreeCAD, CalculiX and the offscreen Qt test enabled. Python compilation and Git whitespace checks also pass. The 33 new tests are included in that total.

Automated tests exercise axial patch response and energy balance, bending convergence, cross-section reduction, preservation of skin/mounting holes/boxes, no load, overlap, nonfinite/missing material, singular and missing solver failures, infeasible constraints, cancellation, actual candidate re-solves and export volume. Real FreeCAD tests round-trip STEP and FCStd, check closed STL, reject curved-hole approximation and preserve a rectangular through-hole. A two-human hub test checks accepted-only creation, reserved names, teammate ownership and stale revisions. An actual offscreen Qt test exercises preview, accept, reject and cancellation.

Offscreen Qt cannot provide an OpenGL viewport on this host; GUI state and CAD contents are checked, but rendered 3D appearance and an interactive two-computer session are not verified. Rust/SpacetimeDB build and live module smoke checks require Cargo and a local published SpacetimeDB instance, absent on this host; those modules are unchanged.

These are finite-mesh linear-static results under one specified load case, not structural certification. Curved geometry, arbitrary selected faces, buckling, fatigue, contact, plasticity, manufacturing access and multiple independent load cases are outside this release.
