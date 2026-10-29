# CADSwarm auto-update design

## Goal

When a new CADSwarm release is cut, everyone's FreeCAD shows a small window: **"A new release is
here"**, with **Update now**. One click saves their work, stops everything cleanly, installs the
release and restarts FreeCAD back into CADSwarm.

Non-goals: updating while someone develops in a git checkout, silently updating without a click,
and touching project folders (`Documents/CadSwarm/...`) or the app folder's manager downloads.

## Release side

The release workflow (`v*` tag) already publishes the team server, the module and
`SHA256SUMS.txt`. Add `cadswarm-workbench.zip` built from the tagged checkout:

- workbench source, excluding dev-only folders (`.git`, `.venv`, `__pycache__`, `node_modules`,
  `target`, `.pytest_cache`, `cadswarm-data`, `exports`, `.cadswarm`, `*.egg-info`, `*.pyc`),
- `module/cadswarm_module.wasm` from that release's module build,
- `cadswarm/version.py` and `package.xml` stamped with the tag's version.

It is added to `dist/` before the checksums step, so the release's `SHA256SUMS.txt` covers it.

`cadswarm/version.py` is the single source of truth for what is installed. The repository carries
the last released version; CI stamps the tag into the zip it publishes.

## Checking

`cadswarm/update.py` (stdlib only):

- `installed_version()` reads `cadswarm/version.py`.
- `check_latest()` asks the GitHub API for the newest published release (`tag_name`, `html_url`,
  `body`), caching the answer for an hour. Offline, rate-limited or malformed answers raise
  `UpdateError` in plain words; the app then quietly tries again later.
- `is_newer(candidate, current)` compares numeric dotted versions (`v0.1.18` > `v0.1.17`).

Checks run from `InitGui.py` a couple of minutes after startup, then every hour, and from a new
**Tools ▸ Check for updates…** entry. A git checkout is not updated automatically (`CADSWARM_UPDATE=1`
forces it, `CADSWARM_UPDATE=0` disables checking) so a developer's uncommitted work is never replaced.

"Later" stores the postponed tag and time; the same release stays quiet for 24 hours, a newer one
still shows.

## The window

`cadswarm/gui/updates.py`, a small non-modal always-on-top dialog in CADSwarm's own theme:

- headline, version, a short read of the release notes,
- **Update now** (primary), **Later**, **What's new** (opens the release page),
- once started: a progress bar and one line per stage.

If this computer hosts the team and other people or helpers are connected, clicking Update now asks
once, naming who will drop offline.

## Update now, in order

1. **Download and verify** — fetch `cadswarm-workbench.zip`, compare its SHA-256 with the release's
   `SHA256SUMS.txt`, extract it into a staging folder beside the workbench (zip-slip safe) and check
   that the staged `cadswarm/version.py` is the release being installed. Any failure stops here with
   the app untouched.
2. **Save** — every open FreeCAD document that has a file is written (FreeCAD's touched flag means
   "needs recompute", not "changed since saving", so it cannot tell us whether disk is behind). The
   live team model is skipped (the team server owns it); a never-saved document is kept in the app
   folder's recovery area and reopened from there. A document that cannot be saved stops the update
   before anything is closed.
3. **Leave the team** — the session disconnects, releasing this person's part locks.
4. **Stop this computer's helpers** — each runner gets its stop file, which stops its agent run and
   its warm AI server; wait for the runners to exit.
5. **Stop hosting** — if this computer runs the team, the host and (in local mode) SpacetimeDB are
   stopped with the existing `hosting.stop_team`, and their PIDs cleared.
6. **Install** — the old workbench folder is renamed aside, the staged folder becomes the workbench,
   and the old copy is deleted (left for the next startup if Windows still holds it).
7. **Restart** — a detached waiter starts `FreeCAD` with `cadswarm/gui/open_cadswarm.py` once this
   process exits, then FreeCAD closes. Opening CADSwarm again rejoins the team and restarts hosting.

If the swap fails, the old folder is put back (moved, or copied when the move is locked) before the
error is shown; if even that fails, the error says exactly where the working copy is. The startup
cleanup puts an old folder back before deleting anything when the workbench path is empty, so cleanup
can never be what makes the add-on disappear. Teammates who have not updated keep working against the
same team server and module.

## Files

- `cadswarm/version.py` — installed version.
- `cadswarm/update.py` — check, verify, extract, swap, sweep, restart helper.
- `cadswarm/gui/updates.py` — dialog and the save/stop/install/restart flow.
- `cadswarm/gui/panel.py` — Tools entry and command.
- `InitGui.py` — startup check and cleanup of a folder set aside by an earlier update.
- `scripts/package-workbench.ps1`, `.github/workflows/release.yml` — publish the workbench zip.
- `tests/test_update.py` — version compare, API/cache, checksum, zip safety, swap and rollback.

## Tests

Pure-Python tests cover version parsing/comparison, GitHub response parsing and caching, checksum
verification, zip-slip rejection, staged-version validation, the directory swap and its rollback, and
the dev-checkout/opt-out rules. The GUI flow is exercised by hand (it stops and restarts FreeCAD).
