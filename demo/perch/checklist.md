# Pre-flight, cue sheet, reset

Times assume the pitch starts on the hour. The driver is the clock that matters.

## T-45 minutes: the stack

- [ ] `powershell -File scripts\demo-up.ps1` finished; the host is running. Add `-SkipBuild` only after a
      successful build on this machine.
- [ ] The web panel is open on the second screen; its **Sponsors** dialog opens with `?debug` or Shift+D.
      The FreeCAD panel on the laptop has its own **Sponsors** tab. They are two separate panels reading
      the same live sponsor state, not one "sponsor tab".
- [ ] `cadswarm-host … invite --kind human --role lead` printed a fresh presenter invite (or the presenter
      folder already has an identity **for this database**: its `.cadswarm/config.json` `db` matches
      `host.json` `db`; an identity from another database joins nothing).
- [ ] Elastic tabs open: the reranked board query, `cadswarm-designs`, the `distil-lessons` execution,
      `cadswarm-board-expert`. These are booth setup, not a slide.

## T-30 minutes: the team

- [ ] `lead-claude`, `arm-opencode`, `lid-creao` are joined and `active` in the panel.
- [ ] The host's planner and up to three workers are enabled.
- [ ] `droid` is signed in (`droid` once on the host) and `cadswarm-host … init-reviewer` has run against this
      database: the reviewer folder's `.cadswarm/config.json` `db` matches `host.json` `db`.
- [ ] Sam's phone is joined and can see the Model, Tasks and Team tabs (Model for the handoff, Tasks for
      Act 2, Team for the handoff switch); the tunnel or the phone invite is live.
- [ ] The numbers: the last rehearsal's `runs/*.json` summary is open in a second tab.

## T-15 minutes: the state

- [ ] `act-1-ready` is restored; the six parts are present (`Clamp_Base`, `Shoulder_Hub`,
      `Arm_Proximal`, `Arm_Distal`, `Cradle_Base`, `Cradle_Lid`).
- [ ] No `Podium_*`, no `Medallion*`, no lattice mesh left visible.
- [ ] No open brief, no stale helper, no lock held by a dead member. Act 1's tasks are all `done` or
      `cancelled`: an `open`, `claimed` or `blocked` task from the last judge refuses Start, and the
      snapshot does not close tasks. The reviewer is the one exception:
      `init-reviewer` mints an HTTP-only member that never heartbeats, so it is `stale` between rounds.
      The sweep never orphans a reviewer, Probe counts it (with `droid` on PATH), and a stale reviewer
      does not block Start. A stale *helper* does.
- [ ] The FEA report and the lattice report are open in the panel for beats 7 and 13.
- [ ] Act 1's design doc and lessons are in Elastic; the previous podium doc is still there.

These T-15 bullets are the table pre-flight only. For the booth, `timeline.json` starts from an empty
model (`parts_any: []`): no `Clamp_`/`Shoulder_`/`Arm_`/`Cradle_`/`Podium_`/`Medallion`, no open brief, and
the manual helpers joined. Act 1 is built live.

## T-5 minutes: the driver

- [ ] `.venv\Scripts\python demo\perch\driver.py --project demo\perch\presenter` is running.
- [ ] The page says `ready — press Start`, Probe is all ✓, and step 1 is `pending`.
- [ ] The timeline is the one you decided on (see below). At the table its brief is the one Sam has on the
      phone; at the booth you type the same text in the panel.
- [ ] The replay recording is open, muted, in a window **labelled "replay"**.

## The timeline decision (made now, never mid-pitch)

- [ ] Open the last `runs/*.json`. If `t_first_op` is **under 60 s** (before 1:30 on the pitch clock), run
      `table.json`.
- [ ] If it is **60 s or more** (1:30 or later), or the last run missed the gate, run `table-safe.json`.
- [ ] Write the choice and the reason in `rehearsal.md`'s **Decision:** line at the top. Do not change it
      after the first judge arrives.

## Cue sheet (table, `table.json`)

| Time | Presenter | Driver step | If it has not landed |
|---|---|---|---|
| 0:00 | "One bare board, one arm, two heads." | — | — |
| 0:15 | point at the window: "five services, all wired in" | — | — |
| 0:30 | **press Start**; Sam types the brief on the phone | `brief` (10 s) | say the phone line, keep going; Next by 0:40 (the brief auto-skips at 0:40) |
| 0:45 (narration) | "it recalls the first head before it plans" | `recall` (25 s) | Next; the recall is still real, say it is running |
| 1:00 (narration) | "the same owners as Act 1" | `tasks` (30 s) | Next; read the task rows that exist |
| 1:15 (narration) | "the body is held; Creao is asked anyway" | `refusal` (40 s) | Next; if the page says `lid-creao refused: …`, claim the refusal. If it shows the body held by another helper or says the step was skipped, say the lock moved and do not claim it |
| 1:30 (narration) | one artifact each: FEA, lattice, round 1 | `first_part` (45 s) | — |
| 1:45 (75 s after the press) | "it is still building" | gate check | say the fallback line; the page shows it in `Runner.status`; stop claiming a part |
| 2:00 | hand to Q&A; leave the page up | still running | — |

The 0:45, 1:00, 1:15 and 1:30 times are narration cues. The JSON's cue values are 10, 20, 35 and 45 s
after the press; the driver starts each step as soon as the previous one closes, not at its cue value, so
the page can run a beat ahead of the spoken line.

Cue sheet (`table-safe.json`): same first three beats, then `first_part` (55 s), narration at 1:15; the
presenter says "the body is the next run" at 1:30.

## During Q&A

- Do not press Next to look busy. The build is the answer.
- A judge's own idea goes in the **Say box**, verbatim, with `@name` if it is for one helper.
- If a card looks empty, read it: `not joined` is a fact, not a failure to hide.
- If asked for a finished part, show `runs/` and the booth capture **as a recording**.

## Reset between table judges (12 minutes)

This is the single operative table reset order; `README.md` mirrors it. The booth does not use it: see the
note after the steps.

1. **Stop claiming.** Pause the driver. Leave the build running; it is evidence.
2. **Snapshot.** Restore `act-1-ready`.
3. **Delete Act 2.** `Podium_*`, `Medallion*`, the lattice mesh; move the cradle back; cancel Act 2's tasks
   and any open brief.
4. **Restart helpers.** Creao bridge, any killed Claude/OpenCode folder; wait for `active`.
5. **Restart the driver.** Stop `driver.py` and start it again with the chosen timeline: the runner refuses
   a second Start and the page disables Start after a run.
6. **Re-probe.** Probe must be all ✓; the page must say `ready — press Start`, and the first step must be
   `pending`.
7. **Log it.** One line in `rehearsal.md`: time the reset took, and anything that had to be skipped.

At the booth there is no reset between judges. `timeline.json` starts from an empty model and refuses Start
once Act 1 parts exist; a second booth run needs a new database (`-Db`, with the module published into it),
the presenter and reviewer folders reset (their stored identities point at the old database), and fresh
invites for the whole cast, not a restore of `act-1-ready`.

If the table reset cannot finish before the next judge: start from the running Act 2, say the recall
already happened, and let the panel carry Act 1. Never present a recorded run as live.

## End of day

- [ ] `powershell -File scripts\demo-down.ps1` stops only what `demo-up.ps1` started.
- [ ] Keep `runs/` and `rehearsal.md`; they are the impact numbers.
- [ ] Leave the design docs and lessons in Elastic for the next session.
