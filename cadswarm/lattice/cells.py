"""Lattice cells as implicit fields: negative inside material, zero on the surface, in millimetres.

TPMS sheets use the level set's first-order distance, f / |grad f|, so `thickness` is the real wall
thickness. Beams are the distance to the struts, so `thickness` is the strut diameter. The struts of a
periodic lattice are whole lines through its nodes, which makes the beam fields exact and seamless.

`thickness` may be a scalar or an array the shape of the points (a graded lattice).
"""
import numpy as np

from . import BEAMS, CELLS, TPMS

TWO_PI = 2 * np.pi


# ------------------------------------------------------------------- TPMS (value and gradient)

def _gyroid(x, y, z):
    sx, cx, sy, cy, sz, cz = np.sin(x), np.cos(x), np.sin(y), np.cos(y), np.sin(z), np.cos(z)
    f = sx * cy + sy * cz + sz * cx
    return f, (cx * cy - sz * sx, -sx * sy + cy * cz, -sy * sz + cz * cx)


def _diamond(x, y, z):
    sx, cx, sy, cy, sz, cz = np.sin(x), np.cos(x), np.sin(y), np.cos(y), np.sin(z), np.cos(z)
    f = sx * sy * sz + sx * cy * cz + cx * sy * cz + cx * cy * sz
    gx = cx * sy * sz + cx * cy * cz - sx * sy * cz - sx * cy * sz
    gy = sx * cy * sz - sx * sy * cz + cx * cy * cz - cx * sy * sz
    gz = sx * sy * cz - sx * cy * sz - cx * sy * sz + cx * cy * cz
    return f, (gx, gy, gz)


def _schwarz_p(x, y, z):
    return np.cos(x) + np.cos(y) + np.cos(z), (-np.sin(x), -np.sin(y), -np.sin(z))


def _iwp(x, y, z):
    cx, cy, cz = np.cos(x), np.cos(y), np.cos(z)
    sx, sy, sz = np.sin(x), np.sin(y), np.sin(z)
    f = 2 * (cx * cy + cy * cz + cz * cx) - (np.cos(2 * x) + np.cos(2 * y) + np.cos(2 * z))
    gx = 2 * (-sx * cy - cz * sx) + 2 * np.sin(2 * x)
    gy = 2 * (-cx * sy - sy * cz) + 2 * np.sin(2 * y)
    gz = 2 * (-cy * sz - sz * cx) + 2 * np.sin(2 * z)
    return f, (gx, gy, gz)


_TPMS = {"gyroid": _gyroid, "diamond": _diamond, "schwarzP": _schwarz_p, "iwp": _iwp}


def _tpms(cell, pts, size, thickness):
    k = TWO_PI / size
    f, grad = _TPMS[cell](pts[..., 0] * k, pts[..., 1] * k, pts[..., 2] * k)
    g = np.sqrt(grad[0] ** 2 + grad[1] ** 2 + grad[2] ** 2) * k
    # Where the gradient vanishes (saddle points) the sheet is far away; cap the estimate.
    dist = np.abs(f) / np.maximum(g, 1e-6 * k)
    return np.minimum(dist, size) - np.asarray(thickness) / 2


# ------------------------------------------------------------------------- beams

def _dirs(*vs):
    a = np.asarray(vs, float)
    return a / np.linalg.norm(a, axis=1, keepdims=True)


_BODY = _dirs((1, 1, 1), (1, 1, -1), (1, -1, 1), (-1, 1, 1))
_FACE = _dirs((1, 1, 0), (1, -1, 0), (1, 0, 1), (1, 0, -1), (0, 1, 1), (0, 1, -1))
_AXES = np.eye(3)
_CORNERS = np.array([(i, j, k) for i in (0, 1) for j in (0, 1) for k in (0, 1)], float)
_FACE_CENTRES = np.array([(.5, .5, 0), (.5, 0, .5), (0, .5, .5), (.5, .5, 1), (.5, 1, .5), (1, .5, .5)])

# Struts as (unit direction, points the lines pass through), in cell units over one periodic cell.
_STRUTS = {
    "cubic": [(_AXES, _CORNERS)],
    "bcc": [(_BODY, _CORNERS)],
    "fcc": [(_FACE, _CORNERS)],
    # Octet truss: every nearest-neighbour bond of the FCC points (corners and face centres).
    "octet": [(_FACE, np.vstack([_CORNERS, _FACE_CENTRES]))],
}


def _beam(cell, pts, size, thickness):
    u = np.mod(pts / size, 1.0)
    best = np.full(pts.shape[:-1], np.inf, dtype=float)
    for dirs, bases in _STRUTS[cell]:
        for d in dirs:
            for b in bases:
                r = u - b
                along = r @ d
                perp = r - along[..., None] * d
                np.minimum(best, np.einsum("...i,...i->...", perp, perp), out=best)
    return np.sqrt(best) * size - np.asarray(thickness) / 2


def field(cell, pts, size, thickness):
    """Signed field of `cell` at points `pts` (..., 3) in mm: < 0 inside the walls or struts."""
    if cell not in CELLS:
        raise ValueError(f"cell must be one of {', '.join(CELLS)}, not {cell!r}")
    if not size > 0:
        raise ValueError("cell size must be positive")
    pts = np.asarray(pts, float)
    return _tpms(cell, pts, size, thickness) if cell in TPMS else _beam(cell, pts, size, thickness)


def grid_points(origin, shape, pitch):
    """Voxel-centre coordinates (nx, ny, nz, 3) of a grid starting at `origin`."""
    axes = [origin[i] + (np.arange(shape[i]) + .5) * pitch for i in range(3)]
    return np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1)


def relative_density(cell, thickness_ratio, samples=48):
    """Solid fraction of one periodic cell at wall/strut thickness = thickness_ratio * cell size."""
    pts = grid_points((0, 0, 0), (samples,) * 3, 1.0 / samples)
    return float(np.mean(field(cell, pts, 1.0, thickness_ratio) < 0))


def is_beam(cell):
    return cell in BEAMS
