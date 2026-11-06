"""What the AI Build task pane shows, as plain data (no Qt, no FreeCAD: unit-testable).

The pane shows one request (a brief) as its numbered steps: done / current / pending, each step's op feed
with durations, a progress bar and ``N of M done · now: X``. The numbers come from ``cadswarm.step_plan``
(the same progress model every client uses). The data comes either from the hub's
``GET /briefs/<id>/progress?ops=1`` (MCP: build_progress: statuses and each step's ``op_feed``) or, when the team's hub predates it, from the panel's
own snapshot: the brief's tasks, whose titles read ``"<n>. <name>"``.
"""
import re

from .. import step_plan

KIND_WORDS = {"part": "Part", "library": "Library", "assembly": "Assembly", "check": "Check"}
STATE_DONE, STATE_CURRENT, STATE_PENDING, STATE_BLOCKED, STATE_CANCELLED = (
    "done", "current", "pending", "blocked", "cancelled")
_NUMBERED = re.compile(r"^\s*(\d+)\s*[.)]\s*(.+)$")


def step_state(status):
    """A task status as one of the five states a step row draws."""
    if status == "done":
        return STATE_DONE
    if status in step_plan.ACTIVE:
        return STATE_CURRENT
    if status == "blocked":
        return STATE_BLOCKED
    if status == "cancelled":
        return STATE_CANCELLED
    return STATE_PENDING


def duration_text(seconds):
    """1.94 -> "1.9 s", 75 -> "1 min 15 s", None -> ""."""
    if seconds is None:
        return ""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return ""
    if s < 60:
        return f"{s:.1f} s"
    m, rest = divmod(int(round(s)), 60)
    return f"{m} min {rest} s" if rest else f"{m} min"


def feed_row(op):
    """One op-feed line: {"label", "target", "time", "running", "failed"}. Takes a step_plan.op_entry dict, a
    raw op row, or a plain string (an op the plan expects, not run yet)."""
    if isinstance(op, str):
        return {"label": op, "target": "", "time": "", "running": False, "failed": False, "planned": True}
    if "label" not in op and ("kind" in op or "args_json" in op):
        op = step_plan.op_entry(op)
    targets = op.get("targets") or []
    status = str(op.get("status") or "")
    failed = status in ("failed", "error", "refused")
    running = bool(op.get("running")) and not failed
    return {"label": str(op.get("label") or step_plan.humanise(op.get("kind")) or "Op"),
            "target": ", ".join(str(t) for t in targets[:3]),
            "time": "Failed" if failed else "Running" if running else duration_text(op.get("duration_s")),
            "running": running, "failed": failed, "planned": False}


def _feed_of(step):
    """The ops a step ran (dicts), under whichever key the hub used."""
    for key in ("feed", "ops_run", "op_feed", "ops_done", "ran"):
        v = step.get(key)
        if isinstance(v, list):
            return [o for o in v if isinstance(o, dict)]
    return [o for o in step.get("ops") or [] if isinstance(o, dict)]


def _planned_of(step):
    return [o for o in step.get("ops") or [] if isinstance(o, str)]


def step_row(step):
    """One row of the step list."""
    status = step.get("status") or "missing"
    state = step_state(status)
    feed = [feed_row(o) for o in _feed_of(step)]
    ran = {r["label"].lower() for r in feed}
    # The ops the plan still expects ("Set color", "Save part") show greyed after what already ran.
    planned = [feed_row(o) for o in _planned_of(step)
               if state in (STATE_CURRENT, STATE_PENDING) and not any(o.lower().startswith(r) for r in ran)]
    who = step.get("owner") or step.get("assignee") or ""
    waiting = step.get("waiting_for") or []
    if state == STATE_PENDING and status == "open":
        note = ("Waiting for " + ", ".join(waiting)) if waiting else "Ready"
    elif state == STATE_CURRENT:
        note = f"{who} is building it" if who else "Building"
    elif state == STATE_BLOCKED:
        note = "Stuck: needs a person"
    else:
        note = ""
    return {"n": step.get("n"), "id": step.get("id"), "name": step.get("name") or "Step",
            "kind": step.get("kind") or "part", "kind_word": KIND_WORDS.get(step.get("kind"), "Part"),
            "summary": step.get("summary") or "", "state": state, "status": status, "who": who,
            "task": step.get("task") or "", "note": note, "feed": feed, "planned": planned,
            "ready": bool(step.get("ready"))}


def view(progress, title=""):
    """The pane's model from a progress dict (step_plan.progress, or the hub's build_progress answer)."""
    steps = [step_row(s) for s in progress.get("steps") or [] if isinstance(s, dict)]
    total = len(steps)
    done = sum(1 for s in steps if s["state"] == STATE_DONE)
    current = [s["name"] for s in steps if s["state"] == STATE_CURRENT]
    return {"title": title or progress.get("title") or "", "steps": steps, "total": total, "done": done,
            "current": current, "line": step_plan.summary_line(done, total, current) if total else "No steps yet",
            "fraction": (done / total) if total else 0.0}


def plan_from_tasks(brief_id, tasks):
    """A version-2 plan read back from a brief's tasks (titles "<n>. <name>"), for hubs without build_progress."""
    mine = [t for t in tasks or [] if t.get("brief") == brief_id]
    rows = []
    for i, t in enumerate(sorted(mine, key=lambda t: t.get("created_at") or 0)):
        m = _NUMBERED.match(t.get("title") or "")
        n, name = (int(m.group(1)), m.group(2).strip()) if m else (None, (t.get("title") or "").strip())
        rows.append((n if n is not None else 1000 + i, name, t))
    rows.sort(key=lambda r: r[0])
    steps = [{"n": i + 1 if n >= 1000 else n, "id": f"s{(i + 1 if n >= 1000 else n):02d}", "name": name,
              "kind": "part", "task": t["id"], "objects": list(t.get("objects") or []),
              "summary": "" if (t.get("detail") or "") == (t.get("title") or "") else (t.get("detail") or "")[:240]}
             for i, (n, name, t) in enumerate(rows)]
    return step_plan.normalise({"version": 2, "steps": steps})


def from_snapshot(brief, tasks):
    """The pane's model from the panel's snapshot alone (no op feed: older hubs don't send ops)."""
    if not brief:
        return view({"steps": []})
    plan = plan_from_tasks(brief["id"], tasks)
    return view(step_plan.progress(plan, tasks), title=brief.get("text") or "")


def from_route(answer, brief=None, tasks=None):
    """The pane's model from the hub's build_progress answer. Accepts the progress dict itself, or one
    wrapped as {"progress": {...}} / {"build": {...}}; a bare {"plan": ..., "ops": [...]} is computed here."""
    if not isinstance(answer, dict):
        return from_snapshot(brief, tasks or [])
    for key in ("progress", "build"):
        if isinstance(answer.get(key), dict):
            inner = dict(answer[key])
            inner.setdefault("title", answer.get("title"))
            answer = inner
            break
    title = answer.get("title") or (brief or {}).get("text") or ""
    if isinstance(answer.get("steps"), list) and answer["steps"] and "status" in answer["steps"][0]:
        return view(answer, title=title)
    plan = answer.get("plan") or answer.get("plan_json")
    if plan:
        plan = step_plan.load(plan)
        rows = answer.get("tasks") or tasks or []
        prog = step_plan.progress(plan, rows)
        ops = answer.get("ops") or []
        by_id = {t["id"]: t for t in rows if isinstance(t, dict) and "id" in t}
        for s in prog["steps"]:
            t = by_id.get(s["task"]) or {}
            start = t.get("claimed_ms") or (t.get("created_at") or 0) * 1000 or None
            end = (t.get("updated_at") or 0) * 1000 if t.get("status") == "done" else None
            s["feed"] = step_plan.step_ops(s, ops, owner=t.get("owner"), start_ms=start, end_ms=end)
        return view(prog, title=title or plan.get("title"))
    return from_snapshot(brief, tasks or [])


def view_key(model):
    """What a redraw depends on (the list is rebuilt only when this changes)."""
    return (model["title"], model["line"], tuple(
        (s["n"], s["name"], s["state"], s["who"], s["note"], tuple((f["label"], f["target"], f["time"])
                                                                  for f in s["feed"]),
         tuple(p["label"] for p in s["planned"])) for s in model["steps"]))


def pick_brief(briefs, chosen=None):
    """The brief the pane follows: the one picked, else the newest that is still building, else the newest."""
    briefs = list(briefs or [])
    if chosen:
        hit = next((b for b in briefs if b.get("id") == chosen), None)
        if hit:
            return hit
    live = [b for b in briefs if b.get("status") not in ("ready", "failed", "cancelled")]
    pool = live or briefs
    return max(pool, key=lambda b: b.get("at") or 0) if pool else None
