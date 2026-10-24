"""Reproducible numerical evidence, portable stress plots and exact surface export."""
from pathlib import Path
import html
import json
import numpy as np

from .geometry import export_stl


def markdown(report):
    r = report
    title = {"improved": "Improvement found; awaiting acceptance", "no_improvement": "No verified improvement found",
             "baseline_infeasible": "Original design violates the specified constraints"}[r["status"]]
    lines = ["# CADSwarm structural analysis", "", title, "", f"Solver: {r['solver']}", "",
             "| Measurement | Original | Optimized |", "|---|---:|---:|"]
    for label, a, b in (("Volume (mm³)", r["original_volume_mm3"], r["optimized_volume_mm3"]),
                        ("Mass (kg)", r["original_mass_kg"], r["optimized_mass_kg"]),
                        ("Peak integration-point stress (MPa)", r["baseline"]["max_stress_mpa"], r["optimized"]["max_stress_mpa"]),
                        ("Maximum displacement (mm)", r["baseline"]["max_displacement_mm"], r["optimized"]["max_displacement_mm"])):
        lines.append(f"| {label} | {a:.8g} | {b:.8g} |")
    lines += ["", f"Material removed: {r['material_removed_percent']:.4f}%. Target reached: {r['target_reached']}.",
              f"Effective stress limit: {r['effective_stress_limit_mpa']:.8g} MPa; displacement limit: "
              f"{r['constraints']['max_displacement_mm']:.8g} mm.",
              f"Protected cells preserved: {r['protected_cells_preserved']} ({r['protected_cells']} cells).",
              f"Baseline constraint failures: {', '.join(r['baseline_failures']) or 'none'}.", "",
              "## Inputs", "", "```json", json.dumps({k: r[k] for k in ("material", "loads", "supports", "constraints", "grid")}, indent=2),
              "```", "", "## Candidate audit", "", "| Trial | Solver completed | Accepted | Outcome |", "|---|---|---|---|"]
    for h in r["history"]:
        lines.append(f"| {h['trial']} | {h['solver_ran']} | {h['accepted']} | {h.get('reason') or ', '.join(h.get('failures', [])) or 'constraints passed'} |")
    lines += ["", "## Interpretation and limitations", ""] + ["- " + s for s in r["limitations"]]
    lines += ["", "`stress.svg` shows the central z cell layer on one shared MPa scale. White cells are voids.",
              "The heatmap is a visualization of the numerical results, not evidence of structural safety.", ""]
    return "\n".join(lines)


def stress_svg(result):
    scale = max(result.baseline.max_stress_mpa, result.final.max_stress_mpa)
    nx, ny, nz = result.original.mask.shape
    cell = min(18, 600 / nx)
    width, height = nx * cell + 100, (ny * cell + 100) * 2 + 50
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="white"/>']
    for panel, (name, grid, sim) in enumerate((("Original", result.original, result.baseline), ("Optimized", result.optimized, result.final))):
        top = 45 + panel * (ny * cell + 100)
        parts.append(f'<text x="30" y="{top-18}" font-family="sans-serif" font-size="16">{name}: z layer {nz//2}</text>')
        for x, y in np.argwhere(grid.mask[:, :, nz // 2]):
            value = sim.element_stress[x, y, nz // 2]
            ratio = value / scale if scale else 0
            color = f"rgb({int(255*ratio)},50,{int(255*(1-ratio))})"
            parts.append(f'<rect x="{30+x*cell}" y="{top+(ny-y-1)*cell}" width="{cell}" height="{cell}" fill="{color}">'
                         f'<title>{html.escape(f"{value:.8g} MPa")}</title></rect>')
    parts.append(f'<text x="30" y="{height-30}" font-family="sans-serif" font-size="13">Blue: 0 MPa; red: {scale:.5g} MPa (shared scale)</text></svg>')
    return "\n".join(parts)


def save_result(result, directory):
    """Only writes a new output directory; never overwrites a prior run."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=False)
    (out / "report.json").write_text(json.dumps(result.report, indent=2, allow_nan=False), encoding="utf-8")
    (out / "report.md").write_text(markdown(result.report), encoding="utf-8")
    (out / "stress.svg").write_text(stress_svg(result), encoding="utf-8")
    np.savez_compressed(out / "fields.npz", original=result.original.mask, optimized=result.optimized.mask,
                        protected=result.protected, original_stress=result.baseline.element_stress,
                        optimized_stress=result.final.element_stress,
                        original_displacements=result.baseline.displacements, optimized_displacements=result.final.displacements)
    export_stl(result.original, out / "original.stl")
    export_stl(result.optimized, out / "optimized.stl")
    return out
