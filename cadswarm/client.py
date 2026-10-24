"""Stdlib-only hub client, shared by the MCP server, the prompt hook and the
FreeCAD workbench (which runs in FreeCAD's own Python, without our venv).

A project folder that has been set up holds two files:
  .cadswarm/config.json    {hub, workspace_token, name, client, role}   written by `cadswarm setup`
  .cadswarm/identity.json  {participant_id, token, fence, ...}          written on join
Re-joining with the stored identity resumes the same participant at fence+1,
which makes any older process using that identity unable to act.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class HubRequestError(Exception):
    def __init__(self, status, body):
        self.status, self.body = status, body if isinstance(body, dict) else {"error": str(body)}
        super().__init__(self.body.get("error", f"HTTP {status}"))

    @property
    def code(self):
        return self.body.get("code")


def state_dir(project_dir):
    return os.path.join(project_dir, ".cadswarm")


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
    os.replace(tmp, path)


class HubClient:
    def __init__(self, hub_url, identity=None, timeout=60):
        self.hub = hub_url.rstrip("/")
        self.identity = identity or {}
        self.timeout = timeout

    def headers(self):
        h = {"content-type": "application/json"}
        if self.identity.get("participant_id") and self.identity.get("token"):
            h["x-participant"] = self.identity["participant_id"]
            h["authorization"] = "Bearer " + self.identity["token"]
            if self.identity.get("fence") is not None:  # a saved sign-in being resumed has none yet
                h["x-fence"] = str(self.identity["fence"])
        return h

    def request(self, method, path, body=None, params=None, timeout=None, raw=False):
        url = self.hub + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self.headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                payload = resp.read()
                return payload if raw else json.loads(payload or b"{}")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            try:
                body = json.loads(text)
            except ValueError:
                body = {"error": text}
            raise HubRequestError(e.code, body)
        except urllib.error.URLError as e:
            raise HubRequestError(0, {"error": f"Cannot reach the CadSwarm hub at {self.hub}: {e.reason}",
                                      "code": "unreachable"})

    def get(self, path, **params):
        return self.request("GET", path, params=params or None)

    def post(self, path, body=None):
        return self.request("POST", path, body if body is not None else {})

    def join(self, workspace_token, name, kind="agent", client=None, role="member"):
        resume = None
        if self.identity.get("participant_id"):
            resume = {"id": self.identity["participant_id"], "token": self.identity["token"]}
        ident = self.post("/join", {"workspace_token": workspace_token, "name": name, "kind": kind,
                                    "client": client, "role": role, "resume": resume})
        self.identity = ident
        return ident


def project_client(project_dir):
    """Client for a set-up project folder, using (but not refreshing) its stored identity."""
    cfg = load_json(os.path.join(state_dir(project_dir), "config.json"))
    if not cfg:
        return None, None
    ident = load_json(os.path.join(state_dir(project_dir), "identity.json"), {})
    return HubClient(cfg["hub"], ident), cfg


def find_project_dir(start):
    """Walk up from `start` to the folder holding .cadswarm/config.json."""
    d = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(d, ".cadswarm", "config.json")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
