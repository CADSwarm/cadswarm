"""What a step and a build are worth, from records the app already writes.

`outcome` comes from the brief, `quality` from the review and from human corrections, and a step
is `bad` when it failed or broke something. A param correction is the only record that keeps both
sides of a decision, so it is the only true preference pair in the back-catalogue.
"""


def outcome(brief, tasks):
    status = (brief or {}).get("status", "")
    if status == "ready":
        return "accepted"
    if status == "failed":
        return "failed"
    if status == "cancelled":
        return "abandoned"
    if any((t or {}).get("status") == "blocked" for t in tasks or []):
        return "blocked"
    return "abandoned"


def quality(review, corrections):
    if corrections:
        return "human_corrected"
    if review is not None and "passed" in review:
        return "reviewed_pass" if review["passed"] else "reviewed_fail"
    return "unreviewed"


def step_label(step, corrected_indexes, outcome_):
    if step.get("status") != "done" or (step.get("result") or {}).get("broken"):
        return "bad"
    if step.get("index") in corrected_indexes:
        return "corrected"
    return "good" if outcome_ == "accepted" else "neutral"


def attach(episode):
    """Fill the per-step labels. The episode must already carry `labels.outcome`."""
    corrected = {c["after_index"] for c in episode.get("corrections") or [] if "after_index" in c}
    outcome_ = (episode.get("labels") or {}).get("outcome", "abandoned")
    episode.setdefault("labels", {})["outcome"] = outcome_
    episode["labels"]["step_labels"] = {
        str(step["index"]): step_label(step, corrected, outcome_) for step in episode.get("steps") or []}


def pairs(episode):
    """(chosen, rejected) pairs this episode proves. Only a param correction has both sides."""
    steps = {step["index"]: step for step in episode.get("steps") or []}
    out = []
    for correction in episode.get("corrections") or []:
        alias, before, after = correction.get("alias"), correction.get("before"), correction.get("after")
        if not alias or before is None or after is None or before == after:
            continue
        out.append({"signal": "correction", "episode": episode["id"],
                    "state": (steps.get(correction.get("after_index")) or {}).get("state_before"),
                    "chosen": {"op": "set_param", "args": {"alias": alias, "value": after}},
                    "rejected": {"op": "set_param", "args": {"alias": alias, "value": before}}})
    return out
