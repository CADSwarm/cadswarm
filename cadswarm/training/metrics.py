"""Comparing a replayed model with the reference one, without heavy dependencies."""


def jaccard(a, b):
    a, b = set(a), set(b)
    if not a and not b:
        return 1.0
    return round(len(a & b) / len(a | b), 4)


def bbox_iou(a, b):
    """Axis-aligned IoU on [x0, y0, z0, x1, y1, z1]."""
    if not a or not b or len(a) < 6 or len(b) < 6:
        return 0.0
    inter = [max(0.0, min(a[3 + i], b[3 + i]) - max(a[i], b[i])) for i in range(3)]
    volume = inter[0] * inter[1] * inter[2]
    size_a = max(0.0, (a[3] - a[0]) * (a[4] - a[1]) * (a[5] - a[2]))
    size_b = max(0.0, (b[3] - b[0]) * (b[4] - b[1]) * (b[5] - b[2]))
    union = size_a + size_b - volume
    return round(volume / union, 4) if union > 0 else 0.0


def volume_ratio(a, b):
    """1.0 for equal volumes, falling off symmetrically."""
    a, b = float(a or 0), float(b or 0)
    if a <= 0 or b <= 0:
        return 0.0
    return round(min(a, b) / max(a, b), 4)


def _objects(view):
    return (view or {}).get("objects") or {}


def geometry(reference, candidate):
    """Name overlap plus per-name box and volume agreement.

    A box is only compared where both sides recorded one: the symbolic replay has volumes but no
    bounding boxes, so `bbox` is None rather than a zero that would read as a bad build.
    """
    ref, got = _objects(reference), _objects(candidate)
    shared = sorted(set(ref) & set(got))
    boxes = [bbox_iou((ref[n] or {}).get("bbox"), (got[n] or {}).get("bbox")) for n in shared
             if (ref[n] or {}).get("bbox") and (got[n] or {}).get("bbox")]
    volumes = [volume_ratio((ref[n] or {}).get("volume"), (got[n] or {}).get("volume"))
               for n in shared]
    return {"names": jaccard(ref, got),
            "bbox": round(sum(boxes) / len(boxes), 4) if boxes else None,
            "volume": round(sum(volumes) / len(volumes), 4) if volumes else None}


def aggregate(rows):
    """Averages the scalar metrics across cases; nested geometry dicts are flattened by key."""
    out, counts = {}, {}
    for row in rows:
        for key, value in row.items():
            values = value if isinstance(value, dict) else {key: value}
            for inner, number in values.items():
                if not isinstance(number, (int, float)) or isinstance(number, bool):
                    continue
                out[inner] = out.get(inner, 0.0) + number
                counts[inner] = counts.get(inner, 0) + 1
    return {key: round(total / counts[key], 4) for key, total in out.items() if counts[key]}
