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

---

## 4. Industry-standard conventions across the eight leading MCAD tools (added 2026-11-07)

Scope: SolidWorks (SW), CATIA V5 / 3DEXPERIENCE (CAT), Siemens NX (NX), PTC Creo (CR), Autodesk
Inventor (INV), Fusion (FU), Onshape (OS), Solid Edge (SE). This section extends section 1 for the
**standalone desktop app** (own PySide/Qt UI; FreeCAD/OCCT only as the kernel). Where vendor docs
disagree between releases, or a value is user-configurable, the table says so; check the installed
release before quoting a value as fact. Section 5 sets the brutalist visual language and section 6 is
the implementation checklist.

### 4.1 Layout regions

| Region | SW | CAT V5 / 3DX | NX | CR | INV | FU | OS | SE |
|---|---|---|---|---|---|---|---|---|
| Title + quick access | Menu flyout + QAT (New, Open, Save, Print, Undo, Rebuild, Options) | V5 menu bar; 3DX top bar + compass top-left | QAT in title bar + command finder | QAT in title bar | QAT in title bar | Application bar (file, save, undo, doc tabs) | Doc name + top-right tools | QAT in title bar |
| Command area | CommandManager (tabbed ribbon) | V5 toolbars docked around viewport; 3DX action bar (bottom) | Ribbon | Ribbon | Ribbon | Tabbed toolbar per workspace | Single feature toolbar | Ribbon |
| Feature tree | FeatureManager (left) | Specification tree (overlaid in viewport) | Part Navigator in resource bar | Model Tree (left) | Model browser (left) | Browser (left) + Timeline (bottom) | Feature list (left) | PathFinder (left) |
| Command inputs | PropertyManager replaces tree tab | Modal dialog | Floating dialog | Dashboard (contextual ribbon tab) | Mini-toolbar + dialog/property panel | Floating command dialog | Floating feature dialog | Command bar |
| Heads-up view toolbar | Top-centre, transparent | — (View toolbar) | View tab + border bar | In-graphics toolbar top-centre | Navigation bar (right) | Navigation bar bottom-centre | Right-click / view cube menu | Status-bar view tools (bottom-right) |
| View cube / triad | Triad bottom-left; view cube optional (newer) | Compass top-right (V5) | Triad bottom-left; view triad | Triad bottom-left | ViewCube top-right | ViewCube top-right | View cube top-right | Triad bottom-left; view cube optional |
| Status bar | Units, edit context, sketch state, rebuild | Prompt line | Cue/status line (top of graphics) | Message area | Prompt + DOF count | — | — | Prompt + quick-view controls |
| Task pane | Right (resources, library, appearance) | — | Resource bar tabs | Right (browser, library) | — | Data panel (left) | Right panels | Right (library, PathFinder tabs) |

Sources: [SW PropertyManager](https://help.solidworks.com/2022/english/SolidWorks/sldworks/r_pm_overview.htm),
[SW Task Pane](https://help.solidworks.com/2025/English/SolidWorks/Sldworks/r_task_pane.htm),
[Creo help](https://support.ptc.com/help/creo/creo_pma/r10.0/usascii/index.html),
[Fusion interface](https://help.autodesk.com/cloudhelp/ENU/Fusion-GetStarted/files/GS-THE-FUSION-INTERFACE.htm),
[Onshape UI basics](https://cad.onshape.com/help/Content/Home/user_interface_basics.htm),
[CATIA 3DX UI](https://cati.com/?p=157309), section 1.1 above.

**Convention for CADSwarm.** Keep the six-region skeleton that all eight share: title bar with quick
access and command search; tabbed ribbon; left feature tree that the **property panel replaces while a
command runs** (SW and Creo pattern, best for keyboard flow and screen use); viewport with a top-centre
heads-up toolbar, a view cube top-right and a triad bottom-left; a right task pane (AI Build, Team,
Library); and a status bar that reports units, sketch state, rebuild state and selection. One extra row,
the **history timeline**, is optional (Fusion pattern) and stays off by default, because the tree
already carries order.

### 4.2 Command sets per environment

Generic names. The core sets below appear, under different names, in all eight products.

**Part (≈22).** Sketch · Extrude (add / cut, up-to-next / through-all / up-to-face / symmetric) · Revolve ·
Sweep · Loft · Hole (simple, counterbore, countersink, tapped, standard sizes) · Fillet (constant,
variable, face) · Chamfer · Shell · Draft · Rib · Thread / cosmetic thread · Linear pattern · Circular
pattern · Pattern along curve / sketch-driven · Mirror · Boolean (unite, subtract, intersect) · Split body ·
Move / copy body · Reference plane · Reference axis · Reference point / coordinate system · Measure ·
Mass properties · Section view · Appearance / material.

**Sketch (≈24).** Line · Centreline / construction toggle · Rectangle (corner, centre, 3-point) · Circle
(centre, 3-point) · Arc (3-point, tangent, centre) · Slot · Polygon · Spline · Point · Ellipse · Fillet
(2D) · Chamfer (2D) · Trim · Extend · Split · Offset · Mirror entities · Linear / circular sketch pattern ·
Project / convert entities (use edge) · Intersection curve · Smart dimension · Add constraint · Show /
hide constraints · Fully define (auto-dimension) · Text · Finish sketch.

**Assembly (≈20).** Insert component · New part in context · Fix / ground · Float · Mate / constraint
(coincident, parallel, perpendicular, tangent, concentric, distance, angle, lock) · Joint (rigid,
revolute, slider, cylindrical, planar, ball) · Move component · Rotate component · Component pattern ·
Mirror component · Replace component · Standard-part library · Exploded view · Interference detection ·
Clearance check · Section · Motion study / drive joint · Bill of materials · Pack and go / save as
copy · Isolate / hide / transparency.

**Drawing (≈20).** New sheet · Sheet format / title block · Base (model) view · Projected view ·
Auxiliary view · Section view · Detail view · Broken-out section · Break view · Crop view · Smart
dimension · Ordinate / baseline dimension · Hole callout · Centre mark / centreline · Note · Balloon /
auto-balloon · Bill of materials · Revision table · Datum / GD&T frame · Surface finish / weld symbol ·
Export PDF / DXF.

**Convention for CADSwarm.** Ship exactly these names. A command that FreeCAD's kernel cannot do
natively in the first release (for example a variable fillet) still appears, disabled, with a tooltip
that says why and the release it is planned for. Users judge a ribbon by whether it is complete.

### 4.3 Feature tree behaviour

| Behaviour | SW | CAT | NX | CR | INV | FU | OS | SE |
|---|---|---|---|---|---|---|---|---|
| Rollback | Rollback bar (drag) | "Define in work object" | "Make current feature" | Insert mode arrow (drag) | End-of-Part marker (drag) | Timeline marker (drag) | Rollback bar (drag, ↑/↓) | Ordered: rollback; synchronous: none |
| Suppress | Suppress / unsuppress (grey) | Deactivate / activate | Suppress | Suppress / resume | Suppress features | Suppress (timeline) | Suppress / unsuppress | Suppress |
| Reorder | Drag; blocked above parents | Reorder dialog | Drag; dependency check | Drag; dependency check | Drag | Drag in timeline | Drag; error if illegal | Drag (ordered) |
| Edit | Edit feature / edit sketch (context bar) | Double-click | Edit with rollback | Edit definition | Edit feature / edit sketch | Edit feature (timeline) | Edit (double-click) | Edit definition |
| Errors | Red X + "What's wrong" | Update diagnosis dialog | Alert in Part Navigator | Failed feature red + Resolve mode | Red/yellow icon + Design Doctor | Red/amber in timeline | Red feature text + hover message | Red/yellow in PathFinder |
| Folders / groups | Folders | Geometrical sets | Feature groups | Groups | Folders | Component groups | Folders | Groups |
| History vs direct | History; direct edit tools | History | History + synchronous | History + flexible modelling | History + direct edit | History or "do not capture" | History only | Synchronous + ordered |

Sources: [SW What's Wrong](https://help.solidworks.com/2017/English/SolidWorks/sldworks/HIDD_NEW_WHATS_WRONG.htm),
[SW rebuild tools](https://help.solidworks.com/2022/English/SolidWorks/sldworks/c_fundamentals_rebuid_tools_help.htm),
[Onshape feature list](https://cad.onshape.com/help/Content/PartStudio/features_and_parts_lists.htm),
[Solid Edge ordered + synchronous](https://blogs.sw.siemens.com/solidedge/hybrid-modeling-in-solid-edge-with-syncronous-and-ordered-modes/).

Shared rules:
- **Parent/child limits on reorder.** A feature cannot move above anything it references (its sketch,
  its sketch plane, the edges it fillets). The drag cursor shows "not allowed" and a tooltip names the
  parent. A sketch nested under a feature moves with it.
- **Errors stop geometry, warnings do not.** The worst status is repeated on the root node, and an
  arrow on a collapsed parent marks a child with a problem. The "What's wrong" view lists each issue
  with type, feature, and a plain-language cause.
- **Edit sketch vs edit feature** are separate commands. Edit feature opens the property panel with
  the model rolled back to that feature, then rolls forward on OK.
- **Rebuild vs force rebuild.** Rebuild recalculates only what changed and its children. Force rebuild
  recalculates the whole tree and is the diagnostic step
  ([CATI](https://cati.com/?p=120968)).

**Convention for CADSwarm.** History-based tree with a draggable rollback bar, suppress/unsuppress,
drag reorder with parent checks, folders, separate Edit feature / Edit sketch, a "What's wrong" list,
and Rebuild (Ctrl+B) / Force rebuild (Ctrl+Q). Direct editing (push/pull a face) is phase 2 and is
recorded as a feature in the tree (like FreeCAD's own approach), never as untracked geometry.

### 4.4 Sketch workflow and constraint-state colours

Entering and exiting: every product enters a sketch by picking a plane or planar face and then
Sketch (or picking Sketch then the plane). The view turns normal to the plane (SW, OS, FU optional;
NX and CR on by default). Exit is a "Finish sketch" button, a confirm corner in the viewport, Esc, or
Ctrl+Enter. Leaving an over-defined sketch is allowed everywhere but marks the sketch with a warning.

Geometric constraints present in all eight: coincident, horizontal, vertical, parallel,
perpendicular, tangent, concentric, equal, collinear, midpoint, symmetric, fix/lock, pierce
(point-on-curve through the plane), plus, in most: smooth (G2), coradial, normal, merge. Smart dimension
infers linear, angular, radial or diameter from what is picked. Auto-constraints (inference): while
drawing, the cursor shows a small constraint glyph (H, V, ⟂, ∥, tangent) and dashed inference lines
from existing endpoints; Ctrl or Shift suppresses inference. Creo goes further with **weak dimensions**
that keep the sketch always fully defined until the user strengthens them
([Creo sketcher](https://support.ptc.com/help/creo/creo_sim/usascii/sim/simulate/sketcher/About_Dimensioning.html)).

| State | SW | OS | FU | INV | CAT V5 | NX | CR | SE |
|---|---|---|---|---|---|---|---|---|
| Under-defined | Blue | Blue | Blue | Colour per scheme; status bar "N dimensions needed" | White | Colour per Part Settings | n/a (weak dims) | Configurable |
| Fully defined | Black | Black | Black + lock icon in browser | Status bar "Fully constrained" | Green (iso-constrained) | Distinct colour (release-dependent) | Strong dims only | Configurable |
| Over-defined / conflict | Red | Red | Refused by a dialog | Refused by a dialog | Purple (over), red (inconsistent) | Red | Resolve Sketch dialog | Refused / red |
| Redundant / dangling | Yellow / brown | Red constraint icon | — | — | Brown (not changed) | — | — | — |
| Driven (reference) dim | Grey | Parenthesised | Parenthesised | Parenthesised | Parenthesised | Reference expression | Reference (Ref) | Blue/parenthesised |

Sources: [SW sketch geometry status](https://help.solidworks.com/2020/English/SolidWorks/sldworks/c_Sketch_Geometry_Status.htm),
[SW sketch states](https://help.solidworks.com/2019/english/SolidWorks/acadhelp/c_sketch_states_acadhelp.htm),
[Onshape troubleshooting sketch geometry](https://cad.onshape.com/help/Content/Sketch/troubleshooting_sketch_geometry.htm),
[Onshape constraints](https://cad.onshape.com/help/Content/Sketch/working_with_constraints.htm),
[Fusion fully define](https://help.autodesk.com/cloudhelp/ENU/Fusion-Sketch/files/SKT-FULLY-DEFINE-CONSTRAIN-SKETCH.htm),
[Inventor sketch constraints](https://help.autodesk.com/cloudhelp/2026/ENU/Inventor-Help/files/GUID-FAF69614-E8F2-4763-975C-552E0BEA1DD1.htm),
[CATIA colours](https://catiahelp.azurewebsites.net/English/DysUserMap/dys-c-BeforeYouBegin-ColorUse.htm),
[CATIA over-constrained analysis](https://catiahelp.azurewebsites.net/English/DysUserMap/dys-m-OverConstrainedSketchAnalyze-sb.htm),
[NX sketch colours KB](https://support.sw.siemens.com/en-US/okba/PL8171019/Sketches-wont-show-different-color-curves-when-constrains-or-dimensions-are-applied/index.html),
[TU Darmstadt NX tutorial](https://www.iim.maschinenbau.tu-darmstadt.de/CAD-Tutorial/.\\4_modeling\\1_sketch_based_modeling\\1_sketch_ger_skizze\\3_complex_sketch/index.en.html),
[Creo resolve conflict](https://support.ptc.com/help/creo/creo_sim/usascii/sim/simulate/sketcher/To_Resolve_a_Conflict.html).
NX and Solid Edge colours vary by release and settings; the table records only what docs state.

The mainstream de-facto mapping (SW, OS, FU, and FreeCAD Sketcher users who come from them) is
**blue = under, black = fully, red = over/conflict**. A dedicated conflict dialog (Creo "Resolve
Sketch": Undo, Delete, Dim→Ref, Explain) is the best solver feedback in the group; SW's SketchXpert
and the Onshape Constraint manager do the same job.

Construction geometry: dashed or centre-line style everywhere, toggled per entity (OS `Q`, FU `X`,
SW "For construction"). It never forms a profile.

**Convention for CADSwarm.** Blue / ink / red, plus grey for driven dimensions and dashed muted lines
for construction (tokens in 5.4). The status bar always shows `Under-defined · 3 DOF` /
`Fully defined` / `Over-defined: 1 conflict`, using the DOF count that the FreeCAD sketch solver
(PlaneGCS) already returns. A conflict opens a **Resolve** popover listing the conflicting constraints
with Delete, Make driven, Undo, and Explain. Never block the user from drawing, and never silently drop
a constraint.

### 4.5 Selection filters and conventions

| Convention | SW | CAT | NX | CR | INV | FU | OS | SE |
|---|---|---|---|---|---|---|---|---|
| Filters | Selection filter toolbar (F5) | User selection filter toolbar | Type filter + selection bar | Filter list (status bar) | Select priority (component / part / feature / face / edge) | Selection filters menu | Filter per dialog | QuickPick + select tools |
| Box L→R | Window (fully inside) | Trap types | Rectangle inside | Box | Window | Window | Window | Window |
| Box R→L | Crossing (touching) | Trap types | Rectangle option | — | Crossing | Crossing | Crossing | Crossing |
| Pre-selection | Hover highlight | Hover highlight | Hover highlight | Hover highlight | Hover highlight | Hover highlight | Hover highlight | Hover highlight |
| Select other | Right-click → Select Other | Other selection | QuickPick (hover pause / right-click) | Right-click cycles | Select Other (hover pause, arrows) | Click-and-hold → list | Alt-click through / long click | QuickPick |
| Add / remove | Ctrl+click toggles | Ctrl / Shift | Shift+click removes | Ctrl+click | Ctrl / Shift | Ctrl / Shift | Click toggles | Ctrl / Shift |

Sources: [Onshape shortcut keys (alt-click through)](https://cad.onshape.com/help/Content/shortcut_keys.htm),
vendor help for the other cells (SW Select Other, NX/SE QuickPick, Inventor Select Other, Fusion
click-and-hold); bindings are configurable in most products.

**Convention for CADSwarm.** Box left-to-right = window (fully inside, solid outline);
right-to-left = crossing (touching, dashed outline). Click selects, Ctrl+click toggles, Shift+click
adds. Hover pre-selection is shown at once. **Alt+click or a 400 ms press-and-hold** opens a Select
Other list of everything under the cursor, ordered front to back, with hover-to-highlight. A filter
bar has toggles for Vertex, Edge, Face, Body, Component, Sketch, Plane/axis, and Feature (see 6,
item 10). Each property-panel selection box applies its
own implicit filter.

### 4.6 Mouse and keyboard

| Action | SW | CAT V5 | NX | CR | INV | FU | OS | SE |
|---|---|---|---|---|---|---|---|---|
| Orbit | MMB drag | MMB + LMB/RMB drag | MB2 drag | MMB drag | Shift+MMB (or F4) | Shift+MMB | RMB drag | MMB drag |
| Pan | Ctrl+MMB | MMB drag | Shift+MB2 or MB2+MB3 | Shift+MMB | MMB drag (or F2) | MMB drag | MMB drag / Ctrl+RMB | Shift+MMB |
| Zoom | Wheel; Shift+MMB | MMB, click LMB/RMB, drag | Wheel; Ctrl+MB2 or MB1+MB2 | Wheel; Ctrl+MMB | Wheel (F3) | Wheel | Wheel | Wheel; Ctrl+MMB |
| Context command menu | S (shortcut bar), RMB context toolbar | RMB | Radial menus (Ctrl+Shift+MB1/2/3) | RMB mini-toolbar | Marking menu (RMB) | Marking menu (RMB), S toolbox | S (shortcut toolbar) | RMB radial / QuickBar |
| View selector | Space | — | F8 snap, Home / End | Ctrl+D standard | F6 home | Home | Shift+7 iso | Ctrl+I iso |
| Rebuild | Ctrl+B / Ctrl+Q | Ctrl+U update | Update (configurable) | Ctrl+G regenerate | Local / global update (configurable) | — (live) | — (live) | — (live) |

Notes and sources: NX, Inventor and Solid Edge bindings vary by release and customer role (verify
in the installed release); NX Home = trimetric, End = isometric, F8 = snap to nearest standard view
([Siemens blog](https://blogs.sw.siemens.com/news/nx-hints-and-tips-using-the-end-key-home-key-and-f8-keys/));
Creo MMB / Shift+MMB / Ctrl+MMB ([manufacturing.net](https://www.manufacturing.net/home/blog/22131393/how-to-rotate-and-zoom-in-creo-and-catia-v5));
CATIA navigation ([PTC CATIA navigation mode](https://support.ptc.com/help/wnc/r12.0.2.0/en/Windchill_Help_Center/common_thingview/Mouse_Navigation_Modes_CATIA.html));
Onshape ships presets for SW, NX, Creo and AutoCAD navigation
([Onshape tech tip](https://www.onshape.com/en/resource-center/tech-tips/tech-tip-changing-rotate-pan-and-zoom));
SW shortcut bar S, view selector Space, Ctrl+B / Ctrl+Q
([SW blog](https://blogs.solidworks.com/tech/2018/09/essential-short-cut-to-start-your-solidworks-learning-journey.html),
[SW rebuild](https://help.solidworks.com/2022/English/SolidWorks/sldworks/c_fundamentals_rebuid_tools_help.htm)).

Standard-view keys: SW uses Ctrl+1 Front, Ctrl+2 Back, Ctrl+3 Left, Ctrl+4 Right, Ctrl+5 Top,
Ctrl+6 Bottom, Ctrl+7 Isometric, Ctrl+8 Normal to, F Zoom to fit (widely published SW cheat sheets,
e.g. [Solid Solutions PDF](https://www.solidsolutions.co.uk/Uploaded/Documents/Webcast-Docs/solidworks%20Keyboard%20Shortcuts%20-%202018.pdf)).
Onshape uses Shift+1…Shift+7 for the same seven views, F fit, N normal to, W zoom box, S shortcut
toolbar, and single letters in sketch (L line, C circle, R centre rectangle, G corner rectangle, A arc,
D dimension, Q construction, M trim, H/V/T/I constraints) and Shift+E extrude, Shift+F fillet
([Onshape shortcut keys](https://cad.onshape.com/help/Content/shortcut_keys.htm)).

**Convention for CADSwarm.** Ship four navigation presets in Preferences: **CADSwarm default (= SW
style: MMB orbit, Ctrl+MMB pan, wheel and Shift+MMB zoom, wheel zooms to cursor)**, Onshape-style
(RMB orbit, MMB pan), Fusion/Inventor-style (Shift+MMB orbit, MMB pan) and Creo/NX-style (MMB orbit,
Shift+MMB pan, Ctrl+MMB zoom). The first-run dialog asks "Which CAD do you use most?" and picks the
preset. Keyboard: SW view keys (Ctrl+1…8), plus Onshape-style single-letter sketch keys, S for the
shortcut bar, Space for the view selector, Ctrl+B / Ctrl+Q. The full keymap is in 6.5.

### 4.7 Units and document templates

| Item | SW | CAT | NX | CR | INV | FU | OS | SE |
|---|---|---|---|---|---|---|---|---|
| Templates | Part / Assembly / Drawing template files | Startup models | Templates by role (mm / inch) | Start parts (mmns / inlbs) | ISO / ANSI / DIN / JIS template sets | Per-doc default units | Per-doc units; company defaults | ISO / ANSI template sets |
| Unit systems | MMGS, IPS, CGS, MKS, custom | Options per magnitude | Metric / English per part | mmNs, inlbs, mmKs, custom | Length / mass / time per doc | mm, cm, m, in, ft | mm, cm, m, in, ft, yd | Per doc |
| Precision | Decimals per unit + dims | Display decimals | Decimals | Decimals | Decimals per unit | Display precision | Display precision per unit | Decimals |

Sources: [Onshape units](https://cad.onshape.com/help/Content/units.htm), vendor template docs
(SW document properties, Creo start parts, Inventor template sets); unit system names are SW's.

Shared expectations: units are a property of the **document**, not the app; a value typed with a unit
(`1 in`, `25.4mm`, `=W/2`) converts automatically; display precision is separate from stored precision
(kernel stores double); angles in degrees; templates bundle units, material, standard (ISO/ANSI),
drawing sheet format, and custom properties (part number, description, material, author).

**Convention for CADSwarm.** Six templates: Part (mm), Part (in), Assembly (mm), Assembly (in),
Drawing ISO A3 first-angle, Drawing ANSI B third-angle. Unit systems: MMGS (default), IPS, MKS, CGS,
custom. Display: 2 decimals for mm, 3 for inch, 1 for degrees; trailing zeros per standard (ISO: drop;
ANSI inch: keep to precision, drop leading zero). Values entered in any unit convert. The status bar
unit button changes the document unit system in one click.

### 4.8 File formats

| Format | Users expect | Notes |
|---|---|---|
| STEP AP203 / AP214 / AP242 | Import and export, all three | AP214 adds colours and layers; AP242 adds semantic PMI and tessellated shapes. AP242 ed2 is the modern safe default. Downgrade loses data (AP242→AP214 drops PMI; AP214→AP203 drops colours/layers) ([Visiativ](https://mycad.visiativ.com/en/knowledge-center/tutorials/solidworks-tutorials/differences-step-ap203-ap214-ap242-solidworks), [STEP standards explained](https://takuto-na.github.io/step-standards-explained/README), [CAx-IF styling practice](https://www.mbx-if.org/documents/rec_prac_styling_org_v16.pdf), [GoEngineer](https://www.goengineer.com/blog/solidworks-step-export-ap203-vs-ap214)) |
| IGES | Import (legacy surfaces), export rarely | Surfaces/wireframe; option "export as solids / surfaces" |
| Parasolid (x_t / x_b) | Import/export in SW, NX, SE, OS | Kernel-native for those; FreeCAD cannot write it without a licence, so offer STEP instead and say so |
| ACIS (sat) | Import | Inventor/Fusion lineage |
| STL | Export (print), import as mesh | Units are not stored: export dialog needs unit, resolution (deviation, angle), binary/ASCII, one file per body or merged |
| 3MF | Export (print) | Stores units, colour, multiple bodies, build plate; preferred for printing |
| OBJ / glTF | Export for render / web | Optional |
| DXF / DWG | Export from sketch, face, flat pattern, drawing; import into sketch | Laser/waterjet flow: right-click face → Export DXF |
| PDF | Drawing export | Vector, layers optional |
| Native kernel file | FreeCAD `.FCStd` | Keep as "Open from FreeCAD", not the CADSwarm document |

Standard import dialog options: unit override, heal/sew surfaces, import as single part vs assembly,
keep colours, keep names, keep PMI. Standard export dialog: format/AP choice, units, "export as one
part / as assembly", colours on/off, tessellation tolerance (mesh formats), coordinate system, file
per body. Users expect a log of what was healed or lost.

**Convention for CADSwarm.** Day-one formats: STEP AP214 and AP242 (import + export, AP242 ed2
default for export), IGES import, STL and 3MF export with a tolerance dialog, DXF export of a face,
sketch, or drawing, PDF drawing export. Every export writes a one-line summary to the status bar and a
full report into the task pane (bodies written, healed faces, data dropped). Parasolid is listed as
"not available (kernel licence)", not hidden.

### 4.9 Drawings

| Element | Convention across vendors |
|---|---|
| Sheet sizes | ISO A4–A0 (ISO 5457 borders, zones); ANSI A–E |
| Projection | ISO first-angle (default outside N. America); ANSI third-angle; symbol in title block |
| Title block | ISO 7200 fields: title, drawing number, sheet x of y, scale, projection, units, material, author/approver, date, revision, legal owner. Fields linked to model custom properties |
| Views | Base/model view (orientation + scale), projected, isometric, auxiliary, section (full, half, offset, aligned), detail (circle/box + label + scale), broken-out section, break, crop, alternate position |
| Dimensions | Model (driving, imported) vs reference dims; linear, aligned, angular, radial, diameter, ordinate, baseline, chain; tolerances (symmetric, deviation, limits, fits) |
| Annotations | Centre marks, centrelines, notes, balloons (auto-balloon), hole callouts, thread callouts, datum features, GD&T frames, surface finish, weld symbols, revision clouds |
| Tables | BOM (item, part number, description, qty, material; top-level / parts-only / indented), revision table, hole table |
| Line styles | ISO 128 line types: visible continuous thick, hidden dashed thin, centre chain thin |

Sources: [Onshape drawings](https://cad.onshape.com/help/Content/drawings.htm); standards ISO 7200
(title block data fields), ISO 5457 (sheet sizes and layout), ISO 128 (line types and views),
ISO 5456-2 (projection methods), ASME Y14.1 / Y14.3 / Y14.5 (US sheets, views, GD&T).

**Convention for CADSwarm.** Phase 1: one sheet, ISO A3/A4 and ANSI A/B formats, title block filled
from document properties, base + three projected views + isometric, section and detail views (via
FreeCAD TechDraw underneath), smart dimension, centre marks, notes, balloons and an auto BOM. Export to
PDF and DXF. GD&T, weld, and revision tables are phase 2. Drawing chrome uses the same tokens; the sheet
itself is always white paper with black ink regardless of theme.

### 4.10 What makes professionals dismiss a tool as "not industry standard"

Distilled from the conventions above and recurring complaints on vendor forums
([Onshape forum](https://forum.onshape.com/), [Eng-Tips](https://www.eng-tips.com/),
[Autodesk forums](https://forums.autodesk.com/t5/fusion-design-validate-document/sketch-constraint-issues/m-p/11135131)):

1. **No parametric history / no rollback.** If editing an early sketch does not ripple forward, or
   the tree cannot be rolled back and reordered, the tool is a modeller, not CAD.
2. **Weak sketch solver feedback.** No DOF count, no colour state, conflicts that silently delete
   constraints, no list of what is wrong.
3. **Topological naming breaks.** A fillet that jumps to another edge after an upstream edit. (This is
   FreeCAD's historic weak point; the kernel's toponaming work in 1.0 must be on, and CADSwarm should
   reference faces by stable names where it builds features itself.)
4. **Unfamiliar navigation** with no preset for the user's previous CAD.
5. **No neutral exchange.** Missing STEP import/export or broken colours/assembly structure on STEP.
6. **No units discipline.** Ambiguous units, no expression input, no per-document units.
7. **No drawings / no BOM.** A part that cannot become a dimensioned drawing does not go to a shop.
8. **No assembly mates with a solver,** or no interference check.
9. **Unstable or slow rebuilds** with no indication of which feature is slow or failed.
10. **Toy UI signals.** Rounded consumer styling, hidden commands, modal wizards, no keyboard path,
    no undo for every action.

**Convention for CADSwarm.** Treat items 1–6 as release blockers for the standalone app; 7–8 as
blockers for "1.0"; 9–10 are the design-language brief below.

---

## 5. Brutalist UI for dense engineering software

### 5.1 Principles

Brutalism in software borrows from the architecture term (exposed structure, raw material, honest
function) rather than from "ugly on purpose". Copeland's guidelines put it as: content readable without
assistance, only links and buttons respond to clicks, links look like links, performance is part of
the design ([Guidelines for Brutalist Web Design](https://brutalist-web.design/)). Usability research
shows the anti-design branch hurts task performance ([Web brutalism: assessing usability](https://cris.pucp.edu.pe/es/publications/web-brutalism-assessing-usability-of-an-ugly-web/),
[LogRocket](https://blog.logrocket.com/ux-design/web-brutalism/)), so for a tool used eight hours a day
CADSwarm takes the **structural** branch, not the shock branch:

1. **Structure is visible.** Regions are separated by solid 2px rules, not shadows, gradients or
   cards. You can see the grid.
2. **Flat, opaque, square.** No radius, no drop shadow, no blur, no glass. Depth = border weight and
   fill contrast only.
3. **Few colours, all meaningful.** Ink and paper carry everything. One signal colour means "active /
   AI / selected"; red means error; yellow means warning; blue is reserved for sketch under-definition.
   Decoration never uses a colour.
4. **Type does the hierarchy.** Size, weight, case and mono vs grotesk replace boxes and icons.
   Labels are uppercase mono; values are mono with tabular figures.
5. **Honest state.** Every state (hover, pressed, focused, disabled, running, error) has a distinct,
   visible treatment. No state is "subtle".
6. **Dense but on a grid.** 4px base grid, fixed row heights, no padding inflation.

### 5.2 Examples worth studying

- **Web:** [brutalist-web.design](https://brutalist-web.design/) (the guidelines themselves);
  Craigslist and Hacker News (raw, fast, content-first); Gumroad's 2021 neo-brutalist redesign (thick
  black borders, flat fills, hard offset shadows; [Webflow on brutalism](https://webflow.com/blog/brutalism));
  the Bloomberg Businessweek web era; catalogue at [brutalistwebsites.com](https://brutalistwebsites.com/).
- **Software:** the Bloomberg Terminal (black field, amber/orange signal, mono everywhere, extreme
  density, used all day by professionals: the best proof that a hard-edged style can be ergonomic);
  terminal UIs and IDE "high contrast" themes; Teenage Engineering product UIs (monochrome, one signal
  orange, uppercase labels); Swiss/International typographic style as the typographic ancestor.
- **Industrial signage:** safety colour standards (ISO 3864, ANSI Z535) give a ready meaning system:
  yellow = caution, orange = hazard/attention, red = stop/danger. Users from shop floors read these
  instantly.

### 5.3 Keeping it usable for hours

- **Contrast without glare.** Body text ≥ 7:1 (WCAG AAA; ink on panel is 17.6:1), secondary and
  status text ≥ 4.5:1 (AA), but avoid pure #000 on pure #FFF for
  large fields: use near-black ink `#111111` on off-white `#F7F7F4` panels; pure white only for input
  fields and the drawing sheet. In dark mode use `#E8E8E3` text on `#121212`, never `#FFFFFF` on
  `#000000` (halation for astigmatic users).
- **Borders carry hierarchy.** 2px ink for region boundaries and primary controls; 1px hairline for
  rows inside lists and tables. Too many 2px lines in a dense tree is noise.
- **Signal colour is scarce.** If more than about 5% of the screen is orange, it stops signalling.
  Use it as fill on the active tab underline, primary button, focused field ring, AI step in progress,
  and selection in the viewport. Never for icons at rest.
- **Focus is loud.** 2px signal ring outside the 2px ink border (so total 4px), visible on every
  control, keyboard and mouse. Qt: draw via border swap in QSS (`:focus { border: 2px solid signal }`)
  plus a 2px offset frame on custom widgets.
- **Disabled is not grey text alone.** Use muted text plus a dashed border, so it reads in both
  themes and for colour-blind users.
- **Viewport is calm.** Flat neutral background (no gradient), mid-grey so both ink edges and light
  parts read. The chrome is hard-edged; the model is the content.
- **Motion is minimal.** 0 ms for hover, 80–120 ms linear for panel open/close, none for tree updates.
  Respect reduced motion.
- **Colour-blind safety.** Under/over-defined (blue/red) also differ by line style: over-defined
  entities draw 3px thick with a red halo glyph; driven dims are parenthesised; errors always pair
  colour with a glyph (X, !, ⟂).

### 5.4 Design tokens

**Colour, light (default)**

| Token | Value | Use |
|---|---|---|
| `--ink` | `#111111` | Text, 2px structural borders, model edges |
| `--paper` | `#FFFFFF` | Inputs, sheet, menus |
| `--panel` | `#F7F7F4` | Panels, tree, task pane |
| `--chrome` | `#EBEBE6` | Title bar, ribbon, status bar |
| `--hairline` | `#C9C9C2` | 1px row dividers |
| `--muted` | `#5A5A55` | Secondary text (6.5:1 on panel) |
| `--signal` | `#FF5F00` | Active / AI / selection fill (ink text on it: 6.2:1) |
| `--signal-ink` | `#B83F00` | Signal-coloured text or 1px lines on paper (5.6:1) |
| `--signal-tint` | `#FFE3D1` | Active selection box, selected row |
| `--warn` | `#FFD000` | Warning fill, ink text/glyph on it |
| `--error` | `#D21F1F` | Error text, glyph, over-defined (5.3:1 on paper) |
| `--error-tint` | `#FBE0DE` | Error row background |
| `--ok` | `#0E7A3E` | Done glyph only |
| `--sk-under` | `#1747E6` | Under-defined sketch geometry (6.8:1 on paper, 4.8:1 on viewport) |
| `--sk-full` | `#111111` | Fully defined (= ink) |
| `--sk-over` | `#D21F1F` | Over-defined / conflicting (= error) |
| `--sk-driven` | `#7A7A74` | Driven / reference dimensions |
| `--sk-construction` | `#7A7A74` dashed 6/4 | Construction geometry |
| `--sk-preselect` / `--sk-select` | `#FF9A55` / `#FF5F00` | Hover / selected in sketch and 3D |
| `--vp-bg` | `#D9D9D4` | Viewport (flat) |
| `--axis-x/y/z` | `#E0302B` / `#1F9D4C` / `#1747E6` | Triad |

**Colour, dark**

| Token | Value | Token | Value |
|---|---|---|---|
| `--ink` (text, borders) | `#E8E8E3` | `--signal` | `#FF6A1A` |
| `--paper` (inputs) | `#0C0C0C` | `--signal-ink` | `#FF8A4C` |
| `--panel` | `#161616` | `--signal-tint` | `#3A1E0C` |
| `--chrome` | `#1F1F1F` | `--warn` | `#FFD000` |
| `--hairline` | `#3A3A37` | `--error` | `#FF4B42` |
| `--muted` | `#A3A39C` | `--error-tint` | `#3A1413` |
| `--sk-under` | `#6E95FF` | `--ok` | `#3CCB7F` |
| `--sk-full` | `#E8E8E3` | `--sk-over` | `#FF4B42` |
| `--sk-driven` / construction | `#8C8C85` | `--vp-bg` | `#2A2A28` |

In dark mode, 2px region borders use `#4A4A46` (not full ink) so the grid stays visible without
glare; focused and active elements switch to full `--ink` or `--signal`.

Teammate colours (avoid orange, red, yellow, blue-sketch so they never read as status):
`#7C3AED` violet, `#0891B2` cyan, `#16A34A` green, `#DB2777` magenta, `#65A30D` lime, `#0D9488`
teal, `#9333EA` purple, `#A16207` ochre-brown. Shown as a 2px outline plus initials, never as fill
behind text.

**Typography**

- Grotesk (headings, tree names, body): **Space Grotesk** (SIL OFL 1.1,
  [GitHub](https://github.com/floriankarsten/space-grotesk)). Fallback: Archivo (OFL), then
  system sans.
- Mono (labels, values, ids, durations, status bar, keyboard hints): **JetBrains Mono** (SIL OFL 1.1,
  [GitHub](https://github.com/JetBrains/JetBrainsMono)). Alternative: IBM Plex Mono (OFL,
  [GitHub](https://github.com/IBM/plex)).
- Bundling: ship the static TTFs (Regular, Medium, Bold for both; JetBrains Mono Regular + Bold) in
  `resources/fonts/` with each `OFL.txt`; register at start-up with
  `QFontDatabase.addApplicationFont()` before any widget is created; web client self-hosts woff2 via
  `@font-face` (or `@fontsource/space-grotesk`, `@fontsource/jetbrains-mono`). OFL allows bundling and
  redistribution with the app as long as the licence travels with the fonts and the fonts are not
  sold alone. Total size is about 1 MB.

| Role | Family | Size / line | Weight | Case |
|---|---|---|---|---|
| Display (empty states, dialogs) | Grotesk | 28 / 32 | 700 | Sentence |
| Pane title | Grotesk | 16 / 20 | 700 | Sentence |
| Group title | Mono | 11 / 16 | 700 | UPPER, +0.06em |
| Body, tree row, menu | Grotesk | 13 / 20 | 400 (500 selected) | Sentence |
| Ribbon label | Mono | 10 / 12 | 500 | UPPER, +0.04em |
| Field label | Mono | 11 / 16 | 500 | UPPER |
| Value, number, duration | Mono | 12 / 16 | 400, tabular | — |
| Status bar | Mono | 11 / 16 | 400 | — |

**Spacing, borders, focus, icons**

- Grid: 4px base. Spacing scale `4, 8, 12, 16, 24, 32`. Row heights: tree 24, menu 28, field 28,
  ribbon button 64×64, title bar 40, ribbon tab row 32, status bar 24.
- Borders: `--bw-structure: 2px` (regions, buttons, fields, panels); `--bw-hairline: 1px` (rows,
  table cells, group separators inside the ribbon). Radius `0` everywhere. Shadows: none, except an
  optional hard offset `4px 4px 0 var(--ink)` for floating popovers so they read above the viewport.
- Focus: `outline: 2px solid var(--signal); outline-offset: 2px` (web); QSS equivalent swaps the border
  to signal and adds a 2px signal frame on custom painted widgets.
- States: hover = `--chrome` fill + ink border; pressed = ink fill, paper text; active tab = 4px
  signal bar on the bottom edge + bold label; disabled = muted text + 1px dashed border.
- Icons: original monoline set on a **24px grid, 2px stroke, square caps, miter joins**, 2px safe
  padding (20px live area), no fills except a single signal accent dot for AI commands; a 16px variant
  for the tree at 1.5px stroke, same caps. Ribbon uses the 24px icon centred in the 64px button with
  the mono label under it. SVG source, recoloured via `currentColor` (web) and QIcon engines (Qt).

---

## 6. Recommendations for CADSwarm (standalone app checklist)

These supersede items 12, 16, 20 and 21 of section 3 for the standalone desktop app (the web client
should follow them as well, so both read as one product). The AI Build items in section 3 stay.

1. **Layout (desktop, 1920×1080 reference).** Title bar 40px: mark, file name + unsaved dot, quick
   access (New, Open, Save, Undo, Redo, Rebuild, Options), command search 320px (Ctrl+K / `/`),
   teammate avatars. Ribbon: tab row 32px + button row 72px (64px buttons). Left pane 300px (220–480)
   with tabs TREE · PROPERTIES · PARAMETERS; the property panel **replaces** the tree while a command
   runs and the tree shows again on OK/Cancel. Viewport: heads-up toolbar top-centre, view cube 96px
   top-right, triad 64px bottom-left, select-filter chip strip bottom-left above the triad. Right task
   pane 340px (collapses to a 40px rail): AI BUILD · TEAM · LIBRARY · REPORT. Status bar 24px. All
   region boundaries 2px rules.
2. **Ribbon, Part environment.** Tabs: FEATURES · SKETCH · SURFACE (phase 2) · EVALUATE · LIBRARY ·
   AI BUILD · VIEW.
   - FEATURES: Sketch | Extrude ▾ (add, cut), Revolve ▾ (add, cut), Sweep, Loft | Fillet ▾ (fillet,
     chamfer), Shell, Draft, Rib | Hole, Thread | Linear pattern ▾ (linear, circular, curve), Mirror |
     Boolean ▾, Split, Move body | Plane, Axis, Point, Coordinate system
   - EVALUATE: Measure, Mass properties | Section view | Interference, Clearance | Draft analysis,
     Thickness, Curvature | Printability (wall, overhang) | Check geometry
   - LIBRARY: Recommend, Search | Fasteners, Bearings, Inserts, Magnets, Electronics, Motors | Insert
     from file
   - AI BUILD: New plan, Run next step, Run all ready | Pause, Re-plan from step | Assign step | Review
   - VIEW: Orientation ▾, Display style ▾, Section | Show/hide ▾ (planes, axes, sketches, origins,
     annotations) | Perspective, Shadows, Edges | Follow teammate | Theme ▾
3. **Ribbon, Sketch environment** (replaces FEATURES while a sketch is open; a contextual tab with a
   signal top bar): Finish sketch | Line, Centreline, Rectangle ▾ (corner, centre, 3-point),
   Circle ▾ (centre, 3-point), Arc ▾ (3-point, tangent, centre), Slot, Polygon, Spline, Point, Text |
   Fillet, Chamfer | Trim, Extend, Split, Offset, Mirror, Pattern ▾ | Project/Use edge, Intersection |
   Dimension, Constraint ▾ (coincident, horizontal, vertical, parallel, perpendicular, tangent,
   concentric, equal, collinear, midpoint, symmetric, fix), Fully define | Construction, Show
   constraints, Show DOF | Resolve.
4. **Ribbon, Assembly environment.** ASSEMBLY tab: Insert component, New part, Fix, Float | Mate ▾
   (coincident, parallel, perpendicular, tangent, concentric, distance, angle, lock), Joint ▾ (rigid,
   revolute, slider, cylindrical, planar, ball) | Move, Rotate | Pattern component, Mirror component,
   Replace | Exploded view | Interference, Clearance | Motion study | BOM. Plus EVALUATE, LIBRARY,
   AI BUILD, VIEW.
5. **Ribbon, Drawing environment.** LAYOUT: New sheet, Sheet format | Base view, Projected, Auxiliary,
   Section, Detail, Broken-out, Break, Crop. ANNOTATE: Dimension, Ordinate, Hole callout | Centre mark,
   Centreline | Note, Balloon, Auto-balloon | Datum, GD&T, Surface finish (phase 2). TABLES: BOM,
   Revision table. OUTPUT: Export PDF, Export DXF, Print.
6. **Tree context menu** (feature row, in this order, with separators): Edit feature · Edit sketch
   (sketch-based only) | Roll to here · Roll to end · Roll to previous | Suppress / Unsuppress ·
   Hide / Show body | Rename (F2) · Add to new folder · Move to folder ▸ | Parents/children… · What's
   wrong… | Go to step (AI Build) · Assign to… · Ask AI to fix | Delete (Del, confirms children).
   Root node menu: Rebuild, Force rebuild, Document properties, Units ▸, Expand/collapse all.
   Rows: 24px; caret, 16px icon, step badge, name, owner outline chip, status glyph (✓ ok, spinner,
   `!` on yellow, `X` on red, dashed = suppressed). Drag reorder with parent check and a "cannot
   move above Sketch3 (parent)" tooltip. Rollback bar = 4px signal bar with a grip, ↑/↓ moves it.
7. **Sketch feedback.** Colours `--sk-under` / `--sk-full` / `--sk-over` / `--sk-driven` /
   `--sk-construction` from 5.4. Status bar text `UNDER-DEFINED · 3 DOF` (blue square),
   `FULLY DEFINED` (ink square), `OVER-DEFINED · 1 CONFLICT` (red square, click → Resolve).
   Inference glyphs (H, V, ⟂, ∥, tangent, coincident) drawn at the cursor in signal-ink with dashed
   inference lines; hold Ctrl to suppress. Resolve popover: conflicting constraints listed, actions
   Delete · Make driven · Undo · Explain. Tree sketch rows show `(-)` under-defined, nothing fully
   defined, `(+)` over-defined, matching the widespread prefix convention.
8. **Keymap (defaults; every binding editable and exportable as JSON).**

| Key | Action | Key | Action |
|---|---|---|---|
| Ctrl+N / O / S | New / Open / Save | Ctrl+Shift+S | Save as |
| Ctrl+Z / Ctrl+Y | Undo / Redo | Ctrl+B / Ctrl+Q | Rebuild / Force rebuild |
| Ctrl+K or `/` | Command search | S | Shortcut bar at cursor |
| Space | View selector | F | Zoom to fit |
| Ctrl+1…7 | Front, Back, Left, Right, Top, Bottom, Iso | Ctrl+8 / N | Normal to selection |
| Z / Shift+Z | Zoom out / in | W | Zoom to box |
| Arrows | Rotate view 15° (Shift 90°) | Enter | Repeat last command / OK |
| Esc | Cancel / exit command | Ctrl+Enter | Finish sketch |
| L / C / R / G / A | Line / Circle / Centre rect / Corner rect / Arc | D | Smart dimension |
| Q | Toggle construction | T / M | Trim / Mirror (sketch) |
| H / V / I / E | Horizontal / Vertical / Coincident / Equal | Shift+E / Shift+R | Extrude / Revolve |
| Shift+F / Shift+C | Fillet / Chamfer | Shift+H | Hole |
| Ctrl+M | Mate (assembly) | Ctrl+I | Insert component |
| F2 | Rename | Del | Delete |
| Ctrl+Tab | Next document | Alt+click | Select other |
| F5 | Selection filter bar | Shift+? | Show keymap |
| Ctrl+Shift+A | AI Build: run next step | Ctrl+Shift+F | Follow teammate |

9. **Navigation defaults.** Preset "Default" = MMB orbit, Ctrl+MMB pan, wheel zoom to cursor,
   Shift+MMB zoom drag, Alt+MMB roll; RMB = context menu (click) or marking-menu ring (press-and-drag,
   8 slots: Sketch, Extrude, Fillet, Hole, Measure, Hide, Normal to, Zoom to fit). Presets also offered:
   Onshape-style, Fusion/Inventor-style, Creo/NX-style, CATIA-style, Trackpad. First-run picker. View
   cube: faces, edges and corners clickable, drag to orbit, home button above it. Orbit centre = point
   under cursor at press (pivot marker shown for 600 ms). Animated view changes 200 ms, off with
   reduced motion. Space-mouse support via the 3D-mouse driver (phase 2).
10. **Selection.** L→R window solid outline, R→L crossing dashed outline; Ctrl toggles, Shift adds;
    Alt+click or 400 ms hold = Select Other list; filter chips (Vertex, Edge, Face, Body, Component,
    Sketch, Datum, Feature) in the viewport and F5 toggles the bar; preselect `--sk-preselect`
    (2px), select `--sk-select` (3px edges / 40% face tint).
11. **Units and templates.** Six templates from 4.7. Document Properties dialog with Units
    (MMGS/IPS/MKS/CGS/custom, decimals per quantity), Standard (ISO/ANSI), Material, Custom
    properties. Every numeric field accepts units and `=` expressions; status bar unit button.
12. **Exchange.** STEP AP214/AP242 import+export (AP242 default), IGES import, STL/3MF export with
    tolerance dialog (deviation, angle, unit, binary, per body), DXF export from face/sketch/drawing,
    PDF drawing export, export report in the REPORT tab. Import dialog: units override, heal, as
    part/assembly, keep colours/names.
13. **Drawings phase 1.** ISO A4/A3 first-angle and ANSI A/B third-angle formats with ISO 7200 title
    block fields bound to document properties; base, projected, iso, section, detail views; dimension,
    centre mark, note, balloon, BOM; PDF/DXF. Sheet is always white/ink.
14. **Design tokens.** Adopt section 5.4 as `tokens.json` (single source), generated into a Qt
    stylesheet and CSS custom properties. Light default, dark selectable, both meeting the contrast
    figures in 5.4. No radius, no shadow (except the hard popover offset), 2px structure /
    1px hairline, 4px grid, Space Grotesk + JetBrains Mono bundled under OFL.
15. **Icons.** Original 24px/2px square-cap monoline set (16px/1.5px tree variant), SVG, `currentColor`;
    AI commands carry one signal dot. No vendor glyphs or metaphors that are trade dress.
16. **Release blockers (from 4.10).** Rollback + reorder + suppress, sketch DOF/state feedback with
    Resolve, navigation presets, STEP AP214/242 round-trip with colours, document units with
    expressions, stable face references. Drawings + BOM and assembly mates + interference before 1.0.
