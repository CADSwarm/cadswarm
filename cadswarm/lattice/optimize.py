"""Lighten without losing stiffness: the lightest graded lattice whose stiffness meets the target.

Search: the lattice region is an equivalent continuum, each voxel as stiff as the calibrated cell at its
density (E = E0 * C * rho**n). One global density level is bisected; every step is a full static solve of
the same load case, compared with the solid part's compliance (stiffness retained = C_solid / C_design).

Verify: the chosen design is then rebuilt as real walls/struts on a finer voxel grid and solved again,
next to the solid part on that same grid. That explicit result is what the report calls verified.
"""
from dataclasses import dataclass, field

import numpy as np

from . import calibration, grade, vfem
from .build import regions

ITERATIONS = 7


@dataclass
class Design:
    status: str                  # "passed", "no_passing_design" or "not_verified"
    scale: float
    rho: np.ndarray              # density on the analysis grid (0 outside the lattice region)
    stiffness_retained: float
    mass_fraction: float         # design mass / solid mass
    safety_factor: object        # min over the part away from supports/loads, or None
    baseline: dict
    history: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    skin_mm: float = 0.0          # the skin the design uses (auto_skin may thicken it)
    sigma: np.ndarray = field(default=None, repr=False)    # smoothed solid stress (the grading shape)
    lattice: np.ndarray = field(default=None, repr=False)  # the lattice region on the analysis grid
    fs_target: float = 0.0

    def rho_at(self, scale, recipe, yield_mpa):
        """The density field at another level (the verify step corrects the level this way)."""
        return grade.density(self.sigma, self.lattice, recipe.cell, recipe.cell_size_mm,
                             recipe.min_thickness_mm, recipe.max_thickness_mm, scale, yield_mpa,
                             self.fs_target or None)


def _fs_field(sol, lattice, rho, cell, yield_mpa, exclude):
    """Local safety factor: solid voxels sigma_y / sigma; lattice voxels sigma_y * strength(rho) / sigma."""
    if not yield_mpa:
        return None
    s = sol.stress
    ok = np.isfinite(s) & (s > 0) & ~exclude
    if not ok.any():
        return None
    cap = np.where(lattice, yield_mpa * calibration.strength(cell, np.maximum(rho, 1e-6)), yield_mpa)
    fs = cap[ok] / s[ok]
    return float(np.percentile(fs, 0.5))  # ignore the few worst voxels (voxel staircase peaks)


def _exclusion(geom, applied):
    """Voxels next to supports and loads: point-load singularities, reported apart from the safety factor."""
    from scipy import ndimage
    return ndimage.binary_dilation(applied.seeds, iterations=2) & geom.part


def _lighten_at(geom, applied, material, recipe, base=None, *, cancel=None, log=lambda *_: None):
    """Search on the analysis grid at the recipe's skin. material: {"youngs_mpa", "poisson", "yield_mpa", "density_kg_m3"}."""
    E, nu, sy = material["youngs_mpa"], material["poisson"], material.get("yield_mpa")
    h = geom.grid.pitch
    solve = lambda mask, **kw: vfem.solve(mask, h, E, nu, applied.fixed, (applied.load_ids, applied.load_vecs),
                                          body_force=applied.body, cancel=cancel, **kw)
    if base is None:
        log("simulate", "Solving the solid part")
        base = solve(geom.part)
    exclude = _exclusion(geom, applied)
    base_fs = (float(np.percentile(sy / base.stress[np.isfinite(base.stress) & (base.stress > 0) & ~exclude], 0.5))
               if sy else None)
    baseline = dict(base.summary(), safety_factor=base_fs, mass_voxels=int(geom.part.sum()))
    baseline["_solution"] = base
    solid, lattice = regions(geom, recipe)
    notes = []
    if not lattice.any():
        return Design("no_passing_design", 0.0, np.zeros(geom.part.shape), 1.0, 1.0, base_fs, baseline,
                      skin_mm=recipe.skin_mm,
                      notes=["After the skin and the solid zones around supports and loads there is no interior "
                             "left to lighten. Use a thinner skin or smaller keep zones."])
    fs_target = recipe.min_safety_factor if sy else 0
    if sy and base_fs is not None and base_fs < fs_target:
        notes.append(f"The solid part itself only reaches a safety factor of {base_fs:.2f} (target "
                     f"{fs_target:g}); the lattice cannot improve on that, so only the stiffness target applies.")
        fs_target = 0
    sigma = grade.smooth_stress(base.stress, geom.part, recipe.cell_size_mm / (2 * h)) \
        if recipe.grading == "stress" else np.where(geom.part, 1.0, 0.0)
    total_mass = float(geom.part.sum())
    prev = [None]
    history = []

    def evaluate(scale):
        rho = grade.density(sigma, lattice, recipe.cell, recipe.cell_size_mm, recipe.min_thickness_mm,
                            recipe.max_thickness_mm, scale, sy, fs_target or None)
        w = np.where(lattice, calibration.stiffness(recipe.cell, np.maximum(rho, 1e-6)), 1.0)
        mw = np.where(lattice, rho, 1.0)
        sol = solve(geom.part, weights=w, mass_weights=mw, u0=prev[0])
        prev[0] = (sol.nodes, sol.u)
        retained = base.compliance_n_mm / sol.compliance_n_mm
        mass = (float(solid.sum()) + float(rho[lattice].sum())) / total_mass
        fs = _fs_field(sol, lattice, rho, recipe.cell, sy, exclude)
        ok = retained >= recipe.stiffness_target and (not fs_target or (fs is not None and fs >= fs_target))
        history.append({"scale": round(scale, 5), "stiffness_retained": round(retained, 4),
                        "mass_fraction": round(mass, 4), "safety_factor": None if fs is None else round(fs, 3),
                        "passed": ok})
        log("lighten", f"density level {scale:.3f}: stiffness {retained:.1%}, mass {mass:.1%}"
                       + (f", FS {fs:.2f}" if fs is not None else ""))
        return ok, rho, retained, mass, fs

    rho_lo, rho_hi = grade.bounds(recipe.cell, recipe.cell_size_mm, recipe.min_thickness_mm,
                                  recipe.max_thickness_mm)
    lo, hi = rho_lo, rho_hi * 4
    best = None
    ok, *res = evaluate(hi)
    if not ok:
        rho, retained, mass, fs = res
        notes.append(f"Even at the thickest allowed walls ({recipe.max_thickness_mm:g} mm) the lattice keeps "
                     f"{retained:.0%} of the stiffness, below the {recipe.stiffness_target:.0%} target. "
                     "Lower the target, thicken the skin, choose a stiffer cell (diamond, I-WP or octet) or "
                     "allow thicker walls.")
        return Design("no_passing_design", hi, rho, retained, mass, fs, baseline, history, notes, recipe.skin_mm,
                      sigma, lattice, fs_target)
    best = (hi, *res)
    ok, *res = evaluate(lo)
    if ok:
        best = (lo, *res)
    else:
        for _ in range(ITERATIONS - 2):
            mid = float(np.sqrt(lo * hi))
            ok, *res = evaluate(mid)
            if ok:
                hi, best = mid, (mid, *res)
            else:
                lo = mid
    scale, rho, retained, mass, fs = best
    return Design("passed", scale, rho, retained, mass, fs, baseline, history, notes, recipe.skin_mm, sigma,
                  lattice, fs_target)


def verify(geom, applied, material, recipe, thickness, *, cancel=None):
    """Real walls/struts on `geom.grid`: solve the design and the solid part, return ratios."""
    from .build import compose
    E, nu = material["youngs_mpa"], material["poisson"]
    h = geom.grid.pitch
    mask = compose(geom, recipe, thickness) < 0
    solve = lambda m: vfem.solve(m, h, E, nu, applied.fixed, (applied.load_ids, applied.load_vecs),
                                 body_force=applied.body, cancel=cancel, tol=1e-6)
    solid = solve(geom.part)
    design = solve(mask)
    exclude = _exclusion(geom, applied)
    sy = material.get("yield_mpa")

    def peak(sol):
        s = sol.stress[np.isfinite(sol.stress) & ~exclude]
        return float(np.percentile(s, 99.5)) if s.size else 0.0
    ps, pd = peak(solid), peak(design)
    return {"voxel_mm": h, "elements": design.elements,
            "stiffness_retained": solid.compliance_n_mm / design.compliance_n_mm,
            "mass_fraction": float(mask.sum()) / float(geom.part.sum()),
            "displacement_ratio": design.max_displacement_mm / solid.max_displacement_mm,
            "stress_ratio": pd / ps if ps else None,
            "safety_factor": sy / pd if sy and pd else None,
            "solid": solid.summary(), "design": design.summary(),
            "disconnected_fraction": design.dropped_fraction}


def lighten(geom, applied, material, recipe, *, cancel=None, log=lambda *_: None):
    """Search on the analysis grid; with recipe.auto_skin, thicken the skin until the target is reachable.

    In bending, the outer skin carries most of the stiffness, so when even the densest core misses the
    target the skin is the lever that works. It grows in steps up to a quarter of the part's thinnest side.
    """
    from dataclasses import replace
    design = _lighten_at(geom, applied, material, recipe, cancel=cancel, log=log)
    base = design.baseline.get("_solution")
    if design.status == "passed" or not recipe.auto_skin or not design.history:
        return _clean(design)
    size = [geom.bbox[i + 3] - geom.bbox[i] for i in range(3)]
    limit = min(size) / 4
    skin = max(recipe.skin_mm, geom.grid.pitch * 2)
    first = design
    while skin < limit:
        skin = min(limit, skin * 1.6)
        log("lighten", f"The core alone cannot reach the target: trying a {skin:.1f} mm skin")
        trial = _lighten_at(geom, applied, material, replace(recipe, skin_mm=skin), base, cancel=cancel, log=log)
        if trial.status == "passed":
            trial.notes.insert(0, f"The skin was thickened from {recipe.skin_mm:g} to {skin:.2g} mm: with the "
                                  f"thinner skin even the densest core kept only "
                                  f"{first.stiffness_retained:.0%} of the stiffness.")
            trial.history = first.history + trial.history
            return _clean(trial)
        first.history += trial.history
    return _clean(first)


def _clean(design):
    design.baseline.pop("_solution", None)
    return design
