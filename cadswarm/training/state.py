"""The object table after every step, reconstructed from the op log alone.

A value the worker never reported stays `known: False` and `None`: the replay is useful precisely
because it does not guess. A failed op is a no-op, because the worker rolls it back. The shared
`Params` sheet is seeded because the worker creates it before the first op is logged.
"""
import re

PARAMS = "Params"
PARAMS_TYPE = "Spreadsheet::Sheet"
MESH_TYPE = "Mesh::Feature"
PRIMITIVE_TYPE = {"box": "Part::Box", "cylinder": "Part::Cylinder", "sphere": "Part::Sphere",
                  "cone": "Part::Cone", "torus": "Part::Torus"}
BOOLEAN_TYPE = {"cut": "Part::Cut", "fuse": "Part::Fuse", "common": "Part::Common"}
FEATURE_OPS = {"create_extrusion", "run_part_script", "insert_component", "insert_standard_part"}
FIELDS = ("created", "modified", "deleted", "recomputed", "broken")


def empty():
    return {"objects": {PARAMS: {"type": PARAMS_TYPE, "rev": 1, "known": True, "volume": None}},
            "params": {}}


def clone(state):
    return {"objects": {k: dict(v) for k, v in state["objects"].items()},
            "params": {k: dict(v) for k, v in state["params"].items()}}


def number(value):
    """The number in "2.4 mm" or 2.4, or None (the same idea as style_memory.number)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def type_of(op, args):
    """The type this op creates, or None when the log does not say. Never a guess."""
    kind = str(args.get("kind") or "").lower()
    if op == "create_primitive":
        return PRIMITIVE_TYPE.get(kind)
    if op == "boolean":
        return BOOLEAN_TYPE.get(kind)
    if op == "insert_mesh":
        return MESH_TYPE
    if op in FEATURE_OPS:
        return "Part::Feature"
    return None


def touched(step):
    result = step.get("result") or {}
    return [name for field in FIELDS for name in (result.get(field) or []) if isinstance(name, str)]


def apply(state, step):
    """Apply one op in place. Returns the names this op touched but the replay never created."""
    if step.get("status") != "done":
        return []
    result = step.get("result") or {}
    args = step.get("args") or {}
    created = result.get("created") or []
    payload = result.get("result") or {}
    volume = payload.get("volume") if len(created) == 1 else None
    unknown = []

    for name in result.get("deleted") or []:
        if state["objects"].pop(name, None) is None:
            unknown.append(name)

    for name in created:
        created_type = type_of(step.get("op"), args)
        state["objects"][name] = {
            "type": created_type, "rev": 0,
            # A script or a component's shape is only described by a snapshot the worker never took.
            "known": created_type is not None and step.get("op") != "run_part_script",
            "volume": volume,
        }

    changed = list(dict.fromkeys((result.get("modified") or []) + (result.get("recomputed") or [])))
    for name in changed:
        if name not in state["objects"]:
            unknown.append(name)
            continue
        obj = state["objects"][name]
        obj["rev"] = obj.get("rev", 0) + 1
        if isinstance(payload.get("volume"), (int, float)):
            obj["volume"] = payload["volume"]
            obj["known"] = True

    if step.get("op") == "set_param" and args.get("alias"):
        state["params"][args["alias"]] = {"value": args.get("value"), "num": number(args.get("value"))}
    return unknown


def replay(steps):
    state = empty()
    before, degraded = [], []
    for index, step in enumerate(steps):
        before.append(clone(state))
        if apply(state, step):
            degraded.append(index)
    return before, state, degraded


def summary(state, max_objects=120):
    """The compact, deterministic view a policy sees: sorted, bounded, no unknown values faked."""
    objects = state["objects"]
    names = sorted(objects)[:max_objects]
    return {"objects": [{"name": n, "type": objects[n]["type"], "rev": objects[n]["rev"],
                         "known": objects[n]["known"], "volume": objects[n].get("volume")}
                        for n in names],
            "objects_total": len(objects),
            "params": {k: state["params"][k]["value"] for k in sorted(state["params"])}}


def conformance(state, cad_objects, params):
    """How far the replay's final table is from the live object table. The pipeline's oracle."""
    live = {row["name"]: row for row in cad_objects if row.get("name")}
    mine = state["objects"]
    missing = sorted(set(live) - set(mine))
    extra = sorted(set(mine) - set(live))
    type_bad = sorted(name for name, row in live.items()
                      if name in mine and mine[name]["type"] is not None
                      and row.get("type_id") != mine[name]["type"])
    volume_bad = []
    for name in sorted(live):
        row, got = live[name], mine.get(name) or {}
        live_volume = row.get("volume") or 0
        if not got.get("known") or not got.get("volume") or not live_volume:
            continue
        if abs(got["volume"] - live_volume) > 0.01 * live_volume:
            volume_bad.append(name)
    total = len(live) + len(extra)
    matched = total - len(missing) - len(extra) - len(type_bad)
    return {"fraction": round(matched / total, 4) if total else 1.0, "missing": missing,
            "extra": extra, "type_mismatch": type_bad, "volume_mismatch": volume_bad}
