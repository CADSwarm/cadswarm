"""Exact orthogonal solid grid, surface extraction and preservation masks."""
from dataclasses import dataclass
from collections import Counter
import numpy as np
from scipy import ndimage

from .model import StructuralError, positive

CORNERS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]])
# Outward winding, indexed by axis and min/max.
FACES = ((0, 4, 7, 3), (1, 2, 6, 5), (0, 1, 5, 4),
         (3, 7, 6, 2), (0, 3, 2, 1), (4, 5, 6, 7))
NEIGHBORS = ndimage.generate_binary_structure(3, 1)
MAX_CELLS = 4096


@dataclass(frozen=True)
class Grid:
    mask: np.ndarray
    pitch_mm: float
    origin: tuple = (0., 0., 0.)

    def __post_init__(self):
        positive(self.pitch_mm, "pitch_mm")
        m = np.asarray(self.mask)
        if m.dtype != bool or m.ndim != 3 or m.size > MAX_CELLS or m.sum() < 2:
            raise StructuralError(f"Expected a boolean 3D grid with 2..{MAX_CELLS} cells")
        if len(self.origin) != 3 or not np.isfinite(self.origin).all():
            raise StructuralError("Grid origin must have three finite coordinates")
        if not connected(m):
            raise StructuralError("Solid must have one face-connected component")
        m = m.copy()
        m.flags.writeable = False
        object.__setattr__(self, "mask", m)
        object.__setattr__(self, "origin", tuple(float(v) for v in self.origin))

    @property
    def volume_mm3(self):
        return int(self.mask.sum()) * self.pitch_mm ** 3

    def with_mask(self, mask):
        return Grid(mask, self.pitch_mm, self.origin)


def connected(mask):
    return ndimage.label(mask, NEIGHBORS)[1] == 1


def mesh(grid):
    cells = np.argwhere(grid.mask)
    points, inverse = np.unique((cells[:, None, :] + CORNERS).reshape(-1, 3), axis=0, return_inverse=True)
    return cells, points, inverse.reshape(-1, 8)


def side_elements(cells, side):
    axis, end = "xyz".index(side[0]), side.split("_")[1]
    extreme = cells[:, axis].min() if end == "min" else cells[:, axis].max()
    return np.flatnonzero(cells[:, axis] == extreme), axis * 2 + (end == "max")


def protected_mask(grid, loads, supports, skin, preserve=None):
    protected = grid.mask & ~ndimage.binary_erosion(grid.mask, NEIGHBORS, iterations=skin)
    cells = np.argwhere(grid.mask)
    for bc in (*loads, *supports):
        elements, _ = side_elements(cells, bc.side)
        protected[tuple(cells[elements].T)] = True
    if preserve is not None:
        p = np.asarray(preserve)
        if p.dtype != bool or p.shape != grid.mask.shape or np.any(p & ~grid.mask):
            raise StructuralError("Preserve mask must be boolean, match the grid, and select existing solid cells")
        protected |= p
    return protected


def surface_quads(grid):
    for cell in np.argwhere(grid.mask):
        for side, face in enumerate(FACES):
            neighbor = cell.copy()
            neighbor[side // 2] += 1 if side % 2 else -1
            if np.all(neighbor >= 0) and np.all(neighbor < grid.mask.shape) and grid.mask[tuple(neighbor)]:
                continue
            yield tuple(tuple(int(v) for v in cell + CORNERS[c]) for c in face)


def manifold(grid):
    edges = Counter()
    # Edge incidence catches diagonal void contacts; vertex-link connectivity catches pinches.
    links = {}
    for face in surface_quads(grid):
        for i, a in enumerate(face):
            b = face[(i + 1) % 4]
            edges[tuple(sorted((a, b)))] += 1
            links.setdefault(a, []).append((face[i - 1], b))
    if any(n != 2 for n in edges.values()):
        return False
    for pairs in links.values():
        graph = {}
        for a, b in pairs:
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)
        seen, todo = set(), [next(iter(graph))]
        while todo:
            p = todo.pop()
            if p not in seen:
                seen.add(p)
                todo.extend(graph[p] - seen)
        if len(seen) != len(graph) or any(len(v) != 2 for v in graph.values()):
            return False
    return True


def export_stl(grid, path):
    if not manifold(grid):
        raise StructuralError("Non-manifold surface cannot be exported")
    with open(path, "w", encoding="ascii") as out:
        out.write("solid cadswarm\n")
        for quad in surface_quads(grid):
            xyz = np.asarray(quad) * grid.pitch_mm + grid.origin
            for ids in ((0, 1, 2), (0, 2, 3)):
                tri = xyz[list(ids)]
                normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
                normal /= np.linalg.norm(normal)
                out.write("facet normal " + " ".join(map(str, normal)) + "\nouter loop\n")
                for point in tri:
                    out.write("vertex " + " ".join(map(str, point)) + "\n")
                out.write("endloop\nendfacet\n")
        out.write("endsolid cadswarm\n")
