# Rehearsal: the numbers the pitch may quote

Status: **not yet rehearsed.** R1–R3 were not run in the environment that built this folder (no SpacetimeDB
host, no Claude Code / OpenCode / Creao helpers, no Droid key, no Elastic, no phone). Every cell below is
blank on purpose; none is an estimate. The runs happen on the demo machine, and each row is filled from a
`runs/<stamp>-<timeline>.json` summary or a clock a person started and stopped.

Until R2 and R3 are filled, the pitch may quote **no timing number at all** and the table decision below
stays unmade. The first judge does not arrive until it is written.

Rules:

- Every number here comes from a `runs/<stamp>-<timeline>.json` summary, a FreeCAD reading, or a clock a
  person started and stopped. Nothing is estimated, and nothing is copied from a target.
- A blank cell is honest. Fill it or say why it is blank; never guess.
- The targets in the spec (10 s brief, 25 s recall, 30 s tasks, 40 s refusal, 45 s first part, first op by
  1:45) are **targets**. Only the measured column is quotable.
- One rehearsal per row. Three rows are required before the judging table: the booth run, the table run,
  the safe run.

**Decision:** ______ — decided ______, before the first judge, from R2 and R3.

## The table decision (fill before the first judge, never change mid-pitch)

- Last rehearsal's `t_first_op`: ______ s
- Rule: under 60 s → `table.json`; 60 s or more, or the gate failed → `table-safe.json`.
- **Chosen timeline:** ______
- Reason, one line: ______
- Decided at (time): ______

The gate is wall clock, not the run clock: the first op must land by 1:45 on the pitch clock, which is 75 s
after the 0:30 press (`first_op_by: 75`). A `t_first_op` of 60 s or more leaves too little room for a slow
beat on stage, so the safe timeline is chosen before the first judge and never swapped mid-pitch.

## Table runs (from the button press)

| Run | Date | Timeline | Probe | `t_recall` | `t_tasks` | `t_first_op` | `t_finish` | Gate failed | Steps skipped | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| R2 | — | table.json | not run | | | | | | | rehearsal not performed: no live stack |
| R3 | — | table-safe.json | not run | | | | | | | rehearsal not performed: no live stack |

What each column means:

- `t_recall` — press → the `recall` step done. The pitch line "it recalled the first head in ___ s".
- `t_tasks` — press → both task rows present with the named owners.
- `t_first_op` — press → the first op that names a part. The only number the 1:45 gate is about.
- `t_finish` — press → the last step done. At the table this is usually "still running" — write
  `still running at 2:00` rather than a number.
- `Gate failed` — `gate_failed` in the summary.
- `Steps skipped` — the step ids whose state is `skipped`, plus the one-word reason.

## Full Act 1 run (booth)

| Run | Date | Clock start (brief submitted) | Clock stop (STEP + STL exported) | Wall clock, min | Human minutes (beats 5, 6, 9, 11, 13) | Defects caught at review 1 | Notes |
|---|---|---|---|---|---|---|---|
| R1 | — |  |  |  |  |  | rehearsal not performed: no live stack |

The three defects are the ones review 1 actually filed. Write them as filed, not as expected:

```
1.
2.
3.
```

## Engineering readings (from the reports, not from memory)

| Reading | Value | Where it came from |
|---|---|---|
| FEA tip deflection |  | the published `fea_report` |
| FEA safety factor |  | the published `fea_report` |
| FEA material and load |  | the report header |
| Lattice mass saved |  | the `lattice_report` verifier console |
| Lattice verified stiffness |  | the `lattice_report` verifier console |
| Lattice cells / skin |  | the `lattice_report` |
| All parts fit 180 mm |  | `check_printability` |
| Thinnest wall after fixes |  | `check_printability` |

## Act 2: the reuse delta

| Run | Date | Timeline | Act 1 wall clock | Act 2 wall clock | Delta | Same owners? | Recall returned Act 1 first? |
|---|---|---|---|---|---|---|---|
| R4 (booth Act 2) | — | timeline.json / phone |  |  |  |  |  |

If the delta is small, the sheet says so and the pitch says so. The delta is the only Act 2 number that
has to be believable.

## Reset timing

| Between | Start | End | Minutes | What had to be skipped |
|---|---|---|---|---|
| after R2 |  |  |  |  |
| after R3 |  |  |  |  |

## What went wrong, and the change

| Run | What broke | The fix, and where it lives |
|---|---|---|
| R1 |  |  |
| R2 |  |  |
| R3 |  |  |
