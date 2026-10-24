"""Elastic client request shapes, against a fake HTTP server (no Elastic account needed)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cadswarm.knowledge import Elastic, ElasticError, design_doc, search_body, validate_spec

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


def test_lessons_are_a_term_query_on_the_board_sorted_by_count(url):
    Elastic(url, url, "k").lessons("raspberry-pi-pico", 3)
    method, path, _xsrf, body = SEEN[-1]
    assert (method, path) == ("POST", "/cadswarm-lessons/_search")
    assert body == {"size": 3, "query": {"term": {"board_id": "raspberry-pi-pico"}}, "sort": [{"count": "desc"}]}
