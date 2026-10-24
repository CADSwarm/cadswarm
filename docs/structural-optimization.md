# Structural optimization

CADSwarm's **Optimize Part…** command analyzes a selected solid, proposes material removal, re-solves every geometrically valid candidate, and lets a person accept a passing result as a new part. The original remains editable and unchanged. Numerical work runs in a separate process; the shared FreeCAD worker is used only for the final accepted change.

## Supported geometry and physics

This first implementation supports **one exact, axis-aligned solid made from cubic grid cells**, up to 4,096 cells in its bounding grid. Rectangular beams, stepped blocks, and grid-aligned rectangular mounting holes are supported. Dimensions and all boundaries must align with the requested pitch. OCC intersection volumes validate every cell, and total CAD/grid volume must agree. Curved holes, fillets, inclined faces, arbitrary imported STL, assemblies, and approximate voxelization are rejected rather than silently changing functional geometry.

The material must be explicitly specified: name, Young's modulus, Poisson ratio, yield stress and density. CAD geometry cannot determine these values. The GUI does not supply engineering defaults until **Load demo assumptions** is clicked. Inputs use **mm, N, MPa and kg/mm³**. For example, 2,700 kg/m³ is 0.0000027 kg/mm³.

Supports fully fix an entire extreme `x_min`, `x_max`, `y_min`, `y_max`, `z_min` or `z_max` face. Loads are force vectors distributed as uniform traction over an entire extreme face, with consistent nodal weights. Loaded and fixed nodes must be disjoint. The API supports multiple loads/supports in **one combined static load case**; the initial GUI exposes one of each. Arbitrary CAD face selection, pressure fields, contacts and independent load envelopes are future extensions.

All original surfaces, hole walls, load/support cells and at least one cell of surrounding skin are non-removable. Additional preserve boxes use world coordinates in mm. Every occupied cell intersecting a preserve box is retained. Optimization only removes material; it cannot fill or relocate a mounting hole.

## Installation

Install CADSwarm normally in FreeCAD 1.x: close FreeCAD, double-click **Install CadSwarm.cmd** in the checkout, then reopen FreeCAD. On Windows FreeCAD 1.1 the add-on normally lives in `%APPDATA%\FreeCAD\v1-1\Mod\CadSwarm`. The installed copy must contain `InitGui.py` and the `cadswarm` directory directly inside it. Restart after updating the copy. Installation registers **CADSwarm Optimization** in the workbench dropdown on every launch; no Python console, macro, team session or AI connection is needed for local optimization. The separate **CadSwarm** workbench retains the collaboration workflow.

The workbench registers the command without importing numerical packages into the collaboration panel. FreeCAD's Python must have NumPy and SciPy; both the official portable and installed Windows FreeCAD 1.1.4 runtimes tested here include them, along with `bin\ccx.exe` (CalculiX 2.22). The installed runtime was found at `C:\Program Files\FreeCAD 1.1\bin`; no extra solver installation or environment variables were needed for its GUI.

Check dependencies using FreeCAD's **bundled Python**, not an unrelated system Python:

```powershell
$fcPython = 'C:\path\to\FreeCAD\bin\python.exe'
& $fcPython -c "import FreeCAD, Part, numpy, scipy; print(FreeCAD.Version()[:3])"
```

If missing, install the optional dependencies into an appropriate dedicated environment for that runtime, or use a FreeCAD distribution that includes them. Avoid replacing FreeCAD's bundled binary packages unnecessarily. For standalone numerical use and repository tests:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[structural,test]' httpx
```

The GUI defaults to **CalculiX**. Discovery checks `CADSWARM_CCX`, `PATH`, the current Python's directory, and the discovered FreeCAD `bin` directory. Missing or failed CalculiX stops the analysis; there is no silent fallback. Selecting `scipy` explicitly runs the independent sparse linear-elastic solver instead.

```powershell
$env:CADSWARM_FREECAD_PYTHON = $fcPython
$env:CADSWARM_CCX = 'C:\path\to\FreeCAD\bin\ccx.exe'
```

Restart FreeCAD after setting environment variables so its analysis subprocess inherits them.

## FreeCAD workflow

1. Choose **CADSwarm Optimization** in FreeCAD's workbench dropdown. Select one solid in an ordinary document. For a ready-to-run example, click **Optimization → New optimization demo** (also in the toolbar); this creates and selects a 72 × 24 × 24 mm box in a new document.
2. Choose **Optimization → Optimize Part…** or the **Optimize Part…** toolbar button. In a collaboration session, select a solid in CADSwarm's live mirror and use **CadSwarm → Optimize Part…** instead.
3. Supply the material, force vector, whole extreme support/load faces, grid pitch, maximum stress/displacement, factor of safety, target removal fraction, and allowed stress/displacement ratios. Enter extra preserve boxes as JSON, e.g. `[[[0,0,0],[12,24,24]]]`.
4. Click **Analyze and optimize**. Progress and errors are shown while the UI and team session remain responsive. Cancel requests are honored at the next solver/geometry checkpoint; a running CalculiX call has a 90-second timeout.
5. Review the original and translated candidate in a separate preview document, plus the numerical report. Completed runs collapse the input form to leave more room for the model; **Show inputs / run again** restores it. Click **Show section view (display only)** to expose the enclosed cavity, and **Show whole models** to restore the full preview. Only preview copies are sectioned; analysis, acceptance and exports always use the complete solid. The candidate's actual placement is preserved; translation is for comparison only. **Open report, stress plot and exports** opens the output folder containing `report.md`, `report.json`, `stress.svg`, fields, STEP and STL.
6. **Accept as new part** creates a new independent `Part::Feature`. Save the original document to persist local acceptance. For a live mirror, **Accept as new part and share** sends the accepted BREP through the existing `/ops` → `run_part_script` transaction. Reject closes the preview without changing the source or team model.

Output folders live under `%APPDATA%\CadSwarm\structural\<run-id>` on Windows (the existing CADSwarm application-data location elsewhere). Each run is unique. Rejected/cancelled evidence is retained locally for inspection; it is not shared. An `acceptance.json` receipt records a successful user acceptance separately from the immutable analysis report.

If the baseline violates absolute limits, the result is `baseline_infeasible`. If no candidate passes, it is `no_improvement`, displayed as **No verified improvement found**. Neither outcome enables acceptance. A passing candidate may miss or exceed the requested target because removal is discrete; `target_reached` and the actual percentage are reported independently.

### Repeat the desktop demonstration

1. Launch FreeCAD normally and select **CADSwarm Optimization**.
2. Click **New optimization demo**, then **Optimize Part…**, then **Load demo assumptions**.
3. Keep **calculix**, `x_min` fixed and the `x_max` force `[0, 0, -200]` N. The demo uses aluminum properties, a 3 mm grid, an 80 MPa stress limit, a 0.05 mm displacement limit, and a 10% removal target.
4. Click **Analyze and optimize** and wait for the numerical report and preview.
5. Click **Show section view (display only)**. The gray original and blue candidate now reveal the removed interior material. Drag the results window aside if it obscures the models.
6. Click **Reject / cancel** to close the preview with no source changes, or **Accept as new part** to add the full candidate to the source document. After acceptance, closing the dialog only closes the preview; it does not undo acceptance. Switch to the source document tab and use **File → Save as…**. The accepted object includes a `Structural Report` property. Hide the original with the Space key if the overlapping solids obscure the accepted part.

The enclosed cavity is intentional: the current engine preserves all original outer surfaces. The section is a viewing aid, not an opening cut into the manufactured design. No structural conclusion is inferred from its appearance.

## API

```python
from cadswarm.structural import optimize_part
from cadswarm.structural.model import Material, Load, Support, Constraints
from cadswarm.structural.calculix import CalculixSolver
from cadswarm.structural.freecad import reconstruct

# Explicit illustrative assumptions, not an inferred material/load specification.
result = optimize_part(
    selected_object,
    Material('Demo aluminum', 70000, 0.33, 240, 2.7e-6),
    [Load('x_max', (0, 0, -200))],
    [Support('x_min')],
    Constraints(max_stress_mpa=80, max_displacement_mm=0.05,
                safety_factor=2, target_reduction=0.10,
                max_stress_ratio=1.25, max_displacement_ratio=1.15),
    pitch_mm=3,
    solver=CalculixSolver(),
)
# This API never changes a document or publishes a design.
if result.improved:
    candidate_shape = reconstruct(selected_object.Shape, result.original, result.optimized)
```

`result` contains the original/optimized exact grids, baseline/final displacement and stress fields, protected mask, material-removal percentage in `report`, inputs, limits and a trial audit. `save_result(result, new_directory)` writes JSON/Markdown, a shared-scale central-z stress SVG, compressed fields, and watertight STL. `structural.job` also reconstructs BREP and STEP for FreeCAD inputs. Accepted STEP/BREP is a static solid, not a rebuilt parametric feature history.

## Numerical method and acceptance

The two solver backends use full-integration 8-node linear brick elements with eight Gauss points. SciPy assembles and solves the actual sparse stiffness system. CalculiX receives an explicit C3D8 input deck and must exit successfully, report job completion, and produce all node displacements and all eight stress rows per element. No soft/ersatz material remains in removed cells.

Both paths check force equilibrium, positive strain energy, finite results and zero motion at fixed nodes. The CalculiX adapter independently recovers stress from the printed displacement field and checks agreement against CalculiX's stress output. Its `.dat` displacements have seven significant digits: the equilibrium tolerance is derived from their rounding uncertainty and the assembled stiffness, with a 1% upper bound on allowable uncertainty. Raw residuals are retained in the report. SciPy's equilibrium tolerance is `1e-8` relative to the applied force vector norm.

Candidate generation is a bounded, removal-only evolutionary search: nested central cavities for rectangular beams, followed by cells ranked by current strain energy. It is **not full BESO or SIMP**, and no global optimum is claimed. Face connectivity, surface edge/vertex manifoldness and preservation are checked before each candidate solve. Each accepted topology satisfies all of:

- peak integration-point von Mises stress ≤ `min(max_stress_mpa, yield_mpa / safety_factor)`;
- maximum displacement ≤ `max_displacement_mm`;
- stress and displacement ≤ their respective original values × the explicit ratio limits;
- exact retained regions and a smaller positive volume.

The final BREP must be valid, contain one solid and match the analyzed volume. CalculiX runs retain `.inp`, `.dat`, `.sta` and logs in the sibling `output-solver/solve-NNN` folders. The result report is not a safety certificate.

## Reproducible demonstration and tests

Create a 72 × 24 × 24 mm box in FreeCAD, select it, and load demo assumptions. The command-line equivalent is:

```powershell
# From the repository; use a new output directory each time.
$env:PYTHONPATH = "$PWD;$(Split-Path $fcPython)"
& $fcPython -m cadswarm.structural.demo --freecad --solver calculix --output exports\structural-demo

# Independent numerical solver and analytical beam benchmark:
.\.venv\Scripts\python.exe -m cadswarm.structural.demo --solver scipy --output exports\structural-scipy
.\.venv\Scripts\python.exe -m cadswarm.structural.benchmark --output exports\benchmark.json

# Full Python regressions, real solver/CAD tests, and offscreen Qt actions:
$env:CADSWARM_FREECAD_PYTHON = $fcPython
$env:CADSWARM_TEST_STRUCTURAL_GUI = '1'
.\.venv\Scripts\python.exe -m pytest -q
```

The FreeCAD demo creates `comparison.FCStd`, containing the unchanged original and the candidate. See [recorded results](../demo/structural/README.md), [CalculiX report](../demo/structural/calculix-report.json) and [analytical convergence](../demo/structural/benchmark.json). Tests explicitly skip FreeCAD, CalculiX or offscreen Qt integration when the relevant runtime/opt-in is absent; installing only the numerical extra does not verify CAD integration.

## Compatibility and limits

Only workbench registration and an optional dependency extra change existing files. Collaboration state, locks, agent orchestration, sandbox restrictions, Rust reducers and generated bindings are unchanged. Analysis takes an immutable shape snapshot. Shared acceptance checks the source revision, mutual source/expected shape difference and output-name revision zero in the existing serialized transaction. It cannot replace a teammate's part, overwrite a previous result, use a reserved name, or accept a stale source. New parts retain the existing automatic ownership and broadcast behavior.

Global deflection convergence was tested on a beam; this does **not** establish convergence of the demo's cavity stress or of another selected object. C3D8 can be overly stiff in bending; the [CalculiX element documentation](https://www.dhondt.de/ccx_2.22.pdf) explicitly describes this limitation. Clamp and re-entrant corner stresses can be singular. Use an independently refined conforming mesh and appropriate higher-order elements before making engineering decisions. No buckling, fatigue, plasticity, contact, thermal response, printing anisotropy, machining access or manufacturing feasibility is evaluated. Enclosed cavities may not be manufacturable. These limitations are also in each report.

## Research and provenance

- The supplied standalone prototype was inspected in `cadswarm-optimizer.zip`. Its centroid-only stress recovery, uniform per-node force distribution, lack of an absolute displacement limit, and untested FreeCAD macro were not treated as validation. This package implements the standard brick-element formulation independently, using eight-point stress recovery, consistent face loads and strict CAD-grid equivalence. No archived prototype output was used as test evidence.
- [CalculiX](https://www.dhondt.de/) is the established external solver used and actually tested here. It remains a separately installed executable under its own GPL terms; no executable or source is vendored.
- [calculix/beso](https://github.com/calculix/beso) provides a more extensive FreeCAD/CalculiX topology workflow under LGPLv3. It was evaluated as a future backend; its source was not copied or bundled. The small bounded search here keeps the initial dependency and integration surface explicit.
- [implicit.build](https://implicit.build/) publicly presents part import, optional simulation and lattice generation. Only publicly accessible website information was consulted as a workflow reference. No authenticated/private implementation, assets or proprietary code were accessed or reproduced; this implementation does not claim equivalent lattice functionality.
- Runtime dependencies NumPy and SciPy use BSD licenses. FreeCAD and its bundled components retain their own licenses. There are no new redistributed third-party binaries in this branch.
