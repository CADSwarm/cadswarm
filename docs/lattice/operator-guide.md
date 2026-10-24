# CadSwarm lattice — operator guide

**Status:** proposed, 2026-10-24. **Nothing described here is installed yet.** The `install-lattice-engine`
command, the `Lattice` tab and the engine do not exist until the
[implementation plan](../superpowers/plans/2026-10-24-cadswarm-06-lattice.md) is executed. This guide is the
operator-facing description of how the feature is meant to behave, so installation, support and rollout can be
reviewed before code is written.

**Audience:** the person hosting the CadSwarm team, and anyone supporting it.
**Related:** [contract](../superpowers/specs/2026-10-24-lattice-contract.md) ·
[implementation plan](../superpowers/plans/2026-10-24-cadswarm-06-lattice.md) ·
[evidence dossier](../superpowers/research/2026-10-24-implicit-build-evidence.md) ·
[agent guide](agent-guide.md) · [documentation index](README.md)

---

## 1. What it adds

A `Lattice` dock tab beside `CadSwarm Team`, plus eight MCP tools for the team's AI helpers. A person or an agent
picks a closed part, chooses a standard lattice cell, sets cell size, wall/strut thickness, skin and open faces,
and starts a job. The result is previewed as statistics (and optionally a coarse mesh), then applied to the model
as a **new** object or exported as STL/3MF.

The heavy work runs in a separate local engine process, not in the FreeCAD worker. The live model stays editable
while a job runs. Only the final apply touches the model, briefly, inside the normal lock and rollback rules.

**Local only.** The engine needs no network. Models are not uploaded anywhere.

---

## 2. Requirements

| Item | Requirement |
|---|---|
| Host OS | Windows 10/11 x64 (primary), macOS arm64 (if the pinned PicoGK build supports it) |
| CadSwarm | Current FastAPI/SQLite hub, FreeCAD 1.x worker, workbench installed |
| FreeCAD | 1.x, as required by CadSwarm today |
| .NET | **Not required on teammate machines.** The engine ships self-contained |
| Disk | Room for the self-contained engine plus the project's lattice cache |
| RAM | 8 GB free is the working assumption; the engine is capped at 8 GB by default |
| Network | Only for the one-time engine download; generation is offline |

**Linux is not supported in v1.** PicoGK's official NuGet package targets Windows x64 and macOS arm64. On Linux
the tab reports the feature as unavailable and the rest of CadSwarm works normally.

**Who needs the engine?** Only the computer that runs the **hub**. Jobs execute in the hub process on the host;
teammates' tabs and agents talk to that hub. A teammate who merely joins someone else's team does not install
anything.

---

## 3. Install the engine

Install it on the host, after CadSwarm itself works.

1. Confirm the hub is running and the `CadSwarm Team` panel is healthy. Do not debug the lattice engine while the
   base install is broken.
2. In a terminal in the CadSwarm checkout (or the installed environment), run:

   ```powershell
   cadswarm install-lattice-engine
   ```

   The command:

   - downloads a pinned engine release over HTTPS;
   - verifies a pinned SHA-256 before extracting anything;
   - extracts to `%APPDATA%\CadSwarm\lattice\<version>\`;
   - writes a manifest with the engine version, PicoGK version, platform and file hashes;
   - refuses to install or run if the hash or manifest is wrong.

3. Restart the hub, and reopen the `Lattice` tab (or FreeCAD) if it was already open.
4. Verify:
   - the `Lattice` tab header shows the engine version and "available";
   - `GET /lattice/capabilities` on the hub returns `"available": true` with the cell list, defaults and limits;
   - `/health` includes a `lattice` block with `available`, `engine`, `platform` and `reason`.

If verification fails, see §9. Do not copy random binaries into the engine folder; the manifest check is the
point.

### 3.1 Upgrade

Re-run the install command with the newer pinned release. Versions live in separate folders, so a rollback is a
matter of pointing `CADSWARM_LATTICE_ENGINE` at the old executable (or reinstalling the old release) and
restarting the hub.

Results record the engine version that produced them. A result from an incompatible engine **major** version is
refused at apply with `engine_mismatch`; regenerate it. Existing exported files are unaffected.

### 3.2 Uninstall

Remove the engine folder, or uninstall it with the CadSwarm installer when that is offered. Uninstalling never
touches project data, the model, `exports/` or `.cadswarm-team/`. The tab then shows the feature as unavailable
and everything else keeps working.

---

## 4. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CADSWARM_LATTICE` | on | `off` disables the feature. Capabilities report `available: false`, reason `disabled`. Generation is refused; nothing else changes |
| `CADSWARM_LATTICE_ENGINE` | auto | Path to a development or side-by-side engine executable. Use only for testing; production uses the installed, manifest-checked build |

Set these in the environment that starts the **hub**, then restart it. Setting them in a teammate's FreeCAD
session has no effect, because that session does not run jobs.

The one-slot queue, time limit, memory cap, source/result size caps and retention limits are engine policy, not
per-user settings in v1. They are reported by `/lattice/capabilities`; do not work around them with ad-hoc
scripts.

---

## 5. The `Lattice` tab

Open it from the CadSwarm toolbar or menu (`CadSwarm_Lattice`). It is tabified with `CadSwarm Team` in the same
dock area — the "another tab" requirement — and can be raised by clicking its tab. Closing it does not stop
jobs.

### 5.1 Layout

| Area | Contents |
|---|---|
| Header | Engine availability and version, current team, number of active jobs |
| Job table | Job id, source, cell, status, progress, owner, age |
| Detail pane | Normalized recipe, source rev, warnings, result stats, error text |
| Actions | New lattice…, Cancel, Apply to model…, Save 3MF…, Save STL…, Preview, Refresh |

### 5.2 Start a job

1. Click **New lattice…**.
2. Choose a source. Only `Part::Feature` objects with exactly one closed solid are listed. If the part you want
   is missing, it is open, empty or multi-solid; fix the model first.
3. Choose a cell: Gyroid, Diamond, Schwarz P, Schoen I–WP, BCC, FCC, Octet or Simple cubic.
4. Set cell size, thickness, skin, open faces, voxel and (optionally) seed.
5. Click **Estimate**. Read the time/memory/density estimate and the warnings.
6. Name the result object (it must not already exist), then click **Start**.

The source revision and a mesh snapshot are recorded when the job starts. You can keep editing the model; the
job does not hold a lock.

### 5.3 Watch and cancel

The table refreshes every 1–2 seconds while the tab is visible. The detail pane shows phase and progress when
available. Select a job and click **Cancel** to stop it. Cancelling a human's own or any teammate's job is
allowed for a human; cancelling never changes the model. A cancelled job leaves the model and locks untouched.

### 5.4 Apply

1. Wait for `done`.
2. Read the warnings and stats: relative density, volume, triangles, watertight.
3. Click **Apply to model…**, confirm the output name, and accept.

Apply is a short transaction through the FreeCAD worker. It:

- checks that the source still exists at the revision recorded at start;
- checks that the result files still match their hashes;
- checks the output name does not already exist;
- checks the normal lock rules for the new name;
- imports the mesh and requires exactly one closed solid with positive volume.

If any check fails, **no object is created and no lock changes**. The job remains `done`, so you can fix the
cause and retry. Typical causes: the source was edited (`stale_source`), the name already exists
(`name_exists`), or another participant reserved the name (`locked`).

v1 never replaces an existing object. If you want the latticed part to take over, delete or rename the old object
yourself after the new one is in place.

### 5.5 Export

Select a `done` job and click **Save 3MF…** or **Save STL…**. The file lands in the project's `exports/` folder
with the normal unique naming, disk guard and storage accounting. 3MF is the default because it is much smaller
for large meshes; it is a single object in millimetres with no colors or materials in v1.

Export does not change the model and is the right choice when the source changed but the result is still wanted.

### 5.6 Preview

If this build enables it, **Preview** imports the coarse `preview.stl` into a throwaway document named
`CadSwarmLatticePreview`. It is never the live mirror and is never used for apply. The final result can look
finer than the preview; trust the result statistics and the exported mesh, not the coarse preview.

### 5.7 When the engine is unavailable

The tab shows the reason instead of an empty table. Common cases:

- `engine_missing` — install the engine (§3), then restart the hub.
- `disabled` — `CADSWARM_LATTICE=off` is set; remove it and restart.
- unsupported platform — the host cannot run the engine; generate on a supported host.
- a development path in `CADSWARM_LATTICE_ENGINE` is wrong or fails its probe.

The rest of CadSwarm — modeling, locks, files, exports, other agents — is unaffected.

---

## 6. Job states

| State | Meaning | What you see / do |
|---|---|---|
| `queued` | Accepted; waiting for the single engine slot | Wait. If it stays here while nothing else runs, see §9 |
| `preparing` | Source mesh exported, hashed and validated | Brief; a failure here is a source problem |
| `generating` | Engine process running | Progress and phase; cancel if needed |
| `packaging` | Output validated, hashed and recorded | Brief; do not close the hub |
| `done` | Result complete | Inspect, apply or export |
| `failed` | Terminal error | Read the error code and `engine.log` |
| `cancelled` | Terminal; cancelled by owner or a human | Model unchanged |
| `expired` | Artifacts removed by retention; metadata remains | Cannot apply/export; regenerate if needed |

There is **one engine slot**. Additional jobs queue FIFO. This bounds memory and makes cancellation predictable;
it is intentional, not a bug.

After a hub restart, any job left `queued`/`preparing`/`generating`/`packaging` is marked `failed` with
`engine_restarted` rather than left as a zombie. Start it again.

---

## 7. Storage, limits and retention

### 7.1 Where it lives

```text
<project>/.cadswarm-team/lattice/
  jobs.sqlite                  job metadata (separate from hub.sqlite)
  jobs/
    lj_7f3a9c2d1e40/
      request.json
      source.stl               snapshot exported by FreeCAD
      result.stl
      result.3mf
      preview.stl
      result.json
      engine.log
```

Job directories are private to the host process. Agents never receive their paths; they only see job ids and
stats. The source snapshot is produced by the trusted FreeCAD worker, not by an agent.

### 7.2 Limits

- One job at a time; one engine process.
- Default wall clock limit: 900 s per job.
- Default memory cap: 8 GB.
- Source cap: 100 MB; result cap: 250 MB.
- Maximum voxels: 150 million (reported by capabilities).
- Queue and retention are host policy; do not raise caps by editing files.

Requests beyond a cap fail early with `too_large`, `no_room`, `timeout` or `out_of_memory` and an actionable
message. They do not take down the hub.

### 7.3 Retention

- Default cache limits: **5 GB or 7 days**, whichever comes first, and at most **50 retained jobs**.
- Cancelled, failed and expired jobs are cleaned before done jobs.
- Applied jobs keep metadata, but artifacts may be removed after 24 hours.
- Files in `exports/` are independent and are **never** deleted automatically.
- Retention runs at hub start and hourly.

### 7.4 Storage dialog

**Team tools > Storage and free up space...** shows a separate `lattice_cache` line alongside files, old
versions, snapshots and exports, so lattice jobs do not hide inside the project total. The disk guard applies
before a job starts and before a result is packaged; a refusal is `no_room`. The existing file/export guard still
reports `disk_full`.

### 7.5 Manual cleanup

Lattice jobs are a cache. If you must reclaim space immediately:

1. Stop the hub (so no engine is running).
2. Delete `.cadswarm-team/lattice/` (or only `lattice/jobs/`).
3. Restart the hub.

You lose job history and any unexported results; the model, `exports/`, snapshots and project files are
unaffected. Never delete `.cadswarm-team/` itself. Prefer letting retention do its job.

---

## 8. Permissions

| Action | Agent owner | Teammate | Human |
|---|---|---|---|
| See a job | yes | yes | yes |
| Start a job | yes | yes | yes |
| Cancel | own jobs | no | any job |
| Apply | own jobs | no | any done job |
| Export | own jobs | no | any done job |
| Rename at apply | no (must match) | no | yes |

Applying still obeys the normal lock rules. A name reserved by another participant is refused. Pre-reserving
the output name with `lock_objects` is the agent-side convention; a human can lock through the panel.

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Lattice` command/tab missing | Workbench not reloaded, or old workbench install | Restart FreeCAD; re-run `cadswarm install-workbench`; check the CadSwarm workbench is active |
| Header says engine missing | Engine not installed on the host, or manifest invalid | Run `cadswarm install-lattice-engine`, restart the hub, check `/lattice/capabilities` |
| `available: false`, reason `disabled` | `CADSWARM_LATTICE=off` | Remove the variable from the hub environment and restart |
| `available: false`, unsupported platform | Linux or an unsupported architecture | Host the team on Windows x64 or macOS arm64 |
| Job stays `queued` with nothing running | Previous engine hung, or hub was restarted | Cancel it; restart the hub; check for a stray engine process |
| Job fails immediately in `preparing` | Source is open, empty or multi-solid | Choose a different closed solid; check the source in FreeCAD |
| `too_large` at start | Voxel/result/source limits | Increase voxel or cell size; use a smaller source; re-estimate |
| `out_of_memory` | Part too large for the 8 GB cap | Coarsen the voxel; split the part; do not raise the cap casually |
| `timeout` | 900 s exceeded | Coarsen the voxel, increase cell size, or split the part |
| `engine_failed` | Engine crash or unreadable output | Read the job's `engine.log`; retry once; report the job id and log |
| `mesh_invalid` / `not_closed` | Result failed geometric checks | Retry with a thicker wall/strut or finer voxel; if it repeats, report a bug |
| Apply says `stale_source` | Source changed after the snapshot | Regenerate from the current source, or export the old result |
| Apply says `name_exists` | Output object already exists | Choose a different name; do not delete the existing part to force it |
| Apply says `locked` / `not_locked` | Normal lock rules | Lock the output name, release the conflicting lock, or pick another name |
| `no_room` | Disk guard or project limit | Free space (see §7.5) or raise the project limit in the storage dialog |
| `engine_mismatch` | Result from another engine major version | Regenerate with the installed engine |
| Tab shows "hub unavailable" | Hub down or unreachable | Start the hub; check the team's URL/token; the tab retries |
| FreeCAD feels slow during a job | Engine uses host CPU/RAM | Expected; one slot limits it. Cancel or wait; do not run several jobs |
| CPU stays high after cancel | Engine child survived a crash | Restart the hub; report it with the job id |

When reporting a problem, include: job id, error code, what you clicked, FreeCAD version, OS, RAM, whether the
engine is installed and its version, and the relevant lines from `engine.log`. Do not send the model unless the
team agrees.

---

## 10. Performance expectations

These are **design targets**, not measurements. Task 8 records actual numbers on the target laptop; update this
table then.

| Case | Target |
|---|---|
| 40×30×12 mm box, 6 mm gyroid, 0.8 mm wall, 0.4 mm voxel | ≤ 60 s, ≤ 2 GB |
| Same, 1.2 mm skin + two open faces | ≤ 90 s, ≤ 2.5 GB |
| 80×60×40 mm box, 6 mm BCC, 0.5 mm voxel | ≤ 5 min, ≤ 6 GB |
| 40×30×12 mm, 0.25 mm voxel | completes, or `too_large` with a suggested coarser voxel; never OOM-kills the hub |

Practical rules:

- Voxel size dominates cost; halving it is roughly eight times the work.
- One job at a time is deliberate. Queueing several large jobs will not make them faster.
- Keep the host awake and plugged in during a long job. Sleep can stall or fail it.
- Generation is outside the FreeCAD worker, but it still competes for CPU and memory on the host.

---

## 11. Security and privacy

- The engine is a local, trusted executable. It needs no network and has no telemetry or hosted fallback.
- It runs as the host user, in its own process, with a wall-clock and memory cap and kill-on-close. On Windows it
  is launched in a Job object; on macOS in a process group with resource limits.
- It is **not** the same sandbox as `run_part_script`. It does not have the AppContainer's file/network
  isolation. Treat the pinned, hash-verified release as trusted code; do not point `CADSWARM_LATTICE_ENGINE` at
  an untrusted binary.
- The engine reads only its job directory and writes only `result.stl`, `result.3mf`, `preview.stl`,
  `result.json` and `engine.log`.
- The source snapshot is written by the FreeCAD worker, not by an agent. Agents cannot pass paths or hashes.
- The worker accepts the internal `lattice_import` op only for a job directory resolved by the hub, and checks
  `realpath` containment and the result SHA-256. `lattice_import` is not a public MCP tool.
- Result files are validated before apply: finite coordinates, closed/manifold edges, positive volume, bbox and
  volume sanity. A bad mesh is refused, never silently inserted.
- The lattice source mesh is stored locally in the project cache. It is not uploaded anywhere.

---

## 12. Known limits (v1)

- Windows x64 and, if the pinned build supports it, macOS arm64. No Linux.
- One closed `Part::Feature` per job; no multi-body.
- Box frame only, regular layout only, uniform thickness only.
- Eight cells: Gyroid, Diamond, Schwarz P, Schoen I–WP, BCC, FCC, Octet, Simple cubic.
- No FEA, thermal, buckling, compression, flow/aero, SIMP or Verify.
- No thickness grading, zones, attractors or stress-driven thickness.
- No noisy/conformal layouts, cylinder/sphere frames, Voronoi/Delaunay/spinodal, or exotic TPMS.
- 3MF is a single millimetre object with no colors, materials or density map.
- No replace-in-place; apply always creates a new object.
- One engine slot; large jobs queue and can take minutes.
- A source edit during generation makes the result stale for apply (it can still be exported).

These are deliberate MVP boundaries, not bugs. Each deferred item is listed in the plan's backlog and needs its
own contract revision.

---

## 13. Rollout checklist

- [ ] Base CadSwarm works on the host: hub, panel, one agent, one export.
- [ ] Engine installed on the host and verified through `/lattice/capabilities`.
- [ ] A small box generates, applies and exports STL and 3MF at the correct size.
- [ ] A second teammate sees the job, but cannot apply/export it.
- [ ] A source edit during a job produces `stale_source` at apply and no model change.
- [ ] Cancelling a running job leaves no engine process.
- [ ] `CADSWARM_LATTICE=off` hides the feature and leaves the existing suite green.
- [ ] Storage shows `lattice_cache`; retention runs after a restart.
- [ ] The agent guide has been shared with the team, and agents know the flow is job-based.

Roll out in stages: developer machine → one real project → team docs → default on. Keep the old workflow
available at every stage; a missing engine must never block modeling.

---

## 14. Support notes

- The contract is the source of truth for field names, limits and error codes. If the tab and this guide
  disagree with the contract, the contract wins and the docs need fixing.
- Do not promise IMPLICIT parity. The MVP reproduces the core loop — choose a part, choose a cell, set size,
  thickness, skin and open faces, generate, export — not its simulation, grading or UI.
- Use the product's own vocabulary only where it is generic engineering language (cell, skin, wall, voxel). Do
  not copy IMPLICIT strings, assets or branding; see the evidence dossier §1 and §7.
- PicoGK and the LEAP 71 libraries are Apache-2.0. Keep versions pinned and ship the licence texts and
  `THIRD_PARTY_NOTICES.md` with any distribution.
