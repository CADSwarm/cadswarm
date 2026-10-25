"""The Perch demo driver: one button, a real Act 2 build, no canned output.

    python demo/perch/driver.py --project . --timeline table.json

The driver is a human member of the team (kind "human", client "presenter"): it submits the same
briefs, steers and messages a person would, and it reads the same snapshot the panel reads. It never
writes geometry, never claims a task and never posts as a helper. Nothing here ships with FreeCAD,
the host or the module: the sequence is `table.json` (judging), `table-safe.json` (slow day) or
`timeline.json` (booth), and every step is done when the module says so, never when an agent says so.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent


class PlanError(Exception):
    """A timeline file the driver will not run."""


class RunnerError(Exception):
    """Start refused: a probe failed, or the team is not back at act-1-ready."""


KNOWN_STEP_KEYS = {"id", "label", "predicate", "at", "deadline", "say", "args", "on_start", "guard"}

# What each predicate needs in its step's `args`. A timeline that forgets one is refused before Start.
REQUIRED_ARGS = {
    "brief_sent": ["marker"],
    "recall": ["marker"],
    "tasks_landed": ["owners"],
    "refusal": [],
    "first_part": ["parts"],
    "parts_landed": ["parts"],
    "param_changed": ["alias", "value"],
    "op_done": ["kind"],
    "review_round": ["round"],
    "report": ["kind"],
    "takeover": [],
    "handoff": [],
    "confirm": [],
}


@dataclass
class Step:
    id: str
    label: str
    predicate: str
    at: int
    deadline: int
    say: str = ""
    args: dict = field(default_factory=dict)
    on_start: dict = field(default_factory=dict)
    guard: dict = field(default_factory=dict)


@dataclass
class StepState:
    step: Step
    state: str = "pending"          # pending | waiting | done | skipped | confirmed
    detail: str = ""
    started: float | None = None
    ended: float | None = None
    sent: bool = False
    sent_at: float | None = None
    events: list = field(default_factory=list)


@dataclass
class View:
    """What the module says, read once per tick. Times are epoch seconds, like the views."""
    snapshot: dict
    ops: list
    events: list          # since this step began
    run_events: list      # since the press
    seq: int
    started: float
    after: float          # when this step's own action went out (the mention), else `started`
    now: float


def load_plan(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(plan)
    return plan


def validate(plan):
    if not isinstance(plan, dict):
        raise PlanError("a timeline is a JSON object")
    if not plan.get("name"):
        raise PlanError("a timeline needs a name")
    brief = plan.get("brief") or {}
    if not isinstance(brief.get("text"), str) or not brief["text"].strip():
        raise PlanError("a timeline needs brief.text: the words that go to the team")
    gate = plan.get("gate") or {}
    if "first_op_by" not in gate or not gate.get("if_missed"):
        raise PlanError("a timeline needs gate.first_op_by (seconds) and gate.if_missed (the fallback line)")
    if not isinstance(plan.get("ready"), dict):
        raise PlanError("a timeline needs ready: parts_any, forbid_prefix, no_open_brief")
    if not isinstance(plan.get("cast"), dict) or not plan["cast"]:
        raise PlanError("a timeline needs cast: owner name -> client")
    # Who must be live before Start. Not every cast member: the booth's `base-claude` joins at the
    # handoff, twenty minutes in, and must not block the start.
    probe = plan.get("probe")
    if not isinstance(probe, list) or not probe:
        raise PlanError("a timeline needs probe: the owners that must be live before Start")
    for name in probe:
        if name not in plan["cast"]:
            raise PlanError(f"probe names {name!r}, which is not in cast")

    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PlanError("a timeline needs at least one step")
    seen, last_at = set(), -1
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise PlanError(f"step {i} is not an object")
        unknown = set(s) - KNOWN_STEP_KEYS
        if unknown:
            raise PlanError(f"step {s.get('id') or i}: unknown keys {sorted(unknown)}")
        for key in ("id", "label", "predicate", "at", "deadline"):
            if key not in s:
                raise PlanError(f"step {s.get('id') or i} has no {key}")
        if s["predicate"] not in PREDICATES:
            raise PlanError(f"step {s['id']}: unknown predicate {s['predicate']!r} "
                            f"(known: {', '.join(sorted(PREDICATES))})")
        if s["id"] in seen:
            raise PlanError(f"step {s['id']} appears twice")
        seen.add(s["id"])
        if int(s["at"]) < last_at:
            raise PlanError(f"step {s['id']} is out of order: cue {s['at']}s after {last_at}s")
        last_at = int(s["at"])
        if int(s["deadline"]) <= 0:
            raise PlanError(f"step {s['id']}: deadline must be seconds greater than zero")
        args = s.get("args") or {}
        if not isinstance(args, dict):
            raise PlanError(f"step {s['id']}: args must be an object")
        for key in REQUIRED_ARGS[s["predicate"]]:
            if key not in args:
                raise PlanError(f"step {s['id']}: predicate {s['predicate']} needs args.{key}")


def _names(snapshot):
    model = snapshot.get("model") or {}
    return {m.get("id"): m.get("name") for m in model.get("participants") or []}


def _holder(snapshot, obj):
    for o in (snapshot.get("model") or {}).get("objects") or []:
        if o.get("name") == obj:
            return o.get("locked_by")
    return None


def _op_seconds(op):
    return float(op.get("finished_ms") or 0) / 1000.0


# --------------------------------------------------------------------------- predicates
# Each returns (done, the sentence the page and the log show). No predicate reads an agent's prose.

def brief_sent(step, view):
    marker = str(step.args["marker"]).lower()
    for e in view.run_events:
        if e.get("kind") != "brief" or marker not in (e.get("text") or "").lower():
            continue
        who = _names(view.snapshot).get(e.get("actor")) or str(e.get("actor"))
        if (step.args.get("by_not") or "").lower() == who.lower():
            return False, f"the brief came from {who}, not from the phone"
        if step.args.get("phone"):
            client = next((m.get("client") for m in (view.snapshot.get("model") or {}).get("participants") or []
                           if m.get("id") == e.get("actor")), "")
            if client != "phone":
                return False, f"the brief came from {who} ({client or 'no client'}), not the phone"
        return True, f"brief from {who}"
    return False, "waiting for the brief"


def recall(step, view):
    marker = str(step.args["marker"]).lower()
    need = int(step.args.get("problems", 3))
    for op in view.ops:
        if op.get("kind") != "recall_designs" or op.get("status") != "done":
            continue
        if _op_seconds(op) < view.started:
            continue
        result = op.get("result") or {}
        for design in result.get("designs") or []:
            text = f"{design.get('request') or ''} {design.get('text') or ''}".lower()
            if marker not in text:
                continue
            problems = design.get("problems") or []
            lessons = result.get("lessons") or []
            if len(problems) < need:
                return False, f"Act 1 returned with {len(problems)} problems, need {need}"
            if not lessons:
                return False, "Act 1 returned, but no lessons came with it"
            return True, f"Act 1 first · {len(problems)} problems · {len(lessons)} lessons"
    return False, "waiting for recall_designs"


def tasks_landed(step, view):
    wanted = step.args["owners"]
    tasks = view.snapshot.get("tasks") or []
    found, why = {}, []
    for part, owner in wanted.items():
        row = next((t for t in tasks if part.lower() in (t.get("title") or "").lower()
                    and (t.get("status") or "") != "done"), None)
        if not row:
            why.append(f"no open task for {part}")
            continue
        assigned = (row.get("assignee") or "").lower()
        claimed = (row.get("owner_name") or "").lower()
        if assigned != owner.lower():
            why.append(f"{part} assigned to {row.get('assignee') or 'nobody'}")
        elif claimed and claimed != owner.lower():
            why.append(f"{part} claimed by {row.get('owner_name')}")
        else:
            found[part] = row
    if len(found) == len(wanted):
        return True, " · ".join(f"{part} → {owner}" for part, owner in wanted.items())
    return False, "; ".join(why)


def refusal(step, view):
    target = str(step.args.get("target") or "").lower()
    who = str(step.args.get("by") or "").lower()
    for e in view.events:
        if e.get("kind") != "op_failed" or e.get("code") != "lock_violation":
            continue
        name = (e.get("name") or "").lower()
        error = (e.get("error") or "").lower()
        if who and name != who:
            continue
        if target and target not in error:
            continue
        return True, f"{e.get('name')} refused: {e.get('error')}"
    return False, f"waiting for a lock refusal from {who or 'any helper'}"


def _ops_on_parts(run_events, parts):
    for e in run_events:
        if e.get("kind") != "op":
            continue
        touched = [o for o in (e.get("created") or []) + (e.get("modified") or [])
                   if any(str(o).lower().startswith(p.lower()) for p in parts)]
        if touched:
            return e, touched
    return None, []


def first_part(step, view):
    event, touched = _ops_on_parts(view.run_events, step.args["parts"])
    if event:
        return True, f"{event.get('op')} → {', '.join(touched)}"
    return False, "waiting for the first op on a part"


def parts_landed(step, view):
    names = [o.get("name") or "" for o in (view.snapshot.get("model") or {}).get("objects") or []]
    missing = [p for p in step.args["parts"] if not any(n.startswith(p) for n in names)]
    if missing:
        return False, "still missing " + ", ".join(missing)
    return True, "all parts are in the model"


def param_changed(step, view):
    params = (view.snapshot.get("model") or {}).get("params") or {}
    alias, want = step.args["alias"], float(step.args["value"])
    got = params.get(alias)
    if got is None:
        return False, f"no {alias} in Params"
    if abs(float(got) - want) > 0.01:
        return False, f"{alias} is {got}, not {want}"
    return True, f"{alias} = {got}"


def op_done(step, view):
    kind = step.args["kind"]
    for op in view.ops:
        if op.get("kind") == kind and op.get("status") == "done" and _op_seconds(op) >= view.started:
            return True, f"{kind} is done"
    return False, f"waiting for {kind}"


def review_round(step, view):
    want = int(step.args["round"])
    passed = bool(step.args.get("passed", False))
    for e in view.run_events:
        if e.get("kind") == "review" and int(e.get("round") or 0) >= want and bool(e.get("passed")) == passed:
            return True, f"round {e.get('round')} · {'approved' if passed else 'findings'}"
    return False, f"waiting for review round {want} · {'approved' if passed else 'findings'}"


def report(step, view):
    kind = step.args["kind"]
    for e in view.run_events:
        if e.get("kind") == kind:
            return True, str(e.get("summary") or e.get("headline") or e.get("folder")
                             or e.get("job_id") or kind)
    return False, f"waiting for {kind}"


def takeover(step, view):
    for e in view.run_events:
        if e.get("kind") == "taken_over":
            return True, f"{e.get('taker')} took {e.get('target')} · {len(e.get('locks') or [])} locks"
    return False, "waiting for a takeover"


def handoff(step, view):
    for e in view.run_events:
        if e.get("kind") == "handoff_accepted":
            return True, f"{e.get('taker')} took the lock from {e.get('from')}"
    return False, "waiting for the handoff to be accepted"


def confirm(step, view):
    return False, "a person confirms this one"


PREDICATES = {
    "brief_sent": brief_sent,
    "recall": recall,
    "tasks_landed": tasks_landed,
    "refusal": refusal,
    "first_part": first_part,
    "parts_landed": parts_landed,
    "param_changed": param_changed,
    "op_done": op_done,
    "review_round": review_round,
    "report": report,
    "takeover": takeover,
    "handoff": handoff,
    "confirm": confirm,
}


class Runner:
    """One step at a time, each one done when the module says so. Every wait has a deadline."""

    def __init__(self, plan, io, log=None, clock=time.time):
        self.plan = plan
        self.io = io
        self.log = log
        self.clock = clock
        self.started_at = None
        self.paused = False
        self.gate_failed = False
        self.seq = 0
        self.checks = []
        self.status = "ready — press Start"
        self.steps = [StepState(Step(**s)) for s in plan["steps"]]
        self.index = 0
        self.run_events = []
        self.reset_why = "not checked yet"
        self._reset = (False, "not checked yet")

    # ---- what the page reads ------------------------------------------------
    @property
    def current(self):
        return self.steps[self.index] if 0 <= self.index < len(self.steps) else None

    @property
    def done(self):
        return self.index >= len(self.steps)

    @property
    def probe_ok(self):
        return bool(self.checks) and all(c.get("ok") for c in self.checks)

    def state(self):
        now = self.clock()
        t = (now - self.started_at) if self.started_at is not None else 0.0
        return {
            "timeline": self.plan["name"],
            "note": self.plan.get("note") or "",
            "started": self.started_at is not None,
            "paused": self.paused,
            "done": self.done,
            "clock": round(t, 1),
            "gate": {"failed": self.gate_failed, "if_missed": self.plan["gate"]["if_missed"]},
            "probe": self.checks,
            "probe_ok": self.probe_ok,
            "reset": {"ok": self._reset[0], "why": self._reset[1]},
            "status": self.status,
            "brief": {"text": self.plan["brief"]["text"], "from": self.plan["brief"].get("from"),
                      "board": self.plan["brief"].get("board") or ""},
            "steps": [{"id": s.step.id, "label": s.step.label, "at": s.step.at,
                       "deadline": s.step.deadline, "state": s.state, "detail": s.detail,
                       "say": s.step.say,
                       "waited": round((s.ended or now) - s.started, 1) if s.started else None,
                       "t": round(s.ended - self.started_at, 1) if s.ended and self.started_at else None}
                      for s in self.steps],
            "log": str(self.log.path) if self.log else "",
        }

    # ---- the controls -------------------------------------------------------
    def probe(self):
        self.checks = [dict(c) for c in self.io.probe()]
        self._log("probe", "ok" if self.probe_ok else "failed",
                  " · ".join(f"{c.get('name')}: {c.get('detail')}" for c in self.checks))
        return self.checks

    def reset_ok(self):
        """act-1-ready: Act 1's parts are on the arm, nothing Act 2 is left over, no brief or helper is open."""
        snap = self.io.snapshot()
        model = snap.get("model") or {}
        names = [o.get("name") or "" for o in model.get("objects") or []]
        ready = self.plan.get("ready") or {}
        problems = []
        missing = [n for n in ready.get("parts_any") or [] if n not in names]
        if missing:
            problems.append("missing " + ", ".join(missing))
        left = [n for n in names if any(n.startswith(p) for p in ready.get("forbid_prefix") or [])]
        if left:
            problems.append("Act 2 is still in the model: " + ", ".join(left))
        if ready.get("no_open_brief"):
            open_ids = [b.get("id") or "?" for b in snap.get("briefs") or []
                        if (b.get("status") or "") in ("open", "planning", "working")]
            if open_ids:
                problems.append("brief still open: " + ", ".join(open_ids))
        stale = [m.get("name") for m in model.get("participants") or [] if m.get("status") == "stale"]
        if stale:
            problems.append("stale helpers: " + ", ".join(stale))
        self.reset_why = "; ".join(problems)
        self._reset = (not problems, self.reset_why)
        return not problems

    def start(self):
        if self.started_at is not None:
            raise RunnerError("this run has already started")
        if not self.probe_ok:
            why = " · ".join(f"{c.get('name')}: {c.get('detail')}" for c in self.checks if not c.get("ok"))
            raise RunnerError("Probe has not passed: " + (why or "run Probe first"))
        if not self.reset_ok():
            raise RunnerError("The team is not back at act-1-ready: " + self.reset_why)
        self.started_at = self.clock()
        self._log("run", "start", self.plan["name"])
        self._begin(self.current)
        if (self.plan["brief"].get("from") or "") == "laptop":
            self.io.send_brief(self.plan["brief"]["text"], self.plan["brief"].get("board") or "")
            self._log("run", "brief", "the tunnel is down: the driver sent the brief from the laptop")
            first = self.current
            if first is not None and first.step.predicate == "brief_sent":
                self._finish(first, "done", "the driver sent it from the laptop; the tunnel is down")

    def toggle_pause(self):
        self.paused = not self.paused
        self._log("run", "paused" if self.paused else "resumed", "")
        return self.paused

    def skip(self):
        st = self.current
        if self.started_at is None or st is None:
            return
        kind = "confirmed" if st.step.predicate == "confirm" else "skipped"
        self._finish(st, kind, st.detail or kind)

    def say(self, text):
        text = (text or "").strip()
        if not text:
            return
        m = re.match(r"@([\w.-]+)\s+(.+)", text, re.S)
        if m:
            self.io.send_steer(m.group(1), m.group(2).strip())
            self._log("say", "steer", text)
        else:
            self.io.send_brief(text, self.plan["brief"].get("board") or "")
            self._log("say", "brief", text)

    # ---- the loop -----------------------------------------------------------
    def tick(self):
        if self.started_at is None:
            self.reset_ok()
            return
        now = self.clock()
        # Poll even while paused: the build keeps going, and a part that started during a pause is
        # evidence the gate must not ignore.
        fresh = self.io.events(self.seq)
        self.seq = int(fresh.get("seq") or self.seq)
        self.run_events.extend(fresh.get("events") or [])
        st = self.current
        if st is not None:
            st.events.extend(fresh.get("events") or [])
        if self.paused:
            return
        if (not self.gate_failed and now - self.started_at > float(self.plan["gate"]["first_op_by"])
                and not self._part_started()):
            self.gate_failed = True
            self.status = self.plan["gate"]["if_missed"]
            self._log("gate", "failed", self.plan["gate"]["if_missed"])
        if st is None:
            return

        view = View(snapshot=self.io.snapshot(), ops=self.io.ops(), events=st.events,
                    run_events=self.run_events, seq=self.seq, started=st.started or self.started_at,
                    after=st.sent_at or st.started or self.started_at, now=now)

        guard = (st.step.guard or {}).get("held_by")
        if guard and not st.sent:
            holder = _holder(view.snapshot, guard["object"])
            if (holder or "").lower() != (guard.get("name") or "").lower():
                st.detail = f"{guard['object']} is held by {holder or 'nobody'}"
                if now - (st.started or now) > st.step.deadline:
                    self._finish(st, "skipped", st.detail + f" (no result in {st.step.deadline}s)")
                return
            self._fire(st)
            st.detail = "the mention is out"

        ok, detail = PREDICATES[st.step.predicate](st.step, view)
        st.detail = detail
        if ok:
            self._finish(st, "done", detail)
            return
        if now - (st.started or now) > st.step.deadline:
            self._finish(st, "skipped", f"{detail} (no result in {st.step.deadline}s)")

    def _part_started(self):
        """The gate's own question: has any op named one of the parts this timeline says must start?"""
        parts = [p for s in self.steps if s.step.predicate == "first_part"
                 for p in (s.step.args.get("parts") or [])]
        if not parts:  # the booth's equivalent claim is `parts_landed`
            parts = [p for s in self.steps if s.step.predicate == "parts_landed"
                     for p in (s.step.args.get("parts") or [])]
        for e in self.run_events:
            if e.get("kind") != "op":
                continue
            names = (e.get("created") or []) + (e.get("modified") or [])
            if any(str(n).lower().startswith(p.lower()) for n in names for p in parts):
                return True
        return any(s.step.predicate in ("first_part", "parts_landed") and s.state == "done"
                   for s in self.steps)

    def _begin(self, st):
        if st is None:
            return
        st.state = "waiting"
        st.started = self.clock()
        st.sent = False
        st.sent_at = None
        self._log("step", "start", f"{st.step.id}: {st.step.label}")
        self.status = f"{st.step.id}: waiting"
        if not (st.step.guard or {}).get("held_by"):
            self._fire(st)

    def _fire(self, st):
        st.sent = True
        st.sent_at = self.clock()
        action = st.step.on_start or {}
        if "steer" in action:
            a = action["steer"]
            self.io.send_steer(a["to"], a["body"])
            self._log("step", "send", f"@{a['to']}: {a['body']}")
        elif "brief" in action:
            self.io.send_brief(action["brief"], self.plan["brief"].get("board") or "")
            self._log("step", "send", action["brief"])

    def _finish(self, st, kind, detail):
        st.state = kind
        st.detail = detail
        st.ended = self.clock()
        self._log("step", kind, f"{st.step.id}: {detail}")
        self.status = f"{st.step.id} {kind}: {detail}"
        self.index += 1
        if self.log:
            self.log.summary(self.state())
        if self.done:
            self._log("run", "done", "every step is finished; the build keeps running")
            return
        self._begin(self.current)

    def _log(self, kind, event, detail):
        if not self.log:
            return
        at = (self.clock() - self.started_at) if self.started_at is not None else 0.0
        self.log.write(at, f"{kind}.{event}", detail)


class RunLog:
    """Every run's rows, plus the summary the pitch quotes. One file per run, beside the timelines."""

    def __init__(self, folder, name):
        stamp = time.strftime("%Y-%m-%dT%H-%M-%S")
        self.path = Path(folder) / "runs" / f"{stamp}-{name}.jsonl"
        self.summary_path = self.path.with_suffix(".json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rows = []

    def write(self, at, kind, detail):
        row = {"at": round(at, 1), "wall": time.strftime("%H:%M:%S"), "kind": kind, "detail": detail}
        self.rows.append(row)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def summary(self, state):
        t = {s["id"]: s["t"] for s in state["steps"] if s["t"] is not None}
        ended = [v for v in t.values() if v is not None]
        out = {
            "timeline": state["timeline"],
            "log": self.path.name,
            "t_recall": t.get("recall"),
            "t_tasks": t.get("tasks"),
            "t_first_op": t.get("first_part"),
            "t_finish": max(ended) if ended else None,
            "gate_failed": state["gate"]["failed"],
            "steps": [{"id": s["id"], "state": s["state"], "t": s["t"]} for s in state["steps"]],
        }
        self.summary_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        return out
