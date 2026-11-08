"""CADSwarm FreeCAD worker.

Runs inside FreeCAD's bundled Python (NOT the hub's venv) and owns the single
live shared document. The hub talks to it over JSON lines on stdin/stdout,
one request at a time, so every mutation of the model is serialized: FreeCAD
is not thread-safe, and this is the one place the document is ever touched.

Every mutating op runs inside a FreeCAD transaction. After the op and a
recompute, the worker diffs every object's "primary" fingerprint (properties a
caller sets, not ones a recompute or an expression derives). If the op changed
or deleted an object outside the caller's `allowed` set, or left an object it
touched in an error state, the transaction is aborted and nothing is saved:
the CAD equivalent of Amoeba's "nothing ever overwrites another participant's
work" guarantee, enforced by rollback instead of a merge.

Request:  {"id": 1, "op": "set_property", "args": {...}, "allowed": ["Box"] | null}
Response: {"id": 1, "ok": true, "result": {...}, "created": [], "modified": [],
           "deleted": [], "recomputed": []}
       or {"id": 1, "ok": false, "code": "lock_violation", "error": "..."}
"""
import ast
import base64
import json
import math
import os
import re
import struct
import sys
import time
import threading
import traceback

import sandbox  # noqa: E402  (next to this file; the worker runs as a plain script)

FREECAD_BIN = os.environ.get("CADSWARM_FREECAD_BIN")

# Where `import FreeCAD` can come from: next to FreeCAD's Python (Windows), its sibling lib folder
# (macOS .app, extracted AppImage), or a distro package's lib folder (Linux, system python3).
FREECAD_LIB_DIRS = ("/usr/lib/freecad/lib", "/usr/lib/freecad-python3/lib", "/usr/lib64/freecad/lib",
                    "/usr/local/lib/freecad/lib")


def add_freecad_paths(bin_dir):
    dirs = [os.environ.get("CADSWARM_FREECAD_LIB")]
    if bin_dir:
        dirs += [bin_dir, os.path.join(os.path.dirname(bin_dir), "lib")]
    dirs += list(FREECAD_LIB_DIRS)
    for d in dirs:
        if d and os.path.isdir(d) and d not in sys.path:
            sys.path.append(d)


add_freecad_paths(FREECAD_BIN)

import FreeCAD  # noqa: E402
import Part  # noqa: E402
import Sketcher  # noqa: E402

App = FreeCAD
Vector = FreeCAD.Vector
Placement = FreeCAD.Placement
Rotation = FreeCAD.Rotation

PARAMS = "Params"
ADDRESS = re.compile(r" at (0x)?[0-9A-Fa-f]{6,}")
# Properties that never count as a direct edit: view state, bookkeeping, and the
# expression engine (compared separately so binding an expression is an edit,
# but the value it produces is not).
IGNORED_PROPS = {"Visibility", "ExpressionEngine", "Label2", "Shape", "cells", "Content"}

PRIMITIVES = {
    "box": ("Part::Box", ["Length", "Width", "Height"]),
    "cylinder": ("Part::Cylinder", ["Radius", "Height", "Angle"]),
    "sphere": ("Part::Sphere", ["Radius"]),
    "cone": ("Part::Cone", ["Radius1", "Radius2", "Height"]),
    "torus": ("Part::Torus", ["Radius1", "Radius2"]),
}
BOOLEANS = {"cut": "Part::Cut", "fuse": "Part::Fuse", "common": "Part::Common"}


class OpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Document lifecycle
# --------------------------------------------------------------------------

DOC_PATH = None
doc = None


def open_document(path):
    global DOC_PATH, doc
    DOC_PATH = os.path.abspath(path)
    if os.path.exists(DOC_PATH):
        doc = FreeCAD.openDocument(DOC_PATH)
    else:
        doc = FreeCAD.newDocument("CadSwarm")
    doc.UndoMode = 1  # required: without it abortTransaction() silently keeps changes
    if doc.getObject(PARAMS) is None:
        params_sheet()
        doc.recompute()
        save()


def params_sheet():
    """The shared Params sheet, made again if it is missing (a model from before delete refused it)."""
    sheet = doc.getObject(PARAMS)
    if sheet is None:
        sheet = doc.addObject("Spreadsheet::Sheet", PARAMS)
        sheet.set("A1", "name")
        sheet.set("B1", "value")
    return sheet


SAVE_ATTEMPTS = 6
SAVE_BACKOFF = 0.15  # seconds, growing: ~2s in all before giving up


def save():
    # saveCopy + replace keeps the on-disk model whole even if we die mid-write.
    tmp = DOC_PATH + ".tmp.FCStd"
    doc.saveCopy(tmp)
    # On Windows the replace fails while anything (OneDrive, antivirus, a download of the model) holds
    # the target open; those holds are brief, so wait them out before giving up.
    for attempt in range(SAVE_ATTEMPTS):
        try:
            os.replace(tmp, DOC_PATH)
            return
        except PermissionError as e:
            if attempt == SAVE_ATTEMPTS - 1:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise OpError("save_failed", f"Could not write {os.path.basename(DOC_PATH)} ({e}); another "
                              "program probably has it open. Your change was rolled back. Try again.")
            time.sleep(SAVE_BACKOFF * (attempt + 1))


# --------------------------------------------------------------------------
# Fingerprints: what counts as "this op changed that object"
# --------------------------------------------------------------------------

def mesh_sig(mesh):
    bb = mesh.BoundBox
    return (mesh.CountPoints, mesh.CountFacets, round(mesh.Volume, 3),
            tuple(round(v, 4) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)))


def shape_sig(shape):
    if shape is None or shape.isNull():
        return None
    bb = shape.BoundBox
    vol = shape.Volume if shape.Solids else 0.0
    return (round(vol, 6), round(shape.Area, 6), tuple(round(v, 6) for v in
            (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)), len(shape.Faces))


def value_repr(v):
    if isinstance(v, FreeCAD.DocumentObject):
        return "obj:" + v.Name
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(value_repr(x) for x in v) + "]"
    if isinstance(v, Part.Shape):
        return repr(shape_sig(v))
    if hasattr(v, "CountFacets") and hasattr(v, "Topology"):  # a Mesh.Mesh: never print its triangles
        return repr(mesh_sig(v))
    text = repr(v)
    if ADDRESS.search(text):
        # e.g. ShapeMaterial reprs as '<Material at 0000029A...>' and gets a new
        # address on every recompute; compare by stable identity instead.
        ident = getattr(v, "UUID", None) or getattr(v, "Name", None)
        return f"{type(v).__name__}:{ident}" if ident else ADDRESS.sub("", text)
    return text


def expression_paths(obj):
    """Root property names driven by an expression ('.Placement.Base.x' -> 'Placement').

    A bound property's value is derived (a Params change re-evaluates it), so it
    is excluded from the primary fingerprint; re-binding it still counts as an
    edit because the ExpressionEngine itself is fingerprinted.
    """
    return {path.lstrip(".").split(".")[0] for path, _ in (obj.ExpressionEngine or [])}


def sheet_contents(sheet):
    return tuple((c, sheet.getContents(c)) for c in sorted(sheet.getUsedCells()))


def primary_fp(obj):
    """Fingerprint of everything a caller sets directly on `obj`."""
    bound = expression_paths(obj)
    parts = [obj.TypeId, repr(sorted(obj.ExpressionEngine or []))]
    for prop in sorted(obj.PropertiesList):
        if prop in IGNORED_PROPS or prop in bound:
            continue
        try:
            parts.append((prop, value_repr(obj.getPropertyByName(prop))))
        except Exception:
            continue
    if obj.TypeId == "Part::Feature" and not getattr(obj, "CadSwarmKind", ""):
        # Plain Part::Feature geometry (imports / polygon extrusion) is directly authored; linked
        # CADSwarm pattern outputs carry their source and dimensions as primary data instead.
        parts.append(("Shape", repr(shape_sig(obj.Shape))))
    if obj.TypeId == "Spreadsheet::Sheet":
        parts.append(("cells", repr(sheet_contents(obj))))
    if obj.TypeId.startswith("Mesh::") and hasattr(obj, "Mesh"):
        parts.append(("Mesh", repr(mesh_sig(obj.Mesh))))
    return repr(parts)


def derived_fp(obj):
    sig = shape_sig(obj.Shape) if hasattr(obj, "Shape") else None
    bound = tuple(sorted((p, value_repr(getattr(obj, p, None))) for p in expression_paths(obj)))
    return repr((sig, bound))


def snapshot():
    return {o.Name: (primary_fp(o), derived_fp(o)) for o in doc.Objects}


def is_broken(obj):
    state = obj.State or []
    return "Invalid" in state or "Error" in state


# --------------------------------------------------------------------------
# Argument helpers
# --------------------------------------------------------------------------

def get_obj(name):
    obj = doc.getObject(name)
    if obj is None:
        raise OpError("not_found", f"No object named {name!r}")
    return obj


def new_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name, re.ASCII):
        raise OpError("bad_args", "name must start with an ASCII letter and contain only letters, numbers or underscores")
    if doc.getObject(name) is not None:
        raise OpError("exists", f"An object named {name!r} already exists")
    return name


def to_placement(spec):
    if spec is None:
        return None
    if isinstance(spec, dict):
        base = Vector(*spec.get("base", [0, 0, 0]))
        rot = spec.get("rotation") or {}
        if "ypr" in rot:
            r = Rotation(*rot["ypr"])
        else:
            r = Rotation(Vector(*rot.get("axis", [0, 0, 1])), float(rot.get("angle", 0)))
        return Placement(base, r)
    raise OpError("bad_args", "placement must be {base:[x,y,z], rotation:{axis:[..],angle:deg} | {ypr:[y,p,r]}}")


def placement_json(p):
    return {"base": [p.Base.x, p.Base.y, p.Base.z],
            "rotation": {"axis": [p.Rotation.Axis.x, p.Rotation.Axis.y, p.Rotation.Axis.z],
                         "angle": math.degrees(p.Rotation.Angle)}}


def coerce(obj, prop, value):
    current = obj.getPropertyByName(prop)
    if isinstance(current, FreeCAD.Placement):
        return to_placement(value)
    if isinstance(current, FreeCAD.Vector):
        return Vector(*value)
    if isinstance(value, str) and doc.getObject(value) is not None and \
            obj.getTypeIdOfProperty(prop) in ("App::PropertyLink", "App::PropertyLinkGlobal"):
        return doc.getObject(value)
    return value


def require_allowed(names, allowed):
    """Check that the caller holds locks for every existing feature this op consumes or edits."""
    if allowed is None:
        return
    required = set()
    for name in names:
        if not name:
            continue
        obj = doc.getObject(name)
        owner = getattr(obj, "CadSwarmInternalOwner", "") if obj is not None and is_internal(obj) else ""
        required.add(owner or name)
    missing = sorted(required - set(allowed))
    if missing:
        raise OpError("lock_violation", f"This operation needs locks on {missing}. Lock them first or ask their owner.")


def is_internal(obj):
    return bool(obj is not None and getattr(obj, "CadSwarmInternal", False))


def _property_name(obj, prop):
    """Accept the shared API's lower-case dimension names as well as FreeCAD property names."""
    raw = str(prop)
    kind = str(getattr(obj, "CadSwarmKind", "") or "")
    if kind:
        aliases = {
            "extrude": {"depth": "CadSwarmDepth"},
            "pocket": {"depth": "CadSwarmDepth"},
            "hole": {"depth": "CadSwarmDepth"},
            "fillet": {"radius": "CadSwarmRadius"},
            "chamfer": {"radius": "CadSwarmRadius"},
            "linear_pattern": {"count": "CadSwarmCount", "spacing": "CadSwarmSpacing",
                               "direction": "CadSwarmDirection"},
            "circular_pattern": {"count": "CadSwarmCount", "angle": "CadSwarmAngle",
                                 "axis": "CadSwarmAxis"},
            "revolve": {"axis": "CadSwarmAxisName", "angle": "Angle"},
        }.get(kind, {})
        mapped = aliases.get(raw.lower())
        if mapped:
            return mapped
    if raw in obj.PropertiesList:
        return raw
    aliases = {"length": "Length", "width": "Width", "height": "Height", "radius": "Radius",
               "angle": "Angle", "axis": "Axis", "placement": "Placement", "label": "Label"}
    mapped = aliases.get(raw.lower())
    return mapped if mapped in obj.PropertiesList else raw


def apply_props(obj, props, expressions):
    for prop, value in (props or {}).items():
        actual = _property_name(obj, prop)
        if actual not in obj.PropertiesList:
            raise OpError("bad_args", f"{obj.TypeId} has no property {prop!r}")
        setattr(obj, actual, coerce(obj, actual, value))
    for prop, expr in (expressions or {}).items():
        actual = _property_name(obj, prop)
        if actual not in obj.PropertiesList:
            raise OpError("bad_args", f"{obj.TypeId} has no property {prop!r}")
        obj.setExpression(actual, expr)


def add_meta(obj, type_name, name, value, description="", hidden=True):
    if name not in obj.PropertiesList:
        obj.addProperty(type_name, name, "CADSwarm", description)
    setattr(obj, name, value)
    if hidden:
        obj.setEditorMode(name, 2)


def add_internal(obj, owner=""):
    add_meta(obj, "App::PropertyBool", "CadSwarmInternal", True, "Internal helper used by a CADSwarm feature")
    if owner:
        add_meta(obj, "App::PropertyString", "CadSwarmInternalOwner", owner,
                 "Feature that owns this internal helper")


def sketch_plane(plane):
    plane = str(plane or "").upper()
    if plane == "XY":
        return plane, Rotation()
    if plane == "XZ":
        return plane, Rotation(Vector(1, 0, 0), 90)
    if plane == "YZ":
        # Local sketch X/Y map to world Y/Z, with the normal along world X.
        return plane, Rotation(Vector(1, 1, 1), 120)
    raise OpError("bad_args", "Sketch plane must be XY, XZ or YZ.")


def sketch_normal(sketch):
    return sketch.Placement.Rotation.multVec(Vector(0, 0, 1))


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OpError("bad_args", f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise OpError("bad_args", f"{label} must be a finite number")
    return number


def _entity_geometry(entity):
    if not isinstance(entity, dict):
        raise OpError("bad_args", "Sketch entities must be objects")
    kind = str(entity.get("type", "")).lower()
    if kind == "line":
        start, end = entity.get("start"), entity.get("end")
        if not isinstance(start, (list, tuple)) or len(start) != 2 or not isinstance(end, (list, tuple)) or len(end) != 2:
            raise OpError("bad_args", "Line entities need two-coordinate start and end points")
        return kind, Part.LineSegment(Vector(_finite(start[0], "x"), _finite(start[1], "y"), 0),
                                      Vector(_finite(end[0], "x"), _finite(end[1], "y"), 0))
    if kind in ("circle", "arc"):
        center = entity.get("center")
        if not isinstance(center, (list, tuple)) or len(center) != 2:
            raise OpError("bad_args", f"{kind.title()} entities need a two-coordinate center")
        cx, cy = _finite(center[0], "center x"), _finite(center[1], "center y")
        radius = _finite(entity.get("radius"), "radius")
        if radius <= 0:
            raise OpError("bad_args", f"{kind.title()} radius must be greater than zero")
        circle = Part.Circle(Vector(cx, cy, 0), Vector(0, 0, 1), radius)
        if kind == "circle":
            return kind, circle
        start = _finite(entity.get("start_angle"), "arc start angle")
        end = _finite(entity.get("end_angle"), "arc end angle")
        if abs(end - start) <= 1e-12:
            raise OpError("bad_args", "Arc start and end angles must be different")
        return kind, Part.ArcOfCircle(circle, start, end)
    raise OpError("unsupported", f"Sketch geometry type {kind!r} is not supported; use line, circle or arc")


def _point_position(entity_kind, point_name, reference):
    valid = {"line": {"start": 1, "end": 2}, "circle": {"center": 3},
             "arc": {"start": 1, "end": 2, "center": 3}}
    if point_name not in valid.get(entity_kind, {}):
        raise OpError("bad_args", f"{reference!r} is not a supported point on this sketch entity")
    return valid[entity_kind][point_name]


def _sketch_point(reference, index_by_id, kind_by_id):
    match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)\.(start|end|center)", str(reference or ""), re.ASCII)
    if not match or match.group(1) not in index_by_id:
        raise OpError("bad_args", f"Invalid sketch point reference: {reference!r}")
    entity_id, point_name = match.groups()
    return index_by_id[entity_id], _point_position(kind_by_id[entity_id], point_name, reference)


def _constraint_specs(constraint, index_by_id, kind_by_id):
    if not isinstance(constraint, dict):
        raise OpError("bad_args", "Sketch constraints must be objects")
    kind = str(constraint.get("type", "")).lower()
    refs = constraint.get("points") or []
    entities = constraint.get("entities") or []
    value = constraint.get("value")
    try:
        if kind == "coincident" and len(refs) == 2:
            a = _sketch_point(refs[0], index_by_id, kind_by_id)
            b = _sketch_point(refs[1], index_by_id, kind_by_id)
            return [Sketcher.Constraint("Coincident", a[0], a[1], b[0], b[1])]
        if kind in ("horizontal", "vertical") and len(entities) == 1:
            geo = index_by_id.get(str(entities[0]))
            if geo is None or kind_by_id[str(entities[0])] != "line":
                raise OpError("bad_args", f"{kind.title()} needs one line entity")
            return [Sketcher.Constraint(kind.title(), geo)]
        if kind in ("parallel", "perpendicular", "equal", "angle") and len(entities) == 2:
            names = [str(e) for e in entities]
            if any(name not in index_by_id or kind_by_id[name] != "line" for name in names):
                raise OpError("bad_args", f"{kind.title()} needs two line entities")
            if kind == "angle":
                return [Sketcher.Constraint("Angle", index_by_id[names[0]], index_by_id[names[1]],
                                             _finite(value, "angle"))]
            return [Sketcher.Constraint(kind.title(), index_by_id[names[0]], index_by_id[names[1]])]
        if kind == "distance" and len(refs) == 2:
            a = _sketch_point(refs[0], index_by_id, kind_by_id)
            b = _sketch_point(refs[1], index_by_id, kind_by_id)
            distance = _finite(value, "distance")
            if distance < 0:
                raise OpError("bad_args", "Distance must be non-negative")
            return [Sketcher.Constraint("Distance", a[0], a[1], b[0], b[1], distance)]
        if kind in ("radius", "diameter") and len(entities) == 1:
            entity_id = str(entities[0])
            if entity_id not in index_by_id or kind_by_id[entity_id] not in ("circle", "arc"):
                raise OpError("bad_args", f"{kind.title()} needs one circle or arc entity")
            number = _finite(value, kind)
            if number <= 0:
                raise OpError("bad_args", f"{kind.title()} must be greater than zero")
            return [Sketcher.Constraint(kind.title(), index_by_id[entity_id], number)]
        if kind == "concentric" and len(entities) == 2:
            names = [str(e) for e in entities]
            if any(name not in index_by_id or kind_by_id[name] not in ("circle", "arc") for name in names):
                raise OpError("bad_args", "Concentric needs two circle or arc entities")
            return [Sketcher.Constraint("Coincident", index_by_id[names[0]], 3, index_by_id[names[1]], 3)]
        if kind == "fixed" and len(refs) == 1:
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise OpError("bad_args", "Fixed point value must be [x, y]")
            geo, pos = _sketch_point(refs[0], index_by_id, kind_by_id)
            x, y = _finite(value[0], "fixed x"), _finite(value[1], "fixed y")
            return [Sketcher.Constraint("DistanceX", geo, pos, -1, 1, x),
                    Sketcher.Constraint("DistanceY", geo, pos, -1, 1, y)]
    except OpError:
        raise
    except Exception as exc:
        raise OpError("bad_args", f"Invalid {kind or 'unknown'} sketch constraint: {exc}") from exc
    raise OpError("unsupported", f"Sketch constraint {kind!r} is not supported or has invalid references")


def _sketch_solver(sketch):
    try:
        dof = int(sketch.DoF)
    except Exception:
        dof = None
    conflicting = list(getattr(sketch, "ConflictingConstraints", []) or [])
    malformed = list(getattr(sketch, "MalformedConstraints", []) or [])
    redundant = list(getattr(sketch, "RedundantConstraints", []) or [])
    if malformed:
        status = "malformed"
    elif conflicting:
        status = "conflicting"
    elif redundant:
        status = "redundant"
    elif bool(getattr(sketch, "FullyConstrained", False)):
        status = "fully_constrained"
    else:
        status = "underconstrained"
    return {"status": status, "dof": dof, "conflicting": conflicting,
            "malformed": malformed, "redundant": redundant}


def _sketch_definition(sketch):
    try:
        entities = json.loads(sketch.CadSwarmEntities or "[]")
        constraints = json.loads(sketch.CadSwarmConstraints or "[]")
    except Exception:
        entities, constraints = [], []
    for index, entity in enumerate(entities):
        if index >= sketch.GeometryCount:
            break
        geometry = sketch.Geometry[index]
        if entity.get("type") == "line":
            entity["start"] = [float(geometry.StartPoint.x), float(geometry.StartPoint.y)]
            entity["end"] = [float(geometry.EndPoint.x), float(geometry.EndPoint.y)]
        elif entity.get("type") == "circle":
            entity["center"] = [float(geometry.Center.x), float(geometry.Center.y)]
            entity["radius"] = float(geometry.Radius)
        elif entity.get("type") == "arc":
            entity["center"] = [float(geometry.Center.x), float(geometry.Center.y)]
            entity["radius"] = float(geometry.Radius)
            entity["start_angle"] = float(geometry.FirstParameter)
            entity["end_angle"] = float(geometry.LastParameter)
    return {"name": sketch.Name, "kind": "sketch", "plane": getattr(sketch, "CadSwarmPlane", "XY"),
            "entities": entities, "constraints": constraints, "solver": _sketch_solver(sketch)}


def _put_sketch_definition(sketch, entities, constraints):
    if not isinstance(entities, list) or not entities:
        raise OpError("bad_args", "A sketch needs at least one line, circle or arc")
    if not isinstance(constraints, list):
        raise OpError("bad_args", "Sketch constraints must be a list")
    index_by_id, kind_by_id = {}, {}
    geometries = []
    for entity in entities:
        entity_id = entity.get("id") if isinstance(entity, dict) else None
        if not isinstance(entity_id, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", entity_id, re.ASCII) or entity_id in index_by_id:
            raise OpError("bad_args", "Every sketch entity needs a unique ASCII id starting with a letter")
        kind, geometry = _entity_geometry(entity)
        index_by_id[entity_id] = len(geometries)
        kind_by_id[entity_id] = kind
        geometries.append(geometry)
    specs = []
    constraint_ids = set()
    for constraint in constraints:
        constraint_id = constraint.get("id") if isinstance(constraint, dict) else None
        if constraint_id is not None:
            if not isinstance(constraint_id, str) or constraint_id in constraint_ids:
                raise OpError("bad_args", "Constraint ids must be unique strings")
            constraint_ids.add(constraint_id)
        specs.extend(_constraint_specs(constraint, index_by_id, kind_by_id))
    try:
        for index in reversed(range(sketch.ConstraintCount)):
            sketch.delConstraint(index)
        for index in reversed(range(sketch.GeometryCount)):
            sketch.delGeometry(index)
        for geometry in geometries:
            sketch.addGeometry(geometry, False)
        for constraint in specs:
            sketch.addConstraint(constraint)
        sketch.CadSwarmEntities = json.dumps(entities, separators=(",", ":"))
        sketch.CadSwarmConstraints = json.dumps(constraints, separators=(",", ":"))
    except Exception as exc:
        raise OpError("bad_args", f"FreeCAD could not build the sketch: {exc}") from exc


def _feature_args(kind, params):
    if not isinstance(params, dict):
        raise OpError("bad_args", "Feature parameters must be an object")
    out = dict(params)
    if kind in ("extrude", "pocket", "hole"):
        depth = _finite(out.get("depth", 10 if kind != "hole" else 10000), "depth")
        if depth <= 0:
            raise OpError("bad_args", "Depth must be greater than zero")
        out["depth"] = depth
    elif kind in ("fillet", "chamfer"):
        radius = _finite(out.get("radius", 1), "radius")
        if radius <= 0:
            raise OpError("bad_args", "Radius must be greater than zero")
        out["radius"] = radius
    elif kind == "revolve":
        angle = _finite(out.get("angle", 360), "angle")
        axis = str(out.get("axis", "Z")).upper()
        if not 0 < angle <= 360 or axis not in ("X", "Y", "Z"):
            raise OpError("bad_args", "Revolve angle must be in (0, 360] and axis must be X, Y or Z")
        out.update(angle=angle, axis=axis)
    elif kind == "linear_pattern":
        count = out.get("count", 2)
        try:
            parsed_count = int(count)
        except (TypeError, ValueError, OverflowError) as exc:
            raise OpError("bad_args", "Pattern count must be an integer between 2 and 1000") from exc
        if isinstance(count, bool) or parsed_count != count or not 2 <= parsed_count <= 1000:
            raise OpError("bad_args", "Pattern count must be an integer between 2 and 1000")
        direction = out.get("direction", [1, 0, 0])
        if not isinstance(direction, (list, tuple)) or len(direction) != 3:
            raise OpError("bad_args", "Linear pattern direction must be a three-coordinate vector")
        direction = [_finite(x, "direction") for x in direction]
        if sum(x * x for x in direction) <= 1e-24:
            raise OpError("bad_args", "Linear pattern direction cannot be zero")
        spacing = _finite(out.get("spacing", 10), "spacing")
        out.update(count=parsed_count, direction=direction, spacing=spacing)
    elif kind == "circular_pattern":
        count = out.get("count", 2)
        try:
            parsed_count = int(count)
        except (TypeError, ValueError, OverflowError) as exc:
            raise OpError("bad_args", "Pattern count must be an integer between 2 and 1000") from exc
        if isinstance(count, bool) or parsed_count != count or not 2 <= parsed_count <= 1000:
            raise OpError("bad_args", "Pattern count must be an integer between 2 and 1000")
        axis = out.get("axis", [0, 0, 1])
        if not isinstance(axis, (list, tuple)) or len(axis) != 3:
            raise OpError("bad_args", "Circular pattern axis must be a three-coordinate vector")
        axis = [_finite(x, "axis") for x in axis]
        if sum(x * x for x in axis) <= 1e-24:
            raise OpError("bad_args", "Circular pattern axis cannot be zero")
        angle = _finite(out.get("angle", 360), "angle")
        out.update(count=parsed_count, axis=axis, angle=angle)
    return out


def _internal_name(target, suffix):
    base = "_CSW_" + re.sub(r"[^A-Za-z0-9_]", "_", target)[:48] + "_" + suffix
    name, counter = base, 1
    while doc.getObject(name) is not None:
        name = f"{base}_{counter}"
        counter += 1
    return name


def _make_cadswarm_feature(obj, kind, source=None, sketch=None, auxiliary=None):
    add_meta(obj, "App::PropertyString", "CadSwarmKind", kind, "CADSwarm feature type")
    if source is not None:
        add_meta(obj, "App::PropertyLink", "CadSwarmSource", source, "Input feature")
    if sketch is not None:
        add_meta(obj, "App::PropertyLink", "CadSwarmSketch", sketch, "Profile sketch")
    if auxiliary is not None:
        add_meta(obj, "App::PropertyLink", "CadSwarmAuxiliary", auxiliary, "Internal cutter feature")


def _rebuild_cadswarm_features():
    """Update persisted App::Link instances inside native Part::Compound pattern features."""
    for obj in [o for o in doc.Objects if getattr(o, "CadSwarmKind", "") in ("linear_pattern", "circular_pattern")]:
        source = getattr(obj, "CadSwarmSource", None)
        if source is None or not hasattr(source, "Shape") or source.Shape.isNull():
            raise OpError("recompute_error", f"Pattern {obj.Name} has a missing or empty source")
        count = int(obj.CadSwarmCount)
        links = list(getattr(obj, "CadSwarmInstances", []) or [])
        while len(links) > count:
            extra = links.pop()
            doc.removeObject(extra.Name)
        while len(links) < count:
            index = len(links)
            link = doc.addObject("App::Link", _internal_name(obj.Name, f"instance{index + 1}"))
            add_internal(link, obj.Name)
            add_meta(link, "App::PropertyInteger", "CadSwarmPatternIndex", index, "Zero-based instance index")
            link.setLink(source)
            link.Visibility = False
            links.append(link)
        if obj.CadSwarmKind == "linear_pattern":
            direction = obj.CadSwarmDirection
            if direction.Length <= 1e-12:
                raise OpError("bad_args", "Linear pattern direction cannot be zero")
            direction = direction.normalize()
            spacing = float(obj.CadSwarmSpacing)
            for index, link in enumerate(links):
                link.setLink(source)
                link.LinkPlacement = Placement(direction * spacing * index, Rotation())
        else:
            axis = obj.CadSwarmAxis
            if axis.Length <= 1e-12:
                raise OpError("bad_args", "Circular pattern axis cannot be zero")
            angle = float(obj.CadSwarmAngle)
            for index, link in enumerate(links):
                link.setLink(source)
                link.LinkPlacement = Placement(Vector(0, 0, 0), Rotation(axis, angle * index / count))
        obj.CadSwarmInstances = links
        obj.Links = links


def _rebuild_native_dimensions():
    """Apply CADSwarm's named parameters to native FreeCAD feature properties before recompute."""
    for obj in doc.Objects:
        kind = str(getattr(obj, "CadSwarmKind", "") or "")
        if kind == "extrude":
            sketch = getattr(obj, "CadSwarmSketch", None)
            if sketch is None:
                raise OpError("recompute_error", f"Extrude {obj.Name} has no profile sketch")
            obj.Dir = sketch_normal(sketch) * float(obj.CadSwarmDepth)
        elif kind in ("pocket", "hole"):
            sketch = getattr(obj, "CadSwarmSketch", None)
            tool = getattr(obj, "CadSwarmAuxiliary", None)
            if sketch is None or tool is None:
                raise OpError("recompute_error", f"{kind.title()} {obj.Name} has no profile or cutter")
            tool.Dir = sketch_normal(sketch) * -float(obj.CadSwarmDepth)
        elif kind == "revolve":
            axes = {"X": Vector(1, 0, 0), "Y": Vector(0, 1, 0), "Z": Vector(0, 0, 1)}
            axis_name = str(obj.CadSwarmAxisName).upper()
            if axis_name not in axes:
                raise OpError("bad_args", "Revolution axis must be X, Y or Z")
            obj.Axis = axes[axis_name]
        elif kind in ("fillet", "chamfer"):
            source = getattr(obj, "CadSwarmSource", None)
            if source is None or not hasattr(source, "Shape"):
                raise OpError("recompute_error", f"{kind.title()} {obj.Name} has no source feature")
            selected = json.loads(obj.CadSwarmSelection or "[]")
            count = len(source.Shape.Edges)
            indexes = selected if selected else list(range(count))
            if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= count for i in indexes):
                raise OpError("bad_args", f"{kind.title()} selection no longer matches the source edges")
            radius = float(obj.CadSwarmRadius)
            obj.Base = source
            obj.Edges = [(i + 1, radius, radius) for i in indexes]


# --------------------------------------------------------------------------
# Mutating ops (run inside a transaction by `mutate`)
# --------------------------------------------------------------------------

def op_create_primitive(a, allowed=None):
    kind = str(a.get("kind", "")).lower()
    if kind not in PRIMITIVES:
        raise OpError("bad_args", f"kind must be one of {sorted(PRIMITIVES)}")
    obj = doc.addObject(PRIMITIVES[kind][0], new_name(a.get("name")))
    apply_props(obj, a.get("props"), a.get("expressions"))
    if a.get("placement"):
        obj.Placement = to_placement(a["placement"])
    if a.get("label"):
        obj.Label = a["label"]
    return {"name": obj.Name}


def op_create_extrusion(a, allowed=None):
    """Closed XY polygon (+ optional round holes) extruded along Z: a static Part::Feature."""
    pts = a.get("points") or []
    if len(pts) < 3:
        raise OpError("bad_args", "points needs at least 3 [x, y] pairs")
    vecs = [Vector(float(x), float(y), 0) for x, y in pts]
    wire = Part.makePolygon(vecs + [vecs[0]])
    face = Part.Face(wire)
    for h in a.get("holes") or []:
        circle = Part.Face(Part.Wire(Part.makeCircle(float(h["r"]), Vector(float(h["x"]), float(h["y"]), 0))))
        face = face.cut(circle)
    solid = face.extrude(Vector(0, 0, float(a.get("height", 1))))
    obj = doc.addObject("Part::Feature", new_name(a.get("name")))
    obj.Shape = solid
    if a.get("placement"):
        obj.Placement = to_placement(a["placement"])
    if a.get("label"):
        obj.Label = a["label"]
    return {"name": obj.Name, "volume": solid.Volume}


def op_boolean(a, allowed=None):
    kind = str(a.get("kind", "")).lower()
    if kind not in BOOLEANS:
        raise OpError("bad_args", f"kind must be one of {sorted(BOOLEANS)}")
    base, tool = get_obj(a.get("base")), get_obj(a.get("tool"))
    require_allowed((base.Name, tool.Name), allowed)
    obj = doc.addObject(BOOLEANS[kind], new_name(a.get("name")))
    obj.Base, obj.Tool = base, tool
    base.Visibility = tool.Visibility = False
    return {"name": obj.Name, "consumed": [base.Name, tool.Name]}


def op_set_property(a, allowed=None):
    obj = get_obj(a.get("name"))
    require_allowed((obj.Name,), allowed)
    apply_props(obj, {a["prop"]: a.get("value")}, None)
    return {"name": obj.Name}


def op_set_props(a, allowed=None):
    obj = get_obj(a.get("name"))
    require_allowed((obj.Name,), allowed)
    apply_props(obj, a.get("props"), a.get("expressions"))
    return {"name": obj.Name}


def op_set_expression(a, allowed=None):
    obj = get_obj(a.get("name"))
    require_allowed((obj.Name,), allowed)
    prop = _property_name(obj, a["prop"])
    if prop not in obj.PropertiesList:
        raise OpError("bad_args", f"{obj.TypeId} has no property {a['prop']!r}")
    obj.setExpression(prop, a.get("expr"))  # None clears the binding
    return {"name": obj.Name}


def op_set_placement(a, allowed=None):
    obj = get_obj(a.get("name"))
    require_allowed((obj.Name,), allowed)
    obj.Placement = to_placement(a.get("placement"))
    return {"name": obj.Name, "placement": placement_json(obj.Placement)}


def _dependent_delete_order(obj):
    order, visiting, seen = [], set(), set()

    def visit(current):
        if current.Name in seen:
            return
        if current.Name in visiting:
            raise OpError("in_use", "The feature dependency graph contains a cycle")
        visiting.add(current.Name)
        for user in current.InList:
            if user.Name != PARAMS:
                visit(user)
        visiting.remove(current.Name)
        seen.add(current.Name)
        order.append(current.Name)

    visit(obj)
    return order


def op_delete(a, allowed=None):
    obj = get_obj(a.get("name"))
    if obj.Name == PARAMS:
        # Every part binds its sizes to it: a helper deleted it twice in one demo, and every set_param
        # after that failed with not_found until the worker restarted.
        raise OpError("not_allowed", "Params is the team's shared dimensions sheet and can't be deleted; "
                                     "change a value with set_param instead")
    users = [o.Name for o in obj.InList if o.Name != PARAMS]
    if users and not a.get("force"):
        raise OpError("in_use", f"{obj.Name} is used by {users}; delete those first or pass force")
    names = _dependent_delete_order(obj) if a.get("force") else [obj.Name]
    require_allowed(names, allowed)
    for name in names:
        if doc.getObject(name) is None:
            continue
        owned_helpers = [o.Name for o in doc.Objects
                         if is_internal(o) and getattr(o, "CadSwarmInternalOwner", "") == name]
        for helper in owned_helpers:
            doc.removeObject(helper)
        doc.removeObject(name)
    return {"name": a["name"]}


def find_param_row(sheet, alias):
    for cell in sheet.getUsedCells():
        if cell.startswith("B") and sheet.getAlias(cell) == alias:
            return int(cell[1:])
    return None


def op_set_param(a, allowed=None):
    alias = str(a.get("alias", ""))
    require_allowed((PARAMS,), allowed)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", alias, re.ASCII):
        raise OpError("bad_args", "alias must be an identifier like hole_pitch")
    sheet = params_sheet()
    row = find_param_row(sheet, alias)
    if row is None:
        rows = [int(c[1:]) for c in sheet.getUsedCells() if c[1:].isdigit()]
        row = max(rows + [1]) + 1
        sheet.set(f"A{row}", alias)
        sheet.set(f"B{row}", str(a.get("value")))
        sheet.setAlias(f"B{row}", alias)
    else:
        sheet.set(f"B{row}", str(a.get("value")))
    if a.get("note"):
        sheet.set(f"C{row}", str(a["note"]))
    return {"alias": alias, "cell": f"B{row}"}


def op_create_sketch(a, allowed=None):
    name = new_name(a.get("name"))
    plane, rotation = sketch_plane(a.get("plane") or "XY")
    sketch = doc.addObject("Sketcher::SketchObject", name)
    sketch.Placement = Placement(Vector(0, 0, 0), rotation)
    add_meta(sketch, "App::PropertyString", "CadSwarmPlane", plane, "Sketch coordinate plane")
    add_meta(sketch, "App::PropertyString", "CadSwarmEntities", "[]", "Stable sketch entity definitions")
    add_meta(sketch, "App::PropertyString", "CadSwarmConstraints", "[]", "Stable sketch constraint definitions")
    _put_sketch_definition(sketch, a.get("entities") or [], a.get("constraints") or [])
    if a.get("label"):
        sketch.Label = str(a["label"])
    doc.recompute()
    solver = _sketch_solver(sketch)
    if solver["conflicting"] or solver["malformed"]:
        raise OpError("recompute_error", f"Sketch constraints did not solve: {solver}")
    return {"name": sketch.Name, "feature": _sketch_definition(sketch)}


def op_edit_sketch(a, allowed=None):
    sketch = get_obj(a.get("name"))
    require_allowed((sketch.Name,), allowed)
    if sketch.TypeId != "Sketcher::SketchObject":
        raise OpError("bad_args", f"{sketch.Name!r} is not a sketch")
    current = _sketch_definition(sketch)
    entities = current["entities"] if a.get("entities") is None else a.get("entities")
    constraints = current["constraints"] if a.get("constraints") is None else a.get("constraints")
    _put_sketch_definition(sketch, entities, constraints)
    doc.recompute()
    solver = _sketch_solver(sketch)
    if solver["conflicting"] or solver["malformed"]:
        raise OpError("recompute_error", f"Sketch constraints did not solve: {solver}")
    return {"name": sketch.Name, "feature": _sketch_definition(sketch)}


def op_create_sketch_feature(a, allowed=None):
    kind = str(a.get("kind", "")).lower()
    if kind not in ("extrude", "revolve", "pocket", "hole", "fillet", "chamfer", "linear_pattern", "circular_pattern"):
        raise OpError("unsupported", f"Sketch feature kind {kind!r} is not supported")
    name = new_name(a.get("name"))
    params = _feature_args(kind, a.get("parameters") or {})
    selection = a.get("selection") or []
    if not isinstance(selection, list) or any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in selection):
        raise OpError("bad_args", "Edge selection must be a list of non-negative zero-based indices")

    sketch = None
    source = None
    if kind in ("extrude", "revolve", "pocket", "hole"):
        sketch = get_obj(a.get("sketch_name"))
        if sketch.TypeId != "Sketcher::SketchObject":
            raise OpError("bad_args", f"{sketch.Name!r} is not a sketch")
    if kind in ("pocket", "hole", "fillet", "chamfer", "linear_pattern", "circular_pattern"):
        source = get_obj(a.get("source_name"))
        if not hasattr(source, "Shape") or source.Shape.isNull():
            raise OpError("bad_args", f"{source.Name} has no shape to use as a feature source")
    require_allowed([x.Name for x in (sketch, source) if x is not None], allowed)

    if kind == "extrude":
        feature = doc.addObject("Part::Extrusion", name)
        feature.Base = sketch
        feature.Solid = True
        _make_cadswarm_feature(feature, kind, sketch=sketch)
        add_meta(feature, "App::PropertyLength", "CadSwarmDepth", params["depth"], "Extrusion distance")
    elif kind == "revolve":
        plane = str(getattr(sketch, "CadSwarmPlane", "XY"))
        axes = {"XY": ("X", "Y"), "XZ": ("X", "Z"), "YZ": ("Y", "Z")}
        if params["axis"] not in axes.get(plane, ()):
            raise OpError("bad_args", f"Revolve axis {params['axis']} must lie in the {plane} sketch plane")
        feature = doc.addObject("Part::Revolution", name)
        feature.Source = sketch
        feature.Base = Vector(0, 0, 0)
        feature.Axis = Vector(*{"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}[params["axis"]])
        feature.Angle = params["angle"]
        feature.Solid = True
        _make_cadswarm_feature(feature, kind, sketch=sketch)
        add_meta(feature, "App::PropertyString", "CadSwarmAxisName", params["axis"], "Revolution axis")
    elif kind in ("pocket", "hole"):
        tool = doc.addObject("Part::Extrusion", _internal_name(name, "cutter"))
        add_internal(tool, name)
        tool.Base = sketch
        tool.Solid = True
        tool.Dir = sketch_normal(sketch) * -params["depth"]
        tool.Visibility = False
        feature = doc.addObject("Part::Cut", name)
        feature.Base, feature.Tool = source, tool
        _make_cadswarm_feature(feature, kind, source=source, sketch=sketch, auxiliary=tool)
        add_meta(feature, "App::PropertyLength", "CadSwarmDepth", params["depth"], "Cut depth")
    elif kind in ("fillet", "chamfer"):
        feature = doc.addObject("Part::Fillet" if kind == "fillet" else "Part::Chamfer", name)
        _make_cadswarm_feature(feature, kind, source=source)
        add_meta(feature, "App::PropertyLength", "CadSwarmRadius", params["radius"], "Dress-up size")
        add_meta(feature, "App::PropertyString", "CadSwarmSelection", json.dumps(selection), "Zero-based source edge indices")
    else:
        feature = doc.addObject("Part::Compound", name)
        _make_cadswarm_feature(feature, kind, source=source)
        add_meta(feature, "App::PropertyLinkList", "CadSwarmInstances", [], "Pattern instances")
        add_meta(feature, "App::PropertyInteger", "CadSwarmCount", params["count"], "Pattern occurrence count")
        if kind == "linear_pattern":
            add_meta(feature, "App::PropertyLength", "CadSwarmSpacing", params["spacing"], "Pattern spacing")
            add_meta(feature, "App::PropertyVector", "CadSwarmDirection", Vector(*params["direction"]), "Pattern direction")
        else:
            add_meta(feature, "App::PropertyAngle", "CadSwarmAngle", params["angle"], "Pattern sweep angle")
            add_meta(feature, "App::PropertyVector", "CadSwarmAxis", Vector(*params["axis"]), "Pattern axis")
    if a.get("label"):
        feature.Label = str(a["label"])
    if sketch is not None:
        sketch.Visibility = False
    if source is not None:
        source.Visibility = False
    return {"name": feature.Name, "feature": {"name": feature.Name, "kind": kind,
            "parameters": params, "selection": selection,
            "sketch_name": sketch.Name if sketch else None, "source_name": source.Name if source else None}}


def op_run_part_script(a):
    """Run agent Python in a confined child against a snapshot of the doc; its `shape` becomes the
    Part::Feature `name` here. The child can't touch the live doc: only the BREP and `result` come back."""
    code = a.get("code", "")
    try:
        reply = sandbox.run_script(sys.executable, doc.saveCopy, {
            "code": code, "params": params_dict(),
            # test hook: prove the OS boundary on its own, with the in-script guards off
            "guards": not os.environ.get("CADSWARM_TEST_NO_AST_GUARD")})
    except sandbox.SandboxError as e:
        raise OpError(e.code, str(e))
    if not reply.get("ok"):
        raise OpError(reply.get("code") or "script_error", reply.get("error") or "script failed")
    out = {}
    if reply.get("brep"):
        shape = Part.Shape()
        shape.importBrepFromString(reply["brep"])
        if shape.isNull():
            raise OpError("bad_args", "`shape` must be a non-null Part.Shape")
        name = a.get("name")
        obj = doc.getObject(name) if name else None
        if obj is None:
            obj = doc.addObject("Part::Feature", new_name(name))
        elif obj.TypeId != "Part::Feature":
            raise OpError("bad_args", f"{name} is a {obj.TypeId}; scripts can only replace Part::Feature shapes")
        obj.Shape = shape
        if a.get("label"):
            obj.Label = a["label"]
        out = {"name": obj.Name, "volume": shape.Volume if shape.Solids else 0.0}
    if reply.get("result") is not None:
        out["result"] = reply["result"]
    return out



# --------------------------------------------------------------------------
# Components library: measure a part file, place it, or build a standard part
# --------------------------------------------------------------------------

LIB_ID = re.compile(r"^c_[a-z0-9_]{1,46}$")
STANDARD_PARTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "library",
                              "standard_parts.json")
LIB_FORMATS = {".step": "step", ".stp": "step", ".fcstd": "fcstd", ".stl": "stl"}
MAX_STL_FACETS = 60_000  # bigger meshes become a mesh-backed shape too slow to fuse or check
_standard = None


def setting(a, key, default):
    """A team setting's number from the host's `_settings` rows ({"setting", "num", "text"})."""
    for r in a.get("_settings") or []:
        if r.get("setting") == key:
            try:
                return float(r.get("num"))
            except (TypeError, ValueError):
                return default
    return default


def library_file(cid):
    """The part file of library component `cid` (library/<id>/part.* next to the model)."""
    if not isinstance(cid, str) or not LIB_ID.match(cid):
        raise OpError("bad_args", f"{cid!r} is not a library part id")
    folder = os.path.join(os.path.dirname(DOC_PATH), "library", cid)
    if os.path.isdir(folder):
        for f in sorted(os.listdir(folder)):
            stem, ext = os.path.splitext(f)
            if stem == "part" and ext.lower() in LIB_FORMATS:
                return os.path.join(folder, f), LIB_FORMATS[ext.lower()]
    raise OpError("not_found", f"Library part {cid} has no file on the host")


def load_part_shape(path, fmt):
    """One shape for a STEP, STL or FreeCAD file (an FCStd contributes its top-level solids)."""
    if fmt == "step":
        shape = Part.Shape()
        shape.read(path)
    elif fmt == "stl":
        import Mesh
        mesh = Mesh.Mesh(path)
        if mesh.CountFacets > MAX_STL_FACETS:
            raise OpError("too_complex", f"That STL has {mesh.CountFacets} triangles; CADSwarm places STLs up to "
                                         f"{MAX_STL_FACETS}. Use the STEP file if there is one.")
        shape = Part.Shape()
        shape.makeShapeFromMesh(mesh.Topology, 0.05)
        try:
            shape = Part.Solid(Part.Shell(shape.Faces))
        except Exception:
            pass  # an open mesh stays a shell: placeable and visible, but has no volume
    elif fmt == "fcstd":
        other = FreeCAD.openDocument(path, True)
        try:
            tops = [o for o in other.Objects if hasattr(o, "Shape") and not o.Shape.isNull() and o.Shape.Solids
                    and not [u for u in o.InList if hasattr(u, "Shape")]]
            if not tops:
                raise OpError("bad_args", "That FreeCAD file has no solid parts")
            shape = Part.makeCompound([o.Shape.copy() for o in tops]) if len(tops) > 1 else tops[0].Shape.copy()
        finally:
            FreeCAD.closeDocument(other.Name)
            FreeCAD.setActiveDocument(doc.Name)
    else:
        raise OpError("bad_args", f"Unknown part format {fmt!r}")
    if shape.isNull() or not shape.Faces:
        raise OpError("bad_args", "The file has no geometry CADSwarm can read")
    return shape


def cylinder_features(shape, limit=400):
    """Holes (inward-facing cylinders) and bosses (outward) by diameter, counted by axis position."""
    holes, bosses = {}, {}
    for face in shape.Faces[:limit]:
        surf = face.Surface
        if type(surf).__name__ != "Cylinder":
            continue
        d = round(surf.Radius * 2, 3)
        u0, u1, v0, v1 = face.ParameterRange
        pt = face.valueAt((u0 + u1) / 2, (v0 + v1) / 2)
        n = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        axis, c = surf.Axis, surf.Center
        radial = (pt - c) - axis * ((pt - c).dot(axis))
        inward = radial.Length > 0 and n.dot(radial) < 0
        # A hole's two half faces share an axis; key the axis by where it crosses the plane through the origin.
        foot = c - axis * c.dot(axis)
        key = (round(foot.x, 1), round(foot.y, 1), round(foot.z, 1), round(abs(axis.x), 2),
               round(abs(axis.y), 2), round(abs(axis.z), 2))
        (holes if inward else bosses).setdefault(d, set()).add(key)

    def listed(m):
        return sorted(({"d": d, "count": len(k)} for d, k in m.items()), key=lambda x: x["d"])
    return {"holes": listed(holes), "bosses": listed(bosses)}


def preview_mesh(shape, tol, budget):
    t, data = tol, stl_bytes(shape, tol)
    for _ in range(6):
        if len(data) <= budget:
            break
        t *= 2
        data = stl_bytes(shape, t)
    return base64.b64encode(data).decode()


def q_analyze_component(a):
    """{"id"} or {"path", "format"} -> bbox, volume, holes/bosses and a preview STL (base64)."""
    if a.get("path"):
        path = a["path"]
        fmt = a.get("format") or LIB_FORMATS.get(os.path.splitext(path)[1].lower())
    else:
        path, fmt = library_file(a.get("id"))
    shape = load_part_shape(path, fmt)
    bb = shape.BoundBox
    feats = cylinder_features(shape)
    feats.update({"solids": len(shape.Solids), "faces": len(shape.Faces),
                  "size": [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]})
    tol = setting(a, "mesh_deviation", 0.2)
    return {"bbox": [round(v, 4) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)],
            "volume": round(shape.Volume, 4) if shape.Solids else 0.0, "features": feats,
            "mesh": preview_mesh(shape, max(tol, 0.05), int(a.get("max_bytes") or 150_000))}


def tag_component(obj, ref, label):
    obj.addProperty("App::PropertyString", "CadSwarmComponent", "CadSwarm", "Library part this came from")
    obj.CadSwarmComponent = ref
    obj.Label = label


def op_insert_component(a):
    """Import a library part or a STEP/STL file from the team's shared files into the FCStd document."""
    if a.get("component"):
        path, fmt = library_file(a.get("component"))
        shape = load_part_shape(path, fmt)
        obj = doc.addObject("Part::Feature", new_name(a.get("name")))
        obj.Shape = shape
        tag_component(obj, a["component"], a.get("label") or a["name"])
    elif a.get("path") or a.get("file"):
        path = project_file(a.get("path") or a.get("file"))
        fmt = LIB_FORMATS.get(os.path.splitext(path)[1].lower())
        if fmt == "stl":
            return op_insert_mesh({**a, "file": a.get("path") or a.get("file")})
        if fmt != "step":
            raise OpError("bad_args", "Shared exchange imports support STEP/STP and STL files")
        shape = load_part_shape(path, fmt)
        obj = doc.addObject("Part::Feature", new_name(a.get("name")))
        obj.Shape = shape
        if a.get("label"):
            obj.Label = str(a["label"])
    else:
        raise OpError("bad_args", "Provide a library component id or a project files path")
    if a.get("placement"):
        obj.Placement = to_placement(a["placement"])
    bb = shape.BoundBox
    return {"name": obj.Name, "component": a.get("component"),
            "size": [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]}


def standard_catalog():
    global _standard
    if _standard is None:
        with open(STANDARD_PARTS, encoding="utf-8") as f:
            _standard = json.load(f)
    return _standard


def _hex_prism(across_flats, height, z0=0.0):
    r = across_flats / math.sqrt(3)
    pts = [Vector(r * math.cos(math.radians(30 + 60 * i)), r * math.sin(math.radians(30 + 60 * i)), z0)
           for i in range(6)]
    return Part.Face(Part.makePolygon(pts + [pts[0]])).extrude(Vector(0, 0, height))


def _tube(od, id_, h, z0=0.0):
    outer = Part.makeCylinder(od / 2, h, Vector(0, 0, z0))
    return outer.cut(Part.makeCylinder(id_ / 2, h, Vector(0, 0, z0))) if id_ > 0 else outer


def build_standard(shape_kind, d, length):
    """Simplified solid for a standard part. Screws: under-head face at z=0, shank down -Z. Others sit on z=0."""
    if shape_kind == "socket_head":
        head = Part.makeCylinder(d["dk"] / 2, d["k"])
        head = head.cut(_hex_prism(d["s"], d["k"] * 0.6, d["k"] * 0.4 + 1e-3))
        return head.fuse(Part.makeCylinder(d["d"] / 2, length, Vector(0, 0, -length))).removeSplitter()
    if shape_kind == "button_head":
        a, k = d["dk"] / 2, d["k"]
        r = (a * a + k * k) / (2 * k)
        cap = Part.makeSphere(r, Vector(0, 0, k - r)).common(Part.makeCylinder(a, k))
        cap = cap.cut(_hex_prism(d["s"], k * 0.55, k * 0.45 + 1e-3))
        return cap.fuse(Part.makeCylinder(d["d"] / 2, length, Vector(0, 0, -length))).removeSplitter()
    if shape_kind == "hex_nut":
        return _hex_prism(d["s"], d["m"]).cut(Part.makeCylinder(d["d"] * 0.85 / 2, d["m"]))
    if shape_kind == "washer":
        return _tube(d["d2"], d["d1"], d["h"])
    if shape_kind == "insert":
        return _tube(d["od"], d["d"] * 0.85, d["length"])
    if shape_kind == "bearing":
        od, id_, b = d["od"], d["id"], d["b"]
        ring = (od - id_) / 2
        body = _tube(od, id_, b)
        shield = _tube(od - ring * 0.6, id_ + ring * 0.6, 0.25)
        return body.cut(shield).cut(shield.translated(Vector(0, 0, b - 0.25)))
    if shape_kind == "pin":
        return Part.makeCylinder(d["d"] / 2, length)
    if shape_kind == "disc":
        return Part.makeCylinder(d["d"] / 2, d["h"])
    raise OpError("bad_args", f"Unknown standard shape {shape_kind!r}")


def op_insert_standard_part(a):
    """{"family", "size", "length"?, "name", "placement"?} -> a simplified solid of that standard part."""
    fams = standard_catalog()["families"]
    fam = fams.get(str(a.get("family", "")))
    if fam is None:
        raise OpError("bad_args", f"family must be one of {sorted(fams)}")
    size = next((k for k in fam["sizes"] if k.lower() == str(a.get("size", "")).lower()), None)
    if size is None:
        raise OpError("bad_args", f"{fam['name']} sizes: {', '.join(fam['sizes'])}")
    length = 0.0
    if "length" in fam["params"]:
        try:
            length = float(a.get("length"))
        except (TypeError, ValueError):
            raise OpError("bad_args", f"{fam['name']} needs a length in mm ({fam.get('lengths')})")
        if length <= 0 or length > 500:
            raise OpError("bad_args", "length must be between 0 and 500 mm")
    solid = build_standard(fam["shape"], fam["sizes"][size], length)
    obj = doc.addObject("Part::Feature", new_name(a.get("name")))
    obj.Shape = solid
    label = f"{size}x{length:g} {fam['name']}" if length else f"{size} {fam['name']}"
    tag_component(obj, f"c_std_{a['family']}:{size}" + (f"x{length:g}" if length else ""), a.get("label") or label)
    if a.get("placement"):
        obj.Placement = to_placement(a["placement"])
    return {"name": obj.Name, "label": obj.Label, "volume": round(solid.Volume, 3)}


def project_file(rel):
    """Absolute path of a project file (files/<rel>), refusing anything outside files/."""
    if not isinstance(rel, str) or not rel.strip():
        raise OpError("bad_args", "file must be a project file path, e.g. 'lattice/.../result.stl'")
    base = os.path.realpath(os.path.join(os.path.dirname(DOC_PATH), "files"))
    rel = rel.replace("\\", "/").lstrip("/")
    if rel.startswith("files/"):
        rel = rel[len("files/"):]
    path = os.path.realpath(os.path.join(base, rel))
    if os.path.commonpath([base, path]) != base:
        raise OpError("bad_args", "file must be inside the project's files folder")
    if not os.path.isfile(path):
        raise OpError("not_found", f"No project file {rel}")
    return path


def op_insert_mesh(a):
    """{"name", "file", "label"?, "source"?} -> a Mesh::Feature (e.g. a lattice) from an STL in project files.

    A lattice has millions of triangles, too many for a B-rep solid; a mesh object shows it, measures it and
    exports it. The original part is left as it was."""
    import Mesh
    name = new_name(a.get("name"))
    path = project_file(a.get("file"))
    mesh = Mesh.Mesh(path)
    if mesh.CountFacets == 0:
        raise OpError("bad_args", f"{a.get('file')} has no triangles")
    obj = doc.addObject("Mesh::Feature", name)
    obj.Mesh = mesh
    obj.Label = str(a.get("label") or name)
    obj.addProperty("App::PropertyString", "CadSwarmSource", "CadSwarm", "What this mesh was made from")
    obj.CadSwarmSource = json.dumps({"file": a.get("file"), "source": a.get("source"), "job": a.get("job")})
    bb = mesh.BoundBox
    return {"name": obj.Name, "triangles": mesh.CountFacets, "volume_mm3": round(mesh.Volume, 3),
            "size": [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]}


MUTATIONS = {
    "create_primitive": op_create_primitive,
    "create_extrusion": op_create_extrusion,
    "create_sketch": op_create_sketch,
    "edit_sketch": op_edit_sketch,
    "update_sketch": op_edit_sketch,
    "create_sketch_feature": op_create_sketch_feature,
    "boolean": op_boolean,
    "set_property": op_set_property,
    "set_props": op_set_props,
    "set_expression": op_set_expression,
    "set_placement": op_set_placement,
    "delete": op_delete,
    "set_param": op_set_param,
    "run_part_script": op_run_part_script,
    "insert_component": op_insert_component,
    "insert_standard_part": op_insert_standard_part,
    "insert_mesh": op_insert_mesh,
}

LOCK_AWARE_MUTATIONS = {"create_sketch", "edit_sketch", "update_sketch", "create_sketch_feature", "boolean",
                        "set_property", "set_props", "set_expression", "set_placement", "delete", "set_param"}


def _shape_error(obj):
    """Return a readable kernel error for a touched shape or sketch, if one exists."""
    if obj is None or obj.TypeId in ("Spreadsheet::Sheet",):
        return None
    if obj.TypeId == "Sketcher::SketchObject":
        solver = _sketch_solver(obj)
        if solver["conflicting"] or solver["malformed"]:
            return f"Sketch constraints did not solve: {solver}"
        return None
    shape = getattr(obj, "Shape", None)
    if shape is not None:
        if shape.isNull():
            return "feature produced an empty shape"
        try:
            if shape.Faces and not shape.isValid():
                return "feature produced an invalid OpenCASCADE shape"
        except Exception:
            pass
    return None


def mutate(op, args, allowed):
    before = snapshot()
    before_internal = {o.Name: is_internal(o) for o in doc.Objects}
    before_internal_owner = {o.Name: getattr(o, "CadSwarmInternalOwner", "") if is_internal(o) else ""
                             for o in doc.Objects}
    doc.openTransaction(f"cadswarm:{op}")
    try:
        mutator = MUTATIONS[op]
        result = mutator(args, allowed) if op in LOCK_AWARE_MUTATIONS else mutator(args)
        # FreeCAD recomputes native links and expressions. CADSwarm's pattern outputs are stored as
        # ordinary FCStd shapes with explicit source/parameter properties, so rebuild them in document order.
        doc.recompute()
        _rebuild_native_dimensions()
        doc.recompute()
        _rebuild_cadswarm_features()
        doc.recompute()
        _rebuild_native_dimensions()
        doc.recompute()
        after = snapshot()
        all_created = sorted(set(after) - set(before))
        all_deleted = sorted(set(before) - set(after))
        common = set(after) & set(before)
        all_modified = sorted(n for n in common if after[n][0] != before[n][0])
        all_recomputed = sorted(n for n in common if n not in all_modified and after[n][1] != before[n][1])
        if allowed is not None:
            def owner_allowed(name):
                obj = doc.getObject(name)
                owner = (getattr(obj, "CadSwarmInternalOwner", "") if obj is not None and is_internal(obj)
                         else before_internal_owner.get(name, ""))
                return name in allowed or bool(owner and owner in allowed)
            violations = sorted(name for name in set(all_modified) | set(all_deleted) if not owner_allowed(name))
            if violations:
                raise OpError("lock_violation",
                              f"This op would change objects you have not locked: {violations}. "
                              "Lock them first (lock_objects) or ask their owner.")
        created = [n for n in all_created if not is_internal(doc.getObject(n))]
        deleted = [n for n in all_deleted if not before_internal.get(n, False)]
        modified = [n for n in all_modified if not is_internal(doc.getObject(n))]
        recomputed = [n for n in all_recomputed if not is_internal(doc.getObject(n))]
        broken = [n for n in all_created + all_modified if is_broken(doc.getObject(n))]
        if broken:
            raise OpError("recompute_error", f"Objects failed to recompute: {broken}")
        invalid = [(n, _shape_error(doc.getObject(n))) for n in all_created + all_modified]
        invalid = [(n, message) for n, message in invalid if message]
        if invalid:
            raise OpError("recompute_error", "; ".join(f"{n}: {message}" for n, message in invalid))
        # Persist while the transaction is still open: if the file can't be written the abort below
        # puts memory back to match the disk (and the hub, which records nothing for a failed op).
        save()
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        doc.recompute()
        raise
    if isinstance(result, dict):
        # Where the agent's pointer rests after the op: a real vertex of the part it made or changed.
        for n in created + modified:
            o = doc.getObject(n)
            point = anchor_of(getattr(o, "Shape", None)) if o is not None and n != PARAMS else None
            if point:
                result.setdefault("pointer", {"object": n, "point": point})
                break
    return {"result": result, "created": created, "modified": modified,
            "deleted": deleted, "recomputed": recomputed,
            "broken": [o.Name for o in doc.Objects if is_broken(o)]}


# --------------------------------------------------------------------------
# Read-only queries
# --------------------------------------------------------------------------

def params_dict():
    sheet = doc.getObject(PARAMS)
    out = {}
    for cell in sheet.getUsedCells():
        if cell.startswith("B") and sheet.getAlias(cell):
            alias = sheet.getAlias(cell)
            try:
                v = sheet.get(alias)
                out[alias] = v.Value if hasattr(v, "Value") else v
            except Exception:
                out[alias] = sheet.getContents(cell)
    return out


def param_users(alias):
    needle = f"{PARAMS}.{alias}"
    return sorted(o.Name for o in doc.Objects
                  if any(needle in expr for _, expr in (o.ExpressionEngine or [])))


def pick_anchor(points):
    """The vertex a pointer should sit on: on the top face, the corner nearest a default isometric
    camera (largest x + y). None when there are no vertices."""
    if not points:
        return None
    top = max(p[2] for p in points)
    tol = 1e-3 * max(1.0, max(abs(c) for p in points for c in p))
    best = max((p for p in points if p[2] >= top - tol), key=lambda p: (round(p[0] + p[1], 6), p[0]))
    return [round(float(c), 3) for c in best]


def anchor_of(shape):
    if shape is None or shape.isNull():
        return None
    return pick_anchor([(v.X, v.Y, v.Z) for v in shape.Vertexes[:5000]])


def obj_summary(o):
    info = {"name": o.Name, "label": o.Label, "type": o.TypeId,
            "visible": bool(getattr(o, "Visibility", True)),
            "uses": sorted(x.Name for x in o.OutList if not is_internal(x)),
            "used_by": sorted(x.Name for x in o.InList if not is_internal(x)),
            "expressions": {p: e for p, e in (o.ExpressionEngine or [])},
            "broken": is_broken(o)}
    if hasattr(o, "Placement"):
        info["placement"] = placement_json(o.Placement)
    if getattr(o, "CadSwarmComponent", ""):
        info["component"] = o.CadSwarmComponent  # placed from the components library
    if o.TypeId == "Sketcher::SketchObject":
        info["solver"] = _sketch_solver(o)
        info["plane"] = getattr(o, "CadSwarmPlane", "XY")
    if getattr(o, "CadSwarmKind", ""):
        info["feature_kind"] = o.CadSwarmKind
    shape = getattr(o, "Shape", None)
    if shape is not None and not shape.isNull():
        bb = shape.BoundBox
        info["bbox"] = [round(v, 3) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)]
        info["volume"] = round(shape.Volume, 3) if shape.Solids else 0.0
        anchor = anchor_of(shape)
        if anchor:
            info["anchor"] = anchor
    elif o.TypeId.startswith("Mesh::") and hasattr(o, "Mesh") and o.Mesh.CountFacets:
        bb = o.Mesh.BoundBox
        info["bbox"] = [round(v, 3) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)]
        info["volume"] = round(o.Mesh.Volume, 3)
        info["triangles"] = o.Mesh.CountFacets
    return info


def q_status(a):
    return {"objects": [obj_summary(o) for o in doc.Objects if not is_internal(o)], "params": params_dict(),
            "doc_path": DOC_PATH}


def q_get_object(a):
    o = get_obj(a.get("name"))
    if is_internal(o):
        raise OpError("not_found", f"No public object named {o.Name!r}")
    info = obj_summary(o)
    props = {}
    for p in o.PropertiesList:
        if p in ("Shape", "ExpressionEngine", "cells"):
            continue
        try:
            props[p] = json.loads(json.dumps(o.getPropertyByName(p), default=value_repr))
        except Exception:
            continue
    info["properties"] = props
    if o.TypeId == "Spreadsheet::Sheet":
        info["cells"] = dict(sheet_contents(o))
    if o.TypeId == "Sketcher::SketchObject":
        info["feature"] = _sketch_definition(o)
    return info


def q_brep(a):
    out = {}
    for name in a.get("names") or [o.Name for o in doc.Objects if not is_internal(o)]:
        o = doc.getObject(name)
        if is_internal(o):
            out[name] = None
            continue
        shape = getattr(o, "Shape", None) if o else None
        if o is None:
            out[name] = None
        elif shape is None or shape.isNull():
            out[name] = {"label": o.Label, "visible": bool(o.Visibility), "brep": None}
        else:
            out[name] = {"label": o.Label, "visible": bool(o.Visibility), "brep": shape.exportBrepToString()}
    return {"objects": out}


def q_interference(a):
    requested = set(a.get("names") or [])
    names = [o.Name for o in doc.Objects if not is_internal(o) and getattr(o, "Visibility", False)
             and (not requested or o.Name in requested) and hasattr(o, "Shape")
             and not o.Shape.isNull() and o.Shape.Solids]
    shapes = [(n, get_obj(n).Shape) for n in names]
    tol = float(a.get("min_volume") or setting(a, "interference_min_vol", 1e-3))
    clashes = []
    for i in range(len(shapes)):
        for j in range(i + 1, len(shapes)):
            (na, sa), (nb, sb) = shapes[i], shapes[j]
            if not sa.BoundBox.intersect(sb.BoundBox):
                continue
            vol = sa.common(sb).Volume
            if vol > tol:
                clashes.append({"a": na, "b": nb, "volume": round(vol, 4)})
    return {"checked": names, "clashes": clashes}


def q_export(a):
    import Import
    path = os.path.abspath(a["path"])
    objs = [get_obj(n) for n in a.get("names") or []] or \
           [o for o in doc.Objects if getattr(o, "Visibility", False) and hasattr(o, "Shape")
            and not o.Shape.isNull()]
    if path.lower().endswith((".step", ".stp")):
        Import.export(objs, path)
    elif path.lower().endswith(".stl"):
        import Mesh
        Mesh.export(objs, path)
    else:
        raise OpError("bad_args", "export path must end in .step/.stp/.stl")
    return {"path": path, "objects": [o.Name for o in objs]}


def q_param_users(a):
    return {"alias": a.get("alias"), "used_by": param_users(a.get("alias"))}


def q_dependents(a):
    """Transitive users of the given objects (who recomputes when these change)."""
    seen, stack = set(), list(a.get("names") or [])
    while stack:
        o = doc.getObject(stack.pop())
        for user in (o.InList if o else []):
            if user.Name not in seen:
                seen.add(user.Name)
                stack.append(user.Name)
    return {"dependents": sorted(n for n in seen if not is_internal(doc.getObject(n)))}


def fea_target(names):
    import fea_runner  # next to this file, like sandbox
    if not names:
        raise OpError("bad_args", "Name the part(s) to analyse")
    shapes = []
    for n in names:
        shape = getattr(get_obj(n), "Shape", None)
        if shape is None or shape.isNull() or not shape.Solids:
            raise OpError("bad_args", f"{n} has no solid to analyse")
        shapes.append(shape)
    try:
        return fea_runner.analysis_shape(Part.makeCompound(shapes) if len(shapes) > 1 else shapes[0])
    except fea_runner.FeaError as e:
        raise OpError(e.code, str(e))


def q_faces(a):
    """Faces of the part(s) an analysis would mesh, plus what `select` selectors resolve to."""
    import fea_runner
    names = a.get("names") or []
    shape, warnings = fea_target(names)
    bb = shape.BoundBox
    out = {"names": names, "bbox": [round(v, 3) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)],
           "faces": fea_runner.face_table(shape)[:300], "face_count": len(shape.Faces), "warnings": warnings}
    if a.get("select"):
        try:
            out["selected"] = {s: fea_runner.select_faces(shape, [s]) for s in a["select"]}
        except fea_runner.FeaError as e:
            raise OpError(e.code, str(e))
    return out


def _mesh_stl(o, budget):
    """Binary STL of a mesh object, decimated until it fits the preview budget."""
    m = o.Mesh.copy()
    for _ in range(8):
        size = 84 + 50 * m.CountFacets
        if size <= budget or m.CountFacets < 200:
            break
        try:
            m.decimate(0.05, min(0.9, 1 - budget / size))
        except Exception:
            break
    tris = []
    for f in m.Facets:
        tris.append(f.Points)
    out = bytearray(80) + struct.pack("<I", len(tris))
    for p in tris:
        out += struct.pack("<12fH", 0, 0, 0, *p[0], *p[1], *p[2], 0)
    return bytes(out)


def _solid_shape(o):
    shape = getattr(o, "Shape", None) if o is not None else None
    if shape is None or shape.isNull() or not shape.Faces:
        return None
    return shape


def stl_bytes(shape, tolerance):
    """Binary STL of a shape's tessellation (written by hand: no file round trip, no ASCII surprise)."""
    verts, tris = shape.tessellate(tolerance)
    out = bytearray(b"cadswarm preview".ljust(80, b"\0"))
    out += struct.pack("<I", len(tris))
    for a, b, c in tris:
        p, q, r = verts[a], verts[b], verts[c]
        n = (q - p).cross(r - p)
        if n.Length > 0:
            n.normalize()
        out += struct.pack("<12fH", n.x, n.y, n.z, p.x, p.y, p.z, q.x, q.y, q.z, r.x, r.y, r.z, 0)
    return bytes(out)


def q_mesh(a):
    """{"names", "tolerance", "max_bytes"} -> {"meshes": {name: base64 binary STL}}; shapeless names omitted."""
    tol = float(a.get("tolerance") or setting(a, "mesh_deviation", 0.2))
    budget = int(a.get("max_bytes") or 200_000)
    out = {}
    for name in a.get("names") or [o.Name for o in doc.Objects if not is_internal(o)]:
        obj = doc.getObject(name)
        if obj is not None and not is_internal(obj) and obj.TypeId.startswith("Mesh::") and hasattr(obj, "Mesh"):
            out[name] = base64.b64encode(_mesh_stl(obj, budget)).decode()
            continue
        if is_internal(obj):
            continue
        shape = _solid_shape(obj)
        if shape is None:
            continue
        t, data = tol, stl_bytes(shape, tol)
        for _ in range(6):  # coarsen until it fits the preview budget
            if len(data) <= budget:
                break
            t *= 2
            data = stl_bytes(shape, t)
        out[name] = base64.b64encode(data).decode()
    return {"meshes": out}


def face_normal(face):
    u0, u1, v0, v1 = face.ParameterRange
    return face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)


def thin_walls(shape, min_wall, limit=20):
    """Best effort: pairs of opposite-facing planar faces closer than `min_wall`."""
    planar = [(i, f) for i, f in enumerate(shape.Faces) if type(f.Surface).__name__ == "Plane"]
    out = []
    for x in range(len(planar)):
        i, fi = planar[x]
        ni = face_normal(fi)
        for y in range(x + 1, len(planar)):
            j, fj = planar[y]
            if ni.dot(face_normal(fj)) > -0.99:
                continue
            d = fi.distToShape(fj)[0]
            if 1e-6 < d < min_wall:
                out.append({"face_a": i, "face_b": j, "distance": round(d, 3)})
                if len(out) >= limit:
                    return out
    return out


def q_printability(a):
    """{"names" | null, "bed": [x, y, z], "min_wall"} -> {"ok", "objects": [...]} (plan 00 worker additions)."""
    bed = [float(v) for v in (a.get("bed") or [180, 180, 180])]
    min_wall = float(a.get("min_wall") or setting(a, "min_wall", 1.2))
    names = a.get("names") or [o.Name for o in doc.Objects if getattr(o, "Visibility", False)
                               and _solid_shape(o) is not None and o.Shape.Solids]
    objects = []
    for n in names:
        s = get_obj(n).Shape
        bb = s.BoundBox
        size = [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]
        # The footprint may be rotated on the bed, so compare the sorted XY extents.
        fits = all(x <= b for x, b in zip(sorted(size[:2]), sorted(bed[:2]))) and size[2] <= bed[2]
        closed = bool(s.Solids) and all(sol.isClosed() for sol in s.Solids) and s.isValid()
        objects.append({"name": n, "size": size, "fits_bed": fits, "closed_solid": closed,
                        "thin_wall_suspects": thin_walls(s, min_wall) if s.Solids else []})
    ok = all(o["fits_bed"] and o["closed_solid"] and not o["thin_wall_suspects"] for o in objects)
    return {"ok": ok, "objects": objects}


QUERIES = {"status": q_status, "get_object": q_get_object, "brep": q_brep,
           "interference": q_interference, "export": q_export,
           "param_users": q_param_users, "dependents": q_dependents,
           "faces": q_faces,
           "mesh": q_mesh, "printability": q_printability,
           "analyze_component": q_analyze_component,
           "ping": lambda a: {"pong": True, "freecad": FreeCAD.Version()[:3],
                              "script_sandbox": sandbox.info(sys.executable)}}


def handle(req):
    op, args = req.get("op"), req.get("args") or {}
    if op in MUTATIONS:
        return {"ok": True, **mutate(op, args, req.get("allowed"))}
    if op in QUERIES:
        return {"ok": True, "result": QUERIES[op](args)}
    raise OpError("unknown_op", f"Unknown op {op!r}")


def main():
    threading.Thread(target=sandbox.prepare, args=(sys.executable,), daemon=True).start()
    open_document(sys.argv[1] if len(sys.argv) > 1 else "model.FCStd")
    # FreeCAD and OCC occasionally print to stdout from C++, past sys.stdout. Keep the protocol on a
    # private duplicate of fd 1 and point fd 1 itself (and Python's stdout) at stderr.
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    out.write(json.dumps({"ready": True, "doc": DOC_PATH}) + "\n")
    out.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            resp = handle(req)
        except OpError as e:
            resp = {"ok": False, "code": e.code, "error": str(e)}
        except Exception as e:  # agent scripts can raise anything; report, never die
            resp = {"ok": False, "code": "op_error", "error": f"{type(e).__name__}: {e}",
                    "trace": traceback.format_exc(limit=3)}
        resp["id"] = rid
        out.write(json.dumps(resp, default=str) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
