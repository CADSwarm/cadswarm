"""Stdlib-only client for SpacetimeDB's HTTP API.

Reducers are called with `POST /v1/database/<db>/call/<reducer>` and a JSON array of arguments;
reads are SQL over the module's member-only views (`v_*`). A reducer refusal comes back as a
non-2xx response (530 on 2.13) whose body is the module's `"<code>: <message>"` text. Success is
200 with an empty body.
"""
import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

# A kept-alive socket idle for longer than this may already be closed by the server: opening a fresh
# one costs ~0.15 s, so throwing away a maybe-dead socket is cheaper than a failed request.
IDLE_SECONDS = 40
POOL_SIZE = 6  # one per reader in a parallel refresh (space_hub.READ_THREADS)


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
        parts = urlsplit(self.uri)
        self._host, self._port = parts.hostname, parts.port
        self._secure = parts.scheme != "http"
        self._idle = []  # [(connection, when it went idle)]: a reused socket skips the TLS handshake
        self._idle_lock = threading.Lock()

    # ---------------------------------------------------------------- connections

    def _open(self):
        cls = http.client.HTTPSConnection if self._secure else http.client.HTTPConnection
        return cls(self._host, self._port, timeout=self.timeout)

    def _take(self):
        now = time.time()
        with self._idle_lock:
            while self._idle:
                conn, since = self._idle.pop()
                if now - since < IDLE_SECONDS and conn.sock is not None:
                    return conn
                conn.close()
        return self._open()

    def _give_back(self, conn):
        if conn.sock is None:
            return
        with self._idle_lock:
            if len(self._idle) < POOL_SIZE:
                self._idle.append((conn, time.time()))
                return
        conn.close()

    def close(self):
        """Drop the kept-alive sockets (a helper that is about to exit does not need them)."""
        with self._idle_lock:
            idle, self._idle = self._idle, []
        for conn, _ in idle:
            conn.close()

    def _post(self, path, data, content_type, repeatable):
        """POST on a kept-alive connection. One query is ~0.25 s over a fresh TLS connection and ~0.09 s
        on a reused one, and a refresh is a dozen queries.

        A socket the server closed while it sat idle is safe to retry while sending (the request never
        arrived). After the request went out only a repeatable read is retried: repeating a reducer could
        post the same message twice."""
        headers = {"content-type": content_type}
        if self.token:
            headers["authorization"] = "Bearer " + self.token
        for attempt in (0, 1):
            conn = self._take()
            try:
                conn.request("POST", path, body=data, headers=headers)
            except (http.client.HTTPException, OSError) as e:
                conn.close()
                if attempt:
                    raise SpaceError(0, f"Cannot reach SpacetimeDB at {self.uri}{path}: {e}")
                continue
            try:
                resp = conn.getresponse()
                text = resp.read().decode("utf-8")
            except (http.client.HTTPException, OSError) as e:
                conn.close()
                if attempt or not repeatable:
                    raise SpaceError(0, f"Cannot reach SpacetimeDB at {self.uri}{path}: {e}")
                continue
            if resp.will_close:
                conn.close()
            else:
                self._give_back(conn)
            if resp.status >= 400:
                raise SpaceError(resp.status, text)
            return json.loads(text) if text.strip() else None

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
        self._post(f"/v1/database/{self.db}/call/{reducer}", json.dumps(list(args)).encode(),
                   "application/json", repeatable=False)

    def sql(self, query):
        return decode_sql(self._post(f"/v1/database/{self.db}/sql", query.encode(), "text/plain",
                                     repeatable=True))
