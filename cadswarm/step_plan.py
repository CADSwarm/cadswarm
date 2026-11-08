"""Step plans for briefs (`Brief.plan_json`, version 2).

A plan is an ordered, numbered list of steps. Standalone teams accept `part` and `check` steps; older plan
kinds and operations remain readable for compatibility but are rejected for new standalone plans. Each step
records the ids of earlier dependencies and the task that tracks it. A version-1 plan (only `parts`) reads as
steps in list order with no dependencies.

Contract: docs/superpowers/specs/2026-11-06-step-build-and-pro-ui-design.md. Pure Python, no FreeCAD.
"""
import json

VERSION = 2
KINDS = ("part", "library", "assembly", "check")
# Task statuses that still need work, and those that mean the step is finished or will never finish.
REMAINING = ("open", "claimed", "awaiting_takeover", "blocked")
ACTIVE = ("claimed", "awaiting_takeover")
# Kinds that make a physical component: these are the `parts` older readers (export, the app) list.
COMPONENT_KINDS = ("part", "library")
# The closed op vocabulary: a plan's `ops` entries start with one of these, and the narrated op feed uses
# the same words, so the feed can be matched against the plan ("Create sketch SK_Mast", "Extrude Mast").
OP_VOCABULARY = ("Create sketch", "Import STEP/STL", "Primitive", "Extrude", "Pocket", "Cut", "Revolve", "Fillet", "Chamfer", "Hole",
                 "Pattern", "Mirror", "Shell", "Boolean", "Set dimensions", "Set placement", "Export STEP/STL",
                 "Insert component", "Place standard part", "Mate", "Set color", "Check interference",
                 "Check printability", "Render", "Save part")
STANDALONE_PLAN_KINDS = ("part", "check")
STANDALONE_OP_VOCABULARY = ("Create sketch", "Import STEP/STL", "Primitive", "Extrude", "Pocket", "Cut", "Revolve", "Fillet",
                             "Chamfer", "Hole", "Pattern", "Boolean", "Set dimensions", "Set placement",
                             "Check interference", "Export STEP/STL")
# Assembly mates over named ports ("Base.mast_bore"). Exactly one part is grounded with `fixed`, and the mate
# graph between parts stays an acyclic tree.
MATE_TYPES = ("fixed", "coincident", "concentric", "distance", "angle", "parallel", "revolute", "slider")
VALUED_MATES = ("distance", "angle")
PORT_TYPES = ("axis", "plane", "point", "face", "edge")


class PlanError(ValueError):
    """The plan cannot be used; `problems` lists each fix the planner has to make."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("Invalid step plan: " + "; ".join(self.problems))


# ----------------------------------------------------------------- reading

def _str(v):
    return "" if v is None else str(v).strip()


def _list(v):
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return [x for x in v if x is not None and _str(x) != ""]
    return [v]


def _int(v):
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else None


def _as_dict(plan):
    if plan is None or plan == "":
        return {}
    if isinstance(plan, (bytes, bytearray)):
        plan = plan.decode("utf-8")
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except ValueError as e:
            raise PlanError([f"plan_json is not valid JSON ({e})"])
    if not isinstance(plan, dict):
        raise PlanError(["plan_json must be a JSON object with a `steps` list"])
    return plan


def _component(v):
    if isinstance(v, dict):
        return {k: x for k, x in v.items() if x is not None and x != ""}
    s = _str(v)
    return s or None


def _step(raw, index):
    """One step with every field present and typed. Unknown keys are kept."""
    if not isinstance(raw, dict):
        return {"n": index + 1, "id": f"s{index + 1:02d}", "name": _str(raw), "kind": "part", "summary": "",
                "task": "", "objects": [], "depends_on": [], "ops": [], "assignee": "", "_raw_bad": True}
    n = _int(raw.get("n"))
    if n is None:
        n = index + 1
    s = dict(raw)
    s.update({
        "n": n,
        "id": _str(raw.get("id")) or f"s{n:02d}",
        "name": _str(raw.get("name") or raw.get("title")),
        "kind": (_str(raw.get("kind")) or "part").lower(),
        "summary": _str(raw.get("summary")),
        "task": _str(raw.get("task") or raw.get("task_id")),
        "objects": [_str(o) for o in _list(raw.get("objects"))],
        "depends_on": _list(raw.get("depends_on", raw.get("deps"))),
        "ops": [_str(o) for o in _list(raw.get("ops"))],
        "assignee": _str(raw.get("assignee")),
    })
    s.pop("title", None)
    s.pop("deps", None)
    s.pop("task_id", None)
    if "component" in raw or s["kind"] == "library":
        s["component"] = _component(raw.get("component"))
    return s


def _resolve_deps(steps):
    """Dependencies may name a step by id or by number; store ids."""
    by_n = {s["n"]: s["id"] for s in steps}
    ids = {s["id"] for s in steps}
    for s in steps:
        out = []
        for d in s["depends_on"]:
            if isinstance(d, str) and d.strip() in ids:
                ref = d.strip()
            elif _int(d) is not None and _int(d) in by_n and _str(d) not in ids:
                ref = by_n[_int(d)]
            else:
                ref = _str(d)
            if ref not in out:
                out.append(ref)
        s["depends_on"] = out


def derive_parts(steps):
    """The `parts` list version-1 readers expect: one entry per component step."""
    return [{"name": s["name"], "task": s["task"], "summary": s["summary"]}
            for s in steps if s["kind"] in COMPONENT_KINDS]


def normalise(plan):
    """The plan as a version-2 dict: `steps` sorted by number, every field present, `parts` derived when absent.

    Accepts a dict or its JSON text, version 2 (`steps`) or version 1 (`parts` only). Raises PlanError only
    when the input is not a JSON object at all; use `validate` for the rules."""
    p = dict(_as_dict(plan))
    raw_steps = p.get("steps")
    if raw_steps is None and isinstance(p.get("parts"), list):
        # Version 1: the parts, in list order, as part steps with no dependencies.
        raw_steps = [{"n": i + 1, "id": f"s{i + 1:02d}", "kind": "part",
                      **({k: v for k, v in part.items() if k in ("name", "task", "summary", "objects")}
                         if isinstance(part, dict) else {"name": part})}
                     for i, part in enumerate(p["parts"])]
    steps = [_step(r, i) for i, r in enumerate(raw_steps if isinstance(raw_steps, list) else [])]
    _resolve_deps(steps)
    steps.sort(key=lambda s: s["n"])
    p["version"] = VERSION
    p["title"] = _str(p.get("title"))
    p["steps"] = steps
    if not isinstance(p.get("parts"), list) or not p["parts"]:
        p["parts"] = derive_parts(steps)
    if raw_steps is not None and not isinstance(raw_steps, list):
        p["_steps_not_a_list"] = True
    return p


def load(plan_json):
    """Lenient read for progress and assignment: a normalised plan, or an empty one if it can't be read."""
    try:
        return normalise(plan_json)
    except PlanError:
        return normalise({})


def _has_cycle(steps):
    deps = {s["id"]: [d for d in s["depends_on"]] for s in steps}
    state = {}

    def visit(i):
        state[i] = 1
        for d in deps.get(i, []):
            if d not in deps:
                continue
            if state.get(d) == 1 or (state.get(d) is None and visit(d)):
                return True
        state[i] = 2
        return False

    return any(state.get(i) is None and visit(i) for i in deps)


def validate(plan, task_ids=None):
    """Every problem with a plan, as sentences a planner can act on; empty when it is valid.

    `plan` may be raw or normalised. `task_ids`, when given, is the set of task ids that exist for the brief:
    each step's `task` must be one of them."""
    try:
        p = normalise(plan)
    except PlanError as e:
        return e.problems
    problems = []
    if p.get("_steps_not_a_list"):
        return ["`steps` must be a list of step objects"]
    steps = p["steps"]
    if not steps:
        return ["the plan has no steps: give `steps` (version 2) with one step per component or action"]
    seen_ids, seen_n, seen_tasks = {}, {}, {}
    for s in steps:
        label = f"step {s['n']}" + (f" ({s['name']})" if s["name"] else "")
        if s.get("_raw_bad"):
            problems.append(f"{label} must be an object with n, id, name, kind and summary")
            continue
        if s["n"] < 1:
            problems.append(f"{label}: `n` must be a whole number from 1")
        if s["n"] in seen_n:
            problems.append(f"step number {s['n']} is used twice ({seen_n[s['n']]} and {s['id']})")
        seen_n.setdefault(s["n"], s["id"])
        if s["id"] in seen_ids:
            problems.append(f"step id {s['id']!r} is used twice")
        seen_ids.setdefault(s["id"], s)
        if not s["name"]:
            problems.append(f"{label} has no `name`")
        if s["kind"] not in KINDS:
            problems.append(f"{label}: kind {s['kind']!r} must be one of {', '.join(KINDS)}")
        if s["kind"] == "library":
            c = s.get("component")
            if not c or (isinstance(c, dict) and not (c.get("id") or (c.get("family") and c.get("size")))):
                problems.append(f"{label} is a library step but has no `component` (the chosen library id, or "
                                f"{{\"family\", \"size\"}} from recommend_components / search_components)")
        if not s["task"]:
            problems.append(f"{label} has no `task`: create_task for it and put the task id here")
        elif task_ids is not None and s["task"] not in task_ids:
            problems.append(f"{label}: task {s['task']!r} is not a task of this brief (create_task with brief set)")
        elif s["task"] in seen_tasks:
            problems.append(f"{label} shares task {s['task']!r} with step {seen_tasks[s['task']]}: one task per step")
        if s["task"]:
            seen_tasks.setdefault(s["task"], s["n"])
        problems.extend(_optional_problems(s, label))
    for s in steps:
        label = f"step {s['n']}" + (f" ({s['name']})" if s["name"] else "")
        for d in s["depends_on"]:
            dep = seen_ids.get(d)
            if dep is None:
                problems.append(f"{label} depends on {d!r}, which is not a step id")
            elif dep is s:
                problems.append(f"{label} depends on itself")
            elif dep["n"] >= s["n"]:
                problems.append(f"{label} depends on step {dep['n']} ({d}), which comes later: "
                                f"a step may only depend on earlier steps")
    if not problems and _has_cycle(steps):
        problems.append("the dependencies form a cycle")
    problems.extend(_mate_graph_problems(steps))
    return problems


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _optional_problems(s, label):
    """The optional v2 fields: never required, checked when present."""
    out = []
    for op in s["ops"]:
        if not any(op.lower() == w.lower() or op.lower().startswith(w.lower() + " ") for w in OP_VOCABULARY):
            out.append(f"{label}: op {op!r} must start with one of: {', '.join(OP_VOCABULARY)}")
    if "params" in s and s["params"] not in (None, {}):
        if not isinstance(s["params"], dict) or not all(isinstance(k, str) and k and (_number(v) or isinstance(v, str))
                                                        for k, v in s["params"].items()):
            out.append(f"{label}: `params` must map names to sizes in mm, e.g. {{\"shaft_d\": 8}}")
    if "targets" in s and s["targets"] not in (None, {}):
        t = s["targets"]
        if not isinstance(t, dict):
            out.append(f"{label}: `targets` must be an object (bbox_mm, tol_mm, holes, min_wall_mm)")
        else:
            bbox = t.get("bbox_mm")
            if bbox is not None and not (isinstance(bbox, list) and len(bbox) == 3
                                         and all(_number(x) and x > 0 for x in bbox)):
                out.append(f"{label}: targets.bbox_mm must be [x, y, z], each above 0 mm")
            for key, ok, want in (("tol_mm", lambda v: _number(v) and v >= 0, "a size of 0 mm or more"),
                                  ("holes", lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
                                   "a whole number"),
                                  ("min_wall_mm", lambda v: _number(v) and v > 0, "a size above 0 mm")):
                if key in t and t[key] is not None and not ok(t[key]):
                    out.append(f"{label}: targets.{key} must be {want}")
    if "ports" in s and s["ports"] not in (None, []):
        ports = s["ports"]
        names = []
        if not isinstance(ports, list):
            out.append(f"{label}: `ports` must be a list of {{\"name\", \"type\"}}")
        else:
            for p in ports:
                if not isinstance(p, dict) or not _str(p.get("name")):
                    out.append(f"{label}: each port needs a `name` (e.g. {{\"name\": \"mast_axis\", \"type\": \"axis\"}})")
                    continue
                if _str(p.get("type")) and _str(p.get("type")).lower() not in PORT_TYPES:
                    out.append(f"{label}: port {p['name']!r} type must be one of {', '.join(PORT_TYPES)}")
                if p["name"] in names:
                    out.append(f"{label}: port {p['name']!r} is named twice")
                names.append(p["name"])
    if "mates" in s and s["mates"] not in (None, []):
        mates = s["mates"]
        if s["kind"] != "assembly":
            out.append(f"{label}: only assembly steps carry `mates`")
        if not isinstance(mates, list):
            out.append(f"{label}: `mates` must be a list of {{\"type\", \"a\", \"b\"}}")
            return out
        fixed = 0
        for i, m in enumerate(mates, 1):
            if not isinstance(m, dict):
                out.append(f"{label}: mate {i} must be an object with type, a and b")
                continue
            kind = _str(m.get("type")).lower()
            if kind not in MATE_TYPES:
                out.append(f"{label}: mate {i} type {m.get('type')!r} must be one of {', '.join(MATE_TYPES)}")
                continue
            if not _str(m.get("a")):
                out.append(f"{label}: mate {i} ({kind}) has no `a`")
            if kind == "fixed":
                fixed += 1
                continue
            if not _str(m.get("b")):
                out.append(f"{label}: mate {i} ({kind}) has no `b`")
            elif _part(m.get("a")) == _part(m.get("b")):
                out.append(f"{label}: mate {i} ({kind}) joins {_part(m.get('a'))!r} to itself")
            if kind in VALUED_MATES and not _number(m.get("value")):
                out.append(f"{label}: mate {i} ({kind}) needs a numeric `value`"
                           + (" in mm" if kind == "distance" else " in degrees"))
        if fixed > 1:
            out.append(f"{label}: {fixed} fixed mates; exactly one part is fixed")
    return out


def _part(ref):
    """The part a mate reference names: "Base.mast_bore" -> "Base"."""
    return _str(ref).split(".", 1)[0]


def mates(plan):
    """Every mate of the plan's assembly steps, in step order, each with the `step` id it belongs to."""
    out = []
    for s in plan["steps"]:
        ms = s.get("mates")
        if not isinstance(ms, list):
            continue
        for m in ms:
            if isinstance(m, dict):
                out.append({**m, "type": _str(m.get("type")).lower(), "step": s["id"]})
    return out


def _mate_graph_problems(steps):
    """Across the plan: one grounded part, and the mates between parts form a tree (no loops)."""
    all_mates = mates({"steps": steps})
    if not all_mates:
        return []
    out = []
    grounded = sorted({_part(m.get("a")) for m in all_mates if m["type"] == "fixed" and _str(m.get("a"))})
    if not grounded:
        out.append("the assembly mates fix no part: add one {\"type\": \"fixed\", \"a\": \"<base part>\"}")
    elif len(grounded) > 1:
        out.append(f"the assembly fixes {len(grounded)} parts ({', '.join(grounded)}); exactly one part is fixed")
    root = {}

    def find(x):
        while root.get(x, x) != x:
            x = root[x]
        return x

    pairs = set()
    for m in all_mates:
        if m["type"] == "fixed" or m["type"] not in MATE_TYPES:
            continue
        a, b = _part(m.get("a")), _part(m.get("b"))
        if not a or not b or a == b:
            continue
        pair = tuple(sorted((a, b)))
        if pair in pairs:
            continue  # several mates between the same two parts are one joint
        pairs.add(pair)
        ra, rb = find(a), find(b)
        if ra == rb:
            out.append(f"the mate between {a} and {b} closes a loop: keep the mates an acyclic tree "
                       f"(each part mated to the part it attaches to)")
        else:
            root[ra] = rb
    return out


def check(plan, task_ids=None):
    """The normalised plan, or PlanError naming every problem."""
    problems = validate(plan, task_ids)
    if problems:
        raise PlanError(problems)
    p = normalise(plan)
    p.pop("_steps_not_a_list", None)
    return p


def standalone_problems(plan):
    """Describe plan kinds and operations the CADSwarm-owned feature worker cannot execute."""
    problems = []
    for step in plan.get("steps", []):
        label = f"step {step.get('n')}" + (f" ({step.get('name')})" if step.get("name") else "")
        kind = step.get("kind")
        if kind not in STANDALONE_PLAN_KINDS:
            problems.append(f"{label}: kind {kind!r} is unsupported by the standalone worker; use part or check")
        for op in step.get("ops", []):
            if not any(op.lower() == word.lower() or op.lower().startswith(word.lower() + " ")
                       for word in STANDALONE_OP_VOCABULARY):
                problems.append(f"{label}: operation {op!r} is unsupported by the standalone worker")
    return problems


# --------------------------------------------------------------- progress

def _status_map(tasks):
    """{task id: task row} from a list of task dicts (id, status, owner, assignee...)."""
    if isinstance(tasks, dict):
        return {k: (v if isinstance(v, dict) else {"id": k, "status": v}) for k, v in tasks.items()}
    return {t["id"]: t for t in tasks or []}


def step_status(step, tasks):
    t = _status_map(tasks).get(step.get("task"))
    return t["status"] if t else "missing"


def unfinished_deps(step, plan, tasks):
    """The dependency steps of `step` whose task is not done."""
    tm = _status_map(tasks)
    by_id = {s["id"]: s for s in plan["steps"]}
    out = []
    for d in step.get("depends_on", []):
        dep = by_id.get(d)
        status = (tm.get(dep["task"]) or {}).get("status", "missing") if dep else "missing"
        if status != "done":
            out.append({"id": d, "n": dep["n"] if dep else None, "name": dep["name"] if dep else d,
                        "status": status})
    return out


def is_ready(step, plan, tasks):
    """Every dependency's task is done (the step's own status is not considered)."""
    return not unfinished_deps(step, plan, tasks)


def ready_steps(plan, tasks):
    """Open steps whose dependencies are all done, lowest number first."""
    plan = plan if isinstance(plan, dict) and "steps" in plan else load(plan)
    tm = _status_map(tasks)
    return [s for s in plan["steps"]
            if (tm.get(s["task"]) or {}).get("status") == "open" and is_ready(s, plan, tm)]


def step_for_task(plan, task_id):
    plan = plan if isinstance(plan, dict) and "steps" in plan else load(plan)
    return next((s for s in plan["steps"] if s["task"] and s["task"] == task_id), None)


def _assignee(step, task):
    return _str(step.get("assignee")) or _str((task or {}).get("assignee"))


def next_step(plan, tasks, me, me_id=None, live=None, extra_tasks=()):
    """The caller's next step.

    - `{"step": <step>, "task": <task row>}`: a step the caller already holds (claimed by `me_id`), else a
      ready, open step assigned to `me`, else the lowest-numbered ready, open, unassigned one. A step pinned to
      a name not in `live` (when given) counts as unassigned, so a gone teammate cannot hold the build up.
      `extra_tasks` are open tasks of the brief outside the plan (a reviewer's fixes): always ready, after steps.
    - `{"wait": True, "blocked": [...]}` while work remains but none is ready for the caller.
    - `{"stuck": True, "blocked": [...]}` when open steps can never become ready (a dependency was stopped)
      and nothing is being worked on.
    - `{"done": True}` when nothing is left."""
    plan = plan if isinstance(plan, dict) and "steps" in plan else load(plan)
    tm = _status_map(tasks)
    me_l = _str(me).lower()
    live_l = None if live is None else {_str(x).lower() for x in live}

    def pinned_to(step, task):
        a = _assignee(step, task).lower()
        if not a or (live_l is not None and a not in live_l and a != me_l):
            return ""
        return a

    steps = list(plan["steps"])
    extra = [{"n": None, "id": t["id"], "name": t.get("title") or t["id"], "kind": "fix", "summary": "",
              "task": t["id"], "objects": list(t.get("objects") or []), "depends_on": [], "ops": [],
              "assignee": ""} for t in extra_tasks or () if t.get("status") in REMAINING]
    for t in extra_tasks or ():
        tm.setdefault(t["id"], t)
    candidates = steps + extra

    if me_id is not None:
        for s in candidates:
            t = tm.get(s["task"])
            if t and t.get("status") == "claimed" and t.get("owner") == me_id:
                return {"step": s, "task": t, "claimed": True}
    ready = [s for s in candidates if (tm.get(s["task"]) or {}).get("status") == "open"
             and (s["kind"] == "fix" or is_ready(s, plan, tm))]
    mine = [s for s in ready if pinned_to(s, tm.get(s["task"])) == me_l]
    free = [s for s in ready if not pinned_to(s, tm.get(s["task"]))]
    pick = (mine or free or [None])[0]
    if pick is not None:
        return {"step": pick, "task": tm.get(pick["task"])}

    remaining = [s for s in candidates if (tm.get(s["task"]) or {}).get("status") in REMAINING]
    if not remaining:
        return {"done": True}
    blocked = [{"n": s["n"], "id": s["id"], "name": s["name"], "status": tm[s["task"]]["status"],
                "waiting_for": [d["name"] for d in unfinished_deps(s, plan, tm)] if s["kind"] != "fix" else [],
                "assignee": _assignee(s, tm.get(s["task"])) or None}
               for s in remaining]
    progressing = any(tm[s["task"]]["status"] in ACTIVE + ("blocked",) for s in remaining) or any(
        (tm.get(s["task"]) or {}).get("status") == "open" and is_ready(s, plan, tm) for s in remaining)
    if not progressing and all(_dead_end(s, plan, tm) for s in remaining):
        return {"stuck": True, "blocked": blocked,
                "message": "These steps wait for steps that will never finish (stopped or missing): "
                           + ", ".join(f"{b['n']}. {b['name']}" for b in blocked)}
    return {"wait": True, "blocked": blocked}


def _dead_end(step, plan, tm, seen=None):
    """True if the step cannot become ready: some dependency is cancelled/missing, or itself a dead end."""
    seen = seen or set()
    if step["id"] in seen:
        return True
    seen = seen | {step["id"]}
    by_id = {s["id"]: s for s in plan["steps"]}
    for d in step.get("depends_on", []):
        dep = by_id.get(d)
        st = (tm.get(dep["task"]) or {}).get("status", "missing") if dep else "missing"
        if st == "done":
            continue
        if st in ("cancelled", "missing") or (st == "open" and _dead_end(dep, plan, tm, seen)):
            return True
    return False


def progress(plan, tasks):
    """`{"total", "done", "current": [names], "line": "N of M done · now: X", "steps": [...]}`.

    Each step carries its task's `status` (`missing` when the task row is gone), whether it is `ready`, and the
    names of the unfinished steps it is `waiting_for`."""
    plan = plan if isinstance(plan, dict) and "steps" in plan else load(plan)
    tm = _status_map(tasks)
    rows = []
    for s in plan["steps"]:
        t = tm.get(s["task"]) or {}
        status = t.get("status", "missing")
        waiting = unfinished_deps(s, plan, tm)
        rows.append({**{k: v for k, v in s.items() if not k.startswith("_")}, "status": status,
                     "ready": status == "open" and not waiting,
                     "waiting_for": [w["name"] for w in waiting] if status == "open" else [],
                     "owner": t.get("owner_name") or t.get("owner") or None,
                     "assignee": _assignee(s, t) or None})
    done = sum(1 for r in rows if r["status"] == "done")
    current = [r["name"] for r in rows if r["status"] in ACTIVE]
    return {"total": len(rows), "done": done, "current": current, "line": summary_line(done, len(rows), current),
            "steps": rows}


def summary_line(done, total, current):
    line = f"{done} of {total} done"
    if current:
        line += " · now: " + ", ".join(current)
    return line


# ---------------------------------------------------------------- op feed

# Op kinds the host runs, in the plan's op vocabulary.
KIND_LABELS = {"create_sketch": "Create sketch", "create_primitive": "Primitive",
               "create_extrusion": "Extrude", "extrude": "Extrude", "pad": "Extrude",
               "pocket": "Pocket", "revolve": "Revolve", "fillet": "Fillet", "chamfer": "Chamfer",
               "hole": "Hole", "pattern": "Pattern", "mirror": "Mirror", "shell": "Shell",
               "insert_component": "Import STEP/STL", "add_standard_part": "Place standard part",
               "boolean": "Boolean", "set_property": "Set dimensions", "set_props": "Set dimensions",
               "set_expression": "Set dimensions", "set_param": "Set dimensions",
               "set_placement": "Set placement", "constrain": "Mate", "mate": "Mate", "set_color": "Set color",
               "check_interference": "Check interference", "interference": "Check interference",
               "check_printability": "Check printability", "printability": "Check printability",
               "render": "Render", "save_part": "Save part", "export": "Export STEP/STL"}


def op_label(kind, args=None):
    """Map a model operation to the vocabulary used by standalone plan steps."""
    k = _str(kind).lower()
    a = args if isinstance(args, dict) else _args(args)
    if k == "create_sketch_feature":
        return {"extrude": "Extrude", "revolve": "Revolve", "pocket": "Pocket", "hole": "Hole",
                "fillet": "Fillet", "chamfer": "Chamfer", "linear_pattern": "Pattern",
                "circular_pattern": "Pattern"}.get(_str(a.get("kind")).lower(), "Feature")
    if k == "boolean":
        operation = _str(a.get("op") or a.get("kind")).lower()
        return "Cut" if operation in ("cut", "difference", "") else f"Boolean {operation or 'operation'}"
    return KIND_LABELS.get(k) or humanise(kind)


def _args(text):
    try:
        v = json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def humanise(kind):
    """`create_sketch` -> `Create sketch`."""
    words = _str(kind).replace("-", "_").split("_")
    text = " ".join(w for w in words if w)
    return text[:1].upper() + text[1:]


def op_targets(args):
    if isinstance(args, str):
        try:
            args = json.loads(args) if args else {}
        except ValueError:
            args = {}
    out = []
    for key in ("name", "base", "tool", "target", "object", "objects", "names", "targets"):
        v = (args or {}).get(key) if isinstance(args, dict) else None
        for x in ([v] if isinstance(v, str) else v if isinstance(v, (list, tuple)) else []):
            if isinstance(x, str) and x and x not in out:
                out.append(x)
    return out


def op_entry(op):
    started, finished = op.get("started_ms") or 0, op.get("finished_ms") or 0
    running = op.get("status") in ("queued", "running") or not finished
    return {"id": op.get("id"), "kind": op.get("kind"), "label": op_label(op.get("kind"), op.get("args_json") or op.get("args")),
            "targets": op_targets(op.get("args_json") or op.get("args")), "status": op.get("status"),
            "started_ms": started or None, "finished_ms": finished or None,
            "duration_s": None if running or not started else round((finished - started) / 1000.0, 2),
            "running": bool(running)}


def step_ops(step, ops, owner=None, start_ms=None, end_ms=None, limit=40):
    """The ops one step ran: by its task owner between claim and report, or naming one of its objects."""
    objects = set(step.get("objects") or [])
    out = []
    for op in sorted(ops or [], key=lambda o: o.get("started_ms") or o.get("created_ms") or 0):
        t = op.get("started_ms") or op.get("created_ms") or 0
        in_window = (owner is not None and op.get("by") == owner and start_ms is not None and t >= start_ms
                     and (end_ms is None or t <= end_ms))
        names = objects and objects.intersection(op_targets(op.get("args_json") or op.get("args")))
        if in_window or names:
            out.append(op_entry(op))
    return out[-limit:]
