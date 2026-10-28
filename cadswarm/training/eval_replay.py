"""Score a policy by building in a real FreeCAD document.

A fresh scratch document per case, the same WorkerClient the app uses, and the worker's own
error codes surfaced rather than swallowed. The reference policy is the ceiling; the random
policy is the floor; `base` and `trained` are what the numbers are actually about.
"""
import os
import random
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
    """A deterministic floor: valid-shaped primitives with unrelated names."""
    name = "random"

    def __init__(self, seed, max_steps=8):
        self.random = random.Random(seed)
        self.max_steps = max_steps

    def next_op(self, seen):
        if len(seen) >= self.max_steps:
            return None
        kind = self.random.choice(["box", "cylinder", "sphere"])
        return {"op": "create_primitive",
                "args": {"kind": kind, "name": f"R{self.random.randrange(1000)}"}}


def _op_view(objects):
    return {"objects": {o.get("name"): {"bbox": o.get("bbox"), "volume": o.get("volume"),
                                        "known": True} for o in objects or []}}


def replay(case, policy, python=None, script=None, workdir=None, max_steps=40, client=None):
    """Run `policy` on a scratch document and score it against the case's reference model."""
    root = workdir or tempfile.mkdtemp(prefix="cadswarm-eval-")
    os.makedirs(root, exist_ok=True)
    worker = client or WorkerClient(os.path.join(root, "eval.FCStd"), python=python, script=script)
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
        printable = bool((worker.call("printability", {}).get("result") or {}).get("ok"))
    finally:
        worker.stop()

    attempted = len(seen)
    valid = sum(1 for step in seen if step["ok"])
    rejected = sum(1 for step in seen if step["code"] == "lock_violation")
    reference = (case.get("final") or {}).get("objects") or {}
    candidate = _op_view(final.get("objects"))
    return {"case": case["id"], "steps": attempted,
            "valid_op_rate": round(valid / attempted, 4) if attempted else 0.0,
            "rejection_rate": round(rejected / attempted, 4) if attempted else 0.0,
            "completion": metrics.jaccard(reference.keys(),
                                          [o.get("name") for o in final.get("objects") or []]),
            "geometry": metrics.geometry({"objects": reference}, candidate),
            "printable": printable, "ops": seen}


def report(results, policy):
    """One comparable row set per policy, with the case count that produced it."""
    summary = metrics.aggregate(results)
    summary.update({"policy": policy, "cases": len(results),
                    "printable_rate": round(sum(1 for r in results if r["printable"]) / len(results), 4)
                    if results else 0.0})
    return summary
