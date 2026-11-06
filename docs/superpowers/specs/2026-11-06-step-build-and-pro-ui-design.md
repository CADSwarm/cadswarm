# Step-by-step builds and a professional CAD workspace

Status: in progress (branch `feat/step-build-pro-ui`).

## Why

The reference is a text-to-CAD tool that builds inside a commercial CAD system: one prompt becomes a
numbered plan (for example "20 steps"), and the AI then builds **one component at a time**. Each step has
a one-sentence engineering description ("The mast: a steel shaft with a thrust collar under the gearbox…")
and a live feed of the CAD operations it runs, with timings (`Create sketch SK_Turnbuckle 1.9 s`,
`Extrude Turnbuckle … Running`, `Set color`, `Save part`). After the parts come assembly steps
(base fixed, mast turning in it, mates), then a motion/verification step. A progress line reads
`3 of 20 done · now: Pitch link`.

CADSwarm today plans "separate printable parts" and lets up to 3 workers grab any open task. Nothing
orders the work, nothing says which step depends on which, and the UI has no step view. The web app and
the FreeCAD panel also look like a FreeCAD add-on, not like a professional CAD product.

## Goals

1. **Step plan.** The planner writes an ordered, numbered plan in which every step is exactly one
   component (or one assembly/verification action), with dependencies, the objects it creates, the CAD
   operations expected, and whether the component comes from the **library** instead of being modelled.
2. **Accurate assignment.** Steps run in dependency order. A worker only gets a step whose dependencies
   are done; independent steps still run in parallel across workers and people (multiplayer stays).
   Steps can be pinned to a named teammate (person or helper).
3. **Library first.** Bought/standard components (screws, bearings, inserts, magnets, boards, motors…)
   are library steps: `recommend_components` / `search_components` then `insert_component` /
   `add_standard_part`, never re-modelled.
4. **Narrated progress.** Every client can show `N of M done · now: <step>` and, per step, the ops it ran
   with durations.
5. **Professional workspace.** A UI with the conventions of top commercial CAD tools (command ribbon,
   feature tree, property panel, heads-up view toolbar, view triad, status bar, task pane), with original
   CADSwarm styling and icons. Nothing in it should look like stock FreeCAD.

## Non-goals

- No change to the SpacetimeDB schema in this round (the CLI that regenerates the bindings cannot be run
  in the build environment). Everything new rides on `Brief.plan_json`, `Task` and `Op` as they are.
- No copying of any vendor's branding, logos, icons or trade dress. Generic CAD vocabulary only.

## Plan contract (`Brief.plan_json`, version 2)

```json
{
  "version": 2,
  "title": "Four-blade rotor head",
  "steps": [
    {
      "n": 1, "id": "s01", "name": "Mast", "kind": "part",
      "summary": "The mast: a steel shaft with a thrust collar under the gearbox.",
      "task": "t_ab12", "objects": ["Mast"], "depends_on": [],
      "ops": ["Create sketch SK_Mast", "Revolve Mast", "Set color"],
      "assignee": ""
    },
    {
      "n": 4, "id": "s04", "name": "Servo screws", "kind": "library",
      "summary": "Four M3x10 socket head screws holding the servo to the base.",
      "task": "t_cd34", "objects": ["ServoScrew1", "ServoScrew2"], "depends_on": ["s02"],
      "component": {"family": "iso4762", "size": "M3", "length": 10}
    },
    {"n": 12, "id": "s12", "name": "Assembly: base, mast, hub", "kind": "assembly",
     "summary": "Base fixed; mast on the base axis; hub on the mast.", "depends_on": ["s01", "s02", "s03"]},
    {"n": 20, "id": "s20", "name": "Check fit", "kind": "check",
     "summary": "Interference and printability across all parts.", "depends_on": ["s12"]}
  ],
  "parts": [{"name": "Mast", "task": "t_ab12", "summary": "…"}]
}
```

- `kind`: `part` (modelled), `library` (placed from the library), `assembly` (placement/constraints of
  already-built components), `check` (interference, printability, FEA).
- `depends_on`: ids of earlier steps. A step is **ready** when every dependency's task is `done`.
- `parts` is still written for older readers (the host's export and the current app read it).
- Every step has exactly one task (`task` = task id). Task titles are `"<n>. <name>"`.
- A version-1 plan (only `parts`) is read as steps with no dependencies, in list order.

## Assignment rules

- `next_step()` (new MCP tool) returns the caller's next step: a ready, open step assigned to the caller,
  else the lowest-numbered ready, open, unassigned step. It returns `{"wait": true}` when work remains but
  none is ready, and `{"done": true}` when nothing is left.
- Claiming a task whose step is not ready is refused by the local hub with `not_ready` and the list of
  unfinished dependencies (people and helpers go through the same hub).
- The host starts workers for the number of **ready** steps (capped at `MAX_WORKERS`), not open ones.
- A worker builds one step completely (sketch → feature → colour → verify → report) before taking the next.

## Progress model (all clients)

- `steps` from `plan_json`, joined with task rows by `task` id.
- Done = task `done`; current = tasks `claimed`; `N of M done · now: <names of current steps>`.
- Op feed per step: ops whose `by` is the step's task owner and whose `started_ms` lies between the
  claim and the report, plus any op whose args name one of the step's objects. Show kind (humanised),
  target, duration (`finished_ms - started_ms`) or `Running`.

## Workspace layout (web app, and mirrored in the FreeCAD workbench)

- **Title bar**: product mark, file name, quick access (New, Save, Undo, Redo, Rebuild), command search,
  teammate avatars.
- **Command ribbon** (tabbed): Features, Sketch, Evaluate, Assembly, Library, AI Build, View. Large
  icon-over-label buttons, grouped, with tooltips. A command either runs an op directly or opens the
  property panel; anything the host cannot run as a direct op is sent to the helpers as a precise
  instruction.
- **Left pane** tabs: Feature tree (parts, owner/lock, step number, status), Properties (the active
  command's inputs with OK/Cancel), Parameters, Library.
- **Viewport**: heads-up view toolbar (fit, view orientation, display style, section, hide/show), view
  triad, neutral gradient background, teammates' pointers.
- **Task pane** (right, collapsible): AI Build (prompt, step list, live op feed, progress), Team (members,
  chat), Review.
- **Status bar**: units (mm), rebuild/connection state, what is selected, who is editing.
- Phone widths keep the bottom sheet; the ribbon collapses to an overflow menu.
