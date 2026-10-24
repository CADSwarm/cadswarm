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
