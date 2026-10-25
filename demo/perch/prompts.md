# Copy-paste: every brief, steer, note and line

Nothing here is read by the driver except the timelines. This file exists so two people type the same
words every time, and so a judge can be handed the exact text.

## Act 1 brief (panel, booth)

```
Build The Perch: a clip-on articulated arm that holds an ESP32-S3-DevKitC-1 at eye level, with a cradle
head. I'll edit the clamp myself. Plan: Clamp_Base (unassigned), Shoulder_Hub -> lead-claude,
Arm_Proximal + Arm_Distal -> arm-opencode, Cradle_Base (unassigned), Cradle_Lid -> lid-creao. Look up the
ESP32-S3 with lookup_board and use its measured Params. Two printable arm segments joined by M4 screws
and heat-set inserts, a 608 bearing at the shoulder, a dovetail head mount. Both USB-C ports must be
reachable. Every part must fit a 180 mm bed and follow our walls and fits. The distal arm is what we'll
simulate and lighten.
```

## Act 1 steers, in beat order

| Beat | Send | To |
|---|---|---|
| 4 | `thicken Arm_Distal by 2 mm; it flexes.` | `@lid-creao` |
| 5 | Tools ▸ Change a shared size…: `arm_reach = 160` | (panel) |
| 6 | Tools ▸ Auto-constrain… → Apply, then Tools ▸ Change a shared size…: `tol_press_fit = -0.08` | (panel) |
| 7 | `linear static on Arm_Distal: fixed at the elbow, 4.9 N down at the head, PETG. Publish it and tell us if it's overbuilt.` | `@arm-opencode` |
| 8 | `take_over("lid-creao")` | `@lead-claude` |
| 9 | Take `Clamp_Base` in the phone's Model tab, send the handoff note below in Team chat, arm **Continue my work if I leave**, then close the tab | (Sam's phone) |
| 11 | Tools ▸ Learned design style: add the three rules | (panel) |
| 13 | `lighten Arm_Distal to 90% stiffness, gyroid, 5 mm cells, held at the elbow, loaded at the head.` | `@arm-opencode` |
| 14 | `distil-lessons` | (Elastic workflow) |

Handoff note, sent in Team chat before arming. The handoff's `note` field is empty here; the agent gets this
text through the snapshot's `recent_messages`, which keeps Sam's last five messages, 300 characters each, so
send it as one short message.

```
Jaw opening is 34 mm; the M8 nut pocket is 0.2 mm tight. Finish the insert pocket, add 0.4 mm clearance,
keep 2.4 mm walls. I've left the lock on the clamp for you.
```

## Style rules (Learned design style, beat 11)

1. Printed enclosure walls are 2.4 mm.
2. Lids fasten with M3 heat-set inserts, not snap tabs.
3. Printed levers and arms get 3 mm outer fillets.

## Act 2 brief: booth (Sam's phone)

```
Make the Podium head for the same Perch: the same ESP32-S3 upright, an OLED window, and a 60 mm medallion
that spins on a 608 bearing. Reuse the arm and the mount. Before planning, recall the Perch we just built
and use the lessons from its review. Assign Podium_Body -> lead-claude, Medallion -> arm-opencode,
Podium_Lid -> lid-creao. Same 2.4 mm walls, same fits, same 180 mm bed.
```

## Act 2 brief: the judging table (Sam's phone)

This is `table.json`'s `brief.text`, word for word:

```
Make the Podium head for the same Perch: the same ESP32-S3 upright, a 2.4 mm lid, the same fits, the
same 180 mm bed. Reuse the arm and the mount. Recall the Perch we just built before planning, and use
what its review found. Podium_Body -> lead-claude, Podium_Lid -> lid-creao.
```

## Act 2 brief: the safe fallback (Sam's phone)

This is `table-safe.json`'s brief, word for word:

```
Make the Podium lid for the same Perch: a 2.4 mm back cover for the upright ESP32-S3 head, same fits,
same 180 mm bed. Recall the Perch we just built before planning, and use what its review found.
Podium_Lid -> lid-creao.
```

## Act 2 steers

| When | Send | To |
|---|---|---|
| the table refusal beat | `thicken Podium_Body by 2 mm` | `@lid-creao` |
| booth, after the first review | `Podium_Lid is 2.0 mm; bring it to 2.4 mm and re-check the OLED window.` | `@lid-creao` |
| booth, the medallion clash | `Medallion clashes with Podium_Body by 0.4 mm; move it 0.4 mm forward and keep the bearing seat.` | `@arm-opencode` |
| booth, the CLIs must print | `check_printability on every printed part against 180 mm; fix wall thickness before re-review.` | `@lead-claude` |

## Phone lines (Sam, table)

Said while typing, not instead of typing:

- "This is a phone, not the build machine. Same team, same model."
- "It is recalling the first head before it plans the second."
- "Those are the same owners as Act 1 — one of them is the Creao helper."
- "That refusal is the lock, not a prompt. Creao cannot edit a part lead-claude holds."
- "It is still building. Ask me anything and we will watch it answer."

## The lines that bound the claims

- Before Start (table): "The team is back at the state we finished Act 1 in; the build you are about to
  see is new."
- Before Start (booth): "We start from a bare board; Act 1 is built live."
- If the gate is missed (`table.json`): "No part has started yet. The recall and the tasks are real; the body
  is the next run." Then show the recall and the task rows, and switch to the labelled replay if the judge
  wants to see the finished head.
- If the gate is missed (`table-safe.json`): "No part has started yet. The recall and the task are real; the
  lid is the smallest job we have and it is still running." Then show the recall and the task row, and
  switch to the labelled replay if the judge wants to see the finished head.

## Never say

- "It is done" when a part has started but not finished.
- "The printer" — nothing is printed; the claim is print-ready on a 180 mm bed.
- Any price, market size, customer count or revenue.
- A number from a rehearsal that is not in `rehearsal.md`.
