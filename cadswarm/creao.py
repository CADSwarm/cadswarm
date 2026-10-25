"""Creao AI's developer API, in the small slice CadSwarm uses.

Creao has no local CLI and cannot be given a custom MCP server (its connectors are a fixed OAuth
catalog), so a CadSwarm helper on Creao is a local bridge: `cadswarm.creao_agent` runs here, asks
Creao for one JSON tool call per turn, and executes that call in-process with the same MCP tools
every other helper sees.

Stdlib only, like agent_setup: this runs with FreeCAD's Python and in `cadswarm setup`.
`CREAO_API_KEY` is read from the process environment (or `cadswarm.env`) at call time and never
written anywhere. The account's Creao agent id is cached once per computer (`shared_path`), and each
helper folder keeps only its own conversation in `.cadswarm/creao.json`.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .client import load_json, save_json, state_dir

DEFAULT_BASE = "https://developer.creao.ai"
APP_BASE = "https://agent.creao.ai"  # an agent built in the browser, run by its `capi_` key
TERMINAL = ("completed", "failed", "cancelled")

# What `setup` asks Creao to build for a helper. Natural language, because POST /v1/agents is a
# creation run, not a template. It names the one structured input (`message`) that every /v1/runs call
# sends, so the agent the account makes accepts our runs.
AGENT_DESCRIPTION = (
    "Create a personal agent named CadSwarm Helper that accepts one text input named `message`. "
    "Each run's `message` contains a CAD task, the result of its last tool call, or a request to "
    "repair its reply. It works on one shared FreeCAD model through a local bridge: it cannot call "
    "tools directly, so every reply is exactly one JSON object, "
    "{\"tool\": \"<name>\", \"args\": {...}} to call a CadSwarm tool, or "
    "{\"done\": true, \"summary\": \"...\"} when its task is finished. It never replies with prose "
    "or markdown.")

# Wait between polls: quick at first (short runs finish fast), never more than a couple of seconds.
FIRST_POLL_SECONDS = 0.5
MAX_POLL_SECONDS = 2.0

# The one-line contract repeated in every run's input, so a missing or stale `cadswarm-tools` skill
# cannot break the loop. The full tool catalog is added on the first turn of a conversation.
PROTOCOL = """You are a CadSwarm AI helper. You cannot call tools directly: every reply is exactly ONE JSON
object, with no prose, no markdown fence and no text around it.

To use a CadSwarm tool, reply:
{"tool": "<tool name>", "args": {<arguments>}}

To finish, reply:
{"done": true, "summary": "<one or two sentences: what changed, in plain words>"}

The tool result comes back in the next message; then reply with the next JSON object. Use one tool per
reply. The bridge runs on the teammate's computer, outside your sandbox: don't look for it, don't
simulate the steps after this one, and don't wait for a connector. Your reply IS the tool call. Never invent a tool or an argument. Never ask questions (this helper runs headlessly): make a
reasonable choice and say so in your summary."""

# The account-level Creao skill, reinstalled (upserted by name) by every Creao helper's setup. It
# teaches the same protocol from inside Creao's sandbox, with a validator the agent can run itself.
SKILL_NAME = "cadswarm-tools"
SKILL_MD = f"""---
name: {SKILL_NAME}
description: Work on a CadSwarm team's shared FreeCAD model, one JSON tool call per turn.
---

# CadSwarm tools

You are a CadSwarm AI helper: a small program on a teammate's computer executes each tool call you
ask for against the team's shared FreeCAD model and sends the result back.

## Reply format

{PROTOCOL}

## Which tools exist

The CadSwarm bridge adds the current tool catalog (names, descriptions and argument schemas) to the
first message of every conversation. Do not hard-code tool names from memory: only use the tools in
that catalog, with the argument names it lists.

## Checking your reply

`python scripts/validate_turn.py` reads your reply on stdin and prints `ok` or what is wrong. Run it
before sending a reply when you are unsure."""

VALIDATE_TURN = '''"""Reject a reply that is not exactly one CadSwarm turn."""
import json
import sys


def check(text):
    try:
        turn = json.loads(text)
    except ValueError:
        return "not JSON"
    if not isinstance(turn, dict):
        return "expected a JSON object"
    if turn.get("done") is True:
        if set(turn) - {"done", "summary"}:
            return "a done turn takes only done and summary"
        return None
    if not isinstance(turn.get("tool"), str) or not turn["tool"]:
        return 'expected {"tool": "<name>", "args": {...}} or {"done": true, "summary": "..."}'
    if not isinstance(turn.get("args", {}), dict):
        return '"args" must be a JSON object'
    return None


if __name__ == "__main__":
    problem = check(sys.stdin.read())
    print(problem or "ok")
    sys.exit(1 if problem else 0)
'''


class CreaoError(Exception):
    """A Creao request failed. `code` is Creao's stable error code when it sent one."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def api_key():
    """The Creao key for this process, from the environment or a CadSwarm env file (never a folder)."""
    key = (os.environ.get("CREAO_API_KEY") or "").strip()
    if key:
        return key
    load_env_file()
    return (os.environ.get("CREAO_API_KEY") or "").strip()


def env_candidates():
    """Where a CadSwarm env file may live, in the order the loaders read them."""
    from .paths import app_dir
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [os.environ.get("CADSWARM_ENV_FILE"),
            os.path.join(app_dir(), "cadswarm.env"),  # written by the GUI's API-key dialog
            os.path.join(root, ".env"),
            os.path.join(os.path.dirname(root), "cadswarm.env")]


def env_file():
    """The env file to write a key to: the one named explicitly, else the first that exists, else the
    per-user file the GUI creates."""
    explicit = os.environ.get("CADSWARM_ENV_FILE")
    if explicit:
        return explicit
    for path in env_candidates()[1:]:
        if os.path.isfile(path):
            return path
    return env_candidates()[1]


def save_api_key(key):
    """Save CREAO_API_KEY in the env file this computer reads (never in a project folder).

    Replaces an existing line in place, keeps every other key and comment, and makes the key visible
    to this process at once. Returns the file written.
    """
    key = (key or "").strip()
    if not key:
        raise ValueError("Paste the Creao API key first.")
    path = env_file()
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    out, seen = [], False
    for line in lines:
        name = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if name == "CREAO_API_KEY":
            if not seen:
                out.append(f"CREAO_API_KEY={key}")
                seen = True
            continue  # drop an older duplicate
        out.append(line)
    if not seen:
        if out and out[-1].strip():
            out.append("")
        out.append(f"CREAO_API_KEY={key}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, path)
    os.environ["CREAO_API_KEY"] = key
    return path


def load_env_file():
    """Read keys from the same files `scripts/with-env.ps1` uses, without overriding real env vars.

    Custom skills run in Creao's sandbox, but the bridge runs here: when FreeCAD was started from the
    desktop (not through the loader) a key in `cadswarm.env` is still found by this. The host helper's
    Elastic client (`knowledge.Elastic.from_env`) reads its keys through this too.
    """
    for path in env_candidates():
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name, value = name.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if name and name not in os.environ:
                os.environ[name] = value
        return  # the first file that exists is the one the loader would use


def config_path(project):
    return os.path.join(state_dir(project), "creao.json")


def shared_path():
    """The one Creao agent this computer uses, shared by every Creao helper (id only, never a key)."""
    from .paths import app_dir
    return os.path.join(app_dir(), "creao.json")


def load_shared():
    return load_json(shared_path(), {}) or {}


def save_shared(agent_id):
    save_json(shared_path(), {"agent_id": agent_id})


def load_config(project):
    """Cached ids for this helper: agent_id, conversation_id, model. Never the API key."""
    return load_json(config_path(project), {}) or {}


def save_config(project, **fields):
    cfg = load_config(project)
    cfg.update({k: v for k, v in fields.items() if v is not None})
    save_json(config_path(project), cfg)
    return cfg


def clear_conversation(project):
    """Forget the cached conversation (the next task starts a new one); keep the Creao agent."""
    path = config_path(project)
    cfg = load_config(project)
    if cfg.pop("conversation_id", None) is None:
        return False
    save_json(path, cfg)
    return True


def _urllib(request, timeout):
    """(status, headers, body). HTTP errors are responses here, not exceptions: their body says why."""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


class Creao:
    """The few endpoints a CadSwarm helper needs. `opener` is injectable for tests."""

    def __init__(self, key=None, base_url=None, opener=None, sleep=time.sleep, clock=time.monotonic,
                 request_timeout=60, rate_limit_retries=2):
        self.key = (key if key is not None else api_key()).strip()
        self.base = (base_url or os.environ.get("CREAO_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.opener = opener or _urllib
        self.sleep = sleep
        self.clock = clock
        self.request_timeout = request_timeout
        self.rate_limit_retries = rate_limit_retries

    # ------------------------------------------------------------------ transport

    def _request(self, method, path, body=None):
        if not self.key:
            raise CreaoError("CREAO_API_KEY isn't set on this computer. Create a cr_sk_ key in Creao's "
                             "Developer Console and add it to cadswarm.env (see .env.example).")
        data = None if body is None else json.dumps(body).encode("utf-8")
        for attempt in range(self.rate_limit_retries + 1):
            request = urllib.request.Request(self.base + path, data=data, method=method, headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                # Creao's Cloudflare refuses the default "Python-urllib/x.y" with 403 (error 1010).
                "User-Agent": "cadswarm/creao-bridge",
            })
            try:
                status, headers, raw = self.opener(request, self.request_timeout)
            except (urllib.error.URLError, OSError) as e:
                raise CreaoError(f"Couldn't reach Creao at {self.base}: {e}") from e
            payload = _json(raw)
            if 200 <= status < 300:
                return payload
            code = str(payload.get("error") or "")
            if status == 429 and attempt < self.rate_limit_retries:
                self.sleep(_retry_after(headers, payload))
                continue
            raise CreaoError(_message(status, code, payload), code)
        raise CreaoError("Creao is rate limiting this account (RATE_LIMITED); try again in a minute.", "RATE_LIMITED")

    # ----------------------------------------------------------------------- API

    def usage(self):
        """A cheap authenticated call: proves the key works without spending credits on a run."""
        return self._request("GET", "/v1/account/usage")

    def list_models(self):
        """Model ids this account can run, for the helper's model picker (in Creao's order, no repeats).
        Accepts {"data": [...]}, {"models": [...]} or a bare list, of ids or {"id"/"name"} objects."""
        payload = self._request("GET", "/v1/models")
        items = payload if isinstance(payload, list) else (payload.get("data") or payload.get("models") or [])
        ids = []
        for item in items:
            mid = item if isinstance(item, str) else (item.get("id") or item.get("name") or "") \
                if isinstance(item, dict) else ""
            if mid and mid not in ids:
                ids.append(mid)
        return ids

    def create_agent(self, description, model=None, timeout=15 * 60):
        """Start Creao's agent-creation run and wait for the new agent's id."""
        body = {"input": description}
        if model:
            body["model"] = model
        run = self._request("POST", "/v1/agents", body)
        done = self.wait(run["run_id"], timeout=timeout)
        agent_id = ((done.get("result") or {}).get("agent_id") or done.get("agent_id") or "")
        if done.get("status") != "completed" or not agent_id:
            raise CreaoError(f"Creao couldn't create the helper's agent (run {run['run_id']} "
                             f"{done.get('status')}{_error_suffix(done)})", _run_code(done))
        return agent_id

    def run(self, agent_id, input, conversation_id=None, model=None):
        """Start one agent run. Returns (run_id, conversation_id)."""
        body = {"agent_id": agent_id, "input": input}
        if conversation_id:
            body["conversation_id"] = conversation_id
        if model:
            body["model"] = model
        run = self._request("POST", "/v1/runs", body)
        return run["run_id"], run.get("conversation_id")

    def wait(self, run_id, timeout=15 * 60):
        """Poll until the run ends (completed, failed or cancelled) and return it."""
        started = self.clock()
        delay = FIRST_POLL_SECONDS
        while True:
            run = self._request("GET", f"/v1/runs/{urllib.parse.quote(run_id, safe='')}?raw_output=true")
            if run.get("status") in TERMINAL:
                return run
            elapsed = self.clock() - started
            if elapsed >= timeout:
                raise CreaoError(f"Creao run {run_id} didn't finish within {timeout / 60:.0f} minutes "
                                 f"({run.get('status') or 'no status'}); it may still be running on Creao.")
            self.sleep(min(delay, timeout - elapsed))
            delay = min(MAX_POLL_SECONDS, delay * 1.5)

    def update_agent(self, agent_id, **fields):
        return self._request("PATCH", f"/v1/agents/{urllib.parse.quote(agent_id, safe='')}", fields)

    def install_skill(self, content, files=None, enabled=True):
        """Install (or upsert) an account-level skill from SKILL.md content. Name comes from the front matter."""
        return self._request("POST", "/v1/skills", {
            "source": "content", "content": content, "files": files or {}, "enabled": enabled})


class AppTrigger(Creao):
    """An agent built on agent.creao.ai, run through its API Trigger with that agent's `capi_` key.

    Same run/wait shape as the /v1 client, so the bridge drives either. The agent's model, skill and
    autonomy are set on its page, and its runs (shareable there) continue a thread via `threadId`.
    """

    def __init__(self, key=None, base_url=None, **kw):
        super().__init__(key=key, base_url=base_url or os.environ.get("CREAO_APP_BASE_URL") or APP_BASE, **kw)
        self._apps = {}  # run id -> agent id: polling a run needs both

    def _path(self, agent_id, *rest):
        return "/".join(["/api/v1/apps", urllib.parse.quote(agent_id, safe="")]
                        + [urllib.parse.quote(part, safe="") for part in rest])

    def usage(self):
        return {}  # no account endpoint here; the first run proves the key

    def list_models(self):
        return []  # the model is the agent's own setting on agent.creao.ai

    def run(self, agent_id, input, conversation_id=None, model=None):
        body = {"inputs": input}
        if conversation_id:
            body["threadId"] = conversation_id
        run = self._request("POST", self._path(agent_id, "runs"), body)
        self._apps[run["id"]] = agent_id
        return run["id"], None  # the thread id arrives with the finished run

    def wait(self, run_id, timeout=15 * 60):
        path = self._path(self._apps[run_id], "runs", run_id)
        started = self.clock()
        delay = FIRST_POLL_SECONDS
        while True:
            run = self._request("GET", path)
            if run.get("status") in TERMINAL:
                return {"status": run["status"], "conversation_id": run.get("threadId"),
                        "result": {"text": turn_text(run.get("result"))},
                        "error": {"code": run.get("errorCode")}}
            elapsed = self.clock() - started
            if elapsed >= timeout:
                raise CreaoError(f"Creao run {run_id} didn't finish within {timeout / 60:.0f} minutes "
                                 f"({run.get('status') or 'no status'}); it may still be running on Creao.")
            self.sleep(min(delay, timeout - elapsed))
            delay = min(MAX_POLL_SECONDS, delay * 1.5)


def client(key=None):
    """The client for this computer's key: an agent.creao.ai `capi_` key runs that agent's API Trigger."""
    key = (key if key is not None else api_key()).strip()
    return AppTrigger(key=key) if is_app_key(key) else Creao(key=key)


def is_app_key(key):
    return (key or "").strip().startswith("capi_")


def turn_text(result):
    """The reply's CadSwarm turn. An App Trigger run narrates in `chatText`, which may also quote example
    turns, and its `text` is a structured echo that names the turn it meant ({"tool": "add_box",
    "done": "false"}) without its args. So: the chat's turns that match that echo, then one in a json
    fence, then the last."""
    result = result or {}
    chat = str(result.get("chatText") or "")
    turns = _turns(chat)
    meant = (_turns(str(result.get("text") or ""), strict=False) or [(None, None, {})])[-1][2]
    if str(meant.get("done")).lower() == "true" or meant.get("tool") == "done":
        turns = [t for t in turns if t[2].get("done") is True] or turns
    elif isinstance(meant.get("tool"), str):
        turns = [t for t in turns if t[2].get("tool") == meant["tool"]] or turns
    fenced = [t for t in turns if chat.count("```", 0, t[0]) % 2 == 1]
    if turns:
        start, end, _ = (fenced or turns)[-1]
        return chat[start:end]
    return str(result.get("text") or chat)


def _turns(text, strict=True):
    """(start, end, object) for each JSON object in text that is a CadSwarm turn (any object if not strict)."""
    decoder, found, start = json.JSONDecoder(), [], 0
    while True:
        start = text.find("{", start)
        if start < 0:
            return found
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            start += 1
            continue
        if isinstance(obj, dict) and (not strict or "tool" in obj or obj.get("done") is True):
            found.append((start, end, obj))
        start = end


def _json(raw):
    try:
        value = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _retry_after(headers, payload):
    for raw in ((headers or {}).get("Retry-After"), payload.get("retryAfterSeconds")):
        try:
            return max(0.0, min(60.0, float(raw)))
        except (TypeError, ValueError):
            continue
    return 5.0


def _run_code(run):
    return str(((run.get("error") or {}).get("code")) or "")


def _error_suffix(run):
    code = _run_code(run)
    return f": {code}" if code else ""


def _message(status, code, payload):
    if code == "AUTHENTICATION_REQUIRED":
        return "Creao rejected the API key (AUTHENTICATION_REQUIRED). Check CREAO_API_KEY in cadswarm.env."
    if code == "INSUFFICIENT_CREDITS":
        detail = ""
        if payload.get("available") is not None or payload.get("required") is not None:
            detail = f" ({payload.get('available', '?')} available, {payload.get('required', '?')} required)"
        return f"Creao has no credits left for this run (INSUFFICIENT_CREDITS{detail})."
    if code == "RATE_LIMITED":
        return "Creao is rate limiting this account (RATE_LIMITED); try again in a minute."
    if code in ("MODEL_NOT_SUPPORTED", "MODEL_NOT_ALLOWED"):
        return f"Creao can't run this model on your plan ({code}). Change CREAO_MODEL or unset it."
    if code == "ACCOUNT_BLOCKED":
        return "Creao has blocked this account from starting runs (ACCOUNT_BLOCKED)."
    if code == "CONVERSATION_NOT_FOUND":
        return "Creao no longer knows this helper's conversation (CONVERSATION_NOT_FOUND)."
    if code == "AGENT_NOT_FOUND":
        return "Creao no longer knows this helper's agent (AGENT_NOT_FOUND). Run `cadswarm setup --fresh`."
    if code:
        return f"Creao refused the request ({code})."
    return f"Creao refused the request (HTTP {status}); its service may be unavailable right now."
