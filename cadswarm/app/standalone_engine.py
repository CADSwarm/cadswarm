"""Independent parametric document engine for the CADSwarm desktop app and MCP server.

This layer deliberately has no FreeCAD imports. build123d supplies OpenCascade B-rep geometry; the
document, feature history, persistence, locking and MCP contract belong to CADSwarm.
"""

from __future__ import annotations

import copy
import ast
import json
import math
import os
import re
import shutil
import tempfile
import threading
import uuid
from contextlib import contextmanager


class ModelError(ValueError):
    """A user-facing model operation error."""


_NAME = re.compile(r"^(?!params$)[A-Za-z][A-Za-z0-9_]{0,63}$", re.IGNORECASE)
_PRIMITIVES = {"box", "cylinder", "sphere"}
_OPERATIONS = {"union", "subtract", "intersect"}
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _placed(shape, placement):
    if not placement:
        return shape
    from build123d import Axis, Location, Rotation, Vector

    base = placement.get("base", [0, 0, 0])
    rotation = placement.get("rotation") or {}
    axis = rotation.get("axis", [0, 0, 1])
    angle = float(rotation.get("angle", 0))
    orientation = Rotation(Axis((0, 0, 0), Vector(*axis)), angle) if angle else Rotation()
    return shape.moved(Location(tuple(float(v) for v in base), orientation))


def _expression_value(expression, params):
    """Evaluate the arithmetic subset used for shared CAD dimensions; never execute Python source."""
    try:
        tree = ast.parse(str(expression), mode="eval")
    except SyntaxError as exc:
        raise ModelError(f"Invalid dimension expression: {exc.msg}.") from exc

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in params:
            return float(params[node.id])
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "Params":
            if node.attr not in params:
                raise ModelError(f"Shared parameter Params.{node.attr} does not exist.")
            return float(params[node.attr])
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left, right = visit(node.left), visit(node.right)
            try:
                return _BINOPS[type(node.op)](left, right)
            except (ArithmeticError, OverflowError) as exc:
                raise ModelError(f"Dimension expression could not be evaluated: {exc}.") from exc
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
            return _UNARYOPS[type(node.op)](visit(node.operand))
        raise ModelError("Dimension expressions support numbers, Params.name and basic arithmetic only.")

    value = visit(tree)
    if not math.isfinite(value) or value <= 0:
        raise ModelError("A resolved dimension expression must produce a finite value greater than zero.")
    return value


_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
           ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b, ast.Mod: lambda a, b: a % b}
_UNARYOPS = {ast.UAdd: lambda a: a, ast.USub: lambda a: -a}


def _resolved_dimensions(state, feature):
    values = copy.deepcopy(feature.get("dimensions", {}))
    for dimension, expression in feature.get("expressions", {}).items():
        values[dimension] = _expression_value(expression, state.get("params", {}))
    return values


def _solve_sketch(entities, constraints):
    from .sketch_solver import SketchSolveError, solve

    try:
        return solve(entities, constraints)
    except SketchSolveError as exc:
        raise ModelError(str(exc)) from exc


def _sketch_loops(entities):
    """Join line/arc entities into closed planar wires, preserving the stored geometry."""
    segments = []
    for entity in entities:
        kind = entity.get("type")
        if kind == "line":
            segments.append([tuple(map(float, entity["start"])), tuple(map(float, entity["end"]))])
        elif kind == "arc":
            center = entity["center"]
            radius = float(entity["radius"])
            start, end = float(entity["start_angle"]), float(entity["end_angle"])
            sweep = (end - start) % (2 * math.pi)
            if math.isclose(sweep, 0.0, abs_tol=1e-9):
                sweep = 2 * math.pi
            count = max(8, min(96, math.ceil(sweep * max(radius, 1) / 0.75)))
            segments.append([(float(center[0]) + radius * math.cos(start + sweep * i / count),
                              float(center[1]) + radius * math.sin(start + sweep * i / count))
                             for i in range(count + 1)])
    loops = []
    tolerance = 1e-6
    while segments:
        path = segments.pop(0)
        start, tail = path[0], path[-1]
        while math.dist(tail, start) > tolerance:
            match = None
            for index, segment in enumerate(segments):
                if math.dist(segment[0], tail) <= tolerance:
                    match = (index, segment)
                    break
                if math.dist(segment[-1], tail) <= tolerance:
                    match = (index, list(reversed(segment)))
                    break
            if match is None:
                raise ModelError("A sketch profile must contain closed, connected line or arc loops.")
            index, segment = match
            segments.pop(index)
            path.extend(segment[1:])
            tail = path[-1]
        loops.append(path[:-1])
    return loops


def _add_sketch_profiles(feature, BuildLine, Polyline, Circle, Locations, Mode, make_face):
    """Add solved sketch loops and exact circles to an active sketch builder."""
    entities = feature.get("solved_entities") or feature.get("entities") or []
    loops = _sketch_loops(entities)
    if loops:
        with BuildLine():
            for loop in loops:
                Polyline(*loop, close=True)
        make_face()
    for entity in entities:
        if entity.get("type") == "circle":
            center = tuple(float(v) for v in entity["center"])
            with Locations(center):
                Circle(float(entity["radius"]), mode=Mode.ADD)


def _build123d():
    try:
        from build123d import (  # type: ignore[import-not-found]
            Box,
            BuildSketch,
            BuildPart,
            Circle,
            Compound,
            Cylinder,
            Location,
            Mode,
            Sphere,
            Rectangle,
            add,
            import_step,
            export_step,
            export_stl,
            extrude,
        )
    except ImportError as exc:
        raise RuntimeError("CADSwarm's standalone model engine needs the 'desktop' extra: pip install .[desktop]") from exc
    return (Box, BuildSketch, BuildPart, Circle, Compound, Cylinder, Location, Mode, Rectangle, Sphere, add,
            import_step, export_step, export_stl, extrude)


@contextmanager
def _locked(path: str):
    """Serialize file-backed edits across the desktop process and its MCP process."""
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(lock_path, threading.RLock())
    with thread_lock, open(lock_path, "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class StandaloneModel:
    """A JSON feature history replayed into exact OpenCascade solids on demand."""

    FORMAT = "cadswarm-model"
    VERSION = 1

    def __init__(self, path: str):
        self.path = os.path.abspath(os.path.expanduser(path))
        self._state = self._empty_state()
        self.reload()

    @classmethod
    def _empty_state(cls):
        return {"format": cls.FORMAT, "version": cls.VERSION, "revision": 0, "units": "mm", "features": [],
                "params": {}, "undo": [], "redo": []}

    def _read_state(self):
        if not os.path.isfile(self.path):
            return self._empty_state()
        try:
            with open(self.path, encoding="utf-8") as source:
                state = json.load(source)
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelError(f"Could not read the CADSwarm project: {exc}") from exc
        if state.get("format") != self.FORMAT or state.get("version") != self.VERSION:
            raise ModelError("This project uses an unsupported CADSwarm model format.")
        if not isinstance(state.get("features"), list):
            raise ModelError("The project feature list is invalid.")
        state.setdefault("undo", [])
        state.setdefault("redo", [])
        state.setdefault("params", {})
        if not isinstance(state["params"], dict):
            raise ModelError("The project shared parameter table is invalid.")
        return state

    def _write_state(self, state):
        parent = os.path.dirname(self.path)
        os.makedirs(parent or ".", exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".cadswarm-", suffix=".tmp", dir=parent or ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                json.dump(state, target, indent=2)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def reload(self):
        with _locked(self.path):
            state = self._read_state()
            self._rebuild(state)
        self._state = state
        return self.revision

    def save(self):
        with _locked(self.path):
            state = self._read_state()
            self._rebuild(state)
            self._write_state(state)
        self._state = state
        return self.path

    @property
    def revision(self):
        return int(self._state.get("revision", 0))

    def _rebuild(self, state):
        (Box, BuildSketch, BuildPart, Circle, Compound, Cylinder, Location, Mode, Rectangle, Sphere, add,
         import_step, _, _, extrude) = _build123d()
        shapes = {}
        features = state["features"]
        ids = {feature.get("id") for feature in features}
        if len(ids) != len(features) or None in ids:
            raise ModelError("Every feature must have a unique stable id.")
        names = [feature.get("name") for feature in features]
        if len(set(names)) != len(names) or any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in names):
            raise ModelError("Feature names must be unique and use letters, numbers and underscores.")
        for feature in features:
            kind = feature.get("kind")
            if kind == "sketch":
                solved = _solve_sketch(feature.get("entities", []), feature.get("constraints", []))
                # Recompute from the definition so older projects that lack this cache still open.
                feature["solved_entities"] = solved["entities"]
                feature["solver"] = {key: solved[key] for key in ("dof", "status", "residual")}
                continue
            if kind in _PRIMITIVES:
                dims = _resolved_dimensions(state, feature)
                try:
                    if kind == "box":
                        shape = BuildPart()
                        with shape:
                            Box(float(dims["length"]), float(dims["width"]), float(dims["height"]))
                        shapes[feature["id"]] = _placed(shape.part, feature.get("placement"))
                    elif kind == "cylinder":
                        shape = BuildPart()
                        with shape:
                            Cylinder(float(dims["radius"]), float(dims["height"]))
                        shapes[feature["id"]] = _placed(shape.part, feature.get("placement"))
                    else:
                        shape = BuildPart()
                        with shape:
                            Sphere(float(dims["radius"]))
                        shapes[feature["id"]] = _placed(shape.part, feature.get("placement"))
                except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                    raise ModelError(f"Invalid dimensions for {feature.get('name', 'feature')}: {exc}") from exc
                continue
            if kind == "extrude":
                profile, dims = feature.get("profile"), _resolved_dimensions(state, feature)
                try:
                    builder = BuildPart()
                    with builder:
                        with BuildSketch():
                            if profile == "rectangle":
                                Rectangle(float(dims["width"]), float(dims["height"]))
                            elif profile == "circle":
                                Circle(float(dims["radius"]))
                            else:
                                raise ModelError(f"Unsupported sketch profile: {profile}")
                        extrude(amount=float(dims["depth"]))
                    shapes[feature["id"]] = _placed(builder.part, feature.get("placement"))
                except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                    if isinstance(exc, ModelError):
                        raise
                    raise ModelError(f"Invalid extrude feature {feature.get('name')}: {exc}") from exc
                continue
            if kind == "extrude_polygon":
                try:
                    from build123d import Locations, Polygon

                    builder = BuildPart()
                    with builder:
                        with BuildSketch():
                            Polygon(*feature["points"])
                            for hole in feature.get("holes", []):
                                with Locations((float(hole["x"]), float(hole["y"]))):
                                    Circle(float(hole["r"]), mode=Mode.SUBTRACT)
                        extrude(amount=float(_resolved_dimensions(state, feature)["depth"]))
                    shapes[feature["id"]] = _placed(builder.part, feature.get("placement"))
                except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                    raise ModelError(f"Invalid polygon extrusion {feature.get('name')}: {exc}") from exc
                continue
            if kind in ("sketch_extrude", "revolve", "pocket", "hole"):
                from build123d import (Axis, BuildLine, BuildPart, BuildSketch, Circle, Locations, Mode,
                                       Plane, Polyline, Vector, extrude, make_face, revolve, add)

                sketch = next((item for item in features if item.get("id") == feature.get("sketch")), None)
                if sketch is None or sketch.get("kind") != "sketch":
                    raise ModelError(f"Feature {feature.get('name')} refers to a missing sketch.")
                planes = {"XY": Plane.XY, "XZ": Plane.XZ, "YZ": Plane.YZ}
                plane_name = sketch.get("plane", "XY")
                if plane_name not in planes:
                    raise ModelError(f"Sketch {sketch.get('name')} uses an unsupported plane.")
                params = feature.get("parameters", {})
                try:
                    builder = BuildPart()
                    with builder:
                        if kind in ("pocket", "hole"):
                            source_id = feature.get("source")
                            if source_id not in shapes:
                                raise ModelError(f"Feature {feature.get('name')} refers to a missing source solid.")
                            add(shapes[source_id])
                        with BuildSketch(planes[plane_name]):
                            _add_sketch_profiles(sketch, BuildLine, Polyline, Circle, Locations, Mode, make_face)
                        if kind == "sketch_extrude":
                            extrude(amount=float(params.get("depth", 10)))
                        elif kind in ("pocket", "hole"):
                            depth = float(params.get("depth", 10))
                            if kind == "hole":
                                depth = float(params.get("depth", 10000))
                            direction = float(params.get("direction", -1))
                            if direction == 0:
                                raise ModelError("Pocket direction must be non-zero.")
                            extrude(amount=abs(depth) * (1 if direction > 0 else -1), mode=Mode.SUBTRACT)
                        else:
                            axis_name = str(params.get("axis", "Z")).upper()
                            directions = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}
                            if axis_name not in directions:
                                raise ModelError("Revolve axis must be X, Y or Z.")
                            allowed_axes = {"XY": {"X", "Y"}, "XZ": {"X", "Z"}, "YZ": {"Y", "Z"}}
                            if axis_name not in allowed_axes[plane_name]:
                                raise ModelError(f"Revolve axis {axis_name} must lie in the {plane_name} sketch plane.")
                            revolve(axis=Axis((0, 0, 0), Vector(*directions[axis_name])),
                                    revolution_arc=float(params.get("angle", 360)))
                    solid = builder.part
                    if solid is None or solid.is_empty:
                        raise ModelError(f"Feature {feature.get('name')} produced an empty solid.")
                    shapes[feature["id"]] = solid
                except ModelError:
                    raise
                except Exception as exc:
                    raise ModelError(f"Could not rebuild {kind} feature {feature.get('name')}: {exc}") from exc
                continue
            if kind in ("fillet", "chamfer"):
                source_id = feature.get("source")
                if source_id not in shapes:
                    raise ModelError(f"Feature {feature.get('name')} refers to a missing source solid.")
                source = shapes[source_id]
                selection = feature.get("selection") or []
                try:
                    edges = source.edges()
                    if selection:
                        indexes = [int(index) for index in selection]
                        if any(index < 0 or index >= len(edges) for index in indexes):
                            raise ModelError(f"{kind.title()} feature {feature.get('name')} selects a missing edge.")
                        edges = [edges[index] for index in indexes]
                    radius = float(feature.get("parameters", {}).get("radius", 1.0))
                    if radius <= 0 or not math.isfinite(radius):
                        raise ModelError(f"{kind.title()} radius must be finite and greater than zero.")
                    shapes[feature["id"]] = source.fillet(radius, edges) if kind == "fillet" else source.chamfer(radius, edges)
                except ModelError:
                    raise
                except Exception as exc:
                    raise ModelError(f"Could not rebuild {kind} feature {feature.get('name')}: {exc}") from exc
                continue
            if kind in ("linear_pattern", "circular_pattern"):
                from build123d import Axis, Compound, Location, Rotation, Vector

                source_id = feature.get("source")
                if source_id not in shapes:
                    raise ModelError(f"Feature {feature.get('name')} refers to a missing source solid.")
                params = feature.get("parameters", {})
                try:
                    count = int(params.get("count", 2))
                    if count < 2 or count > 1000:
                        raise ModelError("Pattern count must be between 2 and 1000.")
                    source = shapes[source_id]
                    copies = []
                    if kind == "linear_pattern":
                        direction = [float(value) for value in params.get("direction", [1, 0, 0])]
                        spacing = float(params.get("spacing", 10))
                        norm = math.sqrt(sum(value * value for value in direction))
                        if len(direction) != 3 or norm == 0 or not math.isfinite(spacing):
                            raise ModelError("Linear pattern needs a finite spacing and non-zero 3D direction.")
                        direction = [value / norm for value in direction]
                        copies = [source.moved(Location(tuple(value * spacing * index for value in direction)))
                                  for index in range(count)]
                    else:
                        direction = [float(value) for value in params.get("axis", [0, 0, 1])]
                        angle = float(params.get("angle", 360))
                        norm = math.sqrt(sum(value * value for value in direction))
                        if len(direction) != 3 or norm == 0 or not math.isfinite(angle):
                            raise ModelError("Circular pattern needs a finite angle and non-zero 3D axis.")
                        axis = Axis((0, 0, 0), Vector(*(value / norm for value in direction)))
                        copies = [source.moved(Location((0, 0, 0), Rotation(axis, angle * index / count)))
                                  for index in range(count)]
                    shapes[feature["id"]] = Compound(children=copies)
                except ModelError:
                    raise
                except Exception as exc:
                    raise ModelError(f"Could not rebuild {kind} feature {feature.get('name')}: {exc}") from exc
                continue
            if kind == "import_step":
                asset = feature.get("asset")
                if not isinstance(asset, str) or os.path.basename(asset) != asset:
                    raise ModelError(f"Imported feature {feature.get('name')} has an invalid asset reference.")
                asset_path = os.path.join(self._asset_dir(), asset)
                if not os.path.isfile(asset_path):
                    raise ModelError(f"The STEP asset for {feature.get('name')} is missing: {asset}")
                try:
                    shapes[feature["id"]] = _placed(import_step(asset_path), feature.get("placement"))
                except Exception as exc:
                    raise ModelError(f"Could not read STEP asset {asset}: {exc}") from exc
                continue
            if kind == "import_stl":
                asset = feature.get("asset")
                if not isinstance(asset, str) or os.path.basename(asset) != asset or not asset.lower().endswith(".stl"):
                    raise ModelError(f"Imported mesh {feature.get('name')} has an invalid asset reference.")
                asset_path = os.path.join(self._asset_dir(), asset)
                if not os.path.isfile(asset_path):
                    raise ModelError(f"The STL asset for {feature.get('name')} is missing: {asset}")
                try:
                    from build123d import import_stl

                    shapes[feature["id"]] = _placed(import_stl(asset_path), feature.get("placement"))
                except Exception as exc:
                    raise ModelError(f"Could not read STL asset {asset}: {exc}") from exc
                continue
            if kind == "translate":
                source_id = feature.get("source")
                if source_id not in shapes:
                    raise ModelError(f"Move feature {feature.get('name')} refers to a missing or later feature.")
                offset = feature.get("offset", {})
                try:
                    location = Location((float(offset["x"]), float(offset["y"]), float(offset["z"])))
                    shapes[feature["id"]] = shapes[source_id].moved(location)
                except (KeyError, TypeError, ValueError) as exc:
                    raise ModelError(f"Invalid offset for {feature.get('name')}: {exc}") from exc
                continue
            if kind == "boolean":
                left_id, right_id = feature.get("left"), feature.get("right")
                if left_id not in shapes or right_id not in shapes:
                    raise ModelError(f"Boolean feature {feature.get('name')} refers to a missing or later feature.")
                operation = feature.get("operation")
                left, right = shapes[left_id], shapes[right_id]
                try:
                    if operation == "union":
                        result = left.fuse(right)
                    elif operation == "subtract":
                        result = left.cut(right)
                    elif operation == "intersect":
                        result = left.intersect(right)
                    else:
                        raise ModelError(f"Unknown boolean operation: {operation}")
                except Exception as exc:  # OpenCascade reports several geometry-specific exception types.
                    raise ModelError(f"Could not {operation} {feature.get('name')}: {exc}") from exc
                if result is None or result.is_empty:
                    raise ModelError(f"The {operation} operation produced an empty shape.")
                shapes[feature["id"]] = result
                continue
            raise ModelError(f"Unsupported feature kind: {kind}")
        return shapes

    def _mutate(self, action):
        with _locked(self.path):
            state = self._read_state()
            shapes = self._rebuild(state)
            previous = {"features": copy.deepcopy(state["features"]),
                        "params": copy.deepcopy(state.get("params", {}))}
            result = action(state, shapes)
            self._rebuild(state)  # Validate the whole history before committing.
            state["undo"] = [*state.get("undo", []), previous][-50:]
            state["redo"] = []
            state["revision"] = int(state.get("revision", 0)) + 1
            self._write_state(state)
        self._state = state
        return result

    def undo(self):
        with _locked(self.path):
            state = self._read_state()
            history = state.get("undo", [])
            if not history:
                raise ModelError("There is no model operation to undo.")
            redo = [*state.get("redo", []), {"features": copy.deepcopy(state["features"]),
                                               "params": copy.deepcopy(state.get("params", {}))}][-50:]
            snapshot = history.pop()
            if isinstance(snapshot, dict):
                state["features"] = snapshot["features"]
                state["params"] = snapshot.get("params", {})
            else:  # v1 projects created before document snapshots were introduced
                state["features"] = snapshot
            state["undo"] = history
            state["redo"] = redo
            self._rebuild(state)
            state["revision"] = int(state.get("revision", 0)) + 1
            self._write_state(state)
        self._state = state
        return self.revision

    def redo(self):
        with _locked(self.path):
            state = self._read_state()
            history = state.get("redo", [])
            if not history:
                raise ModelError("There is no model operation to redo.")
            undo = [*state.get("undo", []), {"features": copy.deepcopy(state["features"]),
                                               "params": copy.deepcopy(state.get("params", {}))}][-50:]
            snapshot = history.pop()
            if isinstance(snapshot, dict):
                state["features"] = snapshot["features"]
                state["params"] = snapshot.get("params", {})
            else:
                state["features"] = snapshot
            state["undo"] = undo
            state["redo"] = history
            self._rebuild(state)
            state["revision"] = int(state.get("revision", 0)) + 1
            self._write_state(state)
        self._state = state
        return self.revision

    @staticmethod
    def _feature(state, name):
        for feature in state["features"]:
            if feature["name"] == name or feature["id"] == name:
                return feature
        raise ModelError(f"No feature named {name!r}.")

    @staticmethod
    def _is_mesh_reference(state, feature):
        if feature["kind"] == "import_stl":
            return True
        if feature["kind"] == "translate":
            source_id = feature.get("source")
            source = next((item for item in state["features"] if item.get("id") == source_id), None)
            return source is not None and StandaloneModel._is_mesh_reference(state, source)
        return False

    def create_primitive(self, kind: str, name: str, dimensions: dict, placement=None, label=None, expressions=None):
        kind = kind.lower()
        if kind not in _PRIMITIVES:
            raise ModelError("Primitive kind must be box, cylinder or sphere.")
        if not _NAME.fullmatch(name or ""):
            raise ModelError("Name must start with a letter and contain only letters, numbers or underscores.")
        expected = {"box": {"length", "width", "height"}, "cylinder": {"radius", "height"},
                    "sphere": {"radius"}}[kind]
        if set(dimensions) != expected:
            raise ModelError(f"{kind} dimensions must be exactly: {', '.join(sorted(expected))}.")
        try:
            values = {key: float(value) for key, value in dimensions.items()}
        except (TypeError, ValueError) as exc:
            raise ModelError("Dimensions must be numbers in millimetres.") from exc
        if any(not math.isfinite(value) or value <= 0 for value in values.values()):
            raise ModelError("All dimensions must be greater than zero.")

        expressions = expressions or {}
        if not isinstance(expressions, dict) or set(expressions) - expected:
            raise ModelError("Primitive expressions must reference dimensions on the selected primitive.")

        def action(state, _):
            if any(feature["name"] == name for feature in state["features"]):
                raise ModelError(f"A feature named {name!r} already exists.")
            feature = {"id": str(uuid.uuid4()), "name": name, "kind": kind, "dimensions": values,
                       "visible": True}
            if expressions:
                feature["expressions"] = {key: str(value) for key, value in expressions.items()}
                for expression in feature["expressions"].values():
                    _expression_value(expression, state.get("params", {}))
            if placement:
                feature["placement"] = copy.deepcopy(placement)
            if label:
                feature["label"] = str(label)
            state["features"].append(feature)
            return copy.deepcopy(feature)

        return self._mutate(action)

    def create_sketch(self, name: str, plane: str, entities, constraints=None, label=None):
        plane = str(plane or "XY").upper()
        if plane not in ("XY", "XZ", "YZ"):
            raise ModelError("Sketch plane must be XY, XZ or YZ.")
        if not _NAME.fullmatch(name or ""):
            raise ModelError("Name must start with a letter and contain only letters, numbers or underscores.")
        solved = _solve_sketch(entities, constraints or [])

        def action(state, _):
            if any(feature["name"] == name for feature in state["features"]):
                raise ModelError(f"A feature named {name!r} already exists.")
            feature = {"id": str(uuid.uuid4()), "name": name, "kind": "sketch", "plane": plane,
                       "entities": copy.deepcopy(entities), "constraints": copy.deepcopy(constraints or []),
                       "solved_entities": solved["entities"],
                       "solver": {key: solved[key] for key in ("dof", "status", "residual")},
                       "visible": True}
            if label:
                feature["label"] = str(label)
            state["features"].append(feature)
            return copy.deepcopy(feature)

        return self._mutate(action)

    def update_sketch(self, name: str, entities=None, constraints=None):
        """Replace a sketch definition atomically and recompute its constraint solution."""
        def action(state, _):
            feature = self._feature(state, name)
            if feature.get("kind") != "sketch":
                raise ModelError(f"{name!r} is not a sketch.")
            new_entities = copy.deepcopy(feature.get("entities", []) if entities is None else entities)
            new_constraints = copy.deepcopy(feature.get("constraints", []) if constraints is None else constraints)
            solved = _solve_sketch(new_entities, new_constraints)
            feature["entities"] = new_entities
            feature["constraints"] = new_constraints
            feature["solved_entities"] = solved["entities"]
            feature["solver"] = {key: solved[key] for key in ("dof", "status", "residual")}
            return copy.deepcopy(feature)

        return self._mutate(action)

    def create_sketch_feature(self, kind: str, name: str, sketch_name=None, parameters=None,
                              source_name=None, selection=None, label=None):
        """Append a parametric feature that consumes a sketch or an existing solid."""
        kind = str(kind or "").lower()
        kind = {"extrude": "sketch_extrude"}.get(kind, kind)
        supported = {"sketch_extrude", "revolve", "pocket", "hole", "fillet", "chamfer",
                     "linear_pattern", "circular_pattern"}
        if kind not in supported:
            raise ModelError("Feature must be extrude, revolve, pocket, hole, fillet, chamfer or a pattern.")
        if not _NAME.fullmatch(name or ""):
            raise ModelError("Name must start with a letter and contain only letters, numbers or underscores.")
        parameters = copy.deepcopy(parameters or {})
        selection = copy.deepcopy(selection or [])
        if not isinstance(parameters, dict) or not isinstance(selection, list):
            raise ModelError("Feature parameters must be an object and edge selection must be a list.")

        def positive(key, default):
            try:
                value = float(parameters.get(key, default))
            except (TypeError, ValueError) as exc:
                raise ModelError(f"{key.title()} must be a number.") from exc
            if not math.isfinite(value) or value <= 0:
                raise ModelError(f"{key.title()} must be finite and greater than zero.")
            parameters[key] = value

        if kind in ("sketch_extrude", "pocket"):
            positive("depth", 10)
        elif kind == "hole":
            positive("depth", 10000)
        elif kind in ("fillet", "chamfer"):
            positive("radius", 1)
        elif kind == "revolve":
            try:
                angle = float(parameters.get("angle", 360))
            except (TypeError, ValueError) as exc:
                raise ModelError("Revolve angle must be a number in degrees.") from exc
            if not math.isfinite(angle) or not 0 < angle <= 360:
                raise ModelError("Revolve angle must be greater than zero and no more than 360 degrees.")
            parameters["angle"] = angle
            parameters["axis"] = str(parameters.get("axis", "Z")).upper()
            if parameters["axis"] not in ("X", "Y", "Z"):
                raise ModelError("Revolve axis must be X, Y or Z.")
        elif kind == "linear_pattern":
            positive("spacing", 10)
            parameters["count"] = self._pattern_count(parameters.get("count", 2))
            direction = parameters.get("direction", [1, 0, 0])
            if not isinstance(direction, (list, tuple)) or len(direction) != 3:
                raise ModelError("Linear pattern direction must be a three-coordinate vector.")
        elif kind == "circular_pattern":
            parameters["count"] = self._pattern_count(parameters.get("count", 2))
            parameters["angle"] = float(parameters.get("angle", 360))
            axis = parameters.get("axis", [0, 0, 1])
            if not isinstance(axis, (list, tuple)) or len(axis) != 3 or not math.isfinite(parameters["angle"]):
                raise ModelError("Circular pattern needs a three-coordinate axis and finite angle.")
        for index in selection:
            if not isinstance(index, int) or index < 0:
                raise ModelError("Edge selections must be non-negative edge indices.")

        def action(state, _):
            if any(feature["name"] == name for feature in state["features"]):
                raise ModelError(f"A feature named {name!r} already exists.")
            sketch = None
            source = None
            if kind in ("sketch_extrude", "revolve", "pocket", "hole"):
                sketch = self._feature(state, sketch_name)
                if sketch.get("kind") != "sketch":
                    raise ModelError(f"{sketch_name!r} is not a sketch.")
                if kind in ("pocket", "hole"):
                    source = self._feature(state, source_name)
            else:
                source = self._feature(state, source_name)
            feature = {"id": str(uuid.uuid4()), "name": name, "kind": kind, "parameters": parameters,
                       "selection": selection, "visible": True}
            if sketch:
                feature["sketch"] = sketch["id"]
                sketch["visible"] = False
            if source:
                feature["source"] = source["id"]
                source["visible"] = False
            if label:
                feature["label"] = str(label)
            state["features"].append(feature)
            return copy.deepcopy(feature)

        return self._mutate(action)

    @staticmethod
    def _pattern_count(value):
        try:
            count = int(value)
        except (TypeError, ValueError) as exc:
            raise ModelError("Pattern count must be an integer between 2 and 1000.") from exc
        if isinstance(value, bool) or count != value or not 2 <= count <= 1000:
            raise ModelError("Pattern count must be an integer between 2 and 1000.")
        return count

    def set_feature_parameters(self, name: str, parameters: dict):
        """Replace numeric parameters for a derived sketch/solid feature and rebuild history."""
        if not isinstance(parameters, dict) or not parameters:
            raise ModelError("Provide feature parameters to update.")
        values = copy.deepcopy(parameters)
        for key, value in values.items():
            if isinstance(value, (list, tuple)):
                if any(not math.isfinite(float(item)) for item in value):
                    raise ModelError(f"Parameter {key!r} must contain finite numbers.")
                values[key] = [float(item) for item in value]
            elif isinstance(value, (int, float)):
                if not math.isfinite(float(value)):
                    raise ModelError(f"Parameter {key!r} must be finite.")
                values[key] = float(value)

        def action(state, _):
            feature = self._feature(state, name)
            if feature.get("kind") not in {"sketch_extrude", "revolve", "pocket", "hole", "fillet", "chamfer",
                                           "linear_pattern", "circular_pattern"}:
                raise ModelError(f"{name!r} does not have editable feature parameters.")
            feature["parameters"] = values
            return copy.deepcopy(feature)

        return self._mutate(action)

    def create_extrude(self, name: str, profile: str, dimensions: dict, placement=None, label=None):
        profile = profile.lower()
        requirements = {"rectangle": {"width", "height", "depth"}, "circle": {"radius", "depth"}}
        if profile not in requirements:
            raise ModelError("Profile must be rectangle or circle.")
        if not _NAME.fullmatch(name or ""):
            raise ModelError("Name must start with a letter and contain only letters, numbers or underscores.")
        if set(dimensions) != requirements[profile]:
            raise ModelError(f"{profile} dimensions must be exactly: {', '.join(sorted(requirements[profile]))}.")
        try:
            values = {key: float(value) for key, value in dimensions.items()}
        except (TypeError, ValueError) as exc:
            raise ModelError("Sketch dimensions must be numbers in millimetres.") from exc
        if any(not math.isfinite(value) or value <= 0 for value in values.values()):
            raise ModelError("Sketch dimensions and extrusion depth must be finite and greater than zero.")

        def action(state, _):
            if any(feature["name"] == name for feature in state["features"]):
                raise ModelError(f"A feature named {name!r} already exists.")
            feature = {"id": str(uuid.uuid4()), "name": name, "kind": "extrude", "profile": profile,
                       "dimensions": values, "visible": True}
            if placement:
                feature["placement"] = copy.deepcopy(placement)
            if label:
                feature["label"] = str(label)
            state["features"].append(feature)
            return copy.deepcopy(feature)

        return self._mutate(action)

    def create_polygon_extrude(self, name: str, points, height: float, holes=None, placement=None, label=None):
        if not _NAME.fullmatch(name or ""):
            raise ModelError("Name must start with a letter and contain only letters, numbers or underscores.")
        try:
            points = [[float(point[0]), float(point[1])] for point in points]
            height = float(height)
            holes = [{"x": float(hole["x"]), "y": float(hole["y"]), "r": float(hole["r"])}
                     for hole in (holes or [])]
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            raise ModelError("Polygon points must be [x, y] pairs and holes need x, y and r values.") from exc
        if len(points) < 3 or any(len(point) != 2 for point in points):
            raise ModelError("An extrusion outline needs at least three 2D points.")
        if not math.isfinite(height) or height == 0 or any(not math.isfinite(v) for p in points for v in p):
            raise ModelError("Polygon coordinates and extrusion height must be finite; height cannot be zero.")
        if any(any(not math.isfinite(v) for v in h.values()) or h["r"] <= 0 for h in holes):
            raise ModelError("Hole coordinates must be finite and radii greater than zero.")

        def action(state, _):
            if any(feature["name"] == name for feature in state["features"]):
                raise ModelError(f"A feature named {name!r} already exists.")
            feature = {"id": str(uuid.uuid4()), "name": name, "kind": "extrude_polygon",
                       "points": points, "holes": holes, "dimensions": {"depth": height}, "visible": True}
            if placement:
                feature["placement"] = copy.deepcopy(placement)
            if label:
                feature["label"] = str(label)
            state["features"].append(feature)
            return copy.deepcopy(feature)

        return self._mutate(action)

    def combine(self, operation: str, name: str, left_name: str, right_name: str, label=None):
        operation = operation.lower()
        if operation not in _OPERATIONS:
            raise ModelError("Operation must be union, subtract or intersect.")
        if not _NAME.fullmatch(name or ""):
            raise ModelError("Name must start with a letter and contain only letters, numbers or underscores.")

        def action(state, _):
            if any(feature["name"] == name for feature in state["features"]):
                raise ModelError(f"A feature named {name!r} already exists.")
            left, right = self._feature(state, left_name), self._feature(state, right_name)
            if left["id"] == right["id"]:
                raise ModelError("A boolean operation needs two different features.")
            if self._is_mesh_reference(state, left) or self._is_mesh_reference(state, right):
                raise ModelError("STL mesh references cannot be used in solid boolean operations.")
            if left["kind"] == "boolean" or right["kind"] == "boolean":
                # Boolean outputs are valid inputs too; the history order is their topological order.
                pass
            feature = {"id": str(uuid.uuid4()), "name": name, "kind": "boolean", "operation": operation,
                       "left": left["id"], "right": right["id"], "visible": True}
            if label:
                feature["label"] = str(label)
            left["visible"] = False
            right["visible"] = False
            state["features"].append(feature)
            return copy.deepcopy(feature)

        return self._mutate(action)

    def set_dimension(self, name: str, dimension: str, value: float):
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ModelError("Dimension must be a number in millimetres.") from exc
        if not math.isfinite(value) or value <= 0:
            raise ModelError("Dimension must be finite and greater than zero.")

        def action(state, _):
            feature = self._feature(state, name)
            if "dimensions" not in feature or dimension not in feature["dimensions"]:
                raise ModelError("Choose a dimension on a box, cylinder, sphere or extrusion feature.")
            feature["dimensions"][dimension] = value
            return copy.deepcopy(feature)

        return self._mutate(action)

    def set_dimensions(self, name: str, dimensions: dict, expressions=None):
        expressions = expressions or {}
        if not isinstance(expressions, dict):
            raise ModelError("Dimension expressions must be a property-to-expression map.")
        if not isinstance(dimensions, dict) or (not dimensions and not expressions):
            raise ModelError("Provide at least one dimension or expression to change.")
        values = {}
        for dimension, value in dimensions.items():
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ModelError(f"Dimension {dimension} must be a number in millimetres.") from exc
            if not math.isfinite(value) or value <= 0:
                raise ModelError(f"Dimension {dimension} must be finite and greater than zero.")
            values[dimension] = value

        def action(state, _):
            feature = self._feature(state, name)
            if "dimensions" not in feature or set(values) - set(feature["dimensions"]):
                raise ModelError("Choose valid dimensions on a box, cylinder, sphere or extrusion feature.")
            feature["dimensions"].update(values)
            stored = feature.setdefault("expressions", {})
            for dimension in values:
                stored.pop(dimension, None)
            for dimension, expression in expressions.items():
                if dimension not in feature["dimensions"]:
                    raise ModelError(f"{dimension!r} is not a dimension on {feature['name']}.")
                if expression is None:
                    stored.pop(dimension, None)
                else:
                    _expression_value(expression, state.get("params", {}))
                    stored[dimension] = str(expression)
            return copy.deepcopy(feature)

        return self._mutate(action)

    def set_expression(self, name: str, dimension: str, expression):
        def action(state, _):
            feature = self._feature(state, name)
            if "dimensions" not in feature or dimension not in feature["dimensions"]:
                raise ModelError("Choose an editable dimension on a feature.")
            stored = feature.setdefault("expressions", {})
            if expression is None:
                stored.pop(dimension, None)
            else:
                _expression_value(expression, state.get("params", {}))
                stored[dimension] = str(expression)
            return copy.deepcopy(feature)

        return self._mutate(action)

    def set_param(self, alias: str, value):
        if not _NAME.fullmatch(alias or ""):
            raise ModelError("Parameter name must start with a letter and contain only letters, numbers or underscores.")
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ModelError("Parameter value must be a number in millimetres.") from exc
        if not math.isfinite(value):
            raise ModelError("Parameter value must be finite.")

        def action(state, _):
            state.setdefault("params", {})[alias] = value
            return {"alias": alias, "value": value}

        return self._mutate(action)

    def param_users(self, alias: str):
        self.reload()
        pattern = re.compile(rf"\bParams\.{re.escape(alias)}\b")
        return sorted(feature["name"] for feature in self._state["features"]
                      if any(pattern.search(str(expr)) for expr in feature.get("expressions", {}).values()))

    def set_placement(self, name: str, placement: dict):
        try:
            base = [float(v) for v in placement["base"]]
            rotation = placement.get("rotation") or {}
            axis = [float(v) for v in rotation.get("axis", [0, 0, 1])]
            angle = float(rotation.get("angle", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelError("Placement must contain a 3D base and an axis-angle rotation.") from exc
        if len(base) != 3 or len(axis) != 3 or any(not math.isfinite(v) for v in [*base, *axis, angle]):
            raise ModelError("Placement coordinates and rotation must be finite 3D values.")
        if angle and math.sqrt(sum(v * v for v in axis)) == 0:
            raise ModelError("A non-zero rotation needs a non-zero axis.")
        placement = {"base": base, "rotation": {"axis": axis, "angle": angle}}

        def action(state, _):
            feature = self._feature(state, name)
            if feature["kind"] in ("translate", "boolean"):
                raise ModelError("Placement edits are currently supported on source features, not derived features.")
            feature["placement"] = placement
            return copy.deepcopy(feature)

        return self._mutate(action)

    def delete_feature(self, name: str, force=False):
        def action(state, _):
            feature = self._feature(state, name)
            doomed = {feature["id"]}
            dependents = True
            while dependents:
                dependents = {item["id"] for item in state["features"]
                              if item["id"] not in doomed and
                              any(item.get(ref) in doomed for ref in ("source", "left", "right", "sketch"))}
                doomed.update(dependents)
            if len(doomed) > 1 and not force:
                children = [item["name"] for item in state["features"] if item["id"] in doomed and item["id"] != feature["id"]]
                raise ModelError(f"Feature {feature['name']} is used by {children}; pass force to remove dependents too.")
            removed = [item["name"] for item in state["features"] if item["id"] in doomed]
            state["features"] = [item for item in state["features"] if item["id"] not in doomed]
            # Re-enable surviving sources when their only consumer was removed.
            used = {item.get(ref) for item in state["features"]
                    for ref in ("source", "left", "right", "sketch") if item.get(ref)}
            for item in state["features"]:
                if item["id"] not in used:
                    item["visible"] = True
            return {"deleted": removed}

        return self._mutate(action)

    def translate_feature(self, name: str, source_name: str, x: float, y: float, z: float):
        if not _NAME.fullmatch(name or ""):
            raise ModelError("Name must start with a letter and contain only letters, numbers or underscores.")
        try:
            offset = {"x": float(x), "y": float(y), "z": float(z)}
        except (TypeError, ValueError) as exc:
            raise ModelError("Move offsets must be numbers in millimetres.") from exc
        if any(not math.isfinite(value) for value in offset.values()):
            raise ModelError("Move offsets must be finite numbers.")

        def action(state, _):
            if any(feature["name"] == name for feature in state["features"]):
                raise ModelError(f"A feature named {name!r} already exists.")
            source = self._feature(state, source_name)
            feature = {"id": str(uuid.uuid4()), "name": name, "kind": "translate", "source": source["id"],
                       "offset": offset, "visible": True}
            source["visible"] = False
            state["features"].append(feature)
            return copy.deepcopy(feature)

        return self._mutate(action)

    def _asset_dir(self):
        return self.path + ".assets"

    def import_step(self, source_path: str, name: str):
        source_path = os.path.abspath(os.path.expanduser(source_path))
        if not os.path.isfile(source_path) or not source_path.lower().endswith((".step", ".stp")):
            raise ModelError("Choose an existing STEP file (.step or .stp).")
        if not _NAME.fullmatch(name or ""):
            raise ModelError("Name must start with a letter and contain only letters, numbers or underscores.")
        feature_id = str(uuid.uuid4())
        asset_name = feature_id + os.path.splitext(source_path)[1].lower()
        asset_dir = self._asset_dir()
        os.makedirs(asset_dir, exist_ok=True)
        staged = os.path.join(asset_dir, "." + asset_name + ".tmp")
        asset_path = os.path.join(asset_dir, asset_name)
        shutil.copyfile(source_path, staged)
        os.replace(staged, asset_path)
        feature = {"id": feature_id, "name": name, "kind": "import_step", "asset": asset_name,
                   "visible": True}

        def action(state, _):
            if any(item["name"] == name for item in state["features"]):
                raise ModelError(f"A feature named {name!r} already exists.")
            state["features"].append(feature)
            return copy.deepcopy(feature)

        try:
            return self._mutate(action)
        except Exception:
            try:
                os.remove(asset_path)
            except OSError:
                pass
            raise

    def import_stl(self, source_path: str, name: str):
        """Import a project-owned STL mesh reference (not a parametric solid)."""
        source_path = os.path.abspath(os.path.expanduser(source_path))
        if not os.path.isfile(source_path) or not source_path.lower().endswith(".stl"):
            raise ModelError("Choose an existing STL mesh file (.stl).")
        if not _NAME.fullmatch(name or ""):
            raise ModelError("Name must start with a letter and contain only letters, numbers or underscores.")
        feature_id = str(uuid.uuid4())
        asset_name = feature_id + ".stl"
        asset_dir = self._asset_dir()
        os.makedirs(asset_dir, exist_ok=True)
        staged = os.path.join(asset_dir, "." + asset_name + ".tmp")
        asset_path = os.path.join(asset_dir, asset_name)
        shutil.copyfile(source_path, staged)
        os.replace(staged, asset_path)
        feature = {"id": feature_id, "name": name, "kind": "import_stl", "asset": asset_name,
                   "visible": True}

        def action(state, _):
            if any(item["name"] == name for item in state["features"]):
                raise ModelError(f"A feature named {name!r} already exists.")
            state["features"].append(feature)
            return copy.deepcopy(feature)

        try:
            return self._mutate(action)
        except Exception:
            try:
                os.remove(asset_path)
            except OSError:
                pass
            raise

    def feature_tree(self):
        self.reload()
        return {"revision": self.revision, "units": "mm", "params": copy.deepcopy(self._state["params"]),
                "features": copy.deepcopy(self._state["features"])}

    def mesh_data(self, tolerance: float = 0.5):
        if not 0 < tolerance <= 5:
            raise ModelError("Tessellation tolerance must be between 0 and 5 mm.")
        self.reload()
        shapes = self._rebuild(self._state)
        output = []
        for feature in self._state["features"]:
            if not feature.get("visible", True) or feature.get("kind") == "sketch" or feature["id"] not in shapes:
                continue
            vertices, triangles = shapes[feature["id"]].tessellate(tolerance)
            edge_descriptors = []
            for index, edge in enumerate(shapes[feature["id"]].edges()):
                center = edge.center()
                endpoints = edge.vertices()
                edge_descriptors.append({
                    "id": f"edge:{index}", "index": index, "length": float(edge.length),
                    "center": [float(center.X), float(center.Y), float(center.Z)],
                    "vertices": [[float(point.X), float(point.Y), float(point.Z)] for point in endpoints],
                })
            output.append({
                "id": feature["id"], "name": feature["name"],
                "vertices": [[float(p.X), float(p.Y), float(p.Z)] for p in vertices],
                "triangles": [list(map(int, tri)) for tri in triangles],
                "edges": edge_descriptors,
            })
        return {"revision": self.revision, "objects": output}

    def export(self, path: str, file_format: str, names=None):
        (Box, BuildSketch, BuildPart, Circle, Compound, Cylinder, Location, Mode, Rectangle, Sphere, add,
         _, export_step, export_stl, extrude) = _build123d()
        del Box, BuildSketch, BuildPart, Circle, Cylinder, Location, Mode, Rectangle, Sphere, add, extrude
        file_format = file_format.lower()
        expected_ext = {"step": ".step", "stl": ".stl"}.get(file_format)
        if not expected_ext:
            raise ModelError("Format must be STEP or STL.")
        path = os.path.abspath(os.path.expanduser(path))
        if not path.lower().endswith((expected_ext, ".stp" if file_format == "step" else expected_ext)):
            path += expected_ext
        self.reload()
        shapes = self._rebuild(self._state)
        selected = set(names or [])
        visible = [shapes[f["id"]] for f in self._state["features"]
                   if f.get("visible", True) and f["id"] in shapes and
                   (not selected or f["name"] in selected)]
        if not visible:
            raise ModelError("There are no visible solids to export.")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        compound = Compound(children=visible)
        ok = export_step(compound, path) if file_format == "step" else export_stl(compound, path)
        if ok is False:
            raise ModelError(f"The {file_format.upper()} exporter could not write the file.")
        return {"path": path, "format": file_format.upper(), "revision": self.revision}


def default_project_path():
    from cadswarm.paths import app_dir

    return os.path.join(app_dir(), "projects", "default.cadswarm")


def active_project_path():
    """Use the project last opened in the desktop unless MCP explicitly selects another one."""
    explicit = os.getenv("CADSWARM_STANDALONE_PROJECT")
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    from cadswarm.paths import app_dir

    active_file = os.path.join(app_dir(), "active-project.json")
    try:
        with open(active_file, encoding="utf-8") as source:
            path = json.load(source).get("path")
        if isinstance(path, str) and path:
            return os.path.abspath(path)
    except (OSError, ValueError, AttributeError):
        pass
    return default_project_path()


def set_active_project(path: str):
    from cadswarm.paths import app_dir

    root = app_dir()
    os.makedirs(root, exist_ok=True)
    target = os.path.join(root, "active-project.json")
    temporary = target + ".tmp"
    with open(temporary, "w", encoding="utf-8") as output:
        json.dump({"path": os.path.abspath(os.path.expanduser(path))}, output)
        output.write("\n")
    os.replace(temporary, target)
