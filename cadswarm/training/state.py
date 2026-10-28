"""The object table after every step, reconstructed from the op log alone.

A value the worker never reported stays `known: False` and `None`: the replay is useful precisely
because it does not guess. A failed op is a no-op, because the worker rolls it back.
"""
import re

PRIMITIVE_TYPE = {"box": "Part::Box", "cylinder": "Part::Cylinder", "sphere": "Part::Sphere",
                  "cone": "Part::Cone", "torus": "Part::Torus"}
BOOLEAN_TYPE = {"cut": "Part::Cut", "fuse": "Part::Fuse", "common": "Part::Common"}
FIELDS = ("created", "modified", "deleted", "recomputed", "broken")


def empty():
    return {"objects": {}, "params": {}}


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
    if op == "create_primitive":
        return PRIMITIVE_TYPE.get(args.get("kind"), "Part::Feature")
    if op == "boolean":
        return BOOLEAN_TYPE.get(args.get("kind"), "Part::Feature")
    return "Part::Feature"


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
    unknown = []
    for name in result.get("deleted") or []:
        if state["objects"].pop(name, None) is None:
            unknown.append(name)
    payload = result.get("result") or {}
    for name in created:
        state["objects"][name] = {
            "type": type_of(step.get("op"), args), "rev": 0,
            # A script's objects are only described by the snapshot the worker never took.
            "known": step.get("op") != "run_part_script",
            "volume": payload.get("volume") if len(created) == 1 else None,
        }
    for name in (result.get("modified") or []) + (result.get("recomputed") or []):
        if name in state["objects"]:
            state["objects"][name]["rev"] = state["objects"][name].get("rev", 0) + 1
        else:
            unknown.append(name)
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
    type_bad = sorted(n for n, row in live.items()
                      if n in mine and row.get("type_id") != mine[n]["type"])
    volume_bad = []
    for name, row in live.items():
        got, live_volume = mine.get(name) or {}, row.get("volume") or 0
        if not got.get("known") or not got.get("volume") or not live_volume:
            continue
        if abs(got["volume"] - live_volume) > 0.01 * live_volume:
            volume_bad.append(name)
    total = len(live) + len(extra)
    matched = total - len(missing) - len(extra) - len(type_bad)
    return {"fraction": round(matched / total, 4) if total else 1.0, "missing": missing,
            "extra": extra, "type_mismatch": type_bad, "volume_mismatch": volume_bad}
