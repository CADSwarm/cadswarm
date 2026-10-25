# The Perch: the live demo

Two acts, one object family, one screen that changes because a team is working. The judging table runs
Act 2 for two minutes; the booth runs both acts for forty. Nothing on either screen is canned: every
part is made by a real helper against the real model, and every claim on a card is read from the module.

The design and its constraints are in
`docs/superpowers/specs/2026-10-24-perch-showcase-design.md`. This folder is the runbook.

## What is here

| File | What it is |
|---|---|
| `driver.py` | joins the team as a human presenter; runs a timeline; serves the button page |
| `table.json` | the two-minute judging run: brief, recall, tasks, refusal, first part |
| `table-safe.json` | the lid-only fallback, chosen before a pitch, never mid-pitch |
| `timeline.json` | the 40–50 minute booth run: Act 1 through Act 2 |
| `prompts.md` | every brief, steer, note and phone line to copy |
| `checklist.md` | pre-flight, the live cue sheet, the reset order, the safe-run decision |
| `rehearsal.md` | the numbers sheet, filled in by rehearsal (Task 7) |
| `runs/` | generated run logs and summaries; ignored by git |

Deferred and not built here: `pitch.md`, `table-card.html`, `testimonials/`, the 2:45 video. The demo
does not depend on any of them.

## Cast

| Who | Client | Job | How it joins |
|---|---|---|---|
| `presenter` | presenter | presses Start, sends the steers, answers judges | `driver.py` (this folder) |
| `you` | FreeCAD | builds, corrects by hand, runs Auto-constrain | `cadswarm setup --client freecad` |
| `Sam` | phone | types and sends the Act 2 brief | the host's phone invite / QR |
| `planner-claude` | claude | turns a brief into assigned tasks, then stops | host-spawned |
| `lead-claude` | claude | `Shoulder_Hub`, Params, coordination, takeover | manual Claude Code |
| `worker-1/2/3-claude` | claude | unassigned tasks: `Clamp_Base`, `Cradle_Base` | host-spawned |
| `arm-opencode` | opencode | arm segments, FEA, Lighten | manual OpenCode |
| `lid-creao` | creao | the lid; killed for the takeover; back for Act 2 | Creao bridge |
| `base-claude` | claude | finishes the clamp after the handoff | host-spawned on handoff |
| `reviewer` | droid | review rounds 1 and 2 | host `init-reviewer` |

The three card claims the window makes about people are these members: `client = "claude"` is the
Anthropic card, `client = "creao"` is the Creao card, `kind = "reviewer"` is the Factory card. The
presenter's `client` is `presenter`, so it never inflates a partner's counts.

Every manual helper joins **before** the brief, so the planner can assign by name. Every manual task is
assigned by name so a free host worker cannot poach it.

## One-time setup on the host

Run from the repo root, with the venv already installed (`python -m venv .venv;
.venv\Scripts\pip install -e ".[test]"`).

```powershell
# 1. The stack: builds the host and web app, runs it against Maincloud, prints a phone invite.
powershell -File scripts\demo-up.ps1 -SkipBuild

# 2. One human invite for the presenter (the host prints a cadswarm-2: string).
host\target\release\cadswarm-host.exe --data $env:USERPROFILE\Documents\Projects\cadswarm-demo invite --kind human --role lead

# 3. The presenter folder: joins once, then reuses its identity on every later run.
.venv\Scripts\python demo\perch\driver.py --project demo\perch\presenter --invite "cadswarm-2:..."

# 4. The manual helpers, each in its own folder (invites come from the host).
.venv\Scripts\cadswarm setup --client claude   --invite "cadswarm-2:..." --name lead-claude  --dir ..\perch\lead-claude
.venv\Scripts\cadswarm setup --client opencode --invite "cadswarm-2:..." --name arm-opencode --dir ..\perch\arm-opencode
.venv\Scripts\cadswarm setup --client creao    --invite "cadswarm-2:..." --name lid-creao    --dir ..\perch\lid-creao
```

`demo/perch/presenter/.cadswarm/` holds the presenter's SpacetimeDB token; `.cadswarm/` is already
ignored. The driver's `--project` points at that folder, **not** at the repo root.

Keys come from `cadswarm.env` through `scripts\with-env.ps1` (Elastic, Factory, Creao). The driver never
reads a key: its probe asks Elastic through `cadswarm.knowledge` exactly as the host does, and only
checks that `droid` is on `PATH`.

## Before every pitch

Read `checklist.md` top to bottom. It is ordered: stack, helpers, Elastic tabs, `act-1-ready`, probe,
then the replay window. The driver refuses to Start unless the probe passes and the team is back at
`act-1-ready`.

## Act 1: "The Perch" (booth, 40–50 minutes)

Brief, typed in the panel (exact text in `prompts.md`): the clip-on arm, the cradle head, the board
lookup, the owners, the 180 mm bed, "the distal arm is what we'll simulate and lighten".

| # | Beat | Time | Action | What the room sees |
|---|---|---|---|---|
| 1 | Cold open | 1′ | Sponsor window on the second screen | Anthropic/Creao go from `not joined` to live |
| 2 | Plan + board | 2′ | Submit the brief | `lookup_board`, board Params, tasks with names |
| 3 | Build | 8–12′ | Let them work | Parts in owner colours; Creao one call per turn |
| 4 | Protection | 1′ | `@lid-creao thicken Arm_Distal by 2 mm; it flexes.` | Refused; it messages the owner |
| 5 | Shared size | 1′ | Tools ▸ Set param `arm_reach = 160 mm` | Segments and head positions recompute |
| 6 | Auto-constrain | 2′ | Owners release; Tools ▸ Auto-constrain → apply; `tol_press_fit = -0.08 mm` | Aliases, M4 pattern, bearing fit; the bore shrinks |
| 7 | FEA | 3–6′ | `@arm-opencode linear static …` | Report pops up for the team; Elastic indexes it |
| 8 | Kill + takeover | 2′ | Kill the Creao bridge; `@lead-claude take_over("lid-creao")` | Creao goes stale, its trace stops, the lock moves |
| 9 | Handoff | 2–3′ | Hold `Clamp_Base`, edit the M8 pocket, **Hand off now** | `base-claude` joins, accepts, finishes, messages back |
| 10 | Review 1 | 2–4′ | Automatic when tasks are done | Droid pointer, trace, three findings |
| 11 | Correct + style | 2–3′ | Set the lid wall to 2.4 mm; add the three rules in Learned design style | The correction is recorded; rules are active |
| 12 | Review 2 | 1–2′ | Fix tasks, automatic re-review | Factory card: round 2 · approved |
| 13 | Snapshot + Lighten | 5–10′ | Snapshot `act-1-ready`; `@arm-opencode lighten Arm_Distal …` | Verifier console, mass saved, cut-open preview |
| 14 | Export + memory | 1–2′ | Export STEP + STL; run `distil-lessons`; open Kibana | Design doc + 3 lessons + Workflow execution |
| 15 | Sponsor recap | 1′ | Leave the window up; restart Creao for Act 2 | Five cards, all live again |

Set the snapshot name `act-1-ready` exactly: `reset_ok()` and every table run depend on the six parts
being present and no `Podium_`/`Medallion` object being left behind.

## Act 2: "The Podium"

Booth: the phone brief asks for the body, the medallion and the lid. Table: two parts, a 2.4 mm lid,
same owners, same recall. The medallion and its 608 bearing are **booth only**.

| # | Beat | Booth | Table |
|---|---|---|---|
| 1 | Phone brief | 1′ | 0:30, the button |
| 2 | Recall | 1′ | by 1:00 |
| 3 | Build | 6–10′ | from 1:15; the pitch stops claiming at 2:00 |
| 4 | Review 1 → fix → round 2 | 4–6′ | not in the two minutes |
| 5 | Export + second design memory | 1–2′ | not in the two minutes |

## The driver

```powershell
# the judging table (default timeline is table.json)
.venv\Scripts\python demo\perch\driver.py --project demo\perch\presenter

# the pre-flight choice for a slow day
.venv\Scripts\python demo\perch\driver.py --project demo\perch\presenter --timeline table-safe.json

# the booth
.venv\Scripts\python demo\perch\driver.py --project demo\perch\presenter --timeline timeline.json

# the phone tunnel is down: send the same brief from this laptop, and the page says so
.venv\Scripts\python demo\perch\driver.py --project demo\perch\presenter --timeline table-safe.json --laptop
```

The page opens at `http://127.0.0.1:8765/` (`--port` changes it, `--no-browser` stops the tab).

- **Start** — refuses unless Probe passed and the team is back at `act-1-ready`; opens the sequence and
  shows the brief to paste on the phone. Sam sends it from the phone.
- **Next** — advance now. A manual step is logged `confirmed`; a module step `skipped`. Both say why.
- **Pause / Resume** — holds the sequence while a judge talks. The build keeps running.
- **Probe** — Claude, Creao, Droid, Elastic, then every other owner in the timeline's `probe` list.
  The booth's `base-claude` joins at the handoff, twenty minutes in, so it is not probed at Start.
  Re-run Probe after touching any helper.
- **Say box** — `@lid-creao …` is a real steer; anything else is a real brief. This is how a judge's own
  idea runs live.

Every step is done when the module says so — a task row, an op, a lock, a review, a bbox, an artifact,
an event. Never when an agent says it is. Every run appends to `runs/<stamp>-<timeline>.jsonl` and writes
`runs/<stamp>-<timeline>.json` with `t_recall`, `t_tasks`, `t_first_op`, `t_finish`; those summary
numbers, not the targets, are what the pitch quotes.

## What the table claims

At the table the presenter claims exactly four things: the recall returned Act 1 with its three problems,
the tasks appeared with the same named owners, the held body refused Creao, and a real part started to
build. Never a finished head. The absolute gate is **the first op by 1:45**; the driver says the
fallback line out loud if it is missed and stops claiming a part.

The build continues after 2:00 and through Q&A. The driver never stops the team.

## If something breaks

| Symptom | Do this |
|---|---|
| Probe fails on Claude/Creao | check that helper's folder is running; re-run Probe; if it stays down, run `table-safe.json` with that helper out of `cast` — never fake its card |
| Probe fails on Droid | run `droid` once on the host (sign-in), or let the host's reviewer autostart path read `cadswarm.env` |
| Probe fails on Elastic | check `ELASTIC_URL`, `KIBANA_URL`, `ELASTIC_API_KEY` in `cadswarm.env`; the recall needs them |
| Nothing started by 1:45 | the driver says so; point at the recall and the tasks that did land, replay the booth recording **labelled as a replay**, and say which is live |
| The phone tunnel died | restart the driver with `--laptop`; same brief, same sequence, the member is the laptop |
| A step hangs | Next; it is logged skipped and the sequence moves on |
| The page is closed | reopen the URL; the run and the log are unaffected |
| Creao stalls mid-Act 1 | kill it earlier: that is the takeover beat; restart it for Act 2 |
| A host worker claims a manual part | cancel that task and re-assign by name; the module only rebalances busy or stale assignees |
| Review passes round 1 | steer a legitimate tight-fit change (thin the podium lid to 1.6 mm so the OLED bezel fits) and wait for the style finding — never invent one |

## Reset between judges

1. Restore the `act-1-ready` snapshot; delete `Podium_*` and `Medallion*`; hide the lattice mesh; move the
   cradle back; delete or cancel Act 2's tasks and the open brief.
2. Leave Act 1's design doc and lessons in Elastic. Leave the previous podium document too: if it ranks
   first for the next recall, say so out loud — the system is returning our own earlier run.
3. Restart Creao and any helper you killed; re-run Probe.
4. Confirm the driver page says `ready — press Start`, and that the first step is `pending`.

If the reset cannot finish before the next judge, do not fake it: start from the running Act 2, say the
recall already happened, and let the panel and `rehearsal.md` carry Act 1.
