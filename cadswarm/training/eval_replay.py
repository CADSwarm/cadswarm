"""Score a policy by building in a real FreeCAD document.

A fresh document per case, the same WorkerClient the app uses, and the worker's own error codes
surfaced rather than swallowed. The reference policy is the ceiling; the random policy is the
floor; `base` and `trained` are what the numbers are actually about.

Two honesty rules: a document containing nothing but the Params sheet is not printable (the
worker's check is `all([])`, which is true for an empty document), and cases never share a
document file.
"""
import os
import random
import re
import shutil
import tempfile

from ..worker_client import WorkerClient, WorkerError
from . import metrics

__all__ = ["WorkerError", "ScriptedPolicy", "RandomPolicy", "replay", "report"]


class ScriptedPolicy:
    """Replays a fixed op sequence: the reference case, or any recorded build."""
    name = "scripted"

    def __init__(self, ops):
        self.ops = [dict(op) for op in ops]

    def next_op(self, seen):
        return self.ops[len(seen)] if len(seen) < len(self.ops) else None


class RandomPolicy:
    """A deterministic floor: valid-shaped primitives with unrelated names.

    `replay` owns the step cap; this policy stops itself only when `max_steps` is given.
    """
    name = "random"

    def __init__(self, seed, max_steps=None):
        self.random = random.Random(seed)
        self.max_steps = max_steps

    def next_op(self, seen):
        if self.max_steps is not None and len(seen) >= self.max_steps:
            return None
        kind = self.random.choice(["box", "cylinder", "sphere"])
        return {"op": "create_primitive",
                "args": {"kind": kind, "name": f"R{self.random.randrange(1000)}"}}


def _op_view(objects):
    return {"objects": {o.get("name"): {"bbox": o.get("bbox"), "volume": o.get("volume")}
                        for o in objects or []}}


def _document_name(case):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(case.get("id") or "case"))
    return f"{safe}.FCStd"


def replay(case, policy, python=None, script=None, workdir=None, max_steps=40, client=None):
    """Run `policy` on a scratch document and score it against the case's reference model."""
    created = None
    if client is None:
        created = workdir or tempfile.mkdtemp(prefix="cadswarm-eval-")
        os.makedirs(created, exist_ok=True)
        worker = WorkerClient(os.path.join(created, _document_name(case)),
                              python=python, script=script)
    else:
        worker = client
        created = workdir
    seen = []
    try:
        for _ in range(max_steps):
            op = policy.next_op(seen)
            if op is None:
                break
            try:
                worker.call(op["op"], op.get("args") or {}, allowed=op.get("allowed"))
                seen.append({"op": op["op"], "args": op.get("args"), "ok": True, "code": ""})
            except WorkerError as e:
                seen.append({"op": op["op"], "args": op.get("args"), "ok": False, "code": e.code})
        final = worker.call("status").get("result") or {}
        objects = final.get("objects") or []
        candidates = [o for o in objects if o.get("name") != "Params"]
        printable = bool(candidates) and bool(
            (worker.call("printability", {}).get("result") or {}).get("ok"))
    finally:
        worker.stop()
        if client is None and workdir is None and created:
            shutil.rmtree(created, ignore_errors=True)

    attempted = len(seen)
    valid = sum(1 for step in seen if step["ok"])
    rejected = sum(1 for step in seen if step["code"] == "lock_violation")
    reference = {name: value for name, value in ((case.get("final") or {}).get("objects") or {}).items()
                 if name != "Params"}
    candidates = [o for o in objects if o.get("name") != "Params"]
    return {"case": case.get("id", "?"), "steps": attempted,
            "valid_op_rate": round(valid / attempted, 4) if attempted else 0.0,
            "rejection_rate": round(rejected / attempted, 4) if attempted else 0.0,
            "completion": metrics.jaccard(reference.keys(),
                                          [o.get("name") for o in candidates]),
            "geometry": metrics.geometry({"objects": reference}, _op_view(candidates)),
            "printable": printable, "ops": seen}


def report(results, policy):
    """One comparable row set per policy, with the case count that produced it."""
    summary = metrics.aggregate(results)
    summary.update({"policy": policy, "cases": len(results),
                    "printable_rate": round(sum(1 for r in results if r.get("printable"))
                                            / len(results), 4) if results else 0.0})
    return summary
