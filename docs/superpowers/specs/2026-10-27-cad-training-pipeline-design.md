# CAD Training Pipeline

**Status:** design, awaiting review
**Branch:** `feat/cad-train` (worktree `997e5f/cad-train`, based on `main` @ `1b79671`)
**Date:** 2026-10-27

---

## 1. Goal

Turn "how we build things in CadSwarm" into a reproducible dataset, and from it train two
models:

- **a builder policy** — given a brief, its plan, and the current state of the model, emit the
  next mutating op (`create_extrusion`, `boolean`, `set_param`, …);
- **a critic** — score a candidate build (or a single step) using human corrections, review
  findings and feedback verdicts.

The pipeline is the deliverable of this spec. The models are the point of it.

## 2. What the data actually is

Measured on this machine, not assumed:

| Source | Ops | Tasks | Briefs | Reviews |
|---|---|---|---|---|
| `Lexus IS350` (local SpacetimeDB, live) | 606 | 18 | 2 | 0 |
| 8 other projects (maincloud) | unreachable — `database is paused` | | | |
| `style_events` (Lexus `hub.sqlite`) | 247 rows, 59 distilled rules | | | |

Every geometry action, human or AI, is already an `op` row with `args_json`, `result_json`
(`{result, created, modified, deleted, recomputed, broken, revs, seq, notified}`), `by`,
`created_ms`, `status` (`queued → running → done | failed | rejected`) and `error_code`.
Ops are never pruned. `team_event` is the ordered cross-actor stream; `cad_object` and `param`
hold the *current* state; `task`, `brief`, `review` hold intent and verdicts; `style_events`
holds per-op context, human corrections with before/after values, and (on
`feat/design-feedback`) feedback verdicts.

Three structural gaps, each addressed in §7:

1. `op` has no `task`/`brief` column, so a step cannot be attributed to a task from the record
   alone.
2. Nothing records the agent turn that produced an op — only unstructured CLI text in
   `helper.log` / `agent.log`.
3. Only the latest `model.FCStd` exists; there is no per-step geometry.

**This is a few hundred ops, not a corpus.** The pipeline's first job is to not waste the
future ones. §13 sets the volume gates at which each model is worth training.

## 3. Non-goals

- Serving the model inside CadSwarm (adapter design, provider registration, latency). Separate
  spec; this one ends at a trained artifact plus an eval scorecard.
- Training from scratch, pretraining, or anything needing more than one accelerator-day.
- Vision. No renders exist (the branch's `render_views` is unimplemented), so the builder is
  text+state only.
- Rewriting the app's agent prompts. The trainer has its own canonical prompt (§6.6).
- Touching `main`'s uncommitted auto-update work. All changes land on `feat/cad-train`.

## 4. Decisions

| Decision | Choice | Why |
|---|---|---|
| Data strategy | Harvest back-catalogue **and** instrument new sessions | Harvest is cheap and gives the trainer real bytes today; instrumentation is what makes the corpus trainable |
| Builder format | Step-level op policy (not one-shot program synthesis) | It is already the app's action space, needs no reconstruction, reuses the live executor for eval, and preserves iteration/correction that a single script flattens |
| Program view | Deferred, derived later | Doubles the surface for no extra signal at this volume |
| Labels | Episode outcome, per-step label, corrections, reviews, feedback | Same records feed both the builder filter and the critic's pairs |
| State representation | Symbolic replay of the op log | The only way to recover per-step state from history; validated against the live `cad_object` table |
| Train split | By **project**, never by step | One `model.FCStd` per project means step-level splits leak |
| Dataset storage | `cadswarm-data/datasets/` (already git-ignored) | Never commit data; commit schema, fixtures, manifests |
| Packaging | `cadswarm/training/` excluded from the FreeCAD addon | Training deps (torch) must never ship to users |
| Trainer backend | QLoRA SFT on an open coder model, configurable base | The default must fit 16 GB VRAM; the base is a config string, not a fork |

## 5. Architecture

```
  SpacetimeDB (op, task, brief, review, member, cad_object, param, team_event)
  hub.sqlite (style_events, style_rules)      ──┐
  knowledge.db (design docs)                    ├──► export.py ──► raw/<stamp>/*.jsonl + source.json
  project folder (model.FCStd, snapshots)     ──┘                          │
                                                                           ▼
                                                    episodes.py + state.py  (pure, offline)
                                                                           │
                                                     episode.json (one build = one file)
                                                                           │
                                          ┌────────────────────────────────┼─────────────────────┐
                                          ▼                                ▼                     ▼
                                   labels.py                        split.py              conformance report
                                          │                                │
                                          ▼                                ▼
                                   render.py ──► sft.jsonl        eval/cases.jsonl
                                            ──► pairs.jsonl  ──► train.py ──► adapter/
                                            ──► eval/cases.jsonl
                                                                           │
                                                                           ▼
                                                              eval_replay.py (+ FreeCAD sandbox)
                                                                           │
                                                                           ▼
                                                                     report.json + dataset card
```

**Unit boundaries.** Each unit has one job, a typed input and output, and no knowledge of the
others' internals:

| Unit | In | Out | Depends on |
|---|---|---|---|
| `source.py` | `host.json` or a project folder | paged rows + a dataset path | `space.SpaceClient` (reused, not reimplemented) |
| `export.py` | a project folder or DB | `raw/<stamp>/` + `source.json` | `dbclient`, sqlite, filesystem |
| `state.py` | ordered ops | symbolic object table after each op | nothing |
| `episodes.py` | raw export | `episode.json` per build | `state` |
| `labels.py` | episodes | labels + critic pairs | `state` |
| `split.py` | episodes | train/val/test assignment | nothing |
| `prompts.py` | nothing | the canonical system prompt + state renderer | nothing |
| `render.py` | episodes + split | `sft.jsonl`, `pairs.jsonl`, `cases.jsonl` | `prompts` |
| `eval_replay.py` | cases + a model or a policy | scorecard | worker sandbox |
| `train.py` | rendered files | adapter + run record | torch/peft, `train` extra |
| `critic.py` | state + candidate | score | heuristic or adapter |

Only `eval_replay.py` and `train.py` have heavy/external dependencies, and both import them
lazily. Nothing in the package imports `FreeCAD` at module import time.

## 6. Data model

### 6.1 Raw export

`python -m cadswarm.training export --project "<dir>" [--project …] [--all] --out cadswarm-data/datasets`

Reads `host.json` for `uri`/`db`/`token`, then `select * from <table>` over the documented
`/v1/database/<db>/sql` endpoint. There is no paging: SpacetimeDB rejects `ORDER BY`
(`Unsupported: SELECT * FROM op ORDER BY id`, measured), so a key-ranged page would be an
arbitrary subset and `where key > last` would silently skip rows. Each table comes back in one
body and is sorted locally by its primary key. Tables: `member, cad_object, param, op, task,
brief, review, team_event`. Plus from the project folder: `style_events`, `style_rules`
(hub.sqlite) and `knowledge.db` design docs, and a listing of `exports/`, `snapshots/`, `files/`.

Writes `raw/<project>/<table>.jsonl` and `source.json`:

```json
{"project":"Lexus IS350","mode":"local","db":"cadswarm-lexus-is350-40de","uri":"http://127.0.0.1:3000",
 "tables":{"op":606,"task":18},"status":"ok","schema_sha256":"…"}
```

A source that is paused, unreachable, or missing gets `status: "skipped: <reason>"` and does
not abort the run. The export is idempotent: re-running over unchanged DBs produces
byte-identical files, because rows are sorted and no wall clock enters the content. The export
time lives in the run log, not in `source.json`.

### 6.2 Episode

One build = one `episode.json`. An episode is built from a `brief` when one exists, otherwise
from a contiguous stretch of one actor's work.

```jsonc
{
  "schema_version": 1,
  "id": "lexus-is350/brief/<brief_id>",
  "project": "Lexus IS350",
  "attribution": "task_linked | object_window | freestyle",
  "request": {"text": "…", "board": "", "plan_json": {"parts": […], "sources": {}}},
  "segments": [{"task_id":"…","title":"…","detail":"…","objects":["BodyShell"],
                "assignee_kind":"agent","status":"done","result":{…}}],
  "steps": [{
    "index": 12, "team_seq": 4711, "op_id": "op_…", "at_ms": 0,
    "actor": {"id": 7, "name": "lucky", "kind": "agent"},
    "op": "create_extrusion",
    "args": {"name":"Hood","points":[[0,0],…],"height":3},
    "result": {"created":["Hood"],"modified":[],"deleted":[],"recomputed":["BodyShell"],"broken":[]},
    "status": "done", "error_code": "",
    "state_before": {"objects": […], "params": {…}},   // §6.3
    "task_id": null, "brief": "…",
    "state_confidence": "full | degraded",
    "render_ok": true
  }],
  "corrections": [{"after_index":14,"subject":"Hood.height","before":"3","after":"2.5",
                   "actor_kind":"human","evidence":"style_events#318"}],
  "review": {"round":1,"passed":false,"findings":[{"object":"Roof","problem":"…"}]},
  "feedback": [{"verdict":"bad","subject":"BodyShell","issues":["flat panels"],"images":[]}],
  "final": {"stl_sha256":"…","objects":{…},"params":{…},"surface_quality":null},
  "labels": {"outcome":"accepted | blocked | failed | abandoned",
             "quality":"reviewed_pass | reviewed_fail | human_corrected | unreviewed",
             "step_labels":{"12":"good | neutral | bad | corrected"}}
}
```

**Segmentation rules** (deterministic, in this order):

1. `op.task` present (post-§7) → that task's episode. `attribution: "task_linked"`.
2. Otherwise an op belongs to the task whose `objects[]` it touches (per `created/modified/
   deleted/recomputed`), inside that task's lifetime `[created_ms, updated_ms]`. If several
   tasks qualify, the one with the fewest objects wins, then the earliest. `attribution:
   "object_window"`.
3. Ops touching no task's objects form a **freestyle** episode per actor, split on an idle
   gap > 15 min. `attribution: "freestyle"`.

Ops are ordered by `(created_ms, id)`. An op is never duplicated into two episodes; on a tie,
the earliest task by `created_ms` takes it.

### 6.3 Symbolic state

`state.py` replays ops forward from an empty document, maintaining:

```json
{"objects":{"BodyShell":{"type":"Part::Feature","bbox":[…],"volume":1234.5,"rev":3,
                         "known":true}},
 "params":{"Wheelbase":{"value":"2790","num":2790.0}}}
```

Object fields come from the op result when the worker reported them and from the later
`cad_object` row when it did not. Values that were never recorded stay `"known": false` with
`null` — nothing is fabricated.

Where the record is insufficient (an op names an object that was never created in this
timeline, or a result payload was spilled and swept), the step is marked
`state_confidence: "degraded"` and the episode records a `warnings[]` entry. Degraded steps are
excluded from SFT (`render_ok: false`) but kept for the critic.

**Conformance check.** After replaying every op in a project, the symbolic table must match the
live `cad_object` table: same name set, same `type_id`, volume within 1%, bbox within 1 mm per
axis. The export writes `conformance.json` with the match fraction and every mismatch. This is
the pipeline's correctness oracle, and it runs on real data in the live check below:

```
python -m cadswarm.training build --project "…/Lexus IS350" --conformance
```

### 6.4 Labels

| Signal | Source | Used for |
|---|---|---|
| `outcome` | `brief.status` (`ready`→accepted, `failed`→failed, cancelled/no export→abandoned); tasks `blocked`→blocked | episode filter |
| `quality` | `review.passed`, `style_events.kind='correction'`, branch `feedback` verdicts | builder filter, critic |
| `step_label=bad` | `status != done`, non-empty `result.broken`, `op_failed` | critic negatives, excluded from SFT |
| `step_label=corrected` | a human op on the same subject within 60 s of an agent op (existing `style_memory` correction rule, reused) | critic pairs |
| `step_label=good` | `done`, no later correction, in a non-failed episode | SFT positives |

Human steps are included and tagged `actor.kind`, so training can weight or filter them.
Human steps matter: they are the corrections the critic learns from.

### 6.5 Artifacts

```
cadswarm-data/
  raw/<project>/<table>.jsonl      §6.1 export cache, reusable across builds
  raw/<project>/source.json
  datasets/<dataset-id>/
    episodes/<id>.json             §6.2
    conformance.json
    split.json                     {train:[…], val:[…], test:[…], mode}
    sft.jsonl                      builder examples
    pairs.jsonl                    critic pairs
    eval/cases.jsonl               held-out briefs
    dataset-card.md                generated: counts, provenance, biases
    manifest.json                  {dataset_id, git_commit, projects, counts, hashes, split}
```

`raw/` sits beside the datasets rather than inside one, so a re-export is reused by every build
and the manifest records the source and schema hashes it read. `dataset_id` is the first 12 hex of
the sha256 over the sorted per-file hashes, so the same inputs always name the same dataset and
every training run can record which one it used. Data never enters git; the tests build their
records inline, so no fixture files are checked in.

### 6.6 Renderings

**Builder (`sft.jsonl`).** One row per `good`/`neutral` step:

```jsonc
{"messages":[
  {"role":"system","content":"<canonical op contract, §prompts>"},
  {"role":"user","content":"<brief>\n<plan>\n<state summary>\n<last 8 ops>"},
  {"role":"assistant","content":"{\"op\":\"create_extrusion\",\"args\":{…}}"}],
 "meta":{"episode":"…","index":12,"actor_kind":"agent","dataset":"…"}}
```

The assistant message is plain JSON, not a provider-specific tool-call object, so the dataset
is backend-agnostic and parseable by anything. `prompts.py` is the single source of the system
prompt; captured rows record `prompt_origin: "captured"`, back-catalogue rows
`"synthesized"`. Both use the same template, so the model never sees two contracts.

State summary is capped at 120 objects, 40 params, 8 previous ops, 2000 chars per free-text
field, and is rendered by one function so training and inference agree.

**Critic (`pairs.jsonl`).**

```jsonc
{"chosen":{"op":"set_props","args":{"name":"Hood","props":{"height":2.5}}},
 "rejected":{"op":"set_props","args":{"name":"Hood","props":{"height":3}}},
 "state":{…},"episode":"…","signal":"correction"}
```

Two pair sources: a param correction (the agent's value vs the person's, `signal: "correction"`)
and an accepted episode's final state vs a failed episode's (`signal: "outcome"`).

Only a **param** correction records both sides (before and after value), so it is the only true
step-level pair the back-catalogue proves. A property correction records what the person set but
not what the agent had set; those become `corrected` labels and extra SFT positives, not pairs.

**Eval cases (`cases.jsonl`).** One per held-out brief: `request`, initial `state` (empty for
most), reference `final` summary, `stl_sha256` for the reference export, and the reference op
sequence (used for the ceiling baseline). Cases never carry machine paths; anything binary is
resolved from the dataset by hash.

### 6.7 Split

Split by project. With the current nine, one project is test and one validation, the rest
train; `split.json` records the assignment. A project with fewer than 20 steps goes to train.
If fewer than three projects are reachable, fall back to a temporal split inside each project
and mark `"mode":"temporal"` in `split.json` and the dataset card — the eval scorecard then
carries a leakage warning. Never split steps from the same project across sets.

## 7. Capture instrumentation

This is the only part that changes the app. It lands last, after the offline pipeline is
proven, and is gated on a migration rehearsal.

**7.1 Attribution.** Add to `Op` in `module/src/tables.rs`:

```rust
pub task: String,    // task id, "" when none
pub brief: String,   // brief id, "" when none
```

Both default to `""`, so the migration is additive and the 606 existing Lexus ops keep their
values. `submit_op` gains the fields in its signature; `space_hub.submit_op` supplies them from
the caller's current claim (`runner`/`mcp_server` know the claimed task; the host knows the
brief). Human panel ops send the task the person is working on, or `""`.

**7.2 Turns.** New table `agent_turn`:

```rust
pub struct AgentTurn { pub id: String, pub ws: u64, pub member: u64, pub task: String,
  pub role: String, pub text: String, pub tool_json: String, pub at_ms: u64 }
```

Written by `runner.py` / `mcp_server.py` from the CLI transcript: the user prompt, the assistant
text, and the tool call each turn. Captured only for helpers this machine runs (the transcript
is local). `CADSWARM_CAPTURE_TURNS=0` disables it; `redact.py` strips absolute paths, the home
directory, invite tokens and e-mail addresses before insert.

**7.3 Per-step geometry.** The worker writes
`snapshots/steps/<op_id>.json`: per object `bbox`, `volume`, `faces`, `planar_share`,
type; plus the touched objects' `surface_quality`. No `FCStd`. Full document copies only when
`CADSWARM_STEP_SNAPSHOTS=1`, because each is ~3.4 MB and a long build would cost gigabytes.

**7.4 Migration.** `spacetime publish` the additive change to a **copy** of the live DB first
(`cadswarm-lexus-is350-<copy>`), verify all 606 ops read back with `task`/`brief` empty and
the module tests pass, then publish to the live DB. The rehearsal is a gate; the live publish
does not happen in this spec's implementation work.

## 8. Training

Two runs, one harness.

**Builder.** QLoRA SFT on an open coder model. Default base
`Qwen2.5-Coder-14B-Instruct`; `--base` overrides it and any compatible causal LM works. 4-bit
NF4 with double quantisation, bf16 compute, rank 16, alpha 32, lr 2e-4 cosine, 3 epochs,
effective batch 16 (batch 1 × grad-accum 16), gradient checkpointing, paged 8-bit AdamW, max
seq 3072 by default (4096 permitted; see the ladder below). Loss on the assistant message only.
Checkpoint and a `run.json` (dataset id, git commit, base, hyperparameters, seed, peak VRAM,
final loss) are written next to the adapter so every artifact is traceable.

**Critic.** The same base with a scalar head, Bradley–Terry loss over `pairs.jsonl`, 2 epochs,
4-bit like the builder. Below 300 pairs the trainer refuses and the eval uses `critic.py`'s
heuristic scorer (planar-share, printability, volume sanity, body-count) as the baseline to
beat.

**Memory ladder (16 GB target).** Estimates include LoRA adapters, activations with gradient
checkpointing, and paged 8-bit optimiser state; they are the numbers `--check` computes, not
guesses to rely on:

| Config | Weights | Total | Verdict |
|---|---|---|---|
| 7B bf16 LoRA | ~14.2 GB | ~15–16 GB | only at seq ≤ 1024 |
| 7B 4-bit QLoRA | ~4.5 GB | ~7–8 GB | seq 4096 |
| **14B 4-bit QLoRA (default)** | **~8.5 GB** | **~11–13 GB** | **seq 2048–3072** |
| 14B bf16 LoRA | ~28 GB | ~30 GB+ | needs 32 GB; not supported on 16 |

`train.py --check` loads the config, reports the estimate for weights, adapters, activations at
the requested sequence length and the optimiser, and exits non-zero above the detected VRAM
budget with the two cheapest fixes (shorter `--seq` or smaller `--base`). Nothing is trained
until it passes.

**Environment.** Training runs under WSL2 (Linux), where bitsandbytes and FlashAttention are
reliable; `eval_replay.py` stays on Windows next to FreeCAD. Datasets are plain files, so the
split costs nothing — the trainer is given a dataset path, the eval is given a policy.

**Context is the squeeze, not the weights.** The state summary (§6.6) is the largest part of
the prompt. At 3072 tokens the default caps fit; a run at 4096 may need `--max-objects` reduced
or a 7B base. Context length is a first-class flag because it, not parameter count, is what
16 GB actually limits.

**Volume gates** (why the first runs are honest experiments, not a product):

| Gate | Threshold | What it means |
|---|---|---|
| Builder worth training | ≥ 5,000 good steps across ≥ 3 projects | enough to beat the random baseline and be comparable to the base model |
| Builder generalises | ≥ 20,000 steps across ≥ 5 projects | held-out project score within 10% of train |
| Critic worth training | ≥ 300 pairs | beats the heuristic scorer on held-out projects |

Today: 606 ops, one project. The pipeline produces a usable dataset now so that every future
session moves these numbers.

## 9. Evaluation

`eval_replay.py` executes a policy against a real FreeCAD worker in the existing sandbox —
fresh document, loop `build prompt → policy → op → submit → check status` — capped at 40 steps
and 30 minutes, with the worker's own error codes surfaced rather than swallowed.

Policies scored: `reference` (the human/agent op sequence replayed: the ceiling), `random-op`
(floor), `base` (the model with no adapter), `trained` (the adapter), plus `critic` as a
separate scoring pass over the same candidates.

Metrics per case, aggregated per split:

| Metric | Definition |
|---|---|
| `valid_op_rate` | ops accepted / ops attempted |
| `rejection_rate` | `status=rejected` (lock/args violations) |
| `completion` | reference object-name set reached (Jaccard on names, type match) |
| `geometry` | bbox IoU, volume ratio, object type histogram distance; mesh Chamfer if `trimesh` is installed. Box IoU is only averaged over names where both sides recorded a box — the symbolic replay has volumes but no bounds, so a missing box is `null`, not zero |
| `printability` | the existing `check_printability` op result |
| `critic_score` | model or heuristic score of the final state |
| `steps`, `wall_ms` | efficiency |

`report.json` carries the table plus the dataset id and split mode, so two reports are always
comparable. A run is only claimed as an improvement if `trained` beats `base` on `completion`
and `geometry` on the held-out project with the same seed set.

## 10. Error handling and integrity

- Unreachable/paused source → `source.json` records it; export continues.
- Unparseable `args_json` → step kept with `parse_error`, `render_ok: false`, still a critic
  negative.
- Missing `model.FCStd`/STL → episode keeps `final.stl_sha256: null`; geometry metrics skip it.
- Spilled op results (`{"blob": …}` with the blob swept) → step `degraded`; no values invented.
- Non-deterministic ordering → banned: every iteration is sorted, no clock in content, dataset
  id is content-derived.
- Redaction runs before any file is written, for both turns and free text.
- A build with zero mutating ops is dropped (nothing to learn).

## 11. Testing

| Test | Asserts |
|---|---|
| `test_training_source.py` | paging by primary key, escaped quotes, a missing `host.json` |
| `test_training_export.py` | one file per table, skipped source, the token never written, style events read |
| `test_training_redact.py` | paths and e-mails removed |
| `test_training_state.py` | forward replay; failed op is a no-op; unknown object → degraded; no fabricated values; conformance |
| `test_training_labels.py` | outcome mapping, step labels, only a param correction pairs |
| `test_training_episodes.py` | golden segmentation, explicit task beats objects, idle-gap freestyle, split modes |
| `test_training_render.py` | row counts, single prompt contract, no split leakage, stable dataset id |
| `test_training_metrics.py` | Jaccard, bbox IoU, volume ratio, aggregation, heuristic critic ordering |
| `test_training_replay.py` | scripted policy reproduces the reference; refusals lower the valid rate; seed determinism |
| `test_training_package.py` | training imports no torch/FreeCAD; both installers exclude the package |
| `test_training_headroom.py` (Plan 3) | the `--check` estimator refuses an over-budget config |
| `cargo test` (module) | new op columns default and round-trip; existing rows unaffected |

Live check, not in CI:

```
python -m cadswarm.training export --project "C:\Users\waves\Documents\CadSwarm\Lexus IS350"
python -m cadswarm.training build  --project "…Lexus IS350" --conformance
```

The second must report conformance ≥ 95% before the dataset is called usable; every mismatch
is a bug in `state.py` or a documented gap in the record, not a number to wave through.

## 12. Packaging and isolation

- `cadswarm/training/` ships in the repo, never in the FreeCAD addon: add `cadswarm\training`
  to the `/XD` list in `install/install.ps1` (robocopy) and `--exclude=cadswarm/training` to
  the `tar` in `install/install.sh`, and assert the exclusion in `test_training_package.py`.
- `pyproject.toml` gains `train = ["torch>=2.4", "transformers>=4.45", "peft>=0.13",
  "datasets>=3", "bitsandbytes>=0.44", "trimesh"]`. The base install does not change.
- The offline pipeline and `eval_replay.py` run on the repo `.venv` (Windows, FreeCAD Python
  where a worker is needed). `train.py` runs in a separate WSL2 virtualenv with the `train`
  extra. The only contract between them is a dataset directory path.
- No network access at training time: datasets are local files.

## 13. Delivery phases

| Phase | Work | Gate |
|---|---|---|
| P0 | package skeleton, CLI, fixtures, `.gitignore`/packaging rules | tests collect and pass |
| P1 | `dbclient`, `export`, source handling | exports Lexus; byte-identical on re-run |
| P2 | `state`, `episodes`, conformance | conformance ≥ 95% on the real Lexus DB |
| P3 | `labels`, `split`, `render`, dataset card | card shows counts, provenance, biases; no split leakage |
| P4 | `critic` heuristic, `eval_replay`, baselines | reference ceiling ≫ random floor; report reproducible |
| P5 | capture instrumentation + migration rehearsal on a DB copy | 606 ops read back; module tests green |
| P6 | `train.py`, first builder and critic runs | builder beats `base` on held-out completion; critic beats heuristic |
| P7 | inference adapter | separate spec |

P0–P4 touch no app code and cannot break a running team. P5 is the only risky phase and it is
isolated to a copied database until the rehearsal passes.

**Plan 1** (`docs/superpowers/plans/2026-10-27-cad-training-pipeline.md`) implements P0–P4. P5
and P6 get their own plans: P5 changes a live database contract and must be planned against the
module, and P6's shape depends on the counts the first real dataset reports.

## 14. Risks

| Risk | Mitigation |
|---|---|
| Dataset too small to train a useful policy | critic first; volume gates in §8; capture from P5 onward; replay augmentation (harvest + replay) as a later phase if it stays small |
| One project dominates the corpus (a car, mostly agents) | split by project, dataset card states the bias, gates require ≥ 3 projects |
| Symbolic replay drifts from reality | conformance oracle against the live `cad_object` table, ≥ 95% gate |
| Per-step state unrecoverable for old data | degraded flag, excluded from SFT, kept for critic; replay augmentation available later |
| `activity` is lossy (200/member) so non-mutating steps vanish | turns capture in P5; the builder conditions on state, not on its own past inspection calls |
| Schema migration hurts the live team | additive defaults, copy-first rehearsal, live publish out of scope |
| 14B QLoRA is tight at long context on 16 GB | `--check` preflight against the memory ladder; `--seq` and `--max-objects` are the knobs; 7B is the documented fallback |
| Training code leaks into a user-facing build | explicit packaging exclusion + test |

## 15. Definition of done

1. `python -m cadswarm.training export --project "<Lexus IS350>"` produces raw data, and
   `build --conformance` reports ≥ 95% with every mismatch explained.
2. `dataset-card.md` and `manifest.json` describe the dataset: counts, provenance, biases,
   content hash, git commit.
3. `sft.jsonl`, `pairs.jsonl`, `eval/cases.jsonl` exist with the record counts the card claims.
4. `eval_replay.py` produces `report.json` for reference and random baselines on the held-out
   project, with a clear and reproducible gap.
5. The capture change is merged behind a rehearsed additive migration, with the existing 606
   ops verified intact.
6. Full test suite green, `cargo test` green, README test count updated, dataset card committed
   and data not committed.
