# Lighten without losing stiffness

CadSwarm can take a solid part, fill its inside with a lattice that is **thicker where the part works hard**,
and check, by simulating the real lattice, that the part keeps the stiffness you asked for. People use
**Lighten part…** in FreeCAD; AI helpers use `lighten_part` and suggest it when a part could be lighter.

**Status: implemented** (engine, jobs, hub, MCP tools, FreeCAD dialog, tests). It runs on the computer that
hosts the team, inside FreeCAD's own Python (NumPy and SciPy, which FreeCAD ships); nothing is uploaded and
nothing new has to be installed.

**Provenance.** The workflow was studied from the public IMPLICIT web app (https://implicit.build), whose home
page headline is "Lighten without losing stiffness". The study was reference only, under a clean-room boundary:
see the [evidence dossier](../superpowers/research/2026-10-24-implicit-build-evidence.md). No IMPLICIT code,
assets or wording is used; TPMS and strut cells, Gibson–Ashby scaling, voxel finite elements and marching
tetrahedra are public mathematics, implemented here from scratch.

## What happens when you lighten a part

```
 1 prepare   voxelize the part; map its supports and loads onto the voxels
 2 simulate  solve the solid part (voxel finite elements) for its stress and stiffness
 3 grade     density follows the stress: thick walls where it is high, thin where it is low
 4 search    find the lightest density level whose stiffness meets the target
             (the lattice as an equivalent material from the calibrated cell map)
             if even the densest core misses, thicken the skin and try again
 5 verify    rebuild the real walls on a finer grid and solve again next to the solid part:
             stiffness kept, displacement x, stress x, safety factor; thicken and re-check if short
 6 build     final lattice = skin + solid zones at supports/loads + graded lattice, open faces cut
 7 mesh      watertight, oriented triangle mesh; STL, 3MF, a preview and a report
 8 apply     (on request) add it to the model as a mesh object next to the original
```

The **verified** stiffness (step 5, real walls) is what the report and the agents quote. The search uses the
calibrated cell map only to choose the design quickly.

## Cells

| Cell | Family | Good for |
|---|---|---|
| gyroid | TPMS sheet | the default: prints without supports, about isotropic |
| diamond | TPMS sheet | the stiffest smooth cell for its weight |
| iwp | TPMS sheet | very stiff, smaller pores |
| schwarzP | TPMS sheet | big pores; channels close above about 55 % density |
| octet | struts | the stiffest strut cell |
| bcc, fcc | struts | light, flexible, bending-dominated |
| cubic | struts | stiff only along its axes |

Each cell's density, stiffness and strength against wall thickness are in
`cadswarm/lattice/calibration.json`, measured by compressing blocks of cells with the same voxel solver
(`python -m cadswarm.lattice.calibration` regenerates it). These are simulations, not material tests.

## Where things are

| Piece | File |
|---|---|
| Engine (NumPy/SciPy) | `cadswarm/lattice/`: `cells`, `voxelize`, `vfem` (matrix-free hex8 + multigrid), `calibration`, `grade`, `build`, `loadcase`, `optimize`, `mc`, `pipeline` |
| FreeCAD runner (one-shot process) | `cadswarm/lattice/runner.py` |
| Jobs on the team server | `cadswarm/lattice_jobs.py` (helper ops `lattice_*`) |
| Hub routes | `cadswarm/space_hub.py` (`/lattice/...`) |
| Agent tools | `cadswarm/mcp_server.py`: `lighten_part`, `lattice_status`, `lattice_apply`, `lattice_cancel`, `lattice_cells`, `lattice_estimate` |
| People | `cadswarm/gui/lighten_ui.py`: **Lighten part…** (Team tools, the CadSwarm toolbar, the CADSwarm Optimization workbench) |
| Placing the result | worker op `insert_mesh` (a `Mesh::Feature`) |
| Tests | `tests/test_lattice.py`, `tests/test_lattice_freecad.py`, `tests/test_lighten_gui.py`, plus helper/hub tests |

Guides: [agent guide](agent-guide.md) (how helpers use and suggest it) and
[operator guide](operator-guide.md) (people, hosting, limits, troubleshooting).
The [contract](../superpowers/specs/2026-10-24-lattice-contract.md) and the
[original PicoGK plan](../superpowers/plans/2026-10-24-cadswarm-06-lattice.md) describe the earlier proposal; the
[implementation plan](../superpowers/plans/2026-10-24-cadswarm-07-lighten.md) records what was built and why.

## Honest limits

- Voxel models: curved edges become staircases, so peak stresses at holes and notches read low unless the
  voxel is small; the verify step reports how many voxels the thinnest wall gets.
- Linear elasticity, one static load case. No buckling, fatigue, plasticity or print anisotropy.
- One closed solid per job. Lattices are meshes, not B-rep solids.
- A design aid, never a certification. Print and test parts that matter.
