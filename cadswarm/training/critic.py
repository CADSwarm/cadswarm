"""The floor a learned critic must beat: cheap, explainable, and honest about what it cannot see.

A model that scores worse than this on held-out projects is not worth shipping, so it exists to
make every critic run comparable from the first day, before any pairs exist.
"""


def heuristic(final, printable=None):
    """0..1 from solidity, printability and (when recorded) curved-surface share."""
    objects = (final or {}).get("objects") or {}
    if not objects:
        return 0.0
    known = sum(1 for o in objects.values() if o.get("known"))
    curved = (final or {}).get("surface_quality") or {}
    score = 0.4 * known / len(objects)
    score += 0.4 * (1.0 if printable else 0.0) if printable is not None else 0.2
    share = curved.get("curved_share") if isinstance(curved, dict) else None
    score += 0.2 * (0.5 if share is None else min(float(share) / 0.2, 1.0))
    return round(min(score, 1.0), 3)
