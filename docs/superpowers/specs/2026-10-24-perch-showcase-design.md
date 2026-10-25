# The Perch: a commercial showcase build

Date: 2026-10-24
Status: approved (design dialogue, sections 1–3); commercial case and sponsor weighting added at
review, both at the user's request
Branch: `feat/perch-showcase` (worktree, based on `origin/main` `6f21059`)

## Goal

Build one physical product family that does two jobs with equal weight:

1. **Commercial.** Prove CadSwarm can sell a real part: a small hardware team describes a bench
   problem in a sentence and gets a correct, strong, light, print-ready accessory — then gets the
   next SKU for the same product for a fraction of the first one's cost, because the fits, style and
   review lessons were remembered. The build is evidence that the business below is deliverable,
   not hypothetical.
2. **Sponsor.** Make every partner in the stack load-bearing for that paid promise: SpacetimeDB is
   the shared team state, Anthropic the designers, Creao the cheap always-on helper, Factory.ai the
   QA gate that stops wasted prints, Elastic the memory that makes SKU #2 cheap. No card is
   decoration; each underwrites a specific line in the pitch.

The project is **The Perch**: a clip-on articulated arm that holds an ESP32-S3-DevKitC-1 at eye
level with both hands free, plus a second **Podium** display head for the same board and the same
arm. It is performed as two briefs against one live model, with Claude Code, OpenCode and Creao AI
helpers, a Droid reviewer, SpacetimeDB as the team's state, and Elastic as board knowledge, design
memory and lessons.

The deliverable is the printable object family plus a runbook that can be performed live, pitched
in ten minutes, or recorded. The runbook carries both halves: the live proof and the business case
built on it.

## Two theses, equal weight

The demo succeeds only if the room can say both of these, and can point at the artifact behind each.

### A. Commercial thesis

1. **The pain is real and it is priced badly today.** A one-off bracket or enclosure for a dev board
   costs a freelancer 3–10 days and $600–2,000, a marketplace gig two rounds of re-explaining with no
   memory, or the team's own weekend in Fusion. Most teams choose a binder clip.
2. **We sell the missing third option.** A brief in, a print-ready part out in about an hour, with
   the fits, the wall rule and the board's own measurements already correct.
3. **The second SKU is where the margin is.** The Podium head reuses the mount, the fits, the style
   and the Act 1 lessons; its design cost is a fraction of the Cradle's. That is the product, not a
   demo trick: every board in the catalog is a family, not a one-off.
4. **The output is trustworthy enough to print.** Two review rounds with real findings, a simulated
   cantilever, a verified lattice, a clash check and a bed check are what let us charge for
   "print-ready" instead of "looks fine in the viewport".

### B. Sponsor thesis

| Sponsor | What it does in this build | The paid promise it underwrites |
|---|---|---|
| **SpacetimeDB** | every member, lock, op, task and revision is one live database the panel and web both read | a team and its agents edit one model at once without merge hell |
| **Anthropic** | planner, lead and worker agents plan, build and hand off | a part in an hour instead of a week of CAD |
| **Creao** | a cheap, always-on helper takes the small parts, one JSON tool call per turn | low-cost SKU variants; capacity that scales without headcount |
| **Factory.ai** | Droid reviews each round and files findings as tasks | fewer wasted prints — the quality claim we charge for |
| **Elastic** | board knowledge, design memory, FEA reports, distilled lessons | SKU #2 is cheap because the team remembers; that memory is the moat |

The sponsor window is the live receipt for that table. It is not a logo wall: a card going stale when
Creao dies, or a round-2 approval landing on the Factory card, is the same event the customer is
paying for.

## The commercial case

### Customer

Small hardware teams (2–10 people) shipping an accessory around a dev board — a holder, a case, a
bracket, a display stand; makerspaces and teaching labs that need the same part in quantity; and
print shops that receive "can you make me one of these" CAD requests they cannot answer.

The first customer is the team that already bought the board and already has a printer: they are one
part away from a product, and the part is exactly the thing they cannot design.

### What we sell

| Offer | Buyer | Price (modeled) | What they get |
|---|---|---|---|
| **Accessory sprint** | a team with one part to make | $1,500 fixed fee | brief → print-ready STEP/STL in about a day, with FEA, printability and review evidence |
| **Team subscription** | a team making parts continuously | $299/team/month | the panel, the host, the helpers, and the memory across parts and boards |
| **Catalog partner** | print farms and board vendors | rev-share per printed part | a family of ready parts per board, already reviewed and print-checked |

The sprint is the wedge: it prices against a freelancer, not against software. The subscription is
the business: it converts the sprint's memory into recurring work. The catalog is the long tail: the
same design doc, licensed per print.

The two clocks are different on purpose: the sprint is a working day end to end, because a customer
conversation, a print service and shipping sit around it; the hour is the part of that day which
used to take three days. The demo's 40–50 minutes are the honest core of the hour.

### Who pays, and what for

They pay for **time and for not wasting prints**. A wasted print is a day and a spool; a missed
press fit is a reprint; a wrong port cutout is a refund. Our invoice is defensible only because the
review, the simulation and the bed check happened before the printer did — which is exactly what the
runbook shows on screen.

### Why us, against the alternatives

| Alternative | What it costs | Where it breaks | Our difference |
|---|---|---|---|
| CAD freelancer | $600–2,000, 3–10 days | no memory: every part is a new briefing, and the board's measurements are re-guessed | board params, fits and style persist across parts |
| Marketplace gig | $50–300, 1–2 weeks of messages | no review, no printability evidence, usually a mesh you cannot edit | parametric model, review rounds, FEA and bed check |
| Do it yourself | 10–30 h of learning | the team's own time, and the fit errors are discovered after printing | brief in, part out, with the checks already run |
| Do nothing | free | tape, a binder clip, a board that slides | a real part for less than a day of engineering |

The comparison is the pitch's second slide: it is not "AI vs CAD", it is "the only option sized for a
two-person team with one part to make".

### Unit economics (modeled, not booked)

| Line | Act 1 (Cradle) | Act 2 (Podium) |
|---|---|---|
| Agent time (host + helpers, ~1 h wall clock) | $2–6 | $1–4 |
| Reviewer pass (Droid + key) | ~$0.25 | ~$0.25 |
| Human time (steer, correct, approve) | ~15 min, ~$25 loaded | ~8 min, ~$13 |
| **Landed cost** | **$27–31** | **$14–17** |
| Price (sprint, first part) | $1,500 | — |
| Price (subscription, per part amortized) | $100 | $100 |
| **Gross margin** | ~97% sprint / ~70% subscription | ~85% subscription |

Two numbers matter more than the table: the marginal cost of the **second head** is roughly half the
first, and the human time per part falls because the style rules and lessons are already in the
prompt. That curve — cost down, price flat — is the business.

Every number above is modeled from the rehearsal, labelled as modeled in the pitch, and never
presented as revenue. The hackathon does not need a booked customer to show a real business; it
needs a deliverable a customer could buy tomorrow and a cost structure that survives the sale.

### The wedge and the expansion path

1. **One board, one family.** ESP32-S3-DevKitC-1 gets a Cradle and a Podium from one arm. The next
   board is a new parameter seed, not a new product.
2. **Board vendors and print farms** already own the customer at the moment of purchase. A reviewed
   part family per board is inventory they can sell without hiring a CAD team.
3. **Education and makerspaces** buy the same sprint repeatedly, with a new brief each term.
4. **The catalog compounds.** Every remembered design and every lesson makes the next brief cheaper
   and more likely to pass review first time, which widens the margin without raising the price.

### Why The Perch proves repeatable value

- The same two people run both briefs, and the second one is submitted from a phone.
- The second head is a **new design**, not a parameter tweak, so reuse is proven on geometry the
  memory did not literally contain.
- Design memory and lessons are what make the second part cheap; the sponsor card and the recall
  result are the receipt.
- A dead helper is taken over instead of stalling the build, so output is not proportional to
  headcount — the operating leverage a $299 subscription is sold on.
- The reviewer is the quality gate behind "print-ready"; its findings become tasks, which is why the
  second part ships without repeating the first part's mistakes.

### What would falsify the commercial case

- If Act 2 costs as much as Act 1 in agent and human time, the memory claim is decoration.
- If round 1 findings are trivial, "print-ready" is not worth a premium.
- If neither head survives a real print, the product is a render.
- If a stranger cannot read the proof ledger and reach the same conclusion, the pitch is a story.

The rehearsal measures the first three (wall clock, findings, printability) and the ledger answers the
fourth. We report the measured numbers, not the modeled ones, if they disagree.

The honest hardware boundary stands: this is **print-ready, not printed**. No printer is available
for the run, so the project does not claim Best Hardware and does not show a photographed part.
Printability is still checked against a real 180 mm bed and the STLs are exported.

## The object

### Family

- **The Perch** — desk clamp, 608-bearing shoulder, two printable arm segments, a dovetail head
  mount. The arm reaches 160 mm after the shared-size steer; both segments print on a 180 mm bed.
- **Cradle head (Act 1)** — a tray for the ESP32-S3-DevKitC-1 with side rails (the board has no
  mounting holes), two USB-C cutouts, and a vented screw-on lid.
- **Podium head (Act 2)** — the same board upright behind an OLED window, plus a 60 mm medallion
  that spins on a 608 bearing. This is the sellable second SKU: the board and the team's award are
  visible at eye level, on the same arm, for roughly half the design cost of the Cradle.

The sellable unit is the **family**, not a part: one arm, a mount, and a head per use. That is why
the demo builds two heads — it shows the catalog, the margin curve and the memory in one object.

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
- The **board-specific fit** is what a customer pays for; a generic box is already free on Printables.
- The **second head** is a second SKU on one mount, so the demo measures the cost of SKU #2 — the
  number the business is built on.
- The **180 mm bed and stock hardware** are what a print farm or a two-person shop already owns, so
  the part is sellable without asking the buyer to buy a machine.

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
| `planner-claude` | Claude Code, host-spawned | plan the brief into assigned tasks, then stop |
| `lead-claude` | Claude Code, manual | `Shoulder_Hub`, Params, coordination, takeover |
| `worker-1/2/3-claude` | Claude Code, host-spawned | unassigned tasks: `Clamp_Base`, `Cradle_Base` |
| `arm-opencode` | OpenCode | arm segments, FEA, Lighten |
| `lid-creao` | Creao AI bridge | cradle lid; killed for takeover; returns for Act 2 |
| `base-claude` | Claude Code, handoff-spawned | finishes the clamp after the human |
| `reviewer` | Droid | review rounds 1 and 2 |
| you / Sam | FreeCAD / phone | build, steer, correct, submit Act 2 from the phone |

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
| 1 | Cold open | 1′ | Sponsor window on the second screen | Anthropic/Creao go from "not joined" to live as helpers join |
| 2 | Plan + board | 2′ | Submit the brief | Planner calls `lookup_board`; board Params fill; Elastic card shows `esp32-s3-devkitc-1`; tasks appear with names |
| 3 | Build | 8–12′ | Let the builders work | Parts in owner colors; Creao's one-call-per-turn trace; Claude workers take clamp/cradle |
| 4 | Protection | 1′ | `@lid-creao thicken Arm_Distal by 2 mm; it flexes.` | Refused "locked by arm-opencode"; it messages the owner |
| 5 | Shared size | 1′ | Tools ▸ Set param: `arm_reach = 160 mm` | Arm segments and head positions recompute; owners notified |
| 6 | Auto-constrain | 2′ | Owners release; Tools ▸ Auto-constrain → preview → apply; then `tol_press_fit = -0.08 mm` | Proposed aliases/pattern/fit; hub bore visibly shrinks |
| 7 | FEA | 3–6′ | `@arm-opencode linear static on Arm_Distal: fixed at the elbow, 4.9 N down at the head, PETG. Publish it and tell us if it's overbuilt.` | Report pops up; deflection and safety factor; Elastic indexes it |
| 8 | Kill + takeover | 2′ | Kill the Creao bridge mid-lid; wait for stale; `@lead-claude take_over("lid-creao")` | Creao card goes stale and its trace stops; Spacetime lock row moves; lead finishes the lid |
| 9 | Handoff | 2–3′ | Human holds `Clamp_Base`, edits the M8 pocket, **Hand off now** with the note below | `base-claude` joins, accepts the lock, finishes, messages back |
| 10 | Review 1 | 2–4′ | Automatic when tasks are done | Droid pointer and trace; three findings: missing USB-OTG cutout, cradle inner height 8.0 < 11.1 mm, lid wall 1.0 mm |
| 11 | Correct + style | 2–3′ | Human sets the lid wall to 2.4 mm and saves; Tools ▸ Learned design style: add the two rules below | Human correction recorded; rules active; later turns carry "Team design style" |
| 12 | Review 2 | 1–2′ | Fix tasks, automatic re-review | Factory card: round 2 · approved |
| 13 | Snapshot + Lighten | 5–10′ | Save snapshot `act-1-ready`; `@arm-opencode lighten Arm_Distal to 90% stiffness, gyroid, 5 mm cells, held at the elbow, loaded at the head.` | Verifier console; mass saved; cut-open preview; `Add to model` leaves the original |
| 14 | Export + memory | 1–2′ | Export STEP + STL; run `distil-lessons` (or show the 10-minute schedule) | `cadswarm-designs` doc with params + 3 problems; 3 lesson docs |
| 15 | Sponsor recap | 1′ | Leave the window up; restart Creao for Act 2 | Spacetime counts, Anthropic traces, Creao live again, Factory verdict, Elastic findings |

Handoff note:

> Jaw opening is 34 mm; the M8 nut pocket is 0.2 mm tight. Finish the insert pocket, add 0.4 mm
> clearance, keep 2.4 mm walls. I've left the lock on the clamp for you.

Style rules:

1. Printed enclosure walls are 2.4 mm.
2. Lids fasten with M3 heat-set inserts, not snap tabs.
3. Printed levers and arms get 3 mm outer fillets.

**Commercial read of Act 1.** The part that lands at beat 14 is the sprint deliverable. The clock
starts at beat 2 and stops at beat 14; that wall clock and the human minutes spent at beats 5, 6, 9,
11 and 13 are recorded in the rehearsal sheet and become the modeled cost line in the pitch.

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
| 2 | Recall | 1′ | Planner calls `recall_designs`; Act 1 returns first; Elastic card's design memory shows it |
| 3 | Build | 6–10′ | Three parts; the cradle is moved aside; the lattice mesh is hidden; the arm stays |
| 4 | Review 1 | 2–3′ | Medallion clashes with the body by 0.4 mm; `Podium_Lid` is 2.0 mm against the 2.4 mm rule (a `style:` finding) |
| 5 | Fix + Review 2 | 2–3′ | Both fixed; round 2 approved; Act 1's lessons prevented a repeat port/depth mistake |
| 6 | Second design memory | 1′ | Export; second design doc; `recall_designs` returns both; lessons grow to 5 |
| 7 | Close | 1′ | Sponsor window + reports + STL preview; the offer price and the next-board path on screen; "print-ready, designed on a 180 mm bed" |

The recall query is deliberately worded differently from the Act 1 request: "a display head for a
dev board with an OLED window and a spinning medallion on the same clip-on arm." Act 1 must return
first (or second only to a closer match), and its three problems must be visible in the result.

**Commercial read of Act 2.** Same clock, same measurement, second SKU. The pitch puts the two rows
side by side: the delta is the memory dividend, and it is the only number that has to be believable.
If the delta is small, we say so and the claim changes.

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

Read a column for one partner's arc, a row for what the room sees at that moment. The commercial
pitch uses the same table: each row is a promise a customer would pay for, and the card on screen is
the evidence it happened.

### Three formats

| Format | Length | Act 1 | Act 2 | FEA/Lighten | Elastic |
|---|---|---|---|---|---|
| **Full live** (booth) | 40–50′ | live | live | live | Workflow run live |
| **Pitch** (judges) | ~10′ | restore `act-1-ready` + show reports | live from phone | pre-run, show reports | recall live, lessons pre-run |
| **Video** | 2:45 | timelapse | live or recorded | cutaways | Kibana cutaways |

The pitch is not a feature tour. It runs **problem → offer → live proof → economics → expansion**:
Act 1 is the proof, Act 2 is the economics, and the sponsor window stays on screen through both.
The full walkthrough of every tool is the booth format, not the pitch.

### Fallbacks

- **Review passes round 1** — steer a legitimate tight-fit change ("thin the podium lid to 1.6 mm
  so the OLED bezel fits"), wait for the style/thin-wall finding; never fabricate a finding.
- **Creao stalls** — kill it earlier; that is the takeover beat; restart it for Act 2.
- **Host worker poaches** — every manual task is assigned by name; keep manual helpers active.
- **Droid not signed in** — run `droid` once on the host, or let the new reviewer autostart path
  read `cadswarm.env`; the host fails the brief with a clear message otherwise.
- **Phone tunnel dies** — submit the same brief from the web app on a laptop.
- **Lighten overruns** — let it run, continue export/index, show the result at the close.
- **Elastic board lookup down** — local Params still seed; design memory and lessons are the proof.

## Sponsor window: five live cards

Order: **SpacetimeDB → Anthropic → Creao → Factory.ai → Elastic**.

Five cards, two jobs: each shows its partner working live, and each is the receipt for one line in
the commercial pitch. The window stays on the second screen for the whole run, because it is the
fastest answer to "is this real?"

### Live vs stamped

- **SpacetimeDB** — members, parts/params/held, open tasks, ops done, activity seq. Fully live.
- **Anthropic** — members with `client=claude`: names/status, tasks done, MCP ops, and a live trace
  of their recent tool calls (web only).
- **Creao** — members with `client=creao`: helper/status, "local bridge · one JSON tool call per
  turn", tasks done, ops, own conversation thread, live trace (web only). When it is killed, the
  card goes stale and the trace stops; that is the takeover proof.
- **Factory.ai** — reviewer member, pointer, last round, findings, tasks filed, tool trace. Live.
- **Elastic** — the brief's board, design-memory line, and "what the Workflow distils". The footer
  line with index counts and Droid versions is a **stamped snapshot**, labelled "checked …"; it is
  never presented as live. Index counts and Workflow executions are shown in Kibana.

The two new cards show "not joined" rather than disappearing, so the window is stable at five.

### Data and layout

Web (`web/src/ui/sponsors.ts`): pure functions over the existing `State`; filter `members` by
`client`, count `tasks` by `owner`, `ops` by `by`, and `activity` by `member`. Add a `traceTitle`
field so the builder cards say "recent work" while Factory keeps "trace". Change
`.sponsors-grid` to `repeat(auto-fit, minmax(280px, 1fr))` so five cards fill 3+2.

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
- **No customer and no revenue.** The prices, margins and price points in the commercial case are
  modeled from the rehearsal and labelled as modeled everywhere they appear. No LOI, invoice or
  booked sale is claimed.
- **No speed benchmark against a human CAD team.** We report our own wall clock and human minutes;
  the freelancer comparison is a market price, not a measured contest.
- **No IP claim.** The object is the team's design; the board belongs to Espressif; the build uses
  stock 608, M8, M4 and M3 hardware.
- **The reviewer is optional in the product, live in this run.** If Droid is not signed in, the
  brief still finishes; the Factory card is then empty.
- **Elastic Agent Builder is not in the demo.** Board search, design memory, FEA indexing and the
  lessons Workflow are; the trial-quota model is not.
- **No staged failures.** Round 1 findings are the first draft's natural errors. If a round passes,
  the runbook uses a real tight-fit change, not a fake defect.
- **No module, rules, schema, host-orchestration or phone-app changes.** The only product change is
  the two sponsor cards.

## Proof ledger

Every claim in the pitch has an artifact a stranger can open. Three columns of proof, weighted
equally: the business, the engineering, the stack.

### Commercial proof

| Claim | Artifact a judge or buyer can inspect |
|---|---|
| A customer could buy this | `demo/perch/pitch.md` offer table plus the exported STEP/STL a buyer would receive |
| A part lands in about an hour | `demo/perch/rehearsal.md`: wall clock per act, recorded from the run |
| Human time per part falls | `demo/perch/rehearsal.md`: measured human minutes at the listed beats, Act 1 vs Act 2 |
| SKU #2 is cheaper than SKU #1 | the two cost rows, built from measured time and token usage |
| The margin survives the sale | the modeled cost table, with every input traced to the rehearsal sheet |
| Print-ready is worth a premium | review rounds, clash check, bed check, FEA and lattice reports |
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
| Creao is a real, cheap helper | Creao card trace, one-call-per-turn cadence, its stale state and the takeover at beat 8 |
| Factory.ai is the quality gate | Factory card: round 1 findings → tasks → round 2 approved; Droid version in the footer |
| Elastic is the memory | `cadswarm-designs` docs, `recall_designs` result, `cadswarm-lessons` docs, Kibana counts |
| No number is invented | stamped footer labelled "checked …"; every live field labelled live |

## Deliverables

| Path | What |
|---|---|
| `docs/superpowers/specs/2026-10-24-perch-showcase-design.md` | this spec |
| `demo/perch/README.md` | runbook: cast, setup, Act 1/2, formats, fallbacks |
| `demo/perch/prompts.md` | copy-paste briefs, steers, handoff note, phone lines, style rules |
| `demo/perch/checklist.md` | pre-flight, live cue sheet, reset order |
| `demo/perch/pitch.md` | 10-minute commercial script: problem → offer → live proof → economics → expansion; the offer table and the one-page buyer handout |
| `demo/perch/rehearsal.md` | measured wall clock, human minutes and token spend per act — the source every modeled cost is traced to |
| `web/src/ui/sponsors.ts` | Anthropic + Creao cards, trace titles |
| `web/src/style.css` | five-card auto-fit grid |
| `web/test/sponsors.test.ts` | card tests |
| `cadswarm/gui/sponsors.py` | Anthropic + Creao cards |
| `tests/panel_gui_check.py` | Sponsors tab assertions |

If the web UI ships, rebuild `website/app` from `web/dist` once and record the new hashes. No
release, no module publish, no host restart is required for the cards.

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
  - wall clock and human minutes are recorded for both acts, and Act 2's human minutes are lower;
  - every number in `pitch.md` traces to `rehearsal.md` and is labelled modeled or measured;
  - a stranger reads the one-pager and can restate who pays, for what, and at what price;
  - each of the five cards is visibly doing its own job during the beat that claims it.

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
| Judges read it as a cool demo, not a business | Open with the offer and the price; the commercial column of the proof ledger; the one-pager answers "who pays" |
| Modeled numbers look invented | Label them modeled, show `rehearsal.md`, quote only measured numbers for time, never say "revenue" |
| Sponsor cards look like a logo wall | Each card is on screen at the beat whose promise it underwrites, and each maps to an offer line |
| "Who is the customer?" at Q&A | The customer paragraph and the sprint offer, answered in one sentence, then the price |

## Scope

In: the object family, the two-act runbook, the commercial case and pitch, the five-card sponsor
window, tests, the three-column proof ledger.

Out: new CAD operations, new module tables/reducers, new host orchestration, new Elastic indices or
Workflow changes, voice/Cartesia, a printed part, a public mock/demo mode, a real customer contract,
a paid pilot, a pricing experiment or go-to-market spend.

## Success criteria

1. The audience can restate the offer without prompting: who pays, for what, and at what price.
2. The audience can name each sponsor's job in the build and point at the card or artifact that
   proves it.
3. A full run produces: auto-constrain applied, an FEA report, a verified lattice, two review rounds
   with real findings, a takeover, a handoff, a live phone brief, and a recalled design.
4. Rehearsal records wall clock and human minutes for both acts; Act 2 is cheaper, and the pitch
   quotes the measured numbers rather than the modeled ones when they disagree.
5. Five sponsor cards are live; no card claims a number it cannot source; the Elastic/Droid footer
   stays visibly stamped.
6. All tests are green, the runbook has been rehearsed end to end, and a stranger can read
   `pitch.md` plus the one-pager and repeat the business case back.
