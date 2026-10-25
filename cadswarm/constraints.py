"""Auto-constrain: turn a model's repeated sizes, patterns and fits into shared Params.

A finished part usually carries the same numbers in several places: a lid and a base share a
height, three holes sit on one pitch, a shaft in a hole has a clearance. Editing one of them by
hand is how models drift apart. This module reads what `model()` and `get_object` report and
proposes constraints that say the relationship once, as a Params alias every part follows.

It is deliberately a planner plus a thin applier, and it uses only op kinds that already exist
(`set_param`, `set_props`), so a team does not need a new module or a republish for it.

Nothing here imports FreeCAD: the panel and the MCP server both feed it dictionaries and receive
a plan a person can review before `apply` writes anything.
"""
import re

# Dimensions that are real geometry, not housekeeping (Angle stays out: 360 is not a size).
DIM_PROPS = ("Length", "Width", "Height", "Radius", "Radius1", "Radius2", "Diameter", "Thickness")
PROP_ALIAS = {"Length": "length", "Width": "width", "Height": "height", "Radius": "radius",
              "Radius1": "radius1", "Radius2": "radius2", "Diameter": "diameter",
              "Thickness": "thickness"}
FIT_SETTINGS = ("tol_slip_fit", "tol_press_fit")
FIT_DEFAULTS = {"tol_slip_fit": 0.1, "tol_press_fit": -0.05}
MAX_FIT_GAP = 0.5          # mm each side; beyond this the two cylinders are not a mating pair
MIN_PATTERN_PARTS = 3
PATTERN_EVEN = 1e-3        # mm; equal spacing means equal within a micron-ish band


def _num(v):
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # not NaN


def _eq(a, b, eps):
    return abs(float(a) - float(b)) <= eps


def _mm(v):
    """A Params value FreeCAD reads as a quantity: 2 mm, 1.55 mm, =-0.05 mm.

    A leading '-' without '=' makes FreeCAD store the cell as text, and an expression that reads
    it is invalid, so a negative fit must be written as a formula.
    """
    f = float(v)
    return f"{f:g} mm" if f >= 0 else f"={f:g} mm"


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    if not s:
        return "size"
    return s if s[0].isalpha() else "p_" + s


def _base(obj):
    p = obj.get("placement") or {}
    b = p.get("base") if isinstance(p, dict) else None
    return [float(x) for x in b] if isinstance(b, (list, tuple)) and len(b) == 3 else [0.0, 0.0, 0.0]


def _axis(obj):
    """The object's local +Z in world coordinates; (0,0,1) when it has no rotation."""
    p = obj.get("placement") or {}
    rot = (p.get("rotation") or {}) if isinstance(p, dict) else {}
    ax = rot.get("axis") or [0, 0, 1]
    ang = _num(rot.get("angle")) or 0.0
    x, y, z = (float(v) for v in (list(ax) + [0, 0, 0])[:3])
    n = (x * x + y * y + z * z) ** 0.5 or 1.0
    x, y, z = x / n, y / n, z / n
    import math
    t = math.radians(ang)
    c, s = math.cos(t), math.sin(t)
    # Rodrigues' rotation of (0, 0, 1) about the axis.
    return [x * z * (1 - c) - y * s, y * z * (1 - c) + x * s, z * z * (1 - c) + c]


def _z_range(obj):
    b = obj.get("bbox")
    if isinstance(b, (list, tuple)) and len(b) == 6:
        return float(b[2]), float(b[5])
    return None


def _bound_roots(obj):
    out = set()
    for path in (obj.get("expressions") or {}):
        out.add(str(path).lstrip(".").split(".")[0])
    return out


def _bound_expr(obj, prop):
    for path, expr in (obj.get("expressions") or {}).items():
        if str(path).lstrip(".").split(".")[0] == prop:
            return expr
    return ""


def _dims(obj):
    """(prop, value) a part still owns itself: numeric, positive, not already expression-bound."""
    if obj.get("locked_by"):
        return []
    props = obj.get("properties") or {}
    bound = _bound_roots(obj)
    out = []
    for prop in DIM_PROPS:
        if prop in bound or prop not in props:
            continue
        v = _num(props.get(prop))
        if v is not None and v > 0:
            out.append((prop, v))
    return out


def _types(objects):
    return {o["name"]: str(o.get("type") or "") for o in objects if o.get("name")}


def _is_cylinder(obj):
    return "Cylinder" in str(obj.get("type") or "")


def _is_hole(obj, types):
    """A cylinder consumed by a cut or common boolean is the tool that made a hole."""
    if not _is_cylinder(obj):
        return False
    return any(types.get(parent, "") in ("Part::Cut", "Part::Common") for parent in obj.get("used_by") or [])


def _unique(alias, taken):
    if alias not in taken:
        return alias
    n = 2
    while f"{alias}_{n}" in taken:
        n += 1
    return f"{alias}_{n}"


class _Builder:
    def __init__(self, params, eps):
        self.existing = {k: float(v) for k, v in (params or {}).items() if _num(v) is not None}
        self.taken = set(self.existing)
        self.eps = eps
        self.constraints = []
        self.new_params = []      # every new alias, in creation order (for plan["params"])
        self._by_alias = {}
        self.bound = set()        # (object, property) already claimed by an earlier constraint

    def param(self, alias, value, note, reused=False):
        row = {"alias": alias, "value": _mm(value), "num": float(value), "note": note, "reused": reused}
        if not reused:
            self.new_params.append(row)
        self._by_alias[alias] = row
        return row

    def reuse_value(self, value, note):
        """An existing param already carrying this size: reuse it rather than adding a synonym."""
        for alias in sorted(self.existing):
            if _eq(self.existing[alias], value, self.eps):
                return self.param(alias, self.existing[alias], note, reused=True)
        return None

    def named(self, base, value, note, reused_value=True):
        """A param called `base` (or base_2...), reusing an equal one with that exact name."""
        alias = base
        if alias in self.existing:
            if reused_value and _eq(self.existing[alias], value, self.eps):
                return self.param(alias, self.existing[alias], note, reused=True)
            alias = _unique(alias, self.taken)
        elif alias in self._by_alias:
            if reused_value and _eq(self._by_alias[alias]["num"], value, self.eps):
                return self._by_alias[alias]
            alias = _unique(alias, self.taken)
        self.taken.add(alias)
        return self.param(alias, value, note)

    def add(self, constraint):
        ids = {c["id"] for c in self.constraints}
        if constraint["id"] in ids:
            constraint["id"] += "_2"
        self.constraints.append(constraint)
        for row in constraint["bindings"]:
            self.bound.add((row["object"], row["property"]))

    def plan(self, scope, skipped):
        seen, params = set(), []
        for c in self.constraints:
            for p in c["params"]:
                if not p["reused"] and p["alias"] not in seen:
                    seen.add(p["alias"])
                    params.append(p)
        parts = sorted({b["object"] for c in self.constraints for b in c["bindings"]})
        return {"scope": scope, "constraints": self.constraints, "params": params,
                "bindings": [b for c in self.constraints for b in c["bindings"]],
                "skipped": skipped,
                "summary": {"constraints": len(self.constraints), "parts": len(parts),
                            "new_params": len(params)}}


def plan(objects, params=None, settings=None, names=None, eps=1e-6):
    """Propose constraints for `objects` (model() rows enriched with get_object's `properties`).

    names limits the scope (a selection); params are the team's current Params.alias -> number;
    settings supply the team's fits (tol_slip_fit/tol_press_fit).
    """
    eps = abs(float(eps or 1e-6))
    settings = {**FIT_DEFAULTS, **(settings or {})}
    everything = [o for o in (objects or []) if isinstance(o, dict) and o.get("name")
                  and o["name"] != "Params" and "Spreadsheet" not in str(o.get("type") or "")]
    if names is not None:
        wanted = {str(n) for n in names}
        scoped = [o for o in everything if o["name"] in wanted]
    else:
        scoped = everything
    scope = [o["name"] for o in scoped]
    types = _types(everything)
    b = _Builder(params, eps)
    skipped = []

    for o in scoped:
        if o.get("locked_by"):
            skipped.append({"object": o["name"], "property": "",
                            "reason": f"locked by {o['locked_by']}"})
        for prop in DIM_PROPS:
            expr = _bound_expr(o, prop)
            if expr:
                skipped.append({"object": o["name"], "property": prop,
                                "reason": f"already follows {expr}"})

    _fits(scoped, types, settings, b, eps)
    _patterns(scoped, b, eps)
    _shared(scoped, b, eps)
    out = b.plan(scope, skipped)
    out["revs"] = {o["name"]: o["rev"] for o in scoped if o.get("rev")}
    out["fits"] = {k: settings.get(k) for k in FIT_SETTINGS}
    return out


def _fits(objects, types, settings, b, eps):
    holes = [o for o in objects if _is_hole(o, types) and not o.get("locked_by")]
    shafts = [o for o in objects if _is_cylinder(o) and not _is_hole(o, types)
              and not o.get("locked_by")]
    for hole in holes:
        if "Radius" in _bound_roots(hole):
            continue
        hr = _num((hole.get("properties") or {}).get("Radius"))
        if hr is None or hr <= 0:
            continue
        hb = _base(hole)
        hz = _z_range(hole)
        best = None
        for shaft in shafts:
            if "Radius" in _bound_roots(shaft):
                continue
            sr = _num((shaft.get("properties") or {}).get("Radius"))
            if sr is None or sr <= 0:
                continue
            sb = _base(shaft)
            if not (_eq(hb[0], sb[0], 1e-3) and _eq(hb[1], sb[1], 1e-3)):
                continue
            ax, az = _axis(hole), _axis(shaft)
            if abs(sum(x * z for x, z in zip(ax, az))) < 0.999:
                continue
            sz = _z_range(shaft)
            if hz and sz and (hz[1] < sz[0] - eps or sz[1] < hz[0] - eps):
                continue
            gap = hr - sr
            if -MAX_FIT_GAP - eps <= gap <= MAX_FIT_GAP + eps:
                if best is None or abs(gap) < abs(best[0]):
                    best = (gap, shaft, sr)
        if best is None:
            continue
        gap, shaft, sr = best
        label = shaft.get("label") or shaft["name"]
        radius = b.named(f"{_slug(label)}_r", sr, f"radius of {shaft['name']}")

        fit = "tol_slip_fit" if gap >= -eps else "tol_press_fit"
        if fit in b.existing:
            tol = b.existing[fit]
            tol_row = b.param(fit, tol, "the team's fit setting", reused=True)
        elif fit in b._by_alias:
            tol_row = b._by_alias[fit]
            tol = tol_row["num"]
        else:
            tol = _num(settings.get(fit))
            tol = FIT_DEFAULTS[fit] if tol is None else tol
            tol_row = b.named(fit, tol, "the team's fit setting")

        new = sr + tol / 2.0
        expr = f"Params.{radius['alias']} + Params.{fit} / 2"
        b.add({"id": f"fit:{hole['name']}", "kind": "fit", "objects": [hole["name"], shaft["name"]],
               "title": f"{hole['name']} follows the team's {fit.replace('tol_', '').replace('_', ' ')}",
               "detail": (f"{hole['name']} (r {hr:g}) mates with {shaft['name']} (r {sr:g}); "
                          f"Radius becomes {new:g} mm and follows Params.{fit}."),
               "params": [radius, tol_row],
               "bindings": [
                   {"object": shaft["name"], "property": "Radius", "old": sr, "new": sr,
                    "expression": f"Params.{radius['alias']}"},
                   {"object": hole["name"], "property": "Radius", "old": hr, "new": new,
                    "expression": expr}]})


def _patterns(objects, b, eps):
    groups = {}
    for o in objects:
        if o.get("locked_by") or "Placement" in _bound_roots(o):
            continue
        dims = tuple(sorted((p, round(v, 6)) for p, v in _dims(o)))
        if not dims:
            continue
        ax = tuple(round(v, 3) for v in _axis(o))
        base = _base(o)
        key = (str(o.get("type") or ""), dims, ax, round(base[1], 3), round(base[2], 3))
        groups.setdefault(key, []).append(o)
    for members in groups.values():
        if len(members) < MIN_PATTERN_PARTS:
            continue
        members = sorted(members, key=lambda o: _base(o)[0])
        xs = [_base(o)[0] for o in members]
        gaps = [round(xs[i + 1] - xs[i], 6) for i in range(len(xs) - 1)]
        if min(gaps) <= eps or max(gaps) - min(gaps) > PATTERN_EVEN:
            continue
        label = re.sub(r"[\s_-]*\d+$", "", str(members[0].get("label") or members[0]["name"]))
        stem = _slug(label)
        origin = b.named(f"{stem}_x0", xs[0], f"first {stem.replace('_', ' ')} position")
        pitch = b.named(f"{stem}_pitch", gaps[0], f"spacing of the {stem.replace('_', ' ')} row")
        bindings = []
        for i, o in enumerate(members):
            expr = f"Params.{origin['alias']}" if i == 0 else \
                (f"Params.{origin['alias']} + Params.{pitch['alias']}" if i == 1 else
                 f"Params.{origin['alias']} + {i} * Params.{pitch['alias']}")
            bindings.append({"object": o["name"], "property": ".Placement.Base.x",
                             "old": _base(o)[0], "new": _base(o)[0], "expression": expr})
        b.add({"id": f"pattern:{stem}", "kind": "pattern", "objects": [o["name"] for o in members],
               "title": f"One pitch for {len(members)} {stem.replace('_', ' ')}s",
               "detail": (f"{len(members)} aligned parts sit {gaps[0]:g} mm apart; "
                          f"Params.{pitch['alias']} moves them together."),
               "params": [origin, pitch], "bindings": bindings})


def _shared(objects, b, eps):
    rows = []
    for o in objects:
        for prop, value in _dims(o):
            rows.append((prop, value, o))
    rows.sort(key=lambda r: (r[0], r[1]))
    groups = []
    for prop, value, o in rows:
        if groups and groups[-1][0][0] == prop and _eq(groups[-1][0][1], value, eps):
            groups[-1].append((prop, value, o))
        else:
            groups.append([(prop, value, o)])
    for group in groups:
        if len(group) < 2:
            continue
        prop, value = group[0][0], group[0][1]
        group = [(p, v, o) for p, v, o in group if (o["name"], prop) not in b.bound]
        if len(group) < 2:
            continue
        names = [o["name"] for _, _, o in group]
        note = f"shared by {', '.join(names)}"
        row = b.reuse_value(value, note) or b.named(PROP_ALIAS.get(prop, _slug(prop)), value, note)
        bindings = [{"object": o["name"], "property": prop, "old": v, "new": v,
                     "expression": f"Params.{row['alias']}"} for _, v, o in group]
        b.add({"id": f"shared:{row['alias']}", "kind": "shared_size", "objects": names,
               "title": f"One {prop.lower()} for {len(names)} parts",
               "detail": f"{', '.join(names)} all use {value:g} mm; changing Params.{row['alias']} changes each.",
               "params": [row], "bindings": bindings})


def plan_from_model(model, details, settings=None, names=None, eps=1e-6):
    """Plan from `model()` plus the `get_object` details the caller fetched.

    The model row carries name/type/rev/bbox/placement/used_by; get_object carries the numeric
    properties (`Length`, `Radius`, ...) an expression can bind. Detail wins where both speak.
    """
    rows = []
    for o in (model or {}).get("objects") or []:
        if not o.get("name"):
            continue
        d = (details or {}).get(o["name"]) or {}
        row = dict(o)
        row["properties"] = d.get("properties") or o.get("properties") or {}
        for key in ("placement", "used_by", "uses", "expressions", "bbox", "label"):
            if d.get(key):
                row[key] = d[key]
        rows.append(row)
    return plan(rows, params=(model or {}).get("params") or {}, settings=settings,
                names=names, eps=eps)


def apply(plan, client, ids=None):
    """Write the chosen constraints through the existing locks + ops routes.

    Returns what was applied. Locks are released even when an op fails, and each object's revision
    travels with its op so a parallel edit is a clear stale_rev rather than a silent overwrite.
    """
    chosen = [c for c in (plan or {}).get("constraints") or []
              if ids is None or c.get("id") in set(ids)]
    if not chosen:
        return {"applied": [], "params": [], "objects": [], "recomputed": []}
    params, seen_p = [], set()
    bindings, seen_b = [], set()
    for c in chosen:
        for p in c.get("params") or []:
            if not p.get("reused") and p["alias"] not in seen_p:
                seen_p.add(p["alias"])
                params.append(p)
        for row in c.get("bindings") or []:
            key = (row["object"], row["property"])
            if key not in seen_b:
                seen_b.add(key)
                bindings.append(row)
    objects = sorted({row["object"] for row in bindings})
    lock_names = sorted((["Params"] if params else []) + objects)
    revs = (plan or {}).get("revs") or {}
    recomputed = []
    client.post("/locks", {"names": lock_names})
    try:
        for p in params:
            args = {"alias": p["alias"], "value": p["value"]}
            if p.get("note"):
                args["note"] = p["note"]
            r = client.post("/ops", {"op": "set_param", "args": args}) or {}
            recomputed.extend(r.get("recomputed") or [])
        for obj in objects:
            exprs = {row["property"]: row["expression"] for row in bindings if row["object"] == obj}
            body = {"op": "set_props", "args": {"name": obj, "expressions": exprs}}
            if revs.get(obj):
                body["expected_revs"] = {obj: revs[obj]}
            r = client.post("/ops", body) or {}
            recomputed.extend(r.get("recomputed") or [])
    finally:
        client.post("/locks/release", {"names": lock_names})
    return {"applied": [c["id"] for c in chosen], "params": [p["alias"] for p in params],
            "objects": objects, "recomputed": sorted(set(recomputed))}
