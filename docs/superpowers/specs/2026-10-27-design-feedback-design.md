# Design feedback that teaches: verdicts, references and shape quality

**Date:** 2026-10-27
**Status:** draft for review

## Problem

CADSwarm teaches style from ops and review findings, but nothing captures a person's judgement of
what the model *looks like*. In the Lexus IS350 project (34 objects, real Params) the shell has 194
faces, 146 of them flat planes, the roof is a 6-face box and the pillars are six boxes. The learning
system has zero aesthetic feedback, so it cannot learn "that reads as a box", and the agents have no
picture of their own work: a text model building a car body never sees it, so it picks the cheapest
geometry that satisfies the numbers.

Two things are missing:

1. **No way to say "this looks wrong, here's why"** in a form the system can learn from, and no way
   to attach the reference image that shows what it should look like.
2. **No quality bar for organic shapes.** A bracket is meant to be boxy; a car is not. Nothing
   distinguishes them, so the reviewer passes both and the planner reaches for extrusions.

## Goals

- Capture a verdict (bad / mixed / good), the specific issues in the person's own words, tags,
  target objects and optional images, in one tool call from chat.
- Attach geometry evidence automatically, so "bad" is comparable across designs: face mix, planar
  share, curved/BSpline counts, bounding box against Params, Params themselves.
- Learn from it with the existing `style_memory` loop (events → distil → gate → recall), including
  what was *accepted*, not only what was rejected.
- Store reference images per subject in the project, versioned and quota-counted like any other file,
  and give agents their paths so they can look at them.
- Let every agent *see* the model: orthographic renders they can open, at a known scale.
- Judge shape quality by intent that the system infers, not by one rule for everything: a table is
  allowed to be planar, a car body is not.
- Rebuild the IS350 as a 1:1 2021 IS 350 F Sport and prove the loop works on it.

## Non-goals

- Training or fine-tuning a model. Learning stays deterministic, local, in SQLite.
- Image understanding (CV) to compare a render with a photo automatically. A person and an agent with
  eyes do the comparing.
- New host geometry ops (`create_loft`, `create_sweep`). `run_part_script` already builds lofts and
  the recipe ships the proven script.
- Automatic mesh repair or surface analysis beyond face-type statistics.
- Changing the panel's existing Learned-style dialog beyond one new section label.

## Design

### 1. Verdict and references

Two new tools, both usable from chat:

```
record_feedback(verdict, issues?, note?, objects?, tags?, images?, subject?, by?)
  verdict  bad | mixed | good
  issues   short phrases in the person's words: ["roof is a box", "too slab-sided"]
  tags     proportions | surfacing | detail | panels | fit | color
  objects  object names; default the whole model
  images   project paths already saved, or nothing
  subject  e.g. "car-exterior"; default: derived from the brief text, else "model"
  by       person | helper   (helper = the agent's own opinion, not a person's words)

add_reference(subject, image_path?, notes?, dimensions?, source_url?, label?)
list_references(subject?)
```

- `record_feedback` copies the geometry evidence at that moment (`surface_quality` per object, all
  Params, brief id, model revision) into the event, so a later rule can say "rejected at 75 % planar".
- Images named in `images` are copied into `reference/<subject>/feedback-<id>/` so they survive the
  original file being changed; the originals stay untouched.
- `add_reference` reads a local file path with the MCP server's own filesystem access (it runs on the
  person's machine), accepts PNG/JPEG/WebP up to 20 MB, and writes it through `ProjectFiles` so it is
  versioned, attributed and counted against quota. A URL is stored as metadata; CADSwarm does not
  download it.
- `list_references(subject)` returns `{subject, label, notes, dimensions, image, added_by, at}` with
  project-relative paths. The agent opens `image` with its own Read tool.
- An agent may only relay what its person actually said. `by` defaults to `person`; an agent that
  is giving its own opinion must pass `by="helper"`, which is stored as a proposal and never counts
  as active evidence (same principle as `teach_style`).
- `subject` defaults to the first vocabulary noun matched in the brief (e.g. `car`, `helmet`,
  `table`), lowercased and hyphenated; if nothing matches it is `model`. A caller may always set it.
- Returns `{id, subject, verdict, evidence: {objects, surface_quality, params}, learned: {rules,
  profile}}` so the agent can immediately say what was recorded and what it will change.

### 2. What the feedback teaches

Feedback is a new `feedback` event in `style_events`; no new database. `distil()` gains three rule
types, all through the existing confidence gate and inbox:

| Rule type | Built from | Statement (deterministic) |
| --- | --- | --- |
| `feedback_theme` | one normalized issue (reuse `normalize_problem`), grouped by subject | `Rejected on car-exterior 3×: "roof is a box". Design to avoid it.` |
| `quality_standard` | bad/mixed verdicts where `planar_share >= max_planar_share` of an aesthetic subject | `This team rejects faceted shells on car-exterior: keep flat faces under 60 % and loft the main surfaces (seen 3×).` |
| `accepted_approach` | good verdicts on an aesthetic subject, with the approach's metrics | `Accepted on car-exterior: lofted shell with 41 % flat faces and 38 curved faces (seen 2×).` |

- Evidence counts distinct verdict ids, and `people` counts `by="person"` only, so one person
  clicking twice on the same model is one occasion.
- Subjects: `subject:<slug>` for the design kind, `object:<type_id>` when the verdict names one part,
  `team` when no subject is known.
- `recommend()` treats `feedback_theme` and `quality_standard` like `recurring_problem`: `confirm`
  at `n >= 4`, `dismiss` at `n <= 2`.
- `SECTION_LABELS` gains `feedback_theme` / `quality_standard` / `accepted_approach` →
  **"Feedback"**. The dialog already renders sections generically.
- `context()` includes the rules as usual, so the planner, the reviewer's `style_block` and every
  `team_style` call see them. Verbatim issues live in the rule's `value`, so the prompt can quote the
  person instead of a paraphrase.

### 3. Shape intent, inferred

`cadswarm/shape_intent.py` (stdlib, no model call):

```
classify(text, names=(), type_ids=()) ->
  {intent: "aesthetic" | "mechanical", confidence: 0..1, matched: [...],
   required: {max_planar_share: 0.6, min_curved_faces: 8}}
```

- Two scored vocabularies: **organic/aesthetic** (car, vehicle, sedan, coupe, motorcycle, boat, hull,
  aircraft, fuselage, helmet, furniture, seat, shoe, toy, figurine, phone, mouse, headphone, bottle,
  vase, lamp, appliance, drone shell, fairing, bodywork, exterior, hood, fender, bumper, spoiler,
  grille, mirror housing, dashboard, brand and model names, "looks real", "smooth", "curvy") and
  **mechanical** (bracket, plate, mount, clamp, spacer, adapter, jig, fixture, frame, table, shelf,
  enclosure, PCB, standoff, flange, beam, hinge, boss, rib). Each hit scores; the higher score wins;
  a tie or no hits means **mechanical**, because a wrongly-boxy bracket is cheap and a wrongly-smooth
  bracket wastes a build.
- An object name that matches a known part role (e.g. `BodyShell`, `Roof`, `Pillar`, `Hood`,
  `Fender`, `Bumper`, `Grille`, `Fairing`, `Hull`) also counts.
- `required` for aesthetic: `max_planar_share 0.6`, `min_curved_faces max(2, 10 % of faces)`. For
  mechanical: no ceiling, but the tool still reports the mix so a person can see it. The floor is
  proportional on purpose: a six-face curved glass panel must not be asked for a car body's
  curvature, and a 400-face shell must not pass on four curved faces.
- The classifier can be overruled: `surface_quality(..., intent="aesthetic" | "mechanical")`. Every
  override is stored (`intent_override` event with the brief text, names and the chosen intent), so
  the vocabulary can be improved from real disagreements. No automatic learning of the classifier.

### 4. Surface quality and renders

Worker-side metric (FreeCAD shape, no extra dependency):

```
surface_quality(names) -> per object:
  {faces, planar, planar_share, bspline, cylindrical, conical, spherical, toroidal, curved,
   edges, vertices, solids, valid, volume_mm3, bbox,
   intent, required, verdict: "pass" | "fails" | "not required",
   advice: ["146 of 194 faces are flat: this is an extruded slab; loft cross-sections instead."]}
```

The MCP server composes it: geometry stats from the host, intent from the helper's classifier (or the
caller's override), verdict locally. Advice is plain language and names the fix.

`render_views(names?, views?, size?, deviation?)` produces PNGs through the same worker mesh the web
viewer already uses, rasterised in pure Python (z-buffer, flat Lambert shading, opaque background,
scale bar). Default views: `front`, `rear`, `left`, `right`, `top`, `three_quarter`; orthographic by
default so a side view at a known scale can be laid over a reference photo. Output goes to
`renders/<timestamp>/<view>.png` on the host, like `exports/`: regenerable, so it is not versioned or
counted against the file quota. The tool returns the paths; the agent opens them, and a person on the
host machine can too.

### 5. Where the bar reaches the agents

- **MCP `INSTRUCTIONS`** (read by Claude, Codex *and* OpenCode at connect): a new step —
  *before you call an organic/aesthetic part done, `shape_intent` and `surface_quality` it; a car
  body, fairing, helmet or product exterior must be lofted, not extruded; look at `render_views` and
  fix what is obviously wrong; when your person says it looks wrong, relay their words with
  `record_feedback`, add their photos with `add_reference`.*
- **Reviewer prompt** (`host/src/agents.rs`): step 6 — *if the brief asks for an aesthetic/organic
  object, call `shape_intent` and `surface_quality` on the shell and the greenhouse; a fully faceted
  shell is a problem (`style: organic shape built from flat faces`), and so is a separate box roof.*
- **Generated `AGENTS.md`** (`agent_setup.AGENT_DOC`): the same rule in one line, because OpenCode
  has no prompt hook.
- **`prompt_hook.py`**: detects critique words ("looks terrible", "boxy", "flat", "toy", "cheap",
  "proportions", "like the real thing") and reminds Claude/Codex helpers to record it with the
  person's exact words and attach images.
- **`team_style` / planner `style_block`**: learned feedback rules, unchanged mechanism.

### 6. The IS350 rebuild (1:1 acceptance)

Measured targets for a 2021 IS 350 F Sport (RWD, mirrors folded):

| Dimension | Target | Current Params |
| --- | --- | --- |
| Length | 4710 mm | 4710 ✓ |
| Width | 1840 mm | 1840 ✓ |
| Height | 1435 mm | 1435 ✓ |
| Wheelbase | 2800 mm | 2800 ✓ |
| Front track | 1580 mm | 1580 ✓ |
| Rear track | 1606 mm | 1590 ✗ |
| Ground clearance | 140 mm | 130 ✗ |
| Front overhang | 885 mm (derived: 2016–20 official 845 + 2021 front bumper growth; ±15 mm until reference photos confirm) | 935 ✗ |
| Rear overhang | 1025 mm | 975 ✗ |
| Front tyre | 235/40R19, OD 670.6 | ✓ |
| Rear tyre | 265/35R19, OD 668.1 | ✓ |
| Wheels | 19 × 8.5 front, 19 × 9.5 rear, F Sport split-five-spoke | 19 in, widths ✓ |

Shape targets:

- `BodyShell`: lofted from ≥ 20 stations along X, planar share ≤ 35 %, ≥ 20 BSpline faces, valid
  solid, no self-intersection.
- Greenhouse: one lofted shell + tapered A/B/C pillars, curved windshield and rear glass set into
  openings, tumblehome in the side glass; no box roof, no box pillars.
- Interfaces preserved from `files/parts/body/notes.md`: hood cut at `hood_front_x`/`hood_front_z`,
  cowl and deck planes, door openings `door_f_x0..door_r_x1` at `rocker_z`, arch radii
  `tire_od/2 + arch_gap` with flared lips, headlight and taillight pockets, rocker tuck.
- F Sport cues kept: spindle grille + F mesh, slim headlamps with L-shaped DRL, shoulder character
  line that runs into the taillight, ducktail lip, diffuser, F badges, staggered 19s.

Proof: a dimension report (bbox, wheelbase, tracks, tyre ODs, height) that matches the table, side
and front orthographic renders at 1:10 overlaid on the person's reference photos, `surface_quality`
passing, `check_interference` clean, and all existing parts still in place.

Sequence on the live project: `save_snapshot` → stop host and helpers → correct Params → rebuild
shell and greenhouse with the recipe script → refit bumpers/grille/hood/trunk/wheels → validate →
restart host with `agent_client: opencode` → relaunch helpers (they must restart to load the new MCP
tools) → record the first real feedback through the new loop.

## Architecture and API

Everything follows an existing seam; no new service.

| Piece | Where it lives | Existing pattern |
| --- | --- | --- |
| `record_feedback`, `add_reference`, `list_references` | op kinds in `rules/` (non-mutating, no locks), dispatched by the host executor to `helper.py` | `teach_style`, `lookup_board` |
| `surface_quality`, `render_views` | host worker queries on the FreeCAD worker | `list_faces` (`POST /fea/faces`) |
| `shape_intent` | pure Python in `cadswarm/shape_intent.py`; the MCP server imports it directly (same machine, no op) | `cadswarm/knowledge.py` helpers |
| Tool wrappers | `mcp_server.py` tools + `space_hub.py` routes | `list_faces`, `team_style` |

MCP tools: `record_feedback`, `model_feedback`, `add_reference`, `list_references`,
`surface_quality`, `render_views`, `shape_intent`.

Reviewers are read-only members: feedback from a reviewer identity is refused at the rules layer
(`is_write` stays false for these kinds so a reviewer can *read* references, but the module refuses
a reviewer's `record_feedback`), and `surface_quality` is always allowed because it only inspects.

## Delivery order

Each phase is usable on its own; later ones depend on the earlier ones only where stated.

1. **Intent + metric** — `shape_intent.py`, `surface_quality` worker query and tool, reviewer and
   instruction text. This alone stops the reviewer passing a box car.
2. **Feedback + references** — the event kind, the three rule types, the three tools, image storage.
   This makes the loop learn from the IS350 rebuild.
3. **Renders** — the rasteriser and `render_views`. This is what lets a text-only helper see its work;
   it also gives the IS350 proof images.
4. **IS350 rebuild** — needs 1 and 3 to validate, and 2 to record the first verdicts.

## Failure modes

- **Agent records its own taste as the person's.** `by` is explicit; helper feedback never gates to
  active without a person confirming it in the inbox.
- **Classifier calls a bracket a car.** Default is mechanical; overrides are logged with the text and
  can be corrected; `surface_quality` always reports the numbers even when no bar applies.
- **A person's "bad" is about something the metric can't see** (e.g. panel gaps). Tags and issues are
  stored verbatim and become `feedback_theme` rules even when the metrics look fine.
- **Renders are slow on a heavy model.** Triangle budget and image size are capped; a timeout returns
  the views already written and says which ones were skipped.
- **Editing the live model while agents work.** The rebuild runs with the host stopped, after a
  snapshot; the README's warning is the contract.
- **Reference images blow the quota.** 20 MB per image, counted by the existing storage accounting,
  and the error tells the person what to free.

## Testing

- `shape_intent`: table tests (car body aesthetic, table/enclosure/bracket mechanical, mixed text,
  overrides, name-based roles), no network.
- `surface_quality`: a box scores 100 % planar and fails aesthetic; a lofted shell passes; a
  mechanical object is `not required`; validity and bbox are reported.
- Feedback: verdict + issues + images round-trip; evidence counts people and occasions correctly;
  distil produces `feedback_theme`, `quality_standard` and `accepted_approach`; gating, dismissal,
  revival and inbox section; a helper's verdict cannot become active alone.
- References: save via `ProjectFiles`, path traversal refused, oversized/unsupported file refused,
  listing by subject, feedback image copy survives the original being replaced.
- Renders: a known box renders to a PNG with the expected solid-colour coverage; orthographic side
  view of a 100 mm cube measures 100 px at 1 px/mm; time budget respected.
- Host: reviewer prompt contains the aesthetic step only when the brief is aesthetic; existing tests
  unchanged.
- Full pytest (currently 722) and `cargo test` for the host.

## Docs

README (a short "It learns your eye, not just your numbers" section next to Learned design style),
`agents` chapter of the website, and `files/parts/body/notes.md` in the IS350 project with the final
dimension table and the recipe's provenance.
