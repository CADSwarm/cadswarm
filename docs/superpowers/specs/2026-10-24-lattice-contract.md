# CadSwarm lattice contract (v1, proposed)

> **Implementation note (2026-10-24).** Implemented by plan 07. Differences from this proposal: apply creates a
> `Mesh::Feature` (not a `Part::Feature`); the request adds `grading`, `stiffness_target`, `fea_job_id`/`fixed`/
> `loads`, `auto_skin`; results carry `verify`; tools are `lighten_part`, `lattice_status`, `lattice_apply`,
> `lattice_cancel`, `lattice_cells`, `lattice_estimate`. See `docs/lattice/`.

**Status:** proposed contract for implementation. No code exists yet.  
**Applies to:** the current FastAPI/SQLite hub and, when it lands, the SpacetimeDB host.  
**Related documents:** [evidence dossier](../research/2026-10-24-implicit-build-evidence.md),
[implementation plan](../plans/2026-10-24-cadswarm-06-lattice.md),
[agent guide](../../lattice/agent-guide.md),
[operator guide](../../lattice/operator-guide.md).

---

## 1. Design rules

1. **Generation is a job; applying is a transaction.** A long lattice build never runs inside the FreeCAD
   worker and never holds the hub lock for its duration.
2. **The source is snapshotted atomically.** The source mesh and its revision are captured together while the
   hub lock is held. The job records both.
3. **Apply is revision-safe.** If the source changed after the snapshot, apply fails with `stale_source`; the
   result stays available for inspection or export but is never inserted into the model.
4. **Apply uses the existing lock and rollback rules.** The output name is created through the normal
   lock-checked mutation path. A failed import aborts and leaves the model untouched.
5. **Agents never pass filesystem paths.** The caller names a job and an output object. The hub resolves the
   job directory and the worker validates it.
6. **Everything is millimetres.** Source geometry is in the FreeCAD document's millimetre space; 3MF declares
   millimetres; STL is interpreted as millimetres.
7. **The engine is optional.** On a platform or install without the engine, the feature reports
   `lattice_unavailable` and the rest of CadSwarm works unchanged.
8. **The contract is additive.** It does not change existing op shapes, lock semantics or MCP tools.

---

## 2. Concepts

| Term | Meaning |
|---|---|
| **Source** | One existing `Part::Feature` object in the shared model whose `Shape` is a closed solid |
| **Job** | A request to generate a lattice result from a source snapshot and a parameter set |
| **Recipe** | The normalized parameters that fully determine the result (including seed and voxel size) |
| **Result** | A validated mesh plus metadata produced by the engine, stored under the job directory |
| **Apply** | Importing a completed result into the live model as a new `Part::Feature` |
| **Export** | Copying a completed result to the project's `exports/` folder as STL or 3MF |

A job is team-visible. Only its owner (or a human) may cancel, apply or export it.

---

## 3. Versioning

- `contract`: `"lattice/1"`
- `engine`: semantic version of the CadSwarm engine, for example `"0.1.0"`
- `picoGK`: the pinned PicoGK version, for example `"2.3.0"`
- Unknown request fields are rejected with `bad_args`. New optional fields require a contract minor revision.
- A job records the engine version that produced it. Applying a result produced by a different engine major
  version is refused with `engine_mismatch`; the caller regenerates.

---

## 4. Parameter schema

### 4.1 Request

```json
{
  "contract": "lattice/1",
  "source": "Bracket",
  "name": "Bracket_lattice",
  "label": "Bracket lightweight",
  "params": {
    "cell": "gyroid",
    "cell_size_mm": 6.0,
    "thickness_mm": 0.8,
    "skin_mm": 1.2,
    "open_faces": ["+z", "-z"],
    "voxel_mm": 0.4,
    "seed": 1,
    "layout": "regular",
    "frame": "box"
  }
}
```

### 4.2 Fields

| Field | Type | Required | Default | Rules |
|---|---|---|---|---|
| `source` | string | yes | — | Existing object name; must resolve to a closed solid |
| `name` | string | yes | — | New object name; must be a valid FreeCAD name after normalization; must not already exist in v1 |
| `label` | string | no | `name` | Human label for the result |
| `params.cell` | enum | yes | — | `gyroid`, `diamond`, `schwarzP`, `iwp`, `bcc`, `fcc`, `octet`, `cubic` in v1 |
| `params.cell_size_mm` | number | yes | — | `0.5 … 50`; must be finite; practical range 3–15 |
| `params.thickness_mm` | number | yes | — | `0.05 … 10`; TPMS sheet wall, beam diameter, or network neck/ligament |
| `params.skin_mm` | number | no | `0` | `0 … 20`; `0` means no skin |
| `params.open_faces` | string[] | no | `[]` | Unique values from `+x`, `-x`, `+y`, `-y`, `+z`, `-z`; only valid when `skin_mm > 0` |
| `params.voxel_mm` | number | yes | — | `0.1 … 5`; must be ≤ `cell_size_mm / 4`; warning below `thickness_mm / 2` |
| `params.seed` | integer | no | `1` | `0 … 2^31-1`; reserved for noisy/stochastic cells; regular v1 ignores it but records it |
| `params.layout` | enum | no | `regular` | v1 accepts only `regular`; `noisy` and `conformal` return `unsupported_layout` |
| `params.frame` | enum | no | `box` | v1 accepts only `box`; `cylinder` and `sphere` return `unsupported_frame` |

### 4.3 Normalization

- Reject booleans where numbers are expected.
- Reject `NaN`, `Infinity`, negative values, and numeric strings.
- Sort and de-duplicate `open_faces` in the recorded recipe; preserve the caller's order only in the response
  if useful for display.
- `thickness_mm` is the canonical field. Do not accept `wall_mm` or `beam_mm` aliases in v1; the agent guide
  explains the mapping. This keeps the contract unambiguous.
- `cell` determines the family: `gyroid`, `diamond`, `schwarzP`, `iwp` are TPMS sheet; `bcc`, `fcc`, `octet`,
  `cubic` are beams.
- The engine records the normalized recipe, not the raw request.

### 4.4 Validation warnings

Warnings do not fail a job unless marked **error**.

| Code | Severity | Meaning |
|---|---|---|
| `cell_too_few_voxels` | warning | `cell_size_mm / voxel_mm < 6`; the cell will be coarse |
| `thickness_too_few_voxels` | warning | `thickness_mm / voxel_mm < 2`; the wall/beam may disappear |
| `skin_too_thin` | warning | `0 < skin_mm < 2 * voxel_mm`; the skin may be porous |
| `closed_skin` | warning | `skin_mm > 0` and `open_faces` is empty; powder/resin may be trapped |
| `open_face_without_skin` | error | `open_faces` non-empty and `skin_mm == 0`; the request is contradictory |
| `large_part` | warning | Any bbox dimension > 300 mm, or estimated voxels > 100 million |
| `thickness_high` | warning | `thickness_mm / cell_size_mm > 0.35`; the lattice may be nearly solid |
| `thickness_low` | warning | `thickness_mm / cell_size_mm < 0.03`; the lattice may be too fragile to print |
| `source_has_multiple_solids` | error | v1 requires exactly one solid |
| `source_not_closed` | error | The source shape is not closed |
| `source_empty` | error | The source has no volume or no mesh |

---

## 5. Capabilities

`GET /lattice/capabilities` (and `lattice_capabilities`) returns:

```json
{
  "contract": "lattice/1",
  "engine": "0.1.0",
  "picoGK": "2.3.0",
  "platform": "win-x64",
  "available": true,
  "cells": [
    {"id": "gyroid", "family": "tpms", "label": "Gyroid"},
    {"id": "bcc", "family": "beam", "label": "BCC"}
  ],
  "defaults": {
    "cell_size_mm": 6.0,
    "thickness_mm": 0.8,
    "skin_mm": 1.2,
    "voxel_mm": 0.4,
    "seed": 1,
    "layout": "regular",
    "frame": "box"
  },
  "limits": {
    "min_cell_size_mm": 0.5,
    "max_cell_size_mm": 50.0,
    "min_thickness_mm": 0.05,
    "max_thickness_mm": 10.0,
    "max_voxels": 150000000,
    "max_source_bytes": 104857600,
    "max_result_bytes": 262144000,
    "max_job_seconds": 900,
    "max_concurrent_jobs": 1
  },
  "deferred": ["noisy", "conformal", "cylinder", "sphere", "grading", "voronoi", "delaunay", "spinodal"]
}
```

If the engine is not installed or the platform is unsupported:

```json
{
  "contract": "lattice/1",
  "available": false,
  "reason": "engine_missing",
  "detail": "The lattice engine is not installed on this computer.",
  "platform": "linux-x64"
}
```

`available: false` is not an error for the rest of CadSwarm. The tab shows the reason; tools return
`lattice_unavailable`.

---

## 6. Estimate

`POST /lattice/estimate` (and `lattice_estimate`) validates a request without creating a job.

```json
{
  "source": "Bracket",
  "params": { "cell": "gyroid", "cell_size_mm": 6, "thickness_mm": 0.8,
              "skin_mm": 1.2, "open_faces": ["+z", "-z"], "voxel_mm": 0.4 }
}
```

Response:

```json
{
  "ok": true,
  "normalized": {
    "cell": "gyroid", "cell_size_mm": 6.0, "thickness_mm": 0.8,
    "skin_mm": 1.2, "open_faces": ["+z", "-z"], "voxel_mm": 0.4,
    "seed": 1, "layout": "regular", "frame": "box"
  },
  "source": {
    "name": "Bracket", "rev": 7, "bbox": [0, 0, 0, 40, 30, 12],
    "volume_mm3": 8640.0, "solids": 1, "closed": true
  },
  "estimate": {
    "voxels": 12960000,
    "memory_mb": 420,
    "seconds": 22,
    "result_volume_mm3": 3200,
    "solid_volume_mm3": 8640,
    "relative_density": 0.37
  },
  "warnings": ["closed_skin"]
}
```

The estimate is advisory. The engine's measured time and memory are authoritative in the job result. If the
estimate exceeds a hard limit, the response is `ok: false` with code `too_large` and an actionable detail.

---

## 7. Job lifecycle

### 7.1 States

```text
queued ──► preparing ──► generating ──► packaging ──► done
   │           │              │              │
   └───────────┴──────────────┴──────────────┴──► failed
   │           │              │
   └───────────┴──────────────┴──► cancelled
done ──► expired   (artifacts removed by retention)
```

| State | Meaning |
|---|---|
| `queued` | Accepted; waiting for the single engine slot |
| `preparing` | Source mesh is being exported, hashed and validated |
| `generating` | The engine process is running |
| `packaging` | Output is being validated, hashed and written to the job record |
| `done` | Result is complete and can be applied or exported |
| `failed` | Terminal error; `error` and `error_code` explain why |
| `cancelled` | Terminal; requested by the owner or a human |
| `expired` | Result artifacts were removed by retention; metadata remains |

### 7.2 Create a job

`POST /lattice/jobs` (and `lattice_start`):

```json
{
  "source": "Bracket",
  "name": "Bracket_lattice",
  "label": "Bracket lightweight",
  "params": { "cell": "gyroid", "cell_size_mm": 6, "thickness_mm": 0.8,
              "skin_mm": 1.2, "open_faces": ["+z", "-z"], "voxel_mm": 0.4 }
}
```

Response `202 Accepted`:

```json
{
  "job_id": "lj_7f3a9c2d1e40",
  "status": "queued",
  "owner": "alice-claude",
  "source": {"name": "Bracket", "rev": 7, "sha256": "…"},
  "normalized": { "…": "…" },
  "warnings": ["closed_skin"],
  "created_at": "2026-10-24T18:22:31Z"
}
```

The source revision and SHA-256 are captured before the response. The job owns a private snapshot, so later
source edits do not change the running build; they only make apply fail.

### 7.3 Read a job

`GET /lattice/jobs/{job_id}` (and `lattice_status`):

```json
{
  "job_id": "lj_7f3a9c2d1e40",
  "status": "generating",
  "phase": "lattice",
  "progress": 0.62,
  "owner": "alice-claude",
  "source": {"name": "Bracket", "rev": 7, "sha256": "…"},
  "normalized": { "…": "…" },
  "created_at": "2026-10-24T18:22:31Z",
  "started_at": "2026-10-24T18:22:33Z",
  "finished_at": null,
  "warnings": ["closed_skin"],
  "result": null,
  "error": null,
  "error_code": null
}
```

When `done`:

```json
{
  "status": "done",
  "progress": 1.0,
  "result": {
    "stl": {"sha256": "…", "bytes": 2841192},
    "three_mf": {"sha256": "…", "bytes": 612430},
    "preview": {"sha256": "…", "bytes": 220114},
    "source_sha256": "…",
    "engine": "0.1.0",
    "picoGK": "2.3.0",
    "bbox": [0, 0, 0, 40, 30, 12],
    "volume_mm3": 3184.2,
    "relative_density": 0.368,
    "triangles": 184220,
    "vertices": 92112,
    "watertight": true,
    "seconds": 24.7,
    "peak_memory_mb": 438,
    "warnings": ["closed_skin"]
  }
}
```

`result` never contains an agent-usable filesystem path. The hub owns the path.

### 7.4 List jobs

`GET /lattice/jobs?limit=20` returns newest first:

```json
{
  "jobs": [
    {"job_id": "lj_…", "status": "done", "source": "Bracket", "name": "Bracket_lattice",
     "owner": "alice-claude", "cell": "gyroid", "created_at": "…", "finished_at": "…"}
  ]
}
```

All team members can list jobs. Result details are visible to all; destructive actions are not.

### 7.5 Cancel

`POST /lattice/jobs/{job_id}/cancel` (and `lattice_cancel`):

- Owner or human only.
- `queued` → `cancelled` immediately.
- `preparing`/`generating`/`packaging` → cancel requested, process tree killed within 2 seconds, then
  `cancelled`.
- `done`/`failed`/`cancelled` → `not_cancellable`.
- Cancellation never changes the model.

### 7.6 Apply

`POST /lattice/jobs/{job_id}/apply` (and `lattice_apply`):

```json
{
  "name": "Bracket_lattice",
  "label": "Bracket lightweight",
  "expected_revs": {"Bracket": 7}
}
```

`name` is required and must match the job's requested name unless the caller is a human changing it before
apply. `expected_revs` is optional; when present it is checked by the normal hub path as well as against the
job's recorded source revision.

Preconditions, in order:

1. Job exists and is `done`.
2. Caller is the owner, or a human member.
3. The job result files exist and their SHA-256 values match the job record.
4. The source still exists and its current revision equals `source.rev`.
5. The output name does not already exist (v1). If it does, `name_exists`; there is no silent replace.
6. `expected_revs`, if supplied, matches current revisions.
7. The caller may create the output name under the normal lock rules (a name reserved by someone else is
   refused).
8. The worker imports the mesh, converts it to a closed solid, validates volume and bbox, and creates the
   `Part::Feature` inside a transaction.

Success reuses the normal commit shape:

```json
{
  "result": {"name": "Bracket_lattice", "volume": 3184.2, "triangles": 184220},
  "created": ["Bracket_lattice"],
  "modified": [],
  "deleted": [],
  "recomputed": [],
  "broken": [],
  "revs": {"Bracket_lattice": 1},
  "seq": 142,
  "notified": {},
  "job_id": "lj_7f3a9c2d1e40"
}
```

Failure codes are in §10. A failed apply leaves no object and no lock change.

### 7.7 Export

`POST /lattice/jobs/{job_id}/export` (and `lattice_export`):

```json
{"format": "3mf", "label": "Bracket lightweight"}
```

Response:

```json
{"file": "Bracket lightweight 2026-10-24 18.31.3mf", "url": "/exports/…", "bytes": 612430}
```

- `format`: `3mf` (default) or `stl`.
- Owner or human only.
- Uses the project's existing export naming, disk guard and storage accounting.
- Does not modify the model.

---

## 8. Source snapshot semantics

The hub performs the snapshot in one critical section:

1. Read the source object's current revision from the hub database.
2. Call the FreeCAD worker query `lattice_source` with the source name and a tessellation tolerance derived
   from `voxel_mm`.
3. The worker exports the source's world-space shape to `source.stl` in the job directory and returns
   `sha256`, `bbox`, `volume`, `solids`, `closed`, `triangles`.
4. Write the job row with `source.rev` and `source.sha256`.
5. Release the lock; queue the engine.

Because the worker serializes mutations, no mutation can run between steps 1 and 3 while the hub lock is held.
The snapshot is therefore consistent with the recorded revision.

If the source is modified after the snapshot, the job still completes. Apply fails with `stale_source` and
includes both revisions:

```json
{
  "error": "Bracket changed after this lattice was generated (now rev 9, job used rev 7). Generate again or apply the result as an export.",
  "code": "stale_source",
  "job_source_rev": 7,
  "current_rev": 9
}
```

This is intentional: a long generation must not block the team, and a stale result must never overwrite newer
geometry.

---

## 9. Geometry semantics

### 9.1 Source

- Exactly one `Part::Feature` with at least one solid in v1.
- World-space placement is applied; the result is in the same coordinate system.
- The source's shape is tessellated to STL at a tolerance `min(0.2 mm, voxel_mm / 4)`.
- The source is not modified, hidden or deleted by the job or by apply.

### 9.2 Lattice

- The source voxel field is the bounding region.
- A TPMS sheet cell is a constant-thickness wall around the chosen implicit surface.
- A beam cell is a network of cylindrical struts along the chosen unit-cell graph.
- The cell array is regular and fitted to the source bounding box in v1.
- The lattice is intersected with the source volume, so it never extends outside the part.
- `thickness_mm` is uniform in v1.
- `seed` is recorded but has no effect on regular cells.

### 9.3 Skin

- `skin_mm == 0`: no shell; the lattice is simply intersected with the source.
- `skin_mm > 0`: a shell of that thickness is kept on the source boundary; the lattice fills the interior.
- The shell and lattice are unioned into one solid.
- `open_faces` cuts windows through the shell on the named sides of the source bounding box. A window is a
  rectangular opening inset from the face edges; the exact inset is an engine constant and is reported in the
  result metadata. The lattice remains inside the source.
- Open faces require a skin. `open_faces` with `skin_mm == 0` is an error.
- Opposite faces are recommended for powder removal; the UI and agent guide say so, but do not enforce it.

### 9.4 Result validation

The engine and the worker both validate. A result is accepted only if:

- triangle count > 0 and vertex count > 0;
- all coordinates are finite;
- bbox is within the source bbox expanded by `2 * voxel_mm`;
- mesh volume is positive and not greater than the source volume plus tolerance;
- the mesh is closed/manifold by an edge-count check;
- the STL and 3MF hashes match what apply reads.

If any check fails, the job fails with `mesh_invalid` or `not_closed`; apply refuses with the same code.

---

## 10. Error codes

| Code | HTTP | Meaning / action |
|---|---:|---|
| `lattice_unavailable` | 503 | Engine missing, unsupported platform, or disabled |
| `engine_missing` | 503 | Capability detail; install the engine |
| `engine_mismatch` | 409 | Result from an incompatible engine major version; regenerate |
| `bad_args` | 400 | Invalid or unknown field; read the detail |
| `unsupported_cell` | 400 | Cell not in capabilities |
| `unsupported_layout` | 400 | Only `regular` in v1 |
| `unsupported_frame` | 400 | Only `box` in v1 |
| `source_not_found` | 404 | Source object does not exist |
| `source_not_solid` | 422 | Source has no closed solid |
| `too_large` | 413 | Estimate exceeds voxel/source/result limits |
| `no_room` | 507 | Disk guard or project budget refused the job |
| `not_owner` | 403 | Only the owner or a human may cancel/apply/export |
| `job_not_found` | 404 | Unknown job id |
| `job_not_done` | 409 | Apply/export before `done` |
| `job_failed` | 409 | Job ended in `failed`; read `error_code` |
| `not_cancellable` | 409 | Job already terminal |
| `stale_source` | 409 | Source revision changed since the snapshot |
| `name_exists` | 409 | Output name already exists; choose another |
| `not_locked` | 409 | Existing lock rule; lock the output name if required |
| `locked` | 409 | Name reserved by another participant |
| `stale_rev` | 409 | Existing expected-revision rule |
| `mesh_invalid` | 422 | Result failed geometric validation |
| `not_closed` | 422 | Result is not a closed solid |
| `timeout` | 504 | Engine exceeded the job time limit |
| `cancelled` | 409 | Job was cancelled |
| `out_of_memory` | 507 | Engine hit its memory limit |
| `engine_failed` | 500 | Engine crashed or returned an unreadable result |
| `path_rejected` | 400 | Internal path failed the job-directory whitelist |

Tool responses keep the existing CadSwarm shape:

```json
{"error": "…actionable text…", "code": "stale_source", "current_rev": 9}
```

---

## 11. Storage and retention

```text
<project>/.cadswarm-team/lattice/
  jobs.sqlite
  jobs/
    lj_7f3a9c2d1e40/
      request.json
      source.stl
      result.stl
      result.3mf
      preview.stl
      result.json
      engine.log
```

Rules:

- `jobs.sqlite` is separate from `hub.sqlite`, WAL mode, one writer.
- Job directories are private to the host process; agents never receive their paths.
- The hub checks the disk guard before creating a job and before packaging a result.
- Default cache limits: 5 GB or 7 days, whichever is reached first; at most 50 retained jobs.
- Applied jobs keep their metadata but may have artifacts removed after 24 hours. Exports in `exports/` are
  independent and are never deleted automatically.
- Cancelled, failed and expired jobs are cleaned before done jobs.
- Retention runs at hub start and hourly; it never deletes the `exports/` folder.
- Storage status reports a `lattice_cache` line separately from files, old versions, snapshots and exports.

---

## 12. Security and resource limits

- The engine runs as the host user, in its own process, with no network requirement.
- On Windows, launch it in a Job object with a memory cap and kill-on-close; on macOS, use a process group and
  resource limits. This mirrors the existing script sandbox's operational pattern, but does not reuse its
  code.
- The engine reads only its job directory; it writes only `result.stl`, `result.3mf`, `preview.stl`,
  `result.json` and `engine.log`.
- `source.stl` is produced by the trusted FreeCAD worker, not by an agent.
- The worker accepts `lattice_import` only for a job directory resolved by the hub and validates the path with
  `realpath` containment, the result SHA-256 and the job id.
- `lattice_import` is not a public `/ops` op. Agents cannot pass a path, a hash or a job directory.
- Time limit default 900 s; memory limit default 8 GB; stdout/stderr capture capped at 256 KB each.
- Result cap default 250 MB; source cap 100 MB.
- One engine job at a time in v1; additional jobs queue. This protects laptops and keeps behaviour predictable.
- The feature can be disabled with `CADSWARM_LATTICE=off`; capabilities then report `available: false` with
  `reason: "disabled"`.

---

## 13. Events and activity

The hub emits normal events so humans and agents see lattice work in the team timeline:

| Event | Data |
|---|---|
| `lattice_started` | `job`, `source`, `name`, `cell`, `owner` |
| `lattice_progress` | `job`, `phase`, `pct` (rate-limited to at most one per 2 s) |
| `lattice_done` | `job`, `source`, `name`, `volume`, `relative_density`, `seconds` |
| `lattice_failed` | `job`, `code`, `error` |
| `lattice_cancelled` | `job` |
| `lattice_applied` | `job`, `name`, `created`, `revs` |
| `lattice_exported` | `job`, `format`, `file` |
| `lattice_expired` | `job` |

`lattice_applied` also produces the normal `op` event through `_commit`. Activity log entries use
`tool_call`/`tool_result` with a short summary; they never include filesystem paths.

---

## 14. SpacetimeDB port

When the SpacetimeDB migration lands first, preserve the agent-visible contract exactly:

- Add `lattice_job` and `lattice_artifact` tables (private) and `v_lattice_job`/`v_lattice_artifact` views.
- The Rust host owns the engine process and job queue, just as it owns the FreeCAD worker.
- Reducers: `start_lattice`, `cancel_lattice`, `apply_lattice`, `export_lattice`, `put_lattice_result`.
- The source revision check is a reducer precondition, using the same `cad_object.rev` values.
- `apply_lattice` queues an op that the host runs through the worker; `complete_op` writes the normal commit
  shape.
- MCP tool names and JSON shapes do not change. The Python `SpaceHub` routes `/lattice/*` to the reducers.
- Job/artifact retention is a host responsibility; the module stores metadata and hashes, not large blobs.

---

## 15. Contract test vectors

These are the minimum cases the implementation must pass.

| # | Case | Expected |
|---|---|---|
| 1 | 30×20×10 mm box, gyroid, 6 mm cell, 0.8 mm wall, no skin, 0.5 mm voxel | `done`; closed; bbox within 0.2 mm; relative density 0.2–0.6 |
| 2 | Same box, diamond | `done`; closed; different relative density from gyroid |
| 3 | Same box, BCC, 0.9 mm thickness | `done`; closed; beam network present |
| 4 | Same box, skin 1.2 mm, no open faces | `done`; warning `closed_skin`; volume greater than no-skin case |
| 5 | Same box, skin 1.2 mm, open faces `+z`, `-z` | `done`; shell has two openings; no `closed_skin` warning |
| 6 | `open_faces` with `skin_mm = 0` | `bad_args` / `open_face_without_skin` |
| 7 | Cell 1 mm, voxel 0.5 mm | warning `cell_too_few_voxels`; still allowed |
| 8 | 500 mm cube, 0.2 mm voxel | `too_large` with a suggested coarser voxel |
| 9 | Source edited between start and apply | `stale_source`; no model change |
| 10 | Source locked by another participant, output name reserved | `locked`; no model change |
| 11 | Apply twice | second `name_exists`; first object unchanged |
| 12 | Result STL corrupted on disk | `mesh_invalid`; no model change |
| 13 | Cancel while generating | `cancelled`; no child process; no model change |
| 14 | Engine missing | capabilities `available: false`; tools `lattice_unavailable` |
| 15 | Same recipe and seed twice | geometric statistics within 1%; no byte-equality requirement |

---

## 16. Compatibility notes

- Existing MCP tools and op kinds are unchanged. `lattice_import` is a hub-internal op, not a new public op.
- Existing lock rules apply at apply time. Generating does not require a lock; applying creates a new object
  under the normal creator-lock rule.
- Existing export URLs and storage pages continue to work. Lattice exports use the same `exports/` folder.
- Existing tests that enumerate op kinds or MCP tools will need an explicit expected update; that is part of
  the implementation plan, not this contract.
- The contract is intentionally narrower than IMPLICIT: one source, one output, regular box layout, uniform
  thickness, eight cells. Every deferred feature has a named extension point.
