# CADSwarm lattice — agent guide

**Status:** proposed, 2026-10-24. **The tools described here do not exist yet.** This guide is written against
the [lattice contract v1](../superpowers/specs/2026-10-24-lattice-contract.md) so the tools can be implemented
and tested against a stable description. Do not call these tools until `lattice_capabilities` is present in your
tool list.

**Audience:** AI helpers (Claude Code, Codex) working in a CADSwarm team.
**Related:** [contract](../superpowers/specs/2026-10-24-lattice-contract.md) ·
[implementation plan](../superpowers/plans/2026-10-24-cadswarm-06-lattice.md) ·
[evidence dossier](../superpowers/research/2026-10-24-implicit-build-evidence.md) ·
[operator guide](operator-guide.md) · [documentation index](README.md)

---

## 1. What this gives you

You can turn one closed solid in the shared FreeCAD model into a lighter latticed version of itself:

- choose a standard TPMS sheet cell or a beam cell;
- set cell size, uniform wall/strut thickness, skin thickness and open faces;
- generate the result as a background job;
- inspect volume, relative density, warnings and mesh statistics;
- apply the finished mesh back to the model as a **new** object, or export STL/3MF.

You cannot, in v1:

- run FEA, thermal, buckling, compression, flow or SIMP;
- grade thickness (gradient, distance, attractors, zones, stress fields);
- use noisy or conformal layouts, cylinder/sphere frames, Voronoi/Delaunay/spinodal cells, or the exotic TPMS
  cells (Neovius, Fischer–Koch, F–RD);
- lattice more than one solid per job, or replace an existing object;
- pick colors or materials in 3MF.

If asked for one of those, say it is deferred and offer the nearest v1 option (§12). Do not approximate an FEA
claim, and never describe a lattice result as structurally verified.

---

## 2. The one rule that matters

**Generation is a job. Applying is a separate, revision-checked edit.** They are not the same call, and a
finished job is not a changed model.

```text
model_status ─► lattice_estimate ─► lattice_start ─► lattice_wait
                                                         │
                                                         ▼
                                              inspect status + warnings
                                                         │
                              ┌──────────────────────────┴──────────────────┐
                              ▼                                             ▼
                       lattice_apply                                 lattice_export
                  (new object; lock + rev check)                  (file in exports/; no model change)
```

A job snapshots the source revision and mesh when it starts. It keeps running while the team edits the model.
When you apply, the hub checks that the source still has the revision the job used. If it changed, the apply
fails with `stale_source` and **nothing is modified**. That is deliberate: regenerate, or export the old result,
but never force it onto newer geometry.

Generation does **not** need a lock and must not hold one. Only the final apply participates in the normal
lock/name rules.

---

## 3. Before you start

1. **Find a valid source.** Call `model_status` (or `get_object`) and choose a `Part::Feature` with exactly one
closed solid. A part with multiple solids, an open shell or an empty shape is refused in v1.
2. **Confirm intent.** If the human has not said which part, what it is for, or whether the result should be
   applied or only exported, ask before spending minutes of host CPU.
3. **Check the install.** Call `lattice_capabilities()`. If it is missing or `available: false`, stop and tell
   the human the reason (`engine_missing`, `disabled`, unsupported platform). Do not invent geometry to
   "approximate" the feature.
4. **Estimate first.** `lattice_estimate` is cheap and catches contradictions, bad ranges and oversized jobs
   before a queue slot is used.
5. **Do not lock the source.** It stays editable during generation. If your team's convention requires a
   pre-reserved name, lock **only the new output name**, and only just before `lattice_apply`.
6. **One job per part per intent.** Reuse a running job instead of starting duplicates; list with
   `lattice_status()`.

---

## 4. Tools

All tools keep the normal CADSwarm response convention: success returns data, failure returns
`{"error": "...actionable text...", "code": "..."}`. Tools never raise through MCP.

| Tool | Mutates model? | Purpose |
|---|---|---|
| `lattice_capabilities()` | no | Cells, defaults, limits, engine availability |
| `lattice_estimate(source, params)` | no | Validate and estimate cost/density |
| `lattice_start(source, name, params, label=None)` | no | Snapshot source and queue a job |
| `lattice_status(job_id=None)` | no | One job, or recent team jobs |
| `lattice_wait(job_id, timeout_s=60)` | no | Bounded wait for progress |
| `lattice_apply(job_id, name=None, label=None, expected_revs=None)` | **yes** | Import result under locks/revisions |
| `lattice_cancel(job_id)` | no | Cancel your own job |
| `lattice_export(job_id, format="3mf", label=None)` | no | Copy result to `exports/` |

No tool accepts a filesystem path, hash, engine argument or shell command. You name a job and an output object;
the hub resolves everything else. If you find yourself wanting a path, you are using the wrong tool.

### 4.1 `lattice_capabilities()`

Returns what this installation can do:

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
  "defaults": {"cell_size_mm": 6.0, "thickness_mm": 0.8, "skin_mm": 1.2,
               "voxel_mm": 0.4, "seed": 1, "layout": "regular", "frame": "box"},
  "limits": {"max_voxels": 150000000, "max_source_bytes": 104857600,
             "max_result_bytes": 262144000, "max_job_seconds": 900,
             "max_concurrent_jobs": 1},
  "deferred": ["noisy", "conformal", "cylinder", "sphere", "grading",
               "voronoi", "delaunay", "spinodal"]
}
```

Treat `cells`, `defaults` and `limits` as authoritative for this host. Do not hardcode the list from this guide.

### 4.2 `lattice_estimate(source, params)`

Validates without creating a job. Use it to choose parameters and to detect an impossible request.

```json
{"source": "Bracket",
 "params": {"cell": "gyroid", "cell_size_mm": 6, "thickness_mm": 0.8,
            "skin_mm": 1.2, "open_faces": ["+z", "-z"], "voxel_mm": 0.4}}
```

```json
{
  "ok": true,
  "normalized": {"cell": "gyroid", "cell_size_mm": 6.0, "thickness_mm": 0.8,
                 "skin_mm": 1.2, "open_faces": ["+z", "-z"], "voxel_mm": 0.4,
                 "seed": 1, "layout": "regular", "frame": "box"},
  "source": {"name": "Bracket", "rev": 7, "bbox": [0, 0, 0, 40, 30, 12],
             "volume_mm3": 8640.0, "solids": 1, "closed": true},
  "estimate": {"voxels": 12960000, "memory_mb": 420, "seconds": 22,
               "result_volume_mm3": 3200, "relative_density": 0.37},
  "warnings": ["closed_skin"]
}
```

- `ok: false` with `code: "too_large"` means the request exceeds a hard limit; coarsen the voxel or cell.
- `seconds` and `memory_mb` are estimates. The job result is authoritative.
- `warnings` are advisory unless the response is an error. Read §7 before starting.

### 4.3 `lattice_start(source, name, params, label=None)`

```json
{"source": "Bracket", "name": "Bracket_lattice", "label": "Bracket lightweight",
 "params": {"cell": "gyroid", "cell_size_mm": 6, "thickness_mm": 0.8,
            "skin_mm": 1.2, "open_faces": ["+z", "-z"], "voxel_mm": 0.4}}
```

Returns `202`-style accepted data:

```json
{"job_id": "lj_7f3a9c2d1e40", "status": "queued", "owner": "alice-claude",
 "source": {"name": "Bracket", "rev": 7, "sha256": "…"},
 "normalized": {"…": "…"}, "warnings": ["closed_skin"],
 "created_at": "2026-10-24T18:22:31Z"}
```

- `name` is the new object name. It must not already exist; v1 never replaces an object.
- The `rev` and hash in the response are the snapshot. Remember them; they are what apply checks.
- The job is team-visible, but only you (or a human) may cancel, apply or export it.
- An accepted start does not mean the lattice is good. Inspect the result before applying.

### 4.4 `lattice_status(job_id=None)`

- With a `job_id`: one job, including `status`, `phase`, `progress`, `normalized`, `warnings`, `result` and
  `error`/`error_code`.
- Without one: the newest jobs for the team (default 20), newest first.
- Result stats appear only when `status` is `done`.

`result` includes `volume_mm3`, `relative_density`, `triangles`, `vertices`, `watertight`, `bbox`, `seconds`,
`peak_memory_mb`, and file hashes/sizes. It never includes a usable filesystem path.

### 4.5 `lattice_wait(job_id, timeout_s=60)`

Polls with bounded backoff and returns the current job object when it finishes or the timeout expires. Cap is
60 s per call. A timeout is **not** a failure: inspect `status` and call again, or tell the human it is still
running. Do not spin in a tight loop.

### 4.6 `lattice_apply(job_id, name=None, label=None, expected_revs=None)`

Imports a completed result as a new `Part::Feature` under the normal lock, name and revision rules.

```json
{"job_id": "lj_7f3a9c2d1e40", "name": "Bracket_lattice",
 "label": "Bracket lightweight", "expected_revs": {"Bracket": 7}}
```

- `name`: omit to use the name requested at start. If supplied by an agent, it must match; only a human may
  rename at apply time.
- `expected_revs`: optional extra guard. The hub always checks the job's recorded source revision as well.
- Preconditions: job is `done`; you own it (or a human is acting); result hashes match; the source still exists
  at the recorded revision; the output name does not exist; lock rules allow the creation.
- Success returns the normal commit shape plus `job_id`:

```json
{"result": {"name": "Bracket_lattice", "volume": 3184.2, "triangles": 184220},
 "created": ["Bracket_lattice"], "modified": [], "deleted": [], "revs": {"Bracket_lattice": 1},
 "seq": 142, "notified": {}, "job_id": "lj_7f3a9c2d1e40"}
```

- A failed apply leaves **no object and no lock change**. The job stays `done`, so you can fix the cause and
  retry, or export instead.
- Apply does not modify, hide or delete the source. Both objects coexist.

### 4.7 `lattice_cancel(job_id)`

Cancels a queued or running job you own. A human may cancel any job. Cancelling never changes the model.
Terminal jobs return `not_cancellable`. Cancel when the human changes their mind, when the estimate was wrong,
or when you started a duplicate by mistake. Do not cancel someone else's job to free the queue; ask them.

### 4.8 `lattice_export(job_id, format="3mf", label=None)`

Copies a completed result into the project's `exports/` folder. No model change, no lock.

```json
{"file": "Bracket lightweight 2026-10-24 18.31.3mf",
 "url": "/exports/…", "bytes": 612430}
```

- `format`: `3mf` (default, roughly five times smaller for large meshes) or `stl`.
- 3MF is a single object in millimetres with no colors or materials in v1.
- Exports are never deleted automatically. Return the `url` or `file` to the human; never invent a path.
- Exporting is the right answer when the source changed and the result is still wanted as a file.

---

## 5. Parameters

### 5.1 Fields

| Field | Type | Required | Rules |
|---|---|---|---|
| `cell` | enum | yes | `gyroid`, `diamond`, `schwarzP`, `iwp`, `bcc`, `fcc`, `octet`, `cubic` |
| `cell_size_mm` | number | yes | 0.5–50; practical 3–15 |
| `thickness_mm` | number | yes | 0.05–10; TPMS sheet wall, beam strut diameter |
| `skin_mm` | number | no | 0–20; `0` means no skin; default `0` |
| `open_faces` | string[] | no | `+x`, `-x`, `+y`, `-y`, `+z`, `-z`; only with `skin_mm > 0` |
| `voxel_mm` | number | yes | 0.1–5; must be ≤ `cell_size_mm / 4` |
| `seed` | integer | no | 0–2^31-1; recorded, no effect on regular cells; default `1` |
| `layout` | enum | no | only `regular` in v1 |
| `frame` | enum | no | only `box` in v1 |

Rules:

- Numbers must be finite JSON numbers. Booleans, numeric strings, `NaN` and `Infinity` are rejected.
- `thickness_mm` is the only thickness field. There are no `wall_mm` or `beam_mm` aliases.
- `open_faces` without a skin is an error (`open_face_without_skin`), not a warning.
- Unknown fields are rejected. Do not send extra keys "just in case".
- Units are millimetres everywhere. STL is interpreted as millimetres.

### 5.2 Cells

**TPMS sheet cells** (`gyroid`, `diamond`, `schwarzP`, `iwp`) are a constant-thickness wall around an implicit
surface. `thickness_mm` is the wall thickness. They give a smooth, isotropic-looking fill and a large surface
area; good default for brackets and heat-exchange shapes.

**Beam cells** (`bcc`, `fcc`, `octet`, `cubic`) are networks of cylindrical struts. `thickness_mm` is the strut
diameter. They are more open, easier to drain, and often stiffer along their strut directions; `octet` is the
stiffest of the four, `cubic` the most anisotropic and the easiest to reason about.

All cells use a regular array fitted to the source bounding box. A partial cell at the boundary is clipped, not
stretched. The lattice is intersected with the source, so it never extends outside the part.

### 5.3 Voxel size

The voxel grid is the resolution of the whole result. It dominates time, memory and file size.

Heuristics, not guarantees:

- **Hard rule:** `voxel_mm ≤ cell_size_mm / 4`.
- **Draft:** `voxel ≈ min(cell_size / 6, thickness / 2)`. Fast; may look chunky.
- **Final:** `voxel ≈ min(cell_size / 12, thickness / 3)`. Better walls and skin.
- Warnings trigger when `cell_size / voxel < 6` or `thickness / voxel < 2`.
- Halving the voxel multiplies voxel count and roughly octuples time and memory.
- If the estimate is `too_large`, coarsen the voxel first, then increase the cell size. Do not remove the skin
  just to fit under a limit unless the human agrees.

### 5.4 Skin and open faces

- `skin_mm = 0`: no shell. The lattice is simply cut out of the source; the outer surface is the lattice.
- `skin_mm > 0`: a solid shell of that thickness is kept on the source boundary and the lattice fills the
  interior. Shell and lattice are one closed solid.
- `open_faces` cuts rectangular windows through the shell on the named sides of the source bounding box, leaving
  the lattice inside. Use opposite faces (for example `["+z", "-z"]`) so powder, resin or cleaning fluid can
  flow through.
- A closed skin is valid but warns `closed_skin`. For additive manufacturing that can trap material; mention it
  to the human and suggest two opposite faces unless they explicitly want a sealed shell.
- `skin_mm` below about two voxels warns `skin_too_thin`; the shell may be porous.

### 5.5 `seed`, `layout`, `frame`

Send them only if you want the defaults recorded. `seed` has no effect on regular cells. `noisy` and
`conformal` layouts and `cylinder`/`sphere` frames return `unsupported_layout` / `unsupported_frame`; do not
retry them with different spelling.

### 5.6 Hard limits

These are host policy. Read `lattice_capabilities().limits` for the actual values; do not try to work around
them.

| Limit | Default | When exceeded |
|---|---|---|
| Source mesh | 100 MB | `too_large` at start |
| Result files | 250 MB | job fails during packaging |
| Voxels | 150 million | `too_large` at estimate/start |
| Job wall clock | 900 s | `timeout` |
| Engine memory | 8 GB | `out_of_memory` |
| Concurrent jobs | 1 | extra jobs stay `queued` |
| Retained jobs | 50 jobs max; 5 GB or 7 days | oldest/expired artifacts are pruned; `expired` |

A too-large request is refused before it can destabilise the host. If you need a bigger part, coarsen the voxel,
increase the cell size, or split the source — do not ask for the limits to be raised as a first move.

---

## 6. Recommended workflow

1. **Read the model.** `model_status`; identify the source and its revision.
2. **Read the install.** `lattice_capabilities`; if unavailable, stop and report.
3. **Estimate.** `lattice_estimate` with your first guess. Fix errors, read warnings.
4. **Start.** `lattice_start` with a unique, descriptive `name` (for example `Bracket_lattice_gyroid`).
5. **Wait.** `lattice_wait` for up to 60 s at a time. Tell the human it is running if it takes longer.
6. **Inspect.** `lattice_status`. Check `watertight`, `relative_density`, `volume_mm3`, `seconds`, `warnings`.
7. **Decide.** Apply, export, or regenerate with changed parameters.
8. **Apply or export.** Apply only if the human wants the model changed and the source revision still matches.
9. **Report.** Name the new object or the exported file, the recipe, the stats and any warnings.

A complete exchange:

```text
model_status()                                  -> Bracket at rev 7, one closed solid
lattice_estimate("Bracket", {...})              -> ok, 22 s, 420 MB, density 0.37, closed_skin
lattice_start("Bracket", "Bracket_lattice", {...}) -> lj_7f3a9c2d1e40, queued
lattice_wait("lj_7f3a9c2d1e40", 60)            -> generating, 0.62
lattice_wait("lj_7f3a9c2d1e40", 60)            -> done, watertight true, volume 3184.2
lattice_apply("lj_7f3a9c2d1e40")               -> created Bracket_lattice, rev 1
```

If the model moved on:

```text
lattice_apply("lj_7f3a9c2d1e40")
  -> {"code": "stale_source", "job_source_rev": 7, "current_rev": 9,
      "error": "Bracket changed after this lattice was generated (...). Generate again or apply the result as an export."}
```

Correct response: tell the human, then either `lattice_start` again from the current source or
`lattice_export` the old result. Never try to bypass the check.

---

## 7. Reading warnings

Warnings do not fail a job. Decide whether to regenerate, proceed, or ask.

| Warning | Meaning | Recommended action |
|---|---|---|
| `cell_too_few_voxels` | Fewer than 6 voxels across a cell | Lower `voxel_mm` or raise `cell_size_mm` |
| `thickness_too_few_voxels` | Wall/strut under 2 voxels | Lower `voxel_mm` or raise `thickness_mm` |
| `skin_too_thin` | Skin under 2 voxels | Raise `skin_mm` to ≥ 2 × `voxel_mm`, or set it to 0 |
| `closed_skin` | Sealed shell; may trap powder/resin | Add two opposite `open_faces`, or confirm the seal is wanted |
| `large_part` | Bbox > 300 mm or > 100 M voxels | Coarsen voxel/cell; warn the human about run time |
| `thickness_high` | Thickness > 35% of cell size | Expect a nearly solid result; reduce thickness |
| `thickness_low` | Thickness < 3% of cell size | Expect a fragile result; increase thickness |
| `open_face_without_skin` | **Error**, not a warning | Add a skin or remove `open_faces` |

Also treat these job outcomes as warnings in your report, even though they are errors at start:
`source_has_multiple_solids`, `source_not_closed`, `source_empty`.

---

## 8. Errors and what to do

Request errors — fix the request; do not retry unchanged.

| Code | Do this |
|---|---|
| `bad_args` | Read the detail, correct the field/type. Unknown fields are rejected |
| `unsupported_cell` | Pick a cell from `lattice_capabilities` |
| `unsupported_layout` | Use `regular`; do not retry `noisy`/`conformal` |
| `unsupported_frame` | Use `box`; cylinder/sphere are deferred |
| `source_not_found` | Re-read `model_status`; the name may have changed |
| `source_not_solid` | Choose a `Part::Feature` with exactly one closed solid |
| `too_large` | Coarsen `voxel_mm`; then increase `cell_size_mm`; re-estimate |
| `no_room` | Disk/project limit. Tell the human; do not retry in a loop |
| `lattice_unavailable` | Engine missing/disabled/unsupported. Tell the human; stop |

Job errors — inspect, then decide.

| Code | Do this |
|---|---|
| `job_not_found` | List jobs with `lattice_status()`; use the exact id |
| `job_not_done` | Wait; do not apply/export early |
| `job_failed` | Read `error_code` and `error`; fix the cause, start a new job |
| `not_cancellable` | Job is already terminal; nothing to do |
| `timeout` | Coarsen the voxel or split the part; retry once |
| `out_of_memory` | Coarsen the voxel; do not raise limits yourself |
| `cancelled` | Expected after cancel; no model change |
| `engine_failed` | Report the job id and `engine.log` to the human; retry once at a coarser voxel |
| `engine_mismatch` | Result came from an incompatible engine; regenerate |

Apply/export errors — the model is unchanged unless stated.

| Code | Do this |
|---|---|
| `not_owner` | Ask the owner or a human to apply/export/cancel |
| `stale_source` | Regenerate from the current source, or export the old result |
| `name_exists` | Choose a different `name`; never delete the existing object to make room |
| `not_locked` | Lock the output name with `lock_objects`, then retry |
| `locked` | Another participant reserved the name; ask them or choose another |
| `stale_rev` | Re-read the model; your `expected_revs` is out of date |
| `mesh_invalid` / `not_closed` | Do not apply. Retry with thicker wall or finer voxel; if it repeats, report a bug |
| `path_rejected` | Internal safety check. Do not retry; report the job id |

`engine_missing` and `disabled` appear in capabilities; tool calls then return `lattice_unavailable`.

---

## 9. Choosing starting values

These are starting points, not recommendations for a structural part. Relative density is reported by the
estimate and the result; use it to compare candidates.

| Intent | `cell` | `cell_size_mm` | `thickness_mm` | `voxel_mm` | `skin_mm` | `open_faces` |
|---|---|---:|---:|---:|---:|---|
| Lightweight bracket | `gyroid` | 8 | 0.9 | 0.45 | 1.2 | `+z`, `-z` |
| Stiff frame / load path | `octet` | 6 | 1.2 | 0.4 | 1.5 | `+z`, `-z` |
| Small, fine part (< 40 mm) | `gyroid` | 3 | 0.4 | 0.15 | 0.6 | `+z`, `-z` |
| Fast visual draft | `diamond` | 10 | 1.2 | 0.5 | 0 | none |
| Open flow / drainable | `bcc` | 8 | 1.0 | 0.5 | 1.0 | `+z`, `-z` |

Tuning loop:

1. Fix the cell family first; compare gyroid vs octet for the same intent.
2. Set cell size from the smallest feature the part must fit (aim for 3–5 cells across the smallest
   dimension).
3. Set thickness from the target relative density: thicker is denser and heavier.
4. Set the finest voxel you can afford for the final job; use a coarse voxel for exploration.
5. Add a skin only if the part needs a closed outer surface; if so, choose two opposite open faces.

Relative density guidance: 0.15–0.35 is a typical lightweight fill; above 0.5 is close to solid and saves little
mass; below 0.1 is often too fragile to handle or print.

---

## 10. Safety and etiquette

- **Do not hold locks during generation.** It blocks teammates for minutes and is not required.
- **Do not apply without intent.** Applying creates a visible object in the shared model. It is a normal CAD
  edit: the human should know it is coming.
- **Do not apply a stale result.** Regenerate or export.
- **Do not reuse a job id from another participant** to apply or export their result; ask them or a human.
- **Do not cancel other people's jobs** to shorten the queue.
- **Do not poll faster than about once per second**; use `lattice_wait` and let it back off.
- **Do not queue duplicate jobs** for the same source and parameters; check `lattice_status()` first.
- **Do not invent paths, hashes or engine options.** The tools have no such parameters.
- **Do not claim verification.** A lattice is geometry. FEA, thermal and certification are out of scope.
- **Respect the host.** One engine slot runs on the host's computer. Coarse estimates and cancellation are
  courtesies, not optional.
- **Report failures with the job id** and the error code so the operator can find `engine.log`.

---

## 11. Reporting to the human

Keep it short and factual. Include the recipe, the measured result, warnings and what changed.

```text
Generated Bracket_lattice from Bracket (rev 7): gyroid, 6 mm cell, 0.8 mm wall,
1.2 mm skin, open +z/-z, 0.4 mm voxel.
Result: 3,184 mm³, 37% relative density, 184,220 triangles, 25 s, watertight.
Warnings: none.
Applied as Bracket_lattice (new object). Bracket is unchanged.
This is geometry only — not a structural verification.
```

If something failed:

```text
Could not apply Bracket_lattice: the source changed after generation (job rev 7, now rev 9).
Nothing was modified. I can regenerate from the current Bracket, or export the old result as 3MF.
Which do you want?
```

Do not paste raw JSON unless the human asks for it. Do not include internal paths, hashes or stack traces.

---

## 12. When asked for something deferred

| Request | Response |
|---|---|
| "Optimise the shape for stress" / "run FEA" | Not in v1. Offer a uniform lattice and say it is not verified |
| "Make the walls thicker near the load" | Grading is deferred. Offer a larger uniform thickness or a different cell |
| "Use a Voronoi/foam/spinodal pattern" | Deferred. Offer gyroid or diamond |
| "Use a cylindrical/conformal fill" | Deferred. Offer a box-framed regular fill |
| "Lattice both bodies at once" | v1 is one closed solid per job. Run one job per body and apply both |
| "Replace the original part" | v1 never replaces. Apply creates a new object; the human can delete the old one |
| "Put colors/materials in the 3MF" | v1 writes a single millimetre mesh with no materials |
| "Make it support itself for FDM" | No overhang-aware mode in v1. Suggest a beam cell at a printable angle and a print test |
| "Is it strong enough?" | No. Only physical testing or a validated analysis can answer that |

---

## 13. Quick reference

```text
capabilities() -> if not available: stop
estimate(source, params) -> fix errors, read warnings
start(source, name, params) -> job_id + source rev
wait(job_id, 60) -> done? inspect
apply(job_id) -> new object, lock/rev checked
export(job_id, "3mf") -> file in exports/
cancel(job_id) -> owner/human only
```

Required params: `cell`, `cell_size_mm`, `thickness_mm`, `voxel_mm`.
Optional: `skin_mm`, `open_faces`, `seed`, `layout`, `frame`.
Cells: `gyroid`, `diamond`, `schwarzP`, `iwp`, `bcc`, `fcc`, `octet`, `cubic`.
Never send: paths, hashes, engine flags, FEA fields, grading fields.
Never do: hold a lock during generation, apply a stale result, replace an existing object, claim verification.
