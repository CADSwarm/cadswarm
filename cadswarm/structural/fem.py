"""Full-integration C3D8 linear elasticity, consistent face traction, sparse solve.

No ersatz material: removed cells are absent from both stiffness and exported solid.
Stress is the maximum von Mises value across all eight Gauss points per element.
"""
from dataclasses import dataclass
from functools import lru_cache
from itertools import product
import warnings
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import MatrixRankWarning, spsolve

from .geometry import CORNERS, FACES, mesh, side_elements
from .model import SolverError, StructuralError


@dataclass
class Simulation:
    solver: str
    max_stress_mpa: float
    max_displacement_mm: float
    compliance_n_mm: float
    residual: float
    reaction_n: list
    element_stress: np.ndarray
    element_energy: np.ndarray
    displacements: np.ndarray

    def summary(self):
        return {k: getattr(self, k) for k in ("solver", "max_stress_mpa", "max_displacement_mm",
                                             "compliance_n_mm", "residual", "reaction_n")}


@lru_cache(maxsize=32)
def matrices(pitch, young, nu):
    d = np.zeros((6, 6))
    d[:3, :3] = nu
    np.fill_diagonal(d[:3, :3], 1 - nu)
    np.fill_diagonal(d[3:, 3:], (1 - 2 * nu) / 2)
    d *= young / ((1 + nu) * (1 - 2 * nu))
    signs = 2 * CORNERS - 1
    bs = []
    for local in product((-1 / np.sqrt(3), 1 / np.sqrt(3)), repeat=3):
        deriv = np.empty((8, 3))
        for axis in range(3):
            other = [i for i in range(3) if i != axis]
            deriv[:, axis] = signs[:, axis] * np.prod(1 + signs[:, other] * np.array(local)[other], axis=1) / (4 * pitch)
        b = np.zeros((6, 24))
        for n, (dx, dy, dz) in enumerate(deriv):
            c = 3 * n
            b[0, c], b[1, c + 1], b[2, c + 2] = dx, dy, dz
            b[3, c], b[3, c + 1] = dy, dx
            b[4, c + 1], b[4, c + 2] = dz, dy
            b[5, c], b[5, c + 2] = dz, dx
        bs.append(b)
    ke = sum(b.T @ d @ b for b in bs) * pitch ** 3 / 8
    return ke, np.asarray(bs), d


def boundary_conditions(cells, nodes, elements, loads, supports):
    if not loads or not supports:
        raise StructuralError("At least one explicit load and fixed support are required")
    fixed_nodes = []
    for support in supports:
        ids, face = side_elements(cells, support.side)
        fixed_nodes.extend(elements[ids][:, FACES[face]].ravel())
    fixed_nodes = np.unique(fixed_nodes)
    f = np.zeros((len(nodes), 3))
    for load in loads:
        ids, face = side_elements(cells, load.side)
        faces = elements[ids][:, FACES[face]]
        if np.intersect1d(fixed_nodes, faces).size:
            raise StructuralError("Loaded and fixed faces overlap at nodes; choose disjoint boundaries")
        # Each equal-area quad contributes one quarter of its total force to each node.
        for component in range(3):
            np.add.at(f[:, component], faces.ravel(), load.force_n[component] / (4 * len(ids)))
    if not np.any(f):
        raise StructuralError("Applied loads cancel to zero")
    fixed = (fixed_nodes[:, None] * 3 + np.arange(3)).ravel()
    return f.ravel(), fixed


def assemble(grid, material, loads, supports):
    cells, nodes, elements = mesh(grid)
    dofs = (elements[:, :, None] * 3 + np.arange(3)).reshape(-1, 24)
    ke, bs, d = matrices(float(grid.pitch_mm), material.young_mpa, material.poisson)
    rows = np.broadcast_to(dofs[:, :, None], (len(cells), 24, 24)).ravel()
    cols = np.broadcast_to(dofs[:, None, :], (len(cells), 24, 24)).ravel()
    k = sparse.coo_matrix((np.broadcast_to(ke, (len(cells), 24, 24)).ravel(), (rows, cols)),
                          shape=(len(nodes) * 3, len(nodes) * 3)).tocsr()
    force, fixed = boundary_conditions(cells, nodes, elements, loads, supports)
    return cells, nodes, elements, dofs, k, force, fixed, ke, bs, d


def recover(grid, system, u, solver, printed_digits=None):
    cells, nodes, elements, dofs, k, force, fixed, ke, bs, d = system
    free = np.setdiff1d(np.arange(len(force)), fixed)
    if not np.isfinite(u).all() or u.shape != force.shape:
        raise SolverError("Solver returned invalid displacements")
    imbalance = k @ u - force
    residual = float(np.linalg.norm(imbalance[free]) / np.linalg.norm(force))
    tolerance = 1e-8
    if printed_digits:
        # CalculiX .dat writes seven significant digits. Bound equilibrium error
        # due to that quantization instead of loosening the check arbitrarily.
        error = np.zeros_like(u)
        nonzero = u != 0
        error[nonzero] = .5 * 10. ** (np.floor(np.log10(np.abs(u[nonzero]))) - printed_digits + 1)
        bound = float(np.linalg.norm((abs(k) @ error)[free]) / np.linalg.norm(force))
        if bound > .01:
            raise SolverError("Printed solver precision is insufficient for an equilibrium check")
        tolerance = max(tolerance, bound * 1.01)
    if residual > tolerance or np.max(np.abs(u[fixed])) > 1e-10:
        raise SolverError(f"Equilibrium or fixed-boundary check failed (relative residual {residual:.3g})")
    ue = u[dofs]
    stress = np.einsum("eik,kj->eij", np.einsum("giq,eq->egi", bs, ue), d)
    xx, yy, zz, xy, yz, xz = np.moveaxis(stress, -1, 0)
    vm = np.sqrt(np.maximum(0, ((xx-yy)**2 + (yy-zz)**2 + (zz-xx)**2) / 2 + 3*(xy*xy+yz*yz+xz*xz)))
    peak = vm.max(axis=1)
    energy = .5 * np.einsum("ei,ij,ej->e", ue, ke, ue)
    compliance = float(force @ u)
    if not np.isfinite(peak).all() or compliance <= 0 or np.any(energy < -1e-10):
        raise SolverError("Nonphysical solution: stress/strain energy checks failed")
    stress_field = np.zeros(grid.mask.shape)
    energy_field = np.zeros(grid.mask.shape)
    stress_field[tuple(cells.T)] = peak
    energy_field[tuple(cells.T)] = energy
    reaction = np.zeros_like(force)
    reaction[fixed] = imbalance[fixed]
    return Simulation(solver, float(peak.max()), float(np.linalg.norm(u.reshape(-1, 3), axis=1).max()),
                      compliance, residual, reaction.reshape(-1, 3).sum(axis=0).tolist(),
                      stress_field, energy_field, u.reshape(-1, 3))


class ScipySolver:
    name = "SciPy C3D8 (2x2x2 integration)"

    def solve(self, grid, material, loads, supports):
        system = assemble(grid, material, loads, supports)
        k, force, fixed = system[4:7]
        free = np.setdiff1d(np.arange(len(force)), fixed)
        u = np.zeros_like(force)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", MatrixRankWarning)
                u[free] = spsolve(k[free][:, free].tocsc(), force[free])
        except (MatrixRankWarning, RuntimeError) as exc:
            raise SolverError(f"Singular or failed stiffness solve: {exc}") from exc
        return recover(grid, system, u, self.name)
