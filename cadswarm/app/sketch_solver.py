"""Constraint solver adapter for editable two dimensional CADSwarm sketches.

Geometry and constraints are stored in the project document. SciPy's trust-region
least-squares implementation supplies the numerical solve; this module owns the
CADSwarm constraint vocabulary, validation, DOF reporting, and failure semantics.
"""

from __future__ import annotations

import copy
import math
import re


class SketchSolveError(ValueError):
    """Invalid or inconsistent sketch geometry/constraints."""


_POINT = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\.(start|end|center)$")


def _backend():
    try:
        import numpy as np
        from scipy.optimize import least_squares
    except ImportError as exc:
        raise RuntimeError("The standalone sketch solver needs SciPy; install CADSwarm's desktop extra.") from exc
    return np, least_squares


def solve(entities, constraints):
    """Solve supported 2D geometry in place and return entities plus solver diagnostics.

    Points are referenced as ``entity_id.start``, ``entity_id.end`` or
    ``entity_id.center``. Length-valued constraints use millimetres; angles use
    radians. Unsupported or inconsistent constraints raise before the document
    mutation is committed.
    """
    np, least_squares = _backend()
    geometry = copy.deepcopy(entities)
    if not isinstance(geometry, list) or not geometry:
        raise SketchSolveError("A sketch needs at least one line, circle, or arc.")
    by_id = {}
    offsets = {}
    variables = []
    for item in geometry:
        if not isinstance(item, dict):
            raise SketchSolveError("Sketch entities must be objects.")
        entity_id, kind = item.get("id"), item.get("type")
        if not isinstance(entity_id, str) or not entity_id or entity_id in by_id:
            raise SketchSolveError("Every sketch entity needs a unique stable id.")
        if kind not in ("line", "circle", "arc"):
            raise SketchSolveError(f"Unsupported sketch entity type: {kind!r}.")
        if kind == "line":
            values = [*item.get("start", []), *item.get("end", [])]
            expected = 4
        elif kind == "circle":
            values = [*item.get("center", []), item.get("radius")]
            expected = 3
        else:
            values = [*item.get("center", []), item.get("radius"), item.get("start_angle"), item.get("end_angle")]
            expected = 5
        try:
            values = [float(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise SketchSolveError(f"Entity {entity_id!r} has invalid coordinates or dimensions.") from exc
        if len(values) != expected or any(not math.isfinite(value) for value in values):
            raise SketchSolveError(f"Entity {entity_id!r} has invalid coordinates or dimensions.")
        if kind in ("circle", "arc") and values[2] <= 0:
            raise SketchSolveError(f"Entity {entity_id!r} must have a radius greater than zero.")
        offsets[entity_id] = (len(variables), kind)
        variables.extend(values)
        by_id[entity_id] = item

    constraints = copy.deepcopy(constraints or [])
    if not isinstance(constraints, list):
        raise SketchSolveError("Sketch constraints must be a list.")
    ids = set()
    for constraint in constraints:
        if not isinstance(constraint, dict):
            raise SketchSolveError("Sketch constraints must be objects.")
        constraint_id = constraint.get("id")
        if constraint_id is not None and (not isinstance(constraint_id, str) or constraint_id in ids):
            raise SketchSolveError("Constraint ids must be unique strings.")
        if constraint_id:
            ids.add(constraint_id)

    initial = np.asarray(variables, dtype=float)

    def unpack(vector):
        out = {}
        for entity_id, (start, kind) in offsets.items():
            if kind == "line":
                out[entity_id] = {"id": entity_id, "type": kind,
                                  "start": vector[start:start + 2].tolist(),
                                  "end": vector[start + 2:start + 4].tolist()}
            elif kind == "circle":
                out[entity_id] = {"id": entity_id, "type": kind,
                                  "center": vector[start:start + 2].tolist(), "radius": float(vector[start + 2])}
            else:
                out[entity_id] = {"id": entity_id, "type": kind,
                                  "center": vector[start:start + 2].tolist(), "radius": float(vector[start + 2]),
                                  "start_angle": float(vector[start + 3]), "end_angle": float(vector[start + 4])}
        return out

    def point(vector, reference):
        match = _POINT.fullmatch(str(reference or ""))
        if not match or match.group(1) not in offsets:
            raise SketchSolveError(f"Invalid sketch point reference: {reference!r}.")
        entity_id, point_name = match.groups()
        start, kind = offsets[entity_id]
        if point_name in ("start", "end") and kind not in ("line", "arc"):
            raise SketchSolveError(f"{reference!r} does not identify a line or arc endpoint.")
        if point_name == "center" and kind not in ("circle", "arc"):
            raise SketchSolveError(f"{reference!r} does not identify a center.")
        if kind == "arc" and point_name in ("start", "end"):
            angle = vector[start + 3] if point_name == "start" else vector[start + 4]
            return vector[start:start + 2] + vector[start + 2] * np.asarray([math.cos(angle), math.sin(angle)])
        return vector[start:start + 2] if point_name in ("start", "center") else vector[start + 2:start + 4]

    def line(vector, entity_id):
        if entity_id not in offsets or offsets[entity_id][1] != "line":
            raise SketchSolveError(f"Constraint requires line entity {entity_id!r}.")
        start = offsets[entity_id][0]
        a, b = vector[start:start + 2], vector[start + 2:start + 4]
        d = b - a
        return a, b, d, float(np.linalg.norm(d))

    def residual(vector):
        values = []
        for constraint in constraints:
            kind = str(constraint.get("type", "")).lower()
            refs = constraint.get("points") or []
            entities_ref = constraint.get("entities") or []
            raw_target = constraint.get("value", 0)
            if kind != "fixed":
                try:
                    target = float(raw_target)
                except (TypeError, ValueError) as exc:
                    raise SketchSolveError(f"Constraint {kind!r} needs a numeric value.") from exc
            if kind == "coincident":
                if len(refs) != 2:
                    raise SketchSolveError("Coincident needs two point references.")
                values.extend((point(vector, refs[0]) - point(vector, refs[1])).tolist())
            elif kind in ("horizontal", "vertical"):
                if len(entities_ref) != 1:
                    raise SketchSolveError(f"{kind.title()} needs one line entity.")
                _, _, d, _ = line(vector, entities_ref[0])
                values.append(float(d[1] if kind == "horizontal" else d[0]))
            elif kind in ("parallel", "perpendicular", "equal", "angle"):
                if len(entities_ref) != 2:
                    raise SketchSolveError(f"{kind.title()} needs two line entities.")
                _, _, a, alen = line(vector, entities_ref[0])
                _, _, b, blen = line(vector, entities_ref[1])
                if min(alen, blen) <= 1e-12:
                    raise SketchSolveError("A constrained line cannot have zero length.")
                cross = float(a[0] * b[1] - a[1] * b[0])
                dot = float(a @ b)
                if kind == "parallel":
                    values.append(cross / (alen * blen))
                elif kind == "perpendicular":
                    values.append(dot / (alen * blen))
                elif kind == "equal":
                    values.append(alen - blen)
                else:
                    values.append(math.atan2(cross, dot) - target)
            elif kind == "distance":
                if len(refs) != 2 or target < 0:
                    raise SketchSolveError("Distance needs two point references and a non-negative value.")
                values.append(float(np.linalg.norm(point(vector, refs[0]) - point(vector, refs[1]))) - target)
            elif kind in ("radius", "diameter"):
                if len(entities_ref) != 1 or entities_ref[0] not in offsets:
                    raise SketchSolveError(f"{kind.title()} needs one circle or arc entity.")
                start, entity_kind = offsets[entities_ref[0]]
                if entity_kind not in ("circle", "arc") or target <= 0:
                    raise SketchSolveError(f"{kind.title()} needs a circle/arc and a positive value.")
                radius = float(vector[start + 2])
                values.append((radius if kind == "radius" else 2 * radius) - target)
            elif kind == "concentric":
                if len(entities_ref) != 2:
                    raise SketchSolveError("Concentric needs two circle or arc entities.")
                first, second = entities_ref
                if any(entity not in offsets or offsets[entity][1] not in ("circle", "arc") for entity in (first, second)):
                    raise SketchSolveError("Concentric only applies to circles and arcs.")
                values.extend((vector[offsets[first][0]:offsets[first][0] + 2] -
                               vector[offsets[second][0]:offsets[second][0] + 2]).tolist())
            elif kind == "fixed":
                if len(refs) != 1:
                    raise SketchSolveError("Fixed needs one point reference and a two-coordinate value.")
                desired = raw_target
                if not isinstance(desired, (list, tuple)) or len(desired) != 2:
                    raise SketchSolveError("Fixed point value must be [x, y].")
                values.extend((point(vector, refs[0]) - np.asarray(desired, dtype=float)).tolist())
            else:
                raise SketchSolveError(f"Unsupported sketch constraint type: {kind!r}.")
        return np.asarray(values, dtype=float)

    if not constraints:
        solved = initial
        jacobian = np.empty((0, len(initial)))
        result_norm = 0.0
    else:
        lower = np.full(len(initial), -np.inf)
        upper = np.full(len(initial), np.inf)
        for entity_id, (start, kind) in offsets.items():
            if kind in ("circle", "arc"):
                lower[start + 2] = 1e-9
        try:
            result = least_squares(residual, initial, bounds=(lower, upper), method="trf", max_nfev=400,
                                   ftol=1e-11, xtol=1e-11, gtol=1e-11)
        except (ValueError, FloatingPointError) as exc:
            raise SketchSolveError(f"Sketch constraints could not be solved: {exc}") from exc
        solved = result.x
        jacobian = result.jac
        result_norm = float(np.linalg.norm(residual(solved)))
        if not result.success or not math.isfinite(result_norm) or result_norm > 1e-5:
            raise SketchSolveError("Sketch constraints are conflicting or have no valid solution.")

    rank = int(np.linalg.matrix_rank(jacobian, tol=1e-8)) if jacobian.size else 0
    dof = max(0, len(initial) - rank)
    for entity_id, (start, kind) in offsets.items():
        if kind in ("circle", "arc") and solved[start + 2] <= 1e-9:
            raise SketchSolveError(f"Solving made {entity_id!r} collapse to zero radius.")
    solved_entities = unpack(solved)
    ordered = [solved_entities[item["id"]] for item in geometry]
    return {"entities": ordered, "dof": dof,
            "status": "fully_constrained" if dof == 0 else "underconstrained",
            "residual": round(result_norm, 12)}
