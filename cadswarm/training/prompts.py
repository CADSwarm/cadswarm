"""The one op contract and the one state renderer.

Training and inference must show the model the same thing, so both render through here. The
assistant side is plain JSON rather than a provider's tool-call object: the dataset stays
portable and any backend can be trained on it. Every part of the request is bounded, because a
9,000-character brief would otherwise be duplicated once per training step.
"""
import json

MAX_TEXT = 2000
MAX_PLAN = 4000
MAX_PARAMS = 40
MAX_OPS = 8
MAX_ARGS = 200
# The largest rendered assistant message a trainer may be handed. One `add_reference` carried a
# 538k-character base64 image; a 16 GB QLoRA run would OOM or silently truncate it.
MAX_TARGET = 8000

# The worker's own MUTATIONS table: the only ops the builder may emit. Queries and helpers
# (brep, get_object, style_inbox, ...) are diagnostics, not part of the build contract.
BUILD_OPS = ("create_primitive", "create_extrusion", "boolean", "set_property", "set_props",
             "set_expression", "set_placement", "delete", "set_param", "run_part_script",
             "insert_component", "insert_standard_part", "insert_mesh")

_OP_ARGS = {
    "create_primitive": '{"kind": "box|cylinder|sphere|cone|torus", "name": str, "props": {}, "placement": {}}',
    "create_extrusion": '{"name": str, "points": [[x, y], ...], "height": number, "holes": [{"x","y","r"}]}',
    "boolean": '{"kind": "cut|fuse|common", "name": str, "base": str, "tool": str}',
    "set_property": '{"name": str, "prop": str, "value": number|str}',
    "set_props": '{"name": str, "props": {"Height": number, ...}}',
    "set_expression": '{"name": str, "prop": str, "expr": str}',
    "set_placement": '{"name": str, "placement": {"base": [x, y, z], "rotation": [ax, ay, az]}}',
    "set_param": '{"alias": str, "value": str, "note": str}',
    "delete": '{"name": str}',
    "run_part_script": '{"code": str, "name": str}',
    "insert_component": '{"component": str, "name": str, "placement": {}}',
    "insert_standard_part": '{"family": str, "size": str, "name": str, "placement": {}}',
    "insert_mesh": '{"file": str, "name": str}',
}

SYSTEM = """You are the builder for a CAD team. You see one request, its plan, the current model
state and the last few operations. Reply with exactly one op as JSON and nothing else:

{"args": {...}, "op": "<name>"}

Ops:
""" + "".join(f"  {op:<22} {_OP_ARGS[op]}\n" for op in BUILD_OPS) + """
Address objects by their exact name. Params are the team spreadsheet: prefer set_param over a
number hard-coded in an op. Never name an object that is not in the model state.
"""


def _clip(value, limit):
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "…"


def render_request(request, summary, previous=()):
    """Deterministic: sorted keys, no clock, every part bounded."""
    view = dict(summary)
    params = view.get("params") or {}
    if len(params) > MAX_PARAMS:
        view["params"] = {key: params[key] for key in sorted(params)[:MAX_PARAMS]}
        view["params_total"] = len(params)
    recent = []
    for step in list(previous)[-MAX_OPS:]:
        args = json.dumps(step.get("args") or {}, sort_keys=True, ensure_ascii=False)
        recent.append({"op": step.get("op"), "args": _clip(args, MAX_ARGS)})
    return ("Request: " + _clip(request.get("text"), MAX_TEXT) + "\n"
            + "Plan: " + _clip(json.dumps(request.get("plan_json") or {}, sort_keys=True,
                                          ensure_ascii=False), MAX_PLAN) + "\n"
            + "Model: " + json.dumps(view, sort_keys=True, ensure_ascii=False) + "\n"
            + "Recent ops: " + json.dumps(recent, sort_keys=True, ensure_ascii=False))


def render_assistant(op, args):
    return json.dumps({"args": args, "op": op}, sort_keys=True, ensure_ascii=False)
