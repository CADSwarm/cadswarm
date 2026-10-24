"""Bounded evolutionary removal; each proposed topology receives a fresh FEM solve."""
from dataclasses import dataclass, asdict
import numpy as np

from .geometry import Grid, connected, manifold, protected_mask
from .model import Material, Load, Support, Constraints, StructuralError, SolverError, Cancelled
from .fem import ScipySolver


@dataclass
class OptimizationResult:
    original: Grid
    optimized: Grid
    baseline: object
    final: object
    protected: np.ndarray
    report: dict

    @property
    def improved(self):
        return self.report["status"] == "improved"


def violations(result, baseline, material, constraints):
    limits = {"stress_mpa": (result.max_stress_mpa, constraints.stress_limit(material)),
              "displacement_mm": (result.max_displacement_mm, constraints.max_displacement_mm)}
    if baseline is not None:
        limits.update({"stress_ratio": (result.max_stress_mpa / baseline.max_stress_mpa, constraints.max_stress_ratio),
                       "displacement_ratio": (result.max_displacement_mm / baseline.max_displacement_mm,
                                              constraints.max_displacement_ratio)})
    return [name for name, (value, limit) in limits.items() if not np.isfinite(value) or value > limit]


def optimize(grid, material, loads, supports, constraints, *, preserve=None, solver=None, progress=None, cancel=None):
    if not isinstance(grid, Grid) or not isinstance(material, Material) or not isinstance(constraints, Constraints):
        raise StructuralError("Expected Grid, Material and Constraints inputs")
    if not loads or not all(isinstance(x, Load) for x in loads) or not supports or not all(isinstance(x, Support) for x in supports):
        raise StructuralError("Explicit Load and Support lists are required")
    if not manifold(grid):
        raise StructuralError("Input surface is non-manifold")
    solver = solver or ScipySolver()
    protected = protected_mask(grid, loads, supports, constraints.skin_cells, preserve)
    def announce(message):
        if cancel and cancel():
            raise Cancelled("Optimization cancelled; no design accepted")
        if progress:
            progress(message)
    announce("Solving original design")
    baseline = solver.solve(grid, material, loads, supports)
    current, final, history = grid, baseline, []
    baseline_failures = violations(baseline, None, material, constraints)
    target_cells = int(np.ceil(constraints.target_reduction * grid.mask.sum()))
    channel_width = 1
    rejected = set()
    for trial in range(1, constraints.max_trials + 1):
        if baseline_failures or int(grid.mask.sum() - current.mask.sum()) >= target_cells:
            break
        eligible = current.mask & ~protected
        if not eligible.any():
            break
        announce(f"Evaluating candidate {trial}/{constraints.max_trials}")
        proposal = current.mask.copy()
        # A centered cavity in a rectangular beam avoids scattered isolated microvoids.
        channel = False
        if grid.mask.all() and len(loads) == len(supports) == 1 and loads[0].side[0] == supports[0].side[0]:
            axis = "xyz".index(loads[0].side[0])
            transverse = [i for i in range(3) if i != axis]
            if channel_width <= min(grid.mask.shape[i] - 2 * constraints.skin_cells for i in transverse):
                slices = [slice(constraints.skin_cells, n - constraints.skin_cells) for n in grid.mask.shape]
                for i in transverse:
                    start = (grid.mask.shape[i] - channel_width) // 2
                    slices[i] = slice(start, start + channel_width)
                cavity = np.zeros_like(proposal)
                cavity[tuple(slices)] = True
                proposal[cavity & eligible] = False
                channel_width += 1
                channel = True
        if not channel:
            cells = [tuple(c) for c in np.argwhere(eligible) if tuple(c) not in rejected]
            if not cells:
                break
            cell = min(cells, key=lambda c: (final.element_energy[c], c))
            proposal[cell] = False
            rejected.add(cell)
        removed = int(current.mask.sum() - proposal.sum())
        if removed == 0:
            continue
        entry = {"trial": trial, "removed_cells": removed, "accepted": False, "solver_ran": False}
        history.append(entry)
        if not connected(proposal):
            entry["reason"] = "disconnected geometry"
            continue
        candidate = grid.with_mask(proposal)
        if not manifold(candidate):
            entry["reason"] = "non-manifold geometry"
            continue
        if np.any(protected & ~proposal):
            raise StructuralError("Internal error: protected geometry changed")
        try:
            estimate = solver.solve(candidate, material, loads, supports)
            entry["solver_ran"] = True
        except SolverError as exc:
            entry["reason"] = str(exc)
            continue
        failures = violations(estimate, baseline, material, constraints)
        entry.update(estimate.summary(), failures=failures)
        if not failures:
            current, final = candidate, estimate
            entry["accepted"] = True
            rejected.clear()
    announce("Analysis complete; awaiting user acceptance")
    reduction = 1 - current.volume_mm3 / grid.volume_mm3
    status = "baseline_infeasible" if baseline_failures else "improved" if reduction > 0 else "no_improvement"
    report = {
        "schema_version": 1, "status": status, "accepted_by_user": False,
        "solver": final.solver, "units": {"length": "mm", "force": "N", "stress": "MPa", "mass": "kg"},
        "material": asdict(material), "loads": [asdict(x) for x in loads], "supports": [asdict(x) for x in supports],
        "constraints": asdict(constraints), "effective_stress_limit_mpa": constraints.stress_limit(material),
        "grid": {"shape": list(grid.mask.shape), "pitch_mm": grid.pitch_mm, "origin": list(grid.origin)},
        "original_volume_mm3": grid.volume_mm3, "optimized_volume_mm3": current.volume_mm3,
        "original_mass_kg": grid.volume_mm3 * material.density_kg_mm3,
        "optimized_mass_kg": current.volume_mm3 * material.density_kg_mm3,
        "material_removed_percent": reduction * 100, "target_reached": reduction >= constraints.target_reduction - 1e-12,
        "baseline": baseline.summary(), "optimized": final.summary(), "baseline_failures": baseline_failures,
        "protected_cells": int(protected.sum()), "protected_cells_preserved": bool(np.all(current.mask[protected])),
        "history": history,
        "limitations": ["One combined static load case; homogeneous isotropic linear elasticity and small displacement.",
                        "Exact grid-aligned solids only; curved and oblique boundaries are rejected.",
                        "Full integration linear bricks can be too stiff in bending; perform mesh convergence for each part.",
                        "Stress is the peak at eight integration points, not a resolved notch or singular boundary stress.",
                        "No buckling, fatigue, contact, plasticity, thermal or additive-manufacturing anisotropy analysis.",
                        "Internal cavities may be enclosed; printability and machining access are not evaluated.",
                        "Passing these finite-mesh checks is not structural certification or a guarantee of safety."]}
    return OptimizationResult(grid, current, baseline, final, protected, report)
