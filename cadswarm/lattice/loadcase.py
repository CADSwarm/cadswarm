"""A load case (supports and loads given as points sampled on CAD faces) applied to any voxel grid.

The same face samples are mapped onto every grid the pipeline uses (the solid baseline, the equivalent-
continuum search and the explicit verify), so all three see the same supports and loads.

Loads: force (total newtons with a direction, or "normal": into the face), pressure (MPa on the face's
area, into the face) and gravity (g times the material density along a direction).
"""
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .vfem import CORNERS, node_lin


@dataclass
class Applied:
    fixed: np.ndarray        # bool node grid
    load_ids: np.ndarray     # node lin ids
    load_vecs: np.ndarray    # (m, 3) N
    body: object             # N/mm³ vector or None
    seeds: np.ndarray        # bool voxel grid: part voxels touching a support or load
    total_force: list


def sample_triangles(vertices, triangles, step):
    """Points covering triangles at spacing <= step (barycentric grids), for mapping faces onto voxels."""
    v = np.asarray(vertices, float)
    t = np.asarray(triangles, int).reshape(-1, 3)
    if not len(t):
        return np.zeros((0, 3))
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    longest = np.max(np.stack([np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1),
                               np.linalg.norm(a - c, axis=1)]), axis=0)
    k = np.maximum(1, np.ceil(longest / step)).astype(int)
    out = [v]
    for n in np.unique(k):
        sel = k == n
        i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
        keep = (i + j) <= n
        u, w = (i[keep] / n)[None, :, None], (j[keep] / n)[None, :, None]
        A, B, C = a[sel][:, None], b[sel][:, None], c[sel][:, None]
        out.append((A + u * (B - A) + w * (C - A)).reshape(-1, 3))
    return np.concatenate(out)


def _nodes_near(grid, part, samples, reach):
    """Node lin ids of part voxels within `reach` of the sample points."""
    if not len(samples):
        return np.zeros(0, np.int64)
    idx = grid.index_of(samples)
    cand = np.zeros(grid.shape, bool)
    lo = np.clip(idx - 1, 0, np.array(grid.shape) - 1)
    hi = np.clip(idx + 1, 0, np.array(grid.shape) - 1)
    for dx in (0, 1, 2):
        for dy in (0, 1, 2):
            for dz in (0, 1, 2):
                q = np.minimum(lo + [dx, dy, dz], hi)
                cand[q[:, 0], q[:, 1], q[:, 2]] = True
    cells = np.argwhere(cand & part)
    if not len(cells):
        return np.zeros(0, np.int64)
    nidx = np.unique((cells[:, None, :] + CORNERS[None]).reshape(-1, 3), axis=0)
    pts = nidx * grid.pitch + np.asarray(grid.origin)
    d, _ = cKDTree(samples).query(pts, distance_upper_bound=reach)
    return np.unique(node_lin(nidx[np.isfinite(d)], grid.shape))


def _inward(grid, sdf, node_ids):
    """Unit vectors into the part at nodes (minus the sdf gradient at the nearest voxel)."""
    gx, gy, gz = np.gradient(sdf.astype(float), grid.pitch)
    pts = grid.node_points(node_ids)
    idx = np.clip(grid.index_of(pts), 0, np.array(grid.shape) - 1)
    g = np.stack([gx[tuple(idx.T)], gy[tuple(idx.T)], gz[tuple(idx.T)]], axis=1)
    return -g / np.maximum(np.linalg.norm(g, axis=1, keepdims=True), 1e-12)


def apply(case, grid, part, sdf, density_kg_m3):
    """case: {"fixed": [[x,y,z]...], "loads": [{"type", "points", "newtons"|"mpa"|"g", "direction", "area"}]}."""
    reach = 0.75 * grid.pitch
    shape = grid.shape
    fixed_pts = case.get("fixed")
    fixed_ids = _nodes_near(grid, part, np.asarray([] if fixed_pts is None else fixed_pts, float).reshape(-1, 3),
                            reach)
    fixed = np.zeros((shape[0] + 1, shape[1] + 1, shape[2] + 1), bool)
    fixed.ravel()[fixed_ids] = True
    ids, vecs, body, touched = [], [], None, [fixed_ids]
    total = np.zeros(3)
    for load in case.get("loads") or []:
        kind = load["type"]
        if kind == "gravity":
            d = np.asarray(load.get("direction") or [0, 0, -1], float)
            d /= np.linalg.norm(d)
            body = d * float(load.get("g", 9.81)) * density_kg_m3 * 1e-9  # N/mm³
            continue
        nid = _nodes_near(grid, part, np.asarray(load["points"], float).reshape(-1, 3), reach)
        nid = np.setdiff1d(nid, fixed_ids)
        if not len(nid):
            continue
        if kind == "pressure":
            mag, direction = float(load["mpa"]) * float(load["area"]), "normal"
        else:
            mag, direction = float(load["newtons"]), load.get("direction", "normal")
        if direction == "normal":
            v = _inward(grid, sdf, nid) * (mag / len(nid))
        else:
            d = np.asarray(direction, float)
            v = np.tile(d / np.linalg.norm(d) * (mag / len(nid)), (len(nid), 1))
        ids.append(nid)
        vecs.append(v)
        total += v.sum(axis=0)
        touched.append(nid)
    load_ids = np.concatenate(ids) if ids else np.zeros(0, np.int64)
    load_vecs = np.concatenate(vecs) if vecs else np.zeros((0, 3))
    node_set = np.zeros((shape[0] + 1, shape[1] + 1, shape[2] + 1), bool)
    node_set.ravel()[np.concatenate(touched)] = True
    seeds = np.zeros(shape, bool)
    for c in CORNERS:
        seeds |= node_set[c[0]:c[0] + shape[0], c[1]:c[1] + shape[1], c[2]:c[2] + shape[2]]
    return Applied(fixed, load_ids, load_vecs, body, seeds & part, total.tolist())
