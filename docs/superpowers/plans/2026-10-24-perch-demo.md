# The Perch live demo: sponsor window, driver and runbook — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the judging-table demo of The Perch: a five-card sponsor window that changes live, a local driver that runs a real Act 2 build from one button, and the runbook two presenters rehearse from.

**Architecture:** Two surfaces plus one local script. The sponsor window is a pure function over state each panel already holds (web `State`, FreeCAD `panel_snapshot`) — no new queries, tables or reducers. `demo/perch/driver.py` joins the team as a human presenter and calls the same reducers the panel does; its timelines are JSON files of steps whose "done" is a predicate over module state (events, ops, tasks, params), never an agent's prose. The runbook documents are written before rehearsal so rehearsal produces numbers, not prose.

**Tech Stack:** Python 3.11 stdlib (`http.server`, `threading`, `json`) + pytest; TypeScript + vitest for the web panel; PySide through FreeCAD for the Sponsors tab; the SpacetimeDB module is read through `cadswarm.space_hub` exactly as the panel reads it.

## Global Constraints

Copied from the spec (`docs/superpowers/specs/2026-10-24-perch-showcase-design.md`); every task includes them.

- No new CAD operations, no new module tables/reducers/bindings/schema, no host-orchestration change, no FreeCAD panel-layout change, no phone-app change.
- One approved exception, found by the Task 6 rehearsal: a reviewer-lifetime fix. `rules::Presence` gains `kind`, `plan_sweep` excludes `reviewer` from `to_orphan`, and the module passes `kind` through. The reviewer is HTTP-only and connects only during a review round, so the old sweep retired it ten minutes after `init-reviewer` and the Factory card and Probe lost it. The fix comes with a native rules test.
- A second approved exception, also found by rehearsal: the host's own recall creates no op and a refused submit returns before any op row exists, so neither leaves an event the page could read. `cadswarm/helper.py` logs one `recall` activity row per returned design (its rank, request, own problem count and the lessons) as the host, and `cadswarm/space_hub.py` logs a `refused` row before re-raising, both through the existing `log_activity` reducer; the driver reads `v_activity`. A recall made by a helper (it carries `_actor`) is not the host's planning and leaves no row. No module, rules, schema, host-orchestration or phone change, and no module publish for this one.
- Product code changes are limited to the sponsor-card UI (`web/src/ui/sponsors.ts`, `cadswarm/gui/sponsors.py`, and the one docstring in `cadswarm/gui/panel.py:1291`) plus tests, and the two approved exceptions: the reviewer-lifetime fix in `rules/src/lib.rs` and `module/src/sweep.rs`, and the activity rows in `cadswarm/helper.py` and `cadswarm/space_hub.py`. `web/src/style.css` already lays five cards out as 3 + 2, so it does not change. The rest is `demo/perch/`, `tests/` and this plan/spec.
- The browser makes no new network calls and holds no API keys. Every live card field comes from state the panel already subscribes to.
- No fabricated numbers. A number we checked once is stamped and labelled `checked …`; a live field is labelled live. The Elastic index counts in the footer are never presented as live.
- Card order is fixed: **SpacetimeDB → Anthropic → Creao → Factory.ai → Elastic**. No card disappears: Anthropic and Creao show `not joined` for an empty client list, and Factory shows `not set up` until `init-reviewer` has run.
- The driver is a human presenter: it never writes geometry, never claims a task, never posts as a helper. It may submit briefs, steers and messages, and read.
- A step is done when the module says so: task, op, lock, review, bbox, artifact, event. Never an agent's prose.
- The table run claims only: the recall returned Act 1, the tasks appeared with named owners, a locked body refused a helper, a part started. Never a finished head. One absolute gate: the first op by 1:45 on the pitch clock (75 s after the 0:30 press).
- The table run uses `table.json`; `table-safe.json` is a pre-flight choice made if the last rehearsal started no part before 1:30 (60 s after the 0:30 press). Never swapped mid-pitch.
- The driver keeps building after 2:00 and after the last step. It never stops the team.
- Five sponsor cards, one job each; the stamped footer stays visibly stamped.
- Not printed, no printer assumed, Best Hardware not entered. No pricing, market, customer or revenue claim.
- Deferred and **not built here**: `demo/perch/pitch.md`, `demo/perch/table-card.html`, `demo/perch/testimonials/`, the 2:45 video.
- Python tests are pytest (`tests/`, `testpaths = ["tests"]`); web tests are vitest (`cd web; npm test`). No new dependencies.

---

## File map

| Path | Create/Modify | Responsibility |
|---|---|---|
| `web/src/ui/sponsors.ts` | modify | Five cards in fixed order; `anthropic` and `creao` from `members.client`; `traceTitle` |
| `web/src/style.css` | leave alone | `.sponsors-grid` is already 3 columns, which renders five cards as 3 + 2 |
| `web/test/sponsors.test.ts` | modify | Card order, both new cards populated and empty, trace filtering |
| `cadswarm/gui/sponsors.py` | modify | The same two cards in FreeCAD, facts only, no trace |
| `cadswarm/gui/panel.py` | modify | One docstring line: "three" → "five" |
| `tests/test_sponsor_report.py` | modify | Exact facts for both new FreeCAD cards |
| `tests/test_panel_gui.py` | modify | Both new names render in the Sponsors tab |
| `demo/perch/driver.py` | create | Presenter join, team reads, plan validation, predicates, runner, run log, button page, probe, CLI |
| `demo/perch/table.json` | create | The five-step two-minute Act 2 sequence |
| `demo/perch/table-safe.json` | create | The four-step lid-only sequence for a slow day |
| `demo/perch/timeline.json` | create | The booth's long sequence, Act 1 through Act 2 |
| `tests/test_perch_demo.py` | create | Predicates, runner rules, gate, log, page, the three timelines |
| `demo/perch/README.md` | create | Runbook: cast, setup, Act 1, Act 2, the driver, fallbacks, reset |
| `demo/perch/prompts.md` | create | Every copy-paste brief, steer, note, phone line and style rule |
| `demo/perch/checklist.md` | create | Pre-flight, live cue sheet, reset order, the `table-safe` decision |
| `demo/perch/rehearsal.md` | create | The measurement sheet and the three rehearsals that gate the table |
| `rules/src/lib.rs` | modify | `Presence.kind`; `plan_sweep` never orphans a `reviewer`; the native test |
| `module/src/sweep.rs` | modify | Passes `kind` into `rules::Presence` |
| `.gitignore` | modify | `demo/perch/runs/` (generated run logs) |

---

### Task 1: The web sponsor window, five cards

**Files:**
- Modify: `web/src/ui/sponsors.ts:10-18` (the `Card` type), `web/src/ui/sponsors.ts:50-115` (`sponsorCards`), `web/src/ui/sponsors.ts:129-139` (`cardHTML`)
- Test: `web/test/sponsors.test.ts`

**Interfaces:**
- Consumes: `State` from `web/src/store.ts` — `members: Member[]` (`id, name, kind, client, role, status`), `tasks: Task[]` (`owner: number, status, createdBy`), `ops: Op[]` (`by: number, status`), `activity: Activity[]` (`member: number, summary`).
- Produces: `Card.id: 'spacetime' | 'anthropic' | 'creao' | 'factory' | 'elastic'`, `Card.traceTitle?: string`, and `sponsorCards(s, project): Card[]` returning five cards in that order. Task 2 mirrors the facts; Task 6 quotes them.

- [ ] **Step 1: Write the failing tests**

Append to `web/test/sponsors.test.ts`. Change the existing import line to include `cardOrder` (do not add a second import of `sponsorCards`), and reuse the file's `base` fixture:

```ts
// line 2 becomes: import { cardOrder, latestReviewOf, parseFindings, sponsorCards, toolLine } from '../src/ui/sponsors';

const team = {
  ...(base as object),
  members: [
    { id: 1, name: 'you', kind: 'human', client: 'phone', role: 'member', status: 'active', statusText: '', fence: 1, online: true },
    { id: 2, name: 'lead-claude', kind: 'agent', client: 'claude', role: 'member', status: 'active', statusText: '', fence: 1, online: true },
    { id: 3, name: 'lid-creao', kind: 'agent', client: 'creao', role: 'member', status: 'stale', statusText: '', fence: 2, online: false },
    { id: 5, name: 'reviewer', kind: 'reviewer', client: 'droid', role: 'member', status: 'left', statusText: '', fence: 1, online: false },
  ],
  tasks: [
    { id: 't_1', status: 'done', owner: 2, createdBy: 1 },
    { id: 't_2', status: 'open', owner: 2, createdBy: 1 },
    { id: 't_3', status: 'done', owner: 3, createdBy: 1 },
    { id: 't_4', status: 'done', owner: 5, createdBy: 5 },
  ],
  ops: [
    { id: 'o_1', by: 2, kind: 'create_primitive', status: 'done' },
    { id: 'o_2', by: 2, kind: 'set_param', status: 'done' },
    { id: 'o_3', by: 3, kind: 'boolean', status: 'running' },
    { id: 'o_4', by: 5, kind: 'interference', status: 'done' },
  ],
  activity: [
    { id: 1, member: 1, kind: 'say', summary: 'submitted a brief', atMs: 1 },
    { id: 2, member: 2, kind: 'tool_call', summary: 'modelling the podium body', atMs: 2 },
    { id: 3, member: 3, kind: 'tool_call', summary: 'thickening the lid', atMs: 3 },
    { id: 4, member: 5, kind: 'tool_call', summary: 'cadswarm___check_interference {}', atMs: 4 },
  ],
} as never;

describe('five sponsor cards', () => {
  it('keeps the fixed order', () => {
    expect(cardOrder(sponsorCards(team, 'perch'))).toEqual(['spacetime', 'anthropic', 'creao', 'factory', 'elastic']);
  });

  it('shows the Claude helpers with their work and only their trace', () => {
    const c = sponsorCards(team, 'perch').find(x => x.id === 'anthropic')!;
    expect(c.facts).toContainEqual(['agents', 'lead-claude · active · 1/1 active']);
    expect(c.facts).toContainEqual(['tasks done', '1/2']);
    expect(c.facts).toContainEqual(['mcp ops', '2 submitted · 2 done']);
    expect(c.traceTitle).toBe('recent work');
    expect(c.trace).toEqual(['modelling the podium body']);
  });

  it('shows Creao stale with the turn-at-a-time mode', () => {
    const c = sponsorCards(team, 'perch').find(x => x.id === 'creao')!;
    expect(c.facts).toContainEqual(['helper', 'lid-creao · stale']);
    expect(c.facts).toContainEqual(['mode', 'local bridge · one JSON tool call per turn']);
    expect(c.facts).toContainEqual(['tasks done', '1/1']);
    expect(c.trace).toEqual(['thickening the lid']);
  });

  it('keeps both cards when nobody has joined', () => {
    const alone = { ...(team as object), members: [], tasks: [], ops: [], activity: [] } as never;
    const cards = sponsorCards(alone, 'perch');
    expect(cards.map(c => c.id)).toEqual(['spacetime', 'anthropic', 'creao', 'factory', 'elastic']);
    expect(cards.find(c => c.id === 'anthropic')!.facts[0]).toEqual(['agents', 'not joined']);
    expect(cards.find(c => c.id === 'creao')!.facts[0]).toEqual(['helper', 'not joined']);
    expect(cards.find(c => c.id === 'creao')!.trace).toEqual([]);
  });

  it('leaves Factory with the plain trace label', () => {
    const f = sponsorCards(team, 'perch').find(x => x.id === 'factory')!;
    expect(f.traceTitle).toBeUndefined();
    expect(f.trace).toContain('cadswarm.check_interference');
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web; npm test -- --run`
Expected: FAIL — `cardOrder` is not exported, and `sponsorCards` returns three cards.

- [ ] **Step 3: Extend the card type and add the order helper**

In `web/src/ui/sponsors.ts`, replace the `Card` interface (lines 10–18) with:

```ts
export interface Card {
  id: 'spacetime' | 'anthropic' | 'creao' | 'factory' | 'elastic';
  name: string;
  role: string;
  facts: Array<[string, string]>;
  list?: { title: string; rows: string[] };
  trace?: string[];
  traceTitle?: string;
  calls: string[];
}

/** The window's order is part of the claim: the team's state, then who builds, then who checks, then memory. */
export function cardOrder(cards: Card[]): Card['id'][] {
  return cards.map(c => c.id);
}
```

Update the import at the top to include the member type:

```ts
import type { Member, Review, State, Store } from '../store';
```

- [ ] **Step 4: Build the two cards inside `sponsorCards`**

Insert these helpers immediately after `const held = s.locks.filter(...)` (line 64):

```ts
  const byClient = (client: string) => s.members.filter(m => m.client === client);
  const owned = (m: Member) => s.tasks.filter(t => t.owner === m.id);
  const opsBy = (m: Member) => s.ops.filter(o => o.by === m.id);
  const doneOf = (rows: Array<{ status: string }>) => rows.filter(r => r.status === 'done').length;
  const traceOf = (members: Member[]) =>
    s.activity.filter(a => members.some(m => m.id === a.member)).map(a => a.summary);
  const people = (members: Member[]) =>
    members.length ? members.map(m => `${m.name} · ${m.status}`).join(', ') : 'not joined';
  const claude = byClient('claude');
  const creao = byClient('creao');
  const claudeTasks = claude.flatMap(owned);
  const claudeOps = claude.flatMap(opsBy);
  const creaoTasks = creao.flatMap(owned);
  const creaoOps = creao.flatMap(opsBy);
```

Then insert the two cards between `spacetime` and `factory` in the returned array:

```ts
    {
      id: 'anthropic',
      name: 'Anthropic',
      role: 'the planners and builders (Claude Code)',
      facts: [
        ['agents', `${people(claude)} · ${claude.filter(m => m.status === 'active').length}/${claude.length} active`.replace(' · 0/0 active', '')],
        ['tasks done', `${doneOf(claudeTasks)}/${claudeTasks.length}`],
        ['mcp ops', `${claudeOps.length} submitted · ${doneOf(claudeOps)} done`],
        ['surface', 'Claude Code → MCP over stdio → the host runs the op'],
      ],
      trace: traceOf(claude),
      traceTitle: 'recent work',
      calls: ['Session(mcp_servers=[cadswarm]) → model_status, ops, tasks, recall_designs',
        'claude → MCP over stdio → submit_op → host → complete_op'],
    },
    {
      id: 'creao',
      name: 'Creao AI',
      role: 'the helper that drives its own conversation',
      facts: [
        ['helper', people(creao)],
        ['mode', 'local bridge · one JSON tool call per turn'],
        ['tasks done', `${doneOf(creaoTasks)}/${creaoTasks.length}`],
        ['mcp ops', `${creaoOps.length} submitted · ${doneOf(creaoOps)} done`],
        ['thread', 'one Creao conversation per helper folder'],
      ],
      trace: traceOf(creao),
      traceTitle: 'recent work',
      calls: ['cadswarm.creao_agent → CREAO_API_KEY → one tool call per turn',
        'a stale card is the takeover proof: the fence moved and the old thread stops'],
    },
```

The `people()` helper already yields `not joined` for an empty list; the `.replace(' · 0/0 active', '')` keeps that fact clean when nobody joined.

- [ ] **Step 5: Use the trace title, and check the grid needs no change**

In `cardHTML`, replace the trace heading line with:

```ts
    ${c.trace?.length ? `<div class="sponsor-trace"><span class="section-label">${esc(c.traceTitle ?? 'trace')}</span>
```

Update `sponsorCards`'s docstring from "The three cards" to "The five cards".

`.sponsors-grid` is already `repeat(3, minmax(0, 1fr))`, which lays five cards out as 3 + 2 — the
window the spec asks for. **Do not change `web/src/style.css`**; the two new cards use the existing
`.sponsor`, `dl` and `.sponsor-trace` styles. The only style-level check is visual: open `?debug`, and
confirm the five cards fit without horizontal scrolling at the demo laptop's width (the stamped footer
is a single line — a wrap there is acceptable).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd web; npm test -- --run`
Expected: PASS — all `sponsors.test.ts` cases, plus the existing suite. Then `cd web; npm run build` → no TypeScript errors.

- [ ] **Step 7: Commit**

```bash
git add web/src/ui/sponsors.ts web/test/sponsors.test.ts
git commit -m "feat(web): Anthropic and Creao join the sponsor window"
```

---

### Task 2: The same two cards in FreeCAD

**Files:**
- Modify: `cadswarm/gui/sponsors.py:33-95` (`cards`), docstrings at `:1-5`
- Modify: `cadswarm/gui/panel.py:1291`
- Test: `tests/test_sponsor_report.py`, `tests/test_panel_gui.py:49`

**Interfaces:**
- Consumes: `session.model["participants"]` rows with `id, name, kind, client, status, locks`; `session.tasks` rows with `owner` (member id or `None`), `status`, `created_by_name`. Both come from `SpaceHub.panel_snapshot()` unchanged.
- Produces: `cards(session, database="")` returns five dicts in the same order and with the same `facts` wording as Task 1, each with `id, name, role, facts, calls` (no `trace`, matching Factory in FreeCAD). Task 6 and the rehearsal read these names.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sponsor_report.py` (its `session()` fixture gains `owner` on the tasks and two more members):

```python
def test_the_window_is_five_cards_in_the_same_order_as_the_web():
    assert [c["id"] for c in cards(session())] == ["spacetime", "anthropic", "creao", "factory", "elastic"]


def test_anthropic_is_the_claude_helpers_and_their_finished_work():
    s = session()
    s.model["participants"] += [
        {"id": 7, "name": "lid-creao", "kind": "agent", "client": "creao", "status": "stale", "locks": []},
    ]
    s.tasks += [
        {"id": "t_7", "status": "done", "owner": 2, "assignee": "ann-claude", "created_by_name": "carol"},
        {"id": "t_8", "status": "open", "owner": 2, "assignee": "ann-claude", "created_by_name": "carol"},
        {"id": "t_9", "status": "done", "owner": 7, "assignee": "lid-creao", "created_by_name": "carol"},
    ]
    ann = card(cards(s), "Anthropic")
    assert ("agents", "ann-claude · active") in ann["facts"]
    assert ("tasks done", "1/2") in ann["facts"]
    assert ("live trace", "the web panel") in ann["facts"]
    assert "trace" not in ann


def test_creao_shows_stale_and_its_mode():
    s = session()
    s.model["participants"] += [
        {"id": 7, "name": "lid-creao", "kind": "agent", "client": "creao", "status": "stale", "locks": []},
    ]
    s.tasks += [{"id": "t_9", "status": "done", "owner": 7, "assignee": "lid-creao", "created_by_name": "carol"}]
    creao = card(cards(s), "Creao AI")
    assert ("helper", "lid-creao · stale") in creao["facts"]
    assert ("mode", "local bridge · one JSON tool call per turn") in creao["facts"]
    assert ("tasks done", "1/1") in creao["facts"]


def test_both_cards_stay_when_neither_helper_has_joined():
    s = session(model={"seq": 1, "params": {}, "objects": [], "participants": []}, tasks=[], briefs=[], reviews=[])
    names = [c["name"] for c in cards(s)]
    assert names == ["SpacetimeDB", "Anthropic", "Creao AI", "Factory.ai", "Elastic"]
    assert card(cards(s), "Anthropic")["facts"][0] == ("agents", "not joined")
    assert card(cards(s), "Creao AI")["facts"][0] == ("helper", "not joined")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_sponsor_report.py -v`
Expected: FAIL — only three cards, no "Anthropic".

- [ ] **Step 3: Add the builder-card helper**

Insert after `_findings` in `cadswarm/gui/sponsors.py`:

```python
def _people(members):
    return ", ".join(f"{m['name']} · {m.get('status')}" for m in members) or "not joined"


def _builder_card(card_id, name, role, client, members, tasks, extra, calls):
    """Who is running for one partner and what they finished. FreeCAD has no activity feed, so no trace:
    the same rule as the Factory card. The live trace is on the second screen, in the web panel."""
    people = [m for m in members if (m.get("client") or "") == client]
    ids = {m["id"] for m in people}
    mine = [t for t in tasks if t.get("owner") in ids]
    done = sum(1 for t in mine if t.get("status") == "done")
    party = "agents" if client == "claude" else "helper"
    facts = [(party, _people(people)), ("tasks done", f"{done}/{len(mine)}")]
    facts += extra
    facts.append(("live trace", "the web panel"))
    return {"id": card_id, "name": name, "role": role, "facts": facts, "calls": calls}
```

- [ ] **Step 4: Insert the two cards between SpacetimeDB and Factory.ai**

In the list returned by `cards()`, immediately before the `"factory"` entry, add:

```python
        _builder_card(
            "anthropic", "Anthropic", "the planners and builders (Claude Code)", "claude", members, tasks,
            [("surface", "Claude Code → MCP over stdio → the host runs the op"),
             ("mcp ops", "the web panel counts them live")],
            ["Session(mcp_servers=[cadswarm]) → model_status, ops, tasks, recall_designs",
             "claude → MCP over stdio → submit_op → host → complete_op"]),
        _builder_card(
            "creao", "Creao AI", "the helper that drives its own conversation", "creao", members, tasks,
            [("mode", "local bridge · one JSON tool call per turn"),
             ("thread", "one Creao conversation per helper folder")],
            ["cadswarm.creao_agent → CREAO_API_KEY → one tool call per turn",
             "a stale card is the takeover proof: the fence moved and the old thread stops"]),
```

Update the module docstring's "the same three services" to "the same five services" and `cards()`'s docstring to "The five cards, built from the snapshot the panel already has, plus the stamped snapshot."

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_sponsor_report.py -v`
Expected: PASS, six tests.

- [ ] **Step 6: Assert the names reach the real Sponsors tab**

In `tests/test_panel_gui.py:49`, replace the one line that names the three cards (`assert "SpacetimeDB" in r["sponsors"] and "Factory.ai" ...`) with:

```python
    assert "SpacetimeDB" in r["sponsors"] and "Anthropic" in r["sponsors"] and "Creao AI" in r["sponsors"]
    assert "Factory.ai" in r["sponsors"] and "Elastic" in r["sponsors"]
```

In `cadswarm/gui/panel.py:1291`, change `"""The three outside services, ...` to `"""The five outside services, ...`.

Run: `$env:CADSWARM_TEST_PANEL_GUI="1"; python -m pytest tests/test_panel_gui.py -v`
Expected: PASS (or SKIP when FreeCAD is not installed on this machine — record which).

- [ ] **Step 7: Commit**

```bash
git add cadswarm/gui/sponsors.py cadswarm/gui/panel.py tests/test_sponsor_report.py tests/test_panel_gui.py
git commit -m "feat(panel): the FreeCAD sponsor window shows the same five cards"
```

---

### Task 3: The driver's core — plans, predicates, the runner, the run log

**Files:**
- Create: `demo/perch/driver.py` (this task writes the module docstring, imports, `PlanError`, `Step`, `StepState`, `View`, `load_plan`, `validate`, the thirteen predicates, `PREDICATES`, `RunnerError`, `Runner`, `RunLog`)
- Test: `tests/test_perch_demo.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–2. Tests import the module by path.
- Produces (Task 4 builds on these exact names):
  - `load_plan(path: str | Path) -> dict`, `validate(plan: dict) -> None`, `PlanError`
  - `Step(id, label, predicate, at, deadline, say="", args={}, on_start={}, guard={})`, `StepState(step, state, detail, started, ended, sent, sent_at, events)`
  - `View(snapshot, ops, events, run_events, seq, started, after, now, activity=[], run_started=0.0)`
  - `PREDICATES: dict[str, Callable[[Step, View], tuple[bool, str]]]`
  - `Runner(plan, io, log=None, clock=time.time)` with `start()`, `tick()`, `skip()`, `toggle_pause()`, `say(text)`, `probe()`, `state()`, `reset_ok()`, `probe_ok`, `gate_failed`, `current`, `done`, `status`, `index`
  - `RunLog(folder, name)` with `path`, `summary_path`, `write(at, kind, detail)`, `summary(state)`
  - The `io` protocol the runner needs: `snapshot() -> dict`, `ops() -> list`, `activity() -> list`, `events(since: int) -> {"seq": int, "events": list}`, `send_brief(text, board)`, `send_steer(name, body)`, `probe() -> list[{"name","ok","detail"}]`.

The code blocks in Tasks 3–4 are the plan's first cut. The shipped `recall` and `refusal` predicates also read `view.activity` (the approved observability exception above), `tasks_landed` ignores tasks older than the press, and `reset_ok` refuses open tasks; `tests/test_perch_demo.py` is the authority for the shipped behavior.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_perch_demo.py` with the core tests. The page, join and timeline tests are added in Tasks 4 and 5.

```python
"""The Perch demo driver: the rules a judge can check on screen, as tests. No network, no FreeCAD."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo" / "perch"))

import driver  # noqa: E402


class FakeIO:
    """The team, canned: the runner sees exactly the module-shaped data it will see live."""

    def __init__(self, snapshot=None, ops=None, events=None, checks=None):
        self.snap = snapshot or {"model": {"objects": [], "participants": [], "params": {}},
                                 "tasks": [], "briefs": [], "reviews": []}
        self.op_rows = ops or []
        self.ev = events or []
        self.checks = checks if checks is not None else [
            {"name": "Claude", "ok": True, "detail": "lead-claude · active"}]
        self.sent = []
        self.seq = 0

    def snapshot(self):
        return self.snap

    def ops(self):
        return self.op_rows

    def events(self, since):
        rows = [e for e in self.ev if int(e["seq"]) > since]
        if rows:
            self.seq = int(rows[-1]["seq"])
        return {"seq": self.seq, "events": rows}

    def send_brief(self, text, board):
        self.sent.append(("brief", text, board))
        return {"brief": "b_demo"}

    def send_steer(self, to, body):
        self.sent.append(("steer", to, body))
        return {"id": "m_demo"}

    def probe(self):
        return self.checks


class Clock:
    def __init__(self, t=1_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def a_step(sid="brief", predicate="brief_sent", args=None, **over):
    base = {"id": sid, "label": sid, "predicate": predicate, "at": 0, "deadline": 30, "args": args or {}}
    base.update(over)
    return driver.Step(**base)


def a_view(**over):
    base = dict(snapshot={"model": {"objects": [], "participants": [], "params": {}},
                          "tasks": [], "briefs": [], "reviews": []},
                ops=[], events=[], run_events=[], seq=0, started=1_000.0, after=1_000.0, now=1_010.0)
    base.update(over)
    return driver.View(**base)


def a_plan(steps=None, **over):
    base = {
        "name": "test",
        "brief": {"from": "phone", "board": "esp32-s3-devkitc-1", "marker": "Podium head",
                  "text": "Make the Podium head."},
        "gate": {"first_op_by": 75, "if_missed": "No part has started yet."},
        "ready": {"parts_any": ["Clamp_Base"], "forbid_prefix": ["Podium_"], "no_open_brief": True},
        "cast": {"lead-claude": "claude"},
        "probe": ["lead-claude"],
        "steps": steps or [{"id": "brief", "label": "the brief", "at": 0, "deadline": 10,
                            "predicate": "brief_sent", "args": {"marker": "Podium head"}}],
    }
    base.update(over)
    return base


def ready_snapshot(**over):
    snap = {"model": {"objects": [{"name": "Clamp_Base", "locked_by": None},
                                  {"name": "Params", "locked_by": None}],
                      "participants": [{"id": 1, "name": "presenter", "client": "presenter", "status": "active"}],
                      "params": {}},
            "tasks": [], "briefs": [], "reviews": []}
    snap.update(over)
    return snap


# --------------------------------------------------------------------------- plans

def test_a_plan_loads_from_json(tmp_path):
    p = tmp_path / "table.json"
    p.write_text(json.dumps(a_plan()), encoding="utf-8")
    assert driver.load_plan(p)["name"] == "test"


def test_a_step_with_an_unknown_predicate_is_refused():
    with pytest.raises(driver.PlanError, match="unknown predicate"):
        driver.validate(a_plan(steps=[{"id": "x", "label": "x", "predicate": "vibes", "at": 0, "deadline": 5}]))


def test_a_step_without_a_deadline_is_refused():
    with pytest.raises(driver.PlanError, match="deadline"):
        driver.validate(a_plan(steps=[{"id": "x", "label": "x", "predicate": "confirm", "at": 0}]))


def test_steps_out_of_order_are_refused():
    steps = [{"id": "a", "label": "a", "predicate": "confirm", "at": 30, "deadline": 5},
             {"id": "b", "label": "b", "predicate": "confirm", "at": 10, "deadline": 5}]
    with pytest.raises(driver.PlanError, match="out of order"):
        driver.validate(a_plan(steps=steps))


def test_a_predicate_missing_its_arguments_is_refused():
    with pytest.raises(driver.PlanError, match="args.marker"):
        driver.validate(a_plan(steps=[{"id": "brief", "label": "b", "predicate": "brief_sent",
                                       "at": 0, "deadline": 5}]))


def test_a_duplicate_step_id_is_refused():
    steps = [{"id": "a", "label": "a", "predicate": "confirm", "at": 10, "deadline": 5},
             {"id": "a", "label": "a again", "predicate": "confirm", "at": 20, "deadline": 5}]
    with pytest.raises(driver.PlanError, match="twice"):
        driver.validate(a_plan(steps=steps))


def test_a_plan_without_a_brief_is_refused():
    with pytest.raises(driver.PlanError, match="brief"):
        driver.validate(a_plan(brief={"from": "phone", "text": "  "}))


# --------------------------------------------------------------------------- predicates

def test_brief_sent_names_the_phone_and_rejects_the_presenter():
    event = {"kind": "brief", "actor": 7, "seq": 3, "text": "Make the Podium head for the same Perch"}
    people = [{"id": 7, "name": "sam-phone", "client": "phone", "status": "active"}]
    v = a_view(run_events=[event], snapshot={"model": {"objects": [], "participants": people}, "tasks": [],
                                             "briefs": [], "reviews": []})
    ok, detail = driver.brief_sent(a_step(args={"marker": "Podium head", "by_not": "presenter"}), v)
    assert ok and "sam-phone" in detail
    ok, detail = driver.brief_sent(a_step(args={"marker": "Podium head", "by_not": "sam-phone"}), v)
    assert not ok and "sam-phone" in detail


def test_recall_needs_act1_its_three_problems_and_lessons():
    result = {"designs": [{"brief_id": "b_1", "request": "Build The Perch: a clip-on articulated arm",
                           "problems": ["missing OTG cutout", "cradle 8.0 mm", "lid wall 1.0 mm"]}],
              "lessons": ["Walls are 2.4 mm", "M3 inserts", "3 mm fillets"]}
    ops = [{"kind": "recall_designs", "status": "done", "finished_ms": 1_005_000, "result": result}]
    step = a_step("recall", "recall", {"marker": "clip-on articulated arm", "problems": 3})
    ok, detail = driver.recall(step, a_view(ops=ops))
    assert ok and "3 problems" in detail and "3 lessons" in detail
    thin = {"designs": [{**result["designs"][0], "problems": ["one"]}], "lessons": ["one"]}
    ok, detail = driver.recall(step, a_view(ops=[{**ops[0], "result": thin}]))
    assert not ok and "need 3" in detail


def test_recall_ignores_an_op_that_finished_before_the_step():
    result = {"designs": [{"request": "clip-on articulated arm", "problems": ["a", "b", "c"]}], "lessons": ["l"]}
    ops = [{"kind": "recall_designs", "status": "done", "finished_ms": 900_000, "result": result}]
    ok, detail = driver.recall(a_step("recall", "recall", {"marker": "clip-on", "problems": 3}),
                               a_view(ops=ops, started=1_000.0))
    assert not ok and "waiting" in detail


def test_tasks_land_with_their_named_owners_and_not_a_poacher():
    owners = {"Podium_Body": "lead-claude", "Podium_Lid": "lid-creao"}
    tasks = [{"title": "Podium_Body: upright", "status": "open", "assignee": "lead-claude", "owner": 2,
              "owner_name": "lead-claude"},
             {"title": "Podium_Lid: back cover", "status": "open", "assignee": "lid-creao", "owner": None,
              "owner_name": None}]
    snap = {"model": {"objects": [], "participants": []}, "tasks": tasks, "briefs": [], "reviews": []}
    ok, detail = driver.tasks_landed(a_step("tasks", "tasks_landed", {"owners": owners}), a_view(snapshot=snap))
    assert ok and "Podium_Body → lead-claude" in detail
    tasks[1] = {**tasks[1], "owner": 9, "owner_name": "host-worker"}
    snap = {**snap, "tasks": tasks}
    ok, detail = driver.tasks_landed(a_step("tasks", "tasks_landed", {"owners": owners}), a_view(snapshot=snap))
    assert not ok and "claimed by host-worker" in detail


def test_a_refusal_is_the_modules_lock_violation_not_a_sentence():
    events = [{"kind": "op_failed", "code": "lock_violation", "name": "lid-creao", "seq": 9,
               "error": "Podium_Body is held by lead-claude"}]
    step = a_step("refusal", "refusal", {"target": "Podium_Body", "by": "lid-creao"})
    assert driver.refusal(step, a_view(events=events))[0] is True
    assert driver.refusal(step, a_view(events=[{**events[0], "code": "bad_args"}]))[0] is False
    assert driver.refusal(step, a_view(events=[{**events[0], "name": "arm-opencode"}]))[0] is False


def test_the_first_part_is_an_op_that_names_a_part():
    events = [{"kind": "op", "op": "create_extrusion", "seq": 11,
               "created": ["Podium_Body"], "modified": [], "deleted": [], "recomputed": [], "broken": []}]
    step = a_step("first_part", "first_part", {"parts": ["Podium_Body", "Podium_Lid"]})
    ok, detail = driver.first_part(step, a_view(run_events=events))
    assert ok and "Podium_Body" in detail
    params = [{**events[0], "created": ["Params"], "op": "set_param"}]
    assert driver.first_part(step, a_view(run_events=params))[0] is False


def test_parts_landed_params_and_reviews_are_read_from_the_module():
    snap = ready_snapshot()
    snap["model"]["objects"] = [{"name": n, "locked_by": None} for n in
                                ["Clamp_Base", "Shoulder_Hub", "Arm_Proximal", "Arm_Distal", "Cradle_Base"]]
    ok, detail = driver.parts_landed(a_step("build", "parts_landed", {"parts": ["Clamp_Base", "Cradle_Lid"]}),
                                     a_view(snapshot=snap))
    assert not ok and "Cradle_Lid" in detail
    snap["model"]["objects"].append({"name": "Cradle_Lid", "locked_by": None})
    assert driver.parts_landed(a_step("build", "parts_landed", {"parts": ["Cradle_Lid"]}), a_view(snapshot=snap))[0]

    snap["model"]["params"] = {"arm_reach": 160.0}
    assert driver.param_changed(a_step("s", "param_changed", {"alias": "arm_reach", "value": 160}),
                                a_view(snapshot=snap))[0]
    assert not driver.param_changed(a_step("s", "param_changed", {"alias": "arm_reach", "value": 150}),
                                    a_view(snapshot=snap))[0]

    review = [{"kind": "review", "round": 1, "passed": False, "seq": 20, "brief": "b_2"}]
    assert driver.review_round(a_step("r1", "review_round", {"round": 1, "passed": False}),
                               a_view(run_events=review))[0]
    assert not driver.review_round(a_step("r2", "review_round", {"round": 2, "passed": True}),
                                   a_view(run_events=review))[0]

    fea = [{"kind": "fea_report", "seq": 21, "folder": "fea/2026-10-24 arm-distal"}]
    assert driver.report(a_step("fea", "report", {"kind": "fea_report"}), a_view(run_events=fea))[0]

    assert driver.takeover(a_step("t", "takeover"),
                           a_view(run_events=[{"kind": "taken_over", "target": "lid-creao",
                                               "taker": "lead-claude", "locks": ["Cradle_Lid"], "seq": 22}]))[0]
    assert driver.handoff(a_step("h", "handoff"),
                          a_view(run_events=[{"kind": "handoff_accepted", "from": "sam", "taker": "base-claude",
                                              "seq": 23}]))[0]
    assert driver.confirm(a_step("c", "confirm"), a_view())[0] is False


# --------------------------------------------------------------------------- runner

def a_runner(io, clock, steps=None, log=None, **plan_over):
    r = driver.Runner(a_plan(steps=steps, **plan_over), io, log=log, clock=clock)
    r.probe()
    return r


def test_start_refuses_without_a_passing_probe():
    io = FakeIO(snapshot=ready_snapshot(), checks=[{"name": "Elastic", "ok": False, "detail": "no index"}])
    r = a_runner(io, Clock())
    with pytest.raises(driver.RunnerError, match="Elastic"):
        r.start()


def test_start_refuses_until_the_team_is_back_at_act1_ready():
    snap = ready_snapshot(model={"objects": [{"name": "Params", "locked_by": None}],
                                 "participants": [], "params": {}})
    r = a_runner(FakeIO(snapshot=snap), Clock())
    with pytest.raises(driver.RunnerError, match="missing Clamp_Base"):
        r.start()


def test_a_leftover_act2_part_also_refuses_start():
    snap = ready_snapshot()
    snap["model"]["objects"].append({"name": "Podium_Body", "locked_by": None})
    r = a_runner(FakeIO(snapshot=snap), Clock())
    assert not r.reset_ok() and "Podium_Body" in r.reset_why


def test_a_stale_helper_refuses_start_but_a_stale_reviewer_does_not():
    # `init-reviewer` mints an HTTP-only member that never heartbeats: it is stale by T-5 either way.
    snap = ready_snapshot()
    snap["model"]["participants"] = [
        {"id": 1, "name": "presenter", "client": "presenter", "kind": "human", "status": "active"},
        {"id": 2, "name": "dee-droid", "client": "droid", "kind": "reviewer", "status": "stale"},
    ]
    r = a_runner(FakeIO(snapshot=snap), Clock())
    assert r.reset_ok(), r.reset_why
    snap["model"]["participants"].append(
        {"id": 3, "name": "lead-claude", "client": "claude", "kind": "agent", "status": "stale"})
    r = a_runner(FakeIO(snapshot=snap), Clock())
    assert not r.reset_ok() and "lead-claude" in r.reset_why and "dee-droid" not in r.reset_why


def test_laptop_mode_sends_the_brief_and_says_the_tunnel_is_down():
    io = FakeIO(snapshot=ready_snapshot())
    r = a_runner(io, Clock(), brief={**a_plan()["brief"], "from": "laptop"})
    r.start()
    assert io.sent[0][0] == "brief" and "Podium head" in io.sent[0][1]
    assert r.steps[0].state == "done" and "tunnel" in r.steps[0].detail
    assert r.index == 1


def test_a_step_that_misses_its_deadline_is_skipped_out_loud(tmp_path):
    clock = Clock()
    r = a_runner(FakeIO(snapshot=ready_snapshot()), clock, log=driver.RunLog(tmp_path, "test"))
    r.start()
    clock.advance(11)
    r.tick()
    assert r.steps[0].state == "skipped" and "no result in 10s" in r.steps[0].detail
    assert "skipped" in r.status


def test_the_mention_waits_for_the_body_and_goes_out_once():
    clock = Clock()
    snap = ready_snapshot()
    snap["model"]["objects"].append({"name": "Podium_Body", "locked_by": None})
    io = FakeIO(snapshot=snap)
    steps = [
        {"id": "refusal", "label": "r", "at": 0, "deadline": 40, "predicate": "refusal",
         "args": {"target": "Podium_Body", "by": "lid-creao"},
         "guard": {"held_by": {"object": "Podium_Body", "name": "lead-claude"}},
         "on_start": {"steer": {"to": "lid-creao", "body": "thicken Podium_Body by 2 mm"}}},
        {"id": "first_part", "label": "f", "at": 10, "deadline": 45, "predicate": "first_part",
         "args": {"parts": ["Podium_Body"]}},
    ]
    r = a_runner(io, clock, steps=steps)
    r.start()
    clock.advance(2)
    r.tick()
    assert io.sent == [] and "nobody" in r.steps[0].detail
    snap["model"]["objects"][-1]["locked_by"] = "lead-claude"
    clock.advance(2)
    r.tick()
    assert io.sent == [("steer", "lid-creao", "thicken Podium_Body by 2 mm")]
    clock.advance(2)
    r.tick()
    assert io.sent == [("steer", "lid-creao", "thicken Podium_Body by 2 mm")]  # sent once, not every tick
    io.ev.append({"kind": "op_failed", "code": "lock_violation", "name": "lid-creao", "seq": 4,
                  "error": "Podium_Body is held by lead-claude"})
    clock.advance(2)
    r.tick()
    assert r.steps[0].state == "done"


def test_a_body_that_is_never_held_skips_the_mention_at_its_deadline():
    clock = Clock()
    snap = ready_snapshot()
    snap["model"]["objects"].append({"name": "Podium_Body", "locked_by": "arm-opencode"})
    steps = [{"id": "refusal", "label": "r", "at": 0, "deadline": 40, "predicate": "refusal",
              "args": {"target": "Podium_Body", "by": "lid-creao"},
              "guard": {"held_by": {"object": "Podium_Body", "name": "lead-claude"}},
              "on_start": {"steer": {"to": "lid-creao", "body": "thicken"}}}]
    io = FakeIO(snapshot=snap)
    r = a_runner(io, clock, steps=steps)
    r.start()
    clock.advance(41)
    r.tick()
    assert io.sent == [] and r.steps[0].state == "skipped" and "arm-opencode" in r.steps[0].detail


def test_the_absolute_gate_fires_once_and_shows_the_fallback_line(tmp_path):
    clock = Clock()
    io = FakeIO(snapshot=ready_snapshot())
    steps = [{"id": "first_part", "label": "f", "at": 0, "deadline": 300, "predicate": "first_part",
              "args": {"parts": ["Podium_Body"]}}]
    r = a_runner(io, clock, steps=steps, log=driver.RunLog(tmp_path, "test"))
    r.start()
    clock.advance(106)
    r.tick()
    assert r.gate_failed and "No part has started" in r.status
    clock.advance(10)
    r.tick()
    assert [row["kind"] for row in r.log.rows].count("gate.failed") == 1


def test_pause_holds_the_sequence_and_the_build_keeps_running():
    clock = Clock()
    io = FakeIO(snapshot=ready_snapshot())
    r = a_runner(io, clock)
    r.start()
    assert r.toggle_pause() is True
    clock.advance(30)
    r.tick()
    assert r.steps[0].state == "waiting" and r.state()["paused"] is True
    assert r.toggle_pause() is False


def test_the_say_box_routes_a_steer_or_a_real_brief():
    io = FakeIO(snapshot=ready_snapshot())
    r = a_runner(io, clock=Clock())
    r.say("@lid-creao thicken Podium_Body by 2 mm")
    r.say("Make the medallion 55 mm and keep the same bearing.")
    assert io.sent[0] == ("steer", "lid-creao", "thicken Podium_Body by 2 mm")
    assert io.sent[1] == ("brief", "Make the medallion 55 mm and keep the same bearing.", "esp32-s3-devkitc-1")


def test_skip_confirms_a_manual_step_and_logs_the_decision(tmp_path):
    io = FakeIO(snapshot=ready_snapshot())
    log = driver.RunLog(tmp_path, "test")
    steps = [{"id": "constrain", "label": "auto-constrain", "at": 0, "deadline": 600, "predicate": "confirm"},
             {"id": "close", "label": "close", "at": 10, "deadline": 600, "predicate": "confirm"}]
    r = a_runner(io, Clock(), steps=steps, log=log)
    r.start()
    r.skip()
    assert r.steps[0].state == "confirmed"
    assert any(row["kind"] == "step.confirmed" for row in log.rows)


def test_the_run_log_records_each_step_and_the_numbers_the_pitch_quotes(tmp_path):
    clock = Clock()
    result = {"designs": [{"request": "clip-on arm", "problems": ["a", "b", "c"]}], "lessons": ["l"]}
    ops = [{"kind": "recall_designs", "status": "done", "finished_ms": 1_006_000, "result": result}]
    events = [
        {"kind": "brief", "actor": 1, "seq": 2, "text": "Make the Podium head"},
        {"kind": "op", "op": "create_extrusion", "seq": 5, "created": ["Podium_Body"],
         "modified": [], "deleted": [], "recomputed": [], "broken": []},
    ]
    io = FakeIO(snapshot=ready_snapshot(), ops=ops, events=events)
    log = driver.RunLog(tmp_path, "test")
    steps = [
        {"id": "brief", "label": "b", "at": 0, "deadline": 10, "predicate": "brief_sent",
         "args": {"marker": "Podium head"}},
        {"id": "recall", "label": "r", "at": 10, "deadline": 25, "predicate": "recall",
         "args": {"marker": "clip-on", "problems": 3}},
        {"id": "first_part", "label": "f", "at": 20, "deadline": 45, "predicate": "first_part",
         "args": {"parts": ["Podium_Body"]}},
    ]
    r = a_runner(io, clock, steps=steps, log=log)
    r.start()
    clock.advance(4)
    r.tick()
    clock.advance(6)
    r.tick()
    clock.advance(9)
    r.tick()
    assert r.done
    out = json.loads(log.summary_path.read_text(encoding="utf-8"))
    assert out["t_recall"] == pytest.approx(10.0)
    assert out["t_first_op"] == pytest.approx(19.0)
    assert out["t_finish"] == pytest.approx(19.0)
    assert [s["state"] for s in out["steps"]] == ["done", "done", "done"]
    rows = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    assert any(row["kind"] == "step.done" and "recall" in row["detail"] for row in rows)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_perch_demo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'driver'`.

- [ ] **Step 3: Write the driver's core**

Create `demo/perch/driver.py`. Step 4 adds the I/O half; keep the module importable on its own.

```python
"""The Perch demo driver: one button, a real Act 2 build, no canned output.

    python demo/perch/driver.py --project . --timeline table.json

The driver is a human member of the team (kind "human", client "presenter"): it submits the same
briefs, steers and messages a person would, and it reads the same snapshot the panel reads. It never
writes geometry, never claims a task and never posts as a helper. Nothing here ships with FreeCAD,
the host or the module: the sequence is `table.json` (judging), `table-safe.json` (slow day) or
`timeline.json` (booth), and every step is done when the module says so, never when an agent says so.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent


class PlanError(Exception):
    """A timeline file the driver will not run."""


class RunnerError(Exception):
    """Start refused: a probe failed, or the team is not back at act-1-ready."""


KNOWN_STEP_KEYS = {"id", "label", "predicate", "at", "deadline", "say", "args", "on_start", "guard"}

# What each predicate needs in its step's `args`. A timeline that forgets one is refused before Start.
REQUIRED_ARGS = {
    "brief_sent": ["marker"],
    "recall": ["marker"],
    "tasks_landed": ["owners"],
    "refusal": [],
    "first_part": ["parts"],
    "parts_landed": ["parts"],
    "param_changed": ["alias", "value"],
    "op_done": ["kind"],
    "review_round": ["round"],
    "report": ["kind"],
    "takeover": [],
    "handoff": [],
    "confirm": [],
}


@dataclass
class Step:
    id: str
    label: str
    predicate: str
    at: int
    deadline: int
    say: str = ""
    args: dict = field(default_factory=dict)
    on_start: dict = field(default_factory=dict)
    guard: dict = field(default_factory=dict)


@dataclass
class StepState:
    step: Step
    state: str = "pending"          # pending | waiting | done | skipped | confirmed
    detail: str = ""
    started: float | None = None
    ended: float | None = None
    sent: bool = False
    sent_at: float | None = None
    events: list = field(default_factory=list)


@dataclass
class View:
    """What the module says, read once per tick. Times are epoch seconds, like the views."""
    snapshot: dict
    ops: list
    events: list          # since this step began
    run_events: list      # since the press
    seq: int
    started: float
    after: float          # when this step's own action went out (the mention), else `started`
    now: float


def load_plan(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(plan)
    return plan


def validate(plan):
    if not isinstance(plan, dict):
        raise PlanError("a timeline is a JSON object")
    if not plan.get("name"):
        raise PlanError("a timeline needs a name")
    brief = plan.get("brief") or {}
    if not isinstance(brief.get("text"), str) or not brief["text"].strip():
        raise PlanError("a timeline needs brief.text: the words that go to the team")
    gate = plan.get("gate") or {}
    if "first_op_by" not in gate or not gate.get("if_missed"):
        raise PlanError("a timeline needs gate.first_op_by (seconds) and gate.if_missed (the fallback line)")
    if not isinstance(plan.get("ready"), dict):
        raise PlanError("a timeline needs ready: parts_any, forbid_prefix, no_open_brief")
    if not isinstance(plan.get("cast"), dict) or not plan["cast"]:
        raise PlanError("a timeline needs cast: owner name -> client")
    # Who must be live before Start. Not every cast member: the booth's `base-claude` joins at the
    # handoff, twenty-five minutes in, and must not block the start.
    probe = plan.get("probe")
    if not isinstance(probe, list) or not probe:
        raise PlanError("a timeline needs probe: the owners that must be live before Start")
    for name in probe:
        if name not in plan["cast"]:
            raise PlanError(f"probe names {name!r}, which is not in cast")

    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PlanError("a timeline needs at least one step")
    seen, last_at = set(), -1
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise PlanError(f"step {i} is not an object")
        unknown = set(s) - KNOWN_STEP_KEYS
        if unknown:
            raise PlanError(f"step {s.get('id') or i}: unknown keys {sorted(unknown)}")
        for key in ("id", "label", "predicate", "at", "deadline"):
            if key not in s:
                raise PlanError(f"step {s.get('id') or i} has no {key}")
        if s["predicate"] not in PREDICATES:
            raise PlanError(f"step {s['id']}: unknown predicate {s['predicate']!r} "
                            f"(known: {', '.join(sorted(PREDICATES))})")
        if s["id"] in seen:
            raise PlanError(f"step {s['id']} appears twice")
        seen.add(s["id"])
        if int(s["at"]) < last_at:
            raise PlanError(f"step {s['id']} is out of order: cue {s['at']}s after {last_at}s")
        last_at = int(s["at"])
        if int(s["deadline"]) <= 0:
            raise PlanError(f"step {s['id']}: deadline must be seconds greater than zero")
        args = s.get("args") or {}
        if not isinstance(args, dict):
            raise PlanError(f"step {s['id']}: args must be an object")
        for key in REQUIRED_ARGS[s["predicate"]]:
            if key not in args:
                raise PlanError(f"step {s['id']}: predicate {s['predicate']} needs args.{key}")


def _names(snapshot):
    model = snapshot.get("model") or {}
    return {m.get("id"): m.get("name") for m in model.get("participants") or []}


def _holder(snapshot, obj):
    for o in (snapshot.get("model") or {}).get("objects") or []:
        if o.get("name") == obj:
            return o.get("locked_by")
    return None


def _op_seconds(op):
    return float(op.get("finished_ms") or 0) / 1000.0


# --------------------------------------------------------------------------- predicates
# Each returns (done, the sentence the page and the log show). No predicate reads an agent's prose.

def brief_sent(step, view):
    marker = str(step.args["marker"]).lower()
    for e in view.run_events:
        if e.get("kind") != "brief" or marker not in (e.get("text") or "").lower():
            continue
        who = _names(view.snapshot).get(e.get("actor")) or str(e.get("actor"))
        if (step.args.get("by_not") or "").lower() == who.lower():
            return False, f"the brief came from {who}, not from the phone"
        if step.args.get("phone"):
            client = next((m.get("client") for m in (view.snapshot.get("model") or {}).get("participants") or []
                           if m.get("id") == e.get("actor")), "")
            if client != "phone":
                return False, f"the brief came from {who} ({client or 'no client'}), not the phone"
        return True, f"brief from {who}"
    return False, "waiting for the brief"


def recall(step, view):
    marker = str(step.args["marker"]).lower()
    need = int(step.args.get("problems", 3))
    for op in view.ops:
        if op.get("kind") != "recall_designs" or op.get("status") != "done":
            continue
        if _op_seconds(op) < view.started:
            continue
        result = op.get("result") or {}
        for design in result.get("designs") or []:
            text = f"{design.get('request') or ''} {design.get('text') or ''}".lower()
            if marker not in text:
                continue
            problems = design.get("problems") or []
            lessons = result.get("lessons") or []
            if len(problems) < need:
                return False, f"Act 1 returned with {len(problems)} problems, need {need}"
            if not lessons:
                return False, "Act 1 returned, but no lessons came with it"
            return True, f"Act 1 first · {len(problems)} problems · {len(lessons)} lessons"
    return False, "waiting for recall_designs"


def tasks_landed(step, view):
    wanted = step.args["owners"]
    tasks = view.snapshot.get("tasks") or []
    found, why = {}, []
    for part, owner in wanted.items():
        row = next((t for t in tasks if part.lower() in (t.get("title") or "").lower()
                    and (t.get("status") or "") != "done"), None)
        if not row:
            why.append(f"no open task for {part}")
            continue
        assigned = (row.get("assignee") or "").lower()
        claimed = (row.get("owner_name") or "").lower()
        if assigned != owner.lower():
            why.append(f"{part} assigned to {row.get('assignee') or 'nobody'}")
        elif claimed and claimed != owner.lower():
            why.append(f"{part} claimed by {row.get('owner_name')}")
        else:
            found[part] = row
    if len(found) == len(wanted):
        return True, " · ".join(f"{part} → {owner}" for part, owner in wanted.items())
    return False, "; ".join(why)


def refusal(step, view):
    target = str(step.args.get("target") or "").lower()
    who = str(step.args.get("by") or "").lower()
    for e in view.events:
        if e.get("kind") != "op_failed" or e.get("code") != "lock_violation":
            continue
        name = (e.get("name") or "").lower()
        error = (e.get("error") or "").lower()
        if who and name != who:
            continue
        if target and target not in error:
            continue
        return True, f"{e.get('name')} refused: {e.get('error')}"
    return False, f"waiting for a lock refusal from {who or 'any helper'}"


def _ops_on_parts(run_events, parts):
    for e in run_events:
        if e.get("kind") != "op":
            continue
        touched = [o for o in (e.get("created") or []) + (e.get("modified") or [])
                   if any(str(o).lower().startswith(p.lower()) for p in parts)]
        if touched:
            return e, touched
    return None, []


def first_part(step, view):
    event, touched = _ops_on_parts(view.run_events, step.args["parts"])
    if event:
        return True, f"{event.get('op')} → {', '.join(touched)}"
    return False, "waiting for the first op on a part"


def parts_landed(step, view):
    names = [o.get("name") or "" for o in (view.snapshot.get("model") or {}).get("objects") or []]
    missing = [p for p in step.args["parts"] if not any(n.startswith(p) for n in names)]
    if missing:
        return False, "still missing " + ", ".join(missing)
    return True, "all parts are in the model"


def param_changed(step, view):
    params = (view.snapshot.get("model") or {}).get("params") or {}
    alias, want = step.args["alias"], float(step.args["value"])
    got = params.get(alias)
    if got is None:
        return False, f"no {alias} in Params"
    if abs(float(got) - want) > 0.01:
        return False, f"{alias} is {got}, not {want}"
    return True, f"{alias} = {got}"


def op_done(step, view):
    kind = step.args["kind"]
    for op in view.ops:
        if op.get("kind") == kind and op.get("status") == "done" and _op_seconds(op) >= view.started:
            return True, f"{kind} is done"
    return False, f"waiting for {kind}"


def review_round(step, view):
    want = int(step.args["round"])
    passed = bool(step.args.get("passed", False))
    for e in view.run_events:
        if e.get("kind") == "review" and int(e.get("round") or 0) >= want and bool(e.get("passed")) == passed:
            return True, f"round {e.get('round')} · {'approved' if passed else 'findings'}"
    return False, f"waiting for review round {want} · {'approved' if passed else 'findings'}"


def report(step, view):
    kind = step.args["kind"]
    for e in view.run_events:
        if e.get("kind") == kind:
            return True, str(e.get("summary") or e.get("headline") or e.get("folder")
                             or e.get("job_id") or kind)
    return False, f"waiting for {kind}"


def takeover(step, view):
    for e in view.run_events:
        if e.get("kind") == "taken_over":
            return True, f"{e.get('taker')} took {e.get('target')} · {len(e.get('locks') or [])} locks"
    return False, "waiting for a takeover"


def handoff(step, view):
    for e in view.run_events:
        if e.get("kind") == "handoff_accepted":
            return True, f"{e.get('taker')} took the lock from {e.get('from')}"
    return False, "waiting for the handoff to be accepted"


def confirm(step, view):
    return False, "a person confirms this one"


PREDICATES = {
    "brief_sent": brief_sent,
    "recall": recall,
    "tasks_landed": tasks_landed,
    "refusal": refusal,
    "first_part": first_part,
    "parts_landed": parts_landed,
    "param_changed": param_changed,
    "op_done": op_done,
    "review_round": review_round,
    "report": report,
    "takeover": takeover,
    "handoff": handoff,
    "confirm": confirm,
}
```

- [ ] **Step 4: Add the runner and the run log**

Append to `demo/perch/driver.py`:

```python
class Runner:
    """One step at a time, each one done when the module says so. Every wait has a deadline."""

    def __init__(self, plan, io, log=None, clock=time.time):
        self.plan = plan
        self.io = io
        self.log = log
        self.clock = clock
        self.started_at = None
        self.paused = False
        self.gate_failed = False
        self.seq = 0
        self.checks = []
        self.status = "ready — press Start"
        self.steps = [StepState(Step(**s)) for s in plan["steps"]]
        self.index = 0
        self.run_events = []
        self.reset_why = "not checked yet"
        self._reset = (False, "not checked yet")

    # ---- what the page reads ------------------------------------------------
    @property
    def current(self):
        return self.steps[self.index] if 0 <= self.index < len(self.steps) else None

    @property
    def done(self):
        return self.index >= len(self.steps)

    @property
    def probe_ok(self):
        return bool(self.checks) and all(c.get("ok") for c in self.checks)

    def state(self):
        now = self.clock()
        t = (now - self.started_at) if self.started_at is not None else 0.0
        return {
            "timeline": self.plan["name"],
            "note": self.plan.get("note") or "",
            "started": self.started_at is not None,
            "paused": self.paused,
            "done": self.done,
            "clock": round(t, 1),
            "gate": {"failed": self.gate_failed, "if_missed": self.plan["gate"]["if_missed"]},
            "probe": self.checks,
            "probe_ok": self.probe_ok,
            "reset": {"ok": self._reset[0], "why": self._reset[1]},
            "status": self.status,
            "brief": {"text": self.plan["brief"]["text"], "from": self.plan["brief"].get("from"),
                      "board": self.plan["brief"].get("board") or ""},
            "steps": [{"id": s.step.id, "label": s.step.label, "at": s.step.at,
                       "deadline": s.step.deadline, "state": s.state, "detail": s.detail,
                       "say": s.step.say,
                       "waited": round((s.ended or now) - s.started, 1) if s.started else None,
                       "t": round(s.ended - self.started_at, 1) if s.ended and self.started_at else None}
                      for s in self.steps],
            "log": str(self.log.path) if self.log else "",
        }

    # ---- the controls -------------------------------------------------------
    def probe(self):
        self.checks = [dict(c) for c in self.io.probe()]
        self._log("probe", "ok" if self.probe_ok else "failed",
                  " · ".join(f"{c.get('name')}: {c.get('detail')}" for c in self.checks))
        return self.checks

    def reset_ok(self):
        """act-1-ready: Act 1's parts are on the arm, nothing Act 2 is left over, no brief or helper is open."""
        snap = self.io.snapshot()
        model = snap.get("model") or {}
        names = [o.get("name") or "" for o in model.get("objects") or []]
        ready = self.plan.get("ready") or {}
        problems = []
        missing = [n for n in ready.get("parts_any") or [] if n not in names]
        if missing:
            problems.append("missing " + ", ".join(missing))
        left = [n for n in names if any(n.startswith(p) for p in ready.get("forbid_prefix") or [])]
        if left:
            problems.append("Act 2 is still in the model: " + ", ".join(left))
        if ready.get("no_open_brief"):
            open_ids = [b.get("id") or "?" for b in snap.get("briefs") or []
                        if (b.get("status") or "") in ("open", "planning", "working")]
            if open_ids:
                problems.append("brief still open: " + ", ".join(open_ids))
        # The reviewer is HTTP-only: `init-reviewer` mints it, then it never heartbeats, so the sweep marks
        # it stale by T-5 with no work at risk. Probe still shows it (and Droid on PATH); only a stale
        # *helper* can strand a part, so only helpers block Start.
        stale = [m.get("name") for m in model.get("participants") or []
                 if m.get("status") == "stale" and (m.get("kind") or "") != "reviewer"]
        if stale:
            problems.append("stale helpers: " + ", ".join(stale))
        self.reset_why = "; ".join(problems)
        self._reset = (not problems, self.reset_why)
        return not problems

    def start(self):
        if self.started_at is not None:
            raise RunnerError("this run has already started")
        if not self.probe_ok:
            why = " · ".join(f"{c.get('name')}: {c.get('detail')}" for c in self.checks if not c.get("ok"))
            raise RunnerError("Probe has not passed: " + (why or "run Probe first"))
        if not self.reset_ok():
            raise RunnerError("The team is not back at act-1-ready: " + self.reset_why)
        self.started_at = self.clock()
        self._log("run", "start", self.plan["name"])
        self._begin(self.current)
        if (self.plan["brief"].get("from") or "") == "laptop":
            self.io.send_brief(self.plan["brief"]["text"], self.plan["brief"].get("board") or "")
            self._log("run", "brief", "the tunnel is down: the driver sent the brief from the laptop")
            first = self.current
            if first is not None and first.step.predicate == "brief_sent":
                self._finish(first, "done", "the driver sent it from the laptop; the tunnel is down")

    def toggle_pause(self):
        self.paused = not self.paused
        self._log("run", "paused" if self.paused else "resumed", "")
        return self.paused

    def skip(self):
        st = self.current
        if self.started_at is None or st is None:
            return
        kind = "confirmed" if st.step.predicate == "confirm" else "skipped"
        self._finish(st, kind, st.detail or kind)

    def say(self, text):
        text = (text or "").strip()
        if not text:
            return
        m = re.match(r"@([\w.-]+)\s+(.+)", text, re.S)
        if m:
            self.io.send_steer(m.group(1), m.group(2).strip())
            self._log("say", "steer", text)
        else:
            self.io.send_brief(text, self.plan["brief"].get("board") or "")
            self._log("say", "brief", text)

    # ---- the loop -----------------------------------------------------------
    def tick(self):
        if self.started_at is None:
            self.reset_ok()
            return
        now = self.clock()
        # Poll even while paused: the build keeps going, and a part that started during a pause is
        # evidence the gate must not ignore.
        fresh = self.io.events(self.seq)
        self.seq = int(fresh.get("seq") or self.seq)
        self.run_events.extend(fresh.get("events") or [])
        st = self.current
        if st is not None:
            st.events.extend(fresh.get("events") or [])
        if self.paused:
            return
        if (not self.gate_failed and now - self.started_at > float(self.plan["gate"]["first_op_by"])
                and not self._part_started()):
            self.gate_failed = True
            self.status = self.plan["gate"]["if_missed"]
            self._log("gate", "failed", self.plan["gate"]["if_missed"])
        if st is None:
            return

        view = View(snapshot=self.io.snapshot(), ops=self.io.ops(), events=st.events,
                    run_events=self.run_events, seq=self.seq, started=st.started or self.started_at,
                    after=st.sent_at or st.started or self.started_at, now=now)

        guard = (st.step.guard or {}).get("held_by")
        if guard and not st.sent:
            holder = _holder(view.snapshot, guard["object"])
            if (holder or "").lower() != (guard.get("name") or "").lower():
                st.detail = f"{guard['object']} is held by {holder or 'nobody'}"
                if now - (st.started or now) > st.step.deadline:
                    self._finish(st, "skipped", st.detail + f" (no result in {st.step.deadline}s)")
                return
            self._fire(st)
            st.detail = "the mention is out"

        ok, detail = PREDICATES[st.step.predicate](st.step, view)
        st.detail = detail
        if ok:
            self._finish(st, "done", detail)
            return
        if now - (st.started or now) > st.step.deadline:
            self._finish(st, "skipped", f"{detail} (no result in {st.step.deadline}s)")

    def _part_started(self):
        """The gate's own question: has any op named one of the parts this timeline says must start?"""
        parts = [p for s in self.steps if s.step.predicate == "first_part"
                 for p in (s.step.args.get("parts") or [])]
        if not parts:  # the booth's equivalent claim is `parts_landed`
            parts = [p for s in self.steps if s.step.predicate == "parts_landed"
                     for p in (s.step.args.get("parts") or [])]
        for e in self.run_events:
            if e.get("kind") != "op":
                continue
            names = (e.get("created") or []) + (e.get("modified") or [])
            if any(str(n).lower().startswith(p.lower()) for n in names for p in parts):
                return True
        return any(s.step.predicate in ("first_part", "parts_landed") and s.state == "done"
                   for s in self.steps)

    def _begin(self, st):
        if st is None:
            return
        st.state = "waiting"
        st.started = self.clock()
        st.sent = False
        st.sent_at = None
        self._log("step", "start", f"{st.step.id}: {st.step.label}")
        self.status = f"{st.step.id}: waiting"
        if not (st.step.guard or {}).get("held_by"):
            self._fire(st)

    def _fire(self, st):
        st.sent = True
        st.sent_at = self.clock()
        action = st.step.on_start or {}
        if "steer" in action:
            a = action["steer"]
            self.io.send_steer(a["to"], a["body"])
            self._log("step", "send", f"@{a['to']}: {a['body']}")
        elif "brief" in action:
            self.io.send_brief(action["brief"], self.plan["brief"].get("board") or "")
            self._log("step", "send", action["brief"])

    def _finish(self, st, kind, detail):
        st.state = kind
        st.detail = detail
        st.ended = self.clock()
        self._log("step", kind, f"{st.step.id}: {detail}")
        self.status = f"{st.step.id} {kind}: {detail}"
        self.index += 1
        if self.log:
            self.log.summary(self.state())
        if self.done:
            self._log("run", "done", "every step is finished; the build keeps running")
            return
        self._begin(self.current)

    def _log(self, kind, event, detail):
        if not self.log:
            return
        at = (self.clock() - self.started_at) if self.started_at is not None else 0.0
        self.log.write(at, f"{kind}.{event}", detail)


class RunLog:
    """Every run's rows, plus the summary the pitch quotes. One file per run, beside the timelines."""

    def __init__(self, folder, name):
        stamp = time.strftime("%Y-%m-%dT%H-%M-%S")
        self.path = Path(folder) / "runs" / f"{stamp}-{name}.jsonl"
        self.summary_path = self.path.with_suffix(".json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rows = []

    def write(self, at, kind, detail):
        row = {"at": round(at, 1), "wall": time.strftime("%H:%M:%S"), "kind": kind, "detail": detail}
        self.rows.append(row)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def summary(self, state):
        t = {s["id"]: s["t"] for s in state["steps"] if s["t"] is not None}
        ended = [v for v in t.values() if v is not None]
        out = {
            "timeline": state["timeline"],
            "log": self.path.name,
            "t_recall": t.get("recall"),
            "t_tasks": t.get("tasks"),
            "t_first_op": t.get("first_part"),
            "t_finish": max(ended) if ended else None,
            "gate_failed": state["gate"]["failed"],
            "steps": [{"id": s["id"], "state": s["state"], "t": s["t"]} for s in state["steps"]],
        }
        self.summary_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_perch_demo.py -v`
Expected: PASS — every test in `tests/test_perch_demo.py`, no failures, no errors.

- [ ] **Step 6: Commit**

```bash
git add demo/perch/driver.py tests/test_perch_demo.py
git commit -m "feat(perch): the demo driver runs timelines on module state, not on prose"
```

---

### Task 4: The driver's I/O — presenter join, team reads, probe, button page, CLI

**Files:**
- Modify: `demo/perch/driver.py` (imports, then append `write_presenter_config`, `connect`, `TeamIO`, `helper_checks`, `probe_checks`, `elastic_ready`, `PAGE`, `page`, `make_handler`, `serve`, `_heartbeat`, `_loop`, `parse_args`, `main`)
- Modify: `.gitignore` (one line)
- Test: `tests/test_perch_demo.py`

**Interfaces:**
- Consumes (from Task 3): `Runner`, `RunnerError`, `load_plan`, `RunLog`, `run`, `HERE`.
- Consumes (from the product, unchanged): `project_hub(project) -> (SpaceHub|None, cfg)`, `SpaceHub.join()`, `.panel_snapshot()`, `.events(since, wait)`, `.post("/briefs", {"text","board"})`, `.send({"to","kind","body"})`, `.heartbeat({"doing"})`, `._rows("v_op")`, `._result(result_json)`; `redeem(ticket, name, client)`, `read_invite(text)`, `state_dir`, `load_json`, `save_json`.
- Produces: `TeamIO` (the `io` protocol Task 3's runner needs), `connect(project, invite="", name="presenter") -> SpaceHub`, `page(plan) -> str`, `make_handler(runner)`, `serve(runner, port, open_browser=True)`, `main(argv=None)`. The JSON the page reads is `Runner.state()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_perch_demo.py`:

```python
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from cadswarm.client import load_json


def test_the_presenter_writes_the_same_files_setup_does(tmp_path):
    ticket = {"uri": "https://maincloud.spacetimedb.com/", "db": "team-db", "role": "member"}
    written = driver.write_presenter_config(
        tmp_path, ticket, "presenter", {"token": "t_demo", "participant_id": 9, "fence": 1})
    assert len(written) == 2
    cfg = load_json(tmp_path / ".cadswarm" / "config.json")
    assert cfg["client"] == "presenter" and cfg["db"] == "team-db"
    assert cfg["uri"] == "https://maincloud.spacetimedb.com"
    assert load_json(tmp_path / ".cadswarm" / "identity.json")["participant_id"] == 9
    hub, cfg = driver.project_hub(tmp_path)
    assert hub is not None and cfg["client"] == "presenter"


def test_the_probe_names_every_owner_and_the_four_partners():
    members = [
        {"id": 1, "name": "lead-claude", "client": "claude", "kind": "agent", "status": "active", "last_seen": 1_000},
        {"id": 2, "name": "lid-creao", "client": "creao", "kind": "agent", "status": "stale", "last_seen": 900},
        {"id": 3, "name": "arm-opencode", "client": "opencode", "kind": "agent", "status": "active", "last_seen": 1_000},
        {"id": 4, "name": "dee-droid", "client": "droid", "kind": "reviewer", "status": "away", "last_seen": 700},
    ]
    cast = {"lead-claude": "claude", "lid-creao": "creao", "arm-opencode": "opencode"}
    checks = driver.probe_checks(cast, members, now=1_050, which=lambda name: "C:/bin/droid.exe",
                                elastic=lambda: True)
    by = {c["name"]: c for c in checks}
    assert by["Claude"]["ok"] is True
    assert by["Creao"]["ok"] is False and "stale" in by["Creao"]["detail"]
    assert by["Droid"]["ok"] is True
    assert by["Elastic"]["ok"] is True
    assert by["arm-opencode"]["ok"] is True
    assert len(checks) == 5


def test_a_missing_helper_or_an_elastic_failure_fails_the_probe():
    checks = driver.probe_checks({"lead-claude": "claude"}, [], now=1_000,
                                 which=lambda name: None, elastic=lambda: (_ for _ in ()).throw(RuntimeError("no key")))
    by = {c["name"]: c for c in checks}
    assert by["Claude"]["ok"] is False and "not joined" in by["Claude"]["detail"]
    assert by["Droid"]["ok"] is False
    assert by["Elastic"]["ok"] is False and "no key" in by["Elastic"]["detail"]


def test_the_page_shows_the_brief_and_the_buttons():
    html = driver.page(a_plan())
    assert "Start" in html and "Next" in html and "Probe" in html
    assert "Make the Podium head." in html
    assert "fetch('/status')" in html


def test_the_status_endpoint_answers_and_start_is_a_post():
    io = FakeIO(snapshot=ready_snapshot())
    r = a_runner(io, Clock())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), driver.make_handler(r))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "Make the Podium head." in html
        status = json.loads(urllib.request.urlopen(base + "/status", timeout=5).read())
        assert status["probe_ok"] is True and status["steps"][0]["state"] == "pending"
        req = urllib.request.Request(base + "/start", data=b"{}", method="POST",
                                     headers={"content-type": "application/json"})
        assert json.loads(urllib.request.urlopen(req, timeout=5).read())["started"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_refused_start_answers_409_with_the_reason():
    snap = ready_snapshot(model={"objects": [], "participants": [], "params": {}})
    r = a_runner(FakeIO(snapshot=snap), Clock())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), driver.make_handler(r))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        req = urllib.request.Request(base + "/start", data=b"{}", method="POST",
                                     headers={"content-type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 409
        assert "act-1-ready" in json.loads(err.value.read())["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_cli_takes_the_timeline_and_the_laptop_fallback():
    a = driver.parse_args(["--timeline", "table-safe.json", "--laptop", "--port", "8899"])
    assert a.timeline == "table-safe.json" and a.laptop is True and a.port == 8899
    assert driver.parse_args([]).timeline == "table.json"
```

Add `import urllib.error` next to `urllib.request`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_perch_demo.py -v`
Expected: FAIL — `write_presenter_config` and `make_handler` do not exist.

- [ ] **Step 3: Replace the import block**

At the top of `demo/perch/driver.py`, replace everything above `HERE = Path(__file__).resolve().parent` with:

```python
import argparse
import html
import json
import os
import re
import shutil
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cadswarm.agent_setup import redeem
from cadswarm.client import load_json, save_json, state_dir
from cadswarm.invite import read_invite
from cadswarm.space_hub import project_hub
```

- [ ] **Step 4: Add the join, the reads and the probe**

Append to `demo/perch/driver.py`:

```python
# --------------------------------------------------------------------------- the presenter

def write_presenter_config(project, ticket, name, identity):
    """The same two files `cadswarm setup` writes, for a client the CLI does not offer.

    `client = "presenter"` is stored on the member and sent on every rejoin, so the team can see that
    this human is the demo's presenter and not a helper. The folder's identity is a SpacetimeDB token:
    it stays under .cadswarm/, which the repo already ignores.
    """
    folder = state_dir(project)
    os.makedirs(folder, exist_ok=True)
    Path(folder, ".gitignore").write_text("*\n", encoding="utf-8")
    ident_path = os.path.join(folder, "identity.json")
    save_json(ident_path, identity)
    save_json(os.path.join(folder, "config.json"), {
        "uri": ticket["uri"].rstrip("/"), "db": ticket["db"], "name": name,
        "client": "presenter", "role": ticket.get("role") or "member"})
    return [ident_path, os.path.join(folder, "config.json")]


def connect(project, invite="", name="presenter"):
    """Join once with an invite, then reuse this folder's identity — the same rule as `cadswarm brief`.

    A rejoin at start is what the MCP server does too: it takes a fresh fence so a crashed run cannot
    keep acting. Nothing here is special-cased in the module or the host.
    """
    if invite:
        ticket = read_invite(invite)
        if not load_json(os.path.join(state_dir(project), "identity.json")):
            write_presenter_config(project, ticket, name, redeem(ticket, name, "presenter"))
    hub, _cfg = project_hub(project)
    if not hub:
        raise SystemExit("driver: run this once with --invite '<cadswarm-2:...>' "
                         "(mint one on the host: cadswarm-host --data <dir> invite --kind human --role lead)")
    hub.join()
    return hub


class TeamIO:
    """The team as the driver sees it: the same reducers and the same reads the panel uses."""

    def __init__(self, hub, cast=None, probe=None):
        self.hub = hub
        self.cast = cast or {}
        self.probe_cast = {n: c for n, c in self.cast.items() if probe is None or n in probe}

    def snapshot(self):
        s = self.hub.panel_snapshot()
        return {"model": s["model"], "tasks": s["tasks"], "briefs": s["briefs"], "reviews": s["reviews"]}

    def ops(self):
        """The op rows the panel's views hold. SpaceHub has no public ops() reader; the demo reads
        `v_op` directly and resolves only the results it needs (a recall's designs and lessons)."""
        rows = sorted(self.hub._rows("v_op"), key=lambda o: o.get("created_ms") or 0)[-60:]
        out = []
        for r in rows:
            result = {}
            if r.get("kind") == "recall_designs" and r.get("status") == "done":
                try:
                    result = self.hub._result(r.get("result_json") or "")
                except Exception:
                    result = {}
            out.append({"id": r.get("id"), "kind": r.get("kind"), "status": r.get("status"),
                        "by": r.get("by"), "finished_ms": r.get("finished_ms") or 0, "result": result})
        return out

    def events(self, since):
        return self.hub.events(since, wait=0)

    def send_brief(self, text, board):
        return self.hub.post("/briefs", {"text": text, "board": board or ""})

    def send_steer(self, to, body):
        # The module matches a bare name (`live_by_name`); an "@" here would address a member that
        # never joins and the steer would wait forever.
        return self.hub.send({"to": str(to).lstrip("@"), "kind": "steer", "body": body})

    def probe(self):
        snap = self.snapshot()
        return probe_checks(self.probe_cast, snap["model"].get("participants") or [], time.time(),
                            which=shutil.which, elastic=elastic_ready)


def helper_checks(cast, members, now, fresh_s=180):
    """Every owner the timeline names: joined as the right client, active, and heartbeating."""
    by_name = {str(m.get("name") or "").lower(): m for m in members}
    out = {}
    for name, client in cast.items():
        m = by_name.get(name.lower())
        if not m or (m.get("client") or "") != client:
            out[name] = {"name": name, "ok": False, "detail": f"not joined as a {client} helper"}
            continue
        why = []
        if m.get("status") != "active":
            why.append(f"status {m.get('status')}")
        if now - float(m.get("last_seen") or 0) > fresh_s:
            why.append(f"no heartbeat in {int(fresh_s)}s")
        out[name] = {"name": name, "ok": not why,
                     "detail": "; ".join(why) or f"{m.get('name')} · {client} · active"}
    return out


def elastic_ready():
    from cadswarm.knowledge import Elastic
    Elastic.from_env().search("cadswarm-designs", "perch", 1)
    return True


def probe_checks(cast, members, now, which=shutil.which, elastic=elastic_ready):
    """The four partners the window claims, then any owner those four do not already name."""
    helpers = helper_checks(cast, members, now)
    out = []
    for partner, client in (("Claude", "claude"), ("Creao", "creao")):
        mine = [helpers[n] for n, c in cast.items() if c == client]
        if not mine:
            continue
        bad = [c for c in mine if not c["ok"]]
        out.append({"name": partner, "ok": not bad,
                    "detail": "; ".join(f"{c['name']}: {c['detail']}" for c in bad) if bad
                              else ", ".join(c["name"] for c in mine) + " · active"})
    reviewer = next((m for m in members if (m.get("kind") or "") == "reviewer"), None)
    droid_cli = bool(which("droid"))
    detail = (f"{reviewer.get('name')} · {reviewer.get('status')}" if reviewer else "no reviewer has joined")
    out.append({"name": "Droid", "ok": bool(reviewer) and droid_cli,
                "detail": detail + ("" if droid_cli else " · droid CLI is not on PATH")})
    try:
        elastic()
        out.append({"name": "Elastic", "ok": True, "detail": "design memory reachable"})
    except Exception as e:
        out.append({"name": "Elastic", "ok": False, "detail": str(e)[:160]})
    for name, client in cast.items():
        if client not in ("claude", "creao"):
            out.append(helpers[name])
    return out
```

- [ ] **Step 5: Add the button page**

Append to `demo/perch/driver.py`:

```python
# --------------------------------------------------------------------------- the button page
# One screen the presenter can hit without aiming: Start, Next, Pause, Probe, the brief to copy, and a
# Say box that runs a judge's own idea. It polls /status; every word on it comes from Runner.state().

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
:root { color-scheme: dark; --bg:#0d1117; --panel:#161b22; --line:#30363d; --body:#e6edf3;
        --muted:#8b949e; --ok:#3fb950; --bad:#f85149; --wait:#d29922; --accent:#58a6ff; }
* { box-sizing: border-box; }
body { margin:0; padding:24px; background:var(--bg); color:var(--body);
       font:16px/1.45 ui-sans-serif, -apple-system, "Segoe UI", sans-serif; }
h1 { font-size:26px; margin:0 0 4px; }
.muted { color:var(--muted); }
#status { font-size:18px; margin:2px 0 14px; }
#clock { font:600 40px/1 ui-monospace, monospace; margin:10px 0 4px; }
#gate { color:var(--bad); font-weight:600; }
button { font:600 17px/1 inherit; color:#0d1117; background:var(--accent); border:0; border-radius:8px;
         padding:14px 22px; cursor:pointer; }
button.quiet { background:var(--panel); color:var(--body); border:1px solid var(--line); }
button:disabled { opacity:.45; cursor:not-allowed; }
.controls { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
ol#steps { list-style:none; padding:0; margin:18px 0; display:grid; gap:8px; }
ol#steps li { background:var(--panel); border:1px solid var(--line); border-left:6px solid var(--line);
              border-radius:8px; padding:10px 14px; }
li.waiting { border-left-color:var(--wait); } li.done, li.confirmed { border-left-color:var(--ok); }
li.skipped { border-left-color:var(--bad); }
.detail { color:var(--muted); font:13px/1.4 ui-monospace, monospace; }
fieldset { border:1px solid var(--line); border-radius:8px; padding:14px; margin:18px 0; }
legend { padding:0 6px; color:var(--muted); }
#brief { font:15px/1.5 ui-monospace, monospace; white-space:pre-wrap; margin:0 0 10px; }
#probe { display:grid; gap:4px; font:13px/1.4 ui-monospace, monospace; }
input[type=text] { width:min(560px, 90%); font:16px/1.4 ui-monospace, monospace; padding:10px 12px;
                   border-radius:8px; border:1px solid var(--line); background:var(--bg); color:var(--body); }
</style>
</head>
<body>
<h1>__TITLE__</h1>
<p class="muted">__NOTE__</p>
<p id="status">loading…</p>
<div id="clock">0:00</div>
<div class="controls">
  <button id="start">Start</button>
  <button id="next" class="quiet">Next</button>
  <button id="pause" class="quiet">Pause</button>
  <button id="probe" class="quiet">Probe</button>
</div>
<p id="gate" hidden></p>
<ol id="steps"></ol>
<fieldset><legend>the brief on Sam's phone</legend>
  <pre id="brief">__BRIEF__</pre>
  <button id="copy" class="quiet">Copy the brief</button>
</fieldset>
<fieldset><legend>say it live (a judge's own idea)</legend>
  <form id="say"><input id="text" type="text" placeholder="@lid-creao thicken Podium_Body by 2 mm">
  <button>Send</button></form>
  <p class="muted">A line that starts with @name is a steer to that helper; anything else is a real brief.</p>
</fieldset>
<fieldset><legend>probe</legend><div id="probe"></div></fieldset>
<script>
const $ = s => document.querySelector(s);
const mmss = s => `${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,'0')}`;
function paint(s) {
  $('#status').textContent = s.status;
  $('#clock').textContent = mmss(s.clock || 0);
  $('#start').disabled = s.started || !s.probe_ok || !s.reset.ok;
  $('#next').disabled = !s.started || s.done;
  $('#pause').textContent = s.paused ? 'Resume' : 'Pause';
  $('#pause').disabled = !s.started;
  $('#gate').hidden = !s.gate.failed;
  $('#gate').textContent = s.gate.failed ? s.gate.if_missed : '';
  $('#steps').innerHTML = s.steps.map((st, i) => `<li class="${st.state}">
    <b>${i+1}. ${st.label}</b> <span class="muted">cue ${mmss(st.at)} · deadline ${st.deadline}s</span>
    <div class="detail">${st.state}${st.waited !== null ? ` · ${st.waited}s` : ''}${st.detail ? ` · ${st.detail}` : ''}</div>
    ${st.say ? `<div class="detail">${st.say}</div>` : ''}</li>`).join('');
  $('#probe').innerHTML = s.probe.map(c => `<div>${c.ok ? '✓' : '✗'} <b>${c.name}</b> — ${c.detail}</div>`).join('');
  $('#start').title = s.reset.ok ? '' : s.reset.why;
}
async function poll() { try { paint(await (await fetch('/status')).json()); } catch (e) {} }
async function post(path, body) {
  const r = await fetch(path, {method:'POST', headers:{'content-type':'application/json'},
                               body: JSON.stringify(body || {})});
  const j = await r.json();
  if (j.error) { $('#status').textContent = j.error; } else { paint(j); }
}
$('#start').onclick = () => post('/start');
$('#next').onclick = () => post('/next');
$('#pause').onclick = () => post('/pause');
$('#probe').onclick = () => post('/probe');
$('#copy').onclick = async () => { await navigator.clipboard.writeText($('#brief').textContent); };
$('#say').onsubmit = e => { e.preventDefault(); const t = $('#text').value;
  if (t.trim()) { post('/say', {text: t}); $('#text').value = ''; } };
setInterval(poll, 1000); poll();
</script>
</body>
</html>"""


def page(plan):
    return (PAGE.replace("__TITLE__", html.escape(f"The Perch · {plan['name']} run"))
                .replace("__NOTE__", html.escape(plan.get("note") or ""))
                .replace("__BRIEF__", html.escape(plan["brief"]["text"])))
```

- [ ] **Step 6: Add the HTTP server, the loop and the CLI**

Append to `demo/perch/driver.py`:

```python
# --------------------------------------------------------------------------- serving

def make_handler(runner):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _json(self, value, code=200):
            self._send(code, json.dumps(value), "application/json")

        def _body(self):
            n = int(self.headers.get("content-length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def do_GET(self):
            if self.path.startswith("/status"):
                self._json(runner.state())
            elif self.path in ("/", "/index.html"):
                self._send(200, page(runner.plan), "text/html; charset=utf-8")
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            body = self._body()
            try:
                if self.path == "/start":
                    runner.start()
                elif self.path == "/next":
                    runner.skip()
                elif self.path == "/pause":
                    runner.toggle_pause()
                elif self.path == "/probe":
                    runner.probe()
                elif self.path == "/say":
                    runner.say(body.get("text") or "")
                else:
                    return self._json({"error": "not found"}, 404)
            except RunnerError as e:
                return self._json({"error": str(e)}, 409)
            self._json(runner.state())

        def log_message(self, *args):
            pass  # one page, one laptop: no access log on the console the presenter is reading

    return Handler


def serve(runner, port, open_browser=True):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(runner))
    url = f"http://127.0.0.1:{port}/"
    print(f"perch driver: {url}  ({runner.plan['name']})")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def _heartbeat(hub, every=10.0):
    """The presenter is a member: without a heartbeat it goes stale and the panel says so."""
    while True:
        try:
            hub.heartbeat({"doing": "presenting"})
        except Exception:
            return
        time.sleep(every)


def _loop(runner, every=2.0):
    while True:
        try:
            runner.tick()
        except Exception as e:  # a demo must never die in silence
            runner.status = f"driver error: {e}"
            runner._log("driver", "error", str(e)[:400])
        time.sleep(every)


# --------------------------------------------------------------------------- cli

def parse_args(argv=None):
    a = argparse.ArgumentParser(description="The Perch demo driver: one button, a real build.")
    a.add_argument("--project", default=".", help="the folder cadswarm setup wrote to (holds .cadswarm/)")
    a.add_argument("--timeline", default="table.json", help="table.json, table-safe.json or timeline.json")
    a.add_argument("--invite", default="", help="a cadswarm-2: invite for the presenter (first run only)")
    a.add_argument("--name", default="presenter", help="the name the team sees")
    a.add_argument("--port", type=int, default=8765)
    a.add_argument("--laptop", action="store_true", help="send the brief from this laptop (the tunnel is down)")
    a.add_argument("--no-browser", action="store_true")
    return a.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    project = Path(a.project).expanduser().resolve()
    hub = connect(project, a.invite, a.name)
    plan_path = Path(a.timeline)
    plan = load_plan(plan_path if plan_path.is_absolute() else HERE / plan_path)
    if a.laptop:
        plan["brief"]["from"] = "laptop"
    log = RunLog(HERE, plan["name"])
    runner = Runner(plan, TeamIO(hub, plan["cast"], plan["probe"]), log=log)
    runner.probe()
    threading.Thread(target=_heartbeat, args=(hub,), daemon=True).start()
    threading.Thread(target=_loop, args=(runner,), daemon=True).start()
    serve(runner, a.port, open_browser=not a.no_browser)


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Ignore the generated run logs**

Append to `.gitignore`:

```
# The Perch demo's run logs: generated by demo/perch/driver.py, quoted by rehearsal.md.
demo/perch/runs/
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m pytest tests/test_perch_demo.py -v`
Expected: PASS — the Task 3 tests plus the join, probe, page and HTTP tests.

- [ ] **Step 9: Check the CLI starts and stops without a team**

Run: `python demo/perch/driver.py --help`
Expected: the usage line, then exit 0 — no network, no browser.

- [ ] **Step 10: Commit**

```bash
git add demo/perch/driver.py tests/test_perch_demo.py .gitignore
git commit -m "feat(perch): the driver joins as a presenter, pages the button screen and logs every run"
```

---

### Task 5: The three timelines

**Files:**
- Create: `demo/perch/table.json`, `demo/perch/table-safe.json`, `demo/perch/timeline.json`
- Test: `tests/test_perch_demo.py`

**Interfaces:**
- Consumes: the plan schema Task 3 validates — `name`, `brief{from,board,marker,text}`, `gate{first_op_by,if_missed}`, `ready{parts_any,forbid_prefix,no_open_brief}`, `cast{owner: client}`, `probe[owner]` (the owners that must be live before Start), `steps[{id,label,at,deadline,predicate,args,say,on_start,guard}]`.
- Produces: the three files the runbook names. `table.json` is what the judging table runs; `table-safe.json` is the pre-flight fallback; `timeline.json` is the booth. Task 6 quotes their briefs verbatim; Task 7 records which was run.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_perch_demo.py`:

```python
TIMELINES = ["table.json", "table-safe.json", "timeline.json"]


@pytest.mark.parametrize("name", TIMELINES)
def test_every_shipped_timeline_validates(name):
    plan = driver.load_plan(ROOT / "demo" / "perch" / name)
    assert plan["steps"] and plan["cast"]


@pytest.mark.parametrize("name", TIMELINES)
def test_every_named_owner_and_steer_is_in_the_cast(name):
    plan = driver.load_plan(ROOT / "demo" / "perch" / name)
    assert plan["probe"] and set(plan["probe"]) <= set(plan["cast"])
    for s in plan["steps"]:
        for owner in (s.get("args") or {}).get("owners", {}).values():
            assert owner in plan["cast"], f"{s['id']} names {owner}"
        steer = ((s.get("on_start") or {}).get("steer") or {}).get("to")
        if steer:
            assert steer in plan["cast"], f"{s['id']} steers {steer}"


def test_the_table_run_is_the_five_beats_in_order():
    plan = driver.load_plan(ROOT / "demo" / "perch" / "table.json")
    assert [s["id"] for s in plan["steps"]] == ["brief", "recall", "tasks", "refusal", "first_part"]
    assert [s["predicate"] for s in plan["steps"]] == [
        "brief_sent", "recall", "tasks_landed", "refusal", "first_part"]
    assert plan["gate"]["first_op_by"] == 75
    assert plan["brief"]["from"] == "phone"
    assert plan["steps"][0]["deadline"] == 10
    assert plan["steps"][-1]["at"] < plan["gate"]["first_op_by"]


def test_the_table_brief_asks_for_two_parts_and_recalls_act1():
    text = driver.load_plan(ROOT / "demo" / "perch" / "table.json")["brief"]["text"]
    assert "Podium_Body" in text and "Podium_Lid" in text and "Medallion" not in text
    assert "Recall the Perch" in text and "180 mm bed" in text


def test_the_safe_timeline_is_lid_only_and_has_no_refusal_beat():
    plan = driver.load_plan(ROOT / "demo" / "perch" / "table-safe.json")
    assert "Podium_Lid" in plan["brief"]["text"] and "Podium_Body" not in plan["brief"]["text"]
    assert plan["steps"][2]["args"]["owners"] == {"Podium_Lid": "lid-creao"}
    assert [s["id"] for s in plan["steps"]] == ["brief", "recall", "tasks", "first_part"]
    assert plan["gate"]["first_op_by"] == 75


def test_the_booth_timeline_covers_every_beat_the_window_claims():
    plan = driver.load_plan(ROOT / "demo" / "perch" / "timeline.json")
    predicates = {s["predicate"] for s in plan["steps"]}
    assert {"brief_sent", "recall", "tasks_landed", "refusal", "parts_landed", "param_changed",
            "confirm", "report", "takeover", "handoff", "review_round", "op_done"} <= predicates
    reports = {s["args"].get("kind") for s in plan["steps"] if s["predicate"] == "report"}
    assert {"fea_report", "lattice_report"} <= reports
    assert plan["brief"]["from"] == "panel"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_perch_demo.py -k timeline -v`
Expected: FAIL — the files do not exist.

- [ ] **Step 3: Write `table.json`**

```json
{
  "name": "table",
  "note": "The two-minute Act 2 run at the judging table. Press at 0:30 of the pitch; the first op must land by 1:45, 75 s after the press. Nothing here claims a finished head.",
  "brief": {
    "from": "phone",
    "board": "esp32-s3-devkitc-1",
    "marker": "Podium head for the same Perch",
    "text": "Make the Podium head for the same Perch: the same ESP32-S3 upright, a 2.4 mm lid, the same fits, the same 180 mm bed. Reuse the arm and the mount. Recall the Perch we just built before planning, and use what its review found. Podium_Body -> lead-claude, Podium_Lid -> lid-creao."
  },
  "gate": {
    "first_op_by": 75,
    "if_missed": "No part has started yet. The recall and the tasks are real; the body is the next run."
  },
  "ready": {
    "parts_any": ["Clamp_Base", "Shoulder_Hub", "Arm_Proximal", "Arm_Distal", "Cradle_Base", "Cradle_Lid"],
    "forbid_prefix": ["Podium_", "Medallion"],
    "no_open_brief": true
  },
  "cast": {
    "lead-claude": "claude",
    "lid-creao": "creao",
    "arm-opencode": "opencode"
  },
  "probe": ["lead-claude", "lid-creao"],
  "steps": [
    {
      "id": "brief", "label": "Sam sends the table brief from the phone",
      "at": 0, "deadline": 10, "predicate": "brief_sent",
      "args": {"marker": "Podium head for the same Perch", "by_not": "presenter", "phone": true},
      "say": "The brief is on Sam's phone. It goes out from the phone, not from this laptop."
    },
    {
      "id": "recall", "label": "The planner recalls the Perch before it plans",
      "at": 10, "deadline": 25, "predicate": "recall",
      "args": {"marker": "clip-on articulated arm", "query": "Make the Podium head", "problems": 3},
      "say": "Before it plans, it looks up what we already built."
    },
    {
      "id": "tasks", "label": "Tasks appear with named owners",
      "at": 20, "deadline": 30, "predicate": "tasks_landed",
      "args": {"owners": {"Podium_Body": "lead-claude", "Podium_Lid": "lid-creao"}},
      "say": "Two parts, the same owners as Act 1."
    },
    {
      "id": "refusal", "label": "The mention is refused on the held body",
      "at": 35, "deadline": 40, "predicate": "refusal",
      "args": {"target": "Podium_Body", "by": "lid-creao"},
      "guard": {"held_by": {"object": "Podium_Body", "name": "lead-claude"}},
      "on_start": {"steer": {"to": "lid-creao", "body": "thicken Podium_Body by 2 mm"}},
      "say": "The body is held. Creao is asked to change it anyway."
    },
    {
      "id": "first_part", "label": "The first part starts to land",
      "at": 45, "deadline": 45, "predicate": "first_part",
      "args": {"parts": ["Podium_Body", "Podium_Lid"]},
      "say": "It is still building."
    }
  ]
}
```

- [ ] **Step 4: Write `table-safe.json`**

```json
{
  "name": "table-safe",
  "note": "The lid-only table run, chosen before the pitch when rehearsal started no part before 1:30. One small part one helper can start fast; the presenter says the body is the next run.",
  "brief": {
    "from": "phone",
    "board": "esp32-s3-devkitc-1",
    "marker": "Podium lid for the same Perch",
    "text": "Make the Podium lid for the same Perch: a 2.4 mm back cover for the upright ESP32-S3 head, same fits, same 180 mm bed. Recall the Perch we just built before planning, and use what its review found. Podium_Lid -> lid-creao."
  },
  "gate": {
    "first_op_by": 75,
    "if_missed": "No part has started yet. The recall and the task are real; the lid is the smallest job we have and it is still running."
  },
  "ready": {
    "parts_any": ["Clamp_Base", "Shoulder_Hub", "Arm_Proximal", "Arm_Distal", "Cradle_Base", "Cradle_Lid"],
    "forbid_prefix": ["Podium_", "Medallion"],
    "no_open_brief": true
  },
  "cast": {
    "lead-claude": "claude",
    "lid-creao": "creao",
    "arm-opencode": "opencode"
  },
  "probe": ["lead-claude", "lid-creao"],
  "steps": [
    {
      "id": "brief", "label": "Sam sends the lid-only brief from the phone",
      "at": 0, "deadline": 10, "predicate": "brief_sent",
      "args": {"marker": "Podium lid for the same Perch", "by_not": "presenter", "phone": true},
      "say": "Same phone, one small part."
    },
    {
      "id": "recall", "label": "The planner recalls the Perch before it plans",
      "at": 10, "deadline": 25, "predicate": "recall",
      "args": {"marker": "clip-on articulated arm", "query": "Make the Podium lid", "problems": 3},
      "say": "Before it plans, it looks up what we already built."
    },
    {
      "id": "tasks", "label": "The lid task appears with its owner",
      "at": 20, "deadline": 30, "predicate": "tasks_landed",
      "args": {"owners": {"Podium_Lid": "lid-creao"}},
      "say": "One part, one owner."
    },
    {
      "id": "first_part", "label": "The lid starts to land",
      "at": 35, "deadline": 55, "predicate": "first_part",
      "args": {"parts": ["Podium_Lid"]},
      "say": "It is still building."
    }
  ]
}
```

- [ ] **Step 5: Write `timeline.json`**

```json
{
  "name": "booth",
  "note": "The 40-50 minute Act 1 + Act 2 run. Every step waits on the module; the cues are for the presenter, not for the driver. The medallion and its 608 bearing are this run's, never the table's.",
  "brief": {
    "from": "panel",
    "board": "esp32-s3-devkitc-1",
    "marker": "clip-on articulated arm",
    "text": "Build The Perch: a clip-on articulated arm that holds an ESP32-S3-DevKitC-1 at eye level, with a cradle head. I'll edit the clamp myself. Plan: Clamp_Base (unassigned), Shoulder_Hub -> lead-claude, Arm_Proximal + Arm_Distal -> arm-opencode, Cradle_Base (unassigned), Cradle_Lid -> lid-creao. Look up the ESP32-S3 with lookup_board and use its measured Params. Two printable arm segments joined by M4 screws and heat-set inserts, a 608 bearing at the shoulder, a dovetail head mount. Both USB-C ports must be reachable. Every part must fit a 180 mm bed and follow our walls and fits. The distal arm is what we'll simulate and lighten."
  },
  "gate": {
    "first_op_by": 2400,
    "if_missed": "Forty minutes in, no part has started. Stop the build, say so, and show the Act 1 recording labelled as a replay."
  },
  "ready": {
    "parts_any": [],
    "forbid_prefix": ["Clamp_", "Shoulder_", "Arm_", "Cradle_", "Podium_", "Medallion"],
    "no_open_brief": true
  },
  "cast": {
    "planner-claude": "claude",
    "lead-claude": "claude",
    "base-claude": "claude",
    "lid-creao": "creao",
    "arm-opencode": "opencode"
  },
  "probe": ["lead-claude", "lid-creao", "arm-opencode"],
  "steps": [
    {
      "id": "brief", "label": "Act 1's brief is typed in the panel",
      "at": 0, "deadline": 300, "predicate": "brief_sent",
      "args": {"marker": "clip-on articulated arm"},
      "say": "The cold open: sponsor window first, then this brief."
    },
    {
      "id": "board", "label": "The planner looks the board up",
      "at": 60, "deadline": 300, "predicate": "op_done", "args": {"kind": "lookup_board"},
      "say": "Board Params fill from the library; the Elastic card shows the board."
    },
    {
      "id": "tasks", "label": "The plan's tasks appear with named owners",
      "at": 120, "deadline": 300, "predicate": "tasks_landed",
      "args": {"owners": {"Shoulder_Hub": "lead-claude", "Arm_Proximal": "arm-opencode",
                          "Arm_Distal": "arm-opencode", "Cradle_Lid": "lid-creao"}},
      "say": "Clamp_Base and Cradle_Base stay unassigned: a host worker takes them."
    },
    {
      "id": "protect", "label": "A request on the held distal arm is refused",
      "at": 240, "deadline": 240, "predicate": "refusal",
      "args": {"target": "Arm_Distal", "by": "lid-creao"},
      "guard": {"held_by": {"object": "Arm_Distal", "name": "arm-opencode"}},
      "on_start": {"steer": {"to": "lid-creao", "body": "thicken Arm_Distal by 2 mm; it flexes."}},
      "say": "The lock is the rule: the holder's part is not yours to change."
    },
    {
      "id": "build", "label": "All six Act 1 parts are in the model",
      "at": 360, "deadline": 1200, "predicate": "parts_landed",
      "args": {"parts": ["Clamp_Base", "Shoulder_Hub", "Arm_Proximal", "Arm_Distal",
                         "Cradle_Base", "Cradle_Lid"]},
      "say": "Build. The Creao trace is one call per turn; the Claude workers take the clamp and cradle."
    },
    {
      "id": "shared_size", "label": "arm_reach is steered to 160 mm",
      "at": 600, "deadline": 300, "predicate": "param_changed",
      "args": {"alias": "arm_reach", "value": 160},
      "say": "One shared size; every part bound to it recomputes and the owners are told."
    },
    {
      "id": "constrain", "label": "Auto-constrain is previewed and applied",
      "at": 900, "deadline": 600, "predicate": "confirm",
      "say": "Tools > Auto-constrain: alias, pattern and fit proposals; apply, then tol_press_fit = -0.08 mm."
    },
    {
      "id": "fea", "label": "The FEA report is published",
      "at": 1080, "deadline": 600, "predicate": "report", "args": {"kind": "fea_report"},
      "say": "4.9 N at the head, fixed at the elbow, PETG; the report pops for the whole team."
    },
    {
      "id": "takeover", "label": "The killed Creao helper is taken over",
      "at": 1260, "deadline": 300, "predicate": "takeover",
      "say": "Kill the bridge, wait for stale, then ask lead-claude to take the lid over."
    },
    {
      "id": "handoff", "label": "The clamp handoff is accepted",
      "at": 1500, "deadline": 400, "predicate": "handoff",
      "say": "Sam takes the clamp on the phone, sends the note, and leaves; the host's handoff agent accepts."
    },
    {
      "id": "review1", "label": "Round 1 comes back with findings",
      "at": 1680, "deadline": 400, "predicate": "review_round",
      "args": {"round": 1, "passed": false},
      "say": "Droid's pointer and trace; the three findings go to tasks."
    },
    {
      "id": "style", "label": "The lid wall is corrected and the rules are added",
      "at": 1860, "deadline": 300, "predicate": "confirm",
      "say": "2.4 mm wall by hand, then Learned design style: the three rules."
    },
    {
      "id": "review2", "label": "Round 2 is approved",
      "at": 2040, "deadline": 300, "predicate": "review_round",
      "args": {"round": 2, "passed": true},
      "say": "The Factory card says approved; that is the quality gate."
    },
    {
      "id": "lighten", "label": "The lattice report is published",
      "at": 2160, "deadline": 900, "predicate": "report", "args": {"kind": "lattice_report"},
      "say": "90% stiffness, gyroid, 5 mm cells, 2.4 mm solid skin, held at the elbow: the verifier console and mass saved."
    },
    {
      "id": "export", "label": "STEP and STL are exported",
      "at": 2400, "deadline": 300, "predicate": "op_done", "args": {"kind": "export"},
      "say": "Print-ready on a 180 mm bed; nothing is printed here."
    },
    {
      "id": "memory", "label": "The design doc and the lessons are in Kibana",
      "at": 2520, "deadline": 300, "predicate": "confirm",
      "say": "cadswarm-designs, the Workflow execution, and the lessons that Act 2 will recall."
    },
    {
      "id": "close", "label": "Act 1 closes on the sponsor window",
      "at": 2700, "deadline": 600, "predicate": "confirm",
      "say": "Leave the window up, restart Creao, and hand the phone to Sam for Act 2."
    },
    {
      "id": "act2_recall", "label": "Act 2's planner recalls both designs",
      "at": 2820, "deadline": 900, "predicate": "recall",
      "args": {"marker": "clip-on articulated arm", "query": "Make the Podium head", "problems": 3},
      "say": "From Sam's phone: the second head starts by recalling the first one, its problems and its lessons."
    }
  ]
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_perch_demo.py -v`
Expected: PASS — including the three parametrised timeline tests.

- [ ] **Step 7: Commit**

```bash
git add demo/perch/table.json demo/perch/table-safe.json demo/perch/timeline.json tests/test_perch_demo.py
git commit -m "feat(perch): the table, the safe fallback and the booth timelines"
```

---

### Task 6: The runbook documents

**Files:**
- Create: `demo/perch/README.md`
- Create: `demo/perch/prompts.md`
- Create: `demo/perch/checklist.md`

**Interfaces:**
- Consumes: the timelines' ids and `say` lines (Task 5), the driver's flags (Task 4), the five card names and facts (Tasks 1–2).
- Produces: the three documents rehearsal is run from. Task 7 adds `rehearsal.md` and quotes these names. No test file changes: these are documents, and the only automated check is Task 7's link check.

- [ ] **Step 1: Write `demo/perch/README.md`**

````markdown
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
| `you` | FreeCAD | builds, corrects by hand, runs Auto-constrain | the CadSwarm workbench's join/invite flow (registers `client = "freecad"`) |
| `Sam` | phone | types and sends the Act 2 brief | the host's phone invite / QR |
| `planner-claude` | claude | turns a brief into assigned tasks, then stops | host-spawned as `planner-<brief>` when the brief is submitted |
| `lead-claude` | claude | `Shoulder_Hub`, Params, coordination, takeover | manual Claude Code |
| `worker-1/2/3-claude` | claude | unassigned tasks: `Clamp_Base`, `Cradle_Base` | host-spawned worker labels |
| `arm-opencode` | opencode | arm segments, FEA, Lighten | manual OpenCode |
| `lid-creao` | creao | the lid; killed for the takeover; back for Act 2 | Creao bridge |
| `base-claude` | claude | finishes the clamp after the handoff | host-spawned at the handoff as `handoff-<id>` |
| `reviewer` | droid | review rounds 1 and 2 | host `init-reviewer` |

The host-spawned names are call-sheet labels in this runbook, not literal member names. The host names the
planner `planner-<brief>` when the brief is submitted, the workers `worker<n>-<brief>`, and the handoff
agent `handoff-<id>`. Those are the names the panel's Team tab shows. `lead-claude`, `arm-opencode` and
`lid-creao` are the manual helpers and keep the names above.

The three card claims the window makes about people are these members: `client = "claude"` is the
Anthropic card, `client = "creao"` is the Creao card, `kind = "reviewer"` is the Factory card. The
presenter's `client` is `presenter`, so it never inflates a partner's counts.

Every manual helper joins **before** the brief, so the planner can assign by name. Every manual task is
assigned by name so a free host worker cannot poach it.

## One-time setup on the host

Run from the repo root, with the venv already installed (`python -m venv .venv;
.venv\Scripts\pip install -e ".[test]"`).

```powershell
# 0. If module/ or rules/ changed since the last publish (the reviewer-lifetime fix is module code),
#    publish the module keeping data before starting. scripts\demo-up.ps1 does not publish it
#    (docs\publishing-spacetimedb.md).

# 1. The stack: builds the host and web app the first time, runs it against Maincloud, prints a phone
#    invite. After one successful build, add -SkipBuild to start it faster.
powershell -File scripts\demo-up.ps1

# 2. One human invite for the presenter (the host prints a cadswarm-2: string).
host\target\release\cadswarm-host.exe --data $env:USERPROFILE\Documents\Projects\cadswarm-demo invite --kind human --role lead

# 3. The presenter folder: joins once, then reuses its identity on every later run.
.venv\Scripts\python demo\perch\driver.py --project demo\perch\presenter --invite "cadswarm-2:..."

# 4. The manual CLI helpers, each in its own folder (invites come from the host).
.venv\Scripts\cadswarm setup --client claude   --invite "cadswarm-2:..." --name lead-claude  --dir demo\perch\lead-claude
.venv\Scripts\cadswarm setup --client opencode --invite "cadswarm-2:..." --name arm-opencode --dir demo\perch\arm-opencode
.venv\Scripts\cadswarm setup --client creao    --invite "cadswarm-2:..." --name lid-creao    --dir demo\perch\lid-creao

# 5. The reviewer member and its folder. Run once per workspace: after this the host starts the review
#    rounds by itself. The member is HTTP-only, so it reads `stale` between rounds and stays valid.
host\target\release\cadswarm-host.exe --data $env:USERPROFILE\Documents\Projects\cadswarm-demo init-reviewer
```

`you` (FreeCAD) does not use `setup`: open the CadSwarm workbench in FreeCAD and use its join/invite flow;
the panel registers `client = "freecad"`. `cadswarm setup --client` takes `claude`, `codex`, `droid`,
`opencode` or `creao`; the three helpers above are `claude`, `opencode` and `creao`.

`demo/perch/presenter/.cadswarm/` holds the presenter's SpacetimeDB token; `.cadswarm/` is already
ignored. The driver's `--project` points at that folder, **not** at the repo root.

Keys come from `cadswarm.env` through `scripts\with-env.ps1` (Elastic, Factory, Creao). The driver never
reads a key: its probe asks Elastic through `cadswarm.knowledge` exactly as the host does, and only
checks that `droid` is on `PATH`.

## Before every pitch

Read `checklist.md` top to bottom. It is ordered: stack, helpers, Elastic tabs, `act-1-ready`, probe,
then the replay window. The driver refuses to Start unless the probe passes and the team is at the
timeline's ready state: `act-1-ready` for the table, the empty model below for the booth.

## Act 1: "The Perch" (booth, 40–50 minutes)

Brief, typed in the panel (exact text in `prompts.md`): the clip-on arm, the cradle head, the board
lookup, the owners, the 180 mm bed, "the distal arm is what we'll simulate and lighten".

| # | Beat | Time | Action | What the room sees |
|---|---|---|---|---|
| 1 | Cold open | 1′ | Sponsor window on the second screen | Booth: the five cards are in place and reading live state (the manual helpers joined at T-30; the reviewer reads `stale` between rounds), and the board is bare; Act 1 starts now. At the table the helpers are also joined by T-30 |
| 2 | Plan + board | 2′ | Submit the brief | `lookup_board`, board Params, tasks with names |
| 3 | Build | 8–12′ | Let them work | Parts in owner colours; Creao one call per turn |
| 4 | Protection | 1′ | `@lid-creao thicken Arm_Distal by 2 mm; it flexes.` | Refused; it messages the owner |
| 5 | Shared size | 1′ | Tools ▸ Change a shared size… `arm_reach = 160 mm` | Segments and head positions recompute |
| 6 | Auto-constrain | 2′ | Owners release; Tools ▸ Auto-constrain… → apply; `tol_press_fit = -0.08 mm` | Aliases, M4 pattern, bearing fit; the bore shrinks |
| 7 | FEA | 3–6′ | `@arm-opencode linear static …` | Report pops up for the team; Elastic indexes it |
| 8 | Kill + takeover | 2′ | Kill the Creao bridge; `@lead-claude take_over("lid-creao")` | Creao goes stale, its trace stops, the lock moves |
| 9 | Handoff | 2–3′ | You hold `Clamp_Base`, edit the M8 pocket, and let go; Sam takes the lock in the phone's Model tab, sends the handoff note in Team chat, arms **Continue my work if I leave**, and closes the tab | Sam goes quiet; the handoff opens; the host's handoff agent accepts, finishes, and messages back |
| 10 | Review 1 | 2–4′ | Automatic when tasks are done | Droid pointer, trace, three findings |
| 11 | Correct + style | 2–3′ | Set the lid wall to 2.4 mm; add the three rules in Learned design style | The correction is recorded; rules are active |
| 12 | Review 2 | 1–2′ | Fix tasks, automatic re-review | Factory card: round 2 · approved |
| 13 | Snapshot + Lighten | 5–10′ | Snapshot `act-1-ready`; `@arm-opencode lighten Arm_Distal to 90% stiffness, gyroid, 5 mm cells, 2.4 mm solid skin` | Verifier console, mass saved, cut-open preview |
| 14 | Export + memory | 1–2′ | Export STEP + STL; run `distil-lessons`; open Kibana | Design doc + 3 lessons + Workflow execution |
| 15 | Sponsor recap | 1′ | Leave the window up; restart Creao for Act 2 | The same five cards, each carrying the work this run gave it |

Set the snapshot name `act-1-ready` exactly: `reset_ok()` and every table run depend on the six parts
being present and no `Podium_`/`Medallion` object being left behind.

The booth starts from a blank model: `timeline.json` has `parts_any: []`, no Act 1 parts, because Act 1 is
built live. The T-15 state section of `checklist.md` is the table pre-flight only; the booth state is an
empty model, no `Clamp_`/`Shoulder_`/`Arm_`/`Cradle_`/`Podium_`/`Medallion`, no open brief, and the manual
helpers joined.

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

- **Start** — refuses unless Probe passed and the team is at the timeline's ready state (`act-1-ready`
  for the table, the empty model for the booth), with no task still `open`, `claimed` or `blocked`;
  opens the sequence and shows the brief. At the table Sam sends it from the phone; at the booth you
  type it in the panel.
- **Next** — advance now. A manual step is logged `confirmed`; a module step `skipped`. Both say why.
- **Pause / Resume** — holds the sequence while a judge talks. The build keeps running.
- **Probe** — Claude, Creao, Droid, Elastic, then every other owner in the timeline's `probe` list.
  The booth's planner joins when the brief is submitted (`planner-<brief>`), and the handoff agent
  (`handoff-<id>`, called `base-claude` here) joins at the handoff, twenty-five minutes in, so neither is
  probed at Start. Re-run Probe after touching any helper.
- **Say box** — `@lid-creao …` is a real steer; anything else is a real brief. This is how a judge's own
  idea runs live.

Every step is done when the module says so — a task row, an op, a lock, a review, a bbox, an artifact,
an event, or an activity row (the host's own recall and a refused submit make no op). Never when an
agent says it is. Every run appends to `runs/<stamp>-<timeline>.jsonl` and writes
`runs/<stamp>-<timeline>.json` with `t_recall`, `t_tasks`, `t_first_op`, `t_finish`; those summary
numbers, not the targets, are what the pitch quotes.

## What the table claims

At the table the `table.json` run claims four things: the recall returned Act 1 with its three problems,
the tasks appeared with the same named owners, the held body refused Creao, and a real part started to
build. The safe run (`table-safe.json`) claims three of those: the recall, the tasks, and a real part
started to build; it has no refusal beat. Never a finished head. The absolute gate is **the first op by
1:45 on the pitch clock — 75 s after the 0:30 press**. The presenter says the fallback line if it is
missed; the page shows it in `Runner.status`.

The build continues after 2:00 and through Q&A. The driver never stops the team.

## If something breaks

| Symptom | Do this |
|---|---|
| Probe fails on Claude | check that helper's folder is running; re-run Probe. If it stays down, use the labelled replay: a timeline will not load with a `probe` name removed from `cast`, and dropping the name from both leaves its tasks unowned. Never fake its card |
| Probe fails on Creao | check the Creao bridge; re-run Probe. The safe run's only task is the lid, owned by `lid-creao`, so taking Creao out of `cast` would leave it unowned: recover with the labelled replay, not a cast edit |
| Probe fails on Droid | run `droid` once on the host (sign-in), or let the host's reviewer autostart path read `cadswarm.env`; then run `cadswarm-host … init-reviewer` if the Probe still says `no reviewer has joined` (signing in does not create the member). If `init-reviewer` prints `already set up` (the reviewer folder holds an identity), check two things: the folder's `.cadswarm/config.json` `db` matches this run's `host.json` `db` (an identity from another database prints the same thing and joins nothing), and `v_member` holds no retired row (the `left`/`taken_over` row) |
| The reviewer shows `stale` | expected: `init-reviewer` mints an HTTP-only member that never heartbeats. The sweep never orphans a reviewer, so it stays on the card between rounds. Probe passes it (with `droid` on PATH) and a stale reviewer does not block Start. A stale *helper* does |
| The Factory card shows a reviewer that is `left` or `taken_over` | the web card reads every `v_member` row and picks the first reviewer, while FreeCAD and Probe read `participants()`, which hides `left`/`taken_over`. With no live reviewer, Probe says `no reviewer has joined` and Start is refused, so this is not cosmetic. Deleting the reviewer folder and re-running `init-reviewer` joins a second row (a `left` namesake is not inherited), and the web card may still show the old one. Check `v_member` **before** a round: an `active` reviewer mid-round is normal. To clear it, start a new database (`-Db`), publish the module into it, reset the presenter and reviewer folders, and re-invite the whole cast; if there is no time for that, delete the reviewer folder and re-run `init-reviewer` so Probe passes, and say the web card may still name the retired row |
| Probe fails on Elastic | check `ELASTIC_URL`, `KIBANA_URL`, `ELASTIC_API_KEY` in `cadswarm.env`; the recall needs them |
| Nothing started by 1:45 (75 s after the press) | the presenter says the fallback line and the page shows it in `Runner.status`; point at the recall and the tasks that did land, replay the booth recording **labelled as a replay**, and say which is live |
| The phone tunnel died | restart the driver with `--laptop`; same brief, same sequence, the member is the laptop |
| A step hangs | Next; a `confirm` step is logged `confirmed`, a module step `skipped`, and the sequence moves on |
| The recall or refusal step never closes while the build moves | restart the host, every helper and the driver (no module publish): the helper logs one `recall` row per returned design (rank, request, its own problems) and the hub client logs a refused submit as `refused`, both read from `v_activity`. The panel's Activity tab shows the same rows. If the recall rows are missing, check Elastic (the Probe's Elastic line) and that `<data-dir>/host.json` holds this run's `uri`/`db`/token — a host that never joined leaves no row |
| The page is closed | reopen the URL; the run and the log are unaffected |
| Creao stalls mid-Act 1 | kill it earlier: that is the takeover beat; restart it for Act 2 |
| A host worker claims a manual part | cancel that task and re-assign by name; the module only rebalances busy or stale assignees |
| Review passes round 1 | steer a legitimate tight-fit change (thin the podium lid to 1.6 mm so the OLED bezel fits) and wait for the style finding — never invent one |

## Reset between table judges

The operative order is in `checklist.md`; this is the same order. It is for the table only.

1. Restore the `act-1-ready` snapshot; delete `Podium_*` and `Medallion*`; hide the lattice mesh; move the
   cradle back; delete or cancel Act 2's tasks and the open brief.
2. Leave Act 1's design doc and lessons in Elastic. Leave the previous podium document too: if it ranks
   first for the next recall, say so out loud — the system is returning our own earlier run.
3. Restart Creao and any helper you killed; wait for `active`.
4. Restart `driver.py`: the runner refuses a second Start and the page disables Start after a run.
5. Re-run Probe. Confirm the page says `ready — press Start`, Probe is all ✓, and the first step is
   `pending`.

At the booth there is no table reset: `timeline.json` starts from an empty model and refuses Start once
Act 1 parts exist. A second booth run needs a new database (`-Db`, with the module published into it), the
presenter and reviewer folders reset (their stored identities point at the old database), and fresh invites
for the whole cast, not a restore of `act-1-ready`.

If the table reset cannot finish before the next judge, do not fake it: start from the running Act 2, say
the recall already happened, and let the panel and `rehearsal.md` carry Act 1.
````

- [ ] **Step 2: Write `demo/perch/prompts.md`**

````markdown
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
| 13 | `lighten Arm_Distal to 90% stiffness, gyroid, 5 mm cells, 2.4 mm solid skin, held at the elbow, loaded at the head.` | `@arm-opencode` |
| 14 | `distil-lessons` | (Elastic workflow) |

Handoff note, sent in Team chat before arming. The handoff's `note` field is empty here; the agent gets this
text through the snapshot's `recent_messages`, which keeps Sam's last five messages, 300 characters each, so
send it as one short message.

```
Jaw opening is 34 mm; the M8 nut pocket is 0.2 mm tight. Finish the insert pocket, add 0.4 mm clearance,
keep 2.4 mm walls. I've left the lock on the clamp for you.
```

## If the booth gate is missed (40 minutes in)

The `timeline.json` line, word for word: "Forty minutes in, no part has started. Stop the build, say so,
and show the Act 1 recording labelled as a replay."

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
````

- [ ] **Step 3: Write `demo/perch/checklist.md`**

````markdown
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
````

- [ ] **Step 4: Check the documents name only things that exist**

Run:

```powershell
Select-String -Path demo\perch\README.md,demo\perch\prompts.md,demo\perch\checklist.md -Pattern "table-safe.json|table.json|timeline.json|driver.py|rehearsal.md|runs/"
```

Expected: every hit is one of the files Task 5 and Task 7 create, and no document refers to `pitch.md`,
`table-card.html` or `testimonials/` except in the "deferred" list.

- [ ] **Step 5: Commit**

```bash
git add demo/perch/README.md demo/perch/prompts.md demo/perch/checklist.md
git commit -m "docs(perch): the runbook, the copy-paste prompts and the checklist"
```

---

### Task 7: Rehearse, decide the timeline, verify

**Files:**
- Create: `demo/perch/rehearsal.md`
- Modify: `demo/perch/rehearsal.md` (after each rehearsal: the measured numbers and the decision)

**Interfaces:**
- Consumes: the driver and the three timelines (Tasks 3–5), the run summaries in `demo/perch/runs/` (keys `t_recall`, `t_tasks`, `t_first_op`, `t_finish`, `gate_failed`, `steps`), the documents from Task 6.
- Produces: the table run's chosen timeline, recorded before the first judge, and the numbers the pitch is allowed to quote.

**No number in this task is invented.** Every cell is either blank until measured, or copied from a
`runs/*.json` summary. A rehearsal that did not run leaves the cell blank and says why.

- [ ] **Step 1: Write the empty sheet**

Create `demo/perch/rehearsal.md`:

````markdown
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

| Run | Date | Timeline | Act 1 wall clock | Act 2 wall clock | Delta | Same owners? | Act 1's rank |
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
````

The sheet is written blank, and stays blank until a rehearsal runs. R1-R3 need the live stack (the
SpacetimeDB host, the helpers, Droid, Elastic and the phone), so they cannot be filled from a machine
that only has the repo. An unfilled row says so in its Notes column; the table decision is not made
until R2 and R3 exist, and until then the pitch quotes no timing number.

- [ ] **Step 2: Rehearse Act 1 (R1)**

Bring the stack up exactly as `README.md` says, with the booth timeline:

```powershell
.venv\Scripts\python demo\perch\driver.py --project demo\perch\presenter --timeline timeline.json
```

Press Start, submit the Act 1 brief in the panel, and run beats 1–15. Start a stopwatch at beat 2 and stop
it at beat 14. Record in R1: wall clock, the human minutes, the three findings **as filed**, and the four
engineering readings from the reports. Do not fill a cell you did not observe.

Expected: all six parts, both reviews, the `fea_report`, the `lattice_report`, the export, and
`runs/<stamp>-booth.json` with every step `done` or `confirmed`. A `skipped` step is a finding for the
"What went wrong" table, not a failure of the rehearsal.

- [ ] **Step 3: Rehearse the table run (R2)**

Reset to `act-1-ready` (checklist, the 12-minute list), then:

```powershell
.venv\Scripts\python demo\perch\driver.py --project demo\perch\presenter
```

Press Start when the stopwatch starts, have Sam send the brief from the phone, and follow the cue sheet.
Record R2's four numbers from `runs/<stamp>-table.json`, plus the gate and the skipped steps. Time the
reset afterwards and record it.

- [ ] **Step 4: Rehearse the safe run (R3)**

Reset again, then:

```powershell
.venv\Scripts\python demo\perch\driver.py --project demo\perch\presenter --timeline table-safe.json
```

Same recording. R3 exists so the fallback is measured, not hoped for; if `table-safe.json` is never slower
than `table.json`, say that in the sheet — it is a reason to prefer it.

- [ ] **Step 5: Decide, in writing**

Apply the rule from Step 1's first section using R2's and R3's `t_first_op`:

- `t_first_op` **under 60 s** and the gate passed → `table.json`.
- `t_first_op` **60 s or more**, or the gate failed in either run → `table-safe.json`.

Fill the decision block and commit it before the first judge. If a later rehearsal changes the numbers,
the decision is re-made **before** the next pitch and the previous line is kept, struck through by hand in
the printed copy — never silently edited.

- [ ] **Step 6: Run the whole suite**

```powershell
python -m pytest tests/test_perch_demo.py tests/test_sponsor_report.py -v
cd web; npm test -- --run; npm run build
```

Expected: the Perch and sponsor tests PASS; the web suite PASS and the build exits 0. Record in the sheet
whether `tests/test_panel_gui.py` ran or skipped (`CADSWARM_TEST_PANEL_GUI=1` needs FreeCAD on the host).

- [ ] **Step 7: Verify the three timelines against the documents**

```powershell
@'
import json, pathlib
here = pathlib.Path("demo/perch")
doc = " ".join((here / "prompts.md").read_text(encoding="utf-8").split())
for name in ("table.json", "table-safe.json", "timeline.json"):
    plan = json.loads((here / name).read_text(encoding="utf-8"))
    text = " ".join(plan["brief"]["text"].split())
    assert text in doc, f"{name}: its brief is not in prompts.md verbatim"
    ids = [s["id"] for s in plan["steps"]]
    assert len(ids) == len(set(ids)), f"{name}: duplicate step id"
print("briefs match prompts.md; step ids unique")
'@ | python -
```

Expected: `briefs match prompts.md; step ids unique`. The check normalises whitespace, so a brief may be
wrapped in the markdown; its words and their order must match. If a brief fails, copy the JSON's `text`
over the markdown — the timeline is the source of truth, not the prose.

- [ ] **Step 8: Commit**

```bash
git add demo/perch/rehearsal.md
git commit -m "docs(perch): the rehearsal sheet, the measured numbers and the timeline decision"
```

---

## Done criteria

The plan is finished when all of these are true, checked in this order:

1. `python -m pytest tests/test_perch_demo.py tests/test_sponsor_report.py -v` passes; `cd web; npm test -- --run` passes; `cd web; npm run build` exits 0.
2. The web panel and the FreeCAD Sponsors tab both show five cards in the order SpacetimeDB → Anthropic → Creao → Factory.ai → Elastic, with `not joined` rather than a missing card.
3. `demo/perch/driver.py --help` exits 0, and a driver started against the live team shows `ready — press Start` with a passing probe and `act-1-ready` detected.
4. `Start` refuses (HTTP 409 with the reason) when the probe fails or the team is not at `act-1-ready`.
5. Each of `table.json`, `table-safe.json`, `timeline.json` validates, its brief is verbatim in `prompts.md`, and every owner it names is in its `cast`.
6. **Code-complete:** `rehearsal.md` exists with R1, R2 and R3 explicitly unrun and blank, and says why.
   **Rehearsed (a separate gate, on the demo machine):** R1, R2 and R3 are filled from run summaries and
   the table decision is written before the first judge. This plan is code-complete now; it is not
   rehearsed, and no timing number may be quoted until it is.
7. `git status` is clean on `feat/perch-showcase`, and no file outside `web/`, `cadswarm/gui/`, `cadswarm/helper.py`, `cadswarm/space_hub.py`, `tests/`, `demo/perch/`, `docs/superpowers/`, `.gitignore`, and the approved `rules/src/lib.rs`/`module/src/sweep.rs` changed.

Nothing in this plan prints a part, claims a finished head at the table, or changes the schema, the host
orchestration, the phone app or the FreeCAD panel's layout. The two code changes outside the cards are
the reviewer-lifetime fix in `rules`/`module` and the activity rows in `cadswarm/helper.py` and
`cadswarm/space_hub.py` (the only place the host's own recall and a refused submit become visible).
