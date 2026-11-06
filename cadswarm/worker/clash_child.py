"""Pairwise interference in a child process, so one boolean that never ends can be stopped.

    python clash_child.py <snapshot.FCStd> <pairs json> <min volume>

The worker picks the pairs (boxes that overlap, cheapest first). This prints one JSON line per pair as it
finishes, {"a", "b", "volume", "clash"} or {"a", "b", "error"}, then {"done": true}. The worker reads them
until its budget runs out; a pair that never answered is reported as unchecked, never as clear.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(sys.executable))
import FreeCAD  # noqa: E402


def say(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def common_volume(a, b):
    """The overlap volume. An exact boolean can fail on nearly coincident faces (a body skin under an old
    roof panel gave Null shape); a fuzzy one usually settles it."""
    try:
        return a.common(b).Volume
    except Exception:
        return a.common(b, 0.01).Volume


def main(path, pairs, tol):
    doc = FreeCAD.openDocument(path)
    for na, nb in pairs:
        sa, sb = doc.getObject(na).Shape, doc.getObject(nb).Shape
        try:
            vol = common_volume(sa, sb)
        except Exception as e:
            say({"a": na, "b": nb, "error": f"{type(e).__name__}: {e}"[:200]})
            continue
        say({"a": na, "b": nb, "volume": round(vol, 4), "clash": vol > tol})
    say({"done": True})


if __name__ == "__main__":
    main(sys.argv[1], json.loads(sys.argv[2]), float(sys.argv[3]))
