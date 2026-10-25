"""Matrix-free linear elasticity on a voxel grid: one hex8 element per occupied voxel.

Every voxel has the same element stiffness (`cadswarm.structural.fem.matrices`), so K·u is a gather, one
small matrix product and a scatter (`bincount`), with no sparse matrix to store. That keeps a few hundred
thousand elements in memory. The system is solved with Jacobi-preconditioned conjugate gradients.

Material that is not connected (face to face) to a support is dropped before the solve: it would make the
stiffness singular, and it carries no load anyway. How much was dropped is reported.

Nodes are indexed on the (nx+1, ny+1, nz+1) node grid: lin = (i * (ny+1) + j) * (nz+1) + k.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from ..structural.fem import matrices

CORNERS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]])  # the node order matrices() uses


class SolveError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass
class Solution:
    compliance_n_mm: float
    max_displacement_mm: float
    stress: np.ndarray            # element von Mises (MPa) on the voxel grid, nan where empty
    iterations: int
    residual: float
    elements: int
    dropped_fraction: float       # occupied volume not connected to a support
    u: np.ndarray = field(repr=False, default=None)
    nodes: np.ndarray = field(repr=False, default=None)  # node lin ids, in u's order

    def stress_percentile(self, q):
        s = self.stress[np.isfinite(self.stress)]
        return float(np.percentile(s, q)) if s.size else 0.0

    def summary(self):
        return {"compliance_n_mm": self.compliance_n_mm, "max_displacement_mm": self.max_displacement_mm,
                "max_stress_mpa": float(np.nanmax(self.stress)) if self.elements else 0.0,
                "p99_stress_mpa": self.stress_percentile(99), "elements": self.elements,
                "iterations": self.iterations, "residual": self.residual,
                "dropped_fraction": self.dropped_fraction}


def node_lin(idx, shape):
    """Linear node ids of integer node coordinates idx (..., 3) on a voxel grid of `shape`."""
    ny, nz = shape[1] + 1, shape[2] + 1
    return (idx[..., 0] * ny + idx[..., 1]) * nz + idx[..., 2]


def element_nodes(cells, shape):
    """(n, 8) node lin ids of the voxels `cells` (n, 3)."""
    return node_lin(cells[:, None, :] + CORNERS[None, :, :], shape)


def touching(mask, node_set):
    """Voxels of `mask` with at least one of their 8 nodes in the boolean node grid `node_set`."""
    out = np.zeros(mask.shape, bool)
    for c in CORNERS:
        out |= node_set[c[0]:c[0] + mask.shape[0], c[1]:c[1] + mask.shape[1], c[2]:c[2] + mask.shape[2]]
    return out & mask


def supported_part(mask, fixed_nodes):
    """The voxels connected face-to-face to a support, and the dropped volume fraction."""
    labels, n = ndimage.label(mask, structure=ndimage.generate_binary_structure(3, 1))
    held = np.unique(labels[touching(mask, fixed_nodes)])
    held = held[held > 0]
    kept = np.isin(labels, held)
    total = int(mask.sum())
    return kept, (1 - kept.sum() / total) if total else 0.0


# ------------------------------------------------------------------- operator and multigrid

class Level:
    """K on a structured element grid with per-element stiffness weights (zero = empty), dof-major arrays."""

    def __init__(self, w, ke, dirichlet):
        self.w = w
        self.dirichlet = dirichlet       # supports only; empty nodes are added below, per level
        ex, ey, ez = self.shape = w.shape
        self.ns = (ex + 1, ey + 1, ez + 1)
        self.wflat = w.ravel()
        self.ke = ke
        self.sl = [np.s_[a:a + ex, b:b + ey, c:c + ez] for a, b, c in CORNERS]
        self.diag = self.scatter(np.diag(ke)[:, None] * self.wflat[None, :])
        self.fixed = dirichlet | (self.diag <= 0)
        self.dinv = np.where(self.fixed, 0.0, 1.0 / np.where(self.diag > 0, self.diag, 1.0))

    def gather(self, xn):
        ue = np.empty((24,) + self.shape)
        for c, sl in enumerate(self.sl):
            for k in range(3):
                ue[3 * c + k] = xn[(k,) + sl]
        return ue.reshape(24, -1)

    def scatter(self, fe):
        fe = fe.reshape((24,) + self.shape)
        y = np.zeros((3,) + self.ns)
        for c, sl in enumerate(self.sl):
            for k in range(3):
                y[(k,) + sl] += fe[3 * c + k]
        return y

    def apply(self, xn):
        y = self.scatter((self.ke @ self.gather(xn)) * self.wflat)
        y[self.fixed] = 0.0
        return y

    def matrix(self):
        """Assembled free-dof matrix (only for small coarse grids)."""
        from scipy import sparse
        cells = np.argwhere(self.w > 0)
        nodes = node_lin(cells[:, None, :] + CORNERS[None], self.shape)
        # Element dof 3 * corner + component (ke's order) -> dof-major index component * nnodes + node.
        nn = int(np.prod(self.ns))
        dofs = (np.arange(3)[None, None, :] * nn + nodes[:, :, None]).reshape(-1, 24)
        vals = (self.ke[None] * self.w[tuple(cells.T)][:, None, None]).ravel()
        rows = np.repeat(dofs, 24, axis=1).ravel()
        cols = np.tile(dofs, (1, 24)).ravel()
        k = sparse.coo_matrix((vals, (rows, cols)), shape=(3 * nn, 3 * nn)).tocsr()
        free = np.flatnonzero(~self.fixed.ravel())
        return k[free][:, free].tocsc(), free


def _prolong_axis(c, axis):
    shape = list(c.shape)
    shape[axis] = 2 * c.shape[axis] - 1
    f = np.empty(shape)
    even = [slice(None)] * c.ndim
    odd = [slice(None)] * c.ndim
    even[axis], odd[axis] = slice(0, None, 2), slice(1, None, 2)
    a = [slice(None)] * c.ndim
    b = [slice(None)] * c.ndim
    a[axis], b[axis] = slice(0, -1), slice(1, None)
    f[tuple(even)] = c
    f[tuple(odd)] = 0.5 * (c[tuple(a)] + c[tuple(b)])
    return f


def _restrict_axis(f, axis):
    even = [slice(None)] * f.ndim
    odd = [slice(None)] * f.ndim
    even[axis], odd[axis] = slice(0, None, 2), slice(1, None, 2)
    c = f[tuple(even)].copy()
    o = 0.5 * f[tuple(odd)]
    a = [slice(None)] * f.ndim
    b = [slice(None)] * f.ndim
    a[axis], b[axis] = slice(0, -1), slice(1, None)
    c[tuple(a)] += o
    c[tuple(b)] += o
    return c


class Multigrid:
    """Geometric multigrid V-cycle used as the CG preconditioner (symmetric, so CG stays valid)."""
    OMEGA, SMOOTH, COARSEST = 0.6, 2, 12_000

    def __init__(self, fine, pitch, young, poisson):
        self.levels = [fine]
        self.pad = []
        lvl, h = fine, pitch
        while 3 * np.prod(lvl.ns) > self.COARSEST and min(lvl.shape) >= 4:
            ex, ey, ez = lvl.shape
            pe = (ex + ex % 2, ey + ey % 2, ez + ez % 2)
            w = np.zeros(pe)
            w[:ex, :ey, :ez] = lvl.w
            wc = w.reshape(pe[0] // 2, 2, pe[1] // 2, 2, pe[2] // 2, 2).mean(axis=(1, 3, 5))
            # Restrict the supports only: a coarse node next to a void is not held, it is just softer.
            fix = self._restrict(self._pad(lvl.dirichlet.astype(float), pe)) > 0
            h *= 2
            ke_c, _, _ = matrices(float(h), float(young), float(poisson))
            self.pad.append(pe)
            lvl = Level(wc, ke_c, fix)
            self.levels.append(lvl)
        self.coarse = None
        if len(self.levels) > 1:
            from scipy.sparse.linalg import splu
            k, self.coarse_free = self.levels[-1].matrix()
            self.coarse = splu(k) if k.shape[0] else None

    @staticmethod
    def _pad(x, pe):
        out = np.zeros((3,) + tuple(n + 1 for n in pe))
        out[:, :x.shape[1], :x.shape[2], :x.shape[3]] = x
        return out

    @staticmethod
    def _restrict(x):
        for a in (1, 2, 3):
            x = _restrict_axis(x, a)
        return x

    @staticmethod
    def _prolong(x):
        for a in (1, 2, 3):
            x = _prolong_axis(x, a)
        return x

    def _omega(self, lvl):
        """Jacobi damping from the level's largest eigenvalue of D^-1 K (a few power iterations):
        the smoother must converge for the V-cycle to stay positive definite on irregular lattices."""
        if getattr(lvl, "omega", None) is None:
            v = np.random.default_rng(1).standard_normal((3,) + lvl.ns)
            v[lvl.fixed] = 0.0
            lam = 1.0
            for _ in range(12):
                n = np.linalg.norm(v)
                if n == 0:
                    break
                v /= n
                w = lvl.dinv * lvl.apply(v)
                lam = float(np.vdot(v, w))
                v = w
            lvl.omega = min(self.OMEGA, 1.0 / max(lam * 1.1, 1e-12))
        return lvl.omega

    def _smooth(self, lvl, x, r):
        om = self._omega(lvl)
        for _ in range(self.SMOOTH):
            x += om * lvl.dinv * (r - lvl.apply(x))
        return x

    def cycle(self, r, i=0):
        lvl = self.levels[i]
        if i == len(self.levels) - 1:
            x = np.zeros_like(r)
            if self.coarse is not None:
                flat = x.reshape(-1)
                flat[self.coarse_free] = self.coarse.solve(r.reshape(-1)[self.coarse_free])
            return x
        x = self._smooth(lvl, np.zeros_like(r), r)
        res = r - lvl.apply(x)
        res[lvl.fixed] = 0.0
        rc = self._restrict(self._pad(res, self.pad[i]))
        rc[self.levels[i + 1].fixed] = 0.0
        xc = self._prolong(self.cycle(rc, i + 1))
        x += xc[:, :lvl.ns[0], :lvl.ns[1], :lvl.ns[2]]
        x[lvl.fixed] = 0.0
        return self._smooth(lvl, x, r)

    def __call__(self, r):
        if len(self.levels) == 1:
            return self.levels[0].dinv * r
        return self.cycle(r)


def solve(mask, pitch, young, poisson, fixed_nodes, loads, *, body_force=None, u0=None, tol=1e-6,
          maxiter=6000, cancel=None, progress=None, weights=None, mass_weights=None, multigrid=True):
    """Static solve.

    mask: bool voxel grid. fixed_nodes: bool node grid (nx+1, ny+1, nz+1), every dof held.
    loads: (node_lin ids (m,), forces (m, 3) N). body_force: N/mm³ vector applied to every kept voxel.
    u0: (node ids, displacements) from an earlier solve to start from.
    weights: per-voxel stiffness scale (a lattice region as an equivalent continuum, E = young * w).
    mass_weights: per-voxel density scale for the body force (defaults to 1).
    The returned stress is the element's (macroscopic) von Mises stress.
    """
    mask = np.asarray(mask, bool)
    shape = mask.shape
    if not fixed_nodes.any():
        raise SolveError("no_supports", "The load case holds no part of the model still.")
    kept, dropped = supported_part(mask, fixed_nodes)
    cells = np.argwhere(kept)
    if not len(cells):
        raise SolveError("no_material", "No material is connected to the supports.")
    # Work on the bounding box of the kept material: elements and nodes are plain array slices there.
    lo, hi = cells.min(axis=0), cells.max(axis=0) + 1
    ex, ey, ez = hi - lo
    crop = np.s_[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]
    ncrop = np.s_[lo[0]:hi[0] + 1, lo[1]:hi[1] + 1, lo[2]:hi[2] + 1]
    km = kept[crop]
    wel = km.astype(float)
    if weights is not None:
        wk = np.asarray(weights, float)[crop]
        if not (np.all(np.isfinite(wk[km])) and np.all(wk[km] > 0)):
            raise SolveError("bad_weights", "Every element needs a positive, finite stiffness.")
        wel = np.where(km, wk, 0.0)
    ns = (ex + 1, ey + 1, ez + 1)
    ke, bs, d = matrices(float(pitch), float(young), float(poisson))
    fine = Level(wel, ke, np.broadcast_to(np.asarray(fixed_nodes, bool)[ncrop][None], (3,) + ns))
    wflat, scatter, gather, apply_k = fine.wflat, fine.scatter, fine.gather, fine.apply

    # Global node coordinates of the crop, for loads and warm starts (raster order is ascending lin id).
    nshape = (shape[0] + 1, shape[1] + 1, shape[2] + 1)
    gi = np.stack(np.meshgrid(*(np.arange(lo[a], hi[a] + 1) for a in range(3)), indexing="ij"), axis=-1)
    nodes = node_lin(gi.reshape(-1, 3), shape)

    diag, fixed = fine.diag, fine.fixed

    f = np.zeros((3,) + ns)
    load_ids, load_vecs = loads
    if len(load_ids):
        applied = np.asarray(load_vecs, float)
        i, rest = np.divmod(np.asarray(load_ids), nshape[1] * nshape[2])
        j, k = np.divmod(rest, nshape[2])
        li, lj, lk = i - lo[0], j - lo[1], k - lo[2]
        inside = (li >= 0) & (li <= ex) & (lj >= 0) & (lj <= ey) & (lk >= 0) & (lk <= ez)
        on = inside.copy()
        on[inside] = diag[0, li[inside], lj[inside], lk[inside]] > 0
        total = np.abs(applied).sum()
        lost = np.abs(applied[~on]).sum() / total if total else 0.0
        if lost > 0.02:
            raise SolveError("load_off_part", f"{lost:.0%} of the load acts on material that is not connected "
                             "to the supports.")
        for k in range(3):
            np.add.at(f[k], (li[on], lj[on], lk[on]), applied[on, k])
    if body_force is not None:
        per_node = np.asarray(body_force, float) * pitch ** 3 / 8
        mw = km.astype(float) if mass_weights is None else np.where(km, np.asarray(mass_weights, float)[crop], 0.0)
        f += scatter(np.tile(per_node, 8)[:, None] * mw.ravel()[None, :])
    f[fixed] = 0.0
    fnorm = np.linalg.norm(f)
    if fnorm == 0:
        raise SolveError("no_load", "The load case applies no force.")
    jacobi = lambda v: fine.dinv * v
    precond = jacobi
    if multigrid:
        try:
            precond = Multigrid(fine, pitch, young, poisson)
        except RuntimeError:  # singular coarse grid (e.g. coarse supports vanish): plain Jacobi
            precond = jacobi

    x = np.zeros((3,) + ns)
    if u0 is not None:
        prev_nodes, prev_u = u0
        if prev_nodes.size:
            pos = np.clip(np.searchsorted(prev_nodes, nodes), 0, prev_nodes.size - 1)
            same = prev_nodes[pos] == nodes
            xf = x.reshape(3, -1)
            xf[:, same] = prev_u.reshape(-1, 3)[pos[same]].T
            x[fixed] = 0.0
    x0 = x.copy()
    r = f - apply_k(x)
    r[fixed] = 0.0
    z = precond(r)
    p = z.copy()
    rz = float(np.vdot(r, z))
    it, res = 0, float(np.linalg.norm(r)) / fnorm
    best_res = res
    while res > tol and it < maxiter:
        # A preconditioner that turns indefinite shows as a growing residual: restart with Jacobi.
        if precond is not jacobi and (rz <= 0 or (it >= 20 and res > 4 * best_res)):
            precond, x = jacobi, x0.copy()
            r = f - apply_k(x)
            r[fixed] = 0.0
            z = precond(r)
            p = z.copy()
            rz = float(np.vdot(r, z))
            res = float(np.linalg.norm(r)) / fnorm
            continue
        best_res = min(best_res, res)
        if cancel is not None and it % 25 == 0 and cancel():
            raise SolveError("cancelled", "Cancelled.")
        ap = apply_k(p)
        ap[fixed] = 0.0
        pap = float(np.vdot(p, ap))
        if pap <= 0:
            raise SolveError("singular", "The stiffness is singular: part of the model can move freely.")
        alpha = rz / pap
        x += alpha * p
        r -= alpha * ap
        it += 1
        res = float(np.linalg.norm(r)) / fnorm
        z = precond(r)
        rz_new = float(np.vdot(r, z))
        p *= rz_new / rz
        p += z
        rz = rz_new
        if progress is not None and it % 200 == 0:
            progress(it, res)
    if res > tol * 50:
        raise SolveError("not_converged", f"The solver did not converge (residual {res:.2g} after {it} "
                         "iterations). Try a coarser analysis voxel or a thicker lattice.")

    # Element stress at the centre: the mean of the Gauss-point strain matrices.
    b_centre = bs.mean(axis=0)
    sig = (d @ b_centre) @ gather(x) * wflat
    xx, yy, zz, xy, yz, xz = sig
    vm = np.sqrt(np.maximum(0, ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2) / 2
                            + 3 * (xy ** 2 + yz ** 2 + xz ** 2))).reshape(ex, ey, ez)
    stress = np.full(shape, np.nan)
    stress[crop] = np.where(km, vm, np.nan)
    x = x.reshape(3, -1).T.reshape(-1)    # node-major (u_x, u_y, u_z per node), like `nodes`
    f = f.reshape(3, -1).T.reshape(-1)
    disp = np.linalg.norm(x.reshape(-1, 3), axis=1)
    return Solution(float(f @ x), float(disp.max()), stress, it, float(res), len(cells), float(dropped),
                    u=x, nodes=nodes)
