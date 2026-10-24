"""Stdlib-only client for SpacetimeDB's HTTP API.

Reducers are called with `POST /v1/database/<db>/call/<reducer>` and a JSON array of arguments;
reads are SQL over the module's member-only views (`v_*`). A reducer refusal comes back as a
non-2xx response (530 on 2.13) whose body is the module's `"<code>: <message>"` text. Success is
200 with an empty body.
"""
import json
import urllib.error
import urllib.request


class SpaceError(Exception):
    def __init__(self, status, text):
        self.status = status
        text = (text or "").strip()
        code, sep, message = text.partition(": ")
        if sep and code.replace("_", "").isalnum() and code.islower():
            self.code, self.message = code, message
        else:
            self.code, self.message = ("unreachable" if status == 0 else "error"), text
        super().__init__(f"{self.code}: {self.message}")


def _unwrap(v):
    """SATS JSON wraps options as {"some": v} / {"none": []}; columns here never need more."""
    if isinstance(v, dict) and len(v) == 1:
        if "some" in v:
            return v["some"]
        if "none" in v:
            return None
    return v


def decode_sql(body):
    """`/sql` returns one envelope per statement: {"schema": {"elements": [...]}, "rows": [[...]]}."""
    out = []
    for table in body or []:
        names = [_unwrap(el["name"]) if isinstance(el["name"], dict) else el["name"]
                 for el in table["schema"]["elements"]]
        out += [{n: _unwrap(v) for n, v in zip(names, row)} for row in table["rows"]]
    return out


class SpaceClient:
    def __init__(self, uri, db, token=None, timeout=30):
        self.uri = uri.rstrip("/")
        self.db = db
        self.token = token
        self.timeout = timeout

    @staticmethod
    def _request(url, data, token=None, content_type="application/json", timeout=30):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("content-type", content_type)
        if token:
            req.add_header("authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text.strip() else None
        except urllib.error.HTTPError as e:
            raise SpaceError(e.code, e.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise SpaceError(0, f"Cannot reach SpacetimeDB at {url}: {reason}")

    @staticmethod
    def new_identity(uri, timeout=30):
        """Mint a fresh SpacetimeDB identity: {"identity", "token"}."""
        return SpaceClient._request(uri.rstrip("/") + "/v1/identity", b"", timeout=timeout)

    def call(self, reducer, *args):
        url = f"{self.uri}/v1/database/{self.db}/call/{reducer}"
        self._request(url, json.dumps(list(args)).encode(), self.token, timeout=self.timeout)

    def sql(self, query):
        url = f"{self.uri}/v1/database/{self.db}/sql"
        return decode_sql(self._request(url, query.encode(), self.token, "text/plain", self.timeout))
