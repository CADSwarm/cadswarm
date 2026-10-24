"""Which library parts suit a need ("hold the lid shut", "an 8 mm axle that spins"), and why.

Builds on search.py: expands the need into part words, ranks team parts and standard families, picks a
concrete size for standard parts, works out the hole the mating part needs from the team's fits, and
lists the settings each choice implies (suggest.py). The agent adds design judgement and asks the team.
"""
import re

from cadswarm.library import search as lib_search
from cadswarm.library import standard, suggest

DEFAULT_SIZE = {"fastener": "M3", "insert": "M3"}


def _pick_size(c, sizes, constraints):
    std = c.get("std") or {}
    fam = standard.families()[std["family"]]
    options = sizes if sizes else list(fam["sizes"])
    want = DEFAULT_SIZE.get(fam["category"])
    if fam["shape"] == "bearing":
        want = "608"  # the commonest bearing anywhere (skateboards, printers): cheap, when it fits
    return want if want in options else options[0]


def _pick_length(c, constraints):
    std = c.get("std") or {}
    lengths = std.get("lengths") or []
    if not lengths:
        return None
    want = lib_search._num(constraints.get("length"))
    grip = lib_search._num(constraints.get("grip"))
    if want is None and grip is not None:
        want = grip + 4  # through the clamped parts and ~4 mm into the insert or nut
    if want is None:
        want = 10
    return min(lengths, key=lambda x: (abs(x - want), x))


def recommend(components, need, constraints=None, values=None, limit=5):
    constraints = dict(constraints or {})
    values = values or {}
    m = re.search(r"\bM(\d(?:\.\d)?)\b", need or "", re.I)
    if m and not constraints.get("thread"):
        constraints["thread"] = f"M{m.group(1)}"
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:axle|shaft|rod|bore)", need or "", re.I)
    if m and not constraints.get("bore_d"):
        constraints["bore_d"] = float(m.group(1))
    hits = lib_search.search(components, need or "", constraints.get("category"), constraints, limit=limit * 2)
    out = []
    for score, c, sizes in hits:
        entry = {"id": c["id"], "name": c.get("name"), "category": c.get("category"), "source": c.get("source"),
                 "score": score, "license": c.get("license", "")}
        std = c.get("std") or {}
        why = []
        if std.get("family"):
            size = _pick_size(c, sizes, constraints)
            length = _pick_length(c, constraints)
            placed = standard.resolve(std["family"], size, length)
            entry.update({"family": std["family"], "size": size, "length": length, "label": placed["label"],
                          "dims": placed["dims"], "hole": standard.hole_for(std["family"], size, values),
                          "insert_with": {"tool": "add_standard_part", "family": std["family"], "size": size,
                                          **({"length": length} if length else {})}})
            why.append(f"standard {placed['label']} ({standard.family(std['family'])['standard']})")
            meta = {"std": {"family": std["family"], "size": size}, "name": placed["label"]}
        else:
            meta = c
            entry.update({"bbox": c.get("bbox"), "insert_with": {"tool": "insert_component", "id": c["id"]}})
            why.append("the team's own part" + (f", used {c['uses']} time(s)" if c.get("uses") else ""))
            if c.get("description"):
                why.append(c["description"][:120])
        entry["why"] = "; ".join(why)
        entry["suggest"] = suggest.suggestions(meta, values)
        out.append(entry)
        if len(out) >= limit:
            break
    return {"need": need, "constraints": constraints, "recommendations": out,
            "note": "" if out else "Nothing in the library fits. Model it, or find a STEP file online and use "
                                   "import_component_from_url."}
