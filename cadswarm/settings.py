"""Team precision settings: tolerances, fits, mesh precision and editor precision.

The spec (keys, defaults, ranges) is settings_spec.json, shared with the SpacetimeDB module and the web
app. The team's values live in the `v_setting` view; a setting with no row has its default. People change
settings; AI helpers suggest them (`suggest_setting`) and a person accepts in the Settings dialog.

Stdlib only.
"""
import json
import os

SPEC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings_spec.json")
_SPEC = None


def specs():
    """The setting specs in display order: dicts with key, group, label, kind, unit, default, min, max..."""
    global _SPEC
    if _SPEC is None:
        with open(SPEC_PATH, encoding="utf-8") as f:
            _SPEC = json.load(f)["settings"]
    return _SPEC


def spec(key):
    return next((s for s in specs() if s["key"] == key), None)


def defaults():
    return {s["key"]: s["default"] for s in specs()}


def value_of(row):
    """A `v_setting` row's value: text for a choice setting, a number otherwise."""
    s = spec(row.get("setting", ""))
    if s is None:
        return None
    return row.get("text") if s["kind"] == "choice" else row.get("num")


def effective(rows):
    """Every setting's current value: defaults overlaid with the team's rows."""
    out = defaults()
    for r in rows or []:
        v = value_of(r)
        if v is not None:
            out[r["setting"]] = v
    return out


def check(key, value):
    """(num, text) to send to set_setting/suggest_setting, or ValueError with a person-readable reason."""
    s = spec(key)
    if s is None:
        raise ValueError(f"Unknown setting {key!r}. Known: {', '.join(x['key'] for x in specs())}")
    if s["kind"] == "choice":
        if value not in s["choices"]:
            raise ValueError(f"{key} must be one of {', '.join(s['choices'])}")
        return 0.0, str(value)
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number in {s['unit'] or 'units'}")
    if not (s["min"] <= num <= s["max"]):
        raise ValueError(f"{key} must be between {s['min']} and {s['max']} {s['unit']}")
    return num, ""


def describe(values):
    """Short text for an agent's context: 'tol_clearance=0.2mm, ...' (only the ones geometry uses)."""
    parts = []
    for s in specs():
        v = values.get(s["key"], s["default"])
        mark = "" if v == s["default"] else "*"
        parts.append(f"{s['key']}={v}{s.get('unit', '')}{mark}")
    return ", ".join(parts)


def worker_settings(values):
    """The subset the FreeCAD worker reads for meshing, clash checks and printability."""
    keys = ("mesh_deviation", "mesh_angular_deg", "interference_min_vol", "min_wall")
    return {k: values.get(k, spec(k)["default"]) for k in keys}
