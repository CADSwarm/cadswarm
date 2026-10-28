"""The one op contract and the one state renderer.

Training and inference must show the model the same thing, so both render through here. The
assistant side is plain JSON rather than a provider's tool-call object: the dataset stays
portable and any backend can be trained on it.
"""
import json

SYSTEM = """You are the builder for a CAD team. You see one request, its plan, the current model
state and the last few operations. Reply with exactly one op as JSON and nothing else:

{"op": "<name>", "args": {...}}

Ops:
  create_primitive  {"kind": "box|cylinder|sphere|cone|torus", "name": str, "props": {}, "placement": {}}
  create_extrusion  {"name": str, "points": [[x, y], ...], "height": number, "holes": [{"x","y","r"}]}
  boolean           {"kind": "cut|fuse|common", "name": str, "base": str, "tool": str}
  set_props         {"name": str, "props": {"Height": number, ...}}
  set_placement     {"name": str, "placement": {"base": [x, y, z], "rotation": [ax, ay, az]}}
  set_param         {"alias": str, "value": str, "note": str}
  delete            {"name": str}
  run_part_script   {"code": str, "name": str}

Address objects by their exact name. Params are the team spreadsheet: prefer set_param over a
number hard-coded in an op. Never name an object that is not in the model state.
"""


def render_request(request, summary, previous=()):
    """Deterministic: sorted keys, no clock, bounded lists."""
    recent = [{"op": step.get("op"), "args": step.get("args")} for step in list(previous)[-8:]]
    return ("Request: " + str(request.get("text") or "") + "\n"
            + "Plan: " + json.dumps(request.get("plan_json") or {}, sort_keys=True, ensure_ascii=False) + "\n"
            + "Model: " + json.dumps(summary, sort_keys=True, ensure_ascii=False) + "\n"
            + "Recent ops: " + json.dumps(recent, sort_keys=True, ensure_ascii=False))


def render_assistant(op, args):
    return json.dumps({"op": op, "args": args}, sort_keys=True, ensure_ascii=False)
