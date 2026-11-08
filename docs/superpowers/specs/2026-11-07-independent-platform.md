# CADSwarm independent platform

Status: Windows-first standalone alpha implementation on `feat/standalone-app`.

## Product direction

CADSwarm owns its desktop application, parametric document, modeling workflow, MCP interface, and multiplayer
integration. The app does not launch, embed, or require FreeCAD. FreeCAD and Onshape are behavioral references;
the retained FreeCAD worker and guide are historical references only. New geometry uses build123d/OpenCascade,
and the sketch constraint layer is CADSwarm-owned with SciPy as its numerical solver. There is no OpenAI API
integration.

## Implemented alpha

- Native PySide6 workspace with a depth-buffered OpenGL viewport, orbit/pan/zoom, object picking, feature tree,
  property editing, sketch canvas, command search, and a CADSwarm `.cadswarm` project flow.
- Versioned JSON feature history with stable IDs, millimetre units, shared Params expressions, atomic writes,
  file locks, revision numbers, and 50-entry undo/redo history.
- Solver-backed line/circle/arc sketches with coincident, horizontal, vertical, parallel, perpendicular, equal,
  angle, distance, radius/diameter, concentric, and fixed constraints; DOF, residual, and solve status are stored.
- Parametric primitives, extrude, revolve, pocket, hole, fillet, chamfer, linear/circular patterns, booleans,
  placement, STEP import/export, and STL mesh reference import/export.
- Local and team MCP operations for sketch and feature changes. The Rust host defaults to the CADSwarm
  JSON-lines worker and rejects FreeCAD runtime configurations. Worker mutations enforce shared object locks,
  including inputs and descendants for destructive edits. Solo MCP writes are refused while a team host is active.
- SpacetimeDB team hosting, invites, chat, browser/phone presence, task plans, locks, and Claude/Codex/OpenCode
  handoff remain in the collaboration path. Windows quick install creates an isolated Python environment,
  installs Python for the current user when needed, and adds app shortcuts without installing FreeCAD.

## Alpha limits

- While multiplayer is active, the desktop host is live but read-only. Shared geometry edits must use the team
  MCP operations so they acquire collaboration locks; a lock-aware desktop editing client remains future work.
- Fillet/chamfer references use edge indices that are valid only for the current model revision. Refresh source
  geometry after upstream edits. Sketch arcs are approximated during profile conversion.
- Existing FreeCAD documents and old FreeCAD team folders are not supported or migrated. STEP is the import
  path for exact solids; STL is a mesh reference.
- 3MF, portable project archives, recovery snapshots, assemblies, stable topological references, sections,
  drawings, FEA, lattice optimization, and the previous FreeCAD-dependent library analysis are deferred.
- Windows is the first packaged target. macOS and Linux installers are intentionally unavailable in this alpha.

## Interfaces and references

The public project file is `.cadswarm`. The shared worker exposes model status, JSON-lines operations, preview
meshes, and STEP/STL export; local and team MCP servers expose matching sketch and feature commands. The team
worker must remain the only writer while a project is hosted. FreeCAD source is a workflow/behavior reference,
not a linked runtime or copied solver implementation: [FreeCAD repository](https://github.com/FreeCAD/FreeCAD).
Onshape sketch behavior is a UX reference: [Sketch basics](https://cad.onshape.com/help/Content/Sketch/sketch_basics.htm),
[constraints](https://cad.onshape.com/help/Content/Sketch/working_with_constraints.htm).

build123d supplies direct OpenCascade geometry and exchange operations: [installation](https://build123d.readthedocs.io/en/stable/installation.html),
[shape API](https://build123d.readthedocs.io/en/stable/direct_api_reference.html),
[import/export](https://build123d.readthedocs.io/en/stable/import_export.html). Its STL importer returns a mesh
reference rather than an editable exact solid.
