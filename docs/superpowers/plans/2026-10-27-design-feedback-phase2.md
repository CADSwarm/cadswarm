# Design feedback, phase 2: verdicts, reference images, learned lessons

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person can say "this looks wrong, here's why, here's a photo" in one message, and the next
build is told what that person rejects, in their own words.

**Architecture:** Feedback is a new `feedback` event in the existing `style_events` table; `distil()`
turns it into one new rule type (`feedback_lesson`) whose key says whether it is a recurring complaint,
a rejected standard or an accepted approach. Images and reference photos are project files, written
through the existing versioned `ProjectFiles` store. Four helper ops carry it, the host routes them like
`teach_style`, and four MCP tools expose it.

**Tech Stack:** Python 3 stdlib, SQLite (project database), FreeCAD worker only for the geometry
evidence already added in phase 1.

## Global Constraints

- No new database and no schema migration: `style_events`, `style_rules` and `style_meta` are enough.
- An agent may relay a person's verdict (`by="person"`) or give its own (`by="helper"`); a helper's
  verdict is a proposal and can never become an active rule on its own.
- Reference images: PNG/JPEG/WebP only, 20 MB maximum, always through `ProjectFiles.save` (versioned,
  attributed, quota-counted). Nothing is written to disk directly.
- Feedback never blocks geometry: a failed save or distil is reported, not fatal.
- Every new op kind goes in `rules/src/lib.rs`, `executor.rs::HELPER_KINDS`, `helper.py::HANDLERS`.
- Full suite must stay green (742 passed at the end of phase 1).

---

### Task 1: The feedback event and the lesson it teaches

**Files:**
- Modify: `cadswarm/style_memory.py`
- Test: `tests/test_style_memory.py` (append), `tests/test_style_triage.py` (one section check)

**Interfaces:**
- Produces: `StyleMemory.observe_feedback(a) -> {"id", "subject", "recorded"}`, where `a` is
  `{verdict, issues?, note?, subject?, tags?, images?, by?, objects?, evidence?: {intent, objects}, actor?, brief?, board?}`
- Produces: `StyleMemory.feedback(subject="", limit=10) -> {"verdicts": [...], "lessons": [rule...]}`
- Produces rule type `feedback_lesson` with `key` in `theme:<norm>`, `standard:<subject>`, `accepted:<subject>`
- Consumes: `normalize_problem`, `_add`, `emit`, `gate` and `recommend` from the same module

- [ ] **Step 1: Write the failing tests**

```python
def test_a_person_s_bad_verdict_becomes_a_feedback_lesson(style):
    style.observe_feedback({"verdict": "bad", "subject": "car",
                            "issues": ["roof is a box", "too slab-sided"], "by": "person",
                            "actor": {"id": 1, "name": "sam", "kind": "human"},
                            "evidence": {"intent": "aesthetic",
                                         "objects": [{"name": "BodyShell", "faces": 194, "planar": 146,
                                                      "curved": 48, "planar_share": 0.753}]}})
    style.observe_feedback({"verdict": "bad", "subject": "car", "issues": ["roof is a box"], "by": "person",
                            "actor": {"id": 1, "name": "sam", "kind": "human"}, "brief": "b_2",
                            "evidence": {"intent": "aesthetic",
                                         "objects": [{"name": "BodyShell", "faces": 194, "planar": 146,
                                                      "curved": 48, "planar_share": 0.753}]}})
    style.distil()
    rules = {r["key"]: r for r in style.rules() if r["type"] == "feedback_lesson"}
    theme = rules["theme:roof is a box"]
    assert theme["status"] == "active" and "roof is a box" in theme["statement"]
    standard = rules["standard:car"]
    assert "flat faces under 60" in standard["statement"]


def test_a_helper_s_own_opinion_never_goes_active(style):
    style.observe_feedback({"verdict": "bad", "subject": "car", "issues": ["too round"], "by": "helper",
                            "actor": {"id": 2, "name": "ann-claude", "kind": "agent"}})
    style.observe_feedback({"verdict": "bad", "subject": "car", "issues": ["too round"], "by": "helper",
                            "actor": {"id": 2, "name": "ann-claude", "kind": "agent"}, "brief": "b_2"})
    style.distil()
    assert not [r for r in style.rules() if r["type"] == "feedback_lesson" and r["status"] == "active"]


def test_a_good_verdict_teaches_the_approach_that_was_accepted(style):
    style.observe_feedback({"verdict": "good", "subject": "car", "by": "person",
                            "actor": {"id": 1, "name": "sam", "kind": "human"},
                            "evidence": {"intent": "aesthetic",
                                         "objects": [{"name": "BodyShell", "faces": 194, "planar": 60,
                                                      "curved": 134, "planar_share": 0.31}]}})
    style.observe_feedback({"verdict": "good", "subject": "car", "by": "person", "brief": "b_2",
                            "actor": {"id": 1, "name": "sam", "kind": "human"},
                            "evidence": {"intent": "aesthetic",
                                         "objects": [{"name": "BodyShell", "faces": 194, "planar": 60,
                                                      "curved": 134, "planar_share": 0.31}]}})
    style.distil()
    got = [r for r in style.rules() if r["key"] == "accepted:car"]
    assert got and "Accepted on car" in got[0]["statement"]


def test_feedback_for_a_mechanical_subject_teaches_no_standard(style):
    style.observe_feedback({"verdict": "bad", "subject": "bracket", "issues": ["holes too close"], "by": "person",
                            "actor": {"id": 1, "name": "sam", "kind": "human"},
                            "evidence": {"intent": "mechanical",
                                         "objects": [{"name": "Bracket", "faces": 6, "planar": 6,
                                                      "curved": 0, "planar_share": 1.0}]}})
    style.distil()
    keys = {r["key"] for r in style.rules()}
    assert "theme:holes too close" in keys
    assert not any(k.startswith(("standard:", "accepted:")) for k in keys)
```

Use the existing `style` fixture in `tests/test_style_memory.py` (a `StyleMemory` over a temporary
project db); if the fixture is named differently, follow the file's convention.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_style_memory.py -k feedback -v`
Expected: FAIL with `AttributeError: 'StyleMemory' object has no attribute 'observe_feedback'`

- [ ] **Step 3: Implement**

In `style_memory.py`:

```python
FEEDBACK_VERDICTS = ("bad", "mixed", "good")
FEEDBACK_TAGS = ("proportions", "surfacing", "detail", "panels", "fit", "color")
MIN_FEEDBACK_OBSERVATIONS = 2

def feedback_confidence(n, consistency, people, occasions, age_days):
    """A verdict is deliberate, so it counts faster than an op: two agreeing ones can teach a rule."""
    volume = 1 - math.exp(-n / 1.5)
    spread = min(1.0, (people + occasions) / 2.0)
    decay = 0.5 ** (max(0.0, age_days) / HALF_LIFE_DAYS)
    return round(consistency * volume * spread * decay, 3)

def feedback_gate(conf, n, occasions):
    return "active" if conf >= ACTIVE_CONFIDENCE and n >= MIN_FEEDBACK_OBSERVATIONS and occasions >= 2 \
        else "candidate"
```

`_add` returns the new row id (`return self.db.execute(...).lastrowid`) — change the insert to capture
the cursor. Then:

```python
    def observe_feedback(self, a):
        """A verdict on how the model looks: the person's words, the subject, and the shape that earned it."""
        verdict = str(a.get("verdict") or "").lower()
        if verdict not in FEEDBACK_VERDICTS:
            raise StyleError("bad_args", f"verdict must be one of {list(FEEDBACK_VERDICTS)}")
        actor = dict(a.get("actor") or {})
        by = "helper" if str(a.get("by") or "").lower() == "helper" else "person"
        if not actor.get("kind"):
            actor["kind"] = "agent" if by == "helper" else "human"
        issues = [str(i).strip() for i in (a.get("issues") or []) if str(i).strip()][:12]
        data = {"verdict": verdict, "issues": issues, "note": str(a.get("note") or "")[:2000],
                "subject": _slug(a.get("subject") or "model"),
                "tags": [t for t in (a.get("tags") or []) if t in FEEDBACK_TAGS],
                "images": [str(i) for i in (a.get("images") or [])][:6], "by": by,
                "objects": [str(o)[:80] for o in (a.get("objects") or [])][:20],
                "evidence": a.get("evidence") or {}}
        rid = self._add("feedback", actor, data, "feedback", brief=a.get("brief") or "",
                        board=a.get("board") or "")
        return {"id": rid, "subject": data["subject"], "recorded": 1}

    def feedback(self, subject="", limit=10):
        """Recent verdicts and the lessons learned from them, newest first."""
        rows = self.db.execute("SELECT * FROM style_events WHERE kind='feedback' ORDER BY id DESC LIMIT ?",
                               (max(1, min(int(limit or 10), 50)),)).fetchall()
        verdicts = []
        for row in rows:
            d = json.loads(row["data"])
            if subject and d.get("subject") != _slug(subject):
                continue
            verdicts.append({"at": row["at"], "verdict": d.get("verdict"), "subject": d.get("subject"),
                             "issues": d.get("issues") or [], "note": d.get("note") or "",
                             "tags": d.get("tags") or [], "images": d.get("images") or [],
                             "by": d.get("by"), "author": row["actor_name"], "id": row["id"]})
        lessons = [r for r in self.rules(("confirmed", "active", "candidate"))
                   if r["type"] == "feedback_lesson" and (not subject or r["scope"] == _slug(subject))]
        return {"verdicts": verdicts, "lessons": lessons}
```

In `candidates()`, add the grouping. Extend `emit` with `fast=False`:

```python
        def emit(type_, scope, key, template, value, obs, consistency, n=None, fast=False):
            ...
            if fast:
                conf = feedback_confidence(n, consistency, people, occasions, (now - last) / 86400)
                status = feedback_gate(conf, n, occasions)
            else:
                conf = confidence(n, consistency, people, occasions, (now - last) / 86400)
                status = gate(conf, n, people, occasions)
```

and after the correction patterns:

```python
        # What a person said about how the model looks. Their words are the lesson; a helper's own
        # opinion waits like a proposal and never becomes active on its own.
        said = [e for e in events if e["kind"] == "feedback" and e["data"].get("by") == "person"]
        by_issue, examples = defaultdict(list), {}
        for e in said:
            d = e["data"]
            if d.get("verdict") not in ("bad", "mixed"):
                continue
            for issue in d.get("issues") or []:
                norm = normalize_problem(issue)
                if norm:
                    by_issue[(d.get("subject") or "model", norm)].append(e)
                    examples.setdefault(norm, issue)
        for (subject, norm), obs in by_issue.items():
            emit("feedback_lesson", subject, f"theme:{norm}",
                 f"Rejected on {subject} {len(obs)}\u00d7: \u201c{examples[norm]}\u201d. Design to avoid it.",
                 {"issue": examples[norm], "subject": subject}, obs, 1.0, fast=True)
        def shape_evidence(e):
            ev = e["data"].get("evidence") or {}
            objs = [o for o in ev.get("objects") or [] if isinstance(o, dict)]
            return ev.get("intent"), objs
        rejected, accepted = defaultdict(list), defaultdict(list)
        for e in said:
            intent, objs = shape_evidence(e)
            subject = e["data"].get("subject") or "model"
            if intent != "aesthetic" or not objs:
                continue
            worst = max((o.get("planar_share") or 0) for o in objs)
            best = next(o for o in objs if (o.get("planar_share") or 1) == min(o.get("planar_share") or 1 for o in objs))
            if e["data"].get("verdict") in ("bad", "mixed") and worst > 0.6:
                rejected[subject].append(e)
            elif e["data"].get("verdict") == "good" and worst <= 0.6:
                accepted[(subject, best)].append(e)
        for subject, obs in rejected.items():
            emit("feedback_lesson", subject, f"standard:{subject}",
                 f"This team rejects faceted shells on {subject}: keep flat faces under 60 % and loft the "
                 f"main surfaces (seen {len(obs)}\u00d7).", {"subject": subject, "max_planar_share": 0.6},
                 obs, 1.0, fast=True)
        for (subject, best), obs in accepted.items():
            emit("feedback_lesson", subject, f"accepted:{subject}",
                 f"Accepted on {subject}: lofted shell with {round(100 * (best.get('planar_share') or 0))} % flat "
                 f"faces and {best.get('curved', 0)} curved faces (seen {len(obs)}\u00d7).",
                 {"subject": subject, "planar_share": best.get("planar_share")}, obs, 1.0, fast=True)
```

Add `feedback_lesson` to `subject_of`/`section_of`, `SECTION_LABELS` (`"Feedback"`),
`SECTION_ORDER` (after `recurring_problem`), and `recommend` (confirm at `n >= 2`, never dismiss).

Add `_slug`:

```python
def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40] or "model"
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_style_memory.py tests/test_style_triage.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cadswarm/style_memory.py tests/test_style_memory.py tests/test_style_triage.py
git commit -m "feat(style): learned lessons from a person's verdict on how the model looks"
```

---

### Task 2: Helper ops for feedback and references

**Files:**
- Modify: `cadswarm/helper.py`
- Modify: `rules/src/lib.rs` (`QUERIES`, `FILE_WRITES`)
- Modify: `host/src/executor.rs` (`HELPER_KINDS`)
- Test: `tests/test_helper.py`, `tests/test_style_memory.py`

**Interfaces:**
- Consumes: `StyleMemory.observe_feedback`, `StyleMemory.feedback` (Task 1), `ProjectFiles.save/listing/read`
- Produces ops: `record_feedback`, `feedback_recent`, `add_reference`, `list_references`

- [ ] **Step 1: Write the failing tests**

```python
def test_record_feedback_and_read_it_back(helper_project):
    r = helper.handle(helper_project, {"id": 1, "op": "record_feedback", "args": {
        "verdict": "bad", "subject": "car", "issues": ["roof is a box"], "by": "person",
        "_actor": {"id": 1, "name": "sam", "kind": "human"}}})
    assert r["ok"] and r["result"]["subject"] == "car"
    back = helper.handle(helper_project, {"id": 2, "op": "feedback_recent", "args": {"subject": "car"}})
    assert back["result"]["verdicts"][0]["issues"] == ["roof is a box"]


def test_a_reference_image_is_saved_as_a_project_file_with_its_metadata(helper_project, tmp_path):
    src = tmp_path / "is350.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    r = helper.handle(helper_project, {"id": 1, "op": "add_reference", "args": {
        "subject": "car", "label": "front", "image_base64": __import__("base64").b64encode(src.read_bytes()).decode(),
        "filename": "is350.png", "notes": "2021 F Sport, side view", "dimensions": "4710 x 1840 x 1435",
        "_actor": {"id": 1, "name": "sam", "kind": "human"}}})
    assert r["ok"] and r["result"]["image"].startswith("reference/car/")
    listed = helper.handle(helper_project, {"id": 2, "op": "list_references", "args": {"subject": "car"}})
    item = listed["result"]["references"][0]
    assert item["notes"] == "2021 F Sport, side view" and item["image"].endswith(".png")


def test_a_reference_refuses_a_huge_or_unsupported_file(helper_project):
    import base64
    big = base64.b64encode(b"x" * (20 * 1024 * 1024 + 1)).decode()
    bad = helper.handle(helper_project, {"id": 1, "op": "add_reference", "args": {
        "subject": "car", "image_base64": big, "filename": "x.png"}})
    assert not bad["ok"] and bad["code"] == "bad_args"
    gif = helper.handle(helper_project, {"id": 2, "op": "add_reference", "args": {
        "subject": "car", "filename": "x.gif",
        "image_base64": base64.b64encode(b"GIF89a").decode()}})
    assert not gif["ok"] and gif["code"] == "bad_args"
```

Follow `tests/test_helper.py` for how it builds `Project` (there is an existing fixture/helper there).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_helper.py -k "feedback or reference" -v`
Expected: FAIL with `unknown_op: The helper has no op 'record_feedback'`

- [ ] **Step 3: Implement**

In `helper.py`:

```python
MAX_REFERENCE_BYTES = 20 * 1024 * 1024
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def record_feedback(p, a):
    out = p.style.observe_feedback(a)
    if p.style.due():
        learned = _learn(p)
        out.update(distilled=learned["rules"], profile=learned["profile"])
    return out


def _save_image(p, subject, name, image_base64, author):
    import base64 as b64mod
    from cadswarm.style_memory import _slug
    name = os.path.basename(str(name or "")).lower()
    ext = os.path.splitext(name)[1]
    if ext not in IMAGE_EXT:
        raise HelperError("bad_args", "Reference images must be PNG, JPEG or WebP.")
    try:
        data = b64mod.b64decode(str(image_base64 or ""), validate=True)
    except (ValueError, TypeError):
        raise HelperError("bad_args", "image_base64 is not valid base64.")
    if not data:
        raise HelperError("bad_args", "The image is empty.")
    if len(data) > MAX_REFERENCE_BYTES:
        raise HelperError("bad_args", "Reference images are limited to 20 MB.")
    stem = _slug(os.path.splitext(name)[0]) or "image"
    saved = p.files.save(f"reference/{_slug(subject)}/{stem}{ext}", author, base64_data=image_base64,
                         if_exists="new_name")
    return saved["path"]


def add_reference(p, a):
    subject = str(a.get("subject") or "").strip()
    if not subject:
        raise HelperError("bad_args", "subject is required (e.g. 'car').")
    image = None
    if a.get("image_base64"):
        image = _save_image(p, subject, a.get("filename") or "image.png", a.get("image_base64"), _author(a))
    meta = {"subject": subject, "label": str(a.get("label") or ""), "notes": str(a.get("notes") or ""),
            "dimensions": str(a.get("dimensions") or ""), "source_url": str(a.get("source_url") or ""),
            "image": image, "added_by": _author(a), "at": p.clock()}
    path = p.files.save(f"reference/{_slug(subject)}/{(meta['label'] or 'reference')}-{int(p.clock())}.json",
                        _author(a), text=json.dumps(meta, indent=2), if_exists="new_name")["path"]
    return {**meta, "meta": path}


def list_references(p, a):
    subject = _slug(a.get("subject") or "") if a.get("subject") else ""
    prefix = f"reference/{subject}/" if subject else "reference/"
    out = []
    for item in p.files.listing(prefix):
        if not str(item["path"]).endswith(".json"):
            continue
        try:
            meta = json.loads(p.files.read(item["path"])["text"])
        except (FileError, ValueError):
            continue
        out.append(meta)
    out.sort(key=lambda m: m.get("at") or 0, reverse=True)
    return {"references": out}


def feedback_recent(p, a):
    return p.style.feedback(a.get("subject") or "", int(a.get("limit") or 10))
```

Add to `HANDLERS`: `"record_feedback": record_feedback, "feedback_recent": feedback_recent,
"add_reference": add_reference, "list_references": list_references`.

In `rules/src/lib.rs`: `QUERIES` += `"feedback_recent", "list_references"`; `FILE_WRITES` +=
`"record_feedback", "add_reference"`.

In `host/src/executor.rs` `HELPER_KINDS` += the four.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_helper.py -k "feedback or reference" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cadswarm/helper.py rules/src/lib.rs host/src/executor.rs tests/test_helper.py
git commit -m "feat(helper): record verdicts and keep reference images in the project"
```

---

### Task 3: The four tools

**Files:**
- Modify: `cadswarm/mcp_server.py`
- Modify: `cadswarm/space_hub.py`
- Test: `tests/test_mcp_space.py`, `tests/test_space_hub.py`

**Interfaces:**
- Consumes: phase 1's `surface_quality` for evidence; the helper ops from Task 2.
- Produces: `record_feedback`, `model_feedback`, `add_reference`, `list_references`

- [ ] **Step 1: Write the failing tests**

```python
def test_record_feedback_attaches_the_shape_evidence(monkeypatch, session):
    posts = []
    def route(path, body=None):
        posts.append((path, body))
        if path == "/surface":
            return {"objects": [{"name": "BodyShell", "faces": 194, "planar": 146, "curved": 48, "valid": True}]}
        return {"id": 7, "subject": "car", "recorded": 1}
    session.client.post = route
    got = mcp_server.record_feedback("bad", ["roof is a box"], subject="car", objects=["BodyShell"],
                                     topic="a Lexus IS350 body")
    assert got["id"] == 7
    feedback_post = posts[-1][1]
    assert feedback_post["verdict"] == "bad" and feedback_post["issues"] == ["roof is a box"]
    assert feedback_post["evidence"]["intent"] == "aesthetic"
    assert feedback_post["evidence"]["objects"][0]["planar_share"] == 0.753


def test_record_feedback_refuses_a_bad_verdict(session):
    assert mcp_server.record_feedback("meh")["code"] == "bad_args"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_space.py -k record_feedback -v`
Expected: FAIL, no attribute `record_feedback`

- [ ] **Step 3: Implement**

Add the tools after `surface_quality`:

```python
@mcp.tool()
def record_feedback(verdict: str, issues: list[str] | None = None, note: str | None = None,
                    objects: list[str] | None = None, tags: list[str] | None = None,
                    images: list[str] | None = None, subject: str | None = None,
                    topic: str | None = None, by: str = "person") -> dict:
    """Record how your person judged the model: their verdict, their exact words, and photos if they sent any.

    pass `by="person"` only when they actually said it ("the roof looks like a box", "this is fine now");
    your own opinion is `by="helper"` and waits for a person to confirm it. `issues` are short phrases in
    their words. `images` are local file paths they gave you (PNG/JPEG/WebP, 20 MB); each is copied into
    the project under reference/<subject>/ and returned. Shape evidence is attached automatically.
    """
    verdict = str(verdict or "").lower()
    if verdict not in ("bad", "mixed", "good"):
        return {"error": "verdict must be bad, mixed or good", "code": "bad_args"}
    if by not in ("person", "helper"):
        return {"error": "by must be person or helper", "code": "bad_args"}
    stats = surface_quality(objects, topic=topic or "", intent=None) if objects else None
    evidence = {"intent": (stats or {}).get("intent", "mechanical"), "objects": (stats or {}).get("objects", [])}
    body = {"verdict": verdict, "issues": issues or [], "note": note or "", "objects": objects or [],
            "tags": tags or [], "subject": subject or "", "by": by, "evidence": evidence,
            "images": _save_images(images or [], subject or "model")}
    return call("POST", "/feedback", body, doing="recording feedback")


@mcp.tool()
def model_feedback(subject: str = "", limit: int = 10) -> dict:
    """What this team said about a kind of model: recent verdicts in their words, and the lessons learned.

    Call it before rebuilding something a person already judged; quote their words, not a paraphrase."""
    return call("GET", "/feedback", subject=subject, limit=limit)


@mcp.tool()
def add_reference(subject: str, image_path: str = "", label: str = "", notes: str = "",
                  dimensions: str = "", source_url: str = "") -> dict:
    """Save the look a design should match: a photo, sketch or render, plus what it is.

    `subject` is the kind of thing ("car", "helmet"); `dimensions` free text like "4710 x 1840 x 1435 mm";
    `image_path` is a local file (PNG/JPEG/WebP, 20 MB) on your machine. The image goes into the project so
    every teammate can open it, and list_references gives the path back."""
    if not image_path:
        return {"error": "image_path is required", "code": "bad_args"}
    return call("POST", "/references", {"subject": subject, "label": label, "notes": notes,
                                        "dimensions": dimensions, "source_url": source_url,
                                        "filename": os.path.basename(image_path),
                                        "image_base64": _read_image(image_path)})


@mcp.tool()
def list_references(subject: str = "") -> dict:
    """The reference images the team saved, with notes and dimensions. Open the `image` path to look at it."""
    return call("GET", "/references", subject=subject)
```

Helpers:

```python
def _read_image(path):
    """A local image as base64, refusing anything the project store should not hold."""
    import base64
    path = os.path.abspath(os.path.expanduser(str(path or "")))
    if not os.path.isfile(path):
        raise ValueError(f"There's no file {path}")
    if os.path.getsize(path) > 20 * 1024 * 1024:
        raise ValueError("Images are limited to 20 MB")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _save_images(paths, subject):
    """Copy the person's photos into the project beside their verdict; return the project paths."""
    out = []
    for path in paths[:6]:
        try:
            saved = call("POST", "/references", {"subject": subject, "label": "feedback",
                                                 "filename": os.path.basename(path),
                                                 "image_base64": _read_image(path)})
        except (OSError, ValueError):
            continue
        if isinstance(saved, dict) and saved.get("image"):
            out.append(saved["image"])
    return out
```

Note: `_read_image` raising is caught by the MCP layer? A tool raising shows an error to the agent;
for `add_reference` that is fine (the message says what is wrong). Keep it.

Hub routes + methods:

```python
            ("POST", r"/feedback", self.feedback_record), ("GET", r"/feedback", self.feedback_recent),
            ("POST", r"/references", self.add_reference), ("GET", r"/references", self.list_references),
```

```python
    def feedback_record(self, body):
        return self.run_op("record_feedback", dict(body or {}))

    def feedback_recent(self, subject="", limit=10):
        return self.run_op("feedback_recent", {"subject": subject, "limit": int(limit or 10)})

    def add_reference(self, body):
        return self.run_op("add_reference", dict(body or {}))

    def list_references(self, subject=""):
        return self.run_op("list_references", {"subject": subject})
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_mcp_space.py tests/test_space_hub.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cadswarm/mcp_server.py cadswarm/space_hub.py tests/test_mcp_space.py tests/test_space_hub.py
git commit -m "feat(mcp): record_feedback, model_feedback and reference images"
```

---

### Task 4: Make the loop reach the agent at the right moment

**Files:**
- Modify: `cadswarm/mcp_server.py` (`INSTRUCTIONS` item 15)
- Modify: `cadswarm/agent_setup.py` (`AGENT_DOC`)
- Modify: `cadswarm/prompt_hook.py` (`SHAPE_HINT`)
- Test: `tests/test_prompt_hook.py`, `tests/test_setup.py`, `tests/test_mcp_space.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_the_critique_hint_tells_the_agent_to_record_and_look_at_references():
    hint = aesthetic_hint("the roof looks like a box, here is a photo")
    assert "record_feedback" in hint and "add_reference" in hint
```

```python
def test_agents_are_told_to_relay_what_the_person_said():
    from cadswarm.agent_setup import AGENT_DOC
    assert "record_feedback" in AGENT_DOC and "by=\"person\"" in AGENT_DOC
```

- [ ] **Step 2: Run to see them fail**

Run: `python -m pytest tests/test_prompt_hook.py tests/test_setup.py -q`
Expected: FAIL on the new assertions

- [ ] **Step 3: Implement (exact text)**

`SHAPE_HINT` gains:

```
When they say it looks wrong, record their exact words with
record_feedback(verdict="bad", issues=[...], by="person") and save any photo they send with add_reference;
before rebuilding, read model_feedback for that subject and open the reference images.
```

`INSTRUCTIONS` item 15 gains:

```
When your person says a model looks wrong, relay their words with record_feedback(by="person") and attach
any photo with add_reference; before rebuilding, call model_feedback and look at the reference images.
```

`AGENT_DOC` gains:

```
- When your person says it looks wrong, record their exact words with `record_feedback` (`by="person"`)
  and attach their photos with `add_reference`; before rebuilding, read `model_feedback` and open the references.
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_prompt_hook.py tests/test_setup.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cadswarm/mcp_server.py cadswarm/agent_setup.py cadswarm/prompt_hook.py tests/test_prompt_hook.py tests/test_setup.py
git commit -m "feat(agents): relay what the person said, and look before rebuilding"
```

---

## Self-review

- **Spec coverage (phase 2):** capture (§1) → Tasks 2–3; references (§1) → Tasks 2–3; learning (§2) →
  Task 1; recall (§2) → `model_feedback` + `context()`; guidance (§5) → Task 4. Renders and the IS350
  rebuild are phases 3–4.
- **Type consistency:** `observe_feedback` consumes `evidence.objects[*].planar_share`, which the MCP
  attaches from phase 1's `surface_quality`; `feedback()` and the rule keys use the same `_slug`.
- **No placeholders:** each step has code or exact text.
