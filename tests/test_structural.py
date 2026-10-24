"""Numerical validation and rejection tests; no heatmap-based acceptance."""
from dataclasses import replace
import json

import pytest
np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from cadswarm.structural import optimize_part
from cadswarm.structural.calculix import CalculixSolver, find_ccx, parse_dat
from cadswarm.structural.collaboration import accepted_operation
from cadswarm.structural.engine import violations
from cadswarm.structural.fem import ScipySolver, matrices, assemble, recover
from cadswarm.structural.freecad import preserve_boxes
from cadswarm.structural.geometry import Grid, protected_mask, manifold, export_stl, surface_quads
from cadswarm.structural.model import Material, Load, Support, Constraints, StructuralError, SolverError, Cancelled
from cadswarm.structural.report import save_result

MAT = Material("Test aluminum", 70000, .3, 240, 2.7e-6)
LOADS = [Load("x_max", (0, 0, -100))]
SUPPORTS = [Support("x_min")]
LIMITS = Constraints(80, .2, 2, target_reduction=.05, max_trials=6)


def beam(shape=(12, 4, 4), pitch=2):
    return Grid(np.ones(shape, bool), pitch)


def test_element_rigid_modes_symmetry_and_positive_energy():
    ke, _, _ = matrices(2, MAT.young_mpa, MAT.poisson)
    assert np.allclose(ke, ke.T)
    eig = np.linalg.eigvalsh(ke)
    assert np.sum(abs(eig) < 1e-7) == 6
    assert np.all(eig[6:] > 0)


def test_axial_patch_force_and_strain_energy():
    grid = beam()
    mat = replace(MAT, poisson=0)
    result = ScipySolver().solve(grid, mat, [Load("x_max", (100, 0, 0))], SUPPORTS)
    stress = 100 / 64
    displacement = stress / mat.young_mpa * 24
    assert result.max_stress_mpa == pytest.approx(stress, rel=1e-9)
    assert result.max_displacement_mm == pytest.approx(displacement, rel=1e-9)
    assert result.reaction_n == pytest.approx([-100, 0, 0], abs=1e-8)
    assert result.compliance_n_mm == pytest.approx(2 * result.element_energy.sum(), rel=1e-9)
    assert result.residual < 1e-9


def test_cantilever_mesh_convergence():
    # Timoshenko rectangular beam tip deflection, L/h=6. Clamp stresses are not a convergence metric.
    length, width, height, force = 48, 8, 8, 100
    inertia = width * height ** 3 / 12
    shear = MAT.young_mpa / (2*(1+MAT.poisson))
    theory = force*length**3/(3*MAT.young_mpa*inertia) + force*length/((5/6)*shear*width*height)
    errors = []
    for pitch in (4, 2, 1):
        grid = beam((int(length/pitch), int(width/pitch), int(height/pitch)), pitch)
        sim = ScipySolver().solve(grid, MAT, LOADS, SUPPORTS)
        errors.append(abs(sim.max_displacement_mm / theory - 1))
    assert errors[2] < errors[1] < errors[0]
    assert errors[2] < .05


def test_reduced_cross_section_increases_stress_and_displacement():
    a = ScipySolver().solve(beam((12, 4, 4)), MAT, LOADS, SUPPORTS)
    b = ScipySolver().solve(beam((12, 4, 3)), MAT, LOADS, SUPPORTS)
    assert b.max_stress_mpa > a.max_stress_mpa
    assert b.max_displacement_mm > a.max_displacement_mm


@pytest.mark.parametrize("factory", [lambda: Material("", 1, .3, 1, 1),
    lambda: replace(MAT, young_mpa=float("nan")), lambda: replace(MAT, poisson=.499),
    lambda: replace(MAT, yield_mpa=-1), lambda: replace(MAT, density_kg_mm3=0),
    lambda: Load("x_max", (0, 0, 0)), lambda: Load("Face2", (1, 0, 0)),
    lambda: Load("x_max", (float("inf"), 0, 0)), lambda: Support("hole"),
    lambda: replace(LIMITS, max_stress_mpa=float("nan")), lambda: replace(LIMITS, safety_factor=.9),
    lambda: replace(LIMITS, max_displacement_mm=0), lambda: replace(LIMITS, target_reduction=1),
    lambda: replace(LIMITS, max_trials=True), lambda: replace(LIMITS, skin_cells=0)])
def test_invalid_inputs(factory):
    with pytest.raises(StructuralError):
        factory()


def test_missing_and_overlapping_boundary_conditions():
    for loads, supports in (([], SUPPORTS), (LOADS, []), (LOADS, [Support("x_max")]), (LOADS, [Support("z_min")])):
        with pytest.raises(StructuralError):
            ScipySolver().solve(beam(), MAT, loads, supports)
    with pytest.raises(StructuralError):
        optimize_part(beam(), None, LOADS, SUPPORTS, LIMITS)


def test_geometry_bounds_connectivity_and_manifold():
    with pytest.raises(StructuralError):
        Grid(np.ones((17, 17, 17), bool), 1)
    with pytest.raises(StructuralError):
        Grid(np.ones((2, 2, 2)), 1)
    with pytest.raises(StructuralError):
        Grid(np.ones((2, 2, 2), bool), 0)
    disconnected = np.ones((3, 3, 3), bool)
    disconnected[1] = False
    with pytest.raises(StructuralError, match="connected"):
        Grid(disconnected, 1)
    m = np.ones((4, 4, 4), bool)
    m[1, 1, 1] = m[2, 2, 1] = False
    assert not manifold(Grid(m, 1))


def test_skin_holes_and_designated_regions_preserved():
    mask = np.ones((12, 8, 8), bool)
    mask[3:5, 3:5, :] = False  # grid-aligned through mounting hole
    grid = Grid(mask, 2)
    keep = preserve_boxes(grid, [[[12, 6, 6], [18, 10, 10]]])
    protected = protected_mask(grid, LOADS, SUPPORTS, 1, keep)
    assert protected[2, 3, 3]  # hole wall
    assert protected[0].all() and protected[-1].all()
    result = optimize_part(grid, MAT, LOADS, SUPPORTS, LIMITS, preserve=keep)
    assert np.all(result.optimized.mask[protected])
    assert not np.any(result.optimized.mask & ~grid.mask)
    assert result.original.volume_mm3 == mask.sum() * 8
    assert np.all(result.optimized.mask[keep])
    with pytest.raises(StructuralError):
        preserve_boxes(grid, [[[100, 100, 100], [101, 101, 101]]])
    with pytest.raises(StructuralError):
        optimize_part(grid, MAT, LOADS, SUPPORTS, LIMITS, preserve=~grid.mask)


def test_actual_optimization_resolves_and_exports(tmp_path):
    class RecordingSolver(ScipySolver):
        def __init__(self):
            self.masks = []
        def solve(self, grid, *args):
            self.masks.append(grid.mask.copy())
            return super().solve(grid, *args)
    solver = RecordingSolver()
    result = optimize_part(beam((16, 6, 6)), MAT, LOADS, SUPPORTS, LIMITS, solver=solver)
    assert result.improved
    assert len(solver.masks) >= 2
    assert not np.array_equal(solver.masks[0], solver.masks[1])
    assert result.optimized.volume_mm3 < result.original.volume_mm3
    assert not violations(result.final, result.baseline, MAT, LIMITS)
    out = save_result(result, tmp_path / "output")
    assert json.loads((out / "report.json").read_text())["status"] == "improved"
    assert (out / "optimized.stl").stat().st_size > 100
    assert (out / "stress.svg").read_text().startswith("<svg")
    with pytest.raises(FileExistsError):
        save_result(result, out)
    # Oriented tetrahedra from the exported surface reproduce actual solid volume.
    volume = 0
    for quad in surface_quads(result.optimized):
        points = np.asarray(quad) * result.optimized.pitch_mm
        for i in (1, 2):
            volume += np.dot(points[0], np.cross(points[i], points[i+1])) / 6
    assert volume == pytest.approx(result.optimized.volume_mm3)


def test_constraints_and_solver_failures_reject_candidates():
    class FailingCandidate(ScipySolver):
        def __init__(self):
            self.calls = 0
        def solve(self, *args):
            self.calls += 1
            if self.calls > 1:
                raise SolverError("test singular candidate")
            return super().solve(*args)
    failed = optimize_part(beam(), MAT, LOADS, SUPPORTS, LIMITS, solver=FailingCandidate())
    assert not failed.improved and all(not h["accepted"] for h in failed.report["history"])
    strict = replace(LIMITS, max_displacement_ratio=1, max_stress_ratio=1)
    result = optimize_part(beam(), MAT, LOADS, SUPPORTS, strict)
    assert not result.improved
    assert any(h.get("failures") for h in result.report["history"])
    bad_baseline = optimize_part(beam(), MAT, LOADS, SUPPORTS, replace(LIMITS, max_displacement_mm=1e-10))
    assert bad_baseline.report["status"] == "baseline_infeasible"
    assert not bad_baseline.report["history"]
    with pytest.raises(Cancelled):
        optimize_part(beam(), MAT, LOADS, SUPPORTS, LIMITS, cancel=lambda: True)


def test_singular_stiffness_is_reported(monkeypatch):
    from scipy.sparse.linalg import MatrixRankWarning
    def fail(*a, **kw):
        raise MatrixRankWarning("singular")
    monkeypatch.setattr("cadswarm.structural.fem.spsolve", fail)
    with pytest.raises(SolverError, match="Singular"):
        ScipySolver().solve(beam(), MAT, LOADS, SUPPORTS)


def test_non_equilibrated_solution_is_rejected():
    grid = beam()
    system = assemble(grid, MAT, LOADS, SUPPORTS)
    with pytest.raises(SolverError, match="Equilibrium"):
        recover(grid, system, np.zeros_like(system[5]), "invalid")


def test_missing_solver_and_partial_results(tmp_path, monkeypatch):
    monkeypatch.setenv("CADSWARM_CCX", str(tmp_path / "missing"))
    with pytest.raises(SolverError, match="does not point"):
        find_ccx()
    with pytest.raises(SolverError):
        CalculixSolver(str(tmp_path / "missing")).solve(beam(), MAT, LOADS, SUPPORTS)
    with pytest.raises(SolverError, match="every node"):
        parse_dat("displacements (x,y,z)\n 1 0 0 0", 2, 1)


def test_calculix_matches_independent_solver():
    try:
        executable = find_ccx()
    except SolverError:
        pytest.skip("CalculiX executable unavailable")
    grid = beam((12, 4, 4))
    a = ScipySolver().solve(grid, MAT, LOADS, SUPPORTS)
    b = CalculixSolver(executable).solve(grid, MAT, LOADS, SUPPORTS)
    assert b.max_displacement_mm == pytest.approx(a.max_displacement_mm, rel=2e-6)
    assert b.max_stress_mpa == pytest.approx(a.max_stress_mpa, rel=2e-6)


def test_accepted_operation_never_replaces_source_and_has_revision_guards():
    report = {"status": "improved", "protected_cells_preserved": True}
    op = accepted_operation("Source", 7, "source brep", "candidate brep", "NewPart", report)
    assert op["op"] == "run_part_script"
    assert op["expected_revs"] == {"Source": 7, "NewPart": 0}
    assert "Output name already exists" in op["args"]["code"]
    with pytest.raises(ValueError):
        accepted_operation("Source", 7, "a", "b", "Source", report)
    with pytest.raises(ValueError):
        accepted_operation("Source", 7, "a", "b", "NewPart", {"status": "no_improvement"})
