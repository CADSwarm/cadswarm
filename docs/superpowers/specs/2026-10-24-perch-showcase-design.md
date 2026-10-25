# The Perch: a showcase of impact and technical rigor

Date: 2026-10-24
Status: approved (design dialogue, sections 1–3); commercial framing removed, impact case and
judging map added at review; the judging-table run is a live Act 2 build, not a static snapshot.
Video, pitch script and table card are **deferred** — the project started 2026-10-23, so the live
demo comes first and the recording follows it.
Branch: `feat/perch-showcase` (worktree, based on `origin/main` `6f21059`)

## Goal

Build one physical project that makes two things undeniable in a live demo:

1. **Impact.** A team that can wire a board but cannot use CAD gets a correct, strong, light,
   print-ready part from a sentence — and gets the second part for the same product far faster,
   because the board's measurements, the team's style and the reviewer's lessons were remembered.
   The object is not a toy: it holds a real dev board at eye level with both ports usable.
2. **Technical rigor.** Every claim is backed by a mechanism and a number: fits computed from shared
   parameters, a cantilever simulated before it is lightened, a lattice verified against a stiffness
   target, a reviewer that cannot write, a dead agent taken over mid-build, and two agents kept from
   overwriting each other by rules in the database rather than in a prompt.

The project is **The Perch**: a clip-on articulated arm that holds an ESP32-S3-DevKitC-1 at eye
level with both hands free, plus a second **Podium** display head for the same board and the same
arm. It is performed as two briefs against one live model, with Claude Code, OpenCode and Creao AI
helpers, a Droid reviewer, SpacetimeDB as the team's state, and Elastic as board knowledge, design
memory and lessons.

The deliverable is the printable object family plus a runbook that can be performed live, pitched in
two minutes at a judging table, or recorded. The sponsor stack is not a logo wall: each partner does
a job the build would fail without, and the five-card window is the live receipt.

## What the room should be able to say

Cal Hacks judges on impact, quality, technical complexity and creativity, and asks for a two-minute
pitch plus a live demo. The demo is built backwards from those four words.

1. **This solves a problem I have seen.** A board on a desk slides, faces the wrong way and eats a
   hand while you probe it. The Perch clamps on, holds it at eye level, and leaves both USB-C ports
   reachable.
2. **The part is engineered, not drawn.** A simulated cantilever with a real deflection and safety
   factor, a lattice that hits 90% stiffness with 30–45% less mass, a 608 press fit that tightens
   when one shared tolerance changes, a lid that screws into heat-set inserts, and every part checked
   against a 180 mm bed.
3. **The team cannot wreck it.** Locks and fences stop two agents editing one part; a dead helper is
   taken over instead of blocking the build; a person can hand a half-finished part to an agent; the
   reviewer can only inspect and its findings become tasks.
4. **It gets better at the next job.** The style the team teaches by correcting build one shows up in
   build two's prompts; build one and its review lessons are recalled for a differently worded
   request; the second head is a new design on the same mount, not a parameter tweak.

### Judging map

| Criterion | What this demo puts in front of the judge |
|---|---|
| **Impact** | a named community with a lived problem (hardware teams and student makerspaces that can wire but not CAD) and a part they can use on Monday |
| **Quality** | two review rounds with real findings, clash and min-wall checks, a closed solid, a real bed, exportable STEP/STL |
| **Technical complexity** | rules in the database (fences, locks, stale sweep, takeover), a transactional FreeCAD worker, expression-driven fits, FEA, lattice verification, memory recall |
| **Creativity** | two agents and two people building one physical object live; a phone-submitted second brief that reuses the first; a referee agent that cannot touch the model |
| **Live demo** (required) | every act runs live in a fresh document; no slide can show this |
| **Anthropic track** (named community, real problem, it works, thoughtful use, known failure modes) | the community in one sentence, the object in the room, one thing done well, and the failure-mode table below |
| **Factory track** (Droid SDK, a trigger you designed, safety, visible work) | the host triggers the reviewer when parts finish; an allowlist plus a server-side write refusal; the activity feed shows plans, steps and the verdict |
| **Creao tracks** (agent-first disruption, team empowerment) | a Creao helper takes a real part, one tool call per turn; two people plus four kinds of agent share one model; a person hands a part to an agent and takes it back |
| **Elastic track** (depth, JINA, Agent Builder + Workflows, Serverless) | the four-row table below, shown in Kibana and on the card |

Best Hardware is out (no printer). The two general prizes this build is shaped for are **Most
Technically Complex** and **Best People-First Hack**. The sponsor tracks it targets are Anthropic,
Factory, Creao and Elastic.

### Creao, on its tracks

Creao's tracks reward agents that do real work and teams that are empowered by them. The build is
that shape, not a chatbot demo:

- **Agent-first disruption.** A Creao helper takes a real part — the cradle lid, and the podium lid
  after the restart — and every turn is one JSON tool call on the card, not a conversation.
- **Team empowerment.** Two people and four kinds of agent share one model. The humans keep
  authority: they set shared parameters, approve the style rules, take a lock back, and hand a
  half-finished clamp to an agent with a note. The takeover beat is the same story in the other
  direction: when the helper stops, the team does not.

### Elastic, on the poster's four criteria

The booth poster is explicit: qualified projects must use Elasticsearch, and are judged on four
things. This is where each one lives.

| Poster criterion | What this build does | Where a judge sees it |
|---|---|---|
| **Depth and creativity of the implementation** | hybrid `semantic_text` search over measured board datasheets, ES\|QL filtering by `board_id`, design memory written back by the host and recalled by meaning, lessons distilled from review findings | Kibana: the three indices, the ingest pipeline, the host's `index_design` round trip, `recall_designs` |
| **JINA models for embeddings** | the `cadswarm-jina` endpoint embeds on ingest and on query; `.jina-reranker-v3.5` reranks the candidate passages | a board question whose reranked hit is the right passage, plus the inference endpoint list |
| **Elastic Agent Builder and Workflows** | `cadswarm-board-expert` with four tools (board docs, lessons, design memory, team style) and the `distil-lessons` Workflow on a 10-minute schedule | a live `converse` when the quota answers, the agent's tool list, and a Workflow execution with both steps completed |
| **Elastic Cloud Serverless** | the project is Elasticsearch Serverless 9.6 in us-east-1, with Kibana on the same project | the project page and the Kibana URL the host uses |

Agent Builder is quota-limited on the trial tier: when the model quota is exhausted, `converse`
returns a bare `429`. The checklist probes it in pre-flight. If it does not answer during judging,
we show the agent definition, its four tools and the last successful trace, and say the quota is the
limit. The other three criteria do not depend on that model.

### Sponsor stack, equally load-bearing

| Sponsor | Its job in this build | What the live card proves |
|---|---|---|
| **SpacetimeDB** | every member, lock, op, task and revision in one database | the shared state is real, not a slide |
| **Anthropic** | planner, lead and worker agents plan, build and hand off | the design work is done by Claude agents, visible in their traces |
| **Creao** | a second vendor's agent takes a real part, one tool call per turn | the agent does the work, and a person still holds the lock |
| **Factory.ai** | Droid reviews each round and files findings as tasks | a referee that cannot write, and knows when to ask |
| **Elastic** | JINA-backed board search, design memory, FEA reports, distilled lessons | the second job starts from what the first learned |

## Who it is for, and why it matters

This is a usefulness demo, not a business pitch: no pricing, no market sizing, no customer claim.

### The community

**Hardware teams and student makerspaces that can wire a board but cannot use CAD.** They buy the
dev board, write the firmware, and then hit the case, the mount, the bracket — the part that has no
tutorial. A hackathon team is the sharpest version of this: 36 hours, no CAD seat, and a physical
demo that needs a housing.

### The problem, lived

The part is small, boring and blocking. It is too small to hire for (a freelancer quotes $600–2,000
and 3–10 days), too specific for a marketplace (two rounds of re-explaining, and no memory of the
last part), and too slow to learn for the weekend (10–30 h in Fusion before the first correct fit).
Most teams tape the board down and hope the demo survives.

### What changes for them

| Before | After |
|---|---|
| the board slides, faces away, or is held in one hand | clamped at eye level, both ports reachable |
| the case is a sketch at 3 a.m. | a print-ready STEP/STL with the ports, the lid and the fits resolved |
| the first print discovers the errors | the reviewer finds the missing port cutout, the short inner height and the thin wall before the printer |
| the next part is a new project | the next head reuses the mount, the fits and the style |
| the part is a one-off | the design and its lessons persist for the next team member, or the next board |

## Impact, measured

Every number below is recorded in `demo/perch/rehearsal.md` and quoted in the pitch; none is a
marketing estimate.

| Impact | How it is measured | What a judge sees |
|---|---|---|
| Time to a correct part | wall clock from brief submission to exported STL, per act | the runbook's clock and `rehearsal.md` |
| Defects caught before printing | review findings that are real, and the fixed part | the two review rounds and the diff |
| Material saved | lattice mass before and after at verified stiffness | the lattice report and both STLs |
| Reuse | the second head's wall clock and human minutes against the first | the two measured rows, side by side |
| Access | the whole build is driven from a brief, a phone and corrections — no CAD commands typed by a person | the live demo and the panel's guide |
| Outside hands | three people outside the team, logged with what each actually did and how long it took | the clips, the run logs behind them, and the person's own words |

The last two are the impact claim that matters most for this community: a person who cannot model can
still end up with a part an engineer would sign. Outside testers make that point better than we can;
until the video is cut, the demo has to carry it.

### Outside testers

Three kinds of people outside the team have used this, and every clip says which one it was:

| What the person did | What the clip may say | What it may not say |
|---|---|---|
| drove a run themselves on a laptop, presenter beside them | "I typed the brief and it built" | "I use this", "we use it at work" |
| rebuilt a part they had modelled by hand, from a sentence | that they rebuilt their own part, and the elapsed time **only if the screen or the run log shows it** | "in seconds" unless that time is on screen or in the log, or that it beat their own CAD work |
| watched a run and typed the prompt themselves | what they watched and what they typed | that they own, printed or shipped the result |

No one outside the team has held a printed Perch — there is no printer — so no clip claims use of the
object; the clips are about the software. Each names the person's role and the date, is cut from a
full recording kept in `demo/perch/testimonials/`, and is never scripted by us.

## What makes it technically hard

Impressiveness has to survive a judge asking "how?". One line per hard problem, with the mechanism
and the artifact that proves it.

| Hard problem | Mechanism | Proof |
|---|---|---|
| Two agents must not overwrite one part | object locks and fenced leases enforced in the SpacetimeDB reducers, not in prompts | the refused op in Activity, the lock row, revisions |
| A plausible shape can still be wrong | the reviewer inspects measured board fit, ports, clashes and printability, then files tasks | review rounds 1 and 2 and the fixed geometry |
| Fits must move together | `Params` aliases with FreeCAD expressions, applied by auto-constrain | the bearing seat shrinking when `tol_press_fit` changes |
| A light part must still be strong | linear-static FEA at a stated load, then lattice verification against a stiffness target | the FEA report and the verified lattice |
| A dead helper must not block a delivery | heartbeats, stale detection, takeover with a new fence | the stale member, the takeover row, the finished lid |
| A person and an agent must share one part | handoff of locks, tasks and a note, with the agent continuing from recent ops | the handoff row and the agent's reply |
| The second job must start smarter | board datasheets, finished designs and distilled lessons in Elastic, recalled by meaning | `recall_designs` returning Act 1, and five lesson documents |
| The model must survive a crash | one worker owns the file; every op commits transactionally with a save | the repo's tests and the host's commit path |

### Where it could go wrong, and what we do about it

The Anthropic track asks a team to know where their use of the model could fail. Our answers are
mechanisms, not promises.

| Failure mode | What catches it |
|---|---|
| an agent invents a board dimension | the measured board library seeds `Params`; the reviewer checks the fit against it |
| an agent overwrites someone else's part | locks and fences in the database; the op is refused |
| an agent "fixes" the model while reviewing | the reviewer is inspect-only and the module refuses its writes |
| a helper dies mid-task | stale detection and takeover; the build continues |
| a mesh looks fine but cannot be printed | bed, min-wall, closed-solid and clash checks before export |
| the model is too weak in the hand | FEA with a stated load and a safety factor, before anyone prints |
| memory returns the wrong design | recall is board-filtered and ranked; the demo shows the result and its problems |

## The object

### Family

- **The Perch** — desk clamp, 608-bearing shoulder, two printable arm segments, a dovetail head
  mount. The arm reaches 160 mm after the shared-size steer; both segments print on a 180 mm bed.
- **Cradle head (Act 1)** — a tray for the ESP32-S3-DevKitC-1 with side rails (the board has no
  mounting holes), two USB-C cutouts, and a vented screw-on lid.
- **Podium head (Act 2)** — the same board upright behind an OLED window, plus a 60 mm medallion
  that spins on a 608 bearing. This is the second real design: the board and the team's award are
  visible at eye level, on the same arm, for a fraction of the first head's design time.

The unit is the **family**, not a part: one arm, a mount, and a head per use. That is why the demo
builds two heads — it shows reuse and memory in one object, instead of one lucky part.

### Parts and owners

| Part | Act | Owner | Purpose | Features it carries |
|---|---|---|---|---|
| `Clamp_Base` | 1 | host Claude worker, then human, then `base-claude` | C-clamp, M8 screw + captured nut + heat-set insert | handoff, library, insert fit |
| `Shoulder_Hub` | 1 | `lead-claude` | 608 press-fit housing + M8 axle | press fit, auto-constrain, bearing |
| `Arm_Proximal` | 1 | `arm-opencode` | 85 mm segment, dovetail + 4× M4 inserts | shared sizes, pattern |
| `Arm_Distal` | 1 | `arm-opencode` | 85 mm cantilever, elbow friction joint, head dovetail | **FEA, Lighten**, fit |
| `Cradle_Base` | 1 | host Claude worker | board tray, two USB-C cutouts, side rails | board fit, ports, Elastic |
| `Cradle_Lid` | 1 | `lid-creao` | vented lid, 2× M3 inserts, 2.4 mm wall | **takeover**, style, min wall |
| `Podium_Body` | 2 | `lead-claude` | board upright, OLED window, 608 medallion seat, same dovetail | design memory, board fit |
| `Medallion` | 2 | `arm-opencode` | 60 mm disc, 8 mm dowel pin axle | second bearing, press/slip fit, clash |
| `Podium_Lid` | 2 | `lid-creao` | back cover, 2.4 mm wall | **style finding** |

### Shared parameters

The planner seeds and owns `Params`; every part binds to it with FreeCAD expressions.

| Alias | Value | Used by |
|---|---|---|
| `pcb_l`, `pcb_w`, `pcb_t` | 62.7, 25.4, 1.6 mm | seeded from the board library |
| `usb_uart_pos`, `usb_uart_z`, `usb_uart_w`, `usb_uart_h` | 6.4, 1.6, 9.5, 3.6 mm | seeded |
| `usb_otg_pos`, `usb_otg_z`, `usb_otg_w`, `usb_otg_h` | 19.0, 1.6, 9.5, 3.6 mm | seeded |
| `gap` | 0.5 mm | seeded; board clearance |
| `wall` | 2.4 mm | cradle lid, podium lid, clamp |
| `tol_press_fit` | −0.05 mm | 608 seats, medallion pin |
| `tol_slip_fit` | 0.1 mm | M3/M4 clearance, dovetail |
| `tol_clearance` | 0.2 mm | clamp jaw, head swap |
| `bearing_od`, `bearing_id`, `bearing_b` | 22, 8, 7 mm | 608 family |
| `mount_w`, `mount_t`, `mount_dovetail` | 20 mm, 6 mm, 15° | both heads, distal arm |
| `arm_reach` | 170 mm, steered to 160 mm | segment lengths and head position |
| `standoff_h` | not used (no board holes) | — |

Bed: the team's printer bed is set to **180 × 180 × 180 mm**, the product default. Materials: PETG
for `Clamp_Base`, `Shoulder_Hub`, `Arm_Proximal`, `Arm_Distal`; PLA for the cradle and podium heads.
FEA uses the PETG preset (E = 2100 MPa, yield 45 MPa).

### Why this object, not a generic enclosure

Each feature is forced by the object rather than bolted on:

- The **cantilever** makes FEA and Lighten meaningful; a box has no interesting load path.
- The **608 shoulder** makes a press fit and auto-constrain meaningful.
- The **two USB-C ports** make board fit and port cutouts concrete, and give the reviewer something
  real to check.
- The **two segments** and 180 mm bed make printability a design constraint, not a checkbox.
- The **second head** makes design memory, lessons and style a new design, not a repeat.
- The **board has no mounting holes**, so the cradle must use rails — a real design decision the
  reviewer can reason about.
- The **board-specific fit** is the engineering a generic Printables box cannot do: the ports, the
  measured length and the no-holes rail solution.
- The **second head** is a second design on one mount, so the demo measures reuse instead of
  claiming it.
- The **180 mm bed and stock hardware** are what a student makerspace already owns, so the part is
  reproducible without a special machine.

## Engineering spine: auto-constrain → FEA → Lighten

All three land on `Arm_Distal`, in this order, so they read as one engineering decision.

### 1. Auto-constrain (person)

After the builders release their parts, the presenter runs **Tools ▸ Auto-constrain** on the arm
and hub. The preview proposes:

- `arm_w` and `arm_t` shared between `Arm_Proximal` and `Arm_Distal`;
- the 4× M4 pattern on the hub and proximal dovetail;
- the bearing fit: `Radius = Params.bearing_od / 2 + Params.tol_press_fit`.

Apply, then change `tol_press_fit` from −0.05 to −0.08 mm in the panel. The hub bore recomputes and
the seated bearing is visibly tighter. The point: fits are shared geometry that every part follows,
not text in a settings dialog.

The 608 bearing is inserted from the library for the fit preview, then hidden before interference
and printability checks so an intentional press-fit overlap is not reported as a clash. Screws stay
visible in clearance holes.

### 2. FEA (`arm-opencode`)

Linear static on `Arm_Distal`:

- material PETG;
- fixed at the elbow clevis faces;
- 4.9 N (500 g) down at the head end;
- publish the report with the helper's own improvements.

Expected: tip deflection around 0.6 mm and a safety factor around 20 — deliberately overbuilt. The
report pops up in every teammate's FreeCAD, is saved under `files/fea/`, and is indexed into
Elastic design memory as a simulation document. The actual numbers are recorded in rehearsal and
used in the pitch; they are not hard-coded in the runbook.

### 3. Lighten (`arm-opencode`, person adds)

`lighten_part` on `Arm_Distal`:

- 90% stiffness target;
- gyroid cells, 5 mm;
- 2.4 mm solid skin;
- held at the elbow, loaded at the head;
- verify the real lattice, not an estimate.

Expected mass saving 30–45% at the verified stiffness. The preview can be cut open; **Add to model**
puts the lattice mesh beside the original and leaves the original untouched. Export both STLs and
compare mass. This runs after review round 2 so the mesh never confuses the reviewer; the lattice
verifier console is shown live while export and indexing continue.

### Printability

`check_printability` on all printed parts against 180³: every part fits, is a closed solid, and has
no wall under 1.2 mm after the fix. This is the only hardware claim: print-ready, not printed.

## Two-act runbook

### Cast

| Who | Client | Job |
|---|---|---|
| `planner-claude` | Claude Code, host-spawned as `planner-<brief>` | plan the brief into assigned tasks, then stop |
| `lead-claude` | Claude Code, manual | `Shoulder_Hub`, Params, coordination, takeover |
| `worker-1/2/3-claude` | Claude Code, host-spawned as `worker<n>-<brief>` | unassigned tasks: `Clamp_Base`, `Cradle_Base` |
| `arm-opencode` | OpenCode | arm segments, FEA, Lighten |
| `lid-creao` | Creao AI bridge | cradle lid; killed for takeover; returns for Act 2 |
| `base-claude` | Claude Code, handoff-spawned as `handoff-<id>` | finishes the clamp after the human |
| `reviewer` | Droid | review rounds 1 and 2 |
| you / Sam | FreeCAD / phone | build, steer, correct, submit Act 2 from the phone |

The host-spawned names above are call-sheet labels; the actual member names are `planner-<brief>`,
`worker<n>-<brief>` and `handoff-<id>`, as the panel's Team tab shows them.

All manual helpers join before the brief so the planner can assign by name. The host starts up to
three Claude workers; they take the unassigned clamp and cradle tasks. Every manual task is assigned
by name so a free host worker cannot poach it (the module only rebalances tasks whose assignee is
busy or stale).

### Act 1 — "The Perch"

Brief, typed in the panel:

> Build **The Perch**: a clip-on articulated arm that holds an ESP32-S3-DevKitC-1 at eye level, with
> a cradle head. I'll edit the clamp myself. Plan: `Clamp_Base` (unassigned), `Shoulder_Hub` →
> lead-claude, `Arm_Proximal` + `Arm_Distal` → arm-opencode, `Cradle_Base` (unassigned),
> `Cradle_Lid` → lid-creao. Look up the ESP32-S3 with `lookup_board` and use its measured Params.
> Two printable arm segments joined by M4 screws and heat-set inserts, a 608 bearing at the
> shoulder, a dovetail head mount. Both USB-C ports must be reachable. Every part must fit a 180 mm
> bed and follow our walls and fits. The distal arm is what we'll simulate and lighten.

| # | Beat | Time | Action / line | What the room sees |
|---|---|---|---|---|
| 1 | Cold open | 1′ | Sponsor window on the second screen | The five cards are in place and reading live state (helpers join at T-30; the reviewer reads `stale` between rounds) and the board is bare; Act 1 starts now |
| 2 | Plan + board | 2′ | Submit the brief | Planner calls `lookup_board`; board Params fill; Elastic card shows `esp32-s3-devkitc-1`; tasks appear with names |
| 3 | Build | 8–12′ | Let the builders work | Parts in owner colors; Creao's one-call-per-turn trace; Claude workers take clamp/cradle |
| 4 | Protection | 1′ | `@lid-creao thicken Arm_Distal by 2 mm; it flexes.` | Refused "locked by arm-opencode"; it messages the owner |
| 5 | Shared size | 1′ | Tools ▸ Change a shared size…: `arm_reach = 160 mm` | Arm segments and head positions recompute; owners notified |
| 6 | Auto-constrain | 2′ | Owners release; Tools ▸ Auto-constrain… → preview → apply; then `tol_press_fit = -0.08 mm` | Proposed aliases/pattern/fit; hub bore visibly shrinks |
| 7 | FEA | 3–6′ | `@arm-opencode linear static on Arm_Distal: fixed at the elbow, 4.9 N down at the head, PETG. Publish it and tell us if it's overbuilt.` | Report pops up; deflection and safety factor; Elastic indexes it |
| 8 | Kill + takeover | 2′ | Kill the Creao bridge mid-lid; wait for stale; `@lead-claude take_over("lid-creao")` | Creao card goes stale and its trace stops; Spacetime lock row moves; lead finishes the lid |
| 9 | Handoff | 2–3′ | You hold `Clamp_Base`, edit the M8 pocket, and let go; Sam takes the lock in the phone's Model tab, sends the note below in Team chat, arms **Continue my work if I leave**, and closes the tab | Sam goes quiet; the host opens the handoff and spawns `handoff-<id>`; it accepts, finishes, and messages back |
| 10 | Review 1 | 2–4′ | Automatic when tasks are done | Droid pointer and trace; three findings: missing USB-OTG cutout, cradle inner height 8.0 < 11.1 mm, lid wall 1.0 mm |
| 11 | Correct + style | 2–3′ | Human sets the lid wall to 2.4 mm and saves; Tools ▸ Learned design style: add the two rules below | Human correction recorded; rules active; later turns carry "Team design style" |
| 12 | Review 2 | 1–2′ | Fix tasks, automatic re-review | Factory card: round 2 · approved |
| 13 | Snapshot + Lighten | 5–10′ | Save snapshot `act-1-ready`; `@arm-opencode lighten Arm_Distal to 90% stiffness, gyroid, 5 mm cells, 2.4 mm solid skin, held at the elbow, loaded at the head.` | Verifier console; mass saved; cut-open preview; `Add to model` leaves the original |
| 14 | Export + memory | 1–2′ | Export STEP + STL; run `distil-lessons`; open Kibana on the board search, the design doc and the Workflow execution | `cadswarm-designs` doc with params + 3 problems; 3 lesson docs; reranked hit, Workflow steps completed, Agent Builder trace if the quota answers |
| 15 | Sponsor recap | 1′ | Leave the window up; restart Creao for Act 2 | Spacetime counts, Anthropic traces, Creao live again, Factory verdict, Elastic findings |

Handoff note:

> Jaw opening is 34 mm; the M8 nut pocket is 0.2 mm tight. Finish the insert pocket, add 0.4 mm
> clearance, keep 2.4 mm walls. I've left the lock on the clamp for you.

Style rules:

1. Printed enclosure walls are 2.4 mm.
2. Lids fasten with M3 heat-set inserts, not snap tabs.
3. Printed levers and arms get 3 mm outer fillets.

**Impact read of Act 1.** The part that lands at beat 14 is the deliverable. The clock starts at
beat 2 and stops at beat 14; that wall clock, the human minutes at beats 5, 6, 9, 11 and 13, and the
three defects caught at beat 10 are recorded in `rehearsal.md` and become the impact numbers in the
pitch.

**Elastic evidence, prepared.** Before beat 1, four Kibana tabs are already open: the reranked board
query, `cadswarm-designs`, the `distil-lessons` execution, and `cadswarm-board-expert`. They are
booth setup, not a slide, and they are what the Elastic track is judged on.

### Act 2 — "The Podium" (from Sam's phone)

Brief, sent from the phone:

> Make the **Podium** head for the same Perch: the same ESP32-S3 upright, an OLED window, and a
> 60 mm medallion that spins on a 608 bearing. Reuse the arm and the mount. Before planning, recall
> the Perch we just built and use the lessons from its review. Assign `Podium_Body` → lead-claude,
> `Medallion` → arm-opencode, `Podium_Lid` → lid-creao. Same 2.4 mm walls, same fits, same 180 mm
> bed.

| # | Beat | Time | What happens |
|---|---|---|---|
| 1 | Phone brief | 1′ | Brief appears in FreeCAD and web; the remote path is live |
| 2 | Recall | 1′ | Planner calls `recall_designs`; Act 1 returns (first, or second to a closer match); Elastic card's design memory shows it |
| 3 | Build | 6–10′ | Three parts; the cradle is moved aside; the lattice mesh is hidden; the arm stays |
| 4 | Review 1 | 2–3′ | Medallion clashes with the body by 0.4 mm; `Podium_Lid` is 2.0 mm against the 2.4 mm rule (a `style:` finding) |
| 5 | Fix + Review 2 | 2–3′ | Both fixed; round 2 approved; Act 1's lessons prevented a repeat port/depth mistake |
| 6 | Second design memory | 1′ | Export; second design doc; `recall_designs` returns both; lessons grow to 5 |
| 7 | Close | 1′ | Sponsor window + reports + STL preview; both heads on one arm and the next-board path on screen; "print-ready, designed on a 180 mm bed" |

**At the judging table the brief asks for two parts, not three.** Two minutes cannot build the
medallion, so the table run uses a shorter brief with the same wording, the same owners and the same
recall:

> Make the **Podium** head for the same Perch: the same ESP32-S3 upright, a 2.4 mm lid, the same fits,
> the same 180 mm bed. Reuse the arm and the mount. **Recall the Perch we just built before
> planning**, and use what its review found. `Podium_Body` → lead-claude, `Podium_Lid` → lid-creao.

The medallion and its 608 bearing are booth only, and the presenter says so rather than implying the
head is finished. The table claims four things it can actually land in the ninety seconds after the
press: the recall returns Act 1, the tasks appear with the same owners, a locked part refuses a
helper, and a real part starts to build. The finished podium on the arm is the rehearsal's artifact,
labelled as such.

The recall query is the brief's own text: the host passes `b.text` straight to `recall_designs`, so
the table's begins "Make the Podium head for the same Perch…" and the booth's adds "… with an OLED
window and a spinning medallion". That wording already differs from Act 1's request, so the memory is
matching a second design, not replaying the first. Act 1 must return first (or second only to a closer
match), and its own three problems must be visible: the helper logs one `recall` activity row per
returned design, each with the design's rank, request and own problem count.

**Impact read of Act 2.** Same clock, same measurement, second design. The pitch puts the two rows
side by side: the delta is the reuse dividend, and it is the only number that has to be believable.
If the delta is small, we say so and the claim changes.

The table run measures a different four numbers from the button press — t(recall), t(tasks),
t(first op), t(finish) — because the table claims recall, task creation and a part starting, not a
finished head. They are recorded in `rehearsal.md` beside the full-run clock, and the pitch quotes
whichever one it is actually showing.

### Sponsor mapping, beat by beat

| Beat | SpacetimeDB | Anthropic | Creao | Factory | Elastic |
|---|---|---|---|---|---|
| Plan/build | members, locks, ops, events | planner + worker trace | one-call-per-turn trace | — | board lookup |
| Refusal | lock row + refused op | — | refusal in its trace | — | — |
| Takeover | stale → fence → takeover | lead trace | card goes stale | — | — |
| Handoff | handoff + lock transfer | new agent joins | — | — | style observation |
| Review 1 | review + tasks | — | — | pointer, trace, 3 findings | findings become design memory |
| Fix/style | revisions | fixing trace | — | style check | style/usage mirror |
| Review 2 | review approved | — | — | approved | — |
| FEA | op/result | — | — | — | report indexed |
| Lighten | op/artifact | — | — | — | — |
| Export | artifact | — | — | — | design doc |
| Workflow | — | — | — | — | 3 lessons |
| Act 2 | new brief/members | build trace | live again | 2 new findings | recall + 5 lessons |

Read a column for one partner's arc, a row for what the room sees at that moment. Every row is a
claim the demo makes, and the card on screen is the evidence that it happened. The judging-table run
covers the Act 2 row only as far as the recall, the tasks, the refusal and the first part; the review
findings, the new lessons and the finished head in that row belong to the booth run.

### Three formats

The guide's judging loop sets the shapes: judges walk the tables between 10:30 and 12:30, each team
gets about two minutes plus a live demo, and projects are judged on impact, quality, technical
complexity and creativity. There is no poster: the laptop, the second screen and a one-page evidence
card on the table do that job.

| Format | Length | Act 1 | Act 2 | FEA/Lighten | Elastic |
|---|---|---|---|---|---|
| **Judging table** | 2′ + Q&A | restore `act-1-ready`; Act 1's design doc and lessons are already in Elastic; reports open | **live from the phone at 0:30, still building through Q&A** | pre-run, shown while the build runs | recall live, lessons pre-run |
| **Full live** (booth) | 40–50′ | live | live, all three parts | live | Workflow run live |
| **Video** | 2:45 | timelapse | live or recorded | cutaways | Kibana cutaways |

Only the judging table and the booth are in scope now. The video is the recording plan for later;
nothing in this build depends on it.

The two-minute run of show. The product is never sitting still: the build starts at 0:30 and does not
stop when the pitch does.

| Time | Beat |
|---|---|
| 0:00–0:15 | the problem, one sentence, bare board in hand |
| 0:15–0:30 | The Perch on screen: the arm and the cradle; "the second head is the test" |
| 0:30–0:40 | **press the button**; the Podium brief leaves Sam's phone |
| 0:40–1:00 | the planner recalls the Perch before it plans; Act 1's design doc and its three problems return |
| 1:00–1:15 | tasks appear with named owners; the Creao mention goes out on the held body |
| 1:15–1:30 | the refusal lands on Creao's card; the first part starts to build |
| 1:30–1:45 | one artifact each from FEA, the lattice and round 1 while the build runs |
| 1:45–2:00 | who it is for; the sponsor strip; "it is still building" — the run continues through Q&A |

**The build outlives the pitch.** The driver does not stop at 2:00. A judge who stays watches the
body take shape and the tasks turn over; a judge who asks "what happens next" gets to see it. The
panel clock keeps the claim honest: the pitch claims recall, tasks, a refusal and a part starting,
not a finished head. `rehearsal.md` records when each of those landed, and that is what the impact
table quotes.

The video is **2:45**, and the outside block is 30 seconds of it. **Deferred: none of this is in the
current plan**, but the shape is fixed now so the demo does not have to change later:

| Time | Content |
|---|---|
| 0:00–0:25 | the problem and the object: the bare board, the Perch, both ports |
| 0:25–1:40 | the build as a timelapse: auto-constrain, FEA, the lattice, both review rounds, the takeover, the handoff |
| 1:40–2:10 | **the outside block**: three clips of ~10 s, one per kind — drove a run, rebuilt their own part, watched and prompted |
| 2:10–2:45 | Act 2 from the phone, the recall, and the two heads on one arm |

Each clip is the person's own words over a caption naming their role and the date, cut from a full
recording we keep. They are not called customers, their words are not scripted, and no clip implies a
printed Perch.

### Demo driver: one button, a live Act 2 build

A canned video cannot answer a judge's "try this", and a finished model on screen cannot show that
the product is doing anything. The answer is a thin local driver with one big button: a person
presses it, the Podium brief goes out from the phone, and the real helpers start building against the
real models. The sequence is a file, so it can change between judging visits.

#### Why it lives outside the product

`demo/perch/driver.py` joins as a human presenter and calls the same reducers the panel does —
submit a brief, set a param, say something — and reads the same snapshot the cards read. The host
prints one extra human invite for it, and it appears in the team as a normal human member, so
nothing in the state is special-cased. Nothing ships with FreeCAD, the host or the module. The
sequence is `timeline.json` (booth) or `table.json` (judging), editable the night before.

#### The sequence waits on state, not seconds

| # | Action | Done when | Deadline |
|---|---|---|---|
| 1 | Sam sends the table brief from the phone (the driver shows it; Sam pastes and taps Send) | the brief appears in FreeCAD and the web, posted by the phone's member | 10 s |
| 2 | The planner recalls the Perch before it plans | the `recall_designs` op returns Act 1's design doc, its three problems and the lesson list | 25 s |
| 3 | Tasks appear with named owners | `Podium_Body` and `Podium_Lid` rows exist with owners, and no free host worker took them | 30 s |
| 4 | The presenter types `@lid-creao thicken Podium_Body by 2 mm` (the driver sends it) | an op is refused for the held body and the refusal shows on Creao's card | 40 s |
| 5 | The first part starts to land | a revision or op exists for either part — **never the whole build** | 45 s |

Steps 4 and 5 overlap the plan: the mention goes out once the body is held, and the builders are
already working, so the refusal and the first op can arrive in either order. Step 4 depends on the
body still being held at that moment; if the lock has moved, the driver skips it and says so.

One gate is absolute: **the first op by 1:45**. Before that, a step that misses its deadline is
skipped and said out loud. These numbers are rehearsal targets; `rehearsal.md` records the measured
waits, and those — not the targets — are what the pitch quotes.

After step 5 the driver stops advancing and just watches: the build keeps running through Q&A, and
the task list on the second screen is the honest progress bar.

The booth run uses the long timeline — brief, build, refusal, takeover, handoff, review, fix, FEA,
lighten, export, recall — on the same engine.

#### Controls

- **Start** — arms the sequence and shows the brief to paste on the phone; Sam sends it. If the tunnel
  is down, Start submits the same brief from the laptop and says so.
- **Next / Skip** — advance now; the driver logs the step as skipped and moves on.
- **Pause / Resume** — hold the sequence while answering a judge; the build keeps going.
- **Say box** — free text that submits a real brief or steer, so a judge's own scenario runs live.
- **Probe** — checks Claude, Creao, Droid and Elastic before Start and refuses to begin if one fails.

#### Rules

- **State is the checkpoint.** A step is done when the module says so: task, op, lock, review, bbox,
  artifact. Never treat an agent's prose as evidence.
- **Never wait for the finish.** The table claims recall, tasks, the refusal and a part starting.
  The finished podium belongs to the booth run and `rehearsal.md`.
- **Every wait has a deadline.** A timed-out step is logged and skipped out loud, not waited on in
  silence.
- **The driver is a presenter, not an agent.** It never writes geometry, claims a task or posts as a
  helper; all model work is the real helpers'.
- **Every run is logged** with step, wait and outcome, and those logs are where the measured impact
  numbers come from.

#### If it stops

`table-safe.json` is a pre-flight choice, not a mid-pitch trick. If the last rehearsal did not start a
part before 1:30, the table runs the safe sequence from the start: the same brief as a lid-only job
("the same Podium lid, 2.4 mm wall, same board and fits; recall the Perch before planning"), which is
one small part a single helper can start fast. The presenter says the body is the next run.

Mid-run, if nothing has started by 1:45, the driver says so on the status line and stops claiming a
part. The presenter points at the recall and the tasks that did land, replays the booth run's screen
recording labelled as a replay, and says plainly which is live and which is recorded. The fallback is
a smaller live job or a labelled replay, never a recorded run passed off as live.

#### Reset between judges

Restore `act-1-ready`, restart the helpers, and restart `driver.py` (the runner refuses a second Start),
and leave Act 1's design doc and lessons in Elastic. A previous table run's podium document stays too; if it ranks first for the next recall, say
so out loud — the system is returning our own earlier run, which is the point. The reset order and its
timing are in `checklist.md`, and the driver refuses to Start until the state is back.

If the reset cannot finish before the next judge, do not fake it: the next pitch starts from the
running Act 2, says the recall already happened, and lets the panel and `rehearsal.md` carry Act 1. The
reset is rehearsed and timed for exactly this reason.

### Fallbacks

- **Review passes round 1** — steer a legitimate tight-fit change ("thin the podium lid to 1.6 mm
  so the OLED bezel fits"), wait for the style/thin-wall finding; never fabricate a finding.
- **Creao stalls** — kill it earlier; that is the takeover beat; restart it for Act 2.
- **Host worker poaches** — every manual task is assigned by name; keep manual helpers active.
- **Droid not signed in** — run `droid` once on the host, or let the new reviewer autostart path
  read `cadswarm.env`; the host fails the brief with a clear message otherwise.
- **Phone tunnel dies** — submit the same brief from the web app on a laptop; the brief and the
  sequence are the same, only the member differs.
- **The Act 2 plan is slow to appear** — skip to step 4 (the refusal) and say the tasks are next; the
  log records it, and `table-safe.json` is the smaller job if the wait runs past 1:30.
- **Lighten overruns** — let it run, continue export/index, show the result at the close.
- **Elastic board lookup down** — local Params still seed; design memory and lessons are the proof.
- **Agent Builder returns 429** — show the agent definition, its four tools and the last successful
  trace in Kibana, say the trial quota is the limit, and lean on the other three criteria.

## Sponsor window: five live cards

Order: **SpacetimeDB → Anthropic → Creao → Factory.ai → Elastic**.

Five cards, two jobs: each shows its partner working live, and each is the receipt for one line in
the judging map. The window stays on the second screen for the whole run, because it is the fastest
answer to "is this real?"

### Live vs stamped

- **SpacetimeDB** — members, parts/params/held, open tasks, ops done, activity seq. Fully live.
- **Anthropic** — members with `client=claude`: names/status, tasks done, MCP ops, and a live trace
  of their recent tool calls (web only).
- **Creao** — members with `client=creao`: helper/status, "local bridge · one JSON tool call per
  turn", tasks done, ops, own conversation thread, live trace (web only). When it is killed, the
  card goes stale and the trace stops; that is the takeover proof.
- **Factory.ai** — reviewer member, pointer, last round, findings, tasks filed, tool trace, all live. The member's own status can read "stale": `init-reviewer` mints an HTTP-only identity that never heartbeats.
- **Elastic** — the brief's board, design-memory line, and "what the Workflow distils". The footer
  line with index counts and Droid versions is a **stamped snapshot**, labelled "checked …"; it is
  never presented as live. Index counts, Agent Builder and Workflow executions are shown in Kibana.

No card disappears, so the window is stable at five: Anthropic and Creao say `not joined` for an empty
client list, SpacetimeDB keeps reading `0 active · 0 joined`, and Factory says `not set up` until
`init-reviewer` has run.

### Data and layout

Web (`web/src/ui/sponsors.ts`): pure functions over the existing `State`; filter `members` by
`client`, count `tasks` by `owner`, `ops` by `by`, and `activity` by `member`. Add a `traceTitle`
field so the builder cards say "recent work" while Factory keeps "trace". No CSS change:
`web/src/style.css` already sets `.sponsors-grid` to `repeat(3, minmax(0, 1fr))`, which renders five
cards as 3 + 2.

FreeCAD (`cadswarm/gui/sponsors.py`): the panel snapshot already carries `client` on participants,
plus tasks; add the two cards with facts only (no trace, matching Factory there). No snapshot or
host change is needed.

Tests: `web/test/sponsors.test.ts` (five cards, populated and empty, trace filtering) and
`tests/panel_gui_check.py` (both new names render in the Sponsors tab).

### What the cards must not do

- No new network calls from the browser and no API keys in the browser.
- No new module tables, reducers, bindings or schema.
- No live Elastic index counts in the web card; that is the stamped footer's job.
- No fabricated numbers: every live field comes from state the panel already subscribes to.

## What we do not claim

- **Not printed.** No printer is available; this is print-ready. Best Hardware is not entered.
- **No model is shipped.** The helpers are the user's Claude Code, OpenCode and Creao accounts.
- **No business claims.** This is a usefulness demo, not a startup pitch: no pricing, no market
  sizing, no customer, no revenue. The freelancer and marketplace figures in "the problem, lived"
  are market prices we did not measure, and they are described as such.
- **No IP claim.** The object is the team's design; the board belongs to Espressif; the build uses
  stock 608, M8, M4 and M3 hardware.
- **The reviewer is optional in the product, live in this run.** If Droid is not signed in, the
  brief still finishes; the Factory card is then empty. `init-reviewer` mints an HTTP-only member, so
  it reads `stale` between rounds by design; the sweep never orphans a reviewer, and a stale reviewer
  does not block the demo driver's Start.
- **Agent Builder is quota-limited.** It is probed in pre-flight and shown live when it answers; when
  the trial model returns `429`, the demo shows the agent definition, its four tools and the last
  successful trace, and says the quota is the limit. The other three Elastic criteria are unaffected.
- **The table run does not finish the head.** Two minutes buys the recall, the tasks, one refusal and
  a part starting. The finished podium is the booth run, and the presenter says which is which.
- **Testimonials are outside testers, not customers.** Every clip is one of the three logged kinds —
  drove a run, rebuilt their own part, watched and prompted — and any elapsed time it quotes is one
  the screen or the run log shows. No clip claims a paying customer, production use, or a printed
  Perch.
- **No staged failures.** Round 1 findings are the first draft's natural errors. If a round passes,
  the runbook uses a real tight-fit change, not a fake defect.
- **No schema, host-orchestration or phone-app changes**, and no new module tables, reducers or
  bindings. The product changes are the two sponsor cards, one reviewer-lifetime fix found by
  rehearsal, and one small client-side observability fix for the two beats the module cannot see.
  Reviewer: `rules::Presence` carries `kind` and `plan_sweep` never orphans a `reviewer` (an HTTP-only
  member that only connects during a review round). Without it the reviewer is retired after ten
  quiet minutes and the Factory card and the driver's Probe lose it. Observability: the host's own
  recall calls `recall_designs` directly and a refused submit returns before any op row exists, so
  neither leaves an event; `cadswarm/helper.py` logs one `recall` activity row per returned design
  (rank, request, its own problem count) as the host, and `cadswarm/space_hub.py` logs a `refused` row
  before re-raising, both through the existing `log_activity` reducer. A recall made by a helper (it
  carries `_actor`) is not the host's planning and leaves no row. The driver reads `v_activity`.
  No module, orchestration, schema or phone change, and no module publish for the observability fix.

## Proof ledger

Every claim in the pitch has an artifact a stranger can open. Three columns of proof, weighted
equally: the impact, the engineering, the stack.

### Impact proof

| Claim | Artifact a judge can inspect |
|---|---|
| A person who cannot CAD still gets a part | the live build from a sentence, the panel's guide, and the exported STEP/STL |
| It works for people who are not us | the three outside clips, the full recordings, and the run logs behind them |
| A part lands in about an hour | `demo/perch/rehearsal.md`: wall clock per act, recorded from the run |
| Defects are caught before printing | the three round-1 findings and the fixed geometry |
| It is lighter without losing stiffness | the lattice report, mass before and after, verified stiffness |
| The second job is faster | `demo/perch/rehearsal.md`: wall clock and human minutes, Act 1 against Act 2 |
| It is repeatable, not a one-off | second brief in different words, recalled memory, second design doc |

### Engineering proof

| Claim | Artifact a judge can inspect |
|---|---|
| It holds the board, both ports usable | `model.FCStd`, STEP/STL, seeded board Params, cutout geometry |
| It will not sag | FEA report (`files/fea/…/report.html`, `.json`) with deflection and safety factor |
| It is light without losing stiffness | Lattice report and both STLs, mass before/after |
| It fits a real bed | `check_printability` output; 180 mm bed in the workspace |
| It will not fall apart | 608 press-fit seat, M4/M3 heat-set inserts, clash-check result |
| The team did not overwrite itself | Refused op in Activity, lock rows, revisions |
| A dead agent did not block the build | Stale member, takeover row, finished lid |
| A person can hand off | Handoff row and the agent's continuation message |
| It remembers | `cadswarm-designs` documents, `recall_designs` result, `cadswarm-lessons` documents |
| It learns the team's style | Active style rules, the `style:` finding, the fixed part |

### Sponsor proof

| Claim | Artifact a judge can inspect |
|---|---|
| SpacetimeDB is the shared state | the window's members, locks, ops, revisions and activity seq |
| Anthropic did the design work | Anthropic card trace and the task rows owned by `client=claude` members |
| Creao is a real helper | Creao card trace, one-call-per-turn cadence, its stale state and the takeover at beat 8 |
| Factory.ai is the quality gate | Factory card: round 1 findings → tasks → round 2 approved; Droid version in the footer |
| Elastic is the memory | `cadswarm-designs` docs, `recall_designs` result, `cadswarm-lessons` docs, Kibana counts |
| No number is invented | stamped footer labelled "checked …"; every live field labelled live |

## Deliverables

Now:

| Path | What |
|---|---|
| `docs/superpowers/specs/2026-10-24-perch-showcase-design.md` | this spec |
| `demo/perch/README.md` | runbook: cast, setup, Act 1/2, formats, fallbacks |
| `demo/perch/prompts.md` | copy-paste briefs, steers, handoff note, phone lines, style rules |
| `demo/perch/checklist.md` | pre-flight, live cue sheet, reset order |
| `demo/perch/rehearsal.md` | measured wall clock, human minutes and defects caught per act, plus Act 2's t(recall)/t(tasks)/t(first op)/t(finish) — the source of every number the pitch quotes |
| `demo/perch/driver.py` | the local driver: presenter join, button page, timeline runner, probes, run log |
| `demo/perch/timeline.json` | the booth sequence: steps, state predicates, deadlines, skip labels |
| `demo/perch/table.json` | the two-minute Act 2 sequence: the phone brief, recall, tasks, refusal, first part |
| `demo/perch/table-safe.json` | the lid-only sequence used when rehearsal starts no part before 1:30 |
| `web/src/ui/sponsors.ts` | Anthropic + Creao cards, trace titles |
| `web/test/sponsors.test.ts` | card tests |
| `cadswarm/gui/sponsors.py` | Anthropic + Creao cards |
| `tests/panel_gui_check.py` | Sponsors tab assertions |
| `rules/src/lib.rs` | `Presence.kind`; `plan_sweep` never orphans a `reviewer` (an approved product exception, found by rehearsal) |
| `module/src/sweep.rs` | passes each member's `kind` into `rules::Presence` |
| `cadswarm/helper.py` | the host's own recall logs one `recall` activity row per returned design (the approved observability exception) |
| `cadswarm/space_hub.py` | a refused submit logs a `refused` activity row before it re-raises |

Deferred until the demo is rehearsed:

| Path | What |
|---|---|
| `demo/perch/pitch.md` | the two-minute judge script and the 60-second Q&A answers |
| `demo/perch/table-card.html` | the one-page evidence card that stands in for a poster |
| `demo/perch/testimonials/` | the three full outside recordings, each with the person's role, date, what they did and their permission to use the clip |
| the 2:45 video | cut from the rehearsed runs and the three clips |

If the web UI ships, rebuild `website/app` from `web/dist` once and record the new hashes. No
release, no module publish, no host restart is required for the cards themselves. The reviewer-lifetime
fix is module code: publish `module/` keeping data (`docs/publishing-spacetimedb.md`) and restart the
host before the first run on a machine that still has the old module. The observability fix lives in
the Python helper and hub client: restart the host and its helpers; no publish.

## Verification

- `pytest` full (including the FreeCAD GUI checks), web `tsc`, web `vitest`.
- Sponsor tests: five cards in order; populated Anthropic/Creao facts and trace; "not joined" when
  the client is absent; Factory still filters by reviewer member.
- Rehearsal gates, recorded in `demo/perch/checklist.md`:
  - round 1 produces at least two real findings; round 2 passes;
  - `recall_designs` returns Act 1 for the Act 2 query;
  - Creao goes stale and the takeover finishes the lid;
  - the phone submits the Act 2 brief;
  - auto-constrain applies and the fit change is visible;
  - FEA and Lighten reports exist with recorded numbers;
  - the sponsor window updates live on web and within one refresh in FreeCAD;
  - five lesson documents exist before Act 2's recall;
  - wall clock, human minutes and defects caught are recorded for both acts, and Act 2 is faster;
  - each Factory-track "what scores well" item is visible: a trigger we designed, an allowlist plus
    the server-side write refusal, and plans, steps and the verdict in the feed;
  - the Elastic checklist is complete: a reranked board hit, the design document, a Workflow
    execution with both steps completed, and Agent Builder either live or shown with its labelled
    fallback;
  - the driver's Probe passes for Claude, Creao, Droid and Elastic, and a failed probe refuses to
    start;
  - the press of the button starts the clock, and `rehearsal.md` records t(recall), t(tasks),
    t(first op) and t(finish) for Act 2; the first op lands before 1:30 in three consecutive
    rehearsals;
  - the `table-safe` lid-only sequence is rehearsed and starts a part, so the fallback is not theory;
  - a reset from a finished table run returns to `act-1-ready` in the recorded time, and the driver
    refuses Start until the state is back;
  - each step lands inside its deadline, Skip advances the run, and the log records the step, the
    wait and the outcome;
  - after 2:00 the build is still moving: a judge who stays sees at least one more state change;
  - each of the five cards is visibly doing its own job during the beat that claims it.

Deferred with the video and the pitch:

- every impact number in `pitch.md` traces to `rehearsal.md` and is presented as measured;
- a stranger reads the table card and can name the community, the problem and the one thing it does
  well;
- the two-minute pitch is delivered inside two minutes with one live artifact per claim;
- each Anthropic-track question has an answer in `pitch.md` (community, real problem, it works, where
  it could fail);
- the outside block has one clip of each kind — drove a run, rebuilt their own part, watched and
  prompted — from people outside the team; any elapsed time it quotes is visible in the clip or in the
  run log, and each person's permission is recorded.

## Risks

| Risk | Mitigation |
|---|---|
| Droid invents a standoff finding (the board has no holes) | Rehearse; steer it to the real fit checks; if it is genuinely false, resolve it without indexing it as a lesson |
| Host Claude workers poach OpenCode/Creao tasks | Assign every manual task by name; keep helpers active; rehearse the plan once |
| Creao bridge is slow or flaky | Give it the smallest part; a stall becomes the takeover beat; restart for Act 2 |
| FEA/Lighten overruns the slot | Start FEA early; start Lighten after round 2; export and index while it runs |
| Two hosts index the same brief | Run one host per database for the demo |
| The cradle and podium both occupy the model | Move the cradle aside after Act 1; hide the lattice mesh before Act 2 |
| No printer | Print-ready only; no Best Hardware claim; optional print-service appendix |
| Sponsor cards change a shipping UI | Read-only from existing state, unit tests, visual check, no schema/binding change |
| Judges cannot tell what is real | Every live number is labelled live, the Elastic/Droid footer stays stamped, and `rehearsal.md` is on the table |
| Two minutes is not enough | One artifact per claim; the run keeps going after the pitch; the 40–50′ booth run is the long version |
| A judge arrives mid-build | The run is already going; the driver's status line says which step it is on and what it is waiting for; the panel carries the claims |
| "How is this different from one agent?" | The refusal, the takeover, the handoff and the second-brief recall are the difference, and each is a beat |
| The demo machine dies mid-run | `act-1-ready`, the STL and the reports live on disk, so the second machine can show the same artifacts |
| The impact claim sounds generic | A named community, the before/after table, and only measured numbers |
| Agent Builder's trial quota returns 429 at judging | Probe it in pre-flight; keep the agent definition, tool list and last trace open; the other three Elastic criteria carry the track |
| A driver step stalls at the table | Every step has a deadline; Skip advances and says so; the labelled replay is the fallback |
| No visible part by 1:30 | `table-safe.json` sends the lid as its own small job; the presenter says the body is the next run; the booth run carries the finish |
| The build is mistaken for finished | The pitch claims recall, tasks, a refusal and a part starting; the task list stays on screen and the finish is the booth run's claim |
| A testimonial reads as a customer claim | Each clip is one of the three logged kinds, names the person's role and what they actually did, and never implies a printed Perch, production use or a paying customer |

## Scope

In now: the object family, the two-act runbook, the impact case and judging map, the rigor and
failure-mode tables, the demo driver and its timelines, the five-card sponsor window, tests, the
three-column proof ledger.

Deferred: the video and its outside block, `pitch.md`, the table card, and the testimonial clips. The
constraints for them are recorded above so the demo does not have to change when they are made.

Out: new CAD operations, new module tables/reducers, new host orchestration, new Elastic indices or
Workflow changes, voice/Cartesia, a printed part, a public mock/demo mode, pricing or market
analysis, and any claim of a customer or revenue. The two approved exceptions are recorded above:
the reviewer-lifetime fix and the helper/hub activity rows (neither adds a table, reducer, schema,
orchestration step or phone change).

## Success criteria

1. A judge can name the community and the problem in one sentence, and say what the part does for
   them.
2. A judge can point at the mechanism behind each hard claim: locks, reviewer, auto-constrain, FEA,
   lattice verification, takeover, handoff, recall.
3. A full run produces: auto-constrain applied, an FEA report, a verified lattice, two review rounds
   with real findings, a takeover, a handoff, a live phone brief, and a recalled design.
4. Rehearsal records wall clock, human minutes and defects caught for both acts; Act 2 is faster, and
   the numbers are on the table.
5. Five sponsor cards are live; no card claims a number it cannot source; the Elastic/Droid footer
   stays visibly stamped.
6. The two-minute table run starts Act 2 live, lands recall, tasks, a refusal and a part inside its
   gates, and keeps building after the pitch ends.
