# Lighten without losing stiffness — people and hosting

## Using it in FreeCAD

1. Select one solid part. In a team, select it in the live model; on your own, any document works (choose
   the **CADSwarm Optimization** workbench, or the CADSwarm toolbar).
2. **Lighten part…** in the CADSwarm panel's **Tools** tab (or the CADSwarm menu).
3. **Part**: the material and how it's made. Powder or resin printing ticks two opposite openings so loose
   material can drain.
4. **Held and loaded**: type faces (`xmin`, `Face6`) or select them in the 3D view and click
   **Use selected faces**; give the force in newtons (10 N ≈ 1 kg). Or choose **No load case** for an even,
   unchecked lattice.
5. **Lattice**: the cell (**Help me choose…** ranks them), cell size, wall range, solid skin, openings, and how
   much stiffness to keep (90 % by default). **Check these settings** shows warnings, a rough time, and warns
   when the STL would be too big to save (the project files take at most 512 MB per file: use a larger
   **Detail** voxel, a larger cell, or a smaller part).
6. **Run and review**: **Lighten it**. It runs in the background; **Stop** cancels. A progress console shows
   each step with the time, including the verify's solver iterations, and the elapsed time since the last
   update. You get the weight saving, the verified stiffness, how much more it bends, and any warnings.
   **Show it** opens a preview next to the original; **Cut in half (view only)** shows the inside. **Add to
   model** puts the lattice in as a mesh; the original part is not changed. **Open files** shows the STL,
   3MF and report.

## What it needs

- FreeCAD 1.x with its bundled Python (NumPy and SciPy are included). Nothing else to install.
- On a team, jobs run on the hosting computer, one at a time; others queue.
- The SpacetimeDB module must be the build that knows the `lattice_*` and `insert_mesh` op kinds
  (see `docs/publishing-spacetimedb.md`).

## Limits and timing

| Limit | Value |
|---|---|
| Build voxels | 24 M (the voxel is enlarged beyond, and the report says so) |
| Search grid | about 60 k elements |
| Verify grid | up to 450 k elements, finer when the thinnest wall needs it |
| Job timeout | 40 min (`CADSWARM_LATTICE_TIMEOUT`, seconds) |

A 40 × 12 × 12 mm bracket takes about 40 s; a 60 × 20 × 20 mm part about 70 s on a laptop.

## Where results go

`files/lattice/<date> <name>/` in the project: `result.stl`, `result.3mf` (millimetres), `preview.stl`,
`report.json`, `report.md`. They are ordinary project files: versioned, shared, and counted against storage.
Project files take at most 512 MB each; a big part at a fine **Detail** can make a larger STL, so check the
warning in **Check these settings** before a long run. If saving still fails, the job is marked failed but
its files stay in the job folder (the message says where), so a long run is never silently thrown away.

## Troubleshooting

| Message | Meaning / fix |
|---|---|
| `source_not_closed`, `source_has_multiple_solids` | Lighten one closed solid at a time. |
| `no_passing_design` | Even the densest lattice missed the stiffness target. Lower it, use diamond/iwp/octet, allow thicker walls or a smaller cell. |
| `load_off_part` | The loaded faces aren't connected to the held faces through material (check the selectors). |
| `verify_coarse` warning | The thinnest wall gets under two verify voxels: the verified numbers are indicative. Use a thicker minimum wall or a smaller part. |
| `stale_source` on Add to model | The part changed after the job started. Run it again, or add it anyway. |
| `too_big` / `publish_failed` | The mesh is over the project's 512 MB per-file limit (the 276 MB STL at 0.36 mm detail fits; a finer **Detail** or a bigger part can exceed it). The job keeps its files in the job folder and says where; rerun with a larger **Detail** voxel or a larger cell. |
| `timeout`, `out_of_memory` | Use a larger voxel (**Detail**) or a larger cell. |
| `lattice_unavailable` | The team server can't find FreeCAD's Python. |

Set `CADSWARM_LATTICE_KEEP=1` on the host to keep each job's working folder (job.json, progress, runner.log).
A job that could not be saved into project files keeps its output folder automatically.
