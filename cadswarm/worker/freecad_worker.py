"""CadSwarm FreeCAD worker.

Runs inside FreeCAD's bundled Python (NOT the hub's venv) and owns the single
live shared document. The hub talks to it over JSON lines on stdin/stdout,
one request at a time, so every mutation of the model is serialized — FreeCAD
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
import json
import math
import os
import re
import sys
import time
import threading
import traceback

import sandbox  # noqa: E402  (next to this file; the worker runs as a plain script)

FREECAD_BIN = os.environ.get("CADSWARM_FREECAD_BIN")
# FreeCAD's module sits beside python on Windows (bin/) and in lib/ in the macOS and Linux bundles.
for _d in ([FREECAD_BIN, os.path.join(FREECAD_BIN, os.pardir, "lib")] if FREECAD_BIN else []):
    if os.path.isdir(_d) and os.path.abspath(_d) not in sys.path:
        sys.path.append(os.path.abspath(_d))

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


def open_document(path):
    global DOC_PATH, doc
    DOC_PATH = os.path.abspath(path)
    if os.path.exists(DOC_PATH):
        doc = FreeCAD.openDocument(DOC_PATH)
    else:
        doc = FreeCAD.newDocument("CadSwarm")
    doc.UndoMode = 1  # required: without it abortTransaction() silently keeps changes
    if doc.getObject(PARAMS) is None:
        sheet = doc.addObject("Spreadsheet::Sheet", PARAMS)
        sheet.set("A1", "name")
        sheet.set("B1", "value")
        doc.recompute()
        save()


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
                              "program probably has it open. Your change was rolled back — try again.")
            time.sleep(SAVE_BACKOFF * (attempt + 1))


# --------------------------------------------------------------------------
# Fingerprints: what counts as "this op changed that object"
# --------------------------------------------------------------------------

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
    """Closed XY polygon (+ optional round holes) extruded along Z — a static Part::Feature."""
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
    obj.Placement = to_placement(a.get("placement"))
    return {"name": obj.Name, "placement": placement_json(obj.Placement)}


def op_delete(a):
    obj = get_obj(a.get("name"))
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
    sheet = get_obj(PARAMS)
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
}


def mutate(op, args, allowed):
    before = snapshot()
    doc.openTransaction(f"cadswarm:{op}")
    try:
        result = MUTATIONS[op](args)
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
        save()
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        doc.recompute()
        raise
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


def obj_summary(o):
    info = {"name": o.Name, "label": o.Label, "type": o.TypeId,
            "visible": bool(getattr(o, "Visibility", True)),
            "uses": sorted(x.Name for x in o.OutList), "used_by": sorted(x.Name for x in o.InList),
            "expressions": {p: e for p, e in (o.ExpressionEngine or [])},
            "broken": is_broken(o)}
    if hasattr(o, "Placement"):
        info["placement"] = placement_json(o.Placement)
    shape = getattr(o, "Shape", None)
    if shape is not None and not shape.isNull():
        bb = shape.BoundBox
        info["bbox"] = [round(v, 3) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)]
        info["volume"] = round(shape.Volume, 3) if shape.Solids else 0.0
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


def q_interference(a):
    names = a.get("names") or [o.Name for o in doc.Objects
                               if getattr(o, "Visibility", False) and hasattr(o, "Shape")
                               and not o.Shape.isNull() and o.Shape.Solids]
    shapes = [(n, get_obj(n).Shape) for n in names]
    tol = float(a.get("min_volume", 1e-3))
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
    bb = shape.BoundBox
    out = {"names": names, "bbox": [round(v, 3) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)],
           "faces": fea_runner.face_table(shape)[:300], "face_count": len(shape.Faces), "warnings": warnings}
    if a.get("select"):
        try:
            out["selected"] = {s: fea_runner.select_faces(shape, [s]) for s in a["select"]}
        except fea_runner.FeaError as e:
            raise OpError(e.code, str(e))
    return out


def q_fea_shape(a):
    """A BREP copy of the target(s) for the FEA process; the live document is only read."""
    names = a.get("names") or []
    shapes = [get_obj(n).Shape for n in names]
    fea_target(names)  # same checks as list_faces, so errors show up before a job is queued
    return {"brep": (Part.makeCompound(shapes) if len(shapes) > 1 else shapes[0]).exportBrepToString()}


QUERIES = {"status": q_status, "get_object": q_get_object, "brep": q_brep,
           "interference": q_interference, "export": q_export,
           "param_users": q_param_users, "dependents": q_dependents,
           "faces": q_faces, "fea_shape": q_fea_shape,
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
