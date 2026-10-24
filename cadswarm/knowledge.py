"""Elastic for CadSwarm: board knowledge and design memory (stdlib only).

Runs only on the host machine, inside the helper process; the API key never leaves it.
"""
import glob
import json
import os
import re
import time
import urllib.error
import urllib.request

RERANKER = ".jina-reranker-v3.5"
AGENT_ID = "cadswarm-board-expert"


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

    def _req(self, base, method, path, body=None, kibana=False, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method)
        req.add_header("authorization", "ApiKey " + self.api_key)
        req.add_header("content-type", "application/json")
        if kibana:
            req.add_header("kbn-xsrf", "true")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                text = r.read().decode("utf-8")
                return json.loads(text) if text.strip() else {}
        except urllib.error.HTTPError as e:
            raise ElasticError(e.code, e.read().decode("utf-8", "replace"))
        except urllib.error.URLError as e:
            raise ElasticError(0, f"cannot reach Elastic: {e.reason}")

    def es(self, method, path, body=None, timeout=None):
        return self._req(self.es_url, method, path, body, timeout=timeout)

    def kibana(self, method, path, body=None, timeout=None):
        return self._req(self.kibana_url, method, path, body, kibana=True, timeout=timeout)

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

    def search(self, index, query, k=5, filters=None):
        r = self.es("POST", f"/{index}/_search", search_body(query, k, filters))
        return [{"score": h.get("_score"), "source": h.get("_source", {}),
                 "chunks": h.get("fields", {}).get("content", [])} for h in r.get("hits", {}).get("hits", [])]

    def index_design(self, doc):
        self.es("PUT", f"/cadswarm-designs/_doc/{doc['brief_id']}", doc)

    def lessons(self, board_id, k=5):
        """Recurring problems for a board, most frequent first (the lessons index has no text to search)."""
        r = self.es("POST", "/cadswarm-lessons/_search",
                    {"size": k, "query": {"term": {"board_id": board_id}}, "sort": [{"count": "desc"}]})
        return [h.get("_source", {}) for h in r.get("hits", {}).get("hits", [])]

    def install_agent(self, folder="elastic"):
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

    def converse(self, text, timeout=180):
        # Agent Builder runs a model and its tools, so it needs far longer than a normal API call.
        r = self.kibana("POST", "/api/agent_builder/converse", {"input": text, "agent_id": AGENT_ID},
                        timeout=timeout)
        # The reply text lives under response.message in the converse API; fall back to the whole body.
        return (r.get("response") or {}).get("message") or json.dumps(r)

    def board_spec(self, board_id, question=""):
        ask = (f"Give the board spec JSON for {board_id}." + (f" Context: {question}" if question else ""))
        return validate_spec(_first_json_object(self.converse(ask)))


def search_body(query, k=5, filters=None):
    """Lexical + semantic (JINA embeddings) fused with RRF, then re-ranked by the JINA reranker."""
    flt = [{"term": {f: v}} for f, v in (filters or {}).items()]

    def leg(field):
        return {"standard": {"query": {"bool": {"must": [{"match": {field: query}}], "filter": flt}}}}

    return {
        "size": k,
        "retriever": {"text_similarity_reranker": {
            # The reranker's window may not exceed the inner rrf's (ES rejects it otherwise).
            "retriever": {"rrf": {"retrievers": [leg("text"), leg("content")], "rank_window_size": 50}},
            "field": "text", "inference_id": RERANKER, "inference_text": query, "rank_window_size": 50}},
        "fields": [{"field": "content", "format": "chunks"}],
        "_source": {"excludes": ["content", "text"]},
    }


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
    hole_d = float(raw.get("hole_d") or 0)
    if holes and not 0.5 <= hole_d <= 8:
        raise ValueError(f"hole_d looks wrong for mounting holes: {raw.get('hole_d')}")
    ports = []
    for p in raw.get("ports", []):
        if not isinstance(p, dict) or p.get("side") not in ("-x", "+x", "-y", "+y"):
            raise ValueError(f"port {p.get('name') if isinstance(p, dict) else p} has no valid side")
        try:
            ports.append({"name": re.sub(r"\W+", "_", str(p["name"]).lower()), "side": p["side"],
                          "center": [float(v) for v in p["center"]], "size": [float(v) for v in p["size"]]})
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"port {p.get('name')} needs a name, center and size ({e})")
    return {"label": str(raw.get("label", "")), "pcb": pcb, "holes": holes, "hole_d": hole_d,
            "clear_top": float(raw.get("clear_top", 0)) or 10.0, "clear_bottom": float(raw.get("clear_bottom", 0)) or 3.0,
            "ports": ports, "sources": src, "verified": False}
