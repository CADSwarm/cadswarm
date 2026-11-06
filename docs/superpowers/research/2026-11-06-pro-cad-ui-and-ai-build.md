# Research: professional CAD UI conventions and AI step-by-step build methods

Date: 2026-11-06. Companion to `docs/superpowers/specs/2026-11-06-step-build-and-pro-ui-design.md`.
Scope: what top CAD tools share in layout and interaction, and how AI text-to-CAD systems break a design
into ordered, checked steps. Sources are linked inline. The recommendations at the end use generic CAD terms
only. They name no vendor and copy no vendor's look.

Note on "extrudo": web searches found nothing public under that name, so the reference behaviour comes
from the spec's own description: a numbered plan, one component per step, a per-step op feed with timings,
then mates, then a motion study. Below, the comparable published systems are CADSmith, ArtiCAD, ASSEMCAD,
CAD-Assistant, and "From Idea to CAD".

---

## 1. Professional CAD UI conventions

### 1.1 Layout regions (shared across products)

| Region | SolidWorks | Onshape | Fusion | NX / Solid Edge / CATIA |
|---|---|---|---|---|
| Top bar | Menu + quick access (New/Open/Save/Undo/Rebuild/Options) | Document bar; collaborator cues top-right | Application bar: file, save, undo/redo, doc tabs, assistant, job status, help, profile | Quick access toolbar above/below ribbon; CATIA top bar with profile top-right |
| Command area | **CommandManager**: tabbed ribbon, large icon + text below, groups split by separators, flyouts | Feature toolbar: icons, vertical separators between groups, drop-down groups; the last-used tool moves to the top of its group; collapses with zoom/resolution | Tabbed toolbar per workspace (Solid, Surface, Mesh, Sheet Metal, Utilities…); contextual tabs appear inside commands | NX ribbon (Home/View/…); CATIA **action bar** with sections that can be detached and pinned |
| Left pane | **FeatureManager tree** + tabs (PropertyManager, ConfigurationManager, DimXpert, DisplayManager) | Feature list (search box, origin + planes, features, rollback bar, separator, parts list) | Browser (components, bodies, sketches, joints, visibility eyes) | NX Resource bar / Part Navigator; Solid Edge PathFinder; CATIA specification tree |
| Property / command input | **PropertyManager** replaces the tree tab while a command runs | Floating feature dialog | Floating command dialog | Solid Edge command bar (floating or docked) |
| Viewport overlays | Heads-up view toolbar (transparent, top-centre), reference triad bottom-left | View cube top-right | ViewCube top-right; navigation bar bottom-centre (orbit/pan/zoom, display settings, grid); marking menu on right-click | CATIA robot (manipulator) bottom-right |
| Right pane | **Task Pane**: Resources, Design Library (incl. Toolbox standard hardware), File Explorer, Appearances/Scenes/Decals. Can be pinned or unpinned, docked or floating, and collapses on drag-to-viewport | Right-side panels (BOM, appearance, configurations) | Assistant chat panel; Data panel (projects) on the left | — |
| History | Tree order + rollback bar | Rollback bar in the feature list | **Timeline** strip along the bottom: right-click to edit, drag to reorder | Solid Edge: synchronous section and ordered section |
| Bottom | Status bar: units, editing context, rebuild state | Document tabs (Part Studio, Assembly…) | Timeline | — |

Sources: [SW PropertyManager](https://help.solidworks.com/2022/english/SolidWorks/sldworks/r_pm_overview.htm),
[SW Heads-up toolbar](https://help.solidworks.com/2022/English/SolidWorks/Sldworks/c_heads_up_view_toolbar.htm),
[SW Task Pane](https://help.solidworks.com/2025/English/SolidWorks/Sldworks/r_task_pane.htm),
[SW CommandManager customisation](https://www.cati.com/blog/exploring-the-solidworks-commandmanager/),
[Onshape UI basics](https://cad.onshape.com/help/Content/Home/user_interface_basics.htm),
[Onshape feature list](https://cad.onshape.com/help/Content/PartStudio/features_and_parts_lists.htm),
[Fusion interface](https://help.autodesk.com/cloudhelp/ENU/Fusion-GetStarted/files/GS-THE-FUSION-INTERFACE.htm),
[Solid Edge PathFinder / command bar](https://soliddna.com/SEHelp/ST5/EN/i_v/pthfndb2a.htm),
[Solid Edge ordered + synchronous](https://blogs.sw.siemens.com/solidedge/hybrid-modeling-in-solid-edge-with-syncronous-and-ordered-modes/),
[CATIA 3DEXPERIENCE UI](https://cati.com/?p=157309), [action bar sections](https://blog.3ds.com/brands/catia/3dexperience-catia-r2019x-action-bar-improvements-for-customization),
[NX resource bar](https://support.sw.siemens.com/en-US/okba/PL8787129/Creating-a-Customized-Tab-Layout-for-the-Resource-Bar/index.html).

### 1.2 Ribbon conventions
- Tabs follow the **workflow**, not object types: Features, Sketch, Evaluate (measure, mass properties,
  interference, section, draft/thickness analysis), Markup, plus optional Surfaces / Sheet Metal / Weldments
  tabs. Assembly documents swap in an Assembly tab (insert component, mate, move/rotate component,
  pattern, exploded view, interference) ([tab list notes](https://workforce.libretexts.org/Courses/Northeast_Wisconsin_Technical_College/Introduction_to_SolidWorks_(NWTC)/01%3A_SolidWorks_User_Interface/1.03%3A_Major_User_Interface_Components)).
- Large icon with label below is the default. A compact mode drops the labels. Related commands share
  one button as a split button with a flyout (for example Fillet / Chamfer, or Linear / Circular pattern).
- Tabs and toolbars change with context: sketch mode gets its own set of tabs, and a contextual
  "Finish sketch" tab appears.
- Command search in the title bar is standard (Onshape "Search tools", Fusion and NX command finder).

### 1.3 Feature tree row anatomy
- Each row has, from left to right: an expand caret, an icon for the feature type, the name (with the
  editable feature number), and state markers on the right. Default rows at the top are the origin, the
  three planes, and the sketches nested under the features that use them.
- **Rollback bar**: a horizontal bar you can drag through the tree. Features below it are suppressed and
  greyed out. The arrow keys move it one step. Context menus offer "Roll to here" and "Roll to end".
  Releasing a part can be blocked while the bar is not at the end
  ([Onshape](https://cad.onshape.com/help/Content/PartStudio/features_and_parts_lists.htm)).
- **Status markers**: an error shows a red X on the feature and red text, and the mark is repeated on the
  document's top node. A warning shows a yellow/amber exclamation mark. A down-arrow on a parent node
  means a child has an issue. Clicking a marker opens a "What's wrong" popover with a cause and a fix.
  Geometry is still created when there are only warnings, but not when there are errors
  ([SW What's Wrong](https://help.solidworks.com/2017/English/SolidWorks/sldworks/HIDD_NEW_WHATS_WRONG.htm)).
- Suppressed features show a grey icon and grey text. Hidden bodies show a closed eye. Rows that the
  current command references are highlighted.

### 1.4 Property panel anatomy
- Header: the command icon and name, then **OK (check), Cancel (X), Help (?), and Keep-visible pushpin**.
  The pin keeps the panel open after OK so the user can repeat the command, for example several mates in
  a row ([pin for mates](https://nexthelp.solidworks.com/2024/english/SolidWorks/sldworks/t_assemblies_mates_pushpininfo.htm)).
- Body: **collapsible group boxes** with titles, some of them opt-in with a checkbox (for example
  "Direction 2" or "Thin feature").
- **Selection boxes**: lists that take picks from the viewport or the tree. The active box is tinted.
  Hovering an item in a box highlights it in the viewport. In Onshape, a solid blue field means "pick in
  the viewport" and a blue outline means "type a value"
  ([Onshape](https://cad.onshape.com/help/Content/Home/user_interface_basics.htm)).
- Numeric fields take expressions and units ("10 mm", "=Width/2") and have spinners. Changes update a
  preview in the viewport live. There is a flip-direction toggle.

### 1.5 Viewport conventions
- A subtle vertical **gradient** background is the default (blue-grey at the top, near-white at the
  bottom). It can be set to a plain colour or an image
  ([SW colours](https://help.solidworks.com/2020/English/SolidWorks/sldworks/HIDD_OPTIONS_SYSTEM_COLORS.htm),
  [background how-to](https://www.javelin-tech.com/blog/2022/12/solidworks-background-colors/)).
- A **triad** sits bottom-left (X red, Y green, Z blue, a universal convention). A **view cube** sits
  top-right (click a face, edge or corner to go to that view; drag it to orbit).
- Heads-up toolbar commands: zoom to fit, zoom to area, previous view, section view, view orientation
  flyout (standard views plus saved views), display style flyout (shaded with edges, shaded, hidden lines
  removed, hidden lines visible, wireframe), hide/show items (planes, origins, sketches), appearance,
  scene, view settings (perspective, shadows).
- Selection uses **two distinct highlight colours**: a pre-selection (hover) highlight and a selection
  highlight. The selection scheme can be switched (blue, green or orange presets).
- Face, edge and vertex filters are available. A right-click shows a context or marking menu with the
  commands that fit the current selection.

### 1.6 Collaboration (Onshape is the reference)
- **Presence cues**: a coloured avatar or initial for each collaborator, top-right, with a count. Each
  document tab and the feature list show coloured marks for who is working where.
- A collaborator's **cursor** appears as a hand in their colour.
- **Follow mode**: double-click an avatar to follow. The follower's workspace gets an outline in that
  colour, and the followed person sees that they are being followed. The follower sees the active tab,
  view and render-mode changes, selections and most feature dialogs. A follower follows one person at a
  time, and clicking anywhere exits
  ([Onshape follow mode](https://cad.onshape.com/help/Content/Collaboration/follow_mode.htm)).
- The UI uses colour tokens, so light and dark themes flip cleanly
  ([forum](https://forum.onshape.com/discussion/comment/109013)).

### 1.7 Palette and typography (observed patterns, not vendor values)
- Chrome is light by default: off-white panels, a light-grey ribbon, 1px cool-grey borders, one saturated
  blue accent for active, selected and primary actions, red for errors and amber for warnings. A dark
  theme is offered as an option.
- Type is the system UI sans at 11–12px for ribbon labels and tree rows, 12–13px for panel text, and
  semibold 12px for group titles. Numeric fields use tabular figures.
- Density is high: tree rows are about 22–24px, and icons are 16px in the tree and 32px for large ribbon
  buttons.

---

## 2. AI text-to-CAD build methods

| System | Decomposition | Execution | Verification | Notes |
|---|---|---|---|---|
| **CADSmith** ([arXiv 2603.26512](https://arxiv.org/html/2603.26512v1)) | A planner writes a JSON design plan: components, target bounding-box dimensions, constraints (hole counts). **The planner writes no code.** | Coder → CadQuery, using retrieved API docs (155 method entries, 28 examples) | Inner loop: up to 3 traceback retries, with a database of 25 error→fix patterns. Outer loop: up to 5 geometry rounds using kernel metrics (bounding box, volume, face/edge/vertex counts; an invalid solid is a hard fail) **plus a vision judge on 3 renders** (isometric, high-angle rear, front profile). The judge is a **stronger, different model** to avoid self-confirmation. | Removing vision raised the hard-tier Chamfer distance from 1.42 to 49.68. The refiner gets the history of earlier attempts and is told to change strategy from round 3. A near-miss (small gaps between arms and hub) passed every check, so explicit contact checks are needed. |
| **ArtiCAD** ([arXiv 2604.10992](https://arxiv.org/abs/2604.10992)) | Design agent predicts **assembly relations and connectors before generating geometry** | Generation agent → parts; Assembly agent aligns connector pairs and applies joints (fixed, revolute, slider, cylindrical, ball) **deterministically in FreeCAD** | Review agent checks renders with a VLM. A **cross-stage rollback** sends design errors back to the design stage and code errors back to generation. An experience store grows across tasks. | Keeps the kinematics an **acyclic tree**, because LLMs cannot keep closed loops consistent. |
| **ASSEMCAD** ([arXiv 2607.05123](https://arxiv.org/abs/2607.05123)) | Assembly specification: typed parts, **geometry-backed ports**, executable mates, engineering axioms (each mate must cite one) | Parametric component factories for standard and custom parts. Mates are applied by deterministic transforms. | Declared interfaces are checked against B-Rep evidence | Beats code-only baselines on assembly validity. |
| **CAD-Assistant** ([arXiv 2412.13810](https://arxiv.org/abs/2412.13810)) | A VLM planner issues FreeCAD Python actions step by step | Runs in FreeCAD and inspects the resulting state after each action | Adjusts later actions based on the current geometry | Zero-shot, no training. Closest to CADSwarm's stack. |
| **From Idea to CAD** ([arXiv 2503.04417](https://arxiv.org/abs/2503.04417)) | Requirements agent → CAD agent → QA agent, modelled on an engineering team | CadQuery | QA renders the model and compares it against the spec, feeds the differences back, and loops with the user | Role split like a human team. |
| **CAD-Coder** ([NeurIPS 2025](https://arxiv.org/html/2505.19713v3)) | Chain-of-thought plan before code | CadQuery | Trained with a geometric reward (Chamfer distance) and a format reward (GRPO) | Planning before code improves accuracy. |
| **Text2CAD** ([NeurIPS 2024](https://arxiv.org/abs/2409.17106)) | Prompts at levels from abstract to expert step-by-step | Sequence of sketch and extrude operations | — | Expert prompts read as ordered "sketch on plane P, extrude d" steps, which is a good template for step summaries. |
| **Zoo Zookeeper / Text-to-CAD** ([Zoo](https://docs.zoo.dev/zookeeper), [Develop3D](https://develop3d.com/cad/zoo-ecosystem-lays-foundation-for-modern-hardware-design-toolkit/)) | Agent with research and reasoning | Produces KCL code (B-Rep, exports STEP) | Engine-level tools to **inspect, snapshot and debug geometry** during generation | Code stays editable, so the design history is the code. |
| **CADAM / Adam** ([GitHub](https://github.com/Adam-CAD/CADAM)) | Single shot → OpenSCAD | Uses the BOSL2/MCAD **libraries** | Pulls dimensions out as named parameters exposed as sliders, so edits need no AI regeneration | Parameters become UI controls. |
| **Fusion AutoConstrain / neural CAD** ([Autodesk Research](https://www.research.autodesk.com/blog/using-ai-to-power-autoconstrain-in-fusion-automated-sketching), [DE247](https://www.digitalengineering247.com/article/autodesk-doubles-down-on-ai)) | — | AI suggests sketch dimensions and constraints. Text-to-CAD is announced but its availability is unconfirmed. | The user can accept or override each suggestion | AI proposes constraints and a human approves them. |

Common lessons:
1. **Plan first, as structured data, without code.** Every step carries target dimensions and
   checkable constraints.
2. **Relations before geometry.** Interfaces (ports, connectors, mate references) are named in the plan,
   so part workers build them and assembly steps consume them.
3. **Deterministic assembly.** The LLM chooses the mates and a solver or transform places the parts.
   Kinematics stay an acyclic tree with one grounded base.
4. **Two-level verification.** Execution errors go through a fast retry loop. Geometry goes through
   kernel metrics plus renders checked by a separate judge, with capped retries and a forced change of
   strategy after repeated failures.
5. **Libraries for standard parts** (fasteners, bearings) and retrieved API docs instead of free-form
   modelling.
6. **Rollback across stages**, so the planner can be asked to re-plan when a step cannot be built.

---

## 3. Recommendations

### A. Planner and worker prompts, and the step-plan contract

1. **The planner writes no CAD code.** It outputs only the v2 plan JSON. The plan prompt must say "one
   step = one component, one library placement, one assembly action, or one check". It must also say
   "a step summary is one engineering sentence" (material or role, the main shape, how it attaches).
2. **Ordering rule in the prompt.** List the grounded base or frame first. Then list parts along the
   load path (each part after the part it attaches to). Library steps come right after their host part,
   because holes must exist before screws. One assembly step follows each sub-group, and `check` steps
   come last. `depends_on` must form a DAG, and `n` must be a topological order.
3. **Add optional fields to each step** (inside `plan_json`, so no schema change): `params` (named key
   dimensions in mm), `targets` (`bbox_mm: [x,y,z]` with tolerance, `holes: 4`, `min_wall_mm`), and
   `ports` (named interfaces the part must expose, for example
   `{"name":"mast_axis","type":"axis"}`, `{"name":"servo_face","type":"plane"}`).
4. **Assembly steps list explicit mates over ports.** For example:
   `"mates":[{"type":"fixed","a":"Base"},{"type":"concentric","a":"Base.mast_bore","b":"Mast.mast_axis"},{"type":"coincident","a":"Base.top","b":"Mast.collar_bottom"}]`.
   Allowed types: fixed, coincident, concentric, distance, angle, parallel, revolute, slider. Exactly one
   part is fixed. The mate graph stays an acyclic tree, and the host applies placements
   deterministically. The LLM never computes placement matrices by hand.
5. **Fixed op vocabulary for the narrated feed.** Workers report op names from a closed list (Create
   sketch, Extrude, Cut, Revolve, Fillet, Chamfer, Hole, Pattern, Mirror, Shell, Insert component, Place
   standard part, Mate, Set color, Check interference, Check printability, Render, Save part). The
   planner's `ops` uses the same words, so the feed can be matched against the plan.
6. **Worker loop per step:** claim → build → **verify** → report. Verification has three parts. Kernel
   checks: a valid solid, bounding box within `targets` tolerance, volume > 0, the hole/face count, and
   every declared `port` present. Three renders (isometric, top-rear, front). A judge pass. Execution
   errors get up to 3 retries. Geometry fixes get up to 3 rounds, and from round 2 the prompt says
   "try a different construction". If the step still fails, report `blocked` with the reason instead of
   reporting it as done.
7. **Use a different model or prompt as the judge** for `check` steps and render review, not the same
   worker that built the part (self-confirmation bias).
8. **Run an interference check after every assembly step,** not only at the end. Also add an explicit
   contact/gap check for parts that should touch, which catches the kind of near-miss CADSmith missed.
9. **The library always comes first.** Any fastener, bearing, insert, magnet, board or motor named in
   the brief must be a `library` step with `component` filled in. The worker prompt forbids modelling
   one, and must call `recommend_components` / `search_components` before inserting.
10. **Re-plan path.** A `blocked` step lets the planner revise only the downstream steps (cross-stage
    rollback). Done steps are never renumbered.
11. **Expose `params` as editable parameters** (Parameters tab), so a size change re-runs only the
    affected step and its dependants.

### B. Web app and FreeCAD workbench UI (generic vocabulary)

12. **Layout sizes:** title bar 36px (mark, file name, quick access, command search 280px, avatars). Ribbon
    tabs row 28px plus a 72px button row (32px icons, 11px labels below). Left pane 280px (resizable
    220–420). Task pane 320px (collapsible to a 40px icon rail). Status bar 24px. Below 900px wide the
    ribbon becomes an overflow menu and the panes become the bottom sheet.
13. **Ribbon tabs and groups** (each group separated by a 1px divider, with a group caption below):
    - **Features**: Sketch-based (Extrude ▾ boss/cut, Revolve ▾, Sweep, Loft) | Dress-up (Fillet ▾/Chamfer, Shell, Draft) | Holes (Hole, Clearance hole) | Patterns (Linear ▾/Circular, Mirror) | Bodies (Boolean ▾, Split) | Reference (Plane, Axis, Point)
    - **Sketch**: Sketch | Line, Rectangle ▾, Circle ▾, Arc, Slot, Polygon | Trim, Offset, Mirror entities | Dimension, Constraints ▾ | Finish sketch
    - **Evaluate**: Measure, Mass properties | Interference, Clearance | Section view | Printability (wall, overhang) | Render check
    - **Assembly**: Insert component, Fix | Mate ▾ (coincident, concentric, distance, angle, revolute, slider) | Move/rotate component | Pattern component | Exploded view | Motion study
    - **Library**: Recommend, Search | Fasteners, Bearings, Inserts, Magnets, Electronics, Motors | Insert from file/URL
    - **AI Build**: New plan, Run next step, Run all ready | Pause, Re-plan from step | Assign step | Review
    - **View**: Orientation ▾, Display style ▾, Section | Show/hide planes, sketches, origins | Perspective, Shadows | Follow teammate
14. **Feature tree row (22px):** caret, 16px type icon, step badge (`3`), name, a right-aligned owner
    dot in the teammate's colour plus a lock icon when claimed, and a status glyph (spinner = building,
    check = done, amber ! = warning, red X = error, grey = suppressed or not ready). The top node repeats
    the worst status. Clicking a glyph opens a "What's wrong" popover. Draw a draggable **rollback bar**
    (2px accent line with a grip) that maps to step progress, plus "Roll to here" and "Roll to end".
15. **Property panel:** header with icon, title, OK / Cancel / Help / Pin. Collapsible group boxes with
    12px semibold titles. **Selection boxes** with an accent-tint fill when active, chips for picked
    items, and hover-to-highlight in the viewport. Numeric fields take units and expressions and have
    spinners. A live preview in the viewport. A command the host cannot run directly shows "Send to
    helpers" as the primary button.
16. **Viewport:** a vertical gradient (tokens below); a 72px triad bottom-left (X/Y/Z red/green/blue); a
    96px view cube top-right; a floating heads-up toolbar top-centre (zoom to fit, previous view,
    orientation ▾, display style ▾ [shaded with edges, shaded, hidden lines removed, wireframe],
    section, hide/show ▾, settings); separate hover and selection highlight colours; teammates' cursors
    in their colours with name tags.
17. **Collaboration:** avatars top-right with a count. Double-clicking an avatar enters follow mode: a
    2px outline in that teammate's colour around the viewport, a "Following <name> · click to stop"
    pill, and an indicator on the followed person's avatar. Tree rows and steps show the owner's colour dot.
18. **Task pane, AI Build tab:** prompt box; progress line `3 of 20 done · now: Pitch link` with a
    segmented bar; a numbered step list (badge, name, kind chip [part, library, assembly, check],
    assignee avatar, status); the expanded step shows its summary and an op feed whose rows read
    `Create sketch SK_Mast  1.9 s` with right-aligned tabular durations, or `Running` with a spinner.
19. **Status bar:** units (`mm`), the rebuild state (`Rebuilt` / `Rebuilding…` / `2 errors`), the selection
    (`Face of Mast`), the editing context (`Editing: Mast (Ana)`), and the connection dot.
20. **Palette tokens** (original values; light is the default, dark is optional):

| Token | Light | Dark |
|---|---|---|
| `--chrome-bg` (title, ribbon) | `#EEF0F3` | `#1B1F24` |
| `--panel-bg` | `#FFFFFF` | `#22272E` |
| `--panel-alt` (group header, hover row) | `#F5F6F8` | `#2A3038` |
| `--border` | `#D3D7DD` | `#363D47` |
| `--text` | `#1F2329` | `#E6E8EB` |
| `--text-muted` | `#5E6773` | `#9AA3AE` |
| `--accent` (active tab, primary, rollback bar) | `#2B6CD9` | `#4C8DF0` |
| `--accent-tint` (active selection box, selected row) | `#E4EDFB` | `#24364F` |
| `--hover-highlight` (viewport pre-select) | `#F2A93B` | `#F2A93B` |
| `--select-highlight` (viewport) | `#1F7AE0` | `#4C9AFF` |
| `--ok` | `#2E9D5B` | `#3FB871` |
| `--warn` | `#D99A00` | `#E8B024` |
| `--error` | `#D23F3F` | `#EF5B5B` |
| `--suppressed` | `#A0A7B0` | `#6B737D` |
| `--vp-top` / `--vp-bottom` (viewport gradient) | `#C5CFDA` / `#F3F5F7` | `#2A3038` / `#474F59` |
| `--edge` (model edges) | `#20242A` | `#0E1013` |
| `--axis-x` / `-y` / `-z` | `#E5484D` / `#2FA567` / `#3D7BF7` | same |

    Teammate colours (8): `#2B6CD9`, `#E5484D`, `#2FA567`, `#D97706`, `#8B5CF6`, `#0EA5A4`,
    `#DB2777`, `#65A30D`.
21. **Typography:** `system-ui, "Segoe UI", -apple-system, Roboto, sans-serif`. 12px base for the tree and
    panels, 11px for ribbon labels and the status bar, 12px semibold for group titles, 13px semibold for
    pane headers. `font-variant-numeric: tabular-nums` on numeric fields and op durations. Use original
    16/32px line icons on a 1.5px stroke, never vendor icons.
22. **FreeCAD workbench mirror:** apply the same tokens through a Qt stylesheet. Use one toolbar per
    ribbon tab with the same group order, a dock for the AI Build task pane, and the same tree status
    glyphs, so the two clients read as one product.
