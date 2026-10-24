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
