"""Clay renders of a model as PNG, with only the standard library.

An agent that cannot see its own work builds slabs: it can satisfy every number and still make a car
that reads as a box. This turns tessellated triangles into orthographic views it can open, so a
reviewer can compare a part with a reference photo and a person can judge the model at a glance.

Orthographic by default: a side view at a known scale can be laid over a blueprint. No text, no
dependencies: a z-buffer, flat Lambert shading, a palette per object and a 1 m scale bar.
"""
import math
import os
import struct
import time
import zlib

BACKGROUND = (244, 244, 246)
BAR_COLOR = (70, 70, 74)
MAX_TRIANGLES = 1_000_000
DEFAULT_VIEWS = ("front", "rear", "left", "right", "top", "three_quarter")
# Muted tints so separate parts are distinguishable without hiding the form.
PALETTE = [(178, 188, 202), (200, 182, 168), (170, 196, 184), (206, 190, 206),
           (188, 196, 166), (204, 176, 176), (168, 184, 206), (196, 200, 176)]
LIGHT_A = (-0.45, -0.55, 0.70)
LIGHT_B = (0.55, 0.25, 0.30)


def _unit(v):
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / length for c in v)


LA = _unit(LIGHT_A)
LB = _unit(LIGHT_B)


def _rotate(p, yaw, pitch):
    """A three-quarter view: yaw about Z, then pitch about the screen's horizontal axis."""
    x, y, z = p
    cy, sy = math.cos(yaw), math.sin(yaw)
    x, y = x * cy - y * sy, x * sy + y * cy
    cp, sp = math.cos(pitch), math.sin(pitch)
    y, z = y * cp - z * sp, y * sp + z * cp
    return (x, y, z)


# u index/sign, v index/sign, camera direction (depth grows away from the camera), pre-rotation.
VIEWS = {
    "front": ((1, 1), (2, 1), (1.0, 0.0, 0.0), (0.0, 0.0)),
    "rear": ((1, -1), (2, 1), (-1.0, 0.0, 0.0), (0.0, 0.0)),
    "left": ((0, 1), (2, 1), (0.0, 1.0, 0.0), (0.0, 0.0)),
    "right": ((0, -1), (2, 1), (0.0, -1.0, 0.0), (0.0, 0.0)),
    "top": ((0, 1), (1, 1), (0.0, 0.0, -1.0), (0.0, 0.0)),
    "three_quarter": ((1, 1), (2, 1), (1.0, 0.0, 0.0), (math.radians(35), math.radians(18))),
}


def png_bytes(width, height, rgb):
    """A truecolour PNG (8-bit RGB) from a row-major bytearray."""
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter: none
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def _shade(normal, base):
    # Two-sided: an open shell or a back face still shows the same material, just less lit.
    nx, ny, nz = normal
    a = max(0.0, nx * LA[0] + ny * LA[1] + nz * LA[2])
    b = max(0.0, nx * LB[0] + ny * LB[1] + nz * LB[2])
    lam = min(1.0, 0.30 + 0.62 * a + 0.18 * b)
    return (int(base[0] * lam), int(base[1] * lam), int(base[2] * lam))


def _triangles(meshes, yaw, pitch):
    """(projected-ready triangles) in rotated space: [(p0, p1, p2, colour)]."""
    out = []
    for index, (_name, verts, tris) in enumerate(meshes):
        base = PALETTE[index % len(PALETTE)]
        rotated = [_rotate(v, yaw, pitch) for v in verts]
        for tri in tris:
            try:
                a, b, c = rotated[tri[0]], rotated[tri[1]], rotated[tri[2]]
            except (IndexError, TypeError):
                continue
            out.append((a, b, c, base))
    return out


def render_view(meshes, view, width, height):
    """One view as a bytearray of RGB pixels."""
    (ui, us), (vi, vs), direction, (yaw, pitch) = VIEWS[view]
    tris = _triangles(meshes, yaw, pitch)
    if not tris:
        raise ValueError("Nothing to render: no triangles")
    if len(tris) > MAX_TRIANGLES:
        raise ValueError(f"{len(tris)} triangles is too many for a render ({MAX_TRIANGLES} max); "
                         f"use a coarser deviation")
    us_vals = [p[ui] * us for tri in tris for p in tri[:3]]
    vs_vals = [p[vi] * vs for tri in tris for p in tri[:3]]
    u0, u1, v0, v1 = min(us_vals), max(us_vals), min(vs_vals), max(vs_vals)
    margin = 0.06
    span_u, span_v = max(u1 - u0, 1e-6), max(v1 - v0, 1e-6)
    scale = min(width * (1 - 2 * margin) / span_u, height * (1 - 2 * margin) / span_v)
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2

    def to_screen(p):
        u = p[ui] * us
        v = p[vi] * vs
        return ((u - cu) * scale + width / 2, height / 2 - (v - cv) * scale)

    pixels = bytearray(bytes(BACKGROUND) * (width * height))
    depth = [float("inf")] * (width * height)
    for a, b, c, base in tris:
        ax, ay = to_screen(a)
        bx, by = to_screen(b)
        cx, cy = to_screen(c)
        dax, day = bx - ax, by - ay
        dbx, dby = cx - ax, cy - ay
        area = dax * dby - day * dbx
        if abs(area) < 1e-9:
            continue
        # Face normal in the rotated frame, for shading; point it at the camera.
        nx = (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1])
        ny = (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2])
        nz = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        nlength = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nx, ny, nz = nx / nlength, ny / nlength, nz / nlength
        facing = nx * direction[0] + ny * direction[1] + nz * direction[2]
        if facing > 0:
            nx, ny, nz = -nx, -ny, -nz
        colour = _shade((nx, ny, nz), base)
        x0 = max(0, int(math.floor(min(ax, bx, cx))))
        x1 = min(width - 1, int(math.ceil(max(ax, bx, cx))))
        y0 = max(0, int(math.floor(min(ay, by, cy))))
        y1 = min(height - 1, int(math.ceil(max(ay, by, cy))))
        if x1 < x0 or y1 < y0 or (x1 - x0) < 1 or (y1 - y0) < 1:
            continue  # sub-pixel detail (grille cells, thread flanks): invisible at this size
        za = a[0] * direction[0] + a[1] * direction[1] + a[2] * direction[2]
        zb = b[0] * direction[0] + b[1] * direction[1] + b[2] * direction[2]
        zc = c[0] * direction[0] + c[1] * direction[1] + c[2] * direction[2]
        inv = 1.0 / area
        for py in range(y0, y1 + 1):
            fy = py + 0.5
            row = py * width
            for px in range(x0, x1 + 1):
                fx = px + 0.5
                w0 = ((bx - fx) * (cy - fy) - (by - fy) * (cx - fx)) * inv
                w1 = ((cx - fx) * (ay - fy) - (cy - fy) * (ax - fx)) * inv
                w2 = 1.0 - w0 - w1
                if w0 < -1e-6 or w1 < -1e-6 or w2 < -1e-6:
                    continue
                z = w0 * za + w1 * zb + w2 * zc
                i = row + px
                if z < depth[i]:
                    depth[i] = z
                    j = i * 3
                    pixels[j] = colour[0]
                    pixels[j + 1] = colour[1]
                    pixels[j + 2] = colour[2]
    _scale_bar(pixels, width, height, scale)
    return pixels


def _scale_bar(pixels, width, height, scale):
    """A 1 m bar, so a side view can be read at a known scale."""
    length = int(round(scale * 1000.0))
    if length < 8 or length > width * 0.6:
        return
    x, y = 24, height - 24
    for dy in range(4):
        for dx in range(length):
            i = ((y + dy) * width + x + dx) * 3
            pixels[i] = BAR_COLOR[0]
            pixels[i + 1] = BAR_COLOR[1]
            pixels[i + 2] = BAR_COLOR[2]


def render_meshes(meshes, views=None, size=900, out_dir="."):
    """Write one PNG per view into `out_dir`; returns [paths]. `meshes` is [(name, verts, triangles)]."""
    os.makedirs(out_dir, exist_ok=True)
    width = max(200, int(size))
    height = max(150, int(size * 0.62))
    written = []
    for view in (views or DEFAULT_VIEWS):
        if view not in VIEWS:
            raise ValueError(f"view must be one of {sorted(VIEWS)}")
        pixels = render_view(meshes, view, width, height)
        path = os.path.join(out_dir, f"{view}.png")
        with open(path, "wb") as f:
            f.write(png_bytes(width, height, pixels))
        written.append(path)
    return written
