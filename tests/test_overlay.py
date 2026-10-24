"""The pointer overlay's pure helpers (no FreeCAD, no Coin, no network)."""
import math

from cadswarm.gui import overlay


def pay(pointers, members, objects):
    return {"pointers": pointers, "members": members, "objects": objects}


def pointer(member, obj="Base", mode="editing", label="", point=None, seq=1):
    return {"member": member, "object": obj, "point": point or [], "mode": mode, "label": label,
            "op": "", "seq": seq, "at_ms": 1}


MEMBER = {"id": 2, "name": "worker2", "kind": "agent", "status": "active", "status_text": "building Standoff1"}
BASE = {"name": "Base", "label": "Base", "bbox": [0, 0, 0, 40, 20, 10], "visible": True}


def shoelace(points):
    return sum(points[i][0] * points[(i + 1) % len(points)][1]
               - points[(i + 1) % len(points)][0] * points[i][1] for i in range(len(points)))


def test_bbox_anchor_prefers_the_exact_point_then_the_top_centre():
    assert overlay.bbox_anchor([0, 0, 0, 40, 20, 10]) == (20.0, 10.0, 10.0)
    assert overlay.bbox_anchor([0, 0, 0, 40, 20, 10], [1, 2, 3]) == (1.0, 2.0, 3.0)
    assert overlay.bbox_anchor([], [1, 2, 3]) == (1.0, 2.0, 3.0)
    assert overlay.bbox_anchor(None) is None
    assert overlay.bbox_anchor([0, 0, 0]) is None
    assert overlay.bbox_anchor([0, 0, 0, 40, 20, 10], [1, 2]) == (20.0, 10.0, 10.0)


def test_arrow_dims_scale_with_the_part_and_stay_sane():
    small = overlay.arrow_dims([0, 0, 0, 2, 2, 2])
    big = overlay.arrow_dims([0, 0, 0, 500, 500, 500])
    assert small["size"] == 4.0          # tiny parts still get a visible separation
    assert big["size"] == 45.0           # huge assemblies do not get a giant one
    assert big["size"] > small["size"]
    assert overlay.arrow_dims(None)["size"] == 8.0


def convex_hull(points):
    pts = sorted(set(points))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def test_the_cursor_points_at_the_tip_and_is_drawn_at_the_asked_size():
    size = overlay.CURSOR_PX
    points = overlay.cursor_points()
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    height = max(ys) - min(ys)
    width = max(xs) - min(xs)
    assert min(math.hypot(x, y) for x, y in points) == 0.0   # the tip touches the spot
    assert -0.02 * size <= max(ys) <= 0.02 * size   # nothing of it reaches above the tip
    assert -1.10 * size <= min(ys) <= -0.75 * size  # and the body hangs below it
    # it leans like a pointer: the body swings to the right of the tip
    assert min(xs) < 0.0 < max(xs)
    assert max(xs) > 2.0 * abs(min(xs))
    # the dart is as long as asked, measured along its own leaned axis
    reach = max(math.hypot(x, y) for x, y in points)
    assert 1.0 * size <= reach <= 1.12 * size
    assert 0.70 * height < width < 0.95 * height
    tall = overlay.cursor_points(size=3.0 * size, tip=(5.0, 7.0))
    assert min(math.hypot(p[0] - 5.0, p[1] - 7.0) for p in tall) == 0.0
    assert round(max(math.hypot(p[0] - 5.0, p[1] - 7.0) for p in tall), 6) == round(3.0 * reach, 6)


def test_the_marker_is_a_rounded_triangle_with_a_deep_notch():
    points = overlay.cursor_points()
    assert len(points) >= 45                    # arc-sampled, not a seven-point polygon
    assert shoelace(points) > 0.0               # counter-clockwise, so the winding is stable
    # every join is tangent-continuous: no hard corner anywhere on the silhouette
    turns = []
    for i in range(len(points)):
        a, b, c = points[i - 1], points[i], points[(i + 1) % len(points)]
        d1 = (b[0] - a[0], b[1] - a[1])
        d2 = (c[0] - b[0], c[1] - b[1])
        turns.append(abs(math.atan2(d1[0] * d2[1] - d1[1] * d2[0],
                                    d1[0] * d2[0] + d1[1] * d2[1])))
    assert max(turns) < math.radians(35.0)
    # a reflex corner proves the notch is scooped into the body, not just a bulge
    crosses = []
    for i in range(len(points)):
        a, b, c = points[i - 1], points[i], points[(i + 1) % len(points)]
        crosses.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
    assert min(crosses) < 0.0
    # and the scoop is big: the arrow fills well under its own convex hull
    filled = abs(shoelace(points)) / 2.0
    hull = abs(shoelace(convex_hull(points))) / 2.0
    assert 0.60 < filled / hull < 0.85


def _inside(poly, x, y):
    hit = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def _edge_distance(poly, x, y):
    best = float("inf")
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        dx, dy = x1 - x0, y1 - y0
        t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / ((dx * dx + dy * dy) or 1.0)))
        best = min(best, math.hypot(x - x0 - t * dx, y - y0 - t * dy))
    return best


def _into(hull):
    """How far inside a convex hull a point is (negative: outside)."""
    def into(x, y):
        return min(((q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0])) / math.dist(p, q)
                   for p, q in zip(hull, hull[1:] + hull[:1]))
    return into


def test_the_notch_is_deep_on_the_vector():
    points = overlay.cursor_points()
    into = _into(convex_hull(points))
    height = max(p[1] for p in points) - min(p[1] for p in points)
    depth = max(into(x, y) for x, y in points)
    assert depth >= 0.22 * height                       # ~4.4 design px at 20 px
    # still open half-way down: wider than the outline on both sides plus a pixel
    half = [p for p in points if into(*p) >= depth / 2.0]
    assert max(math.dist(a, b) for a in half for b in half) >= overlay.OUTLINE_PX + 1.0


def test_the_notch_and_the_point_survive_the_raster():
    # As Coin draws it without multisampling: one sample per pixel centre, the
    # outline painted over the fill.  20 px at dpr 1 and 30 px at dpr 1.5.
    for size, line in ((overlay.CURSOR_PX, overlay.OUTLINE_PX),
                       (1.5 * overlay.CURSOR_PX, 1.5 * overlay.OUTLINE_PX)):
        points = overlay.cursor_points(size)
        into = _into(convex_hull(points))
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        holes, colour = [], []
        for py in range(math.floor(min(ys)), math.ceil(max(ys))):
            for px in range(math.floor(min(xs)), math.ceil(max(xs))):
                x, y = px + 0.5, py + 0.5
                if _edge_distance(points, x, y) <= line / 2.0:
                    continue
                if _inside(points, x, y):
                    colour.append(y)
                elif into(x, y) > 0.0:
                    holes.append(into(x, y))
        # background still shows through the scoop, several pixels deep
        assert len(holes) >= 0.035 * size * size, size
        assert max(holes) >= 0.135 * size, size
        # and the bottom point keeps its colour instead of becoming a dark stem
        assert min(colour) - min(ys) <= 0.18 * size, size


class _Field:
    def __init__(self):
        self.value, self.values, self.count = None, {}, 0

    def setValue(self, *value):
        self.value = value if len(value) > 1 else value[0]

    def getValue(self):
        return self.value

    def setNum(self, count):
        self.count = count

    def getNum(self):
        return self.count

    def set1Value(self, i, *value):
        self.values[i] = value if len(value) > 1 else value[0]


class _Node:
    def __init__(self, kind):
        self.kind, self.children = kind, []

    def addChild(self, child):
        self.children.append(child)

    def __getattr__(self, name):
        field = _Field()
        setattr(self, name, field)
        return field


class _FakeCoin:
    """Just enough of pivy.coin to build an arrow and read back what it drew."""

    def __getattr__(self, name):
        def make():
            return _Node(name)
        make.BLEND, make.BASE_COLOR, make.UNKNOWN_FACE_TYPE = "BLEND", "BASE_COLOR", "UNKNOWN_FACE_TYPE"
        return make


def _find(node, kind):
    found = [node] if node.kind == kind else []
    for child in node.children:
        found += _find(child, kind)
    return found


def test_fill_and_outline_are_one_loop_and_the_fill_may_be_concave():
    arrow = overlay._Arrow(_FakeCoin(), 2, overlay.PALETTE[1])
    fill_coords, edge_coords = _find(arrow.root, "SoCoordinate3")
    points = overlay.cursor_points(overlay.CURSOR_PX)
    n = len(points)
    fill = [fill_coords.point.values[i] for i in range(fill_coords.point.getNum())]
    edge = [edge_coords.point.values[i] for i in range(edge_coords.point.getNum())]
    assert fill == [(x, y, 0.0) for x, y in points]
    assert edge == fill + fill[:1]                      # the same loop, closed once
    faces = _find(arrow.root, "SoIndexedFaceSet")[0]
    assert [faces.coordIndex.values[i] for i in range(n + 1)] == list(range(n)) + [-1]
    assert _find(arrow.root, "SoLineSet")[0].numVertices.getValue() == n + 1
    hints = _find(arrow.root, "SoShapeHints")
    assert hints and hints[0].faceType.getValue() == "UNKNOWN_FACE_TYPE"
    # the fill and the outline each hold their own material: member colour, deeper edge
    mats = [m for m, _ in arrow.mats]
    assert mats[0].diffuseColor.getValue() == tuple(overlay.PALETTE[1])
    assert mats[1].diffuseColor.getValue() == overlay.edge_color(overlay.PALETTE[1])
    # per frame: one logical pixel per design unit, and a device-pixel line width
    arrow.set_target({"anchor": (1.0, 2.0, 3.0)})
    arrow.frame(0.04, 0.0, True, cam=None, viewport_h=800, dpr=1.5)
    assert arrow.pos_node.translation.getValue() == (1.0, 2.0, 3.0)
    assert arrow.scale.scaleFactor.getValue() == (1.5, 1.5, 1.5)
    assert arrow.style.lineWidth.getValue() == overlay.OUTLINE_PX * 1.5


def test_a_bigger_arrow_scales_every_part_in_proportion():
    small = overlay.cursor_points(size=20.0)
    big = overlay.cursor_points(size=60.0)
    assert len(small) == len(big)

    def extent(points):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return max(xs) - min(xs), max(ys) - min(ys)

    small_width, small_height = extent(small)
    big_width, big_height = extent(big)
    assert round(big_width / small_width, 3) == 3.0
    assert round(big_height / small_height, 3) == 3.0
    # every corner radius scales too, so the area grows by nine, not by a little more
    assert round(abs(shoelace(big)) / abs(shoelace(small)), 1) == 9.0


def test_every_point_has_a_mirror_twin_across_the_leaned_axis():
    points = overlay.cursor_points()
    angle = math.radians(overlay.CURSOR_TILT_DEG)
    axis = (math.sin(angle), -math.cos(angle))      # the dart's own axis, leaned with it
    assert round(math.hypot(*axis), 12) == 1.0
    worst = 0.0
    for x, y in points:
        along = x * axis[0] + y * axis[1]
        rx, ry = 2.0 * along * axis[0] - x, 2.0 * along * axis[1] - y
        worst = max(worst, min(math.hypot(rx - a, ry - b) for a, b in points))
    assert worst <= 1e-9, worst


def test_the_marker_leans_but_is_not_otherwise_distorted():
    upright = overlay.cursor_points(tilt=0.0)
    leaned = overlay.cursor_points()
    assert len(upright) == len(leaned)
    # the lean is a rotation about the tip: every point keeps its distance from it
    for a, b in zip(upright, leaned):
        assert round(math.hypot(*b), 9) == round(math.hypot(*a), 9)
    angle = math.radians(overlay.CURSOR_TILT_DEG)
    for (ax, ay), (bx, by) in zip(upright, leaned):
        if math.hypot(ax, ay) < 1e-9:
            continue
        turned = math.atan2(ay, ax) + angle
        assert round(math.atan2(by, bx), 6) == round((turned + math.pi) % (2 * math.pi) - math.pi, 6)


def test_the_raster_is_mirror_symmetric_too():
    """An exactly symmetric outline can still draw an asymmetric marker if the
    stroke, the sampling or the scale is off, so reflect the pixels as well.
    Checked upright, because a leaned outline does not land on the pixel grid."""
    points = overlay.cursor_points(overlay.CURSOR_PX, tilt=0.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    axis = 0.5 * (min(xs) + max(xs))
    half = overlay.OUTLINE_PX / 2.0
    mask = {}
    for py in range(math.floor(min(ys)), math.ceil(max(ys))):
        for px in range(math.floor(min(xs)), math.ceil(max(xs))):
            x, y = px + 0.5, py + 0.5
            if _inside(points, x, y):
                mask[(px, py)] = _edge_distance(points, x, y) <= half
    off = 0
    for (px, py), edge in mask.items():
        twin = (int(math.floor(2.0 * axis - px - 1.0)), py)
        if mask.get(twin) != edge:
            off += 1
    assert mask, "nothing was rasterised"
    assert off <= 0.02 * len(mask), (off, len(mask))


def test_the_marker_carries_no_text_and_only_its_own_colour():
    import inspect
    source = inspect.getsource(overlay)
    assert "SoText2" not in source and "SoFont" not in source
    target = overlay.targets_from(pay([pointer(2, label="building Standoff1")], [MEMBER], [BASE]))[2]
    assert "tag" not in target and "label" not in target


def test_the_outline_is_a_deeper_shade_of_the_same_colour():
    fill = overlay.PALETTE[2]                       # green
    edge = overlay.edge_color(fill)
    assert all(0.0 <= e < f for e, f in zip(edge, fill))   # deeper, never lighter
    assert edge[1] > edge[0] and edge[1] > edge[2]         # still clearly the same hue
    assert overlay.edge_color((0.0, 0.0, 0.0)) == (0.0, 0.0, 0.0)
    assert overlay.edge_color((1.0, 1.0, 1.0)) != (1.0, 1.0, 1.0)  # never white


def test_targets_skip_idle_left_and_unplaceable_pointers():
    objects = [BASE]
    members = [MEMBER,
               {"id": 3, "name": "gone", "kind": "agent", "status": "left", "status_text": ""},
               {"id": 4, "name": "stale", "kind": "agent", "status": "stale", "status_text": ""}]
    rows = [pointer(2, mode="idle"), pointer(3, mode="editing"), pointer(4, mode="editing"),
            pointer(5, mode="editing"),                       # member not in the team any more
            pointer(2, obj="Missing", mode="editing"),
            pointer(2, obj="Hidden", mode="editing")]
    objects.append({"name": "Hidden", "bbox": [0, 0, 0, 1, 1, 1], "visible": False})
    assert overlay.targets_from(pay(rows, members, objects)) == {}


def test_targets_carry_the_anchor_and_who_is_pointing():
    got = overlay.targets_from(pay([pointer(2, label="building Standoff1")], [MEMBER], [BASE]))
    assert set(got) == {2}
    t = got[2]
    assert t["name"] == "worker2"
    assert t["kind"] == "agent"
    assert t["object"] == "Base"
    assert t["anchor"] == (20.0, 10.0, 10.0)
    assert t["mode"] == "editing"


def test_two_agents_on_one_part_are_stood_side_by_side():
    a = {"id": 2, "name": "a", "kind": "agent", "status": "active", "status_text": ""}
    b = {"id": 3, "name": "b", "kind": "agent", "status": "active", "status_text": ""}
    got = overlay.targets_from(pay([pointer(3), pointer(2)], [a, b], [BASE]))
    assert got[2]["anchor"][0] < got[3]["anchor"][0]      # sorted by member, left to right
    assert got[2]["anchor"][1] == got[3]["anchor"][1] == 10.0
    assert abs(got[2]["anchor"][0] - 20.0) == abs(got[3]["anchor"][0] - 20.0)
    single = overlay.targets_from(pay([pointer(2)], [MEMBER], [BASE]))[2]
    assert single["anchor"] == (20.0, 10.0, 10.0)         # alone: right on the top centre


def test_an_exact_point_is_used_as_is_and_never_offset():
    got = overlay.targets_from(pay([pointer(2, point=[7, 8, 9]), pointer(3, point=[30, 8, 9])],
                                   [MEMBER, dict(MEMBER, id=3, name="other")], [BASE]))
    assert got[2]["anchor"] == (7.0, 8.0, 9.0)
    assert got[3]["anchor"] == (30.0, 8.0, 9.0)


def test_member_colours_match_the_panel_palette():
    colours = [overlay.member_color(i) for i in (1, 2, 3)]
    assert len(set(colours)) == 3
    assert colours[0] == overlay.PALETTE[0]


def test_spring_step_glides_and_lands_without_overshooting():
    x, v = 0.0, 0.0
    for _ in range(200):
        x, v = overlay.spring_step(x, v, 10.0, 1 / 25)
        assert -0.5 <= x <= 10.5, "a critically damped spring must not fly past the target"
    assert abs(x - 10.0) < 0.05
    assert abs(v) < 0.2
    assert math.isfinite(x)
