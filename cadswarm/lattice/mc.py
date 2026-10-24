"""Field to watertight triangle mesh (marching tetrahedra), checks, and STL/3MF writers.

Each cube between eight neighbouring samples is split into six tetrahedra around its main diagonal (the
Freudenthal split). The split is the same in every cube, so neighbouring cubes cut their shared face the
same way and the surface has no cracks. Vertices sit on grid edges and are welded exactly by edge id, which
makes the result an indexed, closed, consistently oriented mesh when the field is closed.
"""
import io
import struct
import zipfile
from dataclasses import dataclass

import numpy as np

# Cube corners by bit index (x + 2y + 4z) and the six tetrahedra sharing the 0-7 diagonal.
_CORNER = np.array([[(c >> 0) & 1, (c >> 1) & 1, (c >> 2) & 1] for c in range(8)])
_TETS = np.array([[0, 1, 3, 7], [0, 1, 5, 7], [0, 2, 3, 7], [0, 2, 6, 7], [0, 4, 5, 7], [0, 4, 6, 7]])
CHUNK = 150_000  # active cubes per pass


@dataclass
class Mesh:
    vertices: np.ndarray   # (n, 3) float64, mm
    faces: np.ndarray      # (m, 3) int64, outward-facing

    @property
    def volume_mm3(self):
        a, b, c = (self.vertices[self.faces[:, i]] for i in range(3))
        return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6)

    @property
    def area_mm2(self):
        a, b, c = (self.vertices[self.faces[:, i]] for i in range(3))
        return float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2)

    def bbox(self):
        return self.vertices.min(axis=0).tolist() + self.vertices.max(axis=0).tolist()

    def check(self):
        """(watertight, consistently oriented): every edge used by exactly two faces, once each way."""
        if not len(self.faces):
            return False, False
        e = np.concatenate([self.faces[:, [0, 1]], self.faces[:, [1, 2]], self.faces[:, [2, 0]]])
        n = len(self.vertices)
        directed = e[:, 0] * n + e[:, 1]
        undirected = np.minimum(e[:, 0], e[:, 1]) * n + np.maximum(e[:, 0], e[:, 1])
        _, counts = np.unique(undirected, return_counts=True)
        closed = bool(np.all(counts == 2))
        oriented = closed and np.unique(directed).size == directed.size
        return closed, bool(oriented)


def contour(values, origin, pitch):
    """Surface where `values` (samples at voxel centres) crosses zero; negative is inside.

    The field is padded with a positive layer, so material touching the grid border is still closed.
    """
    v = np.pad(np.asarray(values, np.float32), 1, constant_values=np.float32(pitch))
    # A sample exactly on zero would put a vertex on a grid node and collapse triangles.
    v = np.where(np.abs(v) < 1e-6 * pitch, np.float32(1e-6 * pitch), v)
    origin = np.asarray(origin, float) + (0.5 - 1) * pitch  # sample (0,0,0) of the padded grid
    nx, ny, nz = v.shape
    neg = v < 0
    # Cubes with both signs among their corners.
    some = np.zeros((nx - 1, ny - 1, nz - 1), bool)
    allneg = np.ones_like(some)
    for c in _CORNER:
        sl = neg[c[0]:c[0] + nx - 1, c[1]:c[1] + ny - 1, c[2]:c[2] + nz - 1]
        some |= sl
        allneg &= sl
    cubes = np.argwhere(some & ~allneg)
    keys, points = [], []
    stride = np.array([ny * nz, nz, 1])
    total = nx * ny * nz
    for s in range(0, len(cubes), CHUNK):
        base = cubes[s:s + CHUNK]
        corner_idx = base[:, None, :] + _CORNER[None, :, :]                # (n, 8, 3)
        lin = corner_idx @ stride                                           # (n, 8)
        val = v.ravel()[lin]
        pos = origin + corner_idx * pitch
        for tet in _TETS:
            tv, tl, tp = val[:, tet], lin[:, tet], pos[:, tet]
            tn = tv < 0
            cnt = tn.sum(axis=1)
            for k, p in _tet_triangles(tv, tl, tp, tn, cnt):
                keys.append(k)
                points.append(p)
    if not keys:
        return Mesh(np.zeros((0, 3)), np.zeros((0, 3), np.int64))
    keys = np.concatenate(keys)                 # (m, 3) edge keys
    points = np.concatenate(points)             # (m, 3, 3)
    uniq, first, inv = np.unique(keys.ravel(), return_index=True, return_inverse=True)
    vertices = points.reshape(-1, 3)[first]
    faces = inv.reshape(-1, 3).astype(np.int64)
    del uniq, total
    # Drop triangles whose welded corners coincide (can happen only in degenerate tets).
    ok = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    return Mesh(vertices, faces[ok])


def _edge(tv, tl, tp, a, b):
    """Zero crossing on edge (a, b) of each tet: key and point."""
    rows = np.arange(len(tv))
    va, vb = tv[rows, a], tv[rows, b]
    la, lb = tl[rows, a], tl[rows, b]
    t = (va / (va - vb))[:, None]
    p = tp[rows, a] + t * (tp[rows, b] - tp[rows, a])
    lo, hi = np.minimum(la, lb).astype(np.int64), np.maximum(la, lb).astype(np.int64)
    return lo * (2 ** 31) + hi, p


def _orient(k, p, inside):
    """Flip triangles whose normal points towards the inside."""
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    flip = np.einsum("ij,ij->i", n, p.mean(axis=1) - inside) < 0
    k[flip] = k[flip][:, [0, 2, 1]]
    p[flip] = p[flip][:, [0, 2, 1]]
    return k, p


def _tet_triangles(tv, tl, tp, tn, cnt):
    out = []
    # One vertex differs from the other three: one triangle around it.
    for c, iso_neg in ((1, True), (3, False)):
        sel = np.flatnonzero(cnt == c)
        if not sel.size:
            continue
        sv, sl, sp, sn = tv[sel], tl[sel], tp[sel], tn[sel]
        iso = np.argmax(sn if iso_neg else ~sn, axis=1)
        others = np.array([[j for j in range(4) if j != i] for i in range(4)])[iso]
        ks, ps = zip(*(_edge(sv, sl, sp, iso, others[:, j]) for j in range(3)))
        k, p = np.stack(ks, axis=1), np.stack(ps, axis=1)
        inside = _inside_point(sp, sn)
        out.append(_orient(k, p, inside))
    # Two inside, two outside: a quad, as two triangles.
    sel = np.flatnonzero(cnt == 2)
    if sel.size:
        sv, sl, sp, sn = tv[sel], tl[sel], tp[sel], tn[sel]
        order = np.argsort(~sn, axis=1, kind="stable")  # inside vertices first
        a, b, c, d = (order[:, i] for i in range(4))
        (kac, pac), (kad, pad), (kbd, pbd), (kbc, pbc) = (_edge(sv, sl, sp, x, y)
                                                          for x, y in ((a, c), (a, d), (b, d), (b, c)))
        inside = _inside_point(sp, sn)
        for k, p in (((kac, kad, kbd), (pac, pad, pbd)), ((kac, kbd, kbc), (pac, pbd, pbc))):
            out.append(_orient(np.stack(k, axis=1), np.stack(p, axis=1), inside))
    return out


def _inside_point(tp, tn):
    w = tn.astype(float)[..., None]
    return (tp * w).sum(axis=1) / w.sum(axis=1)


# ----------------------------------------------------------------------------- writers

def write_stl(mesh, path):
    """Binary STL, millimetres."""
    tri = mesh.vertices[mesh.faces].astype(np.float32)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-20)
    rec = np.zeros(len(tri), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    rec["n"], rec["v"] = n, tri
    with open(path, "wb") as f:
        f.write(b"CadSwarm lattice (mm)".ljust(80, b" "))
        f.write(struct.pack("<I", len(tri)))
        rec.tofile(f)


def read_stl(path):
    """Binary STL to Mesh (vertices welded by exact coordinates)."""
    with open(path, "rb") as f:
        f.seek(80)
        (n,) = struct.unpack("<I", f.read(4))
        rec = np.fromfile(f, dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")], count=n)
    pts = rec["v"].reshape(-1, 3).astype(np.float64)
    uniq, inv = np.unique(pts, axis=0, return_inverse=True)
    return Mesh(uniq, inv.reshape(-1, 3).astype(np.int64))


_CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/'
                  'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.'
                  'openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="'
                  'application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/'
         'package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://'
         'schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')


def write_3mf(mesh, path, name="lattice"):
    """Minimal 3MF core: one object, millimetres, no extensions."""
    buf = io.StringIO()
    buf.write('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
              'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
              f'<metadata name="Title">{_xml(name)}</metadata><metadata name="Application">CadSwarm</metadata>'
              '<resources><object id="1" type="model"><mesh><vertices>')
    for s in range(0, len(mesh.vertices), 200_000):
        np.savetxt(buf, mesh.vertices[s:s + 200_000], fmt='<vertex x="%.4f" y="%.4f" z="%.4f"/>',
                   newline="")
    buf.write("</vertices><triangles>")
    for s in range(0, len(mesh.faces), 200_000):
        np.savetxt(buf, mesh.faces[s:s + 200_000], fmt='<triangle v1="%d" v2="%d" v3="%d"/>', newline="")
    buf.write('</triangles></mesh></object></resources><build><item objectid="1"/></build></model>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", buf.getvalue())


def _xml(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
