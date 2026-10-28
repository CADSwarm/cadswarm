"""The quality signals that already exist in the records: brief outcome, reviews, corrections."""
from cadswarm.training import labels


def step(index, op="set_props", status="done", broken=()):
    return {"index": index, "op": op, "args": {}, "status": status,
            "result": {"result": {}, "broken": list(broken)}}


def test_a_ready_brief_is_accepted_and_a_failed_one_is_not():
    assert labels.outcome({"status": "ready"}, []) == "accepted"
    assert labels.outcome({"status": "failed"}, []) == "failed"
    assert labels.outcome({"status": "new"}, []) == "abandoned"


def test_a_blocked_task_makes_the_episode_blocked():
    assert labels.outcome({"status": "building"}, [{"status": "blocked"}]) == "blocked"


def test_reviews_and_corrections_outrank_no_signal():
    assert labels.quality({"passed": True}, []) == "reviewed_pass"
    assert labels.quality({"passed": False}, []) == "reviewed_fail"
    assert labels.quality({"passed": True}, [{"after_index": 1}]) == "human_corrected"
    assert labels.quality(None, []) == "unreviewed"


def test_step_labels_prefer_bad_then_corrected_then_outcome():
    assert labels.step_label(step(1, status="failed"), set(), "accepted") == "bad"
    assert labels.step_label(step(2, broken=["X"]), set(), "accepted") == "bad"
    assert labels.step_label(step(3), {3}, "accepted") == "corrected"
    assert labels.step_label(step(4), set(), "accepted") == "good"
    assert labels.step_label(step(5), set(), "abandoned") == "neutral"


def test_attach_labels_every_step():
    episode = {"id": "e", "steps": [step(0), step(1, status="failed")], "corrections": [],
               "labels": {"outcome": "accepted", "quality": "unreviewed"}}
    labels.attach(episode)
    assert episode["labels"]["step_labels"] == {"0": "good", "1": "bad"}


def test_only_a_param_correction_makes_a_pair():
    episode = {"id": "e", "steps": [step(0), step(1)],
               "corrections": [
                   {"after_index": 1, "alias": "wall", "before": 3.0, "after": 2.4, "target": "Params.wall"},
                   {"after_index": 1, "alias": None, "edit": "set_props", "target": "Hood",
                    "props": {"height": 2.5}}]}
    [pair] = labels.pairs(episode)
    assert pair["signal"] == "correction"
    assert pair["chosen"] == {"op": "set_param", "args": {"alias": "wall", "value": 2.4}}
    assert pair["rejected"] == {"op": "set_param", "args": {"alias": "wall", "value": 3.0}}
