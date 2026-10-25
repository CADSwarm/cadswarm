"""The Perch demo driver: one button, a real Act 2 build, no canned output.

    python demo/perch/driver.py --project . --timeline table.json

The driver is a human member of the team (kind "human", client "presenter"): it submits the same
briefs, steers and messages a person would, and it reads the same snapshot the panel reads. It never
writes geometry, never claims a task and never posts as a helper. Nothing here ships with FreeCAD,
the host or the module: the sequence is `table.json` (judging), `table-safe.json` (slow day) or
`timeline.json` (booth), and every step is done when the module says so, never when an agent says so.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cadswarm.agent_setup import redeem
from cadswarm.client import load_json, save_json, state_dir
from cadswarm.invite import read_invite
from cadswarm.space_hub import project_hub

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
    activity: list = field(default_factory=list)  # v_activity rows: recalls and refused submits
    run_started: float = 0.0                      # when Start was pressed: a recall from a prior run is not evidence


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
    # handoff, twenty-five minutes in, and must not block the start.
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


def _activity_seconds(row):
    return float(row.get("at_ms") or 0) / 1000.0


def _numbered(text, word):
    """`3 problems` / `1 problem` in an activity summary, or None when the row carries no count."""
    m = re.search(r"(\d+)\s+" + word + r"s?\b", str(text or ""), re.I)
    return int(m.group(1)) if m else None


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
    """The host's own recall makes no op: it logs a `recall` activity row (helper.recall_designs).
    Filter by the press, not this step's start: the host may recall while a step's deadline runs out.
    `query` (when a timeline sets it) keeps Act 1's planning recall from standing in for Act 2's."""
    marker = str(step.args["marker"]).lower()
    query = str(step.args.get("query") or "").lower()
    need = int(step.args.get("problems", 3))
    for row in view.activity:
        if row.get("kind") != "recall" or _activity_seconds(row) < view.run_started:
            continue
        summary = str(row.get("summary") or "")
        if marker not in summary.lower() or (query and query not in summary.lower()):
            continue
        problems, lessons = _numbered(summary, "problem"), _numbered(summary, "lesson")
        if problems is None or lessons is None:
            return False, f"the host's recall row has no counts: {summary}"
        if problems < need:
            return False, f"Act 1 returned with {problems} problems, need {need}"
        if not lessons:
            return False, "Act 1 returned, but no lessons came with it"
        return True, f"Act 1 first · {problems} problems · {lessons} lessons"
    return False, "waiting for the host's recall"


def tasks_landed(step, view):
    wanted = step.args["owners"]
    tasks = view.snapshot.get("tasks") or []
    found, why = {}, []
    for part, owner in wanted.items():
        # A task from the previous judge is still `open` in the same workspace: only this run's tasks
        # count, and `created_at` is in the same epoch seconds as the press.
        row = next((t for t in tasks if part.lower() in (t.get("title") or "").lower()
                    and (t.get("status") or "") in ("open", "claimed")
                    and float(t.get("created_at") or 0) >= view.run_started), None)
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
    """A steer is refused inside `submit_op`: no op row, no event, so SpaceHub logs a `refused` row.
    A queued op that later meets a lock still arrives as `op_failed`; read both."""
    target = str(step.args.get("target") or "").lower()
    who = str(step.args.get("by") or "").lower()
    names = _names(view.snapshot)
    for row in view.activity:
        if row.get("kind") != "refused" or _activity_seconds(row) < view.after:
            continue
        name = str(names.get(row.get("member")) or "").lower()
        summary = str(row.get("summary") or "")
        if who and name != who:
            continue
        if target and target not in summary.lower():
            continue
        return True, f"{name or 'a helper'} refused: {summary}"
    for e in view.events:
        if e.get("kind") != "op_failed" or e.get("code") not in ("lock_violation", "not_locked"):
            continue
        name = (e.get("name") or "").lower()
        error = (e.get("error") or "").lower()
        if who and name != who:
            continue
        if target and target not in error:
            continue
        return True, f"{e.get('name')} refused: {e.get('error')}"
    return False, f"waiting for a refusal from {who or 'any helper'}"


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
        # The tick thread and the threaded HTTP handlers both drive this runner. One lock keeps two
        # presses (or a press and a tick) from finishing the same step twice and skipping the next.
        # `state()` and the read-only properties stay lock-free: they only read plain attributes.
        self._lock = threading.Lock()

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
        with self._lock:
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
        # A task from the last judge is still open in the same workspace: Start would land it again.
        open_tasks = [t.get("title") or t.get("id") or "?"
                      for t in snap.get("tasks") or []
                      if (t.get("status") or "") in ("open", "claimed", "blocked")]
        if open_tasks:
            problems.append("tasks still open: " + ", ".join(open_tasks[:6]))
        # The reviewer is HTTP-only: `init-reviewer` mints it, then it never heartbeats, so the sweep marks
        # it stale by T-5 with no work at risk. Probe still shows it (and Droid on PATH); only a stale
        # *helper* can strand a part, so only helpers block Start.
        stale = [m.get("name") for m in model.get("participants") or []
                 if m.get("status") == "stale" and (m.get("kind") or "") != "reviewer"]
        if stale:
            problems.append("stale helpers: " + ", ".join(stale))
        self.reset_why = "; ".join(problems)
        self._reset = (not problems, self.reset_why)
        return not problems

    def start(self):
        with self._lock:
            if self.started_at is not None:
                raise RunnerError("this run has already started")
            if not self.probe_ok:
                why = " · ".join(f"{c.get('name')}: {c.get('detail')}" for c in self.checks if not c.get("ok"))
                raise RunnerError("Probe has not passed: " + (why or "run Probe first"))
            if not self.reset_ok():
                raise RunnerError("The team is not back at act-1-ready: " + self.reset_why)
            # Events from before the press are not this run's evidence: the between-judge reset never
            # prunes the hub's log, so a previous run's brief or op would satisfy this one's first steps.
            # `model.seq` is the hub's latest event seq (SpaceHub.latest_seq() reads v_event).
            self.seq = int((self.io.snapshot().get("model") or {}).get("seq") or 0)
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
        with self._lock:
            self.paused = not self.paused
            self._log("run", "paused" if self.paused else "resumed", "")
            return self.paused

    def skip(self):
        with self._lock:
            st = self.current
            if self.started_at is None or st is None:
                return
            kind = "confirmed" if st.step.predicate == "confirm" else "skipped"
            self._finish(st, kind, st.detail or kind)

    def say(self, text):
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            m = re.match(r"@([\w.-]+)\s+(.+)", text, re.S)
            if m:
                self.io.send_steer(m.group(1), m.group(2).strip())
                self._log("say", "steer", text)
            else:
                self.io.send_brief(text, self.plan["brief"].get("board") or "")
                self._log("say", "brief", text)

    # ---- the loop -----------------------------------------------------------
    def tick(self):
        with self._lock:
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
                        after=st.sent_at or st.started or self.started_at, now=now,
                        activity=self.io.activity(), run_started=self.started_at)

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
        # The numbers the pitch quotes come only from steps the module actually closed as done (or a
        # person confirmed); a skipped step still has a `t`, but it is the deadline, not a result.
        done = {s["id"]: s["t"] for s in state["steps"]
                if s["state"] in ("done", "confirmed") and s["t"] is not None}
        out = {
            "timeline": state["timeline"],
            "log": self.path.name,
            "t_recall": done.get("recall"),
            "t_tasks": done.get("tasks"),
            "t_first_op": done.get("first_part"),
            "t_finish": max(done.values()) if done else None,
            "gate_failed": state["gate"]["failed"],
            "steps": [{"id": s["id"], "state": s["state"], "t": s["t"]} for s in state["steps"]],
        }
        self.summary_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        return out


# --------------------------------------------------------------------------- the presenter

def write_presenter_config(project, ticket, name, identity):
    """The same two files `cadswarm setup` writes, for a client the CLI does not offer.

    `client = "presenter"` is stored on the member and sent on every rejoin, so the team can see that
    this human is the demo's presenter and not a helper. The folder's identity is a SpacetimeDB token:
    it stays under .cadswarm/, which the repo already ignores.
    """
    folder = state_dir(project)
    os.makedirs(folder, exist_ok=True)
    Path(folder, ".gitignore").write_text("*\n", encoding="utf-8")
    ident_path = os.path.join(folder, "identity.json")
    save_json(ident_path, identity)
    save_json(os.path.join(folder, "config.json"), {
        "uri": ticket["uri"].rstrip("/"), "db": ticket["db"], "name": name,
        "client": "presenter", "role": ticket.get("role") or "member"})
    return [ident_path, os.path.join(folder, "config.json")]


def _require_presenter_folder(cfg):
    """A folder set up for a helper is not the presenter's: reusing its identity would join as that
    helper, inflate a partner's card and make `helper_checks` believe a fake helper is live."""
    owner = (cfg or {}).get("client")
    if owner and owner != "presenter":
        raise RunnerError(
            f"this folder belongs to another client ({owner}), not the presenter: point --project at "
            f"the presenter folder, or pass a fresh --invite into a new folder")
    return cfg


def connect(project, invite="", name="presenter"):
    """Join once with an invite, then reuse this folder's identity — the same rule as `cadswarm brief`.

    A rejoin at start is what the MCP server does too: it takes a fresh fence so a crashed run cannot
    keep acting. Nothing here is special-cased in the module or the host.
    """
    if invite:
        ticket = read_invite(invite)
        # Refuse before `write_presenter_config`: another client's config is not ours to rewrite.
        _require_presenter_folder(load_json(os.path.join(state_dir(project), "config.json")))
        if not load_json(os.path.join(state_dir(project), "identity.json")):
            write_presenter_config(project, ticket, name, redeem(ticket, name, "presenter"))
    hub, cfg = project_hub(project)
    if not hub:
        raise SystemExit("driver: run this once with --invite '<cadswarm-2:...>' "
                         "(mint one on the host: cadswarm-host --data <dir> invite --kind human --role lead)")
    _require_presenter_folder(cfg)
    hub.join()
    return hub


class TeamIO:
    """The team as the driver sees it: the same reducers and the same reads the panel uses."""

    def __init__(self, hub, cast=None, probe=None):
        self.hub = hub
        self.cast = cast or {}
        self.probe_cast = {n: c for n, c in self.cast.items() if probe is None or n in probe}

    def snapshot(self):
        s = self.hub.panel_snapshot()
        return {"model": s["model"], "tasks": s["tasks"], "briefs": s["briefs"], "reviews": s["reviews"]}

    def ops(self):
        """The op rows the panel's views hold, for `op_done` and the gate. SpaceHub has no public
        ops() reader, so the demo reads `v_op` directly; recall and refusal evidence is in activity()."""
        rows = sorted(self.hub._rows("v_op"), key=lambda o: o.get("created_ms") or 0)[-60:]
        return [{"id": r.get("id"), "kind": r.get("kind"), "status": r.get("status"),
                 "by": r.get("by"), "finished_ms": r.get("finished_ms") or 0} for r in rows]

    def events(self, since):
        return self.hub.events(since, wait=0)

    def activity(self):
        """The team's activity rows. The host's own recall and a refused submit make no op or event,
        so this view is where the driver finds both; newest last, capped to what a run can use."""
        rows = sorted(self.hub._rows("v_activity"), key=lambda a: a.get("id") or 0)[-80:]
        return [{"id": r.get("id"), "member": r.get("member"), "kind": r.get("kind"),
                 "summary": r.get("summary"), "at_ms": r.get("at_ms") or 0} for r in rows]

    def send_brief(self, text, board):
        return self.hub.post("/briefs", {"text": text, "board": board or ""})

    def send_steer(self, to, body):
        # The module matches a bare name (`live_by_name`); an "@" here would address a member that
        # never joins and the steer would wait forever.
        return self.hub.send({"to": str(to).lstrip("@"), "kind": "steer", "body": body})

    def probe(self):
        snap = self.snapshot()
        return probe_checks(self.probe_cast, snap["model"].get("participants") or [], time.time(),
                            which=shutil.which, elastic=elastic_ready)


def helper_checks(cast, members, now, fresh_s=180):
    """Every owner the timeline names: joined as the right client, active, and heartbeating."""
    by_name = {str(m.get("name") or "").lower(): m for m in members}
    out = {}
    for name, client in cast.items():
        m = by_name.get(name.lower())
        if not m or (m.get("client") or "") != client:
            out[name] = {"name": name, "ok": False, "detail": f"not joined as a {client} helper"}
            continue
        why = []
        if m.get("status") != "active":
            why.append(f"status {m.get('status')}")
        if now - float(m.get("last_seen") or 0) > fresh_s:
            why.append(f"no heartbeat in {int(fresh_s)}s")
        out[name] = {"name": name, "ok": not why,
                     "detail": "; ".join(why) or f"{m.get('name')} · {client} · active"}
    return out


def elastic_ready():
    from cadswarm.knowledge import Elastic
    Elastic.from_env().search("cadswarm-designs", "perch", 1)
    return True


def probe_checks(cast, members, now, which=shutil.which, elastic=elastic_ready):
    """The four partners the window claims, then any owner those four do not already name."""
    helpers = helper_checks(cast, members, now)
    out = []
    for partner, client in (("Claude", "claude"), ("Creao", "creao")):
        mine = [helpers[n] for n, c in cast.items() if c == client]
        if not mine:
            continue
        bad = [c for c in mine if not c["ok"]]
        out.append({"name": partner, "ok": not bad,
                    "detail": "; ".join(f"{c['name']}: {c['detail']}" for c in bad) if bad
                              else ", ".join(c["name"] for c in mine) + " · active"})
    reviewer = next((m for m in members if (m.get("kind") or "") == "reviewer"), None)
    droid_cli = bool(which("droid"))
    detail = (f"{reviewer.get('name')} · {reviewer.get('status')}" if reviewer else "no reviewer has joined")
    out.append({"name": "Droid", "ok": bool(reviewer) and droid_cli,
                "detail": detail + ("" if droid_cli else " · droid CLI is not on PATH")})
    try:
        elastic()
        out.append({"name": "Elastic", "ok": True, "detail": "design memory reachable"})
    except Exception as e:
        out.append({"name": "Elastic", "ok": False, "detail": str(e)[:160]})
    for name, client in cast.items():
        if client not in ("claude", "creao"):
            out.append(helpers[name])
    return out


# --------------------------------------------------------------------------- the button page
# One screen the presenter can hit without aiming: Start, Next, Pause, Probe, the brief to copy, and a
# Say box that runs a judge's own idea. It polls /status; every word on it comes from Runner.state().

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
:root { color-scheme: dark; --bg:#0d1117; --panel:#161b22; --line:#30363d; --body:#e6edf3;
        --muted:#8b949e; --ok:#3fb950; --bad:#f85149; --wait:#d29922; --accent:#58a6ff; }
* { box-sizing: border-box; }
body { margin:0; padding:24px; background:var(--bg); color:var(--body);
       font:16px/1.45 ui-sans-serif, -apple-system, "Segoe UI", sans-serif; }
h1 { font-size:26px; margin:0 0 4px; }
.muted { color:var(--muted); }
#status { font-size:18px; margin:2px 0 14px; }
#clock { font:600 40px/1 ui-monospace, monospace; margin:10px 0 4px; }
#gate { color:var(--bad); font-weight:600; }
button { font:600 17px/1 inherit; color:#0d1117; background:var(--accent); border:0; border-radius:8px;
         padding:14px 22px; cursor:pointer; }
button.quiet { background:var(--panel); color:var(--body); border:1px solid var(--line); }
button:disabled { opacity:.45; cursor:not-allowed; }
.controls { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
ol#steps { list-style:none; padding:0; margin:18px 0; display:grid; gap:8px; }
ol#steps li { background:var(--panel); border:1px solid var(--line); border-left:6px solid var(--line);
              border-radius:8px; padding:10px 14px; }
li.waiting { border-left-color:var(--wait); } li.done, li.confirmed { border-left-color:var(--ok); }
li.skipped { border-left-color:var(--bad); }
.detail { color:var(--muted); font:13px/1.4 ui-monospace, monospace; }
fieldset { border:1px solid var(--line); border-radius:8px; padding:14px; margin:18px 0; }
legend { padding:0 6px; color:var(--muted); }
#brief { font:15px/1.5 ui-monospace, monospace; white-space:pre-wrap; margin:0 0 10px; }
#probe { display:grid; gap:4px; font:13px/1.4 ui-monospace, monospace; }
input[type=text] { width:min(560px, 90%); font:16px/1.4 ui-monospace, monospace; padding:10px 12px;
                   border-radius:8px; border:1px solid var(--line); background:var(--bg); color:var(--body); }
</style>
</head>
<body>
<h1>__TITLE__</h1>
<p class="muted">__NOTE__</p>
<p id="status">loading…</p>
<div id="clock">0:00</div>
<div class="controls">
  <button id="start">Start</button>
  <button id="next" class="quiet">Next</button>
  <button id="pause" class="quiet">Pause</button>
  <button id="probe" class="quiet">Probe</button>
</div>
<p id="gate" hidden></p>
<ol id="steps"></ol>
<fieldset><legend>__BRIEF_LEGEND__</legend>
  <pre id="brief">__BRIEF__</pre>
  <button id="copy" class="quiet">Copy the brief</button>
</fieldset>
<fieldset><legend>say it live (a judge's own idea)</legend>
  <form id="say"><input id="text" type="text" placeholder="@lid-creao thicken Podium_Body by 2 mm">
  <button>Send</button></form>
  <p class="muted">A line that starts with @name is a steer to that helper; anything else is a real brief.</p>
</fieldset>
<fieldset><legend>probe</legend><div id="probe"></div></fieldset>
<script>
const $ = s => document.querySelector(s);
const mmss = s => `${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,'0')}`;
function paint(s) {
  $('#status').textContent = s.status;
  $('#clock').textContent = mmss(s.clock || 0);
  $('#start').disabled = s.started || !s.probe_ok || !s.reset.ok;
  $('#next').disabled = !s.started || s.done;
  $('#pause').textContent = s.paused ? 'Resume' : 'Pause';
  $('#pause').disabled = !s.started;
  $('#gate').hidden = !s.gate.failed;
  $('#gate').textContent = s.gate.failed ? s.gate.if_missed : '';
  $('#steps').innerHTML = s.steps.map((st, i) => `<li class="${st.state}">
    <b>${i+1}. ${st.label}</b> <span class="muted">cue ${mmss(st.at)} · deadline ${st.deadline}s</span>
    <div class="detail">${st.state}${st.waited !== null ? ` · ${st.waited}s` : ''}${st.detail ? ` · ${st.detail}` : ''}</div>
    ${st.say ? `<div class="detail">${st.say}</div>` : ''}</li>`).join('');
  $('#probe').innerHTML = s.probe.map(c => `<div>${c.ok ? '✓' : '✗'} <b>${c.name}</b> — ${c.detail}</div>`).join('');
  $('#start').title = s.reset.ok ? '' : s.reset.why;
}
async function poll() { try { paint(await (await fetch('/status')).json()); } catch (e) {} }
async function post(path, body) {
  const r = await fetch(path, {method:'POST', headers:{'content-type':'application/json'},
                               body: JSON.stringify(body || {})});
  const j = await r.json();
  if (j.error) { $('#status').textContent = j.error; } else { paint(j); }
}
$('#start').onclick = () => post('/start');
$('#next').onclick = () => post('/next');
$('#pause').onclick = () => post('/pause');
$('#probe').onclick = () => post('/probe');
$('#copy').onclick = async () => { await navigator.clipboard.writeText($('#brief').textContent); };
$('#say').onsubmit = e => { e.preventDefault(); const t = $('#text').value;
  if (t.trim()) { post('/say', {text: t}); $('#text').value = ''; } };
setInterval(poll, 1000); poll();
</script>
</body>
</html>"""


def page(plan):
    legend = ("the brief to send from the phone" if (plan["brief"].get("from") or "") == "phone"
              else "the brief to type in the panel")
    return (PAGE.replace("__TITLE__", html.escape(f"The Perch · {plan['name']} run"))
                .replace("__NOTE__", html.escape(plan.get("note") or ""))
                .replace("__BRIEF_LEGEND__", html.escape(legend))
                .replace("__BRIEF__", html.escape(plan["brief"]["text"])))


# --------------------------------------------------------------------------- serving

def make_handler(runner):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _json(self, value, code=200):
            self._send(code, json.dumps(value), "application/json")

        def _body(self):
            n = int(self.headers.get("content-length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def do_GET(self):
            if self.path.startswith("/status"):
                self._json(runner.state())
            elif self.path in ("/", "/index.html"):
                self._send(200, page(runner.plan), "text/html; charset=utf-8")
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            body = self._body()
            try:
                if self.path == "/start":
                    runner.start()
                elif self.path == "/next":
                    runner.skip()
                elif self.path == "/pause":
                    runner.toggle_pause()
                elif self.path == "/probe":
                    runner.probe()
                elif self.path == "/say":
                    runner.say(body.get("text") or "")
                else:
                    return self._json({"error": "not found"}, 404)
            except RunnerError as e:
                return self._json({"error": str(e)}, 409)
            except Exception as e:
                # A dead hub, Elastic or socket is not the presenter's fault and must not drop the
                # connection: the page clears the Say box on submit, so a lost request loses the
                # judge's idea. Answer with the failure and keep the page talking.
                msg = f"{type(e).__name__}: {e}"[:400]
                runner._log("page", "error", f"{self.path}: {msg}")
                return self._json({"error": msg}, 502)
            self._json(runner.state())

        def log_message(self, *args):
            pass  # one page, one laptop: no access log on the console the presenter is reading

    return Handler


def serve(runner, port, open_browser=True, stop=None):
    stop = stop or threading.Event()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(runner))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"perch driver: {url}  ({runner.plan['name']})")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()


def _heartbeat(hub, every=10.0, stop=None):
    """The presenter is a member: without a heartbeat it goes stale and the panel says so.

    One blip on venue Wi-Fi must not make the presenter stale for the rest of the run: retry with a
    bounded backoff (1 s, 2 s, 4 s … 30 s) until a beat succeeds, and stop quietly once the server is
    shutting down. A transient failure is not worth a traceback on the console the presenter reads.
    """
    stop = stop or threading.Event()
    delay = 1.0
    while not stop.is_set():
        try:
            hub.heartbeat({"doing": "presenting"})
        except Exception:
            stop.wait(delay)
            delay = min(delay * 2, 30.0)
            continue
        delay = 1.0
        stop.wait(every)


def _loop(runner, every=2.0):
    while True:
        try:
            runner.tick()
        except Exception as e:  # a demo must never die in silence
            runner.status = f"driver error: {e}"
            runner._log("driver", "error", str(e)[:400])
        time.sleep(every)


# --------------------------------------------------------------------------- cli

def parse_args(argv=None):
    a = argparse.ArgumentParser(description="The Perch demo driver: one button, a real build.")
    a.add_argument("--project", default=".", help="the folder cadswarm setup wrote to (holds .cadswarm/)")
    a.add_argument("--timeline", default="table.json", help="table.json, table-safe.json or timeline.json")
    a.add_argument("--invite", default="", help="a cadswarm-2: invite for the presenter (first run only)")
    a.add_argument("--name", default="presenter", help="the name the team sees")
    a.add_argument("--port", type=int, default=8765)
    a.add_argument("--laptop", action="store_true", help="send the brief from this laptop (the tunnel is down)")
    a.add_argument("--no-browser", action="store_true")
    return a.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    project = Path(a.project).expanduser().resolve()
    hub = connect(project, a.invite, a.name)
    plan_path = Path(a.timeline)
    plan = load_plan(plan_path if plan_path.is_absolute() else HERE / plan_path)
    if a.laptop:
        plan["brief"]["from"] = "laptop"
    log = RunLog(HERE, plan["name"])
    runner = Runner(plan, TeamIO(hub, plan["cast"], plan["probe"]), log=log)
    runner.probe()
    stop = threading.Event()
    threading.Thread(target=_heartbeat, args=(hub,), kwargs={"stop": stop}, daemon=True).start()
    threading.Thread(target=_loop, args=(runner,), daemon=True).start()
    serve(runner, a.port, open_browser=not a.no_browser, stop=stop)


if __name__ == "__main__":
    main()
