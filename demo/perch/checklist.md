# Pre-flight, cue sheet, reset

Times assume the pitch starts on the hour. The driver is the clock that matters.

## T-45 minutes: the stack

- [ ] `powershell -File scripts\demo-up.ps1 -SkipBuild` finished; the host is running.
- [ ] The web panel is open on the second screen at the sponsor tab; the FreeCAD panel is on the laptop.
- [ ] `cadswarm-host … invite --kind human --role lead` printed a fresh presenter invite (or the presenter
      folder already has an identity).
- [ ] Elastic tabs open: the reranked board query, `cadswarm-designs`, the `distil-lessons` execution,
      `cadswarm-board-expert`. These are booth setup, not a slide.

## T-30 minutes: the team

- [ ] `lead-claude`, `arm-opencode`, `lid-creao` are joined and `active` in the panel.
- [ ] The host's planner and up to three workers are enabled.
- [ ] `droid` is signed in (`droid` once on the host) and `cadswarm-host … init-reviewer` has run.
- [ ] Sam's phone is joined and can see the briefs tab; the tunnel or the phone invite is live.
- [ ] The numbers: the last rehearsal's `runs/*.json` summary is open in a second tab.

## T-15 minutes: the state

- [ ] `act-1-ready` is restored; the six parts are present (`Clamp_Base`, `Shoulder_Hub`,
      `Arm_Proximal`, `Arm_Distal`, `Cradle_Base`, `Cradle_Lid`).
- [ ] No `Podium_*`, no `Medallion*`, no lattice mesh left visible.
- [ ] No open brief, no stale helper, no lock held by a dead member.
- [ ] The FEA report and the lattice report are open in the panel for beats 7 and 13.
- [ ] Act 1's design doc and lessons are in Elastic; the previous podium doc is still there.

## T-5 minutes: the driver

- [ ] `.venv\Scripts\python demo\perch\driver.py --project demo\perch\presenter` is running.
- [ ] The page says `ready — press Start`, Probe is all ✓, and step 1 is `pending`.
- [ ] The timeline is the one you decided on (see below), and its brief is the brief Sam has on the phone.
- [ ] The replay recording is open, muted, in a window **labelled "replay"**.

## The timeline decision (made now, never mid-pitch)

- [ ] Open the last `runs/*.json`. If `t_first_op` is **under 90 s**, run `table.json`.
- [ ] If it is **90 s or more**, or the last run missed the gate, run `table-safe.json`.
- [ ] Write the choice and the reason on the top line of `rehearsal.md`. Do not change it after the first
      judge arrives.

## Cue sheet (table, `table.json`)

| Time | Presenter | Driver step | If it has not landed |
|---|---|---|---|
| 0:00 | "One bare board, one arm, two heads." | — | — |
| 0:15 | point at the window: "five services, all live" | — | — |
| 0:30 | **press Start**; Sam types the brief on the phone | `brief` (10 s) | say the phone line, keep going; Next at 0:45 |
| 0:45 | "it recalls the first head before it plans" | `recall` (25 s) | Next; the recall is still real, say it is running |
| 1:00 | "the same owners as Act 1" | `tasks` (30 s) | Next; read the task rows that exist |
| 1:15 | "the body is held; Creao is asked anyway" | `refusal` (40 s) | Next; say the lock is on the body either way |
| 1:30 | one artifact each: FEA, lattice, round 1 | `first_part` (45 s) | — |
| 1:45 | "it is still building" | gate check | say the fallback line; stop claiming a part |
| 2:00 | hand to Q&A; leave the page up | still running | — |

Cue sheet (`table-safe.json`): same first three beats, then `first_part` at 1:15 with 55 s; the presenter
says "the body is the next run" at 1:30.

## During Q&A

- Do not press Next to look busy. The build is the answer.
- A judge's own idea goes in the **Say box**, verbatim, with `@name` if it is for one helper.
- If a card looks empty, read it: `not joined` is a fact, not a failure to hide.
- If asked for a finished part, show `runs/` and the booth capture **as a recording**.

## Reset between judges (12 minutes)

1. **Stop claiming.** Pause the driver. Leave the build running; it is evidence.
2. **Snapshot.** Restore `act-1-ready`.
3. **Delete Act 2.** `Podium_*`, `Medallion*`, the lattice mesh; cancel Act 2's tasks and any open brief.
4. **Restart helpers.** Creao bridge, any killed Claude/OpenCode folder; wait for `active`.
5. **Re-probe.** The page must be all ✓ and `ready — press Start`.
6. **Log it.** One line in `rehearsal.md`: time the reset took, and anything that had to be skipped.

If the reset cannot finish before the next judge: start from the running Act 2, say the recall already
happened, and let the panel carry Act 1. Never present a recorded run as live.

## End of day

- [ ] `powershell -File scripts\demo-down.ps1` stops only what `demo-up.ps1` started.
- [ ] Keep `runs/` and `rehearsal.md`; they are the impact numbers.
- [ ] Leave the design docs and lessons in Elastic for the next session.
