"""One build = one episode: a task's steps with its brief, corrections and verdicts.

Attribution is explicit when the op carries a task id (only new sessions do), otherwise the task
whose objects the op touched inside that task's lifetime wins, and everything else is a
freestyle stretch per actor, split on a 15-minute idle gap. An op never lands twice.
"""
import json

from . import labels
from .state import replay

IDLE_GAP_MS = 15 * 60 * 1000
TASK_SLACK_MS = 5 * 60 * 1000
CORRECTION_WINDOW_MS = 60 * 1000
FIELDS = ("created", "modified", "deleted", "recomputed", "broken")


def _json(value, default):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _result(op):
    return _json(op.get("result_json"), {})


def _args(op):
    return _json(op.get("args_json"), {})


def names(op):
    result = _result(op)
    return [n for field in FIELDS for n in (result.get(field) or []) if isinstance(n, str)]


def step_names(step):
    """The object names a finished step touched, from its already-parsed result."""
    result = step.get("result") or {}
    return [n for field in FIELDS for n in (result.get(field) or []) if isinstance(n, str)]


def _actors(members):
    return {m["id"]: {"id": m["id"], "name": m.get("name", str(m["id"])),
                      "kind": m.get("kind", "unknown")} for m in members}


def _match_task(op, tasks):
    explicit = op.get("task") or _args(op).get("task")
    if explicit and explicit in tasks:
        return explicit
    touched, at = set(names(op)), op.get("created_ms", 0)
    best = None
    for task in tasks.values():
        objects = set(task.get("objects") or [])
        if not touched & objects:
            continue
        if not (task.get("created_ms", 0) - TASK_SLACK_MS <= at
                <= task.get("updated_ms", 0) + TASK_SLACK_MS):
            continue
        rank = (len(objects), task.get("created_ms", 0), task["id"])
        if best is None or rank < best[0]:
            best = (rank, task["id"])
    return best[1] if best else None


def _step(op, actors, index):
    result = _result(op)
    return {"index": index, "team_seq": result.get("seq"), "op_id": op.get("id"),
            "at_ms": op.get("created_ms", 0),
            "actor": actors.get(op.get("by"), {"id": op.get("by"), "name": "?", "kind": "unknown"}),
            "op": op.get("kind"), "args": _args(op),
            "result": {key: result.get(key) for key in ("result",) + FIELDS},
            "status": op.get("status"), "error_code": op.get("error_code") or ""}


step_from_op = _step


def replay_project(raw):
    """(before, final, degraded) for the project's whole op log, in order.

    The dataset build uses this for conformance: the replay must end where the live model is.
    """
    ordered = sorted(raw.get("op") or [], key=lambda o: (o.get("created_ms", 0), o.get("id", "")))
    return replay([step_from_op(op, {}, i) for i, op in enumerate(ordered)])


def _corrections(events):
    out = []
    for event in events or []:
        if event.get("kind") != "correction":
            continue
        data = _json(event.get("data"), {})
        out.append({"at_ms": int(float(event.get("at") or 0) * 1000),
                    "target": str(data.get("target") or ""), "alias": data.get("alias"),
                    "before": data.get("before"), "after": data.get("after"),
                    "edit": data.get("edit") or "", "props": data.get("props") or {},
                    "actor_kind": event.get("actor_kind") or "",
                    "evidence": f"style_events#{event.get('id')}"})
    return sorted(out, key=lambda c: c["at_ms"])


def _corrected_step(correction, steps):
    """The latest agent step before the correction that touched the same subject."""
    for step in reversed(steps):
        if step["at_ms"] > correction["at_ms"]:
            continue
        if correction["at_ms"] - step["at_ms"] > CORRECTION_WINDOW_MS:
            break
        if step["actor"]["kind"] != "agent":
            continue
        if (correction["alias"] and step["op"] == "set_param"
                and step["args"].get("alias") == correction["alias"]):
            return step["index"]
        if correction["target"] in set(step_names(step)):
            return step["index"]
    return None


def _episode(project, key, group, actors, tasks, briefs, reviews, corrections, feedback):
    steps = [_step(op, actors, i) for i, op in enumerate(group["ops"])]
    before, final, degraded = replay(steps)
    for position, step in enumerate(steps):
        step["state_before"] = before[position]
        step["state_confidence"] = "degraded" if position in degraded else "full"

    task = tasks.get(key[1]) if key[0] == "task" else None
    brief = briefs.get((task or {}).get("brief") or "") or {}
    found = []
    for correction in corrections:
        if not (steps[0]["at_ms"] - CORRECTION_WINDOW_MS <= correction["at_ms"]
                <= steps[-1]["at_ms"] + CORRECTION_WINDOW_MS):
            continue
        index = _corrected_step(correction, steps)
        if index is not None:
            found.append({**correction, "after_index": index})
    review = (reviews.get(brief.get("id")) or [{}])[-1] if brief else {}
    episode = {
        "schema_version": 1,
        "id": f"{project}/{key[1]}",
        "project": project,
        "attribution": group["attribution"],
        "request": {"text": brief.get("text", ""), "board": brief.get("board", ""),
                    "plan_json": _json(brief.get("plan_json"), {})},
        "segments": [{"task_id": task["id"], "title": task.get("title", ""),
                      "detail": task.get("detail", ""), "objects": task.get("objects") or [],
                      "assignee_kind": task.get("assignee") or task.get("status", ""),
                      "status": task.get("status", ""),
                      "result": _json(task.get("result_json"), {})}] if task else [],
        "steps": steps,
        "corrections": found,
        "review": {k: review.get(k) for k in ("round", "passed", "findings_json")} if review else None,
        "feedback": [f for f in feedback
                     if steps[0]["at_ms"] - CORRECTION_WINDOW_MS <= f["at_ms"]
                     <= steps[-1]["at_ms"] + CORRECTION_WINDOW_MS],
        "final": {"stl_sha256": brief.get("stl_hash") or None, "objects": final["objects"],
                  "params": final["params"], "surface_quality": None},
        "labels": {"outcome": labels.outcome(brief, [task] if task else []),
                   "quality": labels.quality(review or None, found)},
        "warnings": [f"step {i}: unknown object names" for i in degraded],
        "first_ms": steps[0]["at_ms"],
    }
    labels.attach(episode)
    return episode


def build_episodes(raw, project):
    """Every op of one project, grouped into episodes. Input order never matters."""
    actors = _actors(raw.get("member") or [])
    ops = sorted(raw.get("op") or [], key=lambda o: (o.get("created_ms", 0), o.get("id", "")))
    tasks = {t["id"]: t for t in raw.get("task") or []}
    briefs = {b["id"]: b for b in raw.get("brief") or []}
    reviews = {}
    for review in raw.get("review") or []:
        reviews.setdefault(review.get("brief"), []).append(review)
    corrections = _corrections(raw.get("style_events"))
    feedback = [{"at_ms": int(float(e.get("at") or 0) * 1000),
                 "verdict": _json(e.get("data"), {}).get("verdict"),
                 "subject": _json(e.get("data"), {}).get("subject")}
                for e in raw.get("style_events") or [] if e.get("kind") == "feedback"]

    groups, order, free_open, last_op = {}, [], {}, {}
    for op in ops:
        actor, at = op.get("by"), op.get("created_ms", 0)
        task_id = _match_task(op, tasks)
        if task_id:
            key = ("task", task_id)
            attribution = "task_linked" if (op.get("task") or _args(op).get("task")) else "object_window"
            free_open[actor] = None      # a freestyle stretch ends when the actor returns to a task
        else:
            key = free_open.get(actor)
            if key is None or at - last_op.get(actor, at) > IDLE_GAP_MS:
                key = ("free", f"{actor}-{at}")
                free_open[actor] = key
            attribution = "freestyle"
        last_op[actor] = at
        if key not in groups:
            order.append(key)
            groups[key] = {"ops": [], "attribution": attribution}
        groups[key]["ops"].append(op)
        if attribution == "task_linked":
            groups[key]["attribution"] = "task_linked"

    return [_episode(project, key, groups[key], actors, tasks, briefs, reviews, corrections, feedback)
            for key in order]
