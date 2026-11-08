"""CADSwarm's app icon: the SVG sources in design/icon and the files built from them.

  design/icon/cadswarm.svg        the mark (256 px artboard)
  design/icon/cadswarm-small.svg  pixel-aligned variant used for 16-32 px
  design/icon/cadswarm.ico        Windows: 16, 24, 32, 48, 64, 128, 256 px (window, taskbar, shortcuts)
  design/icon/cadswarm-256.png    everything else (Linux .desktop, macOS, the About box); also -16 ... -64

Rebuild after editing an SVG (needs Qt; FreeCAD's Python has it):
    "<FreeCAD>/bin/python.exe" -m cadswarm.app.app_icon

``ico_from_pngs`` is pure (no Qt) and unit-tested.
"""
import os
import struct

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ICON_DIR = os.path.join(REPO, "design", "icon")
SVG = os.path.join(ICON_DIR, "cadswarm.svg")
SVG_SMALL = os.path.join(ICON_DIR, "cadswarm-small.svg")
ICO = os.path.join(ICON_DIR, "cadswarm.ico")
PNG = os.path.join(ICON_DIR, "cadswarm-256.png")
PNG_SIZES = (16, 24, 32, 48, 64, 256)  # loose PNGs for Qt's QIcon (FreeCAD's Qt has no .ico/.svg image reader)

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
SMALL_MAX = 32  # sizes up to this use the pixel-aligned small SVG


def png_path(size):
    return os.path.join(ICON_DIR, f"cadswarm-{size}.png")


def ico_from_pngs(pngs):
    """A Windows .ico holding PNG images. ``pngs`` maps size (px, 1-256) to PNG bytes."""
    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for size in sizes:
        data = pngs[size]
        if not data.startswith(b"\x89PNG"):
            raise ValueError(f"{size}px image is not a PNG")
        dim = 0 if size >= 256 else size  # 0 means 256 in an ICO directory entry
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    return header + entries + blobs


def icon_files():
    """(size, path) of the built PNGs that exist, smallest first: what a QIcon is made of."""
    return [(s, png_path(s)) for s in PNG_SIZES if os.path.exists(png_path(s))]


def render_png(svg_path, size):
    """PNG bytes of an SVG at size x size (Qt)."""
    from PySide6 import QtCore, QtGui, QtSvg
    img = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(img)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    QtSvg.QSvgRenderer(svg_path).render(painter)
    painter.end()
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def build():
    from PySide6 import QtGui
    _app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])  # noqa: F841
    pngs = {s: render_png(SVG_SMALL if s <= SMALL_MAX else SVG, s) for s in ICO_SIZES}
    with open(ICO, "wb") as f:
        f.write(ico_from_pngs(pngs))
    for size in PNG_SIZES:
        with open(png_path(size), "wb") as f:
            f.write(pngs[size])
    return ICO, PNG


if __name__ == "__main__":
    print(*build())
