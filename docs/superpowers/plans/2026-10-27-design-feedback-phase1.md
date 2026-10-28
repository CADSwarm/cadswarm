# Design feedback, phase 1: shape intent + surface quality + the gate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** A part that is judged on how it looks cannot pass review while it is a pile of flat faces, and a boxy part is never punished for being a box.

**Architecture:** A pure-Python intent classifier scores the request and the part names (`cadswarm/shape_intent.py`). A new read-only worker query reports each part's face mix. The MCP server composes the two into one verdict with plain-language advice, and the reviewer prompt, MCP instructions and generated AGENTS.md make that verdict part of finishing a shape that is judged on looks. Nothing here learns yet; phases 2–4 (feedback events, references, renders, the IS350 rebuild) get their own plan.

**Tech Stack:** Python 3 stdlib (FreeCAD `Part` only inside the worker), Rust host tests, pytest.

## Global Constraints

- New Python modules are stdlib only; no new dependencies.
- Op kinds must be registered in `rules/src/lib.rs`; the module rejects unknown kinds.
- The vocabulary and every message are lowercase English; match whole words (`[a-z0-9]+`), never substrings.
- Default to **mechanical** on a tie or no evidence: a wrongly-boxy bracket is cheap, a wrongly-smooth bracket wastes a build.
- Aesthetic ceiling is `max_planar_share = 0.6` and `min_curved_faces = max(8, 5 % of faces)`.
- Tests run from the worktree with the repo venv: `..\..\..\..\Documents\Projects\3D CAD\.venv\Scripts\python.exe -m pytest`.
- FreeCAD-dependent tests skip when FreeCAD is absent (`find_freecad_python()`); keep that guard.

---

### Task 1: Intent classifier

**Files:**
- Create: `cadswarm/shape_intent.py`
- Test: `tests/test_shape_intent.py`

**Interfaces:**
- Produces: `classify(text="", names=(), type_ids=()) -> {"intent", "confidence", "matched", "required"}`
- Produces: `required(intent, faces=0) -> dict | None`
- Produces: `assess(stats, intent, bar=None) -> {"intent", "required", "verdict", "advice", "planar_share"?}`

- [ ] **Step 1: Write the failing test**

```python
"""Shape intent: a car is judged on form, a bracket on function."""
from cadswarm.shape_intent import assess, classify, required


def test_a_car_body_is_aesthetic_and_a_table_is_mechanical():
    assert classify("model a 2021 Lexus IS 350 F Sport exterior")["intent"] == "aesthetic"
    assert classify("a workbench table for the shop")["intent"] == "mechanical"


def test_a_part_name_is_evidence_and_a_tie_defaults_to_mechanical():
    assert classify("", names=["BodyShell", "Roof"])["intent"] == "aesthetic"
    assert classify("a frame")["intent"] == "mechanical"
    assert classify("")["intent"] == "mechanical"
    assert classify("")["required"] is None


def test_the_stronger_vocabulary_wins_and_reports_what_matched():
    got = classify("a car bracket for the bumper")
    assert got["intent"] == "aesthetic"
    assert "car" in got["matched"] and "bumper" in got["matched"]
    assert classify("a table bracket")["intent"] == "mechanical"


def test_the_ceiling_scales_with_the_face_count():
    assert required("mechanical", 200) is None
    assert required("aesthetic", 0) == {"max_planar_share": 0.6, "min_curved_faces": 8}
    assert required("aesthetic", 400)["min_curved_faces"] == 20


def test_a_box_fails_an_aesthetic_bar_and_passes_a_mechanical_one():
    box = {"faces": 6, "planar": 6, "curved": 0, "valid": True}
    bad = assess(box, "aesthetic")
    assert bad["verdict"] == "fails" and bad["planar_share"] == 1.0
    assert "block model" in bad["advice"][0]
    assert assess(box, "mechanical")["verdict"] == "not required"


def test_a_lofted_shell_passes_and_an_invalid_solid_does_not():
    shell = {"faces": 120, "planar": 20, "curved": 100, "valid": True}
    assert assess(shell, "aesthetic")["verdict"] == "pass"
    broken = {"faces": 120, "planar": 20, "curved": 100, "valid": False}
    assert assess(broken, "aesthetic")["verdict"] == "fails"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_shape_intent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cadswarm.shape_intent'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Whether a design is judged on how it looks or on what it does.

The reviewer and the MCP tools use this to decide whether a part is allowed to be made of flat
faces. A bracket, a plate and a table are boxes because a box is the honest shape. A car body, a
helmet, a fairing or a product exterior are not: flat faces there read as a block model.

A plain score over a curated vocabulary, never a model call: it must work offline, be explainable
to the person it is judging, and never punish a box for being a box.
"""
import re

# What is judged on form. Whole words in the request, and anywhere in a part's name.
AESTHETIC = frozenset("""
aerodynamic aesthetic automotive boat bodywork bonnet bumper bus camera car cars chair concept
coupe curvy curved dashboard diffuser ergonomic exterior fairing fascia fender figurine
furniture fuselage grille headlamp headlight helmet hinge-less hood hull impeller interior jet
kettle lamp lifelike mirror motorbike motorcycle mouse organic phone pillar propeller realistic
roof scooter seat sedan sleek smooth sofa spoiler statue suv tablet taillamp taillight toy truck
turbine van vase vehicle watch wing yacht
""".split())
# What is judged on function.
MECHANICAL = frozenset("""
adapter beam bearing board bolt boss box bracket bushing cabinet case chassis clamp coupler desk
enclosure fastener fixture flange frame gusset heatsink hinge housing jig knob motor mount mounts
nut pcb plate profile pulley rail rib shaft shelf spacer standoff table tube washer
""".split())
# A make or model is a design brief, whatever else the sentence says.
BRANDS = frozenset("""
apple audi bmw corvette ferrari ford honda iphone is350 lamborghini lexus mazda mclaren mercedes
mustang nissan porsche subaru tesla toyota
""".split())

# A shape judged on form may have at most this share of flat faces, and at least this many curved
# ones (scaled up for big models so a 400-face body still needs real curvature).
MAX_PLANAR_SHARE = 0.6
MIN_CURVED_FACES = 8


def _words(text):
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _hits(words, vocabulary):
    return sorted(words & vocabulary)


def required(intent, faces=0):
    """The bar a part of this intent must clear, or None when it is allowed to be boxy."""
    if intent != "aesthetic":
        return None
    return {"max_planar_share": MAX_PLANAR_SHARE,
            "min_curved_faces": max(MIN_CURVED_FACES, round(0.05 * max(0, int(faces or 0))))}


def classify(text="", names=(), type_ids=()):
    """Which vocabulary the request and the part names support, and by how much.

    A part's own name is evidence (`BodyShell`, `Roof`, `Fairing`), and a brand or model name is a
    design brief. The stronger vocabulary wins; a tie or no evidence is mechanical.
    """
    words = _words(text)
    labels = _words(" ".join([*(names or ()), *(type_ids or ())]).replace("_", " "))
    aesthetic = set(_hits(words, AESTHETIC)) | set(_hits(labels, AESTHETIC))
    mechanical = set(_hits(words, MECHANICAL)) | set(_hits(labels, MECHANICAL))
    aesthetic |= set(_hits(words | labels, BRANDS))
    a, m = len(aesthetic), len(mechanical)
    intent = "aesthetic" if a > m else "mechanical"
    confidence = round(abs(a - m) / (a + m), 2) if a + m else 0.0
    return {"intent": intent, "confidence": confidence,
            "matched": sorted(aesthetic if intent == "aesthetic" else mechanical),
            "required": required(intent)}


def assess(stats, intent, bar=None):
    """The verdict and the fix for one part's face mix, in words a person would use."""
    faces = int(stats.get("faces") or 0)
    bar = required(intent, faces) if bar is None else bar
    out = {"intent": intent, "required": bar}
    if not faces:
        return {**out, "verdict": "unknown", "advice": []}
    planar_share = stats.get("planar", 0) / faces
    out["planar_share"] = round(planar_share, 3)
    if bar is None:
        return {**out, "verdict": "not required", "advice": []}
    problems = []
    if planar_share > bar["max_planar_share"]:
        problems.append(
            f"{stats.get('planar', 0)} of {faces} faces are flat ({planar_share:.0%}): this reads as a block "
            f"model. Build the main surfaces as lofts or sweeps (Part.makeLoft across 20+ stations) and keep "
            f"boxes out of the shell.")
    if stats.get("curved", 0) < bar["min_curved_faces"]:
        problems.append(
            f"Only {stats.get('curved', 0)} curved faces: an organic shape needs real curvature, not a "
            f"chamfer on a box.")
    if stats.get("valid") is False:
        problems.append("The shape is not a valid solid; fix that before judging its form.")
    return {**out, "verdict": "fails" if problems else "pass", "advice": problems}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_shape_intent.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add cadswarm/shape_intent.py tests/test_shape_intent.py
git commit -m "feat(shape): infer whether a design is judged on form or function"
```

---

### Task 2: Worker face-mix query

**Files:**
- Modify: `cadswarm/worker/freecad_worker.py` (add `q_surface_quality` before `QUERIES`, register it)
- Modify: `rules/src/lib.rs` (add `surface_quality` to `QUERIES`)
- Test: `tests/test_worker_queries.py` (append), `rules/src/lib.rs` test module

**Interfaces:**
- Consumes: nothing from Task 1 (the worker reports numbers only).
- Produces: worker op `surface_quality({names: [str]}) -> {"objects": [{"name", "label", "faces", "planar", "curved", "bspline", "cylindrical", "conical", "spherical", "toroidal", "extruded", "revolved", "edges", "vertices", "solids", "valid", "volume_mm3", "bbox"}]}`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_worker_queries.py`:

```python
def test_surface_quality_counts_flat_against_curved_faces(worker):
    r = worker.call("surface_quality", {"names": ["Small"]})["result"]["objects"][0]
    assert r["name"] == "Small" and r["faces"] == 6 and r["planar"] == 6 and r["curved"] == 0
    assert r["valid"] is True and r["bbox"][3] - r["bbox"][0] == 20

def test_a_lofted_shell_has_curved_faces(worker):
    worker.call("run_part_script", {"name": "Shell", "code": (
        "sections = [Part.Wire(Part.makeCircle(20 + i, Vector(0, 0, i * 10))) for i in range(6)]\n"
        "shape = Part.makeSolid(Part.makeLoft(sections, True))")})
    r = worker.call("surface_quality", {"names": ["Shell"]})["result"]["objects"][0]
    assert r["curved"] > 0 and r["planar"] < r["faces"]
    assert worker.call("surface_quality", {"names": ["Nope"]})["code"] == "not_found"
```

Add to the `rules` test module:

```rust
#[test]
fn surface_quality_is_a_read_only_query() {
    assert!(is_query("surface_quality"));
    assert!(!is_write("surface_quality"));
    assert!(check_op("surface_quality", &json!({"names": ["Body"]}), &[], |_| None, |_| true, 1).is_ok());
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_worker_queries.py -k surface -v`
Expected: FAIL with `unknown_op: Unknown op 'surface_quality'`

Run: `cargo test -p cadswarm-rules surface_quality`
Expected: FAIL, `surface_quality` is not a query

- [ ] **Step 3: Write minimal implementation**

In `cadswarm/worker/freecad_worker.py`, above `QUERIES`:

```python
def q_surface_quality(a):
    """How much of each part's surface is flat, and how much is real curvature.

    The reviewer and the MCP tool use this to tell a boxy part (allowed) from a blocky attempt at
    an organic one (not allowed). Numbers only: the judgement lives in cadswarm/shape_intent.py.
    """
    want = set(a.get("names") or [])
    out = []
    for o in doc.Objects:
        sh = getattr(o, "Shape", None)
        if sh is None or sh.isNull():
            continue
        if want and o.Name not in want and o.Label not in want:
            continue
        kinds = {}
        for f in sh.Faces:
            k = f.Surface.__class__.__name__
            kinds[k] = kinds.get(k, 0) + 1
        faces = len(sh.Faces)
        planar = kinds.get("Plane", 0)
        bb = sh.BoundBox
        out.append({
            "name": o.Name, "label": o.Label, "faces": faces, "planar": planar,
            "curved": faces - planar, "bspline": kinds.get("BSplineSurface", 0),
            "cylindrical": kinds.get("Cylinder", 0), "conical": kinds.get("Cone", 0),
            "spherical": kinds.get("Sphere", 0), "toroidal": kinds.get("Toroid", 0),
            "extruded": kinds.get("SurfaceOfExtrusion", 0),
            "revolved": kinds.get("SurfaceOfRevolution", 0),
            "edges": len(sh.Edges), "vertices": len(sh.Vertexes), "solids": len(sh.Solids),
            "valid": bool(sh.isValid()), "volume_mm3": round(sh.Volume, 3),
            "bbox": [round(v, 3) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)],
        })
    if not out:
        raise OpError("not_found", f"No solid geometry in {sorted(want) or 'the model'}")
    return {"objects": out}
```

and register it:

```python
QUERIES = {"status": q_status, "get_object": q_get_object, "brep": q_brep,
           "interference": q_interference, "export": q_export,
           "param_users": q_param_users, "dependents": q_dependents,
           "faces": q_faces,
           "surface_quality": q_surface_quality,
           "mesh": q_mesh, "printability": q_printability,
           "analyze_component": q_analyze_component,
           "ping": lambda a: {"pong": True, "freecad": FreeCAD.Version()[:3],
                              "script_sandbox": sandbox.info(sys.executable)}}
```

In `rules/src/lib.rs`, in `QUERIES`:

```rust
    // How flat vs curved each part is: an aesthetic shape may not be all planes.
    "surface_quality",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_worker_queries.py -k surface -v`
Expected: PASS (2 tests)

Run: `cargo test -p cadswarm-rules surface_quality`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cadswarm/worker/freecad_worker.py rules/src/lib.rs tests/test_worker_queries.py
git commit -m "feat(worker): surface_quality reports the flat-to-curved face mix"
```

---

### Task 3: The MCP tool that turns numbers into a verdict

**Files:**
- Modify: `cadswarm/mcp_server.py` (add the tool after `list_faces`)
- Modify: `cadswarm/space_hub.py` (route `/surface`, method)
- Test: `tests/test_mcp_space.py`

**Interfaces:**
- Consumes: `surface_quality` worker query (Task 2), `classify`/`assess` (Task 1).
- Produces: `surface_quality(names=None, topic="", intent=None) -> {"intent", "objects", "failing", "advisory"}` where each object has the worker fields plus `verdict`, `advice`, `planar_share`, `required`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mcp_space.py`:

```python
def test_surface_quality_composes_the_verdict_from_the_topic(monkeypatch, session):
    session.client.post = lambda path, body=None: {
        "objects": [{"name": "BodyShell", "faces": 194, "planar": 146, "curved": 48, "valid": True}]}
    got = mcp_server.surface_quality(["BodyShell"], topic="a Lexus IS350 body")
    assert got["intent"] == "aesthetic"
    assert got["failing"] == ["BodyShell"]
    assert got["objects"][0]["verdict"] == "fails"
    assert "block model" in got["objects"][0]["advice"][0]


def test_surface_quality_leaves_a_bracket_alone_and_takes_an_override(monkeypatch, session):
    session.client.post = lambda path, body=None: {
        "objects": [{"name": "Bracket", "faces": 6, "planar": 6, "curved": 0, "valid": True}]}
    assert mcp_server.surface_quality(["Bracket"], topic="a mount bracket")["failing"] == []
    got = mcp_server.surface_quality(["Bracket"], topic="a mount bracket", intent="aesthetic")
    assert got["intent"] == "aesthetic" and got["failing"] == ["Bracket"]
    assert mcp_server.surface_quality(intent="sideways")["code"] == "bad_args"
```

Note: `RecordingHub.post` records and returns `{"ok": True}`; the lambda replaces it, so reset in each test.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_space.py -k surface_quality -v`
Expected: FAIL with `AttributeError: module 'cadswarm.mcp_server' has no attribute 'surface_quality'`

- [ ] **Step 3: Write minimal implementation**

In `cadswarm/mcp_server.py`, after `list_faces`:

```python
@mcp.tool()
def surface_quality(names: list[str] | None = None, topic: str = "", intent: str | None = None) -> dict:
    """Whether a part reads as smooth or as a block model, and what to do about it.

    `topic` is what the person asked for in their words (pass the request, not a summary): it decides
    whether the part is judged on form (car body, helmet, fairing, product exterior) or on function
    (bracket, plate, table, enclosure). Boxy parts are allowed to be boxy and come back `not required`.
    For an aesthetic part, `fails` means more than 60 % of the faces are flat or there are too few
    curved faces: build the main surfaces as lofts/sweeps in run_part_script and fix it before
    reporting done. `intent` ("aesthetic" or "mechanical") overrides the classifier for this call.
    """
    if intent not in (None, "aesthetic", "mechanical"):
        return {"error": "intent must be aesthetic or mechanical", "code": "bad_args"}
    r = call("POST", "/surface", {"names": names or []}, doing="checking surface quality", targets=names)
    if "error" in r:
        return r
    from .shape_intent import assess, classify
    chosen = intent or classify(topic or _current_brief_text(), names or [])["intent"]
    objects = [{**o, **assess(o, chosen)} for o in r["objects"]]
    return {"intent": chosen, "objects": objects,
            "failing": [o["name"] for o in objects if o["verdict"] == "fails"],
            "advisory": chosen == "aesthetic"}
```

and above the tool:

```python
def _current_brief_text():
    """The request of a brief that is still running, so the classifier sees the person's words."""
    try:
        rows = S.client.get("/briefs")
    except HubRequestError:
        return ""
    for b in (rows.get("briefs") if isinstance(rows, dict) else None) or []:
        if isinstance(b, dict) and b.get("status") not in ("ready", "failed", "cancelled"):
            return str(b.get("text") or "")
    return ""
```

In `cadswarm/space_hub.py`, add the route next to the style routes:

```python
            ("POST", r"/surface", self.surface_quality),
```

and the method next to `style_get`:

```python
    def surface_quality(self, body):
        return self.run_op("surface_quality", {"names": body.get("names") or []})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_space.py -k surface_quality -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add cadswarm/mcp_server.py cadswarm/space_hub.py tests/test_mcp_space.py
git commit -m "feat(mcp): surface_quality turns the face mix into a verdict and a fix"
```

---

### Task 4: Make the bar reach every agent, including OpenCode

**Files:**
- Modify: `cadswarm/mcp_server.py` (`INSTRUCTIONS`, new item 15)
- Modify: `host/src/agents.rs` (`review_prompt`, new step; renumber style)
- Modify: `cadswarm/agent_setup.py` (`AGENT_DOC`)
- Modify: `cadswarm/prompt_hook.py` (aesthetic hint for Claude/Codex)
- Test: `host/src/agents.rs` test module, `tests/test_setup.py`, `tests/test_prompt_hook.py` (create)

**Interfaces:**
- Consumes: the `surface_quality` tool (Task 3).
- Produces: no API change; prompt text only.

- [ ] **Step 1: Write the failing tests**

In `host/src/agents.rs` tests:

```rust
#[test]
fn the_reviewer_prompt_checks_the_shape_of_an_aesthetic_brief() {
    let p = review_prompt("b_x", "model a car body", "", [200.0, 200.0, 200.0], 1, "- Car bodies are lofted.");
    assert!(p.contains("surface_quality"), "{p}");
    assert!(p.contains("organic") && p.contains("flat faces"), "{p}");
    assert!(p.contains("6. Team design style"), "the style step still follows: {p}");
}
```

Create `tests/test_prompt_hook.py`:

```python
"""The prompt hook nudges Claude and Codex towards real surfaces (OpenCode reads AGENTS.md)."""
from cadswarm.prompt_hook import aesthetic_hint


def test_a_shape_request_gets_the_surface_hint():
    hint = aesthetic_hint("make the Lexus IS350 F Sport body look right, it is all flat")
    assert "surface_quality" in hint and "loft" in hint


def test_a_bracket_gets_no_hint():
    assert aesthetic_hint("a bracket for the shelf") == ""
```

In `tests/test_setup.py`, add:

```python
def test_opencode_agents_md_carries_the_shape_rule(tmp_path):
    from cadswarm.agent_setup import AGENT_DOC
    assert "surface_quality" in AGENT_DOC
```

(Adjust the import/style to the file's existing helpers if it builds `AGENT_DOC` through a function.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test -p cadswarm-host the_reviewer_prompt_checks_the_shape`
Expected: FAIL, the prompt does not mention `surface_quality`

Run: `python -m pytest tests/test_prompt_hook.py tests/test_setup.py -k "prompt_hook or shape_rule" -v`
Expected: FAIL with `ImportError: cannot import name 'aesthetic_hint'`

- [ ] **Step 3: Write minimal implementation**

`host/src/agents.rs`, set the style step to 6 and insert step 5:

```rust
    let style_check = if style.trim().is_empty() {
        String::new()
    } else {
        format!("\n6. Team design style: the rules below are how this team designs. Each part that breaks one is a \
                 problem; write its `problem` as \"style: <the rule> (<what the part does instead>)\".\n{style}\n")
    };
```

and after step 4 in the format string:

```rust
        5. Shape: if the brief is for an object judged on how it looks (car, vehicle, body, helmet, fairing,\n\
            furniture or a product exterior), call surface_quality(topic=\"<the person's request>\")\n\
            on the shell and the greenhouse. A `fails` verdict is a problem: write it as\n\
            \"style: organic shape built from flat faces (<n> of <m> faces are flat)\". A separate box roof\n\
            or box pillars on a body are problems too. Boxy parts (brackets, plates, enclosures) are fine.\n\
```

`cadswarm/mcp_server.py` `INSTRUCTIONS`, after item 14:

```
15. Shape quality. Some parts are judged on how they look (a car body, fairing, helmet, furniture, product
    exterior) and some on what they do (brackets, plates, enclosures). Before you call an aesthetic part
    done, call surface_quality(topic="<what the person asked for>", names=[...]). `fails` means the part
    reads as a block model: build its main surfaces as lofts or sweeps in run_part_script (Part.makeLoft
    across 20+ stations), keep separate boxes out of the shell, and fix it before reporting done. A boxy
    part needs none of this; call it anyway if you are unsure.
```

`cadswarm/agent_setup.py` `AGENT_DOC`, one line in the modelling section:

```
- A shape judged on how it looks (car body, fairing, helmet, product exterior) must be lofted, not
  extruded: call surface_quality before finishing it. A bracket or a plate is allowed to be a box.
```

`cadswarm/prompt_hook.py`:

```python
# Words that usually mean "this is judged on how it looks". A false positive only adds a short reminder.
SHAPE_WORDS = re.compile(r"\b(car|vehicle|sedan|coupe|motorcycle|boat|hull|aircraft|fuselage|fairing|helmet|"
                         r"body|bodywork|exterior|bonnet|hood|fender|bumper|grille|spoiler|dashboard|chair|"
                         r"furniture|shoe|toy|figurine|bottle|vase|looks? (wrong|bad|terrible|cheap|flat)|"
                         r"boxy|slab[- ]sided|blocky|proportions?|curvy|smooth|realistic|like the real)\b", re.I)
SHAPE_HINT = ("\n## This looks like a shape judged on how it looks\n"
              "Before calling it done, call surface_quality(topic=<the person's request>) on the shell and "
              "the greenhouse: `fails` means it reads as a block model. Build the main surfaces as lofts or "
              "sweeps in run_part_script (Part.makeLoft across 20+ stations), not extrusions, and keep box "
              "roofs and box pillars out of a body. A bracket or plate is allowed to be a box.")


def aesthetic_hint(prompt):
    return SHAPE_HINT if isinstance(prompt, str) and SHAPE_WORDS.search(prompt) else ""
```

and append it in `main()`: `hint = fea_hint(prompt) + library_hint(prompt) + lattice_hint(prompt) + aesthetic_hint(prompt)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cargo test -p cadswarm-host`
Expected: PASS

Run: `python -m pytest tests/test_prompt_hook.py -v && python -m pytest tests/test_setup.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cadswarm/mcp_server.py host/src/agents.rs cadswarm/agent_setup.py cadswarm/prompt_hook.py tests/test_prompt_hook.py tests/test_setup.py
git commit -m "feat(review): an organic shape made of flat faces is a finding"
```

---

### Task 5: Docs and the full gate

**Files:**
- Modify: `README.md` (a short section next to "Learned design style")
- Modify: `tests/test_setup.py` if the README test counts words (check `scripts/check_docs.py` first)

- [ ] **Step 1: Write the failing doc check**

Run: `python scripts/check_docs.py`
Expected: PASS today; after the README edit it must still pass (no placeholder links).

- [ ] **Step 2: Add the README section**

After the Learned design style section:

```markdown
### Shapes judged on how they look

A bracket is allowed to be a box; a car body is not. Before a helper finishes a shape that is judged
on how it looks, `surface_quality` reports how much of it is flat and whether that reads as a block
model, and the reviewer treats a fully faceted organic shape as a finding. The bar is inferred from
the request and the part names, so tables, plates and enclosures are never punished for being boxy.
```

- [ ] **Step 3: Run everything**

Run: `python -m pytest -q`
Expected: PASS (722 + the new tests; FreeCAD tests included when FreeCAD is installed)

Run: `cargo test -p cadswarm-rules -p cadswarm-host`
Expected: PASS

Run: `python scripts/check_docs.py`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: shapes judged on how they look"
```

---

## Self-review

- **Spec coverage (phase 1):** intent (§3) → Task 1; metric (§4) → Task 2; verdict/advice (§4) → Task 3; bar in
  instructions/reviewer/AGENTS.md/hook (§5) → Task 4; docs → Task 5. Feedback, references, renders and the
  IS350 rebuild are phases 2–4 and are intentionally absent.
- **Type consistency:** `assess()` consumes exactly the keys `q_surface_quality` emits (`faces`, `planar`,
  `curved`, `valid`); `surface_quality` merges them with `**`; the tool's `names`/`topic`/`intent` are the
  names used in the instructions.
- **No placeholders:** every step has the code or the exact text to insert.
