# Windows desktop verification

Tested on 2026-10-23 with installed **FreeCAD 1.1.4**, its bundled Python 3.11,
NumPy 1.26.4, SciPy 1.16.3 and **CalculiX 2.22** in
`C:\Program Files\FreeCAD 1.1\bin`. No solver package was downloaded or substituted.

The installed add-on was loaded on a fresh desktop-shell launch of `FreeCAD.exe`
without passing a Python startup script. **CADSwarm Optimization** appeared in
the workbench selector; its toolbar created a selected demonstration box and
opened **Optimize Part…**. The existing `comparison.FCStd` was opened in the
visible GUI and saved with an isometric camera and the original visible.

## Actual desktop runs

Three independent runs were started with mouse clicks on **Load demo assumptions**
and **Analyze and optimize**, using the 72 × 24 × 24 mm box, 3 mm cells,
`x_min` fully fixed, and a distributed `[0, 0, -200]` N force on `x_max`.
The explicit backend was `calculix`. Each run produced four solver directories
whose logs identify CalculiX 2.22 and report `Job finished`.

| Quantity | Original | Optimized |
|---|---:|---:|
| Volume (mm³) | 41,472 | 36,126 |
| Mass (kg) | 0.1119744 | 0.0975402 |
| Peak integration-point von Mises stress (MPa) | 5.816849939 | 6.275724354 |
| Maximum displacement (mm) | 0.01381871861 | 0.01476722402 |

**12.890625%** material reduction. All 744 protected cells remained. The result
passed the entered 80 MPa and 0.05 mm absolute limits and the 1.25 stress / 1.15
displacement ratios relative to the baseline. This verifies the stated discrete
linear-static model, not mesh-independent strength or manufacturing suitability.

- Run `5d9c898d9e8443bcafb40d6d37e875b4`: previewed both models, enabled the
  section view, inspected the visible interior cavity, then clicked **Reject /
  cancel**. The preview closed and the source tree contained only the original.
- Run `e84d21e814154392a8f687445d1044f9`: repeated the simulation after a normal
  restart, enabled the section view and clicked **Accept as new part**.
  `acceptance.json` records creation of `Optimized_d8b2bcacaa0a`. Closing the
  dialog left the original and accepted object in the source tree.
- Run `ace2c1b8ab5c49a596490ac8ab235cf5`: reran the saved original after another
  restart with the final installed build. The compact result panel and section
  view were verified visibly, with the same numerical result. This preview was
  left open for inspection; the previously accepted file remains saved.

The accepted document was saved through **File → Save as…** as
`exports/structural-verified/desktop-accepted.FCStd`. A separate installed
FreeCAD Python process reopened that exact file and verified:

- exactly two objects; original volume **41,472 mm³**;
- accepted geometry is one valid solid of **36,126 mm³**, even though acceptance
  occurred while the preview was sectioned;
- no material was added outside the original;
- the embedded report identifies the passing CalculiX result;
- STEP export and re-import preserve the **36,126 mm³** volume and valid solid.

The check and STEP file are in `exports/structural-desktop-evidence/`. GUI run
inputs, full reports, displacement/stress fields, solver decks and logs remain
in `%APPDATA%\CadSwarm\structural\<run-id>`. Analysis reports are immutable;
the separate acceptance receipt records the UI decision.

## Fixes and repeatability

Added a standalone workbench and a one-click demonstration command, corrected
registration for FreeCAD's separate-global/local startup loader, added a
reversible section view of preview copies, and a collapsible input panel.
The existing team workbench and communication paths remain intact.

An offscreen Qt run initially terminated natively while using the desktop's
FreeCAD preferences. The GUI test now uses an isolated FreeCAD profile, following
[FreeCAD's startup configuration](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Start_up_and_Configuration.md),
and explicitly exercises registration, preview, section restoration, acceptance
of the full solid, rejection, cancellation and demonstration creation. The
isolated GUI test passed. The final repository regression run passed **246
tests in 141.65 seconds**, including actual installed FreeCAD and CalculiX
integration, with no skipped tests. Visible desktop interaction was verified separately;
offscreen rendering is not used as visual evidence.

See [click-by-click instructions](../../docs/structural-optimization.md#repeat-the-desktop-demonstration).
The original outer skin is deliberately preserved, so the removed volume is
enclosed. Use **Show section view (display only)** to see it; accepting or
exporting always uses the complete analyzed geometry.
