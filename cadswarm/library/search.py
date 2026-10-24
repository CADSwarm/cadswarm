"""Ranking library parts (team parts and standard families) against a query and dimension constraints.

Local and deterministic: names, tags, descriptions and categories score by token overlap, and constraints
(`thread`, `bore_d`, `shaft_d`, `od_max`, `max_dims`) keep only parts or sizes that fit. Team parts the
team already used rank a little higher.
"""
import re

from cadswarm.library import standard

SYNONYMS = {
    "bolt": ["screw", "fastener"], "screw": ["bolt", "fastener"], "fasten": ["fastener", "screw"],
    "attach": ["fastener", "screw"], "mount": ["fastener", "screw", "insert"], "spin": ["bearing"],
    "rotate": ["bearing"], "rotating": ["bearing"], "axle": ["bearing", "shaft"], "wheel": ["bearing"],
    "pulley": ["bearing"], "shaft": ["bearing", "pin"], "lid": ["magnet"], "closure": ["magnet"],
    "align": ["dowel", "pin"], "locate": ["dowel", "pin"], "thread": ["insert", "screw"],
    "pcb": ["board", "electronics"], "circuit": ["board", "electronics"], "nut": ["nut"],
}
STOP = {"a", "an", "the", "for", "to", "of", "in", "on", "with", "and", "or", "i", "need", "want", "some", "that",
        "into", "part", "parts", "component", "components", "mm"}


def tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", str(text).lower()) if t not in STOP]


def expand(words):
    out = list(words)
    for w in words:
        out += SYNONYMS.get(w, [])
        if w.endswith("s") and len(w) > 3:
            out.append(w[:-1])
    return out


def _haystack(c):
    return {
        "name": set(tokens(c.get("name", ""))),
        "tags": set(t for tag in c.get("tags", []) for t in tokens(tag)),
        "category": set(tokens(c.get("category", ""))),
        "description": set(tokens(c.get("description", ""))),
    }


def text_score(c, query):
    words = expand(tokens(query))
    if not words:
        return 0.0
    hay = _haystack(c)
    score = 0.0
    for w in set(words):
        if w in hay["name"]:
            score += 3
        if w in hay["tags"]:
            score += 2
        if w in hay["category"]:
            score += 2
        if w in hay["description"]:
            score += 1
    full = str(query).strip().lower()
    if full and full in str(c.get("name", "")).lower():
        score += 3
    return score


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def matching_sizes(c, constraints):
    """For a standard family: the sizes satisfying the constraints (None if the family can't satisfy them)."""
    std = c.get("std") or {}
    fam = standard.families().get(std.get("family", ""))
    if fam is None:
        return None
    sizes = []
    thread = str(constraints.get("thread") or constraints.get("size") or "").strip().lower()
    bore = _num(constraints.get("bore_d") or constraints.get("shaft_d"))
    od_max = _num(constraints.get("od_max"))
    for key, d in fam["sizes"].items():
        if thread and key.lower() != thread:
            continue
        if bore is not None:
            # a bore or shaft size means what fits on the shaft: a bearing's bore, or a pin of that size
            if fam["shape"] not in ("bearing", "pin"):
                continue
            have = d.get("id", d.get("d"))
            if have is None or abs(have - bore) > 0.01:
                continue
        if od_max is not None and (d.get("od") or d.get("dk") or d.get("d2") or d.get("s") or d.get("d") or 0) > od_max:
            continue
        sizes.append(key)
    return sizes


def fits_box(c, max_dims):
    bbox = c.get("bbox") or []
    if len(bbox) != 6 or not max_dims:
        return True
    have = sorted(bbox[i + 3] - bbox[i] for i in range(3))
    room = sorted(float(x) for x in max_dims)
    return all(h <= r + 1e-6 for h, r in zip(have, room))


def search(components, query="", category=None, constraints=None, limit=10):
    """[(score, component, sizes)] best first. `components` are meta dicts (team parts and standard families)."""
    constraints = constraints or {}
    out = []
    for c in components:
        if category and c.get("category") != category:
            continue
        sizes = None
        if (c.get("std") or {}).get("family"):
            sizes = matching_sizes(c, constraints)
            if (constraints.get("thread") or constraints.get("size") or constraints.get("bore_d")
                    or constraints.get("shaft_d") or constraints.get("od_max")) and not sizes:
                continue
        elif not fits_box(c, constraints.get("max_dims")):
            continue
        score = text_score(c, query) if query else 1.0
        if query and score == 0:
            continue
        if c.get("source") in ("upload", "url", "pack"):
            score += 0.5  # the team's own parts first
        score += min(int(c.get("uses") or 0), 20) * 0.1
        out.append((round(score, 2), c, sizes))
    out.sort(key=lambda t: (-t[0], str(t[1].get("name", ""))))
    return out[:limit]
