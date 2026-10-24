# Elastic: board knowledge, design memory and lessons

Optional. Without an Elastic project CadSwarm builds from the local board library and skips
memory; with one, agents can look up board dimensions from datasheets, remember finished
designs, and learn from reviewer findings.

Everything runs on the host machine inside the helper process. The API key stays in that
process's environment and is never written to the repo, the database, or an agent's context.

## What it holds

| Index | Written by | Read by |
| --- | --- | --- |
| `cadswarm-boards` | `python -m cadswarm.ingest boards/sources.json` | `cadswarm.board_docs` tool, `lookup_board` helper op |
| `cadswarm-designs` | `index_design` (`cadswarm.knowledge`) for a finished brief | `cadswarm.design_memory` tool, `recall_designs` helper op |
| `cadswarm-lessons` | the `distil-lessons` Workflow (every 10 minutes), from reviewer findings | `cadswarm.board_lessons` tool, `lessons` helper op |

`cadswarm-boards` and `cadswarm-designs` use `semantic_text` with the JINA embedding endpoint,
so Elasticsearch chunks and embeds the text itself; queries are hybrid (BM25 + semantic) and
reranked by `.jina-reranker-v3.5`.

## Configure

Add these to the shared `cadswarm.env` next to the checkout (see `.env.example`):

```dotenv
ELASTIC_URL=https://<project-id>.es.<region>.<provider>.elastic.cloud
KIBANA_URL=https://<project-id>.kb.<region>.<provider>.elastic.cloud
ELASTIC_API_KEY=<base64 api key>
```

`KIBANA_URL` is the same project host with `es` replaced by `kb`; Agent Builder and Workflows
live there. The key needs read/write on these indices plus `manageAgents` for the Board expert.

Then, from this checkout:

```powershell
pip install pypdf                       # only for PDF datasheets
powershell -File ..\cadswarm-guide\scripts\with-env.ps1 .venv\Scripts\python.exe -m cadswarm.ingest boards\sources.json
```

`elastic/setup.json` creates the inference endpoint and the three indices (idempotent).
`ingest` skips a source whose text extracts to nothing (scanned or vector-only PDFs) instead of
indexing a blank document.

Run one host per database. Two `cadswarm-host` processes share the host identity and supersede
each other's fence, so either one can win the export completion; if the loser predates
`index_design`, the brief still reaches `ready` but is never indexed.

## Agent Builder (optional, not in the demo)

The demo and the judging story use search, design memory and the Workflow only. This section is the
extra that ships alongside them; it degrades to the local board library whenever it is unavailable.

`elastic/agent.json` and `elastic/tools/*.json` define the Board expert:

- `cadswarm.board_docs(question)` - ES|QL over `cadswarm-boards`, with the query text as the
  semantic match.
- `cadswarm.board_lessons(board_id)` - the distilled lessons for a board.
- `cadswarm.design_memory(query)` - similar finished designs.

`Elastic.install_agent()` (called from `cadswarm.knowledge`) upserts the three tools and the
agent. Its instructions make it answer with only a JSON board spec whose every number comes from
a retrieved passage, with source URLs, and to say "not found" rather than guess. The host asks it
through `board_spec` only for a board the local library does not have; the reply is parsed and
validated (`validate_spec`: plausible dimensions, holes inside the PCB, sources present) before
the host trusts it for the rest of the session.

`converse` uses a 180 s timeout: Agent Builder runs a model and its tools, unlike a plain API call.

## Lessons Workflow

`elastic/workflows/distil-lessons.yaml` turns reviewer findings into per-board lessons:

```
cadswarm-designs.problems -> count per board -> cadswarm-lessons (one doc per board+problem)
```

Create or update it (this is also what the Kibana UI's Workflows > Create does), then run it:

```powershell
# POST /api/workflows {"workflows":[{"yaml":"<file>"}]} then
# POST /api/workflows/workflow/distil-lessons/run {"inputs":{}}
```

A run takes about a second. `GET /api/workflows/executions/<id>` shows step-by-step status and
the failure reason if a step errors; `DELETE /api/workflows/workflow/<id>` removes a workflow. The
scheduled trigger fires every 10 minutes, so lessons stay current without anyone pressing run.

The Workflow only has something to write when a design carries `problems`, and those come from the
design reviewer. The reviewer is a Factory Droid agent (`python -m cadswarm.reviewer`, enabled by
`cadswarm-host init-reviewer`), so on a machine without Droid every brief finishes with
`problems: []`: the Workflow still runs and completes, it just distils nothing. The judged demo
therefore shows board search, design memory and the Workflow itself, not a populated lessons index.

## Validated live (2026-10-24)

Serverless Elasticsearch/Kibana 9.6.0, project region us-east-1:

- `cadswarm-jina` embeds 1024-dim vectors; `.jina-reranker-v3.5` scores pairs.
- 6 board documents indexed (Pico 46k chars, Pi 4 brief, ESP32-S3, Uno, XIAO); the Pi 4
  mechanical drawing extracts no text and is skipped.
- Hybrid + rerank search returns the right board chunks for Pico holes, ESP32-S3 dimensions, and
  Uno mounting holes with a `board_id` filter.
- Design memory round trip: a live brief ("an enclosure for an ESP32-S3-DevKitC-1 with a lid and a
  USB cutout") was built by the swarm, exported, and indexed by the host on export completion with
  its 36 final params. `recall_designs` then returned it first (score 1.661) for "a pocket enclosure
  for an ESP32-S3 dev board with a USB cutout". Deleting the document and re-running the export put
  it back, so the automatic path works, not just the helper op.
- The three tools and `cadswarm-board-expert` install cleanly; one converse answered in 65 s before
  the trial model quota ran out (see Known limit).
- `distil-lessons` ran end to end, both steps `completed`, against the real design. It wrote 0
  lessons: that design finished with `problems: []` because no reviewer ran, which is the case
  above rather than a fault in the Workflow.

Known limit: the agent's model is whichever model Agent Builder is configured with, and on the trial
tier that is a managed model with a per-model request quota. When the quota is exhausted, `converse`
returns a bare `429 Too Many Requests` with no refill time, and the model selection made in the
Kibana UI is not stored on the agent document, so it cannot be changed from this API. Nothing else
in the pipeline (search, ingest, design memory, the Workflow) uses that model, and the host's
`board_spec` call is best effort: on any error it falls back to the local board library.
