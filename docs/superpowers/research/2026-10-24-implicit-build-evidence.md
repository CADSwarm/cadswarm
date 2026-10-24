# IMPLICIT reverse-engineering dossier

**Status:** evidence and inference only. No product code was copied, decompiled, or executed outside the
public browser app. This document feeds the CadSwarm lattice plan; it is not an implementation.

**Target:** <https://implicit.build/>  
**Observation date:** 2026-10-24  
**Method reference:** the evidence and provenance discipline used by
[`morluto/rea`](https://github.com/morluto/rea): keep observed facts, derivations, inferences and unknowns
separate; record artifact identity and retrieval limits; never turn an absent observation into a negative
claim.

---

## 1. Scope, authority and clean-room boundary

The target is a publicly reachable, client-side web application. The review was limited to:

- loading the public page and its advertised assets,
- reading the rendered UI, tutorials, FAQ and pricing text,
- reading the public JavaScript bundles as text,
- extracting the English locale dictionary,
- checking public package and dependency metadata,
- reading the public PicoGK and LEAP 71 Lattice Library documentation and licences.

No account was created, no paywall or access control was bypassed, no private endpoint was queried, and no
network capture, runtime hook or debugger was attached. No source map was found in the retrieved assets, so
there is no original TypeScript source to compare.

The site identifies itself as proprietary: `Proprietary software © 2026 IMPLICIT. All rights reserved.` Its
`robots.txt` carries `Content-Signal: search=yes,ai-train=no,use=reference` and then a blanket
`User-agent: * / Disallow: /` block. This work uses the public material as **reference**, not as training
data, and does not redistribute the target's code or assets. Any implementation must be a clean-room
reimplementation from observed behaviour and public, permissively licensed components.

**Clean-room rules for CadSwarm:**

1. Do not copy, translate, minify, de-minify, vendor or adapt IMPLICIT JavaScript, CSS, shaders, icons,
   locale strings, WASM binaries, 3MF writers or `implicit-step`.
2. Do not reuse the IMPLICIT name, visual identity, wording or tutorial copy in the product.
3. Implement standard lattice mathematics and workflows independently. Public mathematical definitions of
   TPMS surfaces and beam cells are not proprietary.
4. Use PicoGK and the LEAP 71 libraries only under their Apache-2.0 terms, with attribution and a NOTICE file.
5. Re-verify licences and terms before shipping. This dossier is engineering input, not legal advice.

---

## 2. Artifact inventory

Retrieved over HTTPS from the public origin. SHA-256 values were computed over the saved bytes.

| Artifact | Bytes | SHA-256 | What it establishes |
|---|---:|---|---|
| `/` (`index.html`) | 698 | `656aafdf7a001352cce7eae6a08ff65e7782fa8172e286c01d4c70ee8d6079ea` | Single-page app shell; module entry point |
| `/assets/index-C4MR6No2.js` | 1,165,540 | `a6f3f1f8e9273c0148b07c10fc947fd6bded81e9bd98231892a5ad07e7593d33` | Application shell, embedded package metadata, UI text, workflow |
| `/assets/hooks-BTrflyEv.js` | 1,300,250 | `105b0b3a25dca6a61e9897af7a0021e3e3ae45bd99cea65ae9f0a31a453b3ea7` | Engine hooks, locale dictionary, feature strings, error mapping |
| `/assets/bodies-CXphfTmZ.js` | 133,702 | `ef255981c06afaa3265cc958a27e8406d12e99fed37fdca91b20a6a40b2cc30f` | Additional domain code |
| `/assets/index-Bj_erlC_.css` | 156,927 | `66cf47ac2a3e5a416475b78123d27e69646b041c6d4b87b92d76b813d6fe60b7` | Visual system only; not used |
| `/manifest.json` | 1,065 | `ba9a6d224c78a645566026c273abe8ee2ce44084725834d6c04dfd37f3ede3bd` | App metadata |
| `/robots.txt` | 2,702 | `da0cadf9e179e3feb4c15154e869c44d0e541d81dc9b8202f32b1b4ac113aa4c` | Content signals; reference-only boundary |

Local extraction artifacts (not product code):

| File | Bytes | Purpose |
|---|---:|---|
| `%TEMP%\opencode\implicit_i18n_en.txt` | 218,325 | English locale dictionary extracted from `hooks-BTrflyEv.js` |
| `%TEMP%\opencode\implicit_en_filtered.txt` | 52,337 | Lattice, simulation and export strings |
| `%TEMP%\opencode\implicit_i18n_filtered.txt` | 61,281 | Broader feature vocabulary |

**Retrieval limits.** The bundles are minified and can change without notice. The hashes above identify the
observed build only. No source maps, server-rendered pages, authenticated flows, native binaries or network
bodies were observed. Strings prove that a capability is named or described; they do not prove its numerical
model, quality or exact algorithm.

---

## 3. What the product does (observed)

### 3.1 End-to-end workflow

The public UI describes four workflow steps:

1. **Prepare** — import a part, inspect bodies and units, choose material and print direction.
2. **Simulate** — optional supports, loads, material model and analysis resolution.
3. **Lattice** — choose a cell family, cell size, wall or beam thickness, skin and layout; preview, then
   generate the final result.
4. **Export** — inspect the result summary and download STL or 3MF.

The FAQ and tutorial text confirm the same flow and describe the product as running entirely in the browser.
The diagnostics string states, in English: geometry is **PicoGK (LEAP 71), compiled to WebAssembly**, and STEP
import uses an **in-house kernel (`implicit-step`)**. The privacy string states that the file never leaves the
computer.

### 3.2 Import and units

- Accepted formats: STEP, STL and 3MF.
- STEP gets an explicit unit/scale step; STL is treated as millimetres unless the user changes the scale.
- Declared units in a file are converted to millimetres.
- Multi-body parts can be inspected, isolated, shown or deleted; the last body cannot be deleted.
- Import errors are specific: unsupported format, empty surface, truncated STL, malformed STL, damaged 3MF.
- The simulation path needs an imported part with faces that can be picked.

### 3.3 Lattice families observed

The FAQ, tutorial and locale dictionary name three families:

- **TPMS surfaces:** Gyroid, Diamond, Schwarz P, Schoen I-WP, Neovius, Fischer–Koch S, Schoen F-RD,
  Fischer–Koch C(Y), plus gyroid-network and diamond-network forms.
- **Beams:** BCC, FCC, octet truss, octahedron, simple cubic.
- **Stochastic:** Voronoi (foam or struts), Delaunay tubes, and Spinodal (sheet or network).

The UI distinguishes TPMS **sheet** and **network** forms. It also warns when a chosen wall closes a channel
system (for example Schwarz P at a stated approximate relative density), when pores are narrower than the
machine's powder/resin clearance, and when a closed skin traps powder.

### 3.4 Lattice parameters observed

| Concept | Observed controls |
|---|---|
| Cell family | TPMS, beams, stochastic |
| Cell type | The families and cells above |
| Cell size | `cell size`; Voronoi uses seed spacing; Spinodal uses wavelength |
| Thickness | `wall thickness` for TPMS sheets; `beam diameter` for beams; strut neck or ligament width for network forms |
| Thickness mode | Uniform; gradient along an axis; distance to surface; attractor points; FEA field; zones |
| Thickness range | Minimum and maximum wall/diameter; depth or radius; axis and reverse |
| Attractors | Centre XYZ, radius, edge, smooth falloff |
| Zones | Sphere or cube with its own wall thickness |
| Skin | No skin, closed skin, or selected skinless faces; skin thickness |
| Cell layout | Regular, noisy, conformal |
| Frame | Box fitted to the part, cylindrical aligned, spherical |
| Noise | Fraction of a cell |
| Conformal frames | Inner/outer radius, axis |
| Resolution | Voxel size; analysis voxel for simulation |
| Preview | Separate preview from final generation; parameters can keep changing while preview runs |
| Seeding | Voronoi seed and re-seed |
| Printability | FDM self-supporting option for Voronoi/Delaunay; overhang warnings |

### 3.5 Simulation and optimisation observed

These are named and described in the public product, but are **not** part of the CadSwarm MVP:

- linear static FEA with fixed/sliding supports, force/pressure/moment loads, von Mises stress, displacement
  and safety factor;
- steady-state thermal analysis and heat-exchange/flow proxies;
- SIMP topology optimisation (“organic shape”);
- lattice buckling;
- flexible-cell compression with a Yeoh material fit;
- aerodynamic optimisation and duct/body export;
- Verify (re-simulate the latticed part).

The FEA text explicitly calls the analysis approximate and says it is not a structural verification. This is
useful positioning: do not present a future CadSwarm check as certification.

### 3.6 Export observed

- STL and 3MF are both offered; 3MF is recommended because it is roughly five times smaller for large meshes.
- The summary shows cell, wall, skin and voxel; lattice and solid mass; weight saving; triangle/vertex counts;
  time and peak memory.
- “Density-map 3MF” is described as coming soon.
- A recipe string describes the generated result; project files preserve settings.

### 3.7 Product and packaging signals

The JavaScript bundle embeds a package manifest. Observed package names and versions:

```text
@lattice-studio/web                  0.0.0 (private)
@lattice-studio/aero                 workspace:*
@lattice-studio/engine-core          workspace:*
@lattice-studio/kernels              workspace:*
@lattice-studio/picovoxel-wasm64     workspace:*
@lattice-studio/recipe               workspace:*
@lattice-studio/simp                 workspace:*
@lattice-studio/step                 workspace:*
manifold-3d                          3.2.1
picovoxel                            0.1.0
fflate                               0.8.3
three                                0.186.1
react                                19.3.0
react-dom                            19.3.0
zustand                              5.0.15
```

The diagnostics text separately names **PicoGK** as the geometry kernel compiled to WebAssembly and
**implicit-step** as the in-house STEP importer. The presence of `manifold-3d` is consistent with mesh boolean
or repair work; `fflate` is consistent with in-browser ZIP handling, including 3MF. These are inferences from
package names, not proof of where each library is called.

The manifest also references test scripts for TPMS, stochastic, Voronoi, grading, FDM, thermal, compression,
flow, SIMP, project trees, heat exchange, Verify and tutorials. Those script names corroborate the feature
vocabulary; they do not reveal implementations.

---

## 4. Architecture reconstruction

### 4.1 Confirmed

- The app is a single-page JavaScript application.
- Geometry is PicoGK compiled to WebAssembly.
- STEP import is a separate in-house module.
- The product claims local-only processing: no upload of the user's model.
- The workflow is staged: prepare, simulate (optional), lattice, export.
- Preview and final generation are distinct; the final result is generated at a chosen resolution.
- The app supports STL and 3MF output.

### 4.2 Strongly inferred

- The app uses a voxel/implicit geometry pipeline: PicoGK voxelizes the source, builds the lattice field or
  beam network, applies skin and Boolean operations, then meshes the result.
- `manifold-3d` is likely used for mesh-level validation, repair or booleans outside PicoGK.
- `three` is the viewport; WebGPU ray-marching and a coarse PicoGK mesh are two preview paths.
- `fflate` likely packages 3MF (a ZIP container) and may compress project files.
- The recipe/package modules exist to serialize a reproducible parameter set.
- The simulation path uses a voxel grid, supports and loads applied to faces, and a linear solver; the exact
  solver, material database and convergence criteria are unknown.

### 4.3 Not established

- The exact TPMS equations, phase offsets, unit-cell normalization and orientation conventions.
- The exact skin construction (offset, shell, Boolean order) and how open faces are cut.
- Whether the final mesh is watertight by construction or repaired afterwards.
- The default voxel size, adaptive resolution rules, meshing tolerance and decimation policy.
- How the preview accelerates large lattices and when it falls back to a coarse mesh.
- The FEA formulation, solver, preconditioner, material data provenance and validation status.
- The stochastic seed algorithm and the self-supporting overhang rule.
- The 3MF writer's exact metadata, color/material extensions and unit handling.
- The pricing, account and entitlement model; these are outside the engineering scope.
- Whether any telemetry exists despite the local-processing claim. No network bodies were inspected, so this
  remains unknown rather than denied.

---

## 5. Feature matrix and CadSwarm decision

Legend: **Observed** = named or described in public material; **Inferred** = consistent evidence but not
directly confirmed; **MVP** = planned for the first CadSwarm lattice release; **Later** = deliberately deferred.

| Capability | Evidence | CadSwarm decision |
|---|---|---|
| STEP/STL/3MF import | Observed | Existing FreeCAD import plus a source-mesh path; STEP handling stays FreeCAD's |
| Unit/scale handling | Observed | Millimetres internally; explicit source scale check |
| Multi-body handling | Observed | MVP: one closed solid per job; multi-body later |
| TPMS sheet cells | Observed | MVP: Gyroid, Diamond, Schwarz P, I–WP |
| TPMS network cells | Observed | Later |
| Exotic TPMS (Neovius, F–K S, F–RD, F–K C(Y)) | Observed | Later |
| Beam cells | Observed | MVP: BCC, FCC, octet, simple cubic |
| Stochastic cells | Observed | Later (Spinodal first, then Voronoi/Delaunay) |
| Regular cell layout | Observed | MVP |
| Noisy layout | Observed | Later |
| Conformal layout | Observed | Later |
| Box frame | Observed | MVP (fitted bounding box) |
| Cylinder/sphere frames | Observed | Later |
| Uniform wall/beam | Observed | MVP |
| Gradient/distance/attractor/zones/FEA grading | Observed | Later |
| Closed skin + skin thickness | Observed | MVP |
| Open/skinless faces | Observed | MVP, axis-aligned bounding-box faces |
| Powder-trap and pore warnings | Observed | MVP: basic wall/pore/skin warnings |
| Preview while editing | Observed | MVP: job status and optional coarse preview; no GPU ray-marcher |
| Final generation at chosen voxel | Observed | MVP |
| STL export | Observed | MVP |
| 3MF export | Observed | MVP (single-object, millimetre) |
| Mass / weight saving | Observed | MVP: computed from result volume and a density input |
| FEA / thermal / buckling / compression / flow / aero / SIMP / Verify | Observed | Later; no claims of parity |
| Measured-lattice library | Observed | Later |
| Project/recipe persistence | Observed | MVP: job request and result metadata; model integration via FreeCAD |

**Parity target for the MVP:** the observable core loop — choose a part, choose a standard cell, set cell size,
wall/beam thickness, skin and open faces, generate, inspect the result, and export STL/3MF — without copying
the product's UI, code or assets.

---

## 6. Behavioural invariants worth preserving

These are the product behaviours that matter to users and agents, independent of implementation:

1. **A lattice is generated, not merely a material setting.** The source solid and the result are distinct;
   the result can be regenerated from a recipe.
2. **Preview is not final.** The final mesh is generated at the chosen resolution; changing parameters after a
   preview does not silently mutate an existing result.
3. **The source must be a solid.** Simulation and lattice generation need a closed, non-empty part. Thin or
   open meshes produce actionable errors.
4. **Units are explicit.** STL carries no units; treat it as millimetres and expose a scale control. STEP/3MF
   declarations are converted to millimetres.
5. **Skin and open faces are a manufacturing decision.** A closed skin can trap powder or resin; open faces
   should be large enough and ideally on opposite sides for powder removal.
6. **Cell size, wall and voxel interact.** A wall thinner than a couple of voxels, or a cell with too few
   voxels across, cannot be represented faithfully. Warn before generating.
7. **Export is reproducible.** The recipe (cell, size, thickness, skin, voxel, seed) is part of the result
   metadata, so the same inputs can be regenerated.
8. **Local processing is a trust property.** For CadSwarm, the lattice engine must run on the team's machine;
   the source model must not be uploaded to a third-party service.

---

## 7. What CadSwarm should not copy

- The IMPLICIT look, layout, icons, illustrations, tutorial prose, names and marketing claims.
- The exact locale strings or error text. Write original, actionable messages.
- The WASM build, shaders, preview implementation, recipe schema, 3MF writer or STEP importer.
- Any reverse-engineered source reconstruction. The bundles were read as evidence, not used as a source base.
- Any claim that a CadSwarm check is a certification or a substitute for physical testing.

PicoGK, LEAP 71 ShapeKernel and LEAP 71 Lattice Library are Apache-2.0. If their code or binaries are shipped,
include the licence text, copyright notices and a NOTICE file, and keep the versions pinned. The public
mathematical definitions of gyroid, diamond, Schwarz P, I–WP, BCC, FCC and octet cells are not proprietary;
the implementation must still be CadSwarm's own.

---

## 8. Open questions to resolve in the implementation spike

These are engineering unknowns, not gaps in the evidence that can be filled by more reading:

1. Does PicoGK 2.3.0 expose a supported headless entry point on the target platforms, or does it always
   create a viewer? Which call sequence is safe for a console process?
2. What is the exact mesh round-trip? PicoGK mesh → STL → FreeCAD `MeshPart` → `Part.Solid` may fail for
   non-manifold or self-intersecting output. Measure the success rate on representative lattices.
3. How much memory and time does a 50 mm cubic part at 0.4 mm voxel take on the target laptop? What is a safe
   default cap?
4. Is PicoGK output deterministic for the same inputs and seed? If not, compare geometric statistics with
   tolerances, not bytes.
5. Which 3MF profile do common slicers accept from a minimal writer (single object, millimetre, no extensions)?
6. Does FreeCAD's `Mesh.export` preserve the exact placement and tessellation needed for a source snapshot?
7. Can a job be cancelled cleanly on Windows without leaving a child process or locked files?
8. How should the engine be packaged and updated without requiring a .NET SDK on every teammate's machine?

The plan's Task 0 is a time-boxed spike to answer 1–4 and 7 before the rest of the work is committed.

---

## 9. Reproduction acceptance criteria

The implementation can claim “core behaviour reproduced” only when all of the following are demonstrated on a
clean machine:

- [ ] A closed STEP or STL part imports and is voxelized without uploading it.
- [ ] Gyroid, Diamond, Schwarz P, I–WP, BCC, FCC, octet and simple-cubic lattices generate at a user-set cell
      size and uniform thickness.
- [ ] Skin, no-skin and two opposite open faces behave visibly and measurably as described.
- [ ] The result is a closed solid that FreeCAD can measure and export; an invalid mesh is refused, not
      silently inserted.
- [ ] The same recipe and seed produce the same geometric statistics within tolerance.
- [ ] STL and 3MF open in a slicer at the correct millimetre size.
- [ ] The tab and the MCP tools show job state, warnings and errors without blocking the live FreeCAD worker.
- [ ] A source edit between generation and apply is refused with `stale_source`; a lock violation is refused
      with the existing lock error.
- [ ] The engine runs locally, needs no network, and leaves no orphan process after cancel, timeout or crash.

---

## 10. Provenance log

| Date | Step | Result |
|---|---|---|
| 2026-10-24 | Loaded public landing page, tutorials, FAQ, pricing | Workflow and feature vocabulary |
| 2026-10-24 | Retrieved module entry and advertised bundles | Artifact hashes and sizes above |
| 2026-10-24 | Extracted English locale dictionary | Parameter and warning vocabulary |
| 2026-10-24 | Read embedded package manifest | Package names and versions |
| 2026-10-24 | Read PicoGK and Lattice Library documentation/licences | Apache-2.0; headless mode available; lattice workflow |
| 2026-10-24 | Checked `robots.txt` content signals | Reference use; no AI training; no redistribution |

**Change control:** if the target bundle changes, record the new hashes and re-check every “observed” claim
before updating the plan. Do not silently carry conclusions across builds.
