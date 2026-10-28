"""Scoring a policy by running it, through the same JSON-lines worker the app uses."""
from cadswarm.training import eval_replay


class FakeWorker:
    """Accepts create_primitive, refuses anything named Bad, and describes one box."""

    def __init__(self):
        self.calls = []

    def call(self, op, args=None, allowed=None):
        self.calls.append((op, args))
        if op == "create_primitive":
            if (args or {}).get("name") == "Bad":
                raise eval_replay.WorkerError("lock_violation", "not yours")
            return {"ok": True, "result": {"name": args["name"]}, "created": [args["name"]],
                    "modified": [], "deleted": [], "recomputed": []}
        if op == "status":
            return {"ok": True, "result": {"objects": [{"name": "Base", "bbox": [0, 0, 0, 1, 1, 1],
                                                        "volume": 1.0}], "params": {}}}
        if op == "printability":
            return {"ok": True, "result": {"ok": True, "objects": []}}
        raise AssertionError(op)

    def stop(self):
        pass


def case(ops):
    return {"id": "p/1", "request": {"text": "x", "plan_json": {}},
            "state": {"objects": {}, "params": {}},
            "reference": ops, "final": {"objects": {"Base": {"bbox": [0, 0, 0, 1, 1, 1],
                                                             "volume": 1.0}}},
            "stl_sha256": None}


def box(name):
    return {"op": "create_primitive", "args": {"kind": "box", "name": name}}


def test_a_scripted_policy_reproduces_the_reference_score():
    ops = [box("Base")]
    got = eval_replay.replay(case(ops), eval_replay.ScriptedPolicy(ops), client=FakeWorker())
    assert got["valid_op_rate"] == 1.0 and got["completion"] == 1.0
    assert got["geometry"]["bbox"] == 1.0 and got["printable"] is True


def test_a_refused_op_counts_against_the_valid_rate():
    ops = [box("Bad")]
    got = eval_replay.replay(case(ops), eval_replay.ScriptedPolicy(ops), client=FakeWorker())
    assert got["valid_op_rate"] == 0.0 and got["rejection_rate"] == 1.0


def test_the_random_baseline_is_deterministic_and_stops_at_the_cap():
    one = eval_replay.replay(case([box("Base")]), eval_replay.RandomPolicy(seed=4), client=FakeWorker(),
                             max_steps=3)
    two = eval_replay.replay(case([box("Base")]), eval_replay.RandomPolicy(seed=4), client=FakeWorker(),
                             max_steps=3)
    assert one["steps"] == two["steps"] == 3
    assert [o["args"] for o in one["ops"]] == [o["args"] for o in two["ops"]]


def test_a_policy_that_returns_nothing_stops():
    got = eval_replay.replay(case([]), eval_replay.ScriptedPolicy([]), client=FakeWorker())
    assert got["steps"] == 0 and got["valid_op_rate"] == 0.0


def test_report_aggregates_and_names_the_policy():
    rows = [eval_replay.replay(case([box("Base")]), eval_replay.ScriptedPolicy([box("Base")]),
                               client=FakeWorker())]
    got = eval_replay.report(rows, policy="reference")
    assert got["policy"] == "reference" and got["cases"] == 1 and got["completion"] == 1.0
