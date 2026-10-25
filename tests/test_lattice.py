"""Lattice engine: cells, voxelizer, mesher, voxel FEM, calibration, grading, the lighten search and verify."""
import json
import os

import numpy as np
import pytest

pytest.importorskip("scipy")

from cadswarm.lattice import CELLS, build, calibration, cells, grade, loadcase, mc, optimize, pipeline, vfem, voxelize

ALU = {"name": "Aluminium", "youngs_mpa": 68900, "poisson": 0.33, "yield_mpa": 276, "density_kg_m3": 2700}


def box_mesh(L, W, H):
    v = np.array([[x, y, z] for x in (0, L) for y in (0, W) for z in (0, H)], float)
    f = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
         (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    return v, np.array(f)


def face_samples(axis, value, size, step=0.5):
    a, b = [i for i in range(3) if i != axis]
    A, B = np.meshgrid(np.arange(0, size[a] + 1e-9, step), np.arange(0, size[b] + 1e-9, step), indexing="ij")
    p = np.zeros((A.size, 3))
    p[:, axis], p[:, a], p[:, b] = value, A.ravel(), B.ravel()
    return p


def cantilever_case(L, W, H, newtons=200.0):
    size = (L, W, H)
    return {"fixed": face_samples(0, 0.0, size), "loads": [
        {"type": "force", "points": face_samples(0, L, size), "newtons": newtons, "direction": [0, 0, -1]}]}


# --------------------------------------------------------------------------- cells

@pytest.mark.parametrize("cell", CELLS)
def test_every_cell_matches_its_calibrated_density(cell):
    rho = cells.relative_density(cell, 0.15, samples=40)
    assert abs(rho - float(calibration.density(cell, 0.15))) < 0.03
    assert 0 < rho < 1


def test_a_tpms_wall_is_as_thick_as_asked():
    # Along a line crossing the gyroid sheet, the inside run is the wall thickness (within a voxel).
    h = 0.01
    pts = np.zeros((2000, 3))
    pts[:, 0] = np.arange(2000) * h
    pts[:, 1], pts[:, 2] = 0.0, 0.0   # the x axis crosses the 10 mm gyroid at x = 0, 5, 10...
    inside = cells.field("gyroid", pts, 10.0, 1.0) < 0
    runs = np.diff(np.flatnonzero(np.diff(np.r_[0, inside.astype(int), 0])))[::2] * h
    # The axis cuts the sheet obliquely (|grad f| / |df/dx| = sqrt 3), so a run is t to sqrt(3) t long.
    runs = runs[1:-1]                 # the first and last walls are cut in half by the line's ends
    assert runs.size >= 3 and np.all(runs >= 0.95) and np.all(runs <= np.sqrt(3) + 0.1)


def test_beam_fields_are_periodic_and_unknown_cells_are_refused():
    p = np.array([[1.0, 2.0, 3.0]])
    for cell in ("bcc", "fcc", "octet", "cubic"):
        assert np.allclose(cells.field(cell, p, 5.0, 1.0), cells.field(cell, p + 5.0, 5.0, 1.0))
    with pytest.raises(ValueError):
        cells.field("honeycomb", p, 5.0, 1.0)


# ------------------------------------------------------------------- voxels and mesh

def test_sphere_meshes_closed_with_the_right_volume_and_voxelizes_back():
    g = voxelize.grid_for((-6, -6, -6), (6, 6, 6), 0.5)
    sdf = np.linalg.norm(g.points(), axis=-1) - 5.0
    m = mc.contour(sdf, g.origin, g.pitch)
    assert m.check() == (True, True)
    assert abs(m.volume_mm3 / (4 / 3 * np.pi * 125) - 1) < 0.03
    assert np.array_equal(voxelize.occupancy(m.vertices, m.faces, g), sdf < 0)


def test_stl_and_3mf_round_trip_at_millimetre_size(tmp_path):
    v, f = box_mesh(20, 10, 5)
    m = mc.Mesh(v, f)
    mc.write_stl(m, tmp_path / "a.stl")
    back = mc.read_stl(tmp_path / "a.stl")
    assert back.check() == (True, True) and np.allclose(back.bbox(), [0, 0, 0, 20, 10, 5])
    mc.write_3mf(m, tmp_path / "a.3mf", "Box & co")
    import zipfile
    with zipfile.ZipFile(tmp_path / "a.3mf") as z:
        model = z.read("3D/3dmodel.model").decode()
        assert set(z.namelist()) >= {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}
    assert 'unit="millimeter"' in model and model.count("<triangle ") == 12 and "Box &amp; co" in model


# ------------------------------------------------------------------------- voxel FEM

def _bar(nx, ny, nz, pitch, force, axis, **kw):
    mask = np.ones((nx, ny, nz), bool)
    fixed = np.zeros((nx + 1, ny + 1, nz + 1), bool)
    fixed[0] = True
    face = np.argwhere(np.ones((1, ny + 1, nz + 1), bool))
    face[:, 0] = nx
    ids = vfem.node_lin(face, mask.shape)
    vec = np.zeros((len(ids), 3))
    vec[:, axis] = force / len(ids)
    return vfem.solve(mask, pitch, 70000.0, kw.pop("nu", 0.0), fixed, (ids, vec), **kw)


def test_voxel_fem_matches_a_bar_and_a_cantilever():
    bar = _bar(60, 10, 10, 1.0, 1000.0, 0)
    assert abs(bar.compliance_n_mm / (1000 ** 2 * 60 / (70000 * 100)) - 1) < 0.01
    beam = _bar(60, 10, 10, 1.0, 100.0, 2, nu=0.3)
    I, G = 10 * 10 ** 3 / 12, 70000 / 2.6
    timoshenko = 100 * 60 ** 3 / (3 * 70000 * I) + 100 * 60 / (5 / 6 * G * 100)
    assert abs(beam.max_displacement_mm / timoshenko - 1) < 0.03


def test_multigrid_and_jacobi_agree_and_weights_scale_stiffness():
    a = _bar(40, 10, 10, 1.0, 100.0, 2, nu=0.3)
    b = _bar(40, 10, 10, 1.0, 100.0, 2, nu=0.3, multigrid=False)
    assert abs(a.compliance_n_mm / b.compliance_n_mm - 1) < 1e-4 and a.iterations < b.iterations / 5
    half = _bar(20, 4, 4, 1.0, 100.0, 0, nu=0.3, weights=np.full((20, 4, 4), 0.5))
    full = _bar(20, 4, 4, 1.0, 100.0, 0, nu=0.3)
    assert abs(half.compliance_n_mm / full.compliance_n_mm - 2) < 1e-6


def test_material_not_held_by_a_support_is_dropped_and_loads_on_it_refused():
    mask = np.zeros((10, 4, 4), bool)
    mask[:4] = True
    mask[6:] = True          # a separate block
    fixed = np.zeros((11, 5, 5), bool)
    fixed[0] = True
    face = np.argwhere(np.ones((1, 5, 5), bool))
    face[:, 0] = 10
    ids = vfem.node_lin(face, mask.shape)
    vec = np.zeros((len(ids), 3))
    vec[:, 2] = 1.0
    with pytest.raises(vfem.SolveError) as e:
        vfem.solve(mask, 1.0, 1000.0, 0.3, fixed, (ids, vec))
    assert e.value.code == "load_off_part"


# ------------------------------------------------------------------ recipe and grading

def test_recipe_rules_and_warnings():
    with pytest.raises(build.RecipeError) as e:
        build.normalize({"skin_mm": 0, "open_faces": ["+z"]})
    assert e.value.code == "open_face_without_skin"
    with pytest.raises(build.RecipeError):
        build.normalize({"cell": "kelvin"})
    r = build.normalize({"cell": "schwarzP", "cell_size_mm": 6, "min_thickness_mm": 0.3, "voxel_mm": 0.4,
                         "max_thickness_mm": 2.4})
    codes = {w["code"] for w in build.warnings(r, (30, 30, 8))}
    assert {"thickness_too_few_voxels", "closed_skin", "channels_close", "thin_part"} <= codes
    coarse = build.normalize({"cell": "gyroid", "cell_size_mm": 2, "min_thickness_mm": 0.8, "voxel_mm": 0.5})
    assert "cell_too_few_voxels" in {w["code"] for w in build.warnings(coarse, (30, 30, 30))}


def test_grading_is_thicker_where_the_stress_is_higher():
    stress = np.linspace(1, 100, 50).reshape(50, 1, 1) * np.ones((50, 4, 4))
    lattice = np.ones_like(stress, bool)
    rho = grade.density(stress, lattice, "gyroid", 8.0, 0.8, 2.8, 0.4)
    t = grade.thickness(rho, "gyroid", 8.0)
    assert np.all(np.diff(t[:, 0, 0]) >= -1e-12) and t[-1, 0, 0] > t[0, 0, 0]
    assert t.min() >= 0.8 - 1e-6 and t.max() <= 2.8 + 1e-6


def test_the_library_ranks_cells_for_a_stiffness_target():
    rows = calibration.library(8.0, "stiffness", 0.1, min_wall_mm=0.8)
    assert len(rows) == len(CELLS) and rows[0]["printable"]
    best = {r["cell"]: r for r in rows}
    # Diamond is stiffer for its weight than Schwarz P.
    assert best["diamond"]["relative_density"] < best["schwarzP"]["relative_density"]


# ----------------------------------------------------------------- search and verify

def _geometry(L, W, H, pitch, case, recipe):
    v, f = box_mesh(L, W, H)
    return pipeline._geometry(v, f, (0, 0, 0, L, W, H), pitch, case, ALU["density_kg_m3"], recipe)


def test_the_search_meets_the_stiffness_target_and_verify_agrees():
    recipe = build.normalize({"cell": "gyroid", "cell_size_mm": 6, "min_thickness_mm": 0.8, "skin_mm": 1.0,
                              "stiffness_target": 0.75, "voxel_mm": 0.5})
    case = cantilever_case(30, 12, 12)
    geom, applied = _geometry(30, 12, 12, 0.6, case, recipe)
    d = optimize.lighten(geom, applied, ALU, recipe)
    assert d.status == "passed" and d.stiffness_retained >= 0.75 and d.mass_fraction < 1
    assert all(h["passed"] == (h["stiffness_retained"] >= 0.75) or h["safety_factor"] is not None
               for h in d.history)
    from dataclasses import replace
    vg, vap = _geometry(30, 12, 12, 0.4, case, replace(recipe, skin_mm=d.skin_mm))
    rho = grade.resample(d.rho_at(d.scale, recipe, ALU["yield_mpa"]), geom.grid, vg.grid)
    v = optimize.verify(vg, vap, ALU, replace(recipe, skin_mm=d.skin_mm), grade.thickness(rho, "gyroid", 6.0))
    assert 0.5 < v["stiffness_retained"] <= 1.0 and v["mass_fraction"] < 1


def test_an_impossible_target_is_reported_not_faked():
    recipe = build.normalize({"cell": "cubic", "cell_size_mm": 6, "min_thickness_mm": 0.8, "skin_mm": 0.6,
                              "stiffness_target": 0.99, "voxel_mm": 0.5, "auto_skin": False})
    geom, applied = _geometry(30, 12, 12, 0.6, cantilever_case(30, 12, 12), recipe)
    d = optimize.lighten(geom, applied, ALU, recipe)
    assert d.status == "no_passing_design" and d.stiffness_retained < 0.99
    assert "below the 99% target" in d.notes[0]


def test_auto_skin_thickens_the_skin_when_the_core_is_not_enough():
    recipe = build.normalize({"cell": "gyroid", "cell_size_mm": 6, "min_thickness_mm": 0.8, "skin_mm": 0.6,
                              "stiffness_target": 0.9, "voxel_mm": 0.5})
    geom, applied = _geometry(30, 12, 12, 0.6, cantilever_case(30, 12, 12), recipe)
    d = optimize.lighten(geom, applied, ALU, recipe)
    assert d.skin_mm > 0.6 and d.status in ("passed", "no_passing_design")
    if d.status == "passed":
        assert "skin was thickened" in d.notes[0]


# ----------------------------------------------------------------------- whole job

def test_the_whole_job_writes_a_closed_lattice_and_an_honest_report(tmp_path, monkeypatch):
    calls = []
    real_contour = mc.contour

    def record(values, origin, pitch):
        calls.append((np.asarray(values).shape, tuple(np.asarray(origin, float)), pitch))
        return real_contour(values, origin, pitch)

    monkeypatch.setattr(mc, "contour", record)
    v, f = box_mesh(24, 12, 12)
    recipe = build.normalize({"cell": "octet", "cell_size_mm": 6, "min_thickness_mm": 1.0, "skin_mm": 1.0,
                              "stiffness_target": 0.7, "voxel_mm": 0.5, "open_faces": ["+y", "-y"]})
    phases = []
    r = pipeline.run(v, f, cantilever_case(24, 12, 12), ALU, recipe, str(tmp_path), name="Bracket",
                     progress=lambda ph, text, fr: phases.append(ph))
    assert {"prepare", "simulate", "lighten", "lattice", "mesh", "done"} <= set(phases)
    assert r["mesh"]["closed"] and r["mesh"]["oriented"]
    assert np.allclose(r["mesh"]["bbox"], [0, 0, 0, 24, 12, 12], atol=0.6)
    # The preview is F[::2] on a coarse grid: its origin must be shifted back by half a build voxel,
    # or the whole preview sits one voxel (+h_b) away from result.stl.
    preview = mc.read_stl(tmp_path / "preview.stl")
    assert np.allclose(preview.bbox(), r["mesh"]["bbox"], atol=0.75 * r["mesh"]["voxel_mm"])
    # Pin the grid convention too: a bbox alone can pass the wrong sign when the part's size makes
    # the half-resolution quantisation cancel the shift (e.g. a 20x10x5 box at h_b = 0.5).
    h_b = r["mesh"]["voxel_mm"]
    assert len(calls) == 2
    (full_shape, full_origin, full_pitch), (preview_shape, preview_origin, preview_pitch) = calls
    assert full_pitch == pytest.approx(h_b) and preview_pitch == pytest.approx(2 * h_b)
    assert preview_shape == tuple((np.asarray(full_shape) + 1) // 2)   # F[::2]'
    assert np.allclose(preview_origin, np.asarray(full_origin) - h_b / 2)
    assert 0 < r["mass"]["weight_saving_pct"] < 100
    for name in ("result.stl", "result.3mf", "preview.stl", "report.json", "report.md"):
        assert (tmp_path / name).stat().st_size > 0
    saved = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert saved["schema"] == "cadswarm.lattice/1" and "not a certification" in saved["disclaimer"]
    if saved["status"] == "passed":
        assert saved["verify"]["stiffness_retained"] >= 0.7
    assert "Weight saving" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_without_a_load_case_the_lattice_is_uniform_and_marked_unverified(tmp_path):
    v, f = box_mesh(16, 16, 8)
    recipe = build.normalize({"cell": "bcc", "cell_size_mm": 5, "min_thickness_mm": 1.2, "skin_mm": 1.0,
                              "voxel_mm": 0.5, "grading": "uniform", "thickness_mm": 1.2})
    r = pipeline.run(v, f, None, ALU, recipe, str(tmp_path))
    assert r["status"] == "not_verified" and r["verify"] is None
    assert r["thickness_mm"]["min"] == r["thickness_mm"]["max"] == 1.2
    assert any("not verified" in n for n in r["notes"])


def test_cancel_stops_the_job(tmp_path):
    v, f = box_mesh(16, 8, 8)
    recipe = build.normalize({"cell": "gyroid", "cell_size_mm": 5, "voxel_mm": 0.5})
    with pytest.raises((pipeline.Cancelled, vfem.SolveError)):
        pipeline.run(v, f, cantilever_case(16, 8, 8), ALU, recipe, str(tmp_path), cancel=lambda: True)
