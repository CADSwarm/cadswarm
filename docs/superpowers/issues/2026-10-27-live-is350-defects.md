# Issue: live IS350 has invalid/misplaced parts, and the app must own the audit

**Date:** 2026-10-27
**Status:** open, for a later session (do not hand-fix the live model)
**Branch:** `feat/design-feedback` (worktree `997e5f/design-feedback`), latest commit `0b98ac4`
**Project:** `C:\Users\waves\Documents\CadSwarm\Lexus IS350`

## Why this exists

The person asked for the app to drive everything. A person (or a coding agent) must not hand-edit the
model, hand-create fix tasks or hand-message helpers. Everything below is a defect in the product's
own loop, or a live state the loop has to correct on its own.

## What happened

The 2021 IS350 brief `b_dcacbab9` was reopened after a host outage. While the front-door and greenhouse
helpers were mid-iteration, the model showed a purple door skin with a large black hole and the mirror
poking through it. That is an intermediate state, but it is also the exact class of output the app
should never accept as done. Audit numbers from a copy of `model.FCStd` (worker `surface_quality` +
`Shape.check`):

| Part | Finding |
| --- | --- |
| `DoorFL` | `valid=false`, **291 open edges**, `check()` = "Self-intersecting wire / Unorientable shape"; bbox `X 1904..3005, Y 795..915, Z 304..1418`; `beltline_z` is **990**, so it climbs 429 mm into the glass area |
| `DoorFR` | missing (only the left door was built) |
| `MirrorL` | 2 shells, valid; bbox `Z 923..1137`; intersects the malformed door |
| `Pillars` | 6 solids totalling **12,957 mm³** (should be litres); `BOPAlgo SelfIntersect`; bbox `Z 927..1072` instead of beltline→roof |
| `Roof` | still the old 6-face box, `Z 1400.75..1434.97` |
| `RearBumper` | bbox `X -400..0` — at the world origin, not the rear (`4310..4710`) |
| `SuspensionF` | bbox `X -150..150`, `valid=false`, 8 unorientable solids — at the origin, not the front axle (`X=935`) |
| `SuspensionR` | bbox `X -2400..150` — at the origin, not the rear axle (`X=3735`) |
| `FloorPan` | **41.8 L** solid 206 mm slab (`Z 130..336`), should be a `panel_t` (3 mm) skin; clashes `BodyShell` by 37.5 L |

Live context at the time: `DoorFL` task `t_bd68ea69` claimed by `worker1-dcacbab9`; greenhouse task
`t_794c2857` claimed by `worker2-dcacbab9`; `BodyShell` rebuild `t_2a06ce62` and underbody
`t_f7ec1338` both claimed by `worker3-dcacbab9`. All helpers run OpenCode.

## What the app must do (the product fix, not a manual fix)

1. **Validity gate in review.** A part with open edges, an unorientable/self-intersecting shell or
   `valid=false` is a finding for *every* part, boxy or not, even if a worker reported it done. The
   reviewer prompt now says this (commit `0b98ac4`); it needs a host install/restart to go live.
2. **Whole-model audit before review**, so existing parts are covered too, not only the current round.
   Reviewer step 5 already carries the wording (`0b98ac4`); verify at the next review that it files
   one fix task per failing part.
3. **References for named products.** A confirmed team rule exists, `add_reference` accepts local
   photos and `image_url` with credit/licence, and the reviewer's reference step is committed
   (`2238c42`); it also needs the host install to go live. First reference is stored:
   `reference/car/2021-lexus-is-fsport-front-left.jpg`.
4. **Renders** (`render_views`, phase 3) are still missing. Without them a helper cannot see a black
   hole or a floating bumper; numeric checks alone let this state through.
5. **No hand-driving.** Remove the temptation for a coding agent to create tasks, message helpers or
   move parts by hand. If the loop cannot fix a defect, that is a product bug.

## Overnight incident (2026-10-27 ~23:40–23:58)

The team stopped because the host **exited** on a single `put_preview` error while SpacetimeDB was
waiting on `https://auth.spacetimedb.com` to validate the token: every reducer timed out at 30 s, the
preview write returned an error, and `refresh()` propagated it with `?` up to `main`. Fixed on
`feat/design-feedback`:

- a failed preview is logged and skipped (it is cosmetic);
- reducers wait 120 s instead of 30 s (`REDUCER_TIMEOUT`).

The local datastore is **3.05 GB / 872,686 transactions** (682 files) after a day of ops and artifact
blobs. That is why replay and early writes are slow. SpacetimeDB's CLI has no compact/vacuum command, so
the morning job is to prune what the module keeps (event history, `op-result:` artifacts) or rebuild the
database and re-create the brief board. Do not delete the model: `model.FCStd` and `files/` are fine.

## Overnight watcher

`cadswarm/watch.py` runs every 10 minutes, audits every part with `surface_quality`, renders every third
cycle, writes `.cadswarm-team/watch.log` and `watch-summary.md`, and teaches a **confirmed** team rule
(never geometry, tasks or messages) when a new class of defect appears: invalid/open solids, parts
outside the car envelope, a slab body or collapsed pillars. It also keeps the machine awake and the
process unthrottled, so a minimised FreeCAD does not pause it.

## Suggested next session


1. Install the current host (`host/target/release/cadswarm-host.exe`) and the `cadswarm` package files
   into the FreeCAD add-on, restart the team **through the app**, and let the next review file the fix
   tasks for the table above. Do not create them by hand.
2. Build phase 3 (`render_views`) from
   `docs/superpowers/specs/2026-10-27-design-feedback-design.md` §4, so helpers and people can see the
   model. Verify on `DoorFL` and the rear bumper.
3. Add a **placement check** to the reviewer for a brief with published dimensions: every part's bbox
   must sit inside the overall envelope (`car_l`, `car_w`, `car_h`), and named assemblies must be at
   their Params (`front_overhang`, `front_overhang+wheelbase`). That is what caught the bumper and
   suspensions only by eye.
4. Re-run the suite: 766 passed, 16 skipped at `2238c42`; host tests 54 passed at `0b98ac4`.

## Tests to add with the next change

- Reviewer prompt contains the validity sentence.
- A review over a fake `surface_quality` with `valid=false` / high planar share files one fix task per
  part and does not touch passing parts.
- Placement audit rejects a bbox outside the envelope or a named assembly at the wrong Params.
