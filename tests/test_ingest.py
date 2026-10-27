from cadswarm.ingest import html_to_text, ingest


def test_html_keeps_text_and_table_cells_and_drops_scripts():
    html = ("<html><head><script>var x=1</script><style>p{}</style></head><body><h1>XIAO</h1>"
            "<table><tr><td>Dimensions</td><td>21 x 17.8 mm</td></tr></table></body></html>")
    t = html_to_text(html)
    assert "XIAO" in t and "21 x 17.8 mm" in t and "var x" not in t and "p{}" not in t


class FakeKnowledge:
    """Stands in for the project's store: ingest only needs somewhere to put a board doc."""

    def __init__(self):
        self.docs = []

    def index_board(self, doc):
        self.docs.append(doc)


def test_ingest_indexes_one_document_per_source_with_its_metadata():
    src = [{"board_id": "b1", "title": "T", "type": "product_page", "url": "https://x/a.html"}]
    fake = FakeKnowledge()
    n = ingest(fake, src, fetch=lambda url: (b"<p>Board is 50 mm long</p>", "text/html"))
    assert n == 1
    doc, = fake.docs
    assert doc["board_id"] == "b1" and doc["source_url"] == "https://x/a.html"
    assert doc["source_type"] == "product_page" and doc["doc_id"]
    assert "50 mm" in doc["text"] and doc["text"] == doc["content"]


def test_a_source_with_no_extractable_text_is_skipped_not_indexed_blank():
    # Scanned/vector-only PDFs (the Pi 4 mechanical drawing) extract to nothing; a blank document
    # would look like knowledge and answer nothing.
    src = [{"board_id": "b1", "title": "T", "url": "https://x/a.html"}]
    fake = FakeKnowledge()
    n = ingest(fake, src, fetch=lambda url: (b"<script>var x=1</script>", "text/html"))
    assert n == 0 and fake.docs == []


def test_one_dead_link_does_not_stop_the_rest():
    src = [{"board_id": "dead", "title": "Gone", "url": "https://x/gone.html"},
           {"board_id": "b2", "title": "T", "url": "https://x/a.html"}]

    def fetch(url):
        if url.endswith("gone.html"):
            raise OSError("404")
        return b"<p>21 x 17.8 mm</p>", "text/html"

    fake = FakeKnowledge()
    assert ingest(fake, src, fetch=fetch) == 1
    assert [d["board_id"] for d in fake.docs] == ["b2"]
