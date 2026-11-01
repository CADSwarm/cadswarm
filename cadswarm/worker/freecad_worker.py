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
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import threading
import traceback

import sandbox  # noqa: E402  (next to this file; the worker runs as a plain script)

# The package root, so `cadswarm.render` (the clay renderer) is importable even when the worker is
# started outside the host (tests, a direct run) and PYTHONPATH does not already include it.
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

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
# The model file's (size, mtime) while it holds exactly what is in memory, else None. Set by a save and by
# opening the file; cleared by every mutation until its save.
ON_DISK = None


def open_document(path):
    global DOC_PATH, doc
    DOC_PATH = os.path.abspath(path)
    if os.path.exists(DOC_PATH):
        doc = FreeCAD.openDocument(DOC_PATH)
        mark_on_disk()
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


def mark_on_disk():
    global ON_DISK
    st = os.stat(DOC_PATH)
    ON_DISK = (st.st_size, st.st_mtime_ns)


def snapshot_copy(path):
    """Write the document to `path` for a child process (a part script, a clash check). The saved model
    file, when it is current, is copied in milliseconds; saveCopy re-serialises every shape, which took
    9 s on the 18 MB IS350 model before every script and every interference check."""
    try:
        st = os.stat(DOC_PATH)
        current = ON_DISK == (st.st_size, st.st_mtime_ns)
    except OSError:
        current = False
    if current:
        shutil.copyfile(DOC_PATH, path)
    else:
        doc.saveCopy(path)


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
            mark_on_disk()
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


# Volume and Area per shape, keyed by the OCC shape handle plus cheap facts about it. Every mutating op
# fingerprints every object twice; integrating a 2000-face grille took 5 s, the whole Lexus 15 s a time.
_MASS_CACHE = {}
_BOX_CACHE = {}


def sub_counts(shape):
    """(faces, edges, vertices). len(shape.Edges) builds a Python object per edge: 2 s across the Lexus
    for the three counts, and every mutating op fingerprints every object twice."""
    try:
        return tuple(shape.countSubElements(k) for k in ("Face", "Edge", "Vertex"))
    except Exception:  # an older FreeCAD without countSubElements
        return len(shape.Faces), len(shape.Edges), len(shape.Vertexes)


def tight_box(shape):
    """The shape's real extent. Shape.BoundBox is built from BSpline control points and ran up to 170 mm
    outside the Lexus parts: a helper read a correct boolean cut as leaking 68 mm and gave up on the bumper.
    Cached per shape handle, since the exact box is ~30 ms a part."""
    key = (shape.hashCode(), *sub_counts(shape))
    box = _BOX_CACHE.get(key)
    if box is None:
        if len(_BOX_CACHE) > 20_000:
            _BOX_CACHE.clear()
        try:
            box = shape.optimalBoundingBox()
        except Exception:  # an older FreeCAD, or a shape OCC can't bound exactly
            box = shape.BoundBox
        _BOX_CACHE[key] = box
    return box


def shape_sig(shape):
    if shape is None or shape.isNull():
        return None
    bb = shape.BoundBox
    box = tuple(round(v, 6) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax))
    # The handle alone could be reused by a new shape once the old one is freed: the counts and box guard it.
    counts = sub_counts(shape)
    key = (shape.hashCode(), *counts, box)
    mass = _MASS_CACHE.get(key)
    if mass is None:
        if len(_MASS_CACHE) > 20_000:
            _MASS_CACHE.clear()
        mass = _MASS_CACHE[key] = (round(shape.Volume if shape.Solids else 0.0, 6), round(shape.Area, 6))
    return (mass[0], mass[1], box, counts[0])


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
    if obj.TypeId == "Part::Feature":  # non-parametric: its Shape IS its primary data
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
    if not name or not str(name).replace("_", "").isalnum():
        raise OpError("bad_args", "name must be alphanumeric/underscore, e.g. 'MotorMount'")
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


def apply_props(obj, props, expressions):
    for prop, value in (props or {}).items():
        if prop not in obj.PropertiesList:
            raise OpError("bad_args", f"{obj.TypeId} has no property {prop!r}")
        setattr(obj, prop, coerce(obj, prop, value))
    for prop, expr in (expressions or {}).items():
        obj.setExpression(prop, expr)


# --------------------------------------------------------------------------
# Mutating ops (run inside a transaction by `mutate`)
# --------------------------------------------------------------------------

def op_create_primitive(a):
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


def op_create_extrusion(a):
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


def op_boolean(a):
    kind = str(a.get("kind", "")).lower()
    if kind not in BOOLEANS:
        raise OpError("bad_args", f"kind must be one of {sorted(BOOLEANS)}")
    base, tool = get_obj(a.get("base")), get_obj(a.get("tool"))
    obj = doc.addObject(BOOLEANS[kind], new_name(a.get("name")))
    obj.Base, obj.Tool = base, tool
    base.Visibility = tool.Visibility = False
    return {"name": obj.Name, "consumed": [base.Name, tool.Name]}


def op_set_property(a):
    obj = get_obj(a.get("name"))
    apply_props(obj, {a["prop"]: a.get("value")}, None)
    return {"name": obj.Name}


def op_set_props(a):
    obj = get_obj(a.get("name"))
    apply_props(obj, a.get("props"), a.get("expressions"))
    return {"name": obj.Name}


def op_set_expression(a):
    obj = get_obj(a.get("name"))
    obj.setExpression(a["prop"], a.get("expr"))  # None clears the binding
    return {"name": obj.Name}


def op_set_placement(a):
    obj = get_obj(a.get("name"))
    bound = {path: expr for path, expr in (obj.ExpressionEngine or []) if path.lstrip(".").startswith("Placement")}
    if bound:
        # The next recompute puts the bound values back, so the move would report success and change
        # nothing (a helper moved TrunkLid three times this way while it sat 3.5 m behind the car).
        raise OpError("bad_args", f"{obj.Name}'s placement is driven by expressions {bound}; change those Params, "
                                  "or clear the expressions first (set_props with expressions set to null)")
    obj.Placement = to_placement(a.get("placement"))
    return {"name": obj.Name, "placement": placement_json(obj.Placement)}


def op_delete(a):
    obj = get_obj(a.get("name"))
    if obj.Name == PARAMS:
        # Every part binds its sizes to it: a helper deleted it twice in one demo, and every set_param
        # after that failed with not_found until the worker restarted.
        raise OpError("not_allowed", "Params is the team's shared dimensions sheet and can't be deleted; "
                                     "change a value with set_param instead")
    users = [o.Name for o in obj.InList]
    if users and not a.get("force"):
        raise OpError("in_use", f"{obj.Name} is used by {users}; delete those first or pass force")
    doc.removeObject(obj.Name)
    return {"name": a["name"]}


def find_param_row(sheet, alias):
    for cell in sheet.getUsedCells():
        if cell.startswith("B") and sheet.getAlias(cell) == alias:
            return int(cell[1:])
    return None


def op_set_param(a):
    alias = str(a.get("alias", ""))
    if not alias.replace("_", "").isalnum() or alias[:1].isdigit():
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


def op_run_part_script(a):
    """Run agent Python in a confined child against a snapshot of the doc; its `shape` becomes the
    Part::Feature `name` here. The child can't touch the live doc: only the BREP and `result` come back."""
    code = a.get("code", "")
    try:
        reply = sandbox.run_script(sys.executable, snapshot_copy, {
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
        bound = {p: e for p, e in (obj.ExpressionEngine or []) if p.lstrip(".").startswith("Placement")}
        if bound:
            # The bound placement is applied on top of the shape's own coordinates, so a shape built where it
            # belongs lands twice as far out (TrunkLid and both taillights ended up metres behind the car).
            doc.recompute()
            bb = tight_box(obj.Shape)
            out["bbox"] = [round(v, 1) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)]
            out["warning"] = (f"{obj.Name}'s placement is driven by expressions {bound}, which move your shape "
                              f"on top of its own coordinates: it now spans {out['bbox']}. Build the shape at the "
                              "origin, or clear those expressions and build it where it belongs.")
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
    """{"component", "name", "placement"?, "label"?} -> a Part::Feature holding the library part's shape."""
    path, fmt = library_file(a.get("component"))
    shape = load_part_shape(path, fmt)
    obj = doc.addObject("Part::Feature", new_name(a.get("name")))
    obj.Shape = shape
    tag_component(obj, a["component"], a.get("label") or a["name"])
    if a.get("placement"):
        obj.Placement = to_placement(a["placement"])
    bb = shape.BoundBox
    return {"name": obj.Name, "component": a["component"],
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
    if not mesh.isSolid():
        raise OpError("not_solid", f"{a.get('file')} is not a closed mesh; it was not added")
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


def mutate(op, args, allowed):
    global ON_DISK
    before = snapshot()
    doc.openTransaction(f"cadswarm:{op}")
    try:
        result = MUTATIONS[op](args)
        # Only now: a part script takes its snapshot inside the op, while memory still matches the file.
        was_on_disk, ON_DISK = ON_DISK, None
        doc.recompute()
        after = snapshot()
        created = sorted(set(after) - set(before))
        deleted = sorted(set(before) - set(after))
        common = set(after) & set(before)
        modified = sorted(n for n in common if after[n][0] != before[n][0])
        recomputed = sorted(n for n in common if n not in modified and after[n][1] != before[n][1])
        if allowed is not None:
            violations = sorted((set(modified) | set(deleted)) - set(allowed))
            if violations:
                raise OpError("lock_violation",
                              f"This op would change objects you have not locked: {violations}. "
                              "Lock them first (lock_objects) or ask their owner.")
        broken = [n for n in created + modified if is_broken(doc.getObject(n))]
        if broken:
            raise OpError("recompute_error", f"Objects failed to recompute: {broken}")
        # Persist while the transaction is still open: if the file can't be written the abort below
        # puts memory back to match the disk (and the hub, which records nothing for a failed op).
        # A script that only measured (no `shape`) changed nothing: the file on disk is still the model.
        if op == "run_part_script" and "name" not in (result or {}) and not (created or deleted or modified
                                                                            or recomputed):
            ON_DISK = was_on_disk
        else:
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
            "uses": sorted(x.Name for x in o.OutList), "used_by": sorted(x.Name for x in o.InList),
            "expressions": {p: e for p, e in (o.ExpressionEngine or [])},
            "broken": is_broken(o)}
    if hasattr(o, "Placement"):
        info["placement"] = placement_json(o.Placement)
    if getattr(o, "CadSwarmComponent", ""):
        info["component"] = o.CadSwarmComponent  # placed from the components library
    shape = getattr(o, "Shape", None)
    if shape is not None and not shape.isNull():
        bb = tight_box(shape)
        if bb.isValid():  # an empty shape's box spans +-1.8e308, which read as a part outside the car
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
    return {"objects": [obj_summary(o) for o in doc.Objects], "params": params_dict(),
            "doc_path": DOC_PATH}


def q_get_object(a):
    o = get_obj(a.get("name"))
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
    return info


def q_brep(a):
    out = {}
    for name in a.get("names") or [o.Name for o in doc.Objects]:
        o = doc.getObject(name)
        shape = getattr(o, "Shape", None) if o else None
        if o is None:
            out[name] = None
        elif shape is None or shape.isNull():
            out[name] = {"label": o.Label, "visible": bool(o.Visibility), "brep": None}
        else:
            out[name] = {"label": o.Label, "visible": bool(o.Visibility), "brep": shape.exportBrepToString()}
    return {"objects": out}


# Inside the host's 120 s per op, with room for the snapshot; CADSWARM_INTERFERENCE_SECONDS for tests.
INTERFERENCE_SECONDS = float(os.environ.get("CADSWARM_INTERFERENCE_SECONDS") or 90)


def q_interference(a):
    names = a.get("names") or [o.Name for o in doc.Objects
                               if getattr(o, "Visibility", False) and hasattr(o, "Shape")
                               and not o.Shape.isNull() and o.Shape.Solids]
    shapes = [(n, get_obj(n).Shape) for n in names]  # unknown names fail here, as before
    tol = float(a.get("min_volume") or setting(a, "interference_min_vol", 1e-3))
    # Pairs whose (loose, cheap) boxes overlap, cheapest boolean first so one slow pair costs the least.
    candidates = []
    for i in range(len(shapes)):
        for j in range(i + 1, len(shapes)):
            (na, sa), (nb, sb) = shapes[i], shapes[j]
            if sa.BoundBox.intersect(sb.BoundBox):
                candidates.append((len(sa.Faces) * len(sb.Faces), na, nb))
    pairs = [(na, nb) for _, na, nb in sorted(candidates)]
    if not pairs:
        return {"checked": names, "clashes": [], "complete": True}
    # One boolean of the new Lexus skin against an old roof panel ran past the host's 120 s limit, which
    # kills FreeCAD and stalls every helper; an OCC boolean can't be interrupted, so the pairs run in a
    # child that is stopped at the budget. What it didn't finish is reported unchecked, never as clear.
    run_dir = tempfile.mkdtemp(prefix="cadswarm-clash-")
    try:
        snap = os.path.join(run_dir, "model.FCStd")
        snapshot_copy(snap)
        child = subprocess.Popen(
            [sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "clash_child.py"),
             snap, json.dumps(pairs), repr(tol)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == "nt" else 0)  # CREATE_NO_WINDOW
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(l) for l in child.stdout] and None, daemon=True).start()
        deadline = time.time() + INTERFERENCE_SECONDS
        answered, clashes, failed, done = set(), [], [], False
        while not done and time.time() < deadline:
            try:
                msg = json.loads(lines.get(timeout=max(0.1, deadline - time.time())))
            except (queue.Empty, ValueError):
                if child.poll() is not None and lines.empty():
                    break
                continue
            if msg.get("done"):
                done = True
            elif "a" in msg:
                answered.add((msg["a"], msg["b"]))
                if "error" in msg:
                    failed.append({"a": msg["a"], "b": msg["b"], "why": msg["error"]})
                elif msg.get("clash"):
                    clashes.append({"a": msg["a"], "b": msg["b"], "volume": msg["volume"]})
        if child.poll() is None:
            child.kill()
        child.wait()
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    why = f"not finished within {INTERFERENCE_SECONDS:g} s; check this pair on its own"
    unchecked = failed + [{"a": p[0], "b": p[1], "why": why} for p in pairs if p not in answered]
    out = {"checked": names, "clashes": clashes, "complete": done and not failed}
    if unchecked:
        out["unchecked"] = unchecked
    return out


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
    return {"dependents": sorted(seen)}


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
    bb = tight_box(shape)
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
    for name in a.get("names") or [o.Name for o in doc.Objects]:
        obj = doc.getObject(name)
        if obj is not None and obj.TypeId.startswith("Mesh::") and hasattr(obj, "Mesh"):
            out[name] = base64.b64encode(_mesh_stl(obj, budget)).decode()
            continue
        shape = _solid_shape(obj)
        if shape is None:
            continue
        out[name] = base64.b64encode(budget_stl(shape, tol, budget)).decode()
    return {"meshes": out}


def budget_stl(shape, tol, budget):
    """Binary STL of `shape` that fits `budget` bytes, as fine as that allows.

    Each try meshes a fresh copy: a shape keeps its first triangulation, and asking the same shape for a
    coarser one returns that finer mesh again (a car's suspension stayed 15 MB at any tolerance). The
    angular limit matters as much as the linear one for small curved detail like springs and spokes;
    the start is sized to the part, since 0.2 mm on a 4 m body is a scan, not a preview.

    Few, big steps: every op waits for this, and eight fine tries on a 32-station car skin took 43 s while
    no try fit anyway. Three tries at most, each four times coarser, keeping the smallest."""
    lin = max(tol, shape.BoundBox.DiagonalLength / 1000)
    ang = 0.5
    best = None
    for _ in range(3):
        data = mesh_stl(shape.copy(True, False), lin, ang)
        if best is None or len(data) < len(best):
            best = data
        if len(data) <= budget:
            break
        lin, ang = lin * 4, min(ang * 2, 1.2)
    return best


def mesh_stl(shape, linear, angular):
    """Binary STL from MeshPart (linear and angular deflection); plain tessellation if MeshPart is missing."""
    try:
        import MeshPart
    except ImportError:
        return stl_bytes(shape, linear)
    m = MeshPart.meshFromShape(Shape=shape, LinearDeflection=linear, AngularDeflection=angular, Relative=False)
    out = bytearray(b"cadswarm preview".ljust(80, b"\0"))
    out += struct.pack("<I", m.CountFacets)
    for f in m.Facets:
        n, (p, q, r) = f.Normal, f.Points
        out += struct.pack("<12fH", n.x, n.y, n.z, *p, *q, *r, 0)
    return bytes(out)


def face_normal(face):
    u0, u1, v0, v1 = face.ParameterRange
    return face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)


PRINTABILITY_SECONDS = 60  # wall checks stop here, well inside the host's 120 s per op


def thin_walls(shape, min_wall, limit=20, deadline=None):
    """Best effort: pairs of opposite-facing planar faces closer than `min_wall` (stops at `deadline`)."""
    planar = [(i, f) for i, f in enumerate(shape.Faces) if type(f.Surface).__name__ == "Plane"]
    out = []
    for x in range(len(planar)):
        if deadline and time.time() > deadline:
            break
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
    # The wall check compares every pair of flat faces. On a whole car it ran past the worker's 120 s limit,
    # which kills and reloads FreeCAD while every other op waits: stop checking walls at this budget.
    deadline = time.time() + PRINTABILITY_SECONDS
    objects = []
    for n in names:
        s = get_obj(n).Shape
        bb = tight_box(s)
        size = [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]
        # The footprint may be rotated on the bed, so compare the sorted XY extents.
        fits = all(x <= b for x, b in zip(sorted(size[:2]), sorted(bed[:2]))) and size[2] <= bed[2]
        closed = bool(s.Solids) and all(sol.isClosed() for sol in s.Solids) and s.isValid()
        # A part too big for the bed fails already; its walls can't change that.
        check = fits and bool(s.Solids) and time.time() < deadline
        objects.append({"name": n, "size": size, "fits_bed": fits, "closed_solid": closed,
                        "thin_wall_suspects": thin_walls(s, min_wall, deadline=deadline) if check else [],
                        "walls_checked": check or not s.Solids})
    ok = all(o["fits_bed"] and o["closed_solid"] and o["walls_checked"] and not o["thin_wall_suspects"]
             for o in objects)
    return {"ok": ok, "objects": objects}


def q_surface_quality(a):
    """How much of each part's surface is flat, and how much is real curvature.

    The reviewer and the MCP tool use this to tell a boxy part (allowed) from a blocky attempt at
    an organic one (not allowed). Numbers only: the judgement lives in cadswarm/shape_intent.py.
    """
    want = set(a.get("names") or [])
    out = []
    for o in doc.Objects:
        sh = getattr(o, "Shape", None)
        if sh is None or sh.isNull():
            continue
        if want and o.Name not in want and o.Label not in want:
            continue
        kinds = {}
        for f in sh.Faces:
            k = f.Surface.__class__.__name__
            kinds[k] = kinds.get(k, 0) + 1
        faces = len(sh.Faces)
        planar = kinds.get("Plane", 0)
        bb = tight_box(sh)
        out.append({
            "name": o.Name, "label": o.Label, "faces": faces, "planar": planar,
            "curved": faces - planar, "bspline": kinds.get("BSplineSurface", 0),
            "cylindrical": kinds.get("Cylinder", 0), "conical": kinds.get("Cone", 0),
            "spherical": kinds.get("Sphere", 0), "toroidal": kinds.get("Toroid", 0),
            "extruded": kinds.get("SurfaceOfExtrusion", 0),
            "revolved": kinds.get("SurfaceOfRevolution", 0),
            "edges": len(sh.Edges), "vertices": len(sh.Vertexes), "solids": len(sh.Solids),
            "valid": bool(sh.isValid()), "volume_mm3": round(sh.Volume, 3),
            "bbox": [round(v, 3) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)],
        })
    if not out:
        raise OpError("not_found", f"No solid geometry in {sorted(want) or 'the model'}")
    return {"objects": out}


def _render_module():
    """The clay renderer, loaded by path: an editable install can otherwise shadow `cadswarm`."""
    import importlib.util
    path = os.path.join(PACKAGE_ROOT, "cadswarm", "render.py")
    spec = importlib.util.spec_from_file_location("cadswarm_render", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _auto_deviation():
    """A tessellation tolerance sized to the model, so the default render fits the triangle budget.

    FreeCAD caches a shape's triangulation, so re-tessellating coarser in the same process does not
    help: pick the tolerance before the first call instead.
    """
    box = None
    for o in doc.Objects:
        sh = getattr(o, "Shape", None)
        if sh is not None and not sh.isNull() and sh.Faces:
            box = sh.BoundBox if box is None else box.united(sh.BoundBox)
    if box is None:
        return 2.0
    diagonal = math.sqrt(box.XLength ** 2 + box.YLength ** 2 + box.ZLength ** 2)
    return max(1.0, min(50.0, diagonal / 700.0))


def render_mesh(shape, deviation):
    """(points, triangles) for a render, from a fresh copy: a shape keeps its first triangulation, so a coarser
    retry on the same shape returns the fine one again. The angular limit keeps small curved detail in check."""
    try:
        import MeshPart
    except ImportError:
        return shape.copy(True, False).tessellate(deviation)
    return MeshPart.meshFromShape(Shape=shape.copy(True, False), LinearDeflection=deviation,
                                  AngularDeflection=0.5, Relative=False).Topology


def q_render_views(a):
    """Orthographic clay renders (PNG) so a helper or person can see the model, not just its numbers."""
    render_meshes = _render_module().render_meshes
    want = set(a.get("names") or [])
    chosen = a.get("deviation")
    deviation = float(chosen or _auto_deviation())
    solid = [o for o in doc.Objects if getattr(o, "Shape", None) is not None and not o.Shape.isNull()
             and o.Shape.Faces and (not want or o.Name in want or o.Label in want)]
    # Draw what FreeCAD shows: a hidden part rendered as if it were there (a hidden FrontBumper made the nose look
    # closed while the person saw a hole), and a boolean's hidden inputs drew over its result. Named parts always draw.
    hidden = [] if want else [o.Name for o in solid if not getattr(o, "Visibility", True)]
    shapes = [(o.Name, o.Shape) for o in solid if o.Name not in hidden]
    if not shapes:
        raise OpError("not_found", f"No solid geometry in {sorted(want) or 'the model'}")
    # A size-based deviation still overshot on a car (grille mesh, spoked wheels): unless the caller chose
    # one, coarsen until the render fits.
    for _ in range(1 if chosen else 4):
        meshes = [(name, *render_mesh(sh, deviation)) for name, sh in shapes]
        total = sum(len(m[2]) for m in meshes)
        if total <= 950_000:
            break
        deviation *= 2
    if total > 950_000:
        raise OpError("bad_args", f"{total} triangles at deviation {deviation:.1f} mm is too many for a "
                                 f"render (950000 max); pass a coarser deviation, e.g. {deviation * 2:.1f}")
    # DOC_PATH, not doc.FileName: a document not saved yet has no FileName, and the worker's cwd
    # (a checkout, in tests) is no place for renders.
    root = os.path.dirname(DOC_PATH)
    out_dir = os.path.join(root, "renders", time.strftime("%Y-%m-%d %H%M%S"))
    try:
        files = render_meshes(meshes, a.get("views") or None, int(a.get("size") or 900), out_dir)
    except ValueError as e:
        raise OpError("bad_args", str(e))
    return {"files": files, "dir": out_dir, "objects": [m[0] for m in meshes], "hidden": hidden,
            "triangles": total, "deviation": round(deviation, 2)}


QUERIES = {"status": q_status, "get_object": q_get_object, "brep": q_brep,
           "interference": q_interference, "export": q_export,
           "param_users": q_param_users, "dependents": q_dependents,
           "faces": q_faces,
           "surface_quality": q_surface_quality,
           "render_views": q_render_views,
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
