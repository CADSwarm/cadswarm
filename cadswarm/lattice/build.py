"""Recipe, warnings, and the composition of skin, keep zones and graded lattice into one field.

Composition (signed fields, negative = material):

    part      P   approximate signed distance of the voxelized part
    skin      S = max(P, -P - skin)        a shell `skin` mm deep, removed near open faces
    lattice   L = cell field with thickness t(x)
    keep      K = max(P, d_keep - r_keep)  solid around supports, loads and preserve boxes
    result    F = min(S, max(P, L), K)
"""
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy import ndimage

from . import CELLS, calibration, cells
from .voxelize import signed_distance

FACES = ("+x", "-x", "+y", "-y", "+z", "-z")
MAX_BUILD_VOXELS = 24_000_000
MAX_ANALYSIS_ELEMENTS = 300_000


class RecipeError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass
class Recipe:
    cell: str = "gyroid"
    cell_size_mm: float = 8.0
    min_thickness_mm: float = 0.8      # thinnest printable wall/strut
    max_thickness_mm: float = 0.0      # 0: 0.35 of the cell
    skin_mm: float = 1.5
    open_faces: list = field(default_factory=list)
    voxel_mm: float = 0.0              # build voxel; 0: chosen from the cell and wall
    analysis_voxel_mm: float = 0.0     # 0: chosen to fit MAX_ANALYSIS_ELEMENTS
    grading: str = "stress"            # "stress" (needs a load case) or "uniform"
    thickness_mm: float = 0.0          # uniform mode: the wall; 0: from target density
    stiffness_target: float = 0.9      # verified stiffness / solid stiffness
    min_safety_factor: float = 1.5
    auto_skin: bool = True             # thicken the skin when the core alone cannot reach the target
    keep_radius_mm: float = 0.0        # solid around supports and loads; 0: max(2*skin, cell/2)
    preserve: list = field(default_factory=list)  # [[[x0,y0,z0],[x1,y1,z1]], ...] world mm, kept solid

    def as_dict(self):
        return asdict(self)


def _num(v, name, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise RecipeError("bad_args", f"{name} must be a number")
    if not np.isfinite(x) or not lo <= x <= hi:
        raise RecipeError("bad_args", f"{name} must be between {lo:g} and {hi:g}")
    return x


def normalize(args):
    """Recipe from loose arguments (agent or dialog); raises RecipeError on contradictions."""
    a = dict(args or {})
    r = Recipe()
    cell = a.get("cell", r.cell)
    if cell not in CELLS:
        raise RecipeError("bad_cell", f"cell must be one of {', '.join(CELLS)}")
    r.cell = cell
    r.cell_size_mm = _num(a.get("cell_size_mm", r.cell_size_mm), "cell_size_mm", 0.5, 50)
    r.min_thickness_mm = _num(a.get("min_thickness_mm", r.min_thickness_mm), "min_thickness_mm", 0.05, 10)
    r.max_thickness_mm = _num(a.get("max_thickness_mm") or max(0.35 * r.cell_size_mm, r.min_thickness_mm),
                              "max_thickness_mm", 0.05, 20)
    if r.max_thickness_mm < r.min_thickness_mm:
        raise RecipeError("bad_args", "max_thickness_mm must be at least min_thickness_mm")
    r.skin_mm = _num(a.get("skin_mm", r.skin_mm), "skin_mm", 0, 20)
    faces = a.get("open_faces") or []
    if isinstance(faces, str):
        faces = [faces]
    if any(f not in FACES for f in faces) or len(set(faces)) != len(faces):
        raise RecipeError("bad_args", f"open_faces must be distinct values from {', '.join(FACES)}")
    if faces and r.skin_mm == 0:
        raise RecipeError("open_face_without_skin", "open_faces only make sense with a skin (skin_mm > 0)")
    r.open_faces = list(faces)
    r.grading = a.get("grading") or "stress"
    if r.grading not in ("stress", "uniform"):
        raise RecipeError("bad_args", "grading must be stress or uniform")
    r.thickness_mm = _num(a.get("thickness_mm") or 0, "thickness_mm", 0, 20)
    r.stiffness_target = _num(a.get("stiffness_target", r.stiffness_target), "stiffness_target", 0.05, 1.0)
    r.min_safety_factor = _num(a.get("min_safety_factor", r.min_safety_factor), "min_safety_factor", 0, 20)
    r.auto_skin = bool(a.get("auto_skin", True))
    r.keep_radius_mm = _num(a.get("keep_radius_mm") or max(2 * r.skin_mm, r.cell_size_mm / 2),
                            "keep_radius_mm", 0, 100)
    pre = a.get("preserve") or []
    try:
        r.preserve = [[[float(c) for c in b[0]], [float(c) for c in b[1]]] for b in pre]
    except (TypeError, ValueError, IndexError):
        raise RecipeError("bad_args", "preserve must be a list of [[x0,y0,z0],[x1,y1,z1]] boxes in mm")
    v = a.get("voxel_mm") or 0
    r.voxel_mm = _num(v or min(r.cell_size_mm / 10, r.min_thickness_mm / 2.5), "voxel_mm", 0.05, 5)
    r.analysis_voxel_mm = _num(a.get("analysis_voxel_mm") or 0, "analysis_voxel_mm", 0, 20)
    return r


def warnings(r, bbox_size):
    """[(code, severity, text)] for a recipe on a part of this size (mm)."""
    out = []
    w = lambda code, text, sev="warning": out.append({"code": code, "severity": sev, "text": text})
    if r.cell_size_mm / r.voxel_mm < 6:
        w("cell_too_few_voxels", f"The cell is only {r.cell_size_mm / r.voxel_mm:.1f} voxels across: its shape "
          "will be coarse. Use a smaller voxel or a larger cell.")
    if r.min_thickness_mm / r.voxel_mm < 2:
        w("thickness_too_few_voxels", f"The thinnest wall ({r.min_thickness_mm:g} mm) is under two voxels "
          f"({r.voxel_mm:g} mm): it may break up. Use a smaller voxel.")
    if 0 < r.skin_mm < 2 * r.voxel_mm:
        w("skin_too_thin", f"A {r.skin_mm:g} mm skin is under two voxels: it may come out porous.")
    if r.skin_mm > 0 and not r.open_faces:
        w("closed_skin", "A closed skin traps powder or resin inside the lattice: open at least two "
          "opposite faces if the part is printed in powder or resin.")
    if r.max_thickness_mm / r.cell_size_mm > 0.35:
        w("thickness_high", "Walls thicker than about a third of the cell make the lattice nearly solid.")
    if r.min_thickness_mm / r.cell_size_mm < 0.03:
        w("thickness_low", "Walls thinner than 3 % of the cell are fragile and hard to print.")
    if r.cell == "schwarzP":
        rho_hi = float(calibration.density("schwarzP", r.max_thickness_mm / r.cell_size_mm))
        if rho_hi > 0.55:
            w("channels_close", "At its thickest, the Schwarz P lattice closes one of its channel systems "
              "(above about 55 % density): powder or resin cannot drain there.")
    shortest = min(bbox_size)
    if shortest < 2 * r.skin_mm + r.cell_size_mm:
        w("thin_part", f"The part is only {shortest:.1f} mm thick at its thinnest axis: after a "
          f"{r.skin_mm:g} mm skin there is little room for a {r.cell_size_mm:g} mm cell. Use a smaller cell or "
          "a thinner skin.")
    vox = np.prod([s / r.voxel_mm for s in bbox_size])
    if vox > MAX_BUILD_VOXELS:
        w("large_part", f"About {vox / 1e6:.0f} M build voxels: over the {MAX_BUILD_VOXELS / 1e6:.0f} M limit, "
          "so the voxel will be enlarged.", "info")
    return out


# --------------------------------------------------------------------------- geometry

@dataclass
class Geometry:
    grid: object            # VoxelGrid
    part: np.ndarray        # bool
    sdf: np.ndarray         # float32, mm
    keep: np.ndarray        # bool seeds: voxels on support/load faces and in preserve boxes
    bbox: tuple             # part bbox (xmin, ymin, zmin, xmax, ymax, zmax)

    @classmethod
    def make(cls, grid, part, keep, bbox):
        return cls(grid, part, signed_distance(part, grid.pitch), keep & part, tuple(bbox))


def preserve_mask(grid, boxes):
    m = np.zeros(grid.shape, bool)
    for lo, hi in boxes:
        sl = []
        for a in range(3):
            c = grid.centres(a)
            sl.append(slice(int(np.searchsorted(c, min(lo[a], hi[a]))), int(np.searchsorted(c, max(lo[a], hi[a]),
                                                                                           side="right"))))
        m[tuple(sl)] = True
    return m


def regions(geom, recipe):
    """(solid, lattice) bool masks: skin and keep zones are solid, the rest of the part becomes lattice."""
    h = geom.grid.pitch
    depth = -geom.sdf
    solid = geom.part & (depth <= recipe.skin_mm) if recipe.skin_mm > 0 else np.zeros_like(geom.part)
    solid &= ~_open_face_band(geom, recipe)
    keep = geom.keep
    if keep.any() and recipe.keep_radius_mm > 0:
        d = ndimage.distance_transform_edt(~keep) * h
        solid |= geom.part & (d <= recipe.keep_radius_mm)
    else:
        solid |= keep
    lattice = geom.part & ~solid
    return solid, lattice


def _open_face_band(geom, recipe):
    """Voxels within one skin of an open bounding-box face (where the skin is left off)."""
    band = np.zeros(geom.part.shape, bool)
    if not recipe.open_faces:
        return band
    width = recipe.skin_mm + geom.grid.pitch
    for f in recipe.open_faces:
        a = "xyz".index(f[1])
        c = geom.grid.centres(a)
        edge = geom.bbox[a + 3] if f[0] == "+" else geom.bbox[a]
        near = np.abs(c - edge) <= width
        sl = [slice(None)] * 3
        sl[a] = near
        band[tuple(sl)] = True
    return band


def compose(geom, recipe, thickness, chunk=16):
    """Final field F on geom.grid (float32, negative = material). `thickness`: scalar or array (mm)."""
    g = geom.grid
    P = geom.sdf
    F = np.empty(g.shape, np.float32)
    if recipe.skin_mm > 0:
        S = np.maximum(P, -P - recipe.skin_mm)
        S[_open_face_band(geom, recipe)] = np.inf
    else:
        S = np.full(g.shape, np.inf, np.float32)
    if geom.keep.any() and recipe.keep_radius_mm > 0:
        K = np.maximum(P, ndimage.distance_transform_edt(~geom.keep) * g.pitch - recipe.keep_radius_mm)
    else:
        K = np.where(geom.keep, P, np.inf)
    thick = np.broadcast_to(np.asarray(thickness, np.float32), g.shape)
    xs, ys = g.centres(0), g.centres(1)
    for s in range(0, g.shape[2], chunk):
        zs = g.centres(2)[s:s + chunk]
        pts = np.stack(np.meshgrid(xs, ys, zs, indexing="ij"), axis=-1)
        L = cells.field(recipe.cell, pts, recipe.cell_size_mm, thick[:, :, s:s + chunk]).astype(np.float32)
        sl = np.s_[:, :, s:s + chunk]
        F[sl] = np.minimum(np.minimum(S[sl], np.maximum(P[sl], L)), K[sl])
    return F
