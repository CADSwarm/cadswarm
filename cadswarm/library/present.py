"""Plain-words text for library parts and settings, shared by the FreeCAD panel (no Qt here, so it tests
without FreeCAD)."""
import html
import math

SOURCE_WORDS = {"upload": "Team part", "url": "From a link", "pack": "From a pack", "builtin": "Standard part"}


def size_label(bbox):
    if not bbox or len(bbox) != 6:
        return ""
    dims = [bbox[i + 3] - bbox[i] for i in range(3)]
    return " × ".join(f"{d:.4g}" for d in dims) + " mm"


def decimals_for(step):
    """Decimal places a spin box needs to show `step` exactly (0.05 -> 2, 0.001 -> 3, 1 -> 0)."""
    if not step or step >= 1:
        return 0
    return min(6, max(0, math.ceil(-math.log10(step) - 1e-9)))


def describe(c, values=None):
    """Rich text for the library panel's details box."""
    e = html.escape
    lines = [f"<b>{e(c.get('name', ''))}</b> <span style='color:#8b949e'>{e(SOURCE_WORDS.get(c.get('source'), ''))}"
             f" · {e(c.get('category', ''))}</span>"]
    if c.get("description"):
        lines.append(e(c["description"]))
    if c.get("bbox"):
        lines.append(f"Size: {e(size_label(c['bbox']))}" + (f", volume {c['volume']:.4g} mm³" if c.get("volume") else ""))
    holes = (c.get("features") or {}).get("holes") or []
    if holes:
        lines.append("Holes: " + ", ".join(f"{h['count']} × Ø{h['d']:g} mm" for h in holes[:6]))
    if c.get("tags"):
        lines.append("Tags: " + e(", ".join(c["tags"])))
    src = []
    if c.get("author"):
        src.append(f"added by {e(c['author'])}")
    if c.get("source_url"):
        src.append(f"from <a href='{e(c['source_url'])}'>{e(c['source_url'][:60])}</a>")
    if c.get("license"):
        src.append(f"license: {e(c['license'])}")
    if src:
        lines.append("<small>" + "; ".join(src) + "</small>")
    if c.get("uses"):
        lines.append(f"<small>Placed {c['uses']} time(s) in this project</small>")
    sugg = c.get("suggest") or []
    if sugg:
        lines.append("<small><b>Suggested settings:</b> " + "; ".join(
            f"{e(s['setting'])} → {e(str(s['value']))} ({e(s['reason'])})" for s in sugg[:3]) + "</small>")
    return "<br>".join(lines)


def setting_value_text(spec, value):
    if spec.get("kind") == "choice":
        return str(value).replace("_", " ")
    unit = spec.get("unit") or ""
    return f"{value:g} {unit}".strip() if isinstance(value, (int, float)) else str(value)
