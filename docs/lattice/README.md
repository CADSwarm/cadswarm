# CadSwarm lattice documentation

This folder documents a proposed CadSwarm capability: local, agent-accessible lattice generation for a closed
part in the shared FreeCAD model, with a thin `Lattice` tab and MCP tools for the team's AI helpers.

**Status: proposed, 2026-10-24. No implementation code has been written.** The documents exist so the feature
can be reviewed, scoped and implemented without rediscovering the design. Commands, tools and tabs described
here do not exist yet. A throwaway headless test of the geometry kernel is recorded in
[the PicoGK spike](spike-2026-10-24.md); it produced an STL outside the product and changed no CadSwarm code.

**Provenance:** the capability is a clean-room reimplementation of the core workflow observed in
[IMPLICIT](https://implicit.build/), using PicoGK 2.3.0 and the LEAP 71 libraries (Apache-2.0). No IMPLICIT
code, assets or strings are used. See the evidence dossier for the boundary and the artifact record.

---

## 1. Document map

| Document | Audience | What it answers |
|---|---|---|
| [Evidence dossier](../superpowers/research/2026-10-24-implicit-build-evidence.md) | reviewers, architects | What was observed in IMPLICIT, how, what is inferred, what remains unknown, and what must not be copied |
| [Contract v1](../superpowers/specs/2026-10-24-lattice-contract.md) | implementers, agent developers | Exact job/apply contract: parameters, states, routes, MCP tools, errors, storage, security, test vectors |
| [Implementation plan](../superpowers/plans/2026-10-24-cadswarm-06-lattice.md) | implementers, project lead | Phased tasks 0–9, repository layout, acceptance criteria, benchmarks, risks, backlog |
| [Agent guide](agent-guide.md) | AI helpers (Claude Code, Codex) | How to use the tools, choose parameters, read warnings, recover from errors, and stay safe |
| [Operator guide](operator-guide.md) | host, support, rollout | Install, configure, run the tab, job states, storage, troubleshooting, limits, security |
| [PicoGK spike, 2026-10-24](spike-2026-10-24.md) | implementers | What a throwaway headless test of the kernel proved (speed, mesh size, process shape) and what it did not |

The contract is the source of truth for names, fields, limits and error codes. If a guide disagrees with it, the
contract wins and the guide is wrong.

---

## 2. Reading order

- **Reviewing the idea:** evidence dossier → contract §1–§5 → plan §1–§3 → this page.
- **Implementing the engine:** plan Task 0 → contract §4, §9, §10 → evidence dossier §8.
- **Implementing the hub/worker:** contract §7–§12 → plan Task 4–5 → `cadswarm/hub/core.py`,
  `cadswarm/worker/freecad_worker.py`.
- **Implementing MCP tools:** contract §5–§7, §10 → agent guide → plan Task 6.
- **Implementing the tab:** plan Task 7 → operator guide §5 → `cadswarm/gui/panel.py`, `InitGui.py`.
- **Hosting or supporting:** operator guide → plan §15 → this page.

---

## 3. What the MVP is

The core loop, not the whole product:

1. Pick one closed solid in the shared model.
2. Choose a standard cell: Gyroid, Diamond, Schwarz P, Schoen I–WP, BCC, FCC, Octet or Simple cubic.
3. Set cell size, uniform wall/strut thickness, skin, open faces and voxel size.
4. Generate as a background job; inspect warnings and statistics.
5. Apply the result as a new object under the existing lock and revision rules, or export STL/3MF.

| In scope (v1) | Deferred |
|---|---|
| 4 TPMS sheet cells, 4 beam cells | Exotic TPMS, TPMS network forms |
| Regular box layout | Noisy, conformal layouts |
| Box frame fitted to the bounding box | Cylinder, sphere frames |
| Uniform thickness | Gradient, distance, attractor, zone, FEA-driven grading |
| Closed skin, no skin, axis-aligned open faces | Arbitrary face selection |
| Job queue, progress, cancel, retention | Multi-job parallelism |
| One closed solid per job | Multi-body lattices |
| STL and single-object millimetre 3MF | Colors, materials, density-map 3MF |
| MCP tools and a thin FreeCAD tab | FEA, thermal, buckling, compression, flow, SIMP, Verify |
| Windows x64 (macOS arm64 if supported) | Linux |

The plan's backlog orders the deferred work; each item needs its own contract revision.

---

## 4. Invariants

These hold across the contract, plan and guides:

1. **Generation is a job; apply is a transaction.** A long build never runs inside the single FreeCAD worker and
   never holds the model lock.
2. **The source is snapshotted atomically.** Mesh and revision are captured together.
3. **Apply is revision-safe.** A changed source yields `stale_source`; a stale result is never inserted.
4. **Apply is lock-safe and rollback-safe.** It uses the normal creation rules; failure leaves no object and no
   lock change.
5. **Agents never pass paths.** They name a job and an output object; the hub resolves the rest.
6. **Everything is millimetres.** 3MF declares millimetres; STL is interpreted as millimetres.
7. **The engine is optional.** Missing or disabled means `lattice_unavailable`, and the rest of CadSwarm is
   unaffected.
8. **Local only.** No uploads, no telemetry, no hosted fallback.
9. **Additive.** Existing ops, tools, locks and storage behavior do not change.
10. **No parity claim.** The MVP reproduces the workflow, not IMPLICIT's simulation, grading or UI.

---

## 5. Architecture at a glance

```text
 agent / Lattice tab
     │  existing HTTP auth, fence and lock rules
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

- Snapshot: short worker query under the hub lock.
- Generate: external one-shot process; the hub lock is free.
- Apply: short worker mutation inside a normal transaction.
- Export: copy to `exports/`; no model change.

---

## 6. Where the code will live

From the plan (§4), for orientation only — none of these files exist yet:

```text
services/lattice/                 .NET 9 engine: CLI, params, cells, build, output, validation, tests
cadswarm/lattice/                 Python contract, job store, engine supervisor, manager
cadswarm/gui/lattice_panel.py     FreeCAD dock tab
cadswarm/hub/core.py, app.py      internal apply hook, HTTP routes
cadswarm/worker/freecad_worker.py lattice_source query, lattice_import mutation
cadswarm/mcp_server.py            eight agent tools
InitGui.py                        command, toolbar, menu
tests/test_lattice_*.py           contract, store, manager, worker, integration, MCP, panel
```

The engine is a separate solution so it can be built, tested and shipped without a .NET SDK on teammate
machines.

---

## 7. Open decisions before implementation

These are recorded in the plan and evidence dossier, and should be settled first:

1. Task 0 spike: can PicoGK 2.3.0 run headless on the host, and how reliably does its STL round-trip into a
   closed FreeCAD solid? If not, the plan pivots to export-only.
2. Platform coverage: is macOS arm64 supported by the pinned build, or is v1 Windows-only?
3. Host-only rollout: is "engine on the hub machine only" acceptable for the hackathon, or does each teammate
   need a local engine?
4. Parity scope: is the eight-cell MVP enough for the demo, or is one grading option (for example uniform axial
   gradient) worth pulling forward?
5. Contract timing: implement against the current FastAPI/SQLite hub, or wait for the SpacetimeDB host? The
   contract preserves the tool shape either way.

---

## 8. Clean-room and licences

- IMPLICIT is proprietary. The public page and bundles were used as **reference** for observable behavior only.
  No code, assets, locale strings, shaders, WASM or recipe schema are copied. Do not add any.
- Public mathematical definitions of TPMS surfaces and beam cells are not proprietary; the implementation must
  still be CadSwarm's own.
- PicoGK, LEAP 71 ShapeKernel and the LEAP 71 Lattice Library are Apache-2.0. Pin exact versions, keep the
  licence texts, and ship `THIRD_PARTY_NOTICES.md` with any distribution. Re-verify before release.
- The evidence dossier is engineering input, not legal advice.

---

## 9. Change control

- The contract is versioned (`lattice/1`). A new optional field or cell is a minor revision; a changed meaning
  or removed field is a major revision.
- If the IMPLICIT bundle changes, record the new artifact hashes and re-check every "observed" claim before
  updating the plan. Do not carry conclusions across builds silently.
- Update this index whenever a document is added, renamed or retired, and fix cross-links in the same change.
- No document may claim the feature works before the plan's definition of done is met and the benchmarks are
  recorded.
