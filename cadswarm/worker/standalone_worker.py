"""CADSwarm-owned implementation of the shared model worker's JSON-lines protocol.

This process is started by cadswarm-host and edits a .cadswarm feature document. It deliberately has no
FreeCAD dependency. It covers the collaborative standalone part-building operations currently supported;
unsupported operations return a typed error instead of silently substituting different geometry.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import struct
import sys
import traceback

from cadswarm.app.standalone_engine import ModelError, StandaloneModel


class OpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _name(value):
    """Preserve the shared model's object names and reject names the document cannot safely reference."""
    name = str(value or "")
    if not name or len(name) > 64 or not name[0].isascii() or not name[0].isalpha() or \
            any(not ch.isascii() or not (ch.isalnum() or ch == "_") for ch in name):
        raise OpError("bad_args", "name must start with a letter and contain only ASCII letters, numbers or underscores")
    return name


def _dimensions(kind, props):
    props = props or {}
    maps = {
        "box": {"Length": "length", "Width": "width", "Height": "height"},
        "cylinder": {"Radius": "radius", "Height": "height"},
        "sphere": {"Radius": "radius"},
    }
    if kind not in maps:
        raise OpError("unsupported", f"The standalone engine does not yet support the {kind!r} primitive.")
    defaults = {"box": {"length": 10, "width": 10, "height": 10},
                "cylinder": {"radius": 5, "height": 10}, "sphere": {"radius": 5}}[kind]
    out = dict(defaults)
    for key, value in props.items():
        dimension = maps[kind].get(key, key if key in defaults else None)
        if dimension is None:
            raise OpError("unsupported", f"Standalone primitive property {key!r} is not supported yet.")
        out[dimension] = value
    return out


def _placement(value):
    if not value:
        return None
    base = value.get("base", [0, 0, 0])
    rotation = value.get("rotation") or {}
    return {"base": base, "rotation": {"axis": rotation.get("axis", [0, 0, 1]),
                                        "angle": rotation.get("angle", 0)}}


def _features(model):
    return model.feature_tree()["features"]


def _feature(model, name):
    for feature in _features(model):
        if feature["name"] == name or feature["id"] == name:
            return feature
    raise OpError("not_found", f"No feature named {name!r}.")


def _check_locked(names, allowed):
    if allowed is None:
        return
    missing = sorted(set(names) - set(allowed))
    if missing:
        raise OpError("lock_violation", f"This operation would modify features you have not locked: {missing}.")


def _parameter_mm(value):
    """Parse a shared dimension value into millimetres without evaluating user code."""
    if isinstance(value, (int, float)):
        return float(value)
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(mm|millimet(?:er|re)s?|cm|m|in|inch|inches)?\s*", str(value), re.I)
    if not match:
        raise OpError("bad_args", "Parameter values must be a number with optional mm, cm, m or inch units.")
    number = float(match.group(1))
    unit = (match.group(2) or "mm").lower()
    factor = {"mm": 1, "millimeter": 1, "millimeters": 1, "millimetre": 1, "millimetres": 1,
              "cm": 10, "m": 1000, "in": 25.4, "inch": 25.4, "inches": 25.4}[unit]
    return number * factor


def _project_file(model, value):
    """Resolve an import under this team's shared files directory, never an arbitrary host path."""
    project_files = os.path.realpath(os.path.join(os.path.dirname(model.path), "files"))
    supplied = os.path.expanduser(str(value or ""))
    path = os.path.realpath(supplied if os.path.isabs(supplied) else os.path.join(project_files, supplied))
    try:
        inside = os.path.commonpath((project_files, path))
    except ValueError as exc:
        raise OpError("bad_args", "Imported files must be inside the team's shared files folder.") from exc
    if os.path.normcase(inside) != os.path.normcase(project_files):
        raise OpError("bad_args", "Imported files must be inside the team's shared files folder.")
    if not os.path.isfile(path):
        raise OpError("not_found", f"No shared import file was found at {value!r}.")
    return path


def _contains_reference(value, feature_id):
    if isinstance(value, dict):
        return any(_contains_reference(item, feature_id) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_reference(item, feature_id) for item in value)
    return value == feature_id


def _descendants(model, names):
    """Return the dependency closure, including sketch-feature references added by the new model."""
    features = _features(model)
    by_name = {item["name"]: item for item in features}
    by_id = {item["id"]: item for item in features}
    refs = {by_name[name]["id"] for name in names if name in by_name}
    found = set()
    while True:
        added = {feature["id"] for feature in features if feature["id"] not in refs and
                 any(_contains_reference(value, ref) for ref in refs for key, value in feature.items()
                     if key not in ("id", "name"))}
        added -= found
        if not added:
            break
        found.update(added)
        refs.update(added)
    return sorted(by_id[fid]["name"] for fid in found)


def _status(model):
    tree = model.feature_tree()
    objects = []
    shapes = model._rebuild(model._state)
    by_id = {feature["id"]: feature for feature in tree["features"]}
    reverse = {feature["id"]: _descendants(model, [feature["name"]]) for feature in tree["features"]}
    for feature in tree["features"]:
        if feature.get("kind") == "sketch":
            objects.append({"name": feature["name"], "label": feature.get("label") or feature["name"],
                            "type": "CADSwarm::Sketch", "visible": bool(feature.get("visible", True)),
                            "uses": [], "used_by": reverse.get(feature["id"], []),
                            "expressions": {}, "broken": feature.get("solver", {}).get("status") == "conflicting",
                            "bbox": [], "volume": 0.0,
                            "solver": feature.get("solver", {}), "plane": feature.get("plane", "XY")})
            continue
        shape = shapes[feature["id"]]
        box = shape.bounding_box()
        uses = sorted(item["name"] for item in tree["features"]
                      if item["id"] != feature["id"] and _contains_reference(feature, item["id"]))
        objects.append({
            "name": feature["name"],
            "label": feature.get("label") or feature["name"],
            "type": feature["kind"],
            "visible": bool(feature.get("visible", True)),
            "bbox": [round(v, 3) for v in (box.min.X, box.min.Y, box.min.Z,
                                             box.max.X, box.max.Y, box.max.Z)],
            "volume": round(float(getattr(shape, "volume", 0) or 0), 6),
            "uses": uses,
            "used_by": reverse.get(feature["id"], []),
            "expressions": feature.get("expressions", {}), "broken": False,
        })
    objects.insert(0, {"name": "Params", "label": "Params", "type": "CADSwarm::Parameters",
                       "visible": True, "uses": [], "used_by": [], "expressions": {}, "broken": False,
                       "bbox": [], "volume": 0.0, "properties": tree.get("params", {})})
    return {"objects": objects, "params": tree.get("params", {}), "doc_path": model.path,
            "revision": tree["revision"], "units": "mm"}


def _mesh_stl(model, names=None, max_bytes=150_000, tolerance=0.5):
    data = model.mesh_data(tolerance)
    selected = set(names or [])
    meshes = {}
    for item in data["objects"]:
        if selected and item["name"] not in selected:
            continue
        vertices, triangles = item["vertices"], item["triangles"]
        stride = max(1, math.ceil(len(triangles) / max(1, int(max_bytes) // 50)))
        chosen = triangles[::stride]
        raw = bytearray(80)
        header = b"CADSwarm standalone mesh"
        raw[:len(header)] = header
        raw.extend(struct.pack("<I", len(chosen)))
        for tri in chosen:
            a, b, c = (vertices[i] for i in tri)
            ab = [b[i] - a[i] for i in range(3)]
            ac = [c[i] - a[i] for i in range(3)]
            normal = [ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2],
                      ab[0] * ac[1] - ab[1] * ac[0]]
            mag = math.sqrt(sum(x * x for x in normal)) or 1.0
            normal = [x / mag for x in normal]
            raw.extend(struct.pack("<12fH", *(normal + a + b + c), 0))
        meshes[item["name"]] = base64.b64encode(raw).decode("ascii")
    return {"meshes": meshes}


def _snapshot(model):
    return {f["name"]: f for f in _features(model)}


def _mutate(model, op, args, allowed):
    before = _snapshot(model)
    result = {}
    if op == "create_primitive":
        kind = str(args.get("kind", "")).lower()
        name = _name(args.get("name"))
        feature = model.create_primitive(kind, name, _dimensions(kind, args.get("props")),
                                         _placement(args.get("placement")), args.get("label"),
                                         args.get("expressions"))
        result = {"name": feature["name"]}
    elif op == "create_extrusion":
        name = _name(args.get("name"))
        points = args.get("points") or []
        feature = model.create_polygon_extrude(name, points, args.get("height", 1), args.get("holes"),
                                               _placement(args.get("placement")), args.get("label"))
        result = {"name": feature["name"]}
    elif op == "create_sketch":
        name = _name(args.get("name"))
        feature = model.create_sketch(name, args.get("plane"), args.get("entities") or [],
                                      args.get("constraints") or [], args.get("label"))
        result = {"name": feature["name"], "feature": feature}
    elif op in ("edit_sketch", "update_sketch"):
        target = _feature(model, args.get("name"))
        _check_locked((target["name"],), allowed)
        feature = model.update_sketch(target["name"], args.get("entities"), args.get("constraints"))
        result = {"name": target["name"], "feature": feature}
    elif op == "create_sketch_feature":
        name = _name(args.get("name"))
        inputs = []
        for key in ("sketch_name", "source_name"):
            if args.get(key):
                inputs.append(_feature(model, args[key])["name"])
        _check_locked(inputs, allowed)
        feature = model.create_sketch_feature(args.get("kind"), name, args.get("sketch_name"),
                                              args.get("parameters"), args.get("source_name"),
                                              args.get("selection"), args.get("label"))
        result = {"name": feature["name"], "feature": feature}
    elif op == "boolean":
        kind = {"fuse": "union", "cut": "subtract", "common": "intersect"}.get(
            str(args.get("kind", "")).lower())
        if not kind:
            raise OpError("bad_args", "Boolean kind must be fuse, cut or common.")
        base, tool = _feature(model, args.get("base")), _feature(model, args.get("tool"))
        name = _name(args.get("name"))
        _check_locked((base["name"], tool["name"]), allowed)
        feature = model.combine(kind, name, base["name"], tool["name"], args.get("label"))
        result = {"name": feature["name"], "consumed": [base["name"], tool["name"]]}
    elif op in ("set_property", "set_props"):
        target = _feature(model, args.get("name"))
        _check_locked((target["name"],), allowed)
        props = {args["prop"]: args.get("value")} if op == "set_property" else (args.get("props") or {})
        aliases = {"Length": "length", "Width": "width", "Height": "height", "Radius": "radius",
                   "depth": "depth", "width": "width", "height": "height", "length": "length",
                   "radius": "radius"}
        aliases.update({key: key for key in (target.get("parameters") or {})})
        dims = {aliases[k]: v for k, v in props.items() if k in aliases}
        if len(dims) != len(props):
            raise OpError("unsupported", "One or more supplied properties are not editable by the standalone engine.")
        expressions = args.get("expressions") or {}
        dimension_expressions = {aliases[key]: value for key, value in expressions.items() if key in aliases}
        if len(dimension_expressions) != len(expressions):
            raise OpError("unsupported", "Only dimension expressions are supported by the standalone feature document.")
        if "parameters" in target:
            if dimension_expressions:
                raise OpError("unsupported", "Expressions are currently supported on primitive dimensions only.")
            params = dict(target.get("parameters") or {})
            params.update(dims)
            model.set_feature_parameters(target["name"], params)
        else:
            model.set_dimensions(target["name"], dims, dimension_expressions)
        result = {"name": target["name"]}
    elif op == "set_expression":
        target = _feature(model, args.get("name"))
        _check_locked((target["name"],), allowed)
        if "parameters" in target:
            raise OpError("unsupported", "Expressions can currently be bound to primitive dimensions only.")
        dimension = {"Length": "length", "Width": "width", "Height": "height", "Radius": "radius"}.get(
            args.get("prop"), args.get("prop"))
        model.set_expression(target["name"], dimension, args.get("expr"))
        result = {"name": target["name"]}
    elif op == "set_param":
        _check_locked(("Params",), allowed)
        changed = model.set_param(str(args.get("alias", "")), _parameter_mm(args.get("value")))
        result = {**changed, "cell": "Params." + changed["alias"]}
    elif op == "set_placement":
        target = _feature(model, args.get("name"))
        _check_locked((target["name"],), allowed)
        changed = model.set_placement(target["name"], _placement(args.get("placement")))
        result = {"name": target["name"], "placement": changed["placement"]}
    elif op == "delete":
        if args.get("name") == "Params":
            raise OpError("not_allowed", "Params is the shared dimensions sheet and cannot be deleted.")
        target = _feature(model, args.get("name"))
        descendants = _descendants(model, [target["name"]])
        _check_locked([target["name"], *(descendants if args.get("force") else [])], allowed)
        result = model.delete_feature(target["name"], bool(args.get("force")))
    elif op == "translate_feature":
        name = _name(args.get("name"))
        source = _feature(model, args.get("source"))
        _check_locked((source["name"],), allowed)
        feature = model.translate_feature(name, source["name"], args.get("x", 0), args.get("y", 0), args.get("z", 0))
        result = {"name": feature["name"]}
    elif op == "insert_component":
        path = _project_file(model, args.get("path") or args.get("file"))
        if not path or not str(path).lower().endswith((".step", ".stp", ".stl")):
            raise OpError("bad_args", "Standalone imports support STEP/STP and STL files.")
        name = _name(args.get("name"))
        feature = (model.import_stl(path, name) if str(path).lower().endswith(".stl")
                   else model.import_step(path, name))
        result = {"name": feature["name"], "component": args.get("component")}
    elif op == "insert_mesh":
        path = args.get("file")
        if not path:
            raise OpError("bad_args", "Provide the STL mesh file path.")
        project_files = os.path.realpath(os.path.join(os.path.dirname(model.path), "files"))
        path = os.path.realpath(path if os.path.isabs(path) else os.path.join(project_files, path))
        try:
            if os.path.commonpath((project_files, path)) != project_files:
                raise OpError("bad_args", "Mesh files must be inside the project's files folder.")
        except ValueError as exc:
            raise OpError("bad_args", "Mesh files must be inside the project's files folder.") from exc
        name = _name(args.get("name"))
        feature = model.import_stl(path, name)
        result = {"name": feature["name"], "triangles": 0}
    else:
        raise OpError("unsupported", f"The standalone worker does not support operation {op!r} yet.")

    after = _snapshot(model)
    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(name for name in set(before) & set(after) if before[name] != after[name])
    recomputed = set(_descendants(model, modified)) - set(modified) - set(created) - set(deleted)
    if op == "set_param":
        users = model.param_users(str(args.get("alias", "")))
        recomputed.update(users)
        recomputed.update(_descendants(model, users))
    recomputed.difference_update(created)
    recomputed.difference_update(deleted)
    recomputed.difference_update(modified)
    return {"result": result, "created": created, "modified": modified, "deleted": deleted,
            "recomputed": sorted(recomputed), "broken": []}


def handle(model, request):
    op, args = request.get("op"), request.get("args") or {}
    if op == "ping":
        return {"ok": True, "result": {"pong": True, "engine": "cadswarm-standalone"}}
    if op == "status":
        return {"ok": True, "result": _status(model)}
    if op == "get_object":
        if args.get("name") == "Params":
            return {"ok": True, "result": next(item for item in _status(model)["objects"] if item["name"] == "Params")}
        feature = _feature(model, args.get("name"))
        summary = next((item for item in _status(model)["objects"] if item["name"] == feature["name"]), {})
        summary["properties"] = feature.get("dimensions", feature.get("parameters", {}))
        summary["feature"] = feature
        return {"ok": True, "result": summary}
    if op == "mesh":
        return {"ok": True, "result": _mesh_stl(model, args.get("names"), args.get("max_bytes", 150_000),
                                                   args.get("tolerance", 0.5))}
    if op == "interference":
        tree = model.feature_tree()["features"]
        selected = set(args.get("names") or [])
        features = [f for f in tree if f.get("kind") != "sketch" and f.get("visible", True)
                    and (not selected or f["name"] in selected)]
        shapes = model._rebuild(model._state)
        tolerance = float(args.get("tolerance", 0.01))
        clashes = []
        for index, left in enumerate(features):
            for right in features[index + 1:]:
                if model._is_mesh_reference(model._state, left) or model._is_mesh_reference(model._state, right):
                    continue
                overlap = shapes[left["id"]].intersect(shapes[right["id"]])
                volume = float(overlap.volume or 0)
                if volume > tolerance:
                    clashes.append({"a": left["name"], "b": right["name"], "volume": round(volume, 4)})
        return {"ok": True, "result": {"checked": [f["name"] for f in features], "clashes": clashes}}
    if op == "export":
        path = args.get("path")
        fmt = str(args.get("format", "step")).lower()
        if not path:
            raise OpError("bad_args", "Export path is required.")
        selected = set(args.get("names") or [])
        result = model.export(path, fmt, args.get("names"))
        return {"ok": True, "result": {"path": result["path"], "objects": [f["name"] for f in _features(model)
                                                                                   if f.get("visible", True) and
                                                                                   (not selected or f["name"] in selected)]}}
    if op == "dependents":
        target = _feature(model, args.get("name"))
        return {"ok": True, "result": {"dependents": _descendants(model, [target["name"]])}}
    if op == "param_users":
        return {"ok": True, "result": {"alias": args.get("alias"), "used_by": model.param_users(str(args.get("alias", "")))}}
    if op in {"create_primitive", "create_extrusion", "create_sketch", "edit_sketch", "update_sketch",
              "create_sketch_feature", "boolean", "set_property", "set_props", "set_expression",
              "set_placement", "delete", "set_param", "translate_feature", "run_part_script", "insert_component",
              "insert_standard_part", "insert_mesh"}:
        if op in {"run_part_script", "insert_standard_part"}:
            raise OpError("unsupported", f"The standalone worker does not support operation {op!r} yet.")
        return {"ok": True, **_mutate(model, op, args, request.get("allowed"))}
    raise OpError("unsupported", f"The standalone worker does not support query {op!r} yet.")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.getcwd(), "model.cadswarm")
    model = StandaloneModel(path)
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    out.write(json.dumps({"ready": True, "doc": model.path, "engine": "cadswarm-standalone"}) + "\n")
    out.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            response = handle(model, request)
        except OpError as exc:
            response = {"ok": False, "code": exc.code, "error": str(exc)}
        except (ModelError, RuntimeError, OSError) as exc:
            response = {"ok": False, "code": "op_error", "error": str(exc)}
        except Exception as exc:
            response = {"ok": False, "code": "op_error", "error": f"{type(exc).__name__}: {exc}",
                        "trace": traceback.format_exc(limit=3)}
        response["id"] = request_id
        out.write(json.dumps(response, default=str) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
