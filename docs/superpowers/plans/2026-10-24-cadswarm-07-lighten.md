# CadSwarm 07: Lighten without losing stiffness (implemented)

**Supersedes** `2026-10-24-cadswarm-06-lattice.md` (the .NET 9 + PicoGK proposal, whose feasibility spike was
never run). The earlier lattice contract still names the routes, job states and warnings used here; where this
document and the contract differ, this one describes the code.

## Why

IMPLICIT's headline capability ("Lighten without losing stiffness") is the whole chain of simulate → lattice
graded by the stress → verify, not just lattice generation. The earlier plan deferred exactly the grading and
the verify. Re-reading the live public bundles (build `index-DbJnvFnT.js`, `hooks-DCBEHMR6.js`,
`Panel-DvOaFFGB.js`; see the dossier addendum) confirmed the parts that matter:

- the lattice "gets thicker where the stress is high" ("Optimised (simulation)" thickness, "calibrated cell
  map", `E*/E0 = C·rho^n` per cell);
- a library picks cell and wall "by target stiffness or density";
- Verify repeats the same load case on the latticed part at a voxel fine enough for the walls and reports
  "lattice / original: stress x, displacement x, mass %".

## Decisions

| Question | Choice | Reason |
|---|---|---|
| Engine | Own NumPy/SciPy pipeline in FreeCAD's bundled Python | Runs on Windows, macOS and Linux with nothing to install; reuses FEA load cases; no .NET packaging |
| Cells | gyroid, diamond, schwarzP, iwp; bcc, fcc, octet, cubic | The contract's v1 list |
| Result | `Mesh::Feature` plus STL/3MF/report in project files | Lattices are millions of triangles: too many for a B-rep solid |
| Solver | Matrix-free hex8 on the voxel grid, CG with a geometric multigrid V-cycle | Every voxel shares one element matrix; multigrid cut lattice solves from ~2,200 to ~30 iterations |
| Search | Bisection on one density level, lattice as an equivalent continuum (calibrated map) | Smooth, fast, monotone; resolution-independent |
| Verify | Real walls on a finer grid, solved next to the solid on the same grid | The number quoted to people is a geometric simulation, not a map lookup |
| Skin | Thickened automatically when even the densest core misses the target | In bending the skin carries most stiffness (a 1.5 mm skin on a 20 mm bar caps a gyroid core at ~66 %) |

## Measured

- Voxel FEM: uniaxial bar +0.6 %, cantilever within 1 % of Timoshenko.
- Mesher: watertight and oriented; a sphere's volume within 0.2 %; voxelizing its own mesh reproduces the
  occupancy exactly.
- Calibration (2×2×2 cells, 20 voxels per cell): e.g. gyroid E*/E0 = 0.075 at rho = 0.30; Schwarz P softest,
  diamond and I-WP stiffest TPMS; cubic ≈ rho/3 along its struts.
- A 60 × 20 × 20 mm aluminium cantilever, 90 % target: skin raised to 5 mm, verified 90.6 % stiffness
  (444 k elements, 29 iterations), 14.7 % lighter, 69 s end to end.

## Done

Engine (`cadswarm/lattice/`), runner, jobs (`lattice_jobs.py`), helper ops, worker `insert_mesh`, rules and
host op kinds, hub routes (`/lattice/*`, apply with `stale_source`), MCP tools and instruction 14, the prompt
hint, the FEA "strength to spare" suggestion, the **Lighten part…** dialog (team and local), docs and tests
(engine, real FreeCAD, offscreen GUI, helper, hub).

## Later

Noisy and conformal layouts, cylinder/sphere frames, stochastic cells, several load cases, buckling,
re-grading from the lattice's own stress, and a lattice preview in teammates' FreeCAD mirrors (the web and
phone previews already decimate the mesh).
