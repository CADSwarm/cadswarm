"""Fill the local board knowledge from datasheets and product pages.

    python -m cadswarm.ingest boards/sources.json [--project DIR]

PDF text extraction uses pypdf (pip install pypdf). The extracted text goes into the project's own
`internal/knowledge.db`, where `lookup_board` searches it offline.
"""
import hashlib
import io
import json
import os
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


def ingest(knowledge, sources, fetch=urlopen_bytes):
    n = 0
    for s in sources:
        try:
            data, ctype = fetch(s["url"])
            is_pdf = "pdf" in ctype or s["url"].lower().endswith(".pdf")
            text = pdf_to_text(data) if is_pdf else html_to_text(data.decode("utf-8", "replace"))
        except Exception as e:  # one dead link or unreadable PDF must not stop the rest
            print(f"skip {s['url']}: {e}", file=sys.stderr)
            continue
        text = text[:MAX_CHARS]
        if not text.strip():
            # Scanned and vector-only PDFs extract nothing; indexing a blank document only hides that.
            print(f"skip {s['url']}: no text extracted (scanned or vector-only?)", file=sys.stderr)
            continue
        doc_id = hashlib.sha256(s["url"].encode()).hexdigest()[:20]
        knowledge.index_board({
            "doc_id": doc_id, "board_id": s["board_id"], "title": s["title"], "source_url": s["url"],
            "source_type": s.get("type", "datasheet"), "text": text, "content": text})
        print(f"indexed {s['board_id']}: {s['title']} ({len(text)} chars)")
        n += 1
    return n


def main(argv=None):
    from .knowledge import Knowledge
    from .paths import project_layout
    argv = list(argv or sys.argv[1:])
    project = os.environ.get("CADSWARM_PROJECT") or os.getcwd()
    if "--project" in argv:
        i = argv.index("--project")
        project = argv[i + 1]
        del argv[i:i + 2]
    path = argv[0] if argv else os.path.join("boards", "sources.json")
    with open(path, encoding="utf-8") as f:
        sources = json.load(f)
    knowledge = Knowledge.for_project(project_layout(project))
    print(f"{ingest(knowledge, sources)} documents indexed into {knowledge.path}")


if __name__ == "__main__":
    main()
