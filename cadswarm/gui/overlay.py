"""Agent pointers drawn *inside* FreeCAD's 3D view.

The team panel already mirrors the shared model in the read-only "CadSwarm Live"
document.  This module adds one marker per teammate whose pointer is not idle: a
soft, rounded cursor arrow in that member's colour, sitting exactly on the spot
they mean.  It carries no text, no outline and no leader line, so the model
underneath stays readable; who is pointing at what, and in which colour, is the
swarm plane's job.

The arrows are drawn at a constant screen size and turned to face the camera each
frame, so they read like the multiplayer cursors in a design tool instead of 3D
geometry.  They are Coin nodes attached to that view's scene graph, so they are a
true overlay: they never become document objects, are never saved into the model
and never appear in the tree or an export.

Networking (three reads from the member-only views) happens on one background
thread.  Only the QTimer touches the Coin scene graph, on the GUI thread, exactly
like ``panel.py``.  The pure helpers at the top are importable without FreeCAD so
they can be unit-tested.
"""
import math
import os
import queue
import threading
import time

MIRROR_DOC = "CadSwarmLive"     # keep in step with panel.MIRROR_DOC
POLL_SECONDS = 0.8              # how often the pointer views are re-read
FRAME_MS = 40                   # 25 fps glide; cheap enough to leave running
FADE_SECONDS = 0.9
SPRING = 70.0                   # critically damped: c = 2 * sqrt(k)
ACTIVE = ("active", "offline")  # members whose pointer is worth drawing

# Same hues as panel.PALETTE (Tableau 10).  panel.owner_color is preferred so an
# arrow always matches the lock tint of the part it is pointing at; this is only
# the fallback for tests or if the panel is unavailable.
PALETTE = [(0.12, 0.47, 0.71), (1.0, 0.50, 0.05), (0.17, 0.63, 0.17), (0.84, 0.15, 0.16),
           (0.58, 0.40, 0.74), (0.55, 0.34, 0.29), (0.89, 0.47, 0.76), (0.09, 0.75, 0.81),
           (0.74, 0.74, 0.13), (0.50, 0.50, 0.50)]


# --------------------------------------------------------------------------- pure helpers

def bbox_diagonal(bbox):
    """Length of the box's space diagonal in mm (0 when there is no box)."""
    if not bbox or len(bbox) < 6:
        return 0.0
    try:
        x0, y0, z0, x1, y1, z1 = (float(v) for v in bbox[:6])
    except (TypeError, ValueError):
        return 0.0
    return math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2)


def bbox_anchor(bbox, point=None):
    """Where the arrow points: an exact [x, y, z] when the agent gave one, otherwise the
    centre of the top face of the object's bounding box.  None if neither is usable."""
    if point is not None and len(point) == 3:
        try:
            return tuple(float(v) for v in point)
        except (TypeError, ValueError):
            pass
    if not bbox or len(bbox) < 6:
        return None
    try:
        x0, y0, z0, x1, y1, z1 = (float(v) for v in bbox[:6])
    except (TypeError, ValueError):
        return None
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0, z1)


def arrow_dims(bbox):
    """How far apart to stand arrows that share a part: a fraction of its size, clamped."""
    diag = bbox_diagonal(bbox)
    size = max(4.0, min(0.11 * diag, 45.0)) if diag > 0 else 8.0
    return {"size": size}


def spring_step(value, velocity, target, dt, stiffness=SPRING):
    """One step of a critically damped spring (no overshoot, settles smoothly)."""
    dt = max(0.0, min(float(dt), 0.25))
    if dt == 0.0:
        return value, velocity
    accel = stiffness * (target - value) - 2.0 * math.sqrt(stiffness) * velocity
    v = velocity + accel * dt
    x = value + v * dt
    if abs(target - x) < 0.02 and abs(v) < 0.2:
        return target, 0.0
    return x, v


def targets_from(payload):
    """Turn one snapshot of the three views into ``{member_id: target}``.

    Idle pointers are omitted (the overlay fades those out), as are members who left
    or were taken over, hidden objects and pointers with nothing to point at."""
    members = {}
    for m in payload.get("members") or []:
        try:
            members[int(m["id"])] = m
        except (KeyError, TypeError, ValueError):
            continue
    objects = {o.get("name"): o for o in payload.get("objects") or [] if o.get("name")}
    rows = []
    for p in payload.get("pointers") or []:
        try:
            mid = int(p["member"])
        except (KeyError, TypeError, ValueError):
            continue
        m = members.get(mid)
        if not m or m.get("status") not in ACTIVE:
            continue
        if (p.get("mode") or "idle") == "idle":
            continue
        obj = objects.get(p.get("object")) or {}
        if obj and obj.get("visible") is False:
            continue
        anchor = bbox_anchor(obj.get("bbox"), p.get("point"))
        if anchor is None:
            continue
        rows.append((mid, p, m, obj, anchor))

    rows.sort(key=lambda r: r[0])
    per_object = {}
    for mid, _p, _m, obj, _anchor in rows:
        per_object.setdefault(obj.get("name") or "", []).append(mid)

    targets = {}
    for mid, p, m, obj, anchor in rows:
        exact = p.get("point") is not None and len(p.get("point") or []) == 3
        group = per_object.get(obj.get("name") or "") or [mid]
        if not exact and len(group) > 1:
            # Two agents on the same part: stand their arrows side by side instead of
            # letting one hide the other.  Exact points are already distinct.
            index = group.index(mid)
            step = arrow_dims(obj.get("bbox"))["size"] * 0.60
            anchor = (anchor[0] + (index - (len(group) - 1) / 2.0) * step,
                      anchor[1], anchor[2])
        targets[mid] = {"member": mid,
                        "name": str(m.get("name") or f"member {mid}"),
                        "kind": str(m.get("kind") or "agent"),
                        "object": str(p.get("object") or ""),
                        "anchor": anchor,
                        "mode": str(p.get("mode") or ""),
                        "seq": int(p.get("seq") or 0)}
    return targets


def member_color(member_id):
    """The same colour the panel gives that member's locks, when the panel is loaded."""
    try:
        from . import panel
        return panel.owner_color(member_id)
    except Exception:  # noqa: BLE001 - tests and early startup have no FreeCAD panel
        return PALETTE[(int(member_id) - 1) % len(PALETTE)] if member_id else PALETTE[-1]


# --------------------------------------------------------------------------- network source

class LiveSource:
    """Reads the live team's pointers, members and object boxes for the overlay.

    A fresh short-lived client is used per poll: the panel's client belongs to its
    own threads, and SQL reads are cheap.  Any failure returns None and the last
    arrows simply stay where they were."""

    def __init__(self, cfg, identity):
        self.uri = (cfg or {}).get("uri") or ""
        self.db = (cfg or {}).get("db") or ""
        self.token = (identity or {}).get("token") or ""

    def fetch(self):
        if not (self.uri and self.db and self.token):
            return None
        from ..space import SpaceClient
        client = SpaceClient(self.uri, self.db, self.token, timeout=8)
        return {"pointers": client.sql("SELECT * FROM v_pointer"),
                "members": client.sql("SELECT id, name, kind, status, status_text FROM v_member"),
                "objects": client.sql("SELECT name, label, bbox, visible FROM v_object")}


class DemoSource:
    """Scripted pointers over three boxes, for previewing the overlay without a team.

    Run ``overlay.preview()`` from FreeCAD's Python console (or the macro editor) to
    see exactly what the live arrows look like; nothing connects to the network."""

    members = [{"id": 1, "name": "planner", "kind": "agent", "status": "active", "status_text": "planning the case"},
               {"id": 2, "name": "worker1", "kind": "agent", "status": "active", "status_text": "cutting the lid"},
               {"id": 3, "name": "worker2", "kind": "agent", "status": "active", "status_text": "checking holes"}]
    objects = [{"name": "Base", "label": "Base", "bbox": [0, 0, 0, 60, 40, 12], "visible": True},
               {"name": "Lid", "label": "Lid", "bbox": [0, 0, 18, 60, 40, 30], "visible": True},
               {"name": "Standoff1", "label": "Standoff1", "bbox": [10, 10, 12, 16, 16, 24], "visible": True}]

    def __init__(self):
        self.t0 = time.time()

    def fetch(self):
        t = time.time() - self.t0
        picks = [("Base", "pointing", "measuring the wall"), ("Lid", "editing", "cutting the opening"),
                 ("Standoff1", "inspecting", "checking the height")]
        pointers = []
        for i, (obj, mode, label) in enumerate(picks):
            pointers.append({"member": i + 1, "object": obj, "point": [], "mode": mode,
                             "label": label, "op": "", "seq": int(t * 4) + i,
                             "at_ms": int(time.time() * 1000)})
        if int(t / 3) % 2:  # every few seconds one agent moves to the other part
            pointers[2]["object"] = "Base"
            pointers[2]["label"] = "re-measuring the floor"
        return {"pointers": pointers, "members": self.members, "objects": self.objects}


# --------------------------------------------------------------------------- Coin pointers

# Every size below is a *design pixel*: one logical screen pixel.  A per-frame
# scale turns them into world units for the point being pointed at, so a cursor
# stays exactly this size at any zoom, like a pointer in a design tool.
CURSOR_PX = 22.0         # length of the marker along its axis, tip to the barbs, in logical pixels
CURSOR_TILT_DEG = 30.0   # lean of the marker, clockwise from upright, like a real pointer
OUTLINE_PX = 1.35        # width of the deeper-shade outline, in logical pixels
EDGE_DARKEN = 0.55       # the outline is the member colour multiplied by this
CORNER_STEPS = 40        # segments per rounded corner: at most 4 degrees of turn each

# The marker is a plain rounded dart: a triangle, tip up, with a concave scoop
# cut into the middle of its base so that it ends in two equal barbs.
# Only the right half is described; the left half is its mirror image, so the two
# sides are identical by construction rather than by matching numbers.
#
# Design units, y down, the tip at the origin and the mirror axis through it.
_CURSOR_TIP = (0.0, 0.0)         # the spot being pointed at
_CURSOR_BARB = (5.9, 16.0)       # the right barb; the left one is its mirror
_CURSOR_NOTCH = (0.0, 9.0)       # the top of the scoop, on the axis
# The radius of the circular arc that rounds each corner, in design units.
_CURSOR_RADII = {"tip": 0.85, "barb": 0.85, "notch": 2.0}
_CURSOR_AXIS = _CURSOR_TIP[0]


def edge_color(color):
    """A deeper shade of a member's colour, for the outline around their cursor."""
    return tuple(max(0.0, min(1.0, float(c) * EDGE_DARKEN)) for c in color)


def _unit(a, b):
    """The direction from ``a`` to ``b``, normalised (safe on coincident points)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    return (0.0, 0.0) if length < 1e-12 else (dx / length, dy / length)


def _turn(into, out):
    """The signed angle the outline turns through from ``into`` to ``out``."""
    return math.atan2(into[0] * out[1] - into[1] * out[0], into[0] * out[0] + into[1] * out[1])


def _corner(vertex, into, out, radius, steps=CORNER_STEPS):
    """Round a corner with a circular arc of ``radius`` tangent to both edges.

    ``into`` and ``out`` are the unit directions the outline arrives and leaves
    along.  The arc is cut back the same distance along both edges, so a corner
    whose edges mirror each other gets a mirror-image arc, and it is sampled at
    equal steps of angle, so every segment turns by the same small amount and no
    single facet shows.  On the concave notch the same arc smooths the scoop.
    """
    turn = _turn(into, out)
    if radius <= 1e-9 or abs(turn) < 1e-9:
        return [vertex]
    back = radius * math.tan(abs(turn) / 2.0)
    start = (vertex[0] - into[0] * back, vertex[1] - into[1] * back)
    side = 1.0 if turn > 0.0 else -1.0
    centre = (start[0] - side * into[1] * radius, start[1] + side * into[0] * radius)
    vx, vy = start[0] - centre[0], start[1] - centre[1]
    points = []
    for i in range(steps + 1):
        c, s = math.cos(turn * i / steps), math.sin(turn * i / steps)
        points.append((centre[0] + vx * c - vy * s, centre[1] + vx * s + vy * c))
    return points


def _mirror(point):
    return (2.0 * _CURSOR_AXIS - point[0], point[1])


def _fit(vertex, into, out, radius, *edges):
    """The largest radius up to ``radius`` whose arc uses at most half of each edge."""
    tan = math.tan(abs(_turn(into, out)) / 2.0)
    return min([radius] + [0.5 * length / tan for length in edges]) if tan > 1e-9 else radius


def _half_outline(steps):
    """The right half, from the top of the scoop round the barb to the tip, y down."""
    tip, barb, notch = _CURSOR_TIP, _CURSOR_BARB, _CURSOR_NOTCH
    left = _mirror(barb)
    side, base = math.dist(tip, barb), math.dist(barb, notch)
    even = steps + steps % 2       # the arcs on the axis need a sample at their apex
    corners = (
        (notch, _unit(left, notch), _unit(notch, barb), "notch", (base,), even),
        (barb, _unit(notch, barb), _unit(barb, tip), "barb", (base, side), steps),
        (tip, _unit(barb, tip), _unit(tip, left), "tip", (side,), even),
    )
    arcs = [_corner(v, i, o, _fit(v, i, o, _CURSOR_RADII[name], *edges), n)
            for v, i, o, name, edges, n in corners]
    half = arcs[0][len(arcs[0]) // 2:] + arcs[1] + arcs[2][:len(arcs[2]) // 2 + 1]
    # the two apexes lie on the axis exactly, so the mirror reproduces them as-is
    half[0] = (_CURSOR_AXIS, half[0][1])
    half[-1] = (_CURSOR_AXIS, half[-1][1])
    return half


def _rotate(points, tip, degrees):
    """Swing the outline about ``tip`` so the dart leans like a pointer."""
    if abs(degrees) < 1e-9:
        return list(points)
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    swung = []
    for x, y in points:
        dx, dy = x - tip[0], y - tip[1]
        swung.append((tip[0] + dx * cos - dy * sin, tip[1] + dx * sin + dy * cos))
    return swung


def cursor_points(size=CURSOR_PX, tip=(0.0, 0.0), steps=CORNER_STEPS, tilt=CURSOR_TILT_DEG):
    """The symmetric dart in design pixels, y up, its rounded tip exactly at ``tip``.

    The tip lands on the spot being pointed at and the body hangs below it, leaned
    over by ``tilt`` degrees so the marker reads as a pointer rather than a badge.
    The lean is a rotation of the whole outline about the tip, so the marker stays
    exactly mirror-symmetric about its own tilted axis.  ``size`` is the length of
    the dart along that axis, from the tip to the foot of the barbs.
    """
    half = _half_outline(max(2, int(steps)))
    loop = [_mirror(p) for p in reversed(half)] + half[1:-1]
    apex_y = loop[0][1]
    k = float(size) / (max(p[1] for p in loop) - apex_y)
    upright = [(tip[0] + (x - _CURSOR_AXIS) * k, tip[1] - (y - apex_y) * k) for x, y in loop]
    return _rotate(upright, tip, tilt)


def _world_per_pixel(coin, cam, point, viewport_h):
    """World units per physical screen pixel at ``point``, for constant-size arrows."""
    if cam is None or viewport_h <= 0:
        return 1.0
    try:
        pos = cam.position.getValue()
        forward = cam.orientation.getValue().multVec(coin.SbVec3f(0.0, 0.0, -1.0))
        depth = ((point[0] - pos[0]) * forward[0] + (point[1] - pos[1]) * forward[1]
                 + (point[2] - pos[2]) * forward[2])
        if cam.getTypeId().isDerivedFrom(coin.SoOrthographicCamera.getClassTypeId()):
            height = float(cam.height.getValue())
        else:
            height = 2.0 * max(1e-3, depth) * math.tan(float(cam.heightAngle.getValue()) / 2.0)
        return max(1e-6, height / float(viewport_h))
    except Exception:  # noqa: BLE001 - an odd camera must never stop the overlay
        return 1.0


class _Arrow:
    """One teammate's pointer: a cursor arrow in their colour, with no text at all."""

    def __init__(self, coin, member_id, color):
        self.coin = coin
        self.member = member_id
        self.color = tuple(float(c) for c in color)
        self.target = None
        self.pos = None
        self.vel = (0.0, 0.0, 0.0)
        self.alpha = 1.0
        self.fading = False
        self.mats = []

        self.root = coin.SoSeparator()
        tint = coin.SoTransparencyType()
        tint.value.setValue(coin.SoTransparencyType.BLEND)  # draw in traversal order
        self.root.addChild(tint)
        self.pos_node = coin.SoTranslation()
        self.rot = coin.SoRotation()
        self.scale = coin.SoScale()
        self.root.addChild(self.pos_node)
        self.root.addChild(self.rot)
        self.root.addChild(self.scale)

        points = cursor_points(CURSOR_PX)
        self.fill = self._shape(self.color, 1.0)
        self._place(self.fill[1], self.fill[2], points)
        self.root.addChild(self.fill[0])

        # The same loop again as a constant-width line in a deeper shade of the same
        # colour: enough edge to lift the cursor off any model colour without ever
        # introducing a second, unrelated colour.
        edge, coords, line, self.style = self._edge(edge_color(self.color), 0.96)
        self._loop(coords, line, points)
        self.root.addChild(edge)

    # ------------------------------------------------------------------ geometry
    def _mat(self, color, alpha):
        coin = self.coin
        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(*color)
        mat.emissiveColor.setValue(*color)
        mat.transparency.setValue(1.0 - alpha)
        self.mats.append((mat, alpha))
        return mat

    def _shape(self, color, alpha=1.0):
        """A flat, unlit polygon: (separator, coordinates, indexed face set)."""
        coin = self.coin
        sep = coin.SoSeparator()
        light = coin.SoLightModel()
        light.model.setValue(coin.SoLightModel.BASE_COLOR)
        # The outline is concave at the scoop; Coin assumes convex faces unless told,
        # and a convex fill would paint straight across the notch.
        hints = coin.SoShapeHints()
        hints.faceType.setValue(coin.SoShapeHints.UNKNOWN_FACE_TYPE)
        coords = coin.SoCoordinate3()
        faces = coin.SoIndexedFaceSet()
        sep.addChild(hints)
        sep.addChild(light)
        sep.addChild(self._mat(color, alpha))
        sep.addChild(coords)
        sep.addChild(faces)
        return sep, coords, faces

    def _edge(self, color, alpha=1.0):
        """The outline around the arrow: (separator, coords, line set, draw style)."""
        coin = self.coin
        sep = coin.SoSeparator()
        light = coin.SoLightModel()
        light.model.setValue(coin.SoLightModel.BASE_COLOR)
        style = coin.SoDrawStyle()
        style.lineWidth.setValue(OUTLINE_PX)
        coords = coin.SoCoordinate3()
        line = coin.SoLineSet()
        sep.addChild(light)
        sep.addChild(self._mat(color, alpha))
        sep.addChild(style)
        sep.addChild(coords)
        sep.addChild(line)
        return sep, coords, line, style

    @staticmethod
    def _place(coords, faces, points):
        count = len(points)
        coords.point.setNum(count)
        for i, (x, y) in enumerate(points):
            coords.point.set1Value(i, x, y, 0.0)
        faces.coordIndex.setNum(count + 1)
        for i in range(count):
            faces.coordIndex.set1Value(i, i)
        faces.coordIndex.set1Value(count, -1)

    @staticmethod
    def _loop(coords, line, points):
        """The same outline as one closed loop, so the stroke joins up cleanly."""
        count = len(points)
        coords.point.setNum(count + 1)
        for i, (x, y) in enumerate(points):
            coords.point.set1Value(i, x, y, 0.0)
        coords.point.set1Value(count, points[0][0], points[0][1], 0.0)
        line.numVertices.setValue(count + 1)

    # -------------------------------------------------------------------- state
    def nodes(self):
        return (self.root,)

    def set_target(self, target):
        self.target = target
        self.fading = False
        if self.pos is None:
            self.pos = tuple(target["anchor"])
            self.vel = (0.0, 0.0, 0.0)

    def frame(self, dt, clock, reduce_motion, cam=None, viewport_h=1, dpr=1.0):
        if self.target is None:
            self.alpha = max(0.0, self.alpha - dt / FADE_SECONDS)
            self.fading = True
        else:
            ax, ay, az = self.target["anchor"]
            if reduce_motion or self.pos is None:
                self.pos = (ax, ay, az)
                self.vel = (0.0, 0.0, 0.0)
            else:
                px, vx = spring_step(self.pos[0], self.vel[0], ax, dt)
                py, vy = spring_step(self.pos[1], self.vel[1], ay, dt)
                pz, vz = spring_step(self.pos[2], self.vel[2], az, dt)
                self.pos, self.vel = (px, py, pz), (vx, vy, vz)
            self.alpha = min(1.0, self.alpha + dt / FADE_SECONDS)

        if self.pos is None:
            self._set_alpha(0.0)
            return

        x, y, z = self.pos
        self.pos_node.translation.setValue(x, y, z)
        if cam is not None:
            try:
                self.rot.rotation.setValue(cam.orientation.getValue())
            except Exception:  # noqa: BLE001 - keep the last orientation
                pass
        per_px = _world_per_pixel(self.coin, cam, (x, y, z), viewport_h) * max(0.1, float(dpr or 1.0))
        self.scale.scaleFactor.setValue(per_px, per_px, per_px)
        # Line width is in device pixels, so it needs the same dpr the fill does.
        self.style.lineWidth.setValue(max(1.0, OUTLINE_PX * max(0.1, float(dpr or 1.0))))
        self._set_alpha(self.alpha)

    def _set_alpha(self, alpha):
        alpha = max(0.0, min(1.0, alpha))
        for mat, base in self.mats:
            mat.transparency.setValue(max(0.0, min(1.0, 1.0 - alpha * base)))

    def dispose(self, parent):
        for node in self.nodes():
            try:
                parent.removeChild(node)
            except Exception:  # noqa: BLE001 - already detached
                pass


def _reduce_motion():
    if os.environ.get("CADSWARM_REDUCE_MOTION", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        import FreeCAD as App
        return bool(App.ParamGet("User parameter:BaseApp/Preferences/Mod/CadSwarm").GetBool("ReduceMotion", False))
    except Exception:  # noqa: BLE001 - no FreeCAD (tests) or no preference yet
        return False


class Overlay:
    """Draws the live pointers over the CadSwarm Live view, or over the active document when it
    holds the parts being pointed at.  Create on the GUI thread."""

    def __init__(self, source, on_error=None, document=MIRROR_DOC):
        self.source = source
        self.on_error = on_error
        self.document = document or MIRROR_DOC
        self._stop = threading.Event()
        self._queue = queue.Queue(maxsize=2)
        self._thread = None
        self._timer = None
        self._coin = None
        self._root = None
        self._anno = None
        self._attached = None
        self._view = None
        self._gui_doc = None
        self._arrows = {}
        self._targets = {}
        self._last = 0.0
        self._clock = 0.0
        self._warned = False
        self._reduce = False
        self._reduce_at = 0.0
        self.last_error = ""

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        if self._timer is not None:
            return
        from PySide import QtCore
        self._coin = _coin()
        self._root = self._coin.SoSeparator()
        self._anno = self._coin.SoAnnotation()
        self._root.addChild(self._anno)
        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_MS)
        self._last = time.time()
        self._thread = threading.Thread(target=self._poll, name="cadswarm-pointers", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._thread = None
        self._detach()
        if self._anno is not None and self._coin is not None:
            for arrow in list(self._arrows.values()):
                arrow.dispose(self._anno)
        self._arrows.clear()
        self._targets.clear()
        self._root = self._anno = self._coin = None

    # ------------------------------------------------------------------ polling
    def _poll(self):
        while not self._stop.is_set():
            data = None
            try:
                data = self.source.fetch()
            except Exception:  # noqa: BLE001 - the last arrows stay on screen
                data = None
            if data is not None and not self._stop.is_set():
                try:
                    while True:
                        self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(data)
                except queue.Full:
                    pass
            self._stop.wait(POLL_SECONDS)

    # -------------------------------------------------------------------- frame
    def _tick(self):
        now = time.time()
        dt = max(0.001, min(0.25, now - self._last))
        self._last = now
        self._clock += dt
        try:
            while True:
                try:
                    data = self._queue.get_nowait()
                except queue.Empty:
                    break
                self._targets = targets_from(data)
            parent = self._ensure_root()
            if parent is None:
                return
            self._sync(parent, dt)
        except Exception as e:  # noqa: BLE001 - one bad frame must never stop the overlay
            self.last_error = str(e)
            if not self._warned:
                self._warned = True
                self._warn(f"pointers paused: {e}")

    def _pick_document(self):
        """The document whose view gets the arrows: the active one when it holds a part someone is
        pointing at (your own working copy of the model), otherwise the CadSwarm Live mirror."""
        import FreeCAD as App
        import FreeCADGui as Gui
        active = Gui.ActiveDocument
        if active is not None and active.Document.Name != self.document:
            if any(active.Document.getObject(t["object"]) for t in self._targets.values() if t["object"]):
                return active
        if self.document in App.listDocuments():
            return Gui.getDocument(self.document)
        return None

    def _shows(self, target):
        """In a document other than the mirror, only arrows on parts that document has."""
        doc = getattr(self._gui_doc, "Document", None)
        if doc is None or doc.Name == self.document or not target["object"]:
            return True
        return doc.getObject(target["object"]) is not None

    def _ensure_root(self):
        doc = self._pick_document()
        self._gui_doc = doc
        view = getattr(doc, "ActiveView", None) if doc is not None else None
        if view is None or not hasattr(view, "getSceneGraph"):
            return None
        graph = view.getSceneGraph()
        self._view = view
        if graph is not self._attached:
            if self._attached is not None:
                try:
                    self._attached.removeChild(self._root)
                except Exception:  # noqa: BLE001 - the old view is gone
                    pass
            graph.addChild(self._root)
            self._attached = graph
        return self._anno

    def _detach(self):
        if self._attached is not None and self._root is not None:
            try:
                self._attached.removeChild(self._root)
            except Exception:  # noqa: BLE001
                pass
        self._attached = None
        self._view = None
        self._gui_doc = None

    def _view_frame(self):
        """The camera, viewport height and physical/logical pixel ratio for this frame."""
        view = self._view
        cam = height = None
        if view is not None:
            try:
                cam = view.getCameraNode()
                height = int(view.getSize()[1])
            except Exception:  # noqa: BLE001 - the view is closing
                cam = height = None
        dpr = 1.0
        try:
            import FreeCADGui as Gui
            dpr = float(Gui.getMainWindow().devicePixelRatioF())
        except Exception:  # noqa: BLE001 - console runs have no main window
            dpr = 1.0
        return cam, height or 1, max(0.1, dpr)

    def _sync(self, parent, dt):
        if self._clock - self._reduce_at > 5.0:
            self._reduce_at = self._clock
            self._reduce = _reduce_motion()
        reduce_motion = self._reduce
        cam, viewport_h, dpr = self._view_frame()
        keep = {mid for mid, t in self._targets.items() if self._shows(t)}
        for mid in keep:
            target = self._targets[mid]
            arrow = self._arrows.get(mid)
            if arrow is None:
                arrow = _Arrow(self._coin, mid, member_color(mid))
                for node in arrow.nodes():
                    parent.addChild(node)
                self._arrows[mid] = arrow
            arrow.set_target(target)
        for mid in [m for m in self._arrows if m not in keep]:
            self._arrows[mid].target = None
        for mid, arrow in list(self._arrows.items()):
            arrow.frame(dt, self._clock, reduce_motion, cam=cam, viewport_h=viewport_h, dpr=dpr)
            if arrow.alpha <= 0.0 and arrow.target is None:
                arrow.dispose(parent)
                del self._arrows[mid]

    def _warn(self, text):
        try:
            import FreeCAD as App
            App.Console.PrintWarning(f"[CadSwarm pointers] {text}\n")
        except Exception:  # noqa: BLE001
            pass


def _coin():
    from pivy import coin
    return coin


# --------------------------------------------------------------------------- install hook

class _Watcher:
    """Starts an Overlay when the panel joins a team and stops it when they leave."""

    def __init__(self):
        self.overlay = None
        self._key = None
        self._timer = None
        self.enabled = True
        self._warned = False

    def start(self):
        from PySide import QtCore
        if self._timer is not None:
            return
        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self.check)
        self._timer.start(700)
        self.check()

    def check(self):
        try:
            from . import panel
            session = getattr(panel, "SESSION", None)
            client = getattr(session, "client", None) if session is not None else None
            ident = getattr(session, "ident", None) or {}
            key = None
            if client is not None and getattr(session, "connected", False):
                cfg = getattr(client, "cfg", None) or {}
                key = (cfg.get("uri"), cfg.get("db"), ident.get("participant_id"),
                       ident.get("fence"), ident.get("token"))
            if key == self._key:
                return
            self._key = key
            self._stop_overlay()
            if key is not None and self.enabled:
                cfg = {"uri": key[0], "db": key[1]}
                self._start_overlay(LiveSource(cfg, ident))
        except Exception as e:  # noqa: BLE001 - never break FreeCAD startup or the panel
            if not self._warned:
                self._warned = True
                self._warn(f"could not watch the team: {e}")

    def _start_overlay(self, source):
        if self.overlay is not None:
            return
        try:
            self.overlay = Overlay(source)
            self.overlay.start()
        except Exception as e:  # noqa: BLE001
            self.overlay = None
            if not self._warned:
                self._warned = True
                self._warn(f"could not draw pointers: {e}")

    def _stop_overlay(self):
        if self.overlay is not None:
            try:
                self.overlay.stop()
            finally:
                self.overlay = None

    def toggle(self):
        self.enabled = not self.enabled
        if self.enabled:
            self._key = None
            self.check()
        else:
            self._stop_overlay()
        return self.enabled


_WATCHER = None


def install():
    """Idempotent: called once from the workbench's Initialize."""
    global _WATCHER
    if _WATCHER is None:
        _WATCHER = _Watcher()
        _WATCHER.start()
    return _WATCHER


def _toggle_command():
    class Toggle:
        def GetResources(self):
            return {"MenuText": "Agent arrows", "ToolTip": "Show or hide the team's pointers in the 3D view",
                    "Pixmap": ""}

        def Activated(self):
            if _WATCHER is not None:
                _WATCHER.toggle()

        def IsActive(self):
            return True

    return Toggle()


def register_command():
    import FreeCADGui as Gui
    Gui.addCommand("CadSwarm_Pointers", _toggle_command())


def preview():
    """Open a throwaway document with three boxes and animated demo arrows.

    From FreeCAD's Python console::

        import cadswarm.gui.overlay as overlay
        overlay.preview()

    Nothing touches the network or the team; close the document when done."""
    import FreeCAD as App
    import FreeCADGui as Gui
    name = "CadSwarmPointersDemo"
    old = App.listDocuments().get(name)
    if old is not None:
        Gui.closeDocument(name)
    doc = App.newDocument(name)
    for obj in DemoSource.objects:
        x0, y0, z0, x1, y1, z1 = obj["bbox"]
        box = doc.addObject("Part::Box", obj["name"])
        box.Length, box.Width, box.Height = x1 - x0, y1 - y0, z1 - z0
        box.Placement.Base = App.Vector(x0, y0, z0)
    doc.recompute()
    Gui.setActiveDocument(name)
    Gui.getDocument(name).ActiveView.viewIsometric()
    Gui.getDocument(name).ActiveView.fitAll()

    overlay = Overlay(DemoSource(), document=name)
    overlay.start()
    _DEMOS.append((name, overlay))
    return overlay


_DEMOS = []


def stop_preview():
    while _DEMOS:
        name, overlay = _DEMOS.pop()
        overlay.stop()
