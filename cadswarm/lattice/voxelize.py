"""Triangle mesh to voxels: occupancy by ray parity, and an approximate signed distance.

For every voxel column (x, y) a vertical ray crosses the closed surface an even number of times. Each
crossing below a voxel centre toggles inside/outside, so a cumulative sum of crossings along z, mod 2, is
the occupancy. Column centres are nudged by a tiny irrational offset so no ray grazes a mesh edge or vertex.
"""
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

NUDGE = (1.234567e-4, 2.718281e-4)  # fractions of a voxel; irrational-looking on purpose


@dataclass(frozen=True)
class VoxelGrid:
    origin: tuple       # world coordinates of the grid's (0, 0, 0) corner, mm
    pitch: float        # voxel edge, mm
    shape: tuple        # (nx, ny, nz)

    def centres(self, axis):
        return self.origin[axis] + (np.arange(self.shape[axis]) + .5) * self.pitch

    def points(self):
        x, y, z = (self.centres(a) for a in range(3))
        return np.stack(np.meshgrid(x, y, z, indexing="ij"), axis=-1)

    def node_points(self, ids):
        """World coordinates of node lin ids."""
        ny, nz = self.shape[1] + 1, self.shape[2] + 1
        i, rest = np.divmod(np.asarray(ids), ny * nz)
        j, k = np.divmod(rest, nz)
        return np.stack([i, j, k], axis=-1) * self.pitch + np.asarray(self.origin)

    def index_of(self, pts):
        """Voxel indices containing world points (n, 3); may be out of range."""
        return np.floor((np.asarray(pts) - np.asarray(self.origin)) / self.pitch).astype(int)

    @property
    def voxels(self):
        return int(np.prod(self.shape))


def grid_for(bbox_min, bbox_max, pitch, margin=2):
    """A grid covering the box with `margin` empty voxels on every side."""
    lo = np.asarray(bbox_min, float) - margin * pitch
    size = np.asarray(bbox_max, float) - np.asarray(bbox_min, float)
    shape = tuple(int(np.ceil(s / pitch)) + 2 * margin for s in size)
    return VoxelGrid(tuple(lo), float(pitch), shape)


def occupancy(vertices, triangles, grid):
    """Boolean (nx, ny, nz): voxel centre inside the closed mesh."""
    v = np.asarray(vertices, float)
    t = np.asarray(triangles, int)
    p = v[t]                                   # (m, 3, 3)
    nx, ny, nz = grid.shape
    h, (ox, oy, oz) = grid.pitch, grid.origin
    toggles = np.zeros((nx, ny, nz + 1), np.int32)
    xs = ox + (np.arange(nx) + .5 + NUDGE[0]) * h
    ys = oy + (np.arange(ny) + .5 + NUDGE[1]) * h
    a, b, c = p[:, 0], p[:, 1], p[:, 2]
    # Signed area of the triangle's xy projection; flat (vertical) triangles never hit a vertical ray.
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
    keep = np.abs(area) > 1e-14
    a, b, c, area = a[keep], b[keep], c[keep], area[keep]
    lo = np.minimum(np.minimum(a, b), c)
    hi = np.maximum(np.maximum(a, b), c)
    i0 = np.clip(np.ceil((lo[:, 0] - ox) / h - .5 - NUDGE[0]).astype(int), 0, nx)
    i1 = np.clip(np.floor((hi[:, 0] - ox) / h - .5 - NUDGE[0]).astype(int), -1, nx - 1)
    j0 = np.clip(np.ceil((lo[:, 1] - oy) / h - .5 - NUDGE[1]).astype(int), 0, ny)
    j1 = np.clip(np.floor((hi[:, 1] - oy) / h - .5 - NUDGE[1]).astype(int), -1, ny - 1)
    # Triangles grouped by how many columns their xy box spans, so each group is one vectorized pass.
    span = (np.maximum(i1 - i0 + 1, 0)) * (np.maximum(j1 - j0 + 1, 0))
    for n in np.unique(span[span > 0]):
        sel = np.flatnonzero(span == n)
        wi = (i1[sel] - i0[sel] + 1)
        # Enumerate the n columns of each triangle's box.
        k = np.arange(n)
        ci = i0[sel][:, None] + k[None, :] % wi[:, None]
        cj = j0[sel][:, None] + k[None, :] // wi[:, None]
        px, py = xs[ci], ys[cj]
        A, B, C, ar = a[sel][:, None], b[sel][:, None], c[sel][:, None], area[sel][:, None]
        # Barycentric coordinates of the column in the projected triangle.
        w0 = ((B[..., 0] - px) * (C[..., 1] - py) - (C[..., 0] - px) * (B[..., 1] - py)) / ar
        w1 = ((C[..., 0] - px) * (A[..., 1] - py) - (A[..., 0] - px) * (C[..., 1] - py)) / ar
        w2 = 1 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        zc = w0 * A[..., 2] + w1 * B[..., 2] + w2 * C[..., 2]
        kz = np.clip(np.ceil((zc - oz) / h - .5).astype(int), 0, nz)
        np.add.at(toggles, (ci[inside], cj[inside], kz[inside]), 1)
    return (np.cumsum(toggles, axis=2)[:, :, :nz] % 2).astype(bool)


def signed_distance(mask, pitch):
    """Approximate signed distance to the voxelized surface (mm): negative inside."""
    inside = ndimage.distance_transform_edt(mask) * pitch
    outside = ndimage.distance_transform_edt(~mask) * pitch
    return np.where(mask, -(inside - pitch / 2), outside - pitch / 2).astype(np.float32)
