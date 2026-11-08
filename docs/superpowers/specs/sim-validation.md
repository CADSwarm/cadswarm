# Simulation features: validation on the branded app (FreeCAD 1.1.4, Windows 11)

Run 2026-11-07 on agent/sim. Every number below came from a real CalculiX/Gmsh run on this machine.

## How to run

```sh
# Headless: the code the team server runs, in fresh FreeCAD processes (about 4.5 min for 3 runs each)
.venv/Scripts/python.exe scripts/sim_validate.py --runs 3 --json sim.json
# Inside the branded app (a window opens), one scenario and case per launch; screenshots go to <out dir>
"<FreeCAD 1.1>/bin/python.exe" scripts/sim_branded.py fem|lighten|optimize|report <out dir> --case 1|2|3
# As tests (skipped in the normal suite)
CADSWARM_TEST_SIM=1 CADSWARM_TEST_SIM_GUI=1 CADSWARM_TEST_SIM_RUNS=3 .venv/Scripts/python.exe -m pytest tests/test_sim_real.py
```

## Solvers under the CADSwarm engine profile

With `FREECAD_USER_HOME` pointing at a fresh CADSwarm folder (as `cadswarm.app` sets it), both our runner and
FreeCAD's own FEM code find `bin/ccx.exe` and `bin/gmsh.exe` of the FreeCAD 1.1 install; no fix was needed.
FreeCAD 1.1's Windows bundle has no `netgen` Python module, so FEM's newer Netgen mesher
(`FEM_MeshNetgenFromShape`) cannot run; the legacy Netgen mesh object does mesh. CADSwarm uses Gmsh.

## Headless (scripts/sim_validate.py): 16/16 pass

FEA: cantilever arm, `xmin` fixed, tip force down on `xmax`, 2nd-order tets. Hand calc: deflection
FL³/3EI + FL/(κGA), root stress 6FL/bh². Pass: deflection within 5 %, reaction within 1 %, peak stress
0.9-2.0x hand (the peak is on the fully fixed face).

| case | FE deflection | hand | error | reaction | peak VM / hand | time |
|---|---|---|---|---|---|---|
| 100x20x10 steel S235, 500 N | 0.4743 mm | 0.4799 mm | -1.16 % | 500.0 N (0 %) | 145.7 / 150 MPa | 8.9 s |
| 150x25x12 Al 6061, 800 N | 3.600 mm | 3.647 mm | -1.29 % | 800.0 N (0 %) | 197.1 / 200 MPa | 9.2 s |
| 80x15x8 steel S355, 200 N | 0.2532 mm | 0.2560 mm | -1.09 % | 200.0 N (0 %) | 98.5 / 100 MPa | 8.7 s |

Reviewer (published report read back as an AI helper does, `fea_report`): 150 N gives SF 5.4, verdict
"check" with info/warning points only; 700 N gives SF 1.15, verdict "check" with a thin-margin warning; 4000 N
gives SF 0.20, verdict "fail", critical point first. (An earlier 1200 N run gave SF 0.67, "fail".) Fixed-face peak
flagged every time, rerun_args repeat the case, report.json/html/png written. 8-15 s each.

Interference (live worker): overlaps of 4000, 125 and 192 mm³ found exactly; face-touching parts never clash.
About 1.2 s each.

Lighten (lattice job with load case): 40x12x12 gyroid 300 N Al: 24.0 % lighter, 75.4 % stiffness kept (62.8 s);
48x14x14 bcc 500 N steel: 26.3 %, 72.9 % (79.5 s); 36x12x10 octet 150 N PLA: 18.9 %, 86.2 % (47.4 s).

Optimize (CalculiX C3D8 backend, real FreeCAD geometry, STEP checked): 5.2 %, 9.3 %, 8.6 % removed, valid single
solid each, 2.8-3.6 s.

## In the branded app (scripts/sim_branded.py): 12/12 pass, all clean exits

| scenario | case 1 | case 2 | case 3 |
|---|---|---|---|
| FEM workbench by its own commands (deflection error vs hand) | -1.17 %, 29 s | -1.02 %, 35 s | -1.09 %, 28 s |
| Lighten part dialog (saving / stiffness kept / verdict) | 24.4 % / 75 % / passed, 51 s | 17.7 % / 87 % / passed, 76 s | 26.5 % / 68 % / no passing design, 93 s |
| Optimize part dialog (removed) | 5.2 %, 21 s | 9.3 %, 22 s | 8.6 %, 23 s |
| FEA report dialog (4x load, verdict) | fail (SF 0.40), 21 s | fail, 19 s | fail, 21 s |

Screenshots: `img/branded/sim-*.png` (FEM setup and von Mises result colours for all three cases, Lighten
dialog/result/section, Optimize dialog/section, report dialog and its plot).

## Fixed here

* Lighten part: after the dialog had been opened, quitting CADSwarm ended in an access violation. The dialog is
  now deleted when closed.
* Optimize part: the finished report showed raw Markdown tables in a plain-text box; it is now rendered.

## Not covered / open

* Run a simulation, Check for clashes and Simulation reports from the ribbon need a joined team; their server-side
  code is what the headless checks drive, and the report dialog was driven with the project's files.
* FEM colour-bar labels are white on the light background (FreeCAD's scale label colour; branding).
* Lighten's "Cut in half" preview does not visibly expose the lattice in the screenshot.
* tests/test_lighten_gui.py and the GUI case of tests/test_structural_freecad.py fail on FreeCAD 1.1 (also at the
  base commit): offscreen Qt has no OpenGL ("QOpenGLWidget is not supported on this platform"). The branded-app
  driver covers the same dialogs in a real window.
