# CadSwarm Components Library and Precision Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An Onshape-style components library per team project (team uploads, built-in standard parts, parts imported from a link, exportable packs) that people and AI helpers browse, place and get recommendations from, plus team precision settings (fits, bolt-hole class, thread mode, printing, mesh and editor precision) that AI helpers suggest and people accept.

**Architecture:** Metadata in SpacetimeDB (`component`, `project_setting`, `setting_suggestion`; previews apart in `component_preview`), part bytes on the host under `<project>/library/<id>/`. Library writes are helper ops whose results carry `_components` / `_component_deletes` for `complete_op`; the host chains FreeCAD's `analyze_component` for new parts and pushes previews with `put_component_preview`. Placing parts is two worker mutations. Settings are reducers; numeric fits are mirrored into Params by a queued `set_param`. Every op gets the team's settings as `_settings`, so the worker meshes, clash-checks and checks walls with live values.

**Tech Stack:** Rust (module, rules, host), Python stdlib (helper, SpaceHub, MCP), FreeCAD `Part` / `Import` / `Mesh` (worker), PySide (FreeCAD panel), TypeScript + three.js (web).

Read first: `docs/superpowers/plans/2026-10-23-cadswarm-00-overview.md` (op kinds, worker protocol; see the 2026-10-24 contract log row).

## Global Constraints

- One spec for settings: `cadswarm/settings_spec.json` (rules crate `include_str!`, Python `cadswarm/settings.py`, web via `scripts/copy-boards.mjs`). One catalog for standard parts: `cadswarm/library/standard_parts.json` (Python, worker, web).
- Only people change settings (`rules::can_change_settings`); anyone may `suggest_setting`; only people delete library parts.
- Library ids match `c_[a-z0-9_]{1,46}`; the worker and helper refuse anything else (no path tricks).
- URL import: https only, public addresses only, 50 MB, part extensions only; nothing is scraped.
- Packs never apply settings; they come back as `pack_settings` for helpers to suggest.

## File structure

| File | Responsibility |
|---|---|
| `cadswarm/settings_spec.json`, `cadswarm/settings.py` | Setting keys, defaults, ranges; effective values; validation |
| `cadswarm/library/standard_parts.json`, `standard.py` | Standard-part dimensions, ISO 273 holes, `hole_for` with the team's fits |
| `cadswarm/library/store.py`, `search.py`, `recommend.py`, `suggest.py`, `fetch.py`, `pack.py`, `ops.py`, `present.py` | Library storage, ranking, recommendations, implied settings, URL import, packs, helper ops, plain-words text |
| `rules/src/lib.rs` | Op kinds, `SETTINGS` checks, `component_changes`, `inserted_component` |
| `module/src/library.rs`, `tables.rs`, `views.rs`, `ops.rs` | Tables, views, settings reducers, library rows from op results |
| `host/src/executor.rs`, `space.rs` | Routing, `_settings`, analysis chaining, spilled-result rows, previews |
| `cadswarm/worker/freecad_worker.py` | `analyze_component`, `insert_component`, `insert_standard_part`; settings-driven tolerances |
| `cadswarm/space_library.py`, `space_hub.py`, `mcp_server.py`, `prompt_hook.py`, `agent_setup.py`, `host/src/agents.rs` | Routes, agent tools, instructions and context |
| `cadswarm/gui/library_ui.py`, `panel.py`, `InitGui.py` | Library tab, Settings dialog, editor precision, commands |
| `web/src/ui/library.ts`, `settings.ts`, `library-data.ts`, `thumbs.ts`, `api.ts`, `store.ts`, `mock.ts` | Web Library and Settings tabs |

---

### Task 1: Rules, module and bindings
- [x] Op kinds, settings spec checks and library-row parsing in `rules`, with tests
- [x] Tables, views, `put_component_preview`, `set_setting`, `reset_setting`, `suggest_setting`, `resolve_suggestion`; `complete_op` mirrors library rows and counts uses
- [x] `spacetime publish` + `spacetime generate` (Rust and TypeScript) with the 2.13.0 CLI; `module/tests/smoke.py` covers library rows, previews, use counts, settings and suggestions

### Task 2: Host and worker
- [x] Library kinds to the helper; `_settings` on every op; `analyze_component` chained for new parts; library rows survive a spilled result; previews after `complete_op`
- [x] Worker: measure STEP / STL / FCStd (bbox, volume, holes, preview), place a library part, build every standard family; mesh / clash / wall checks read `_settings`

### Task 3: Helper, SpaceHub and agents
- [x] Store, search, recommend, suggest, URL import, packs as helper ops; SpaceHub routes (browsing runs locally); MCP tools; instructions, context block and part-word hint; planner and worker prompts

### Task 4: FreeCAD panel and web app
- [x] Library tab (fifth tab, so existing tab indexes hold), part details / link / recommend dialogs, Settings dialog with suggestions, editor precision from settings, event lines
- [x] Web Library tab with three.js thumbnails, Settings tab with suggestions, mock data

### Task 5: Verification
- [x] `cargo test` (rules, host), `pytest` (incl. FreeCAD worker tests), offscreen FreeCAD GUI check (`CADSWARM_TEST_LIBRARY_GUI=1`), `npm test`, `tsc`, `vite build`
- [x] End to end on a published module with the real host (`module/tests/library_e2e.py`): upload a STEP (measured, previewed), recommend and place a 608 and the part, a helper's setting change refused and its suggestion accepted (Params.tol_press_fit follows), pack export and re-import, people-only delete
- [ ] URL import against a real download site (covered by unit tests with a stubbed opener)
