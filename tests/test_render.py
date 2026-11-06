"""The clay renderer: PNGs a helper can open, built from triangles with only the standard library."""
import struct

from cadswarm.render import BACKGROUND, png_bytes, render_meshes, render_view

VERTS = [(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0),
         (0, 0, 100), (100, 0, 100), (100, 100, 100), (0, 100, 100)]
TRIS = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
        (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
CUBE = ("Cube", VERTS, TRIS)


def test_a_png_has_a_valid_header_and_size():
    data = png_bytes(3, 2, bytearray([255, 0, 0] * 6))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (3, 2)
    assert data[-8:] == b"IEND" + struct.pack(">I", 0xAE426082)


def test_a_cube_shows_solid_pixels_in_every_view():
    for view in ("front", "rear", "left", "right", "top", "three_quarter"):
        pixels = render_view([CUBE], view, 200, 124)
        solid = sum(1 for i in range(0, len(pixels), 3) if tuple(pixels[i:i + 3]) != BACKGROUND)
        assert solid > 400, f"{view} drew {solid} solid pixels"


def test_the_side_view_keeps_the_proportions_of_the_part():
    """A 100 mm cube on a 200 px canvas with 6 % margins is about 176 px tall."""
    width = 200
    pixels = render_view([CUBE], "left", width, 200)
    rows = [y for y in range(200)
            if any(tuple(pixels[(y * width + x) * 3:(y * width + x) * 3 + 3]) != BACKGROUND
                   for x in range(width))]
    assert rows, "nothing drawn"
    assert 160 <= rows[-1] - rows[0] + 1 <= 180


def test_renders_are_written_one_png_per_view(tmp_path):
    files = render_meshes([CUBE], ["front", "left"], size=200, out_dir=str(tmp_path / "r"))
    assert [f.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for f in files] == ["front.png", "left.png"]
    for path in files:
        with open(path, "rb") as f:
            assert f.read(8) == b"\x89PNG\r\n\x1a\n"
