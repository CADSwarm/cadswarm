"""The whole lighten job on a triangle mesh: grids, simulate, search, verify, build, mesh, files, report.

Pure NumPy/SciPy, so it runs (and is tested) without FreeCAD; runner.py feeds it from a FreeCAD part.
Phases are reported through `progress(phase, text, fraction)`; `cancel()` returning True stops at the next
check. Everything lands in `out_dir`: result.stl, result.3mf, preview.stl, report.json and report.md.
"""
import json
import os
import time

import numpy as np

from . import calibration, grade, loadcase, mc, optimize, voxelize
from .build import MAX_BUILD_VOXELS, Geometry, compose, preserve_mask, regions, warnings

MAX_SEARCH_ELEMENTS = 60_000   # the search's equivalent continuum is smooth: a coarse grid is enough
MAX_VERIFY_ELEMENTS = 450_000  # verify resolves the real walls: as fine as memory allows
SCHEMA = "cadswarm.lattice/1"


class Cancelled(Exception):
    pass


def _pitch_for(volume, limit, finest):
    return max(finest, (volume / limit) ** (1 / 3))


def _geometry(vertices, faces, bbox, pitch, case, density, recipe):
    grid = voxelize.grid_for(bbox[:3], bbox[3:], pitch)
    part = voxelize.occupancy(vertices, faces, grid)
    if not part.any():
        raise ValueError("The part has no inside at this voxel size: is it a closed solid?")
    geom = Geometry.make(grid, part, np.zeros_like(part), bbox)
    applied = loadcase.apply(case, grid, part, geom.sdf, density) if case else None
    seeds = applied.seeds if applied is not None else np.zeros_like(part)
    if recipe.preserve:
        seeds = seeds | preserve_mask(grid, recipe.preserve)
    geom.keep = seeds & part
    return geom, applied


def run(vertices, faces, case, material, recipe, out_dir, *, name="lattice", progress=None, cancel=None):
    """case: load case samples (loadcase.apply), or None for an unverified uniform lattice."""
    t0 = time.time()
    say = progress or (lambda *a: None)
    stop = cancel or (lambda: False)

    def check():
        if stop():
            raise Cancelled()

    os.makedirs(out_dir, exist_ok=True)
    mesh_in = mc.Mesh(np.asarray(vertices, float), np.asarray(faces, np.int64))
    volume = abs(mesh_in.volume_mm3)
    if volume <= 0:
        raise ValueError("The part has no volume.")
    bbox = mesh_in.bbox()
    size = [bbox[i + 3] - bbox[i] for i in range(3)]
    notes = []
    warn = warnings(recipe, size)
    rho_kg = material["density_kg_m3"]

    # -------------------------------------------------------------- simulate and search
    design, ver = None, None
    if case is not None:
        h_a = recipe.analysis_voxel_mm or _pitch_for(volume, MAX_SEARCH_ELEMENTS, min(size) / 40)
        say("prepare", f"Voxelizing for the analysis at {h_a:.2f} mm", 0.05)
        ana, applied = _geometry(vertices, faces, bbox, h_a, case, rho_kg, recipe)
        check()
        design = optimize.lighten(ana, applied, material, recipe, cancel=stop,
                                  log=lambda ph, text: say(ph, text, 0.2 if ph == "simulate" else 0.45))
        notes += design.notes
        if design.skin_mm != recipe.skin_mm:
            from dataclasses import replace
            recipe = replace(recipe, skin_mm=design.skin_mm)
        check()
        # ---------------------------------------------------------- verify the real walls
        if design.status == "passed":
            target_h = recipe.min_thickness_mm / 2.5
            h_v = min(h_a, max(target_h, _pitch_for(volume * max(design.mass_fraction, 0.2), MAX_VERIFY_ELEMENTS,
                                                    target_h)))
            say("verify", f"Verifying the real lattice at {h_v:.2f} mm", 0.6)
            vg, vap = _geometry(vertices, faces, bbox, h_v, case, rho_kg, recipe)
            n_e = calibration.table()["cells"][recipe.cell]["stiffness"]["n"]
            scale = design.scale
            verify_lo = 0.6
            for attempt in range(3):
                check()
                rho_v = grade.resample(design.rho_at(scale, recipe, material.get("yield_mpa")), ana.grid, vg.grid)
                verify_hi = min(0.79, verify_lo + 0.18)

                def on_verify(text, fraction, lo=verify_lo, hi=verify_hi):
                    say("verify", text, lo + (hi - lo) * fraction)

                ver = optimize.verify(vg, vap, material, recipe, grade.thickness(rho_v, recipe.cell,
                                                                                 recipe.cell_size_mm), cancel=stop,
                                      progress=on_verify)
                verify_lo = verify_hi
                ver["density_level"] = scale
                if ver["stiffness_retained"] >= recipe.stiffness_target or attempt == 2:
                    break
                scale *= (recipe.stiffness_target / ver["stiffness_retained"]) ** (1 / n_e) * 1.03
                say("verify", f"The real lattice kept {ver['stiffness_retained']:.0%}: thickening and checking "
                              "again", verify_lo)
            design.scale = scale
            across = recipe.min_thickness_mm / h_v
            ver["voxels_across_thinnest"] = round(across, 2)
            if across < 2:
                warn.append({"code": "verify_coarse", "severity": "warning",
                             "text": f"The thinnest wall gets only {across:.1f} verify voxels: the verified values "
                                     "are indicative (thin walls come out weaker or disconnected)."})
            if ver["stiffness_retained"] < recipe.stiffness_target:
                design.status = "no_passing_design"
                notes.append(f"The real lattice keeps {ver['stiffness_retained']:.0%} of the stiffness, below the "
                             f"{recipe.stiffness_target:.0%} target, even after thickening. Lower the target or "
                             "choose a stiffer cell.")
    # -------------------------------------------------------------------- build and mesh
    h_b = max(recipe.voxel_mm, (np.prod(size) / MAX_BUILD_VOXELS) ** (1 / 3))
    if h_b > recipe.voxel_mm * 1.001:
        notes.append(f"The build voxel was enlarged from {recipe.voxel_mm:g} to {h_b:.3g} mm to stay within "
                     f"{MAX_BUILD_VOXELS / 1e6:.0f} M voxels.")
    say("lattice", f"Building the lattice at {h_b:.2f} mm", 0.8)
    bg, _ = _geometry(vertices, faces, bbox, h_b, case, rho_kg, recipe)
    check()
    if design is not None:
        rho_b = grade.resample(design.rho_at(design.scale, recipe, material.get("yield_mpa")), ana.grid, bg.grid)
        _, lat_b = regions(bg, recipe)
        rho_b = np.where(lat_b, np.maximum(rho_b, grade.bounds(recipe.cell, recipe.cell_size_mm,
                                                              recipe.min_thickness_mm,
                                                              recipe.max_thickness_mm)[0]), rho_b)
        thick = grade.thickness(np.clip(rho_b, 1e-6, None), recipe.cell, recipe.cell_size_mm)
        thick_used = thick[lat_b] if lat_b.any() else np.array([recipe.min_thickness_mm])
    else:
        t = recipe.thickness_mm or recipe.min_thickness_mm
        thick, thick_used = t, np.array([t])
        notes.append("No load case was given, so the lattice is uniform and its stiffness is not verified.")
    F = compose(bg, recipe, thick)
    check()
    say("mesh", "Meshing", 0.9)
    mesh = mc.contour(F, bg.grid.origin, h_b)
    closed, oriented = mesh.check()
    if not (closed and oriented):
        raise ValueError("The lattice mesh is not closed; try a different voxel size.")
    # F[::2] samples sit at origin + (2i + 0.5) h_b, i.e. half a coarse voxel before the coarse sample
    # centre; contour treats `origin` as a corner, so shift back by h_b / 2 (not forward).
    preview = mc.contour(F[::2, ::2, ::2], tuple(np.asarray(bg.grid.origin) - h_b / 2), h_b * 2)
    mc.write_stl(mesh, os.path.join(out_dir, "result.stl"))
    mc.write_3mf(mesh, os.path.join(out_dir, "result.3mf"), name)
    mc.write_stl(preview, os.path.join(out_dir, "preview.stl"))
    # ---------------------------------------------------------------------------- report
    density_g_mm3 = rho_kg * 1e-6
    solid_mass, lattice_mass = volume * density_g_mm3, mesh.volume_mm3 * density_g_mm3
    report = {
        "schema": SCHEMA, "name": name, "status": design.status if design else "not_verified",
        "recipe": recipe.as_dict(), "material": {k: material.get(k) for k in
                                                 ("name", "youngs_mpa", "poisson", "yield_mpa", "density_kg_m3")},
        "mass": {"solid_g": round(solid_mass, 2), "lattice_g": round(lattice_mass, 2),
                 "weight_saving_pct": round(100 * (1 - mesh.volume_mm3 / volume), 1)},
        "thickness_mm": {"min": round(float(np.min(thick_used)), 3), "max": round(float(np.max(thick_used)), 3),
                         "mean": round(float(np.mean(thick_used)), 3)},
        "search": None if design is None else {
            "stiffness_retained": round(design.stiffness_retained, 4), "mass_fraction": round(design.mass_fraction, 4),
            "safety_factor": design.safety_factor, "density_level": round(design.scale, 5),
            "baseline": design.baseline, "history": design.history},
        "verify": None if ver is None else {k: (round(v, 4) if isinstance(v, float) else v) for k, v in ver.items()},
        "mesh": {"triangles": int(len(mesh.faces)), "vertices": int(len(mesh.vertices)),
                 "volume_mm3": round(mesh.volume_mm3, 2), "bbox": [round(v, 3) for v in mesh.bbox()],
                 "closed": closed, "oriented": oriented, "voxel_mm": round(h_b, 4)},
        "warnings": warn, "notes": notes,
        "files": {"stl": "result.stl", "3mf": "result.3mf", "preview": "preview.stl", "report": "report.md"},
        "seconds": round(time.time() - t0, 1),
        "disclaimer": "Computed on voxel grids with linear elasticity and a simulated cell map. A design aid, "
                      "not a certification: test printed parts before relying on them.",
    }
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=float)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(markdown(report))
    say("done", "Done", 1.0)
    return report


def markdown(r):
    lines = [f"# {r['name']}: lattice report", ""]
    status = {"passed": "Verified: meets the stiffness target", "no_passing_design": "No design met the target",
              "not_verified": "Not verified (no load case)"}[r["status"]]
    lines += [f"**{status}.**", ""]
    m = r["mass"]
    lines += [f"- Weight saving: **{m['weight_saving_pct']} %** ({m['solid_g']} g solid, {m['lattice_g']} g "
              "with the lattice)"]
    v = r.get("verify")
    if v:
        lines += [f"- Stiffness kept (verified on real walls, {v['voxel_mm']:.2f} mm voxels): "
                  f"**{v['stiffness_retained'] * 100:.1f} %** (target {r['recipe']['stiffness_target'] * 100:.0f} %)",
                  f"- Lattice / solid: displacement x{v['displacement_ratio']:.2f}"
                  + (f", peak stress x{v['stress_ratio']:.2f}" if v.get("stress_ratio") else "")
                  + (f", safety factor {v['safety_factor']:.2f}" if v.get("safety_factor") else "")]
    rec = r["recipe"]
    t = r["thickness_mm"]
    lines += [f"- Lattice: {rec['cell']} {rec['cell_size_mm']:g} mm cells, walls {t['min']:g}-{t['max']:g} mm "
              f"({'graded by the stress' if rec['grading'] == 'stress' else 'uniform'}), skin {rec['skin_mm']:g} mm"
              + (f", open faces {', '.join(rec['open_faces'])}" if rec["open_faces"] else ""),
              f"- Mesh: {r['mesh']['triangles']:,} triangles, closed, {r['mesh']['voxel_mm']:g} mm voxels", ""]
    if r["warnings"] or r["notes"]:
        lines += ["## Check before printing", ""] + [f"- {w['text']}" for w in r["warnings"]] + \
                 [f"- {n}" for n in r["notes"]] + [""]
    lines += [f"_{r['disclaimer']}_", ""]
    return "\n".join(lines)
