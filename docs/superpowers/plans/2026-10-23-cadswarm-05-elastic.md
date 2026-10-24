# CadSwarm Elastic Implementation Plan (Person A after M0, or whoever is free first)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the swarm real dimensions for any dev board and memory of past designs, using Elasticsearch on Elastic Cloud Serverless with JINA embeddings and reranking, an Agent Builder agent, and a Workflow.

**Architecture:** Elastic is reached only from the host machine, which holds the only key. A Python helper process (`cadswarm/helper.py`) speaks the same JSON-lines protocol as the FreeCAD worker; the Rust host routes the `lookup_board`, `recall_designs` and `index_design` ops to it (and, per the Python plan, the project-file ops). The helper calls Elasticsearch for hybrid search and Kibana's Agent Builder for board specs. A one-time ingestion command fills the `boards` index from datasheets; finished briefs fill `designs`; a scheduled Workflow fills `lessons`.

**Tech Stack:** Python 3 stdlib (`urllib`, `json`, `html.parser`), `pypdf` (ingestion only), Elastic Cloud Serverless (Elasticsearch project), Elastic Inference Service (`jina-embeddings-v5-text-small`, `.jina-reranker-v3.5`), Agent Builder (Kibana API), Workflows (YAML).

Read first: `docs/superpowers/plans/2026-10-23-cadswarm-00-overview.md` (op kinds `lookup_board`, `recall_designs`), the spec section "Elastic: board knowledge and design memory".

## Global Constraints

- Elastic credentials live only on the host: `ELASTIC_URL`, `KIBANA_URL`, `ELASTIC_API_KEY` (env vars for the weekend; the host's keychain later). Never in the repo, the web app or an agent folder.
- Auth header: `Authorization: ApiKey <key>`. Kibana POST/PUT also need `kbn-xsrf: true`.
- Index names: `cadswarm-boards`, `cadswarm-designs`, `cadswarm-lessons`. Inference ids: `cadswarm-jina` (text_embedding), reranker `.jina-reranker-v3.5` (preconfigured).
- Every board value returned to the product carries a `source_url`. Measured entries in `boards/boards.json` (`"verified": true`) always win over looked-up ones.
- The helper never raises out of a request: errors come back as `{"ok": false, "code", "error"}` like the worker's.

## File structure

| File | Responsibility |
|---|---|
| `cadswarm/knowledge.py` | Elastic client (stdlib): setup, index, hybrid search, Agent Builder converse, board-spec validation |
| `cadswarm/ingest.py` | Fetch sources, extract text from PDF/HTML, index into `cadswarm-boards` |
| `cadswarm/helper.py` | JSON-lines helper process the host drives: knowledge ops (and file ops, added by the Python plan) |
| `boards/sources.json` | Datasheet and product-page URLs per board |
| `elastic/setup.json` | Index mappings and inference endpoint bodies |
| `elastic/agent.json`, `elastic/tools/*.json` | Agent Builder agent and tools |
| `elastic/workflows/distil-lessons.yaml` | The lessons Workflow |
| `tests/test_knowledge.py`, `tests/test_ingest.py`, `tests/test_helper.py` | Tests |
| `host/src/executor.rs`, `host/src/briefs.rs`, `host/src/main.rs` | Routing to the helper and the three call sites (Task 7) |

---

### Task 1: Serverless project, inference endpoint and indices

**Files:**
- Create: `elastic/setup.json`, `cadswarm/knowledge.py` (setup part), `tests/test_knowledge.py`

**Interfaces:**
- Produces: `class Elastic(es_url, kibana_url, api_key, timeout=30)` with `.es(method, path, body=None) -> dict`, `.kibana(method, path, body=None) -> dict`, `.setup(spec: dict) -> list[str]` (idempotent; returns what it created); `ElasticError(status, text)`; `Elastic.from_env()`.

- [ ] **Step 1: Create the project (manual, 5 minutes)**

Open `https://ela.st/calhacks` (the booth's sign-up link), create an **Elasticsearch** Serverless project. In Kibana: Help menu → Connection details → copy the Elasticsearch endpoint, and create an API key (Manage API keys, so it does not expire in 90 days). Find the Kibana URL in the browser address bar (the part before `/app/`). Set on the host machine:
```powershell
$env:ELASTIC_URL = "https://<project>.es.<region>.elastic.cloud"
$env:KIBANA_URL  = "https://<project>.kb.<region>.elastic.cloud"
$env:ELASTIC_API_KEY = "<key>"
curl.exe -s $env:ELASTIC_URL -H "Authorization: ApiKey $env:ELASTIC_API_KEY"
```
Expected: JSON with a `version` block. The API key must have Agent Builder privileges (the `manageAgents` privilege, to create agents); a key made from the Connection details panel as the project owner has them.

- [ ] **Step 2: Write `elastic/setup.json`**

```json
{
  "inference": [
    {"path": "_inference/text_embedding/cadswarm-jina",
     "body": {"service": "elastic", "service_settings": {"model_id": "jina-embeddings-v5-text-small"}}}
  ],
  "indices": {
    "cadswarm-boards": {"mappings": {"properties": {
      "board_id": {"type": "keyword"}, "title": {"type": "text"}, "source_url": {"type": "keyword"},
      "source_type": {"type": "keyword"}, "text": {"type": "text"},
      "content": {"type": "semantic_text", "inference_id": "cadswarm-jina"}}}},
    "cadswarm-designs": {"mappings": {"properties": {
      "brief_id": {"type": "keyword"}, "board_id": {"type": "keyword"}, "request": {"type": "text"},
      "outcome": {"type": "keyword"}, "findings": {"type": "text"}, "problems": {"type": "keyword"},
      "params": {"type": "flattened"}, "finished_at": {"type": "date"}, "text": {"type": "text"},
      "content": {"type": "semantic_text", "inference_id": "cadswarm-jina"}}}},
    "cadswarm-lessons": {"mappings": {"properties": {
      "board_id": {"type": "keyword"}, "problem": {"type": "keyword"}, "count": {"type": "integer"},
      "lesson": {"type": "text"}, "updated_at": {"type": "date"}}}}
  }
}
```
`text` duplicates `content` as a plain `text` field so hybrid search has a lexical side.

- [ ] **Step 3: Write the failing tests**

`tests/test_knowledge.py`:
```python
"""Elastic client request shapes, against a fake HTTP server (no Elastic account needed)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cadswarm.knowledge import Elastic, ElasticError

SEEN = []
EXISTING = {"/cadswarm-lessons"}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _body(self):
        n = int(self.headers.get("content-length", 0))
        return json.loads(self.rfile.read(n) or b"null")

    def do_HEAD(self):
        self.send_response(200 if self.path in EXISTING else 404)
        self.end_headers()

    def do_GET(self):
        code = 200 if self.path.startswith("/_inference/text_embedding/cadswarm-jina") and "/_inference/text_embedding/cadswarm-jina" in EXISTING else 404
        self.reply(code, {})

    def do_PUT(self):
        SEEN.append(("PUT", self.path, self.headers.get("authorization"), self._body()))
        EXISTING.add(self.path if not self.path.startswith("/_inference") else "/_inference/text_embedding/cadswarm-jina")
        self.reply(200, {"acknowledged": True})

    def do_POST(self):
        SEEN.append(("POST", self.path, self.headers.get("kbn-xsrf"), self._body()))
        if self.path.endswith("/boom"):
            return self.reply(400, {"error": {"type": "x", "reason": "bad request"}})
        self.reply(200, {"ok": True})

    def reply(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def url():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def spec():
    return {"inference": [{"path": "_inference/text_embedding/cadswarm-jina", "body": {"service": "elastic"}}],
            "indices": {"cadswarm-boards": {"mappings": {}}, "cadswarm-lessons": {"mappings": {}}}}


def test_setup_creates_only_what_is_missing_with_the_api_key(url):
    e = Elastic(url, url, "k")
    made = e.setup(spec())
    assert "_inference/text_embedding/cadswarm-jina" in made and "cadswarm-boards" in made
    assert "cadswarm-lessons" not in made  # already existed
    assert all(auth == "ApiKey k" for (m, p, auth, b) in SEEN if m == "PUT")
    assert e.setup(spec()) == []  # idempotent


def test_kibana_posts_carry_the_xsrf_header_and_errors_carry_the_reason(url):
    e = Elastic(url, url, "k")
    e.kibana("POST", "/api/agent_builder/converse", {"input": "hi"})
    assert SEEN[-1][2] == "true"
    with pytest.raises(ElasticError) as err:
        e.es("POST", "/boom", {})
    assert err.value.status == 400 and "bad request" in str(err.value)
```

- [ ] **Step 4: Run to see them fail**

Run: `python -m pytest tests/test_knowledge.py -q`
Expected: `ModuleNotFoundError: No module named 'cadswarm.knowledge'`.

- [ ] **Step 5: Implement the client part of `cadswarm/knowledge.py`**

```python
"""Elastic for CadSwarm: board knowledge and design memory (stdlib only).

Runs only on the host machine, inside the helper process; the API key never leaves it.
"""
import json
import os
import urllib.error
import urllib.request


class ElasticError(Exception):
    def __init__(self, status, text):
        self.status = status
        try:
            err = json.loads(text).get("error")
            reason = err.get("reason") if isinstance(err, dict) else err
        except (ValueError, AttributeError):
            reason = text
        super().__init__(f"Elastic {status}: {reason or text}")


class Elastic:
    def __init__(self, es_url, kibana_url, api_key, timeout=30):
        self.es_url, self.kibana_url = es_url.rstrip("/"), kibana_url.rstrip("/")
        self.api_key, self.timeout = api_key, timeout

    @classmethod
    def from_env(cls):
        missing = [k for k in ("ELASTIC_URL", "KIBANA_URL", "ELASTIC_API_KEY") if not os.environ.get(k)]
        if missing:
            raise ElasticError(0, f"Elastic is not configured on this host (missing {', '.join(missing)})")
        return cls(os.environ["ELASTIC_URL"], os.environ["KIBANA_URL"], os.environ["ELASTIC_API_KEY"])

    def _req(self, base, method, path, body=None, kibana=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method)
        req.add_header("authorization", "ApiKey " + self.api_key)
        req.add_header("content-type", "application/json")
        if kibana:
            req.add_header("kbn-xsrf", "true")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                text = r.read().decode("utf-8")
                return json.loads(text) if text.strip() else {}
        except urllib.error.HTTPError as e:
            raise ElasticError(e.code, e.read().decode("utf-8", "replace"))
        except urllib.error.URLError as e:
            raise ElasticError(0, f"cannot reach Elastic: {e.reason}")

    def es(self, method, path, body=None):
        return self._req(self.es_url, method, path, body)

    def kibana(self, method, path, body=None):
        return self._req(self.kibana_url, method, path, body, kibana=True)

    def _exists(self, path):
        try:
            self._req(self.es_url, "HEAD" if not path.startswith("/_inference") else "GET", path)
            return True
        except ElasticError as e:
            if e.status == 404:
                return False
            raise

    def setup(self, spec):
        """Create the inference endpoints and indices that do not exist yet."""
        made = []
        for inf in spec.get("inference", []):
            if not self._exists("/" + inf["path"]):
                self.es("PUT", "/" + inf["path"], inf["body"])
                made.append(inf["path"])
        for name, body in spec.get("indices", {}).items():
            if not self._exists("/" + name):
                self.es("PUT", "/" + name, body)
                made.append(name)
        return made
```

- [ ] **Step 6: Run the tests, then set up the real project**

Run: `python -m pytest tests/test_knowledge.py -q` → `2 passed`.
Then: `python -c "import json; from cadswarm.knowledge import Elastic; print(Elastic.from_env().setup(json.load(open('elastic/setup.json'))))"`
Expected: the inference id and the three index names. If creating `cadswarm-jina` fails because `jina-embeddings-v5-text-small` is unavailable on your project, change `model_id` in `elastic/setup.json` to `jina-embeddings-v3` and rerun. Then run `GET _inference/_all`: it must list `.jina-reranker-v3.5`; if it lists a different `.jina-reranker-*` id, set `RERANKER` in `cadswarm/knowledge.py` to that id. The 13.0 Elastic prize rules are not published (12.0's required Agent Builder with MCP); confirm at the booth whether agents must reach `cadswarm-board-expert` over Agent Builder's MCP endpoint.

- [ ] **Step 7: Commit**

```powershell
git add elastic/setup.json cadswarm/knowledge.py tests/test_knowledge.py
git commit -m "feat(elastic): client, JINA inference endpoint and indices"
```

---

### Task 2: Hybrid search with reranking, and indexing designs

**Files:**
- Modify: `cadswarm/knowledge.py`, `tests/test_knowledge.py`

**Interfaces:**
- Produces: `search_body(query, k=5, filters=None) -> dict` (pure); `Elastic.search(index, query, k=5, filters=None) -> list[dict]` (`{"score", "source", "chunks"}`); `design_doc(brief, plan, reviews, params) -> dict` (pure); `Elastic.index_design(doc) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_knowledge.py`:
```python
from cadswarm.knowledge import design_doc, search_body


def test_search_is_hybrid_rrf_reranked_by_jina_and_filterable():
    b = search_body("pico mounting holes", k=3, filters={"board_id": "raspberry-pi-pico"})
    rr = b["retriever"]["text_similarity_reranker"]
    assert rr["inference_id"] == ".jina-reranker-v3.5" and rr["inference_text"] == "pico mounting holes"
    inner = rr["retriever"]["rrf"]["retrievers"]
    kinds = [list(r["standard"]["query"]["bool"]["must"][0]["match"].keys())[0] for r in inner]
    assert kinds == ["text", "content"]
    assert inner[0]["standard"]["query"]["bool"]["filter"] == [{"term": {"board_id": "raspberry-pi-pico"}}]
    assert b["size"] == 3


def test_a_design_doc_summarises_the_brief_and_its_reviews():
    d = design_doc({"id": "b_1", "text": "case for a pico", "board": "raspberry-pi-pico", "status": "ready"},
                   {"parts": [{"name": "Base"}, {"name": "Lid"}]},
                   [{"round": 1, "passed": False, "findings_json": '[{"object":"Lid","problem":"clips usb plug"}]'},
                    {"round": 2, "passed": True, "findings_json": "[]"}],
                   {"wall": 2.0, "gap": 0.5})
    assert d["brief_id"] == "b_1" and d["outcome"] == "ready" and d["problems"] == ["clips usb plug"]
    assert "clips usb plug" in d["content"] and "Base" in d["content"] and d["params"]["gap"] == 0.5
```

- [ ] **Step 2: Run to see them fail**

Run: `python -m pytest tests/test_knowledge.py -q`
Expected: `ImportError: cannot import name 'design_doc'`.

- [ ] **Step 3: Implement**

Append to `cadswarm/knowledge.py`:
```python
import time

RERANKER = ".jina-reranker-v3.5"


def search_body(query, k=5, filters=None):
    """Lexical + semantic (JINA embeddings) fused with RRF, then re-ranked by the JINA reranker."""
    flt = [{"term": {f: v}} for f, v in (filters or {}).items()]

    def leg(field):
        return {"standard": {"query": {"bool": {"must": [{"match": {field: query}}], "filter": flt}}}}

    return {
        "size": k,
        "retriever": {"text_similarity_reranker": {
            "retriever": {"rrf": {"retrievers": [leg("text"), leg("content")]}},
            "field": "text", "inference_id": RERANKER, "inference_text": query, "rank_window_size": 50}},
        "fields": [{"field": "content", "format": "chunks"}],
        "_source": {"excludes": ["content", "text"]},
    }


def _search(self, index, query, k=5, filters=None):
    r = self.es("POST", f"/{index}/_search", search_body(query, k, filters))
    return [{"score": h.get("_score"), "source": h.get("_source", {}),
             "chunks": h.get("fields", {}).get("content", [])} for h in r.get("hits", {}).get("hits", [])]


Elastic.search = _search


def design_doc(brief, plan, reviews, params):
    findings = []
    for rv in sorted(reviews, key=lambda r: r["round"]):
        try:
            findings += json.loads(rv.get("findings_json") or "[]")
        except ValueError:
            pass
    problems = [f.get("problem", "") for f in findings if f.get("problem")]
    parts = [p.get("name", "") for p in (plan or {}).get("parts", [])]
    text = (f"Request: {brief['text']}\nBoard: {brief.get('board') or 'unspecified'}\nParts: {', '.join(parts)}\n"
            f"Problems found in review: {'; '.join(problems) or 'none'}\n"
            f"Final params: {', '.join(f'{k}={v}' for k, v in sorted(params.items()))}")
    return {"brief_id": brief["id"], "board_id": brief.get("board") or "", "request": brief["text"],
            "outcome": brief.get("status", ""), "findings": json.dumps(findings), "problems": problems,
            "params": params, "finished_at": int(time.time() * 1000), "text": text, "content": text}


def _index_design(self, doc):
    self.es("PUT", f"/cadswarm-designs/_doc/{doc['brief_id']}", doc)


Elastic.index_design = _index_design
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_knowledge.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Try a real search after Task 3's ingestion**

`python -c "from cadswarm.knowledge import Elastic; import json; print(json.dumps(Elastic.from_env().search('cadswarm-boards', 'pico mounting hole spacing', 3), indent=1)[:2000])"`
Expected: Pico datasheet chunks first. If the reranker errors with an unknown inference id, list endpoints with `GET _inference` and use the reranker id it shows.

- [ ] **Step 6: Commit**

```powershell
git add cadswarm/knowledge.py tests/test_knowledge.py
git commit -m "feat(elastic): hybrid search reranked by JINA, and design documents"
```

---

### Task 3: Ingest board datasheets

**Files:**
- Create: `cadswarm/ingest.py`, `boards/sources.json`, `tests/test_ingest.py`

**Interfaces:**
- Produces: `html_to_text(html) -> str` (pure), `pdf_to_text(bytes) -> str`, `ingest(elastic, sources, fetch=urlopen_bytes) -> int` (documents indexed); CLI `python -m cadswarm.ingest boards/sources.json`.

- [ ] **Step 1: Write `boards/sources.json`**

```json
[
  {"board_id": "raspberry-pi-pico", "title": "Raspberry Pi Pico datasheet", "type": "datasheet",
   "url": "https://datasheets.raspberrypi.com/pico/pico-datasheet.pdf"},
  {"board_id": "raspberry-pi-4", "title": "Raspberry Pi 4 mechanical drawing", "type": "drawing",
   "url": "https://datasheets.raspberrypi.com/rpi4/raspberry-pi-4-mechanical-drawing.pdf"},
  {"board_id": "raspberry-pi-4", "title": "Raspberry Pi 4 product brief", "type": "datasheet",
   "url": "https://datasheets.raspberrypi.com/rpi4/raspberry-pi-4-product-brief.pdf"},
  {"board_id": "esp32-s3-devkitc-1", "title": "ESP32-S3-DevKitC-1 user guide", "type": "product_page",
   "url": "https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32s3/esp32-s3-devkitc-1/user_guide_v1.1.html"},
  {"board_id": "arduino-uno-r3", "title": "Arduino Uno R3 product page", "type": "product_page",
   "url": "https://docs.arduino.cc/hardware/uno-rev3/"},
  {"board_id": "seeed-xiao-esp32c3", "title": "Seeed XIAO ESP32C3 wiki", "type": "product_page",
   "url": "https://wiki.seeedstudio.com/XIAO_ESP32C3_Getting_Started/"}
]
```
Before relying on any URL, open it in a browser; replace dead ones. Add boards teams at the venue actually bring.

- [ ] **Step 2: Write the failing tests**

`tests/test_ingest.py`:
```python
from cadswarm.ingest import html_to_text, ingest


def test_html_keeps_text_and_table_cells_and_drops_scripts():
    html = ("<html><head><script>var x=1</script><style>p{}</style></head><body><h1>XIAO</h1>"
            "<table><tr><td>Dimensions</td><td>21 x 17.8 mm</td></tr></table></body></html>")
    t = html_to_text(html)
    assert "XIAO" in t and "21 x 17.8 mm" in t and "var x" not in t and "p{}" not in t


class FakeElastic:
    def __init__(self):
        self.docs = []

    def es(self, method, path, body=None):
        self.docs.append((path, body))
        return {}


def test_ingest_indexes_one_document_per_source_with_its_metadata():
    src = [{"board_id": "b1", "title": "T", "type": "product_page", "url": "https://x/a.html"}]
    fake = FakeElastic()
    n = ingest(fake, src, fetch=lambda url: (b"<p>Board is 50 mm long</p>", "text/html"))
    assert n == 1
    path, doc = fake.docs[0]
    assert path.startswith("/cadswarm-boards/_doc/")
    assert doc["board_id"] == "b1" and doc["source_url"] == "https://x/a.html"
    assert "50 mm" in doc["content"] and doc["text"] == doc["content"]
```

- [ ] **Step 3: Run to see them fail**

Run: `python -m pytest tests/test_ingest.py -q`
Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement `cadswarm/ingest.py`**

```python
"""Fill the boards index from datasheets and product pages.

    python -m cadswarm.ingest boards/sources.json

PDF text extraction uses pypdf (pip install pypdf); Elastic's semantic_text field chunks and
embeds the text with the JINA endpoint on its own.
"""
import hashlib
import io
import json
import sys
import urllib.request
from html.parser import HTMLParser

MAX_CHARS = 400_000


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in ("p", "br", "tr", "li", "h1", "h2", "h3", "h4", "div", "table"):
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.out.append(data.strip() + " ")


def html_to_text(html):
    p = _Text()
    p.feed(html)
    lines = [" ".join(l.split()) for l in "".join(p.out).splitlines()]
    return "\n".join(l for l in lines if l)


def pdf_to_text(data):
    from pypdf import PdfReader
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


def urlopen_bytes(url):
    req = urllib.request.Request(url, headers={"user-agent": "cadswarm-ingest/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), r.headers.get("content-type", "")


def ingest(elastic, sources, fetch=urlopen_bytes):
    n = 0
    for s in sources:
        try:
            data, ctype = fetch(s["url"])
        except Exception as e:  # one dead link must not stop the rest
            print(f"skip {s['url']}: {e}", file=sys.stderr)
            continue
        is_pdf = "pdf" in ctype or s["url"].lower().endswith(".pdf")
        text = pdf_to_text(data) if is_pdf else html_to_text(data.decode("utf-8", "replace"))
        text = text[:MAX_CHARS]
        doc_id = hashlib.sha256(s["url"].encode()).hexdigest()[:20]
        elastic.es("PUT", f"/cadswarm-boards/_doc/{doc_id}", {
            "board_id": s["board_id"], "title": s["title"], "source_url": s["url"],
            "source_type": s.get("type", "datasheet"), "text": text, "content": text})
        print(f"indexed {s['board_id']}: {s['title']} ({len(text)} chars)")
        n += 1
    return n


def main(argv=None):
    from .knowledge import Elastic
    path = (argv or sys.argv[1:] or ["boards/sources.json"])[0]
    with open(path, encoding="utf-8") as f:
        print(f"{ingest(Elastic.from_env(), json.load(f))} documents indexed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests, then ingest for real**

Run: `python -m pytest tests/test_ingest.py -q` → `2 passed`.
Then: `pip install pypdf; python -m cadswarm.ingest boards/sources.json`
Expected: one `indexed ...` line per live source. Indexing waits on embeddings, so long PDFs take a few seconds each.

- [ ] **Step 6: Commit**

```powershell
git add cadswarm/ingest.py boards/sources.json tests/test_ingest.py
git commit -m "feat(elastic): ingest board datasheets and product pages"
```

---

### Task 4: The Agent Builder board expert

**Files:**
- Create: `elastic/tools/board_docs.json`, `elastic/tools/design_memory.json`, `elastic/tools/board_lessons.json`, `elastic/agent.json`
- Modify: `cadswarm/knowledge.py`, `tests/test_knowledge.py`

**Interfaces:**
- Produces: `Elastic.install_agent(dir="elastic") -> None` (creates or updates tools and the agent); `Elastic.board_spec(board_id, question="") -> dict`; `validate_spec(raw: dict) -> dict` (pure; raises `ValueError` with the reason) returning the `boards.json` shape plus `"sources"`.

- [ ] **Step 1: Write the tools and agent**

`elastic/tools/board_docs.json`:
```json
{"id": "cadswarm.board_docs", "type": "esql",
 "description": "Search dev board datasheets and product pages. Returns the most relevant passages with their source URL.",
 "configuration": {
   "query": "FROM cadswarm-boards METADATA _score | WHERE MATCH(content, ?question) | SORT _score DESC | KEEP board_id, title, source_url, text, _score | LIMIT 6",
   "params": {"question": {"type": "keyword", "description": "What to look up, e.g. 'Raspberry Pi Pico mounting hole positions'"}}}}
```
`elastic/tools/design_memory.json`:
```json
{"id": "cadswarm.design_memory", "type": "esql",
 "description": "Search past CadSwarm designs: what was requested, which board, which problems the reviewer found.",
 "configuration": {
   "query": "FROM cadswarm-designs METADATA _score | WHERE MATCH(content, ?query) | SORT _score DESC | KEEP brief_id, board_id, request, problems, outcome, _score | LIMIT 5",
   "params": {"query": {"type": "keyword", "description": "The new request, to find similar past designs"}}}}
```
`elastic/tools/board_lessons.json`:
```json
{"id": "cadswarm.board_lessons", "type": "esql",
 "description": "Recurring problems found when designing for a board.",
 "configuration": {
   "query": "FROM cadswarm-lessons | WHERE board_id == ?board_id | SORT count DESC | KEEP problem, count, lesson | LIMIT 10",
   "params": {"board_id": {"type": "keyword", "description": "Board id such as raspberry-pi-pico"}}}}
```
`elastic/agent.json`:
```json
{"id": "cadswarm-board-expert", "name": "CadSwarm board expert",
 "description": "Board dimensions from datasheets, and lessons from past CadSwarm designs.",
 "configuration": {
   "instructions": "You give exact mechanical data for development boards so an enclosure fits. Use cadswarm.board_docs to find dimensions; never guess. When asked for a board spec, answer with ONLY a JSON object: {\"label\": str, \"pcb\": [length_mm, width_mm, thickness_mm], \"holes\": [[x_mm, y_mm], ...], \"hole_d\": mm, \"clear_top\": mm, \"clear_bottom\": mm, \"ports\": [{\"name\", \"side\": \"-x|+x|-y|+y\", \"center\": [pos_along_edge_mm, height_above_pcb_mm], \"size\": [w_mm, h_mm]}], \"sources\": {\"<field>\": \"<source_url>\"}}. Coordinates are from the PCB's lower-left corner viewed from the top, length along x. Every number must come from a passage you retrieved, and every field you fill must have a source_url in sources. If a value is not in the documents, leave the list empty or the number 0 and say so in sources (\"<field>\": \"not found\"). When asked about past designs, use cadswarm.design_memory and cadswarm.board_lessons and answer in plain sentences.",
   "tools": [{"tool_ids": ["cadswarm.board_docs", "cadswarm.design_memory", "cadswarm.board_lessons"]}]}}
```

- [ ] **Step 2: Write the failing validation tests**

Append to `tests/test_knowledge.py`:
```python
from cadswarm.knowledge import validate_spec


def good():
    return {"label": "Pico", "pcb": [51, 21, 1], "holes": [[2, 4.8], [49, 4.8]], "hole_d": 2.1,
            "clear_top": 4, "clear_bottom": 2,
            "ports": [{"name": "usb", "side": "-x", "center": [10.5, 1.4], "size": [8.5, 3.2]}],
            "sources": {"pcb": "https://d/pico.pdf", "holes": "https://d/pico.pdf", "ports": "https://d/pico.pdf"}}


def test_a_sourced_spec_passes_and_keeps_its_sources():
    s = validate_spec(good())
    assert s["pcb"] == [51.0, 21.0, 1.0] and s["sources"]["holes"] == "https://d/pico.pdf"
    assert s["verified"] is False


def test_unsourced_or_implausible_specs_are_refused():
    bad = good(); bad["sources"].pop("pcb")
    with pytest.raises(ValueError, match="pcb"):
        validate_spec(bad)
    bad = good(); bad["pcb"] = [5000, 21, 1]
    with pytest.raises(ValueError, match="pcb"):
        validate_spec(bad)
    bad = good(); bad["holes"] = [[80, 4]]
    with pytest.raises(ValueError, match="hole"):
        validate_spec(bad)
```

- [ ] **Step 3: Run to see them fail**

Run: `python -m pytest tests/test_knowledge.py -q`
Expected: `ImportError: cannot import name 'validate_spec'`.

- [ ] **Step 4: Implement**

Append to `cadswarm/knowledge.py`:
```python
import glob
import re

AGENT_ID = "cadswarm-board-expert"


def _install_agent(self, folder="elastic"):
    for path in sorted(glob.glob(os.path.join(folder, "tools", "*.json"))):
        tool = json.load(open(path, encoding="utf-8"))
        try:
            self.kibana("POST", "/api/agent_builder/tools", tool)
        except ElasticError as e:
            if e.status != 409:
                raise
            body = {k: v for k, v in tool.items() if k not in ("id", "type")}
            self.kibana("PUT", f"/api/agent_builder/tools/{tool['id']}", body)
    agent = json.load(open(os.path.join(folder, "agent.json"), encoding="utf-8"))
    try:
        self.kibana("POST", "/api/agent_builder/agents", agent)
    except ElasticError as e:
        if e.status != 409:
            raise
        self.kibana("PUT", f"/api/agent_builder/agents/{agent['id']}", {k: v for k, v in agent.items() if k != "id"})


Elastic.install_agent = _install_agent


def _converse(self, text):
    r = self.kibana("POST", "/api/agent_builder/converse", {"input": text, "agent_id": AGENT_ID})
    # The reply text lives under response.message in the converse API; fall back to the whole body.
    return (r.get("response") or {}).get("message") or json.dumps(r)


Elastic.converse = _converse


def _first_json_object(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("the board expert did not return JSON")
    return json.loads(m.group(0))


def validate_spec(raw):
    src = raw.get("sources") or {}
    pcb = [float(x) for x in raw.get("pcb", [])]
    if len(pcb) != 3 or not all(0 < x < 400 for x in pcb[:2]) or not 0 < pcb[2] < 10:
        raise ValueError(f"pcb dimensions look wrong: {raw.get('pcb')}")
    if not str(src.get("pcb", "")).startswith("http"):
        raise ValueError("pcb has no source_url")
    holes = [[float(a), float(b)] for a, b in raw.get("holes", [])]
    for x, y in holes:
        if not (0 <= x <= pcb[0] and 0 <= y <= pcb[1]):
            raise ValueError(f"hole ({x}, {y}) is outside the {pcb[0]} x {pcb[1]} board")
    if holes and not str(src.get("holes", "")).startswith("http"):
        raise ValueError("holes have no source_url")
    ports = []
    for p in raw.get("ports", []):
        if p.get("side") not in ("-x", "+x", "-y", "+y"):
            raise ValueError(f"port {p.get('name')} has no valid side")
        ports.append({"name": re.sub(r"\W+", "_", str(p["name"]).lower()), "side": p["side"],
                      "center": [float(v) for v in p["center"]], "size": [float(v) for v in p["size"]]})
    return {"label": str(raw.get("label", "")), "pcb": pcb, "holes": holes, "hole_d": float(raw.get("hole_d", 0)),
            "clear_top": float(raw.get("clear_top", 0)) or 10.0, "clear_bottom": float(raw.get("clear_bottom", 0)) or 3.0,
            "ports": ports, "sources": src, "verified": False}


def _board_spec(self, board_id, question=""):
    ask = (f"Give the board spec JSON for {board_id}." + (f" Context: {question}" if question else ""))
    return validate_spec(_first_json_object(self.converse(ask)))


Elastic.board_spec = _board_spec
```

- [ ] **Step 5: Run the tests, install the agent, ask it**

Run: `python -m pytest tests/test_knowledge.py -q` → `6 passed`.
Then:
```powershell
python -c "from cadswarm.knowledge import Elastic; Elastic.from_env().install_agent()"
python -c "from cadswarm.knowledge import Elastic; import json; print(json.dumps(Elastic.from_env().board_spec('raspberry-pi-pico'), indent=1))"
```
Expected: a Pico spec close to 51 × 21 mm with four holes and source URLs. Test each tool first if the agent misbehaves: `POST {KIBANA_URL}/api/agent_builder/tools/_execute` with `{"tool_id": "cadswarm.board_docs", "tool_params": {"question": "pico mounting holes"}}`. If ES|QL rejects `MATCH` on a `semantic_text` field, change the three queries to `WHERE MATCH(text, ?question)` (lexical) and file it as a known limitation. If the converse reply is not under `response.message`, print the raw body once and fix `_converse`.

- [ ] **Step 6: Commit**

```powershell
git add elastic cadswarm/knowledge.py tests/test_knowledge.py
git commit -m "feat(elastic): Agent Builder board expert with sourced, validated specs"
```

---

### Task 5: The lessons Workflow

**Files:**
- Create: `elastic/workflows/distil-lessons.yaml`

**Interfaces:**
- Produces: `cadswarm-lessons` documents `{board_id, problem, count, lesson, updated_at}`, refreshed every 10 minutes.

- [ ] **Step 1: Write the Workflow**

```yaml
name: distil-lessons
description: Turn reviewer findings from finished CadSwarm designs into per-board lessons.
enabled: true
tags: [cadswarm]
triggers:
  - type: scheduled
    with:
      every: 10m
steps:
  - name: count_problems
    type: elasticsearch.esql.query
    with:
      query: >
        FROM cadswarm-designs
        | MV_EXPAND problems
        | WHERE problems IS NOT NULL
        | STATS count = COUNT(*) BY board_id, problems
        | WHERE count >= 1
        | SORT count DESC
        | LIMIT 200
  - name: write_lessons
    type: foreach
    foreach: "{{ steps.count_problems.output.values }}"
    steps:
      - name: upsert_lesson
        type: elasticsearch.index
        with:
          index: cadswarm-lessons
          id: "{{ foreach.item[1] }}::{{ foreach.item[2] }}"
          document:
            board_id: "{{ foreach.item[1] }}"
            problem: "{{ foreach.item[2] }}"
            count: "{{ foreach.item[0] }}"
            lesson: "Seen {{ foreach.item[0] }} time(s) on {{ foreach.item[1] }}: {{ foreach.item[2] }}. Design to avoid it."
            updated_at: "{{ execution.startedAt }}"
```

- [ ] **Step 2: Import and run it once**

In Kibana open Workflows → Create → paste the YAML. The editor validates step types and template syntax: if `elasticsearch.esql.query`, `foreach` or `elasticsearch.index` are named differently in your project's step catalog, use the catalog's names (the shape stays: an ES|QL STATS query, then one index call per row; ES|QL rows come back as arrays in column order `count, board_id, problems`). Run it manually once.
Expected: after a design with findings exists (Task 7, or index one by hand with `Elastic.index_design(design_doc(...))`), `GET cadswarm-lessons/_search` returns lessons.

- [ ] **Step 3: Commit**

```powershell
git add elastic/workflows/distil-lessons.yaml
git commit -m "feat(elastic): scheduled Workflow that distils lessons from past designs"
```

---

### Task 6: The host's Python helper process

> **Revised 2026-10-24:** plan 03 Task 5 already creates `cadswarm/helper.py`, with `Project`, `handle(project, request)` and `HANDLERS[op](project, args)` for the file and storage ops. Don't create the file again. Add `lookup_board`, `board_spec`, `recall_designs` and `index_design` to its `HANDLERS`. They use `project.elastic`, a `knowledge.Elastic` built lazily from the host's keys on first use. Adapt the tests below to `p = helper.Project(tmp_path); p.elastic = FakeElastic(); helper.handle(p, {...})`. The protocol and error format are the same.

**Files:**
- Create: `cadswarm/helper.py`, `tests/test_helper.py`

**Interfaces:**
- Consumes: `Elastic`, `search`, `board_spec`, `index_design`, `design_doc`.
- Produces: `python cadswarm/helper.py <project_dir>` speaking the worker protocol (first line `{"ready": true}`; request `{"id", "op", "args"}`; reply `{"id", "ok", "result"}` or `{"id", "ok": false, "code", "error"}`). Ops: `lookup_board {question, board}`, `board_spec {board}`, `recall_designs {query, board}`, `index_design {brief, plan, reviews, params}`. `HANDLERS: dict[str, callable]` so the Python plan can register the file ops in the same process.

- [ ] **Step 1: Write the failing tests**

`tests/test_helper.py`:
```python
import json

from cadswarm import helper


class FakeElastic:
    def search(self, index, query, k=5, filters=None):
        return [{"score": 1.0, "source": {"board_id": "raspberry-pi-pico", "source_url": "https://d/p.pdf",
                                          "title": "Pico datasheet"}, "chunks": ["holes are 47 x 11.4 mm apart"]}]

    def board_spec(self, board, question=""):
        return {"label": "Pico", "pcb": [51.0, 21.0, 1.0], "sources": {"pcb": "https://d/p.pdf"}}

    def index_design(self, doc):
        self.indexed = doc


def test_lookup_returns_passages_with_their_sources():
    r = helper.handle({"op": "lookup_board", "args": {"question": "pico holes"}}, FakeElastic())
    assert r["ok"] and r["result"]["answers"][0]["source_url"] == "https://d/p.pdf"
    assert "47 x 11.4" in r["result"]["answers"][0]["text"]


def test_unknown_ops_and_failures_are_errors_not_crashes():
    assert helper.handle({"op": "nope", "args": {}}, FakeElastic())["code"] == "unknown_op"

    class Broken(FakeElastic):
        def search(self, *a, **k):
            raise RuntimeError("down")
    r = helper.handle({"op": "recall_designs", "args": {"query": "x"}}, Broken())
    assert r["ok"] is False and "down" in r["error"]


def test_index_design_builds_the_document():
    fake = FakeElastic()
    r = helper.handle({"op": "index_design", "args": {
        "brief": {"id": "b_1", "text": "case", "board": "raspberry-pi-pico", "status": "ready"},
        "plan": {"parts": []}, "reviews": [], "params": {"wall": 2.0}}}, fake)
    assert r["ok"] and fake.indexed["brief_id"] == "b_1"
```

- [ ] **Step 2: Run to see them fail**

Run: `python -m pytest tests/test_helper.py -q`
Expected: `ImportError`.

- [ ] **Step 3: Implement `cadswarm/helper.py`**

```python
"""Host-side helper for ops that are not FreeCAD geometry: Elastic lookups (and project files,
registered by the Python plan). Same JSON-lines protocol as the FreeCAD worker, so cadswarm-host
drives both with one driver. Holds the Elastic key; nothing else ever sees it.

    python cadswarm/helper.py <project_dir>
"""
import json
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cadswarm.knowledge import design_doc  # noqa: E402


def lookup_board(e, a):
    filters = {"board_id": a["board"]} if a.get("board") else None
    hits = e.search("cadswarm-boards", a["question"], 5, filters)
    return {"answers": [{"board_id": h["source"].get("board_id"), "title": h["source"].get("title"),
                         "source_url": h["source"].get("source_url"), "score": h["score"],
                         "text": " … ".join(h["chunks"][:2])[:1500]} for h in hits]}


def board_spec(e, a):
    return e.board_spec(a["board"], a.get("question", ""))


def recall_designs(e, a):
    filters = {"board_id": a["board"]} if a.get("board") else None
    designs = e.search("cadswarm-designs", a["query"], 3, filters)
    lessons = e.search("cadswarm-lessons", a.get("board") or a["query"], 5) if a.get("board") else []
    return {"designs": [d["source"] for d in designs], "lessons": [l["source"].get("lesson") for l in lessons]}


def index_design(e, a):
    e.index_design(design_doc(a["brief"], a.get("plan") or {}, a.get("reviews") or [], a.get("params") or {}))
    return {"indexed": a["brief"]["id"]}


HANDLERS = {"lookup_board": lookup_board, "board_spec": board_spec,
            "recall_designs": recall_designs, "index_design": index_design}


def handle(req, elastic):
    fn = HANDLERS.get(req.get("op"))
    if fn is None:
        return {"ok": False, "code": "unknown_op", "error": f"Unknown op {req.get('op')!r}"}
    try:
        return {"ok": True, "result": fn(elastic, req.get("args") or {})}
    except Exception as ex:  # report, never die: the host keeps one helper for the whole session
        return {"ok": False, "code": getattr(ex, "code", "op_error"), "error": f"{type(ex).__name__}: {ex}"}


def main():
    from cadswarm.knowledge import Elastic, ElasticError
    try:
        elastic = Elastic.from_env()
    except ElasticError as ex:
        elastic = None
        print(f"helper: {ex}", file=sys.stderr)
    out = sys.stdout
    sys.stdout = sys.stderr
    out.write(json.dumps({"ready": True, "project": sys.argv[1] if len(sys.argv) > 1 else ""}) + "\n")
    out.flush()
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        resp = handle(req, elastic) if elastic else {"ok": False, "code": "not_configured",
                                                      "error": "Elastic is not configured on the host"}
        resp["id"] = req.get("id")
        out.write(json.dumps(resp, default=str) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_helper.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```powershell
git add cadswarm/helper.py tests/test_helper.py
git commit -m "feat(elastic): host-side helper process for Elastic ops"
```

---

### Task 7: Wire Elastic into the host and the agents

**Files:**
- Modify: `host/src/config.rs`, `host/src/main.rs`, `host/src/executor.rs`, `host/src/briefs.rs`, `host/src/agents.rs`, `cadswarm/space_hub.py`, `cadswarm/mcp_server.py`

**Interfaces:**
- Consumes: `cadswarm/helper.py`, `WorkerDriver` (reused for the helper), host plan Tasks 5–6.
- Produces: `HELPER_KINDS` routing in `executor::run_op`; brief seeding from `board_spec` when the board is not verified locally; planner prompt with recalled designs and lessons; `index_design` when a brief turns `ready`; MCP tools `lookup_board(question, board)` and `recall_designs(query, board)`.

- [ ] **Step 1: Route helper ops in `host/src/executor.rs`**

Change `run_op`'s signature to take the helper too, and route:
```rust
pub const HELPER_KINDS: &[&str] = &["lookup_board", "recall_designs", "board_spec", "index_design",
    "list_files", "read_file", "file_history", "save_file", "make_folder", "move_file",
    "delete_file", "restore_file", "snapshot"];

pub fn run_op(space: &Space, worker: &mut WorkerDriver, helper: &mut WorkerDriver, cfg: &HostConfig, op: &OpRow) -> Result<()> {
    space.start_op(&op.id)?;
    let mut args: Value = serde_json::from_str(&op.args_json).unwrap_or(json!({}));
    let reply = if HELPER_KINDS.contains(&op.kind.as_str()) {
        args["_author"] = json!(space.member_name(op.by));
        helper.call(&op.kind, &args, None)
    } else {
        match op.kind.as_str() {
            "export" => export(space, worker, cfg, op, &args),
            "printability" => {
                if args.get("bed").is_none() {
                    args["bed"] = json!(space.bed());
                }
                worker.call("printability", &args, None)
            }
            _ if op.mutating => worker.call(&op.kind, &args, Some(&op.allowed)),
            _ => worker.call(&op.kind, &args, None),
        }
    };
    let done = completion(reply);
    space.complete_op(&op.id, &done)?;
    if done.ok && op.mutating {
        refresh(space, worker, &touched(&done))?;
    }
    Ok(())
}
```
Add to `impl Space` in `space.rs`:
```rust
    pub fn member_name(&self, id: u64) -> String {
        self.conn.db.v_member().iter().find(|m| m.id == id).map(|m| m.name).unwrap_or_default()
    }
```
Add `helper_script: PathBuf` to `HostConfig`, set in `init` with `config::python_says(&python, "__import__('os').path.join(__import__('os').path.dirname(c.__file__), 'helper.py')")?`. In `run`, create `let mut helper = worker::WorkerDriver::new(cfg.cadswarm_python.clone(), cfg.helper_script.clone(), cfg.data_dir.clone(), Duration::from_secs(90));` and pass `&mut helper` to `run_op` and to `orchestrator.tick(&space, &mut helper)`.

- [ ] **Step 2: Seed unknown boards and give the planner memory in `host/src/briefs.rs`**

Change `tick`/`step` to take `helper: &mut WorkerDriver`. In `Action::Seed`, replace the `params` line with:
```rust
                let local = self.boards.get(&b.board).cloned();
                let board = match local {
                    Some(bd) if bd.verified => Some(bd),
                    other => match helper.call("board_spec", &json!({"board": b.board, "question": b.text}), None) {
                        Ok(r) => serde_json::from_value::<Board>(r["result"].clone()).ok().or(other),
                        Err(e) => { eprintln!("[host] board_spec for {}: {e}", b.board); other }
                    },
                };
                let params = board.as_ref().map(seed_params).unwrap_or_default();
```
Add `#[serde(default)] pub verified: bool,` to `Board` in `boards.rs`.
In `Action::Plan`, before spawning, fetch memory and append it to the prompt:
```rust
                let memory = helper
                    .call("recall_designs", &json!({"query": b.text, "board": b.board}), None)
                    .map(|r| r["result"].to_string())
                    .unwrap_or_else(|_| "{}".into());
                let prompt = format!("{}\n\nWhat earlier CadSwarm designs taught us (designs and lessons, JSON): {memory}\n\
                    Apply the lessons in your plan.", crate::agents::planner_prompt(&b.id, &b.text, &label, &params));
```
and pass `&prompt` to `Agent::spawn`. Where the export finishes (`"done"` arm, after `set_brief_stl`), index the design:
```rust
                        let reviews = space.reviews_json(&b.id);
                        let _ = helper.call("index_design", &json!({
                            "brief": {"id": b.id, "text": b.text, "board": b.board, "status": "ready"},
                            "plan": serde_json::from_str::<serde_json::Value>(&space.brief_plan(&b.id)).unwrap_or_default(),
                            "reviews": reviews, "params": space.params_map()}), None);
```
Add to `impl Space`:
```rust
    pub fn brief_plan(&self, id: &str) -> String {
        self.conn.db.v_brief().iter().find(|b| b.id == id).map(|b| b.plan_json).unwrap_or_default()
    }
    pub fn reviews_json(&self, brief: &str) -> serde_json::Value {
        serde_json::Value::Array(self.conn.db.v_review().iter().filter(|r| r.brief == brief)
            .map(|r| serde_json::json!({"round": r.round, "passed": r.passed, "findings_json": r.findings_json})).collect())
    }
    pub fn params_map(&self) -> serde_json::Value {
        serde_json::Value::Object(self.conn.db.v_param().iter().map(|p| (p.alias, serde_json::json!(p.num))).collect())
    }
```
and add `"SELECT * FROM v_param"` to `QUERIES`.

- [ ] **Step 3: Expose the lookups to agents**

In `cadswarm/space_hub.py` add routes and methods:
```python
            ("POST", r"/knowledge/board", self.lookup_board), ("POST", r"/knowledge/designs", self.recall_designs),
```
```python
    def lookup_board(self, body):
        return self.run_op("lookup_board", {"question": body["question"], "board": body.get("board") or ""})

    def recall_designs(self, body):
        return self.run_op("recall_designs", {"query": body["query"], "board": body.get("board") or ""})
```
In `cadswarm/mcp_server.py` add:
```python
@mcp.tool()
def lookup_board(question: str, board: str | None = None) -> dict:
    """Look up a dev board's real dimensions in its datasheet (hole spacing, port positions, PCB size).
    Answers come with source links; prefer them to guessing. `board` narrows to one board id."""
    return call("POST", "/knowledge/board", {"question": question, "board": board}, doing=f"looking up {board or question}")


@mcp.tool()
def recall_designs(query: str, board: str | None = None) -> dict:
    """Similar past CadSwarm designs and the lessons learned from their reviews."""
    return call("POST", "/knowledge/designs", {"query": query, "board": board}, doing="recalling past designs")
```
Add `"lookup_board"` to the reviewer's `REVIEW_TOOLS` list in `cadswarm/reviewer.py` (with the same id prefix), and one line to its prompt: "Use lookup_board to check port cutouts against the board's datasheet."

- [ ] **Step 4: Show sources in the app**

In `web/src/ui/params.ts` (web plan Task 6), after each seeded input, show the source when the brief's plan or the param came from a looked-up board: the host writes `{"sources": {...}}` into `brief.plan_json` at seed time (`space.set_brief(&b.id, "planning", &json!({"sources": board.sources}).to_string())` in `Action::Seed`, adding `#[serde(default)] pub sources: serde_json::Value` to `Board`). Render `from datasheet ↗` links for `pcb`, `holes` and `ports` from that object.

- [ ] **Step 5: End-to-end check**

Build and run the host with Elastic env vars set; submit a brief for `seeed-xiao-esp32c3` (not in `boards.json`). Expected: the host log shows `board_spec`, the Params sheet gets the XIAO's dimensions, the app shows "from datasheet" links, the planner's log mentions lessons when any exist, and after the brief is ready `GET cadswarm-designs/_doc/<brief id>` returns the design.

- [ ] **Step 6: Commit**

```powershell
git add host cadswarm web
git commit -m "feat(elastic): seed unknown boards, give the planner memory, index finished designs"
```
