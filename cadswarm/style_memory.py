"""Team design style: CADSwarm learns how this team uses FreeCAD and its agents, and teaches the agents.

The loop (observe -> distil -> gate -> recall -> observe again):

- observe: the host reports every finished op (who did it, human or agent, what it changed) and every
  finished brief (plan, review findings, final Params). A person changing what an agent made is recorded
  as a `correction`, the strongest signal of all.
- distil: plain statistics over those events become rules: the values the team uses for a Param, the
  corrections people keep making to agent work, preferred standard parts, modelling habits (Params
  expressions vs hard-coded numbers) and problems reviews keep finding. A rule's id is a hash of what it is
  about, so re-distilling updates it in place.
- gate: a rule with broad, consistent, recent evidence is `active` and goes into prompts; a weaker one is
  a `candidate` for a person to confirm or dismiss. Confirmed rules are always used and never demoted;
  dismissed ones come back only once their evidence has doubled. People can also teach a rule directly.
- recall: `context()` gives planners, workers, the reviewer and every agent turn the rules that apply.

SQLite in the project database is the source of truth: learning needs no account, keys or network.

Stdlib only.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter, defaultdict

from .knowledge import design_problems

SCHEMA = """
CREATE TABLE IF NOT EXISTS style_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor_id INTEGER, actor_name TEXT,
  actor_kind TEXT, kind TEXT NOT NULL, op_kind TEXT, ok INTEGER, error_code TEXT, brief TEXT,
  board TEXT, data TEXT NOT NULL, mirrored INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS style_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS style_rules (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL, statement TEXT NOT NULL,
  template TEXT NOT NULL, value TEXT, evidence TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL,
  source TEXT NOT NULL, resolved_n REAL DEFAULT 0, resolved_by TEXT, created_at REAL, updated_at REAL,
  seen_at REAL, mirrored INTEGER DEFAULT 0);
"""
STATUSES = ("candidate", "active", "confirmed", "dismissed", "expired")
# Ops that change the model; queries never teach anything about style.
MUTATING = {"create_primitive", "create_extrusion", "boolean", "set_property", "set_props", "set_expression",
            "set_placement", "delete", "set_param", "run_part_script", "insert_component",
            "insert_standard_part"}
CREATES = {"create_primitive", "create_extrusion", "boolean", "run_part_script", "insert_component",
           "insert_standard_part"}
EDITS = {"set_property", "set_props", "set_expression", "set_placement", "delete"}
# What the reviewer records when it never reported: about the reviewer, not about the design.
# Shared with design memory so a design never learns them as a lesson (see cadswarm.knowledge).

DISTIL_EVERY = 25          # new events between automatic re-distils
ACTIVE_CONFIDENCE = 0.7
MIN_OBSERVATIONS = 5
HALF_LIFE_DAYS = 30.0
WINDOW_EVENTS = 5000       # distil looks at this many most recent events
CANDIDATE_TTL_DAYS = 14.0  # a candidate nobody answers and nothing supports again leaves the inbox
# The triage inbox shows one decision per subject, under the kind of thing learned.
SECTION_LABELS = {"param_convention": "Shared sizes", "correction_pattern": "Corrections",
                  "preferred_component": "Preferred parts", "modelling_practice": "Habits",
                  "recurring_problem": "Review problems", "proposed": "Helper proposals",
                  "taught": "Your rules"}
SECTION_ORDER = tuple(SECTION_LABELS)
RECOMMEND_ORDER = {"confirm": 0, "look": 1, "dismiss": 2}


class StyleError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def setting_params():
    """Params that mirror team settings (tol_clearance, min_wall...): never learned as style."""
    try:
        from cadswarm import settings
        return {x["key"] for x in settings.specs() if x.get("param")}
    except (OSError, ValueError, KeyError):
        return set()


def rule_id(type_, scope, key):
    return "r_" + hashlib.sha1(f"{type_}|{scope}|{key}".encode()).hexdigest()[:12]


def number(value):
    """The number in a Param value like "40 mm" or 2.4, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(m.group(0)) if m else None


def unit(value):
    """The unit after the number in a Param value ("40 mm" -> "mm"), or ""."""
    m = re.search(r"-?\d+(?:\.\d+)?\s*([a-zA-Z°]+)", str(value or ""))
    return m.group(1) if m else ""


def fmt(x):
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def normalize_problem(text):
    """Reworded findings about the same thing group together: lowercase, numbers and object names out."""
    t = str(text or "").lower()
    t = re.sub(r"-?\d+(?:\.\d+)?\s*(mm|mm\^?3|°|deg)?", "#", t)
    t = re.sub(r"\b[a-z]+_?\d+\b", "#", t)          # Standoff_3, hole2
    t = re.sub(r"[^a-z#\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def confidence(n, consistency, people, occasions, age_days):
    """0..1: enough observations, mostly agreeing, from more than one person or occasion, and recent."""
    volume = 1 - math.exp(-n / 4.0)
    spread = min(1.0, (people + occasions) / 3.0)
    decay = 0.5 ** (max(0.0, age_days) / HALF_LIFE_DAYS)
    return round(consistency * volume * spread * decay, 3)


def gate(conf, n, people, occasions):
    ok = conf >= ACTIVE_CONFIDENCE and n >= MIN_OBSERVATIONS and (people >= 2 or occasions >= 2)
    return "active" if ok else "candidate"


def recommend(kind, evidence, confidence, source=""):
    """What a person will probably decide: `confirm`, `dismiss` or `look`.

    A suggestion, never an auto-apply. It is a pure function of the evidence and the kind of rule,
    so a model-based classifier can replace it later without touching the inbox or its API."""
    if source == "proposed" or kind in ("proposed", "taught"):
        return "look"  # a person's wording or a helper's proposal always needs a person
    evidence = evidence or {}
    n = float(evidence.get("n") or 0)
    if kind == "correction_pattern":
        return "confirm" if n >= 4 else "look"  # corrections count double: two is a pattern
    if kind == "recurring_problem":
        if n >= 4:
            return "confirm"  # seen in two designs, not a one-off
        return "dismiss" if n <= 2 else "look"
    if kind == "param_convention":
        if confidence >= 0.45 and n >= 5:
            return "confirm"
        return "dismiss" if n <= 1 and confidence < 0.25 else "look"
    if kind == "preferred_component":
        share = evidence.get("consistency")
        if n >= 5 and (share is None or share >= 0.6):
            return "confirm"
        return "dismiss" if n <= 1 else "look"
    if kind == "modelling_practice":
        if confidence >= 0.5 and n >= 5:
            return "confirm"
        return "dismiss" if n <= 1 else "look"
    return "look"


def subject_of(rule):
    """The one thing a rule is about; rules with the same subject are one decision.

    A size convention and the correction pattern for the same Param are two views of one fact,
    so they share `param:<alias>` and a person answers them together."""
    kind = rule["type"]
    key = rule.get("key") or ""
    if rule.get("source") == "proposed":
        return f"proposal:{rule['id']}"
    if rule.get("source") == "taught":
        return f"taught:{rule['id']}"
    if kind == "param_convention":
        return f"param:{key}"
    if kind == "correction_pattern":
        head, _, tail = key.partition(":")
        return f"param:{tail}" if head == "param" else f"edit:{tail or key}"
    if kind == "preferred_component":
        return f"part:{key}"
    if kind == "modelling_practice":
        return f"habit:{key}"
    if kind == "recurring_problem":
        return f"problem:{rule.get('scope') or 'team'}:{key}"
    return f"{kind}:{rule['id']}"


def section_of(subject):
    """Which kind section a subject belongs to: a shared size even when its correction is stronger."""
    for prefix, kind in (("param:", "param_convention"), ("edit:", "correction_pattern"),
                         ("part:", "preferred_component"), ("habit:", "modelling_practice"),
                         ("problem:", "recurring_problem"), ("proposal:", "proposed"), ("taught:", "taught")):
        if subject.startswith(prefix):
            return kind
    return "taught"


def evidence_detail(rule):
    """One line of evidence under a decision, in the same words the dialog already uses."""
    ev = rule.get("evidence") or {}
    if rule.get("source") in ("taught", "proposed"):
        who = ev.get("taught_by") or "someone"
        return f"taught by {who}" if rule["source"] == "taught" else f"proposed by {who}"
    bits = []
    if isinstance(ev.get("n"), (int, float)):
        bits.append(f"seen {ev['n']:g}×")
    if ev.get("people"):
        bits.append(f"{ev['people']} {'person' if ev['people'] == 1 else 'people'}")
    bits.append(f"confidence {rule.get('confidence', 0):.0%}")
    return ", ".join(bits)


def _group_status(rules):
    statuses = {r["status"] for r in rules}
    for status in ("confirmed", "active", "expired", "dismissed"):
        if status in statuses:
            return status
    return "candidate"


def _group(rules):
    """Collapse rules into decisions: strongest statement first, the rest as `also`."""
    by_subject = {}
    for r in rules:
        by_subject.setdefault(subject_of(r), []).append(r)
    out = []
    for subject, rs in by_subject.items():
        rs.sort(key=lambda r: (r["confidence"], r["updated_at"] or 0), reverse=True)
        top = rs[0]
        kind = section_of(subject)
        # Each rule is judged by its own kind (a correction and a convention need different
        # evidence); the group takes the strongest recommendation.
        choices = [recommend(r["type"], r["evidence"], r["confidence"], r["source"]) for r in rs]
        recommendation = "confirm" if "confirm" in choices else (
            "dismiss" if all(c == "dismiss" for c in choices) else "look")
        out.append({"subject": subject, "kind": kind, "title": top["statement"],
                    "detail": evidence_detail(top), "also": [r["statement"] for r in rs[1:]],
                    "ids": [r["id"] for r in rs], "confidence": top["confidence"],
                    "recommendation": recommendation, "source": top["source"],
                    "status": _group_status(rs),
                    "seen_at": max((r["seen_at"] or 0) for r in rs)})
    return out


def _numbers_in(text):
    return {float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(text))}


class StyleMemory:
    def __init__(self, db, clock=time.time):
        self.db, self.clock = db, clock
        self.lock = threading.RLock()
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT OR IGNORE INTO style_meta (key, value) VALUES ('project', ?)", (uuid.uuid4().hex[:12],))
        self.project = self.db.execute("SELECT value FROM style_meta WHERE key='project'").fetchone()[0]
        self._since_distil = 0

    # ---------------------------------------------------------------- observe

    def _add(self, kind, actor, data, op_kind=None, ok=True, error_code="", brief="", board=""):
        actor = actor or {}
        with self.lock:
            self.db.execute(
                "INSERT INTO style_events (at, actor_id, actor_name, actor_kind, kind, op_kind, ok, error_code, "
                "brief, board, data) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self.clock(), actor.get("id"), actor.get("name") or "", actor.get("kind") or "", kind, op_kind,
                 1 if ok else 0, error_code or "", brief or "", board or "", json.dumps(data)))
            self._since_distil += 1

    def _last_param_set(self, alias):
        row = self.db.execute(
            "SELECT actor_kind, actor_name, data FROM style_events WHERE kind='op' AND op_kind='set_param' AND ok=1 "
            "AND json_extract(data, '$.alias')=? ORDER BY id DESC LIMIT 1", (alias,)).fetchone()
        return row

    def observe_op(self, a):
        """One finished op: {kind, args, ok, error_code, actor{id,name,kind}, objects[{name,type_id,bbox,
        volume,expressions}], prior_actors{name: {id, name, kind}}, brief?}. Returns what was recorded."""
        kind = str(a.get("kind") or "")
        if kind not in MUTATING:
            return {"recorded": 0}
        actor = a.get("actor") or {}
        args = {k: v for k, v in (a.get("args") or {}).items() if not k.startswith("_")}
        objects = a.get("objects") or []
        ok = bool(a.get("ok", True))
        human = actor.get("kind") == "human"
        recorded = 0
        if not ok:
            self._add("op_failed", actor, {"args": _trim(args)}, kind, False, a.get("error_code"), a.get("brief"))
            return {"recorded": 1}
        data = {"args": _trim(args), "objects": [_obj(o) for o in objects]}
        if kind == "set_param":
            alias = str(args.get("alias") or "")
            data.update(alias=alias, num=number(args.get("value")), unit=unit(args.get("value")))
            prev = self._last_param_set(alias)
            if human and prev is not None and prev["actor_kind"] == "agent":
                before = json.loads(prev["data"]).get("num")
                if before is not None and data["num"] is not None and before != data["num"]:
                    self._add("correction", actor, {"target": f"Params.{alias}", "alias": alias,
                                                    "before": before, "after": data["num"],
                                                    "agent": prev["actor_name"]}, kind, brief=a.get("brief"))
                    recorded += 1
        elif human and kind in EDITS:
            prior = (a.get("prior_actors") or {}).get(str(args.get("name") or "")) or {}
            if prior.get("kind") == "agent":
                obj = next((o for o in objects if o.get("name") == args.get("name")), {})
                self._add("correction", actor, {"target": args.get("name"), "type_id": obj.get("type_id", ""),
                                                "edit": kind, "props": _trim(args.get("props") or {}),
                                                "expressions": _trim(args.get("expressions") or {}),
                                                "agent": prior.get("name") or ""}, kind, brief=a.get("brief"))
                recorded += 1
        self._add("op", actor, data, kind, True, "", a.get("brief"))
        return {"recorded": recorded + 1}

    def observe_brief(self, a):
        """A finished brief: {brief{id,text,board}, plan{parts}, reviews[{round,passed,findings_json}], params}."""
        brief = a.get("brief") or {}
        problems = design_problems(a.get("reviews") or [])
        parts = [p.get("name", "") for p in (a.get("plan") or {}).get("parts", []) if isinstance(p, dict)]
        self._add("brief_done", {"name": "host", "kind": "host"},
                  {"text": brief.get("text", ""), "parts": parts, "problems": problems,
                   "params": a.get("params") or {}}, brief=brief.get("id"), board=brief.get("board"))
        return {"recorded": 1, "problems": len(problems)}

    def due(self):
        return self._since_distil >= DISTIL_EVERY

    # ----------------------------------------------------------------- distil

    def _events(self):
        rows = self.db.execute("SELECT * FROM style_events ORDER BY id DESC LIMIT ?", (WINDOW_EVENTS,)).fetchall()
        return [dict(r, data=json.loads(r["data"])) for r in reversed(rows)]

    def candidates(self, events=None):
        """Every rule the events support right now (not yet gated against stored status)."""
        events = self._events() if events is None else events
        now = self.clock()
        out = []

        def emit(type_, scope, key, template, value, obs, consistency, n=None):
            n = len(obs) if n is None else n
            people = len({o["actor_name"] for o in obs if o["actor_kind"] == "human"})
            # Separate occasions: different briefs, or different days for ops outside a brief.
            occasions = len({o["brief"] or time.strftime("%Y-%m-%d", time.gmtime(o["at"])) for o in obs})
            last = max(o["at"] for o in obs)
            conf = confidence(n, consistency, people, occasions, (now - last) / 86400)
            out.append({"id": rule_id(type_, scope, key), "type": type_, "scope": scope, "key": key,
                        "template": template, "value": value, "confidence": conf, "seen_at": last,
                        "evidence": {"n": round(n, 2), "people": people, "occasions": occasions,
                                     "consistency": round(consistency, 2),
                                     "humans": sum(1 for o in obs if o["actor_kind"] == "human"),
                                     "agents": sum(1 for o in obs if o["actor_kind"] == "agent")},
                        "status": gate(conf, n, people, occasions)})

        ops = [e for e in events if e["kind"] == "op"]
        corrections = [e for e in events if e["kind"] == "correction"]

        # Param conventions: the values people and agents settle on. Host-seeded Params are board
        # measurements, not style; values people corrected away from are not the convention either.
        corrected_away = {(e["data"]["alias"], round(e["data"]["before"], 3)) for e in corrections
                          if e["data"].get("alias") and e["data"].get("before") is not None}
        by_alias = defaultdict(list)
        for e in ops:
            if e["data"].get("alias") in setting_params():
                continue  # a team setting mirrored into Params: Settings already governs it
            if e["op_kind"] == "set_param" and e["actor_kind"] in ("human", "agent") and e["data"].get("num") is not None:
                if e["actor_kind"] == "agent" and (e["data"]["alias"], round(e["data"]["num"], 3)) in corrected_away:
                    continue  # people replaced this value; it is a mistake, not the convention
                by_alias[e["data"]["alias"]].append(e)
        for alias, obs in by_alias.items():
            weights = Counter()
            for o in obs:
                weights[round(o["data"]["num"], 3)] += 2 if o["actor_kind"] == "human" else 1
            value, w = weights.most_common(1)[0]
            consistency = w / sum(weights.values())
            agreeing = [o for o in obs if round(o["data"]["num"], 3) == value]
            u = next((o["data"].get("unit") for o in reversed(agreeing) if o["data"].get("unit")), "")
            emit("param_convention", "team", alias,
                 f"Params.{alias} is {fmt(value)}{' ' + u if u else ''} on this team "
                 f"(seen {len(agreeing)} of {len(obs)} times).",
                 {"alias": alias, "value": value, "unit": u}, agreeing, consistency, n=len(agreeing))

        # Corrections: people redo what agents did. Each counts double.
        by_target = defaultdict(list)
        for e in corrections:
            d = e["data"]
            key = f"param:{d['alias']}" if d.get("alias") else f"{d.get('edit')}:{d.get('type_id') or 'object'}"
            by_target[key].append(e)
        for key, obs in by_target.items():
            d0 = obs[-1]["data"]
            if d0.get("alias"):
                afters = Counter(round(o["data"]["after"], 3) for o in obs)
                after, c = afters.most_common(1)[0]
                befores = sorted({fmt(o["data"]["before"]) for o in obs})
                template = (f"People change Params.{d0['alias']} from what agents set ({', '.join(befores)}) "
                            f"to {fmt(after)}: use {fmt(after)}.")
                value, consistency = {"alias": d0["alias"], "value": after}, c / len(obs)
            else:
                edit, type_id = key.split(":", 1)
                props = Counter(p for o in obs for p in (o["data"].get("props") or {}))
                # What people set each property to, most often: that is the rule ("Height to 3").
                values = {p: Counter(json.dumps(o["data"]["props"][p], default=str) for o in obs
                                     if p in (o["data"].get("props") or {})).most_common(1)[0][0]
                          for p in props}
                what = ", ".join(f"{p} to {values[p].strip(chr(34))}" for p, _ in props.most_common(3))
                if edit == "delete":
                    template = f"People often delete {type_id} objects agents made ({len(obs)} times): ask before adding one."
                elif what:
                    template = (f"People often change {type_id} parts agents made, setting {what} "
                                f"({len(obs)} times): build them that way.")
                else:
                    template = (f"People often {'move' if edit == 'set_placement' else 'edit'} {type_id} objects "
                                f"agents made ({len(obs)} times): check them against the team's style before finishing.")
                value, consistency = {"edit": edit, "type_id": type_id, "props": dict(props)}, 1.0
            emit("correction_pattern", "team", key, template, value, obs, consistency, n=2 * len(obs))

        # Preferred standard parts and library components.
        by_family = defaultdict(list)
        for e in ops:
            if e["op_kind"] == "insert_standard_part":
                fam = str(e["data"]["args"].get("family") or "")
                if fam:
                    by_family[f"standard:{fam}"].append((str(e["data"]["args"].get("size") or "?"), e))
            elif e["op_kind"] == "insert_component":
                comp = str(e["data"]["args"].get("component") or "")
                if comp:
                    by_family["component"].append((comp, e))
        for key, pairs in by_family.items():
            counts = Counter(v for v, _ in pairs)
            choice, c = counts.most_common(1)[0]
            obs = [e for v, e in pairs if v == choice]
            label = key.split(":", 1)[1] if ":" in key else "library component"
            emit("preferred_component", "team", key,
                 f"Prefer {label} {choice} (used {c} of {len(pairs)} times).",
                 {"family": label, "choice": choice}, obs, c / len(pairs), n=c)

        # Modelling habits: bound to Params, or hard-coded numbers.
        creates = [e for e in ops if e["op_kind"] == "create_primitive" and e["actor_kind"] == "human"]
        if creates:
            bound = [e for e in creates if e["data"]["args"].get("expressions")]
            share = len(bound) / len(creates)
            if share >= 0.5:
                emit("modelling_practice", "team", "expressions",
                     f"Bind dimensions to Params with expressions: people on this team do it in "
                     f"{round(100 * share)}% of their edits.",
                     {"share": round(share, 2)}, bound, share)
        scripts = [e for e in ops if e["op_kind"] in CREATES]
        if scripts:
            n_script = sum(1 for e in scripts if e["op_kind"] == "run_part_script")
            share = 1 - n_script / len(scripts)
            if share >= 0.8 and len(scripts) >= MIN_OBSERVATIONS:
                emit("modelling_practice", "team", "parametric",
                     f"Prefer parametric primitives, extrusions and booleans over part scripts "
                     f"({round(100 * share)}% of parts are built that way).",
                     {"share": round(share, 2)}, [e for e in scripts if e["op_kind"] != "run_part_script"], share)

        # Problems reviews keep finding, per board, and ops that keep failing.
        by_problem = defaultdict(list)
        examples = {}
        for e in events:
            if e["kind"] == "brief_done":
                for p in e["data"].get("problems") or []:
                    norm = normalize_problem(p)
                    if norm:
                        by_problem[(f"board:{e['board']}" if e["board"] else "team", norm)].append(e)
                        examples.setdefault(norm, p)
        for (scope, norm), obs in by_problem.items():
            emit("recurring_problem", scope, norm,
                 f"Reviews keep finding: {examples[norm]} (in {len(obs)} design(s)). Design to avoid it.",
                 {"example": examples[norm]}, obs, 1.0, n=2 * len(obs))
        by_error = defaultdict(list)
        for e in events:
            if e["kind"] == "op_failed" and e["error_code"]:
                by_error[f"{e['op_kind']}:{e['error_code']}"].append(e)
        for key, obs in by_error.items():
            if len(obs) >= 3:
                op_kind, code = key.split(":", 1)
                emit("recurring_problem", "team", f"op:{key}",
                     f"{op_kind} keeps failing with {code} ({len(obs)} times): check its inputs first.",
                     {"op": op_kind, "code": code}, obs, 1.0)
        return out

    def distil(self):
        """Recompute rules from the events and store them, honouring what people decided."""
        with self.lock:
            now = self.clock()
            found = self.candidates()
            changed = []
            for r in found:
                row = self.db.execute("SELECT * FROM style_rules WHERE id=?", (r["id"],)).fetchone()
                status = r["status"]
                if row is not None:
                    if row["status"] == "confirmed":
                        status = "confirmed"
                    elif row["status"] in ("dismissed", "expired") and \
                            r["evidence"]["n"] < 2 * (row["resolved_n"] or 0):
                        status = row["status"]  # a person's no, or a quiet expiry, holds until evidence doubles
                statement = r["template"]
                if row is not None and row["template"] == r["template"] and row["statement"]:
                    statement = row["statement"]  # keep an analyst's wording while the facts are the same
                if row is not None and (row["status"], row["template"], row["confidence"]) == \
                        (status, r["template"], r["confidence"]):
                    continue
                self.db.execute(
                    "INSERT INTO style_rules (id, type, scope, key, statement, template, value, evidence, confidence,"
                    " status, source, created_at, updated_at, seen_at, mirrored) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)"
                    " ON CONFLICT(id) DO UPDATE SET statement=excluded.statement, template=excluded.template,"
                    " value=excluded.value, evidence=excluded.evidence, confidence=excluded.confidence,"
                    " status=excluded.status, updated_at=excluded.updated_at, seen_at=excluded.seen_at, mirrored=0",
                    (r["id"], r["type"], r["scope"], r["key"], statement, r["template"], json.dumps(r["value"]),
                     json.dumps(r["evidence"]), r["confidence"], status, "learned", now, now, r["seen_at"]))
                changed.append(r["id"])
            # Learned rules nothing supports any more fade out: an active one becomes a candidate, and a
            # candidate nobody decided on goes. People's decisions (confirmed, dismissed) stay.
            ids = {r["id"] for r in found}
            for row in self.db.execute("SELECT id, status FROM style_rules WHERE source='learned' "
                                       "AND status IN ('active', 'candidate')").fetchall():
                if row["id"] in ids:
                    continue
                if row["status"] == "active":
                    self.db.execute("UPDATE style_rules SET status='candidate', updated_at=?, mirrored=0 WHERE id=?",
                                    (now, row["id"]))
                else:
                    self.db.execute("DELETE FROM style_rules WHERE id=?", (row["id"],))
                changed.append(row["id"])
            changed.extend(self._expire(now))
            self._since_distil = 0
            counts = dict(self.db.execute("SELECT status, COUNT(*) FROM style_rules GROUP BY status").fetchall())
            return {"changed": changed, "rules": {s: counts.get(s, 0) for s in STATUSES}}

    def _expire(self, now):
        """Candidates nothing has supported for two weeks leave the inbox, recoverably.

        `seen_at` is the last evidence, not the last recompute, so a rule that keeps gathering
        evidence (and may still become active) is never expired. The evidence count is recorded
        so the existing `evidence doubles` rule can bring it back."""
        cutoff = now - CANDIDATE_TTL_DAYS * 86400
        rows = self.db.execute("SELECT id, evidence FROM style_rules WHERE status='candidate' AND seen_at < ?",
                               (cutoff,)).fetchall()
        for row in rows:
            n = json.loads(row["evidence"] or "{}").get("n", 0)
            self.db.execute("UPDATE style_rules SET status='expired', resolved_n=?, updated_at=?, mirrored=0 "
                            "WHERE id=?", (n, now, row["id"]))
        return [row["id"] for row in rows]

    # ----------------------------------------------------------------- people

    def teach(self, statement, by, scope="team", proposed=False):
        """A rule in so many words: a person's is confirmed; an agent's is `proposed`, a candidate for a person."""
        statement = str(statement or "").strip()
        if not statement:
            raise StyleError("bad_args", "statement is required")
        if len(statement) > 300:
            raise StyleError("bad_args", "keep a rule under 300 characters")
        same = _same_words(statement)
        for row in self.db.execute("SELECT id, statement, status FROM style_rules WHERE type='taught'").fetchall():
            if _same_words(row["statement"]) == same:
                # Taught again: one rule, not two. A person saying it again confirms it, even if dismissed.
                if not proposed and row["status"] != "confirmed":
                    self.resolve(row["id"], "confirm", by)
                return self.rule(row["id"])
        rid = "t_" + uuid.uuid4().hex[:12]
        now = self.clock()
        with self.lock:
            self.db.execute(
                "INSERT INTO style_rules (id, type, scope, key, statement, template, value, evidence, confidence,"
                " status, source, resolved_by, created_at, updated_at, seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, "taught", scope or "team", rid, statement, statement, "null",
                 json.dumps({"taught_by": by}), 0.5 if proposed else 1.0, "candidate" if proposed else "confirmed",
                 "proposed" if proposed else "taught", None if proposed else by, now, now, now))
        return self.rule(rid)

    def resolve(self, rid, decision, by):
        if decision not in ("confirm", "dismiss"):
            raise StyleError("bad_args", "decision must be confirm or dismiss")
        row = self.db.execute("SELECT * FROM style_rules WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise StyleError("not_found", f"No style rule {rid}")
        n = json.loads(row["evidence"]).get("n", 0)
        with self.lock:
            self.db.execute("UPDATE style_rules SET status=?, resolved_n=?, resolved_by=?, updated_at=?, mirrored=0"
                            " WHERE id=?", ("confirmed" if decision == "confirm" else "dismissed", n, by,
                                            self.clock(), rid))
        return self.rule(rid)

    def resolve_many(self, ids, decision, by):
        """One answer for a whole decision: every rule describing the same thing is resolved."""
        if decision not in ("confirm", "dismiss"):
            raise StyleError("bad_args", "decision must be confirm or dismiss")
        ids = list(dict.fromkeys(str(i) for i in (ids or [])))
        if not ids:
            raise StyleError("bad_args", "rule_ids is required")
        rows = {row["id"]: row for row in self.db.execute(
            f"SELECT * FROM style_rules WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()}
        missing = [i for i in ids if i not in rows]
        if missing:
            raise StyleError("not_found", f"No style rule {missing[0]}")
        status = "confirmed" if decision == "confirm" else "dismissed"
        now = self.clock()
        with self.lock:
            for rid in ids:
                n = json.loads(rows[rid]["evidence"] or "{}").get("n", 0)
                self.db.execute("UPDATE style_rules SET status=?, resolved_n=?, resolved_by=?, updated_at=?,"
                                " mirrored=0 WHERE id=?", (status, n, by, now, rid))
        return [self.rule(i) for i in ids]

    # ----------------------------------------------------------------- recall

    def rule(self, rid):
        row = self.db.execute("SELECT * FROM style_rules WHERE id=?", (rid,)).fetchone()
        return _rule(row) if row else None

    def rules(self, statuses=STATUSES):
        marks = ",".join("?" * len(statuses))
        rows = self.db.execute(f"SELECT * FROM style_rules WHERE status IN ({marks}) "
                               "ORDER BY confidence DESC, updated_at DESC", tuple(statuses)).fetchall()
        return [_rule(r) for r in rows]

    def inbox(self):
        """What waits for a person, as decisions grouped by subject and kind.

        Reading the inbox also expires quiet candidates, so opening the dialog always shows the
        same backlog a later distil would."""
        self._expire(self.clock())
        rows = [_rule(r) for r in self.db.execute("SELECT * FROM style_rules").fetchall()]
        groups = _group(rows)  # one group per subject, whatever the status of its rules
        # A subject is only pending when every rule about it is still a candidate: once any rule is
        # in use (or was answered), the same fact never asks again through a sibling rule.
        pending = [g for g in groups if g["status"] == "candidate"]
        sections = []
        for kind in SECTION_ORDER:
            found = sorted((g for g in pending if g["kind"] == kind),
                           key=lambda g: (RECOMMEND_ORDER[g["recommendation"]], -g["confidence"]))
            if found:
                sections.append({"kind": kind, "label": SECTION_LABELS[kind], "count": len(found),
                                 "decisions": found})
        used = sorted((g for g in groups if g["status"] in ("confirmed", "active")),
                      key=lambda g: (g["status"] != "confirmed", -g["confidence"]))
        history = sorted((g for g in groups if g["status"] in ("expired", "dismissed")),
                         key=lambda g: (-g["seen_at"], g["title"]))[:200]
        return {"rules": sum(len(g["ids"]) for g in pending), "decisions": len(pending),
                "ready": sum(1 for g in pending if g["recommendation"] == "confirm"),
                "weak": sum(1 for g in pending if g["recommendation"] == "dismiss"),
                "sections": sections, "used": used, "history": history}

    def context(self, query="", board="", limit=8):
        """The rules an agent should follow for this request, firmest and most relevant first."""
        words = set(re.findall(r"[a-z0-9]+", str(query or "").lower()))
        scopes = {"team"} | ({f"board:{board}"} if board else set())

        def score(r):
            text = set(re.findall(r"[a-z0-9]+", (r["statement"] + " " + r["key"]).lower()))
            return (r["status"] == "confirmed", r["scope"] != "team", len(words & text), r["confidence"])

        use = [r for r in self.rules(("confirmed", "active")) if r["scope"] in scopes]
        use.sort(key=score, reverse=True)
        use = use[:limit]
        pending = self.db.execute("SELECT COUNT(*) FROM style_rules WHERE status='candidate'").fetchone()[0]
        lines = [f"- {'[confirmed] ' if r['status'] == 'confirmed' else ''}{r['statement']}" for r in use]
        text = ("Team design style (learned from how this team works in CADSwarm; follow it, and ask a person "
                "before going against a confirmed rule):\n" + "\n".join(lines)) if lines else ""
        return {"text": text, "rules": [{k: r[k] for k in ("id", "type", "scope", "statement", "status",
                                                           "confidence")} for r in use],
                "pending": pending}


def _same_words(text):
    return " ".join(re.findall(r"[a-z0-9.]+", str(text).lower())).rstrip(".")


def _trim(value, limit=400):
    text = json.dumps(value, default=str)
    return value if len(text) <= limit else {"_truncated": text[:limit]}


def _obj(o):
    return {k: o.get(k) for k in ("name", "type_id", "bbox", "volume") if k in o} | \
        ({"bound": True} if o.get("expressions") not in (None, "", "{}", {}) else {})


def _rule(row):
    d = dict(row)
    d["evidence"] = json.loads(d["evidence"] or "{}")
    d["value"] = json.loads(d["value"] or "null")
    return d


# --------------------------------------------------------------------- CLI

def main(argv=None):
    from cadswarm.paths import project_layout
    ap = argparse.ArgumentParser(prog="python -m cadswarm.style_memory",
                                 description="Inspect what CADSwarm has learned about this team's design style.")
    ap.add_argument("command", choices=["show", "distil", "export"])
    ap.add_argument("--project", default=os.environ.get("CADSWARM_PROJECT") or os.getcwd())
    ns = ap.parse_args(argv)
    layout = project_layout(ns.project)
    db = sqlite3.connect(layout["db"], isolation_level=None)
    db.row_factory = sqlite3.Row
    style = StyleMemory(db)
    if ns.command == "distil":
        print(json.dumps(style.distil(), indent=2))
    elif ns.command == "export":
        print(json.dumps(style.rules(), indent=2, default=str))
    else:
        rules = style.rules()
        if not rules:
            print("Nothing learned yet: finish a brief or edit the model through CADSwarm first.")
        for r in rules:
            ev = r["evidence"]
            print(f"[{r['status']:9}] {r['confidence']:.2f}  {r['statement']}  "
                  f"({r['type']}, n={ev.get('n', '-')}, people={ev.get('people', '-')}, id={r['id']})")


if __name__ == "__main__":
    main()
