# CadSwarm Lattice (IMPLICIT-parity core) implementation plan

> **Status: proposed 2026-10-24. No implementation code has been written.** This plan exists so the work can
> be reviewed, split and executed later. Do not treat unchecked tasks as started.

> **For agentic workers:** after approval, use `superpowers:subagent-driven-development` or
> `superpowers:executing-plans` and work task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** add a local, agent-accessible lattice-generation capability to CadSwarm: choose a closed part in the
shared FreeCAD model, generate a standard TPMS or beam lattice with skin/open-face control, inspect the result,
apply it back to the model under the existing lock and revision rules, and export STL/3MF. Expose the same
capability through a new FreeCAD tab and through MCP tools for the team's AI agents.

**Architecture:** a separate .NET 9 lattice engine process (PicoGK 2.3.0, Apache-2.0) is driven by a Python job
manager in the hub. Generation never runs inside the single FreeCAD worker. The hub snapshots the source mesh
and revision atomically, runs the engine outside the model lock, then applies the finished mesh in a short,
lock-checked FreeCAD transaction. The FreeCAD UI gets a thin `Lattice` dock tab tabified with `CadSwarm Team`;
agents get job-based MCP tools.

**Tech Stack:** .NET 9, C#, PicoGK 2.3.0, LEAP 71 ShapeKernel/Lattice Library (Apache-2.0, pinned), Python
3.11+ stdlib for the manager/contract, FastAPI + SQLite for the current hub, FreeCAD 1.x for the worker and
workbench, MCP 2.3+, pytest. No JavaScript, no browser WASM, no cloud service.

**Read first:**

- [IMPLICIT evidence dossier](../research/2026-10-24-implicit-build-evidence.md)
- [Lattice contract v1](../specs/2026-10-24-lattice-contract.md)
- `README.md` — current hub, worker, lock and storage rules
- `cadswarm/hub/core.py` — `MUTATING`, `TARGETS`, `CREATES`, `Hub.op`, `_commit`
- `cadswarm/worker/freecad_worker.py` — `MUTATIONS`, `mutate`, transactions, queries, export
- `cadswarm/mcp_server.py` — tool style and error shape
- `cadswarm/gui/panel.py` — dock panel, session, background-thread pattern
- `InitGui.py` — workbench commands, toolbar and menu
- `cadswarm/worker/sandbox.py` — process-isolation pattern (reference, not reuse)
- `docs/superpowers/plans/2026-10-23-cadswarm-00-overview.md` — current migration/contract conventions

---

## 1. Why this shape

### 1.1 The constraint

CadSwarm has exactly one live FreeCAD worker. Every mutation is serialized through it, and an op that runs
longer than the worker timeout is killed and rolled back. Lattice generation is seconds to minutes and can use
gigabytes of memory. Putting it in the worker would stall every teammate and risk the live document.

### 1.2 The split

```text
 agent / tab
     │  HTTP (existing auth, fence, lock rules)
     ▼
 FastAPI hub ──────────────► LatticeJobs (Python)
     │                         │  one queue, one engine process
     │                         ▼
     │                    LatticeEngine.exe  (PicoGK 2.3.0, local, no network)
     │                         │
     │  short query            │  result.stl + result.3mf + result.json
     ▼                         ▼
 FreeCAD worker  ◄──── source.stl / import result.stl
     │
     └── the live .FCStd, transactions, lock rollback
```

- **Snapshot** is a short worker query (`lattice_source`), performed under the hub lock.
- **Generate** is an external process; the hub lock is free.
- **Apply** is a short worker mutation (`lattice_import`) inside a normal transaction.
- **Export** copies the completed 3MF/STL into `exports/`; no model change.

### 1.3 Why a job instead of one blocking tool call

- MCP clients and HTTP proxies have timeouts; a 10-minute tool call is fragile.
- Agents need progress, cancellation and a result they can apply deliberately.
- A finished result can be exported even if the source changed, but it can never be applied to a newer source.
- The model stays editable while a job runs; revision safety is enforced at apply, not by a long lock.

---

## 2. Scope

### 2.1 MVP (this plan)

- One closed `Part::Feature` source per job, in millimetres, world placement applied.
- Standard cells:
  - TPMS sheet: **Gyroid**, **Diamond**, **Schwarz P**, **Schoen I–WP**.
  - Beams: **BCC**, **FCC**, **octet truss**, **simple cubic**.
- Regular cell array fitted to the source bounding box.
- Uniform `thickness_mm` (wall for TPMS, strut diameter for beams).
- No skin, closed skin, or skin with axis-aligned open faces (`+x/-x/+y/-y/+z/-z`).
- User-set voxel size with hard caps and actionable warnings.
- Deterministic regular geometry; seed recorded for future stochastic cells.
- Result validation: finite, closed/manifold, positive volume, bbox sanity.
- STL and single-object millimetre 3MF output.
- Job queue, progress, cancel, retention, disk guard.
- Apply through the existing lock/revision/transaction path.
- Thin FreeCAD `Lattice` tab, tabified with `CadSwarm Team`.
- MCP tools with the same contract and no filesystem paths.
- Local-only operation; no uploads.

### 2.2 Explicitly deferred

- Noisy and conformal cell arrays; cylinder/sphere frames.
- Thickness grading (gradient, distance, attractors, zones, FEA field).
- TPMS network forms, Neovius, Fischer–Koch S/C(Y), F–RD.
- Spinodal, Voronoi, Delaunay.
- Multi-body sources and per-body lattices.
- FEA, thermal, buckling, compression, flow/aero, SIMP, Verify.
- Measured-lattice library and JSON import.
- Density-map 3MF, colors, materials in 3MF.
- GPU/WebGPU preview; the tab shows status and an optional coarse mesh preview.
- Linux support (PicoGK's official NuGet package targets Windows x64 and macOS arm64).

### 2.3 Non-goals

- Rebuilding the IMPLICIT UI or copying its wording/assets.
- A general implicit-modelling language.
- A slicer or print-service integration.
- Certification or structural verification claims.

---

## 3. Global constraints

1. **Clean-room.** No IMPLICIT code, assets, strings, shaders, WASM, STEP importer or recipe schema. See the
   evidence dossier §1 and §7.
2. **Licences.** PicoGK, ShapeKernel and Lattice Library are Apache-2.0. Pin exact versions/commits, keep the
   licence texts and add `THIRD_PARTY_NOTICES.md`. Review before redistribution.
3. **No FreeCAD worker blocking.** Generation runs in its own process; only snapshot/import touch the worker.
4. **No arbitrary paths.** Agents pass a job id and an output name. The hub resolves the directory; the worker
   checks `realpath` containment and the result hash.
5. **Revision safety.** Apply checks the source revision recorded at snapshot time. A changed source is
   `stale_source`, never a silent overwrite.
6. **Lock safety.** Apply goes through the same lock checks as any creation. A failed import is rolled back.
7. **Millimetres.** All engine geometry, 3MF units and FreeCAD coordinates are millimetres.
8. **One engine slot.** One job runs at a time in v1; others queue. This bounds memory and makes cancellation
   predictable.
9. **Local only.** The engine needs no network. Do not add analytics, uploads or a hosted fallback.
10. **Feature flag.** `CADSWARM_LATTICE=off` disables the feature; `CADSWARM_LATTICE_ENGINE` can point at a
    development build. Absence of the engine must not break the rest of CadSwarm.
11. **Additive.** Existing ops, MCP tools, locks and storage behaviour stay compatible. Tests that enumerate
    tools/op kinds get explicit expected updates.
12. **No code without the spike.** Task 0 must confirm the headless and mesh round-trip assumptions before
    Tasks 1–3 are committed.

---

## 4. Repository layout

```text
services/lattice/
  CadSwarm.Lattice.sln
  CadSwarm.Lattice/
    CadSwarm.Lattice.csproj          net9.0, PicoGK 2.3.0, pinned LEAP refs
    Program.cs                       CLI: probe, generate
    Protocol.cs                      request/result records and JSON
    Params.cs                        validation/normalization, warnings
    SourceMesh.cs                    STL ingest, bbox, watertight check
    Build/
      VoxelSource.cs                 source voxel field
      Shell.cs                       skin + open faces
      LatticeField.cs                union/intersect/offset pipeline
      Mesher.cs                      voxel -> mesh
    Cells/
      Tpms.cs                        gyroid, diamond, Schwarz P, I–WP SDFs
      Beams.cs                       BCC, FCC, octet, cubic graphs
      CellCatalog.cs                 ids, families, defaults
    Output/
      StlWriter.cs
      ThreeMfWriter.cs               minimal OPC/ZIP package, millimetre
      Stats.cs                       volume, density, triangles, hashes
    Validation/
      MeshCheck.cs                   finite, manifold, bbox, volume
    third_party/
      THIRD_PARTY_NOTICES.md
  CadSwarm.Lattice.Tests/
  README.md
cadswarm/lattice/
  __init__.py
  contract.py                        constants, normalization, warning codes
  store.py                           jobs.sqlite, job rows, retention
  engine.py                          process supervisor, probe, generate
  manager.py                         queue, snapshot, apply/export orchestration
cadswarm/gui/lattice_panel.py        FreeCAD dock tab
cadswarm/hub/core.py                 hook: apply_lattice / internal import op
cadswarm/hub/app.py                  /lattice routes
cadswarm/worker/freecad_worker.py    lattice_source query + lattice_import mutation
cadswarm/mcp_server.py               agent tools
InitGui.py                           command, toolbar, menu
tests/test_lattice_contract.py
tests/test_lattice_store.py
tests/test_lattice_manager.py
tests/test_lattice_worker.py
tests/test_lattice_integration.py
tests/test_lattice_mcp.py
tests/test_lattice_panel.py
```

No files are created by this plan yet.

---

## 5. Interfaces

### 5.1 Engine CLI

```powershell
CadSwarm.Lattice.exe probe --json
CadSwarm.Lattice.exe generate --request <job>/request.json --out <job>
```

`probe` prints one JSON capability object and exits 0. `generate` reads the request, writes `result.json`,
`result.stl`, `result.3mf`, `preview.stl` and `engine.log`, and exits 0 on success. A non-zero exit is an engine
failure; the manager reads `result.json` for a structured error when present.

The engine is one-shot in v1. It does not keep a warm process, open a viewer or listen on a socket. This makes
cancellation, memory release and crash recovery simple; startup cost is measured in Task 0.

### 5.2 Engine request

```json
{
  "contract": "lattice/1",
  "job_id": "lj_7f3a9c2d1e40",
  "source": {"name": "Bracket", "stl": "source.stl", "sha256": "…", "bbox": [0,0,0,40,30,12]},
  "params": {
    "cell": "gyroid", "cell_size_mm": 6.0, "thickness_mm": 0.8,
    "skin_mm": 1.2, "open_faces": ["+z","-z"], "voxel_mm": 0.4,
    "seed": 1, "layout": "regular", "frame": "box"
  },
  "limits": {"max_voxels": 150000000, "max_seconds": 900, "max_memory_mb": 8192}
}
```

The engine resolves `source.stl` relative to its request file and rejects absolute or escaping paths.

### 5.3 Engine result

```json
{
  "ok": true,
  "engine": "0.1.0",
  "picoGK": "2.3.0",
  "files": {
    "stl": {"name": "result.stl", "sha256": "…", "bytes": 2841192},
    "three_mf": {"name": "result.3mf", "sha256": "…", "bytes": 612430},
    "preview": {"name": "preview.stl", "sha256": "…", "bytes": 220114}
  },
  "stats": {
    "bbox": [0,0,0,40,30,12], "volume_mm3": 3184.2, "relative_density": 0.368,
    "triangles": 184220, "vertices": 92112, "watertight": true,
    "seconds": 24.7, "peak_memory_mb": 438, "voxels": 12960000
  },
  "warnings": ["closed_skin"]
}
```

On failure:

```json
{"ok": false, "code": "out_of_memory", "error": "…actionable…", "phase": "lattice"}
```

### 5.4 Manager interface

`LatticeJobs` (one per hub process) exposes:

| Method | Purpose |
|---|---|
| `capabilities()` | Cached engine probe + limits; `available: false` on absence |
| `estimate(pid, body)` | Validate and estimate; no job row |
| `start(pid, body)` | Snapshot source, create job, enqueue |
| `list(pid, limit)` | Recent team jobs |
| `get(pid, job_id)` | One job, redacted paths |
| `cancel(pid, job_id)` | Owner/human cancellation |
| `export(pid, job_id, fmt, label)` | Copy completed artifact to `exports/` |
| `prepare_apply(pid, job_id, name, label)` | Check ownership/status/revs/hash; return trusted worker args |
| `retain()` | TTL/size cleanup |
| `stop()` | Cancel queue, kill child, close DB |

`Hub` holds `self.lattice` (or `None`). `Hub.op` gets one narrow hook: if `op == "lattice_import"`, it replaces
caller args with `lattice.prepare_apply(...)` before the normal lock/expected-rev checks. The op is not exposed
through the public MCP tool set, and callers cannot supply a path or hash.

### 5.5 Worker protocol additions

Query `lattice_source`:

```json
{"op": "lattice_source", "args": {
  "name": "Bracket", "path": "<job>/source.stl", "tolerance_mm": 0.1
}}
```

Result:

```json
{"name": "Bracket", "path": "<job>/source.stl", "sha256": "…", "bbox": [0,0,0,40,30,12],
 "volume": 8640.0, "solids": 1, "closed": true, "triangles": 18220}
```

Mutation `lattice_import`:

```json
{"op": "lattice_import", "args": {
  "name": "Bracket_lattice", "label": "Bracket lightweight",
  "job_id": "lj_…", "path": "<job>/result.stl", "sha256": "…",
  "source_name": "Bracket", "source_rev": 7
}}
```

Result: `{"name": "...", "volume": 3184.2, "triangles": 184220}`. The worker validates the path is inside the
job directory, the hash matches, the mesh converts to exactly one closed solid, and the volume is positive.

### 5.6 HTTP routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/lattice/capabilities` | Engine availability, cells, limits, defaults |
| POST | `/lattice/estimate` | Validate + estimate |
| POST | `/lattice/jobs` | Start a job |
| GET | `/lattice/jobs` | List recent jobs |
| GET | `/lattice/jobs/{id}` | Job status/result |
| POST | `/lattice/jobs/{id}/cancel` | Cancel |
| POST | `/lattice/jobs/{id}/apply` | Apply to the model |
| POST | `/lattice/jobs/{id}/export` | Save STL/3MF |
| GET | `/lattice/jobs/{id}/preview` | Optional preview mesh for the tab |

All routes use the existing participant/token/fence authentication. `/preview` is bounded and only available
for `done` jobs.

### 5.7 MCP tools

| Tool | Mutates? | Purpose |
|---|---|---|
| `lattice_capabilities()` | no | What this install can generate, limits, defaults |
| `lattice_estimate(source, params)` | no | Validate + time/memory/density estimate |
| `lattice_start(source, name, params, label=None)` | no model change | Start a job |
| `lattice_status(job_id=None)` | no | One job or recent jobs |
| `lattice_wait(job_id, timeout_s=60)` | no | Bounded wait for progress |
| `lattice_apply(job_id, name=None, label=None, expected_revs=None)` | yes | Import result under locks/revs |
| `lattice_cancel(job_id)` | no model change | Cancel own job |
| `lattice_export(job_id, format="3mf", label=None)` | no model change | Save to `exports/` |

No tool accepts a path, hash, engine argument or shell command.

---

## 6. Task 0 — feasibility spike (time-boxed)

**Files:** `docs/superpowers/research/2026-10-24-picogk-spike.md` (new, after the spike), no product code.

**Steps:**

- [ ] Build a throwaway .NET 9 console project outside the repo with `PicoGK 2.3.0`; confirm the NuGet package
      and native runtime load on Windows x64.
- [ ] Find and document the supported headless entry point (no viewer window). Confirm it can run in a
      console/service context and exit cleanly.
- [ ] Generate a 20 mm box and a 30×20×10 mm gyroid block; export STL.
- [ ] Import the STL into FreeCAD with `MeshPart.meshToShape`; try `Part.Solid(...)`, vertex welding and
      `Part.removeSplitter` as needed. Record the success rate and the failure modes.
- [ ] Repeat with a BCC beam block, a 1.2 mm skin, and two open faces.
- [ ] Measure wall time, peak working set and output size at 0.8, 0.5 and 0.3 mm voxels.
- [ ] Run the same recipe twice; compare triangle/vertex counts and volume. Record whether output is
      deterministic.
- [ ] Kill the process mid-run; confirm no child process, file lock or temporary folder survives.
- [ ] Confirm the exact licence/attribution obligations for PicoGK and the LEAP libraries.

**Acceptance:** a short spike report answers the eight open questions in the evidence dossier §8 with measured
numbers. If mesh-to-solid conversion is unreliable, stop and choose one of these before continuing:

1. improve tessellation/welding until it is reliable, or
2. keep the result as an export-only artifact (no model apply) for v1, or
3. add a mesh-native object path with explicit limitations.

Do not silently insert a `Mesh::Feature` into a model whose checks/STEP export assume solids.

**Stop condition:** if PicoGK cannot run headless on the host platform, the plan pivots to an export-only tool
or defers the feature. Do not build the rest on an unverified assumption.

---

## 7. Task 1 — engine skeleton and protocol

**Files:** `services/lattice/CadSwarm.Lattice.sln`, `services/lattice/CadSwarm.Lattice/*`,
`services/lattice/README.md`.

**Steps:**

- [ ] Create the .NET 9 solution and console project. Pin `PicoGK` to `2.3.0`. Add the LEAP ShapeKernel and
      Lattice Library sources as pinned references or a documented submodule; do not vendor unreviewed code.
- [ ] Implement `probe`: contract, engine version, PicoGK version, platform, cells, defaults, limits, deferred
      list. It must work without a source file.
- [ ] Implement the request/result records and strict JSON parsing. Reject unknown fields, wrong types,
      non-finite numbers and absolute/escaping paths.
- [ ] Implement `generate` as a one-shot process: read request, resolve `source.stl`, validate, build, write
      outputs and `result.json`, exit 0/2/3 with distinct meanings.
- [ ] Capture engine logs to `engine.log`; cap stdout/stderr in the manager.
- [ ] Add a tiny fixture source (a generated box STL) so the engine can be tested without FreeCAD.
- [ ] Add a `--version`/`probe` smoke test and a malformed-request test.

**Acceptance:** `probe` returns valid JSON on a machine with the engine; `generate` on the fixture box produces
a non-empty `result.stl` and `result.json`; malformed JSON produces a structured `bad_args` result, not a
stack trace on stdout.

---

## 8. Task 2 — geometry core

**Files:** `services/lattice/CadSwarm.Lattice/Params.cs`, `SourceMesh.cs`, `Cells/*`, `Build/*`.

**Steps:**

- [ ] Implement parameter normalization and warnings from contract §4. Keep the warning codes stable.
- [ ] Implement STL ingest: binary and ASCII, reject truncated/malformed files, compute bbox/volume/triangle
      count, check edge-manifoldness.
- [ ] Implement the four TPMS sheet fields:
      - Gyroid: `sin x cos y + sin y cos z + sin z cos x`.
      - Diamond: the standard diamond surface expression.
      - Schwarz P: `cos x + cos y + cos z`.
      - Schoen I–WP: the standard I–WP expression.
      Normalize each to the chosen cell size, apply the half-wall threshold, and orient consistently. Record
      the phase/orientation convention in the engine README.
- [ ] Implement the four beam graphs on a regular cell array:
      - BCC: corners to body centre.
      - FCC: corners to face centres.
      - Octet: the octet truss graph.
      - Simple cubic: edge beams.
      Use cylindrical struts with hemispherical/rounded joints so the network is connected.
- [ ] Implement the regular box cell array fitted to the source bbox. Clip at the boundary; do not require a
      whole number of cells.
- [ ] Voxelize the source; intersect the lattice field with the source; keep a consistent inside/outside
      convention.
- [ ] Implement skin:
      - `skin_mm == 0`: no shell.
      - `skin_mm > 0`: shell = source minus eroded source; interior = source minus shell; union shell +
        interior lattice.
- [ ] Implement open faces as rectangular windows cut through the shell, inset from the bbox edges by a
      documented constant; union the result. Reject open faces without skin.
- [ ] Reject multiple solids in v1 with `source_not_solid`; report the count.
- [ ] Record normalized recipe, cell orientation, open-face inset and voxel size in `result.json`.

**Acceptance:** unit tests on small boxes for each cell produce non-empty, closed meshes; relative density is
in a plausible range; changing cell size or thickness changes volume monotonically in the expected direction;
skin increases volume; open faces reduce shell volume; no source extends outside its bbox.

---

## 9. Task 3 — output, stats and validation

**Files:** `Output/StlWriter.cs`, `Output/ThreeMfWriter.cs`, `Output/Stats.cs`, `Validation/MeshCheck.cs`.

**Steps:**

- [ ] Mesh the final voxel field at `voxel_mm`; export binary STL with millimetre coordinates.
- [ ] Write a minimal single-object 3MF:
      - OPC package: `[Content_Types].xml`, `_rels/.rels`, `3D/3dmodel.model`.
      - Unit `millimeter`, one object, one mesh, one build item.
      - No colors, materials or extensions in v1.
      - Deterministic ZIP entry order and timestamps where practical.
- [ ] Compute stats: bbox, mesh volume, relative density vs source, triangles, vertices, watertight, peak
      memory, seconds, voxel count.
- [ ] Validate: finite coordinates, triangle count, vertex count, manifold edges, positive volume, bbox
      within `source bbox + 2*voxel`, volume <= source volume + tolerance.
- [ ] Write `preview.stl` at a coarser voxel (or decimated) for the tab; never use it for apply.
- [ ] Hash all outputs with SHA-256; write `result.json`.
- [ ] Add a 3MF round-trip test: open in FreeCAD and at least one common slicer; verify the size is millimetres
      and the mesh is not empty.

**Acceptance:** STL and 3MF both open at the correct size; a corrupted/NaN mesh is rejected; the result JSON
hashes match the bytes; 3MF is smaller than STL on the benchmark part.

---

## 10. Task 4 — job store, manager and engine supervisor

**Files:** `cadswarm/lattice/__init__.py`, `contract.py`, `store.py`, `engine.py`, `manager.py`,
`tests/test_lattice_contract.py`, `tests/test_lattice_store.py`, `tests/test_lattice_manager.py`.

**Steps:**

- [ ] Define job ids `lj_` + 12 lowercase hex; timestamps as UTC ISO-8601 or epoch milliseconds consistently.
- [ ] Create `jobs.sqlite` under `.cadswarm-team/lattice/`, WAL mode, one writer. Schema covers job id, owner,
      source name/rev/hash, normalized params, status/phase/progress, result JSON, error, engine version,
      timestamps and expiry.
- [ ] Implement the queue: one worker thread, FIFO, one engine process at a time. New jobs are `queued`;
      `preparing` while snapshotting; `generating` while the engine runs; `packaging` while hashing/validating.
- [ ] Implement the engine supervisor:
      - resolve the executable from `CADSWARM_LATTICE_ENGINE`, the installed app dir, or the dev tree;
      - `probe` once, cache capabilities;
      - spawn with a clean environment, no shell, working directory = job dir;
      - capture capped stdout/stderr to `engine.log`;
      - enforce wall-clock and memory limits; kill the process tree on timeout/cancel;
      - classify exit codes and structured errors.
- [ ] Add disk guard/project accounting: check free space and the lattice cache limit before starting and
      before packaging; include `lattice_cache` in storage status.
- [ ] Add retention: delete artifacts for expired/cancelled/failed jobs first, then oldest done jobs; never
      touch `exports/`. Run at start and hourly.
- [ ] Emit `lattice_started`, rate-limited progress, `lattice_done`, `lattice_failed`, `lattice_cancelled`,
      `lattice_expired` events through the hub.
- [ ] On hub restart, mark any `queued`/`preparing`/`generating`/`packaging` job from the previous process as
      `failed` with `engine_restarted`; do not leave a zombie state.
- [ ] Add a fake-engine fixture (a Python script named as the engine) for tests. It can sleep, emit a valid
      result, fail, hang or ignore cancel. Do not require .NET for the Python unit suite.

**Acceptance:** tests cover FIFO order, one-at-a-time, cancel during generation, timeout kill, malformed engine
output, restart recovery, disk refusal, retention order and no path leakage. No test leaves a child process.

---

## 11. Task 5 — worker and hub integration

**Files:** `cadswarm/worker/freecad_worker.py`, `cadswarm/hub/core.py`, `cadswarm/hub/app.py`,
`tests/test_lattice_worker.py`, `tests/test_lattice_integration.py`.

### 11.1 Worker

- [ ] Add query `lattice_source`:
      - validate the source is a `Part::Feature` with exactly one solid;
      - export the world-space shape to the supplied `source.stl` using `MeshPart`/`Mesh` with tolerance
        `min(0.2, voxel_mm/4)`;
      - return hash, bbox, volume, solid count, closed flag, triangle count;
      - reject paths outside the job directory.
- [ ] Add mutation `lattice_import`:
      - resolve the job directory from `job_id`; reject caller paths outside it;
      - verify SHA-256 before reading;
      - import STL, weld vertices, convert to a shell/solid;
      - require exactly one closed solid, positive volume, bbox within tolerance;
      - refuse to replace an existing object in v1; `name_exists`;
      - create `Part::Feature`, set `Shape`, label, and return name/volume/triangles.
- [ ] Do not add `lattice_import` to the public MCP tool list or to the normal `/ops` allow-list.
- [ ] Ensure `mutate` still saves before commit and rolls back on any failure. A failed import must leave no
      object and no lock row.

### 11.2 Hub

- [ ] Extract the existing op precondition logic from `Hub.op` into a small reusable helper, without changing
      existing op behaviour. Use it for `lattice_import`.
- [ ] `Hub.op`: if `op == "lattice_import"`, require `self.lattice`, call `prepare_apply`, replace the args with
      the trusted `{name, label, job_id, path, sha256, source_name, source_rev}` and continue through the
      normal `TARGETS`/`CREATES`/`expected_revs`/`_commit` path.
- [ ] `CREATES["lattice_import"] = "name"`; it does not target the source because apply checks the recorded
      revision directly.
- [ ] Add routes from §5.6 to `app.py`; authenticate every route with the existing `me` dependency.
- [ ] `POST /lattice/jobs` uses `hub.lattice.start`; `GET`/cancel/export/apply delegate. Apply returns the
      normal commit shape plus `job_id`.
- [ ] On shutdown, stop the queue and engine; on startup, recover jobs and start retention.
- [ ] Ensure `/health` reports `lattice: {available, engine, platform, reason}` without failing the rest of the
      health check.

**Acceptance:** integration test with real FreeCAD: create a box, start a job with the fake or real engine,
apply, assert the new object exists, is a closed solid, has the expected volume tolerance, and `check_interference`
can see it. Test stale source, reserved name, existing name, corrupted result, path traversal and lock
violation. All failures leave the model and locks unchanged.

---

## 12. Task 6 — MCP tools

**Files:** `cadswarm/mcp_server.py`, `tests/test_lattice_mcp.py`.

**Steps:**

- [ ] Add the eight tools from §5.7 with clear docstrings that explain when to use them and what is deferred.
- [ ] Add tool-call helpers:
      - `lattice_start` posts to `/lattice/jobs`;
      - `lattice_status`/`lattice_wait` poll with bounded backoff;
      - `lattice_apply` posts the job apply route and returns the commit shape;
      - `lattice_export` returns a download URL, not a path.
- [ ] Keep the existing error convention: `{"error": "...", "code": "..."}`; never raise through MCP.
- [ ] Add a short section to `INSTRUCTIONS`:
      - lattice is optional and job-based;
      - start only after `model_status` and `lattice_estimate`;
      - do not hold locks during generation;
      - apply only after checking warnings and source revision;
      - don't apply a stale result; regenerate;
      - don't ask for FEA/SIMP in v1.
- [ ] Ensure `lattice_wait` caps at 60 s per call and returns `status` rather than blocking forever.
- [ ] Add a tool-list snapshot/contract test so accidental renames are caught.

**Acceptance:** an MCP client sees the tools; a fake hub returns the documented shapes; `lattice_apply` sends
no path/hash; errors preserve codes; the existing tool suite still passes.

---

## 13. Task 7 — FreeCAD `Lattice` tab

**Files:** `cadswarm/gui/lattice_panel.py`, `cadswarm/gui/panel.py` (only to share the session/show helper),
`InitGui.py`, `tests/test_lattice_panel.py`.

**Behavior:**

- [ ] `LatticePanel(QtGui.QDockWidget)` with object name `CadSwarmLattice` and title `Lattice`.
- [ ] `show_lattice_panel()` creates it once, adds it to the right dock area, then calls
      `Gui.getMainWindow().tabifyDockWidget(SESSION.panel, panel)` and raises it. This is the “another tab”
      requirement: it shares the dock area with `CadSwarm Team`, not a separate window.
- [ ] Header: engine availability, current team, job count.
- [ ] Job table: id, source, cell, status, progress, owner, age.
- [ ] Detail: normalized recipe, source rev, warnings, result stats (volume, density, triangles, seconds,
      memory) and error text.
- [ ] Buttons:
      - **New lattice…** — source combo (closed solids only), cell combo, cell size, thickness, skin, open-face
        checkboxes, voxel, seed, estimate, Start.
      - **Cancel**
      - **Apply to model…**
      - **Save 3MF…** and **Save STL…**
      - **Refresh**
- [ ] Optional **Preview** action: import `preview.stl` into a throwaway `CadSwarmLatticePreview` document, never
      the live mirror. Keep this behind a small button; do not build a GPU viewer.
- [ ] Poll `/lattice/jobs` at 1–2 s only while the tab is visible; use the existing background-thread/queue
      pattern; never touch Qt from a worker thread.
- [ ] Add `CadSwarm_Lattice` command in `register_commands`; add it to the toolbar and menu in `InitGui.py`.
- [ ] Human-only actions: apply/export can be done by a human; cancel any job; the tab must explain when the
      engine is unavailable instead of showing an empty table.
- [ ] Ensure the tab does not block the panel or FreeCAD when the hub is down; show a retry state.

**Acceptance:** manual smoke in FreeCAD 1.x: tab appears beside `CadSwarm Team`; a job can be started, watched,
cancelled, applied and exported; the mirror updates after apply; closing the tab does not stop jobs; no Qt
warnings in the report view.

---

## 14. Task 8 — tests and benchmarks

**Files:** `tests/test_lattice_*.py`, `services/lattice/CadSwarm.Lattice.Tests/*`.

### 14.1 Test layers

- [ ] **Contract tests:** schema, defaults, warnings, unknown fields, numbers vs booleans, open-face rules.
- [ ] **Engine unit tests:** STL parsing, each cell, shell/open faces, validation, 3MF structure, deterministic
      stats.
- [ ] **Manager tests:** fake engine; queue, cancel, timeout, restart, retention, disk guard, ownership.
- [ ] **Hub/worker tests:** source snapshot, apply, stale source, name conflicts, path traversal, hash mismatch,
      lock violation, rollback.
- [ ] **MCP tests:** tool list, request shapes, error codes, no path fields.
- [ ] **Integration test:** real FreeCAD worker on a box, with the real engine when available; skip with a
      clear reason when not.
- [ ] **UI smoke:** instantiate the panel offscreen, check tabification, command registration, no thread
      violations.

### 14.2 Benchmarks

Record on the target laptop (state CPU/RAM/OS in the result):

| Case | Target |
|---|---|
| 40×30×12 mm box, 6 mm gyroid, 0.8 mm wall, 0.4 mm voxel | ≤ 60 s, ≤ 2 GB |
| Same, 1.2 mm skin + two open faces | ≤ 90 s, ≤ 2.5 GB |
| 80×60×40 mm box, 6 mm BCC, 0.5 mm voxel | ≤ 5 min, ≤ 6 GB |
| 40×30×12 mm, 0.25 mm voxel | completes or returns `too_large` with a suggested coarser voxel; never OOM-kills the hub |

If the targets are missed, lower the default max voxels and update capabilities; do not hide the limit.

### 14.3 Regression

- [ ] Run the existing suite (213 tests at the time of writing) and add the new tests.
- [ ] Update any test that enumerates MCP tools or op kinds.
- [ ] Verify no test leaves a `LatticeEngine` process, job directory or SQLite lock.
- [ ] Verify `CADSWARM_LATTICE=off` leaves the existing suite green.

**Acceptance:** all layers pass locally; the integration test proves apply/rollback; benchmarks are recorded
in the plan or a linked report; no orphan processes.

---

## 15. Task 9 — packaging, install and rollout

**Files:** `cadswarm/cli.py`, `cadswarm/config.py`, `cadswarm/paths.py`, `install/*`, `README.md`,
`services/lattice/README.md`, `THIRD_PARTY_NOTICES.md`.

**Steps:**

- [ ] Publish the engine self-contained for `win-x64` (and `osx-arm64` if supported). Do not require a .NET
      SDK on teammate machines.
- [ ] Add `cadswarm install-lattice-engine`:
      - download a pinned release archive over HTTPS;
      - verify a pinned SHA-256;
      - extract to `%APPDATA%\CadSwarm\lattice\<version>\`;
      - write a manifest with engine version, PicoGK version, platform and hashes;
      - refuse to run if the hash or manifest is wrong.
- [ ] Add version discovery and a clear `engine_missing` message with the install command.
- [ ] Add `CADSWARM_LATTICE=off` and `CADSWARM_LATTICE_ENGINE` docs.
- [ ] Add licence/attribution files and mention PicoGK/LEAP 71 in the README's third-party section.
- [ ] Update the README with a short “Lattice (optional)” section and a link to the operator/agent guides.
- [ ] Add a feature flag in the tab so the first rollout can be host-only.
- [ ] Roll out: dev machine → one team project → docs → default on. Keep the old workflow untouched if the
      engine is absent.

**Acceptance:** a clean Windows machine without .NET can install the engine, start a job and export; uninstall
removes the engine dir but never project data; `lattice_unavailable` is graceful.

---

## 16. Task ordering and ownership

| Order | Task | Depends on | Suggested owner |
|---:|---|---|---|
| 0 | Feasibility spike | — | engine/CAD |
| 1 | Engine skeleton | 0 | engine |
| 2 | Geometry core | 1 | engine/CAD |
| 3 | Output/validation | 2 | engine |
| 4 | Job store/manager | 1 (can start with fake engine) | Python/backend |
| 5 | Worker/hub integration | 3, 4 | Python/FreeCAD |
| 6 | MCP tools | 5 | Python/MCP |
| 7 | FreeCAD tab | 5 | FreeCAD/UI |
| 8 | Tests/benchmarks | 2–7 | all |
| 9 | Packaging/rollout | 3, 8 | release |

Tasks 4 and 7 can proceed in parallel with 2/3 once the contract and fake engine exist. Task 6 can be written
against a fake hub before Task 5 lands.

---

## 17. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| PicoGK cannot run headless or has no supported Linux build | Feature unavailable on some hosts | Task 0 gate; export-only fallback; platform capability report |
| PicoGK mesh cannot be converted to a closed FreeCAD solid | Apply unreliable | Spike; weld/refine; export-only fallback; never insert broken solids |
| Generation memory/time exceeds a laptop | Hub/OS instability | Hard voxel/memory/time caps; estimate; one slot; kill process tree |
| Source changes mid-job | Stale geometry | Record rev/hash at snapshot; `stale_source` at apply |
| Long generation blocks collaboration | Team friction | No lock during generation; apply is the only mutation |
| Job/result files leak paths or grow unbounded | Security/disk | Private dirs, TTL/cache cap, path whitelist, no path in tool responses |
| 3MF writer is rejected by a slicer | Export failure | Minimal OPC profile; test in FreeCAD + two slicers; keep STL |
| Non-deterministic mesh | Golden tests flaky | Compare stats with tolerance; seed recorded; no byte equality |
| UI scope creep | Schedule risk | Thin tab: status/apply/export; no GPU preview, no full IMPLICIT clone |
| Accidental copying of IMPLICIT material | Legal/reputational | Clean-room rules; independent math; code review; licence/NOTICE |
| Existing tests break | Regression | Additive routes/tools; explicit expected updates; feature flag |

---

## 18. Definition of done (MVP)

- [ ] Engine installs and probes on Windows x64; macOS arm64 if supported; missing engine degrades gracefully.
- [ ] Gyroid, Diamond, Schwarz P, I–WP, BCC, FCC, octet and cubic generate from a closed source.
- [ ] Cell size, uniform thickness, skin, no-skin and two opposite open faces are honored.
- [ ] Result is a closed solid; invalid output is rejected before it reaches the model.
- [ ] Apply is lock-checked, revision-checked and transactional; stale/locked/name conflicts are actionable.
- [ ] STL and 3MF export at the correct millimetre size.
- [ ] Jobs run outside the FreeCAD worker; hub and model stay responsive.
- [ ] The `Lattice` tab is tabified with `CadSwarm Team` and shows status, warnings, apply and export.
- [ ] MCP tools expose the same flow and never accept filesystem paths.
- [ ] Tests cover contract, engine, manager, worker/hub, MCP, UI and the integration path.
- [ ] Benchmarks meet the targets or the limits are lowered and documented.
- [ ] Clean-room and Apache-2.0 notices are in place.
- [ ] README, agent guide and operator guide are updated.

---

## 19. Backlog after MVP

Ordered by user value and dependency:

1. **Noisy layout** — seeded jitter of cell vertices; reuse seed and validation.
2. **Cylinder/sphere frames** — coordinate transforms and clipping; add capabilities and tests.
3. **Thickness grading** — gradient along an axis, distance to surface, then attractors/zones.
4. **TPMS network forms** — gyroid/diamond network; same SDF infrastructure.
5. **Spinodal** — Gaussian random field, sheet/network; stochastic validation.
6. **Voronoi/Delaunay** — Poisson-disk seeds, foam/struts/tubes; FDM self-supporting option.
7. **Exotic TPMS** — Neovius, F–K S/C(Y), F–RD; pore-closing warnings.
8. **FEA import** — accept a stress field and grade thickness; do not build a solver first.
9. **Thermal/flow** — only with a validated model; keep the “approximate” disclaimer.
10. **SIMP** — separate optimiser and solid-result path.
11. **Measured library** — JSON import, provenance and licences.
12. **Density-map 3MF** — only after the base 3MF is accepted by slicers.

Each backlog item gets its own contract revision, capabilities entry and tests. Do not quietly widen v1.

---

## 20. Review checklist for this plan

- [ ] The MVP is still recognisably the IMPLICIT core loop without copying its implementation.
- [ ] The job/apply split is understood and accepted by the team.
- [ ] The one-source/one-output restriction is acceptable for the first release.
- [ ] Windows x64 (and macOS arm64) coverage is acceptable; Linux is explicitly unsupported.
- [ ] The mesh-to-solid risk is accepted pending Task 0.
- [ ] The tab is thin by design; agents are the primary interface.
- [ ] No code will be written until Task 0 and this plan are approved.
