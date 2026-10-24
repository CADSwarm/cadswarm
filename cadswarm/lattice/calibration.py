"""Calibrated cell map: how dense, stiff and strong each cell is at a given wall or strut thickness.

`python -m cadswarm.lattice.calibration` measures it with our own voxel solver and writes calibration.json:

- relative density rho(t/L), from the cell field on a fine periodic sample;
- apparent stiffness E*/E0 = C * rho**n and strength sigma*/sigma_y = Cs * rho**m, fitted (log-log) to
  compressions of a 2x2x2-cell block (20 voxels per cell) between a held face and a loaded face, compared with a solid block
  under the same supports. That is the same way the verify step measures stiffness, so a design rule
  taken from this map is checked on the same footing.

These are design starting points from simulation, not material tests; the report says so.
"""
import json
import math
import os
import sys

import numpy as np

from . import CELLS, cells

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")
RATIOS = (0.03, 0.05, 0.08, 0.12, 0.16, 0.2, 0.25, 0.3, 0.35, 0.42)
# Fitted where a wall or strut spans at least two voxels of the measuring block.
FIT_RATIOS = {"tpms": (0.1, 0.13, 0.16, 0.2, 0.25), "beam": (0.15, 0.2, 0.25, 0.3, 0.35)}
_TABLE = None


def table():
    global _TABLE
    if _TABLE is None:
        with open(PATH, encoding="utf-8") as f:
            _TABLE = json.load(f)
    return _TABLE


def _cell(cell):
    t = table()["cells"].get(cell)
    if t is None:
        raise ValueError(f"no calibration for cell {cell!r}")
    return t


def density(cell, thickness_ratio):
    """Relative density at wall/strut thickness / cell size (interpolated; arrays allowed)."""
    t = _cell(cell)
    return np.interp(thickness_ratio, t["ratios"], t["density"])


def thickness_ratio(cell, rho):
    """Inverse of density(): wall/strut thickness / cell size for a relative density (clipped to the table)."""
    t = _cell(cell)
    return np.interp(rho, t["density"], t["ratios"])


def stiffness(cell, rho):
    t = _cell(cell)
    return t["stiffness"]["C"] * np.asarray(rho, float) ** t["stiffness"]["n"]


def strength(cell, rho):
    t = _cell(cell)
    return t["strength"]["C"] * np.asarray(rho, float) ** t["strength"]["n"]


def density_for_stiffness(cell, ratio):
    t = _cell(cell)["stiffness"]
    return (np.asarray(ratio, float) / t["C"]) ** (1 / t["n"])


def density_for_strength(cell, ratio):
    t = _cell(cell)["strength"]
    return (np.asarray(ratio, float) / t["C"]) ** (1 / t["n"])


def library(cell_size_mm, target_kind, target, min_wall_mm=0.0, youngs_mpa=None):
    """Every cell's wall/strut for a target: kind "stiffness" (E*/E0, or MPa with youngs_mpa) or "density".

    Rows are sorted lightest first; `printable` is False below min_wall_mm or outside the calibrated range.
    """
    if target_kind == "stiffness" and youngs_mpa:
        target = target / youngs_mpa
    rows = []
    for cell in CELLS:
        t = _cell(cell)
        rho = float(density_for_stiffness(cell, target)) if target_kind == "stiffness" else float(target)
        lo, hi = t["density"][0], t["density"][-1]
        in_range = lo <= rho <= hi
        ratio = float(thickness_ratio(cell, rho))
        wall = ratio * cell_size_mm
        rows.append({"cell": cell, "family": t["family"], "relative_density": round(rho, 4),
                     "thickness_mm": round(wall, 3), "stiffness_ratio": round(float(stiffness(cell, rho)), 5),
                     "strength_ratio": round(float(strength(cell, rho)), 5),
                     "printable": bool(in_range and wall >= min_wall_mm),
                     "note": "" if in_range else "outside the calibrated range"})
    return sorted(rows, key=lambda r: (not r["printable"], r["relative_density"]))


# ----------------------------------------------------------------------- measuring

def _compress(mask, pitch):
    """Compliance and p99 von Mises of a block held at z=0 and pushed on top with 1 MPa mean stress."""
    from . import vfem
    nx, ny, nz = mask.shape
    fixed = np.zeros((nx + 1, ny + 1, nz + 1), bool)
    fixed[:, :, 0] = True
    top = np.zeros_like(fixed)
    top[:, :, nz] = True
    top &= np.pad(mask[:, :, -1:], ((0, 1), (0, 1), (0, 0)), constant_values=False) | \
        np.pad(mask[:, :, -1:], ((1, 0), (1, 0), (0, 0)), constant_values=False) | \
        np.pad(mask[:, :, -1:], ((1, 0), (0, 1), (0, 0)), constant_values=False) | \
        np.pad(mask[:, :, -1:], ((0, 1), (1, 0), (0, 0)), constant_values=False)
    ids = np.flatnonzero(top.ravel())
    area = nx * ny * pitch ** 2
    force = np.zeros((len(ids), 3))
    force[:, 2] = -area / len(ids)           # 1 MPa over the block's footprint
    sol = vfem.solve(mask, pitch, 1000.0, 0.3, fixed, (ids, force), tol=1e-7)
    return sol.compliance_n_mm, sol.stress_percentile(99)


def measure(cell, voxels_per_cell=20, n_cells=2, ratios=RATIOS, log=print):
    pitch = 1.0 / voxels_per_cell
    shape = (voxels_per_cell * n_cells,) * 3
    pts = cells.grid_points((0, 0, 0), shape, pitch)
    solid = np.ones(shape, bool)
    c0, s0 = _compress(solid, pitch)
    fam = "beam" if cells.is_beam(cell) else "tpms"
    dens = [cells.relative_density(cell, r, samples=64) for r in ratios]
    pts_rho, pts_e, pts_s = [], [], []
    for r in FIT_RATIOS[fam]:
        mask = cells.field(cell, pts, 1.0, r) < 0
        c, s99 = _compress(mask, pitch)
        rho = mask.mean()
        pts_rho.append(rho)
        pts_e.append(c0 / c)                  # apparent stiffness relative to the solid block
        pts_s.append(s0 / s99)                # same mean stress: strength scales inversely with the peak
        log(f"  {cell} t/L={r:.2f}: rho={rho:.3f} E*/E0={c0 / c:.4f} s*/sy={s0 / s99:.4f}")
    fe = np.polyfit(np.log(pts_rho), np.log(pts_e), 1)
    fs = np.polyfit(np.log(pts_rho), np.log(pts_s), 1)
    return {"family": fam, "ratios": list(ratios), "density": [round(d, 5) for d in dens],
            "stiffness": {"C": round(math.exp(fe[1]), 5), "n": round(fe[0], 4)},
            "strength": {"C": round(math.exp(fs[1]), 5), "n": round(fs[0], 4)},
            "samples": [{"rho": round(float(a), 4), "E": round(float(b), 5), "S": round(float(c), 5)}
                        for a, b, c in zip(pts_rho, pts_e, pts_s)]}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out = {"_doc": "Measured by cadswarm.lattice.calibration (voxel FEM, 2x2x2 cells, 20 voxels/cell, "
                   "compression between a held and a loaded face). Simulation, not material tests.",
           "cells": {}}
    for cell in (argv or CELLS):
        print(cell, flush=True)
        out["cells"][cell] = measure(cell)
    if argv and os.path.exists(PATH):  # measuring a few cells: keep the others
        with open(PATH, encoding="utf-8") as f:
            old = json.load(f)
        old["cells"].update(out["cells"])
        out = old
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("wrote", PATH)


if __name__ == "__main__":
    main()
