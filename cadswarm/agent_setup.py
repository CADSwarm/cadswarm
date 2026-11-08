"""Connecting an AI helper (Claude Code, Codex or OpenCode) in a project folder to a team.

Shared by `cadswarm setup` and the CADSwarm desktop team setup, so stdlib only. Agent
configs launch `cadswarm_launch.py` by absolute path with an explicit Python
(the CADSwarm managed runtime when launched from the desktop), so nothing has to be on PATH or
pip-installed into the agent's environment.
"""
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

from .client import load_json, save_json, state_dir
from .paths import LAUNCHER

BEGIN, END = "<!-- cadswarm:begin -->", "<!-- cadswarm:end -->"
TOML_BEGIN, TOML_END = "# >>> cadswarm (managed by `cadswarm setup`)", "# <<< cadswarm"
# Codex asks before every MCP tool call unless the server's config says otherwise, and a background run
# (`codex exec`) has nobody to answer, so the call is refused outright. `approve` covers the cadswarm
# server only: Codex's own sandbox and the person's other MCP servers keep their settings.
CODEX_APPROVAL_KEY = "default_tools_approval_mode"

CLIENTS = {
    "claude": {"label": "Claude Code", "exe": "claude",
               "install_url": "https://code.claude.com/docs/en/setup"},
    "codex": {"label": "Codex", "exe": "codex", "install_url": "https://learn.chatgpt.com/docs/codex/cli"},
    # Deliberately last: the first installed client in this order is what the guide preselects, so
    # OpenCode stays opt-in and never displaces someone's Claude Code.
    "opencode": {"label": "OpenCode", "exe": "opencode", "install_url": "https://opencode.ai/docs/"},
}

# OpenCode reads AGENTS.md by itself; this is the schema its config wants.
OPENCODE_SCHEMA = "https://opencode.ai/config.json"

AGENT_DOC = """{begin}
## CADSwarm: you are part of a team building one shared CADSwarm model

The shared document is CADSwarm's .cadswarm feature history. All model edits go through the cadswarm
MCP tools; never write the project file directly. The host desktop shows the shared model and is read-only
while the team is active. Keep dimensions in millimetres and explain choices in plain words.

Start with model_status, list_tasks, and get_inbox. Claim a ready task before working. Lock existing
features before changing them; ask their owners before editing work they hold. New output features are
reserved by the host. Use next_step for a planned brief, then claim_task for its task. If no step is ready,
call wait_for_changes.

The standalone model supports constrained line, circle, and arc sketches; box, cylinder, and sphere primitives;
extrude, revolve, pocket, hole, fillet, chamfer, linear and circular patterns, booleans, placement, dimensions,
shared Params expressions, interference checks, and STEP/STL exchange. A failed sketch solve or feature rebuild
is rejected without saving a partial edit. Edge indices can change after upstream edits, so refresh the source
feature before selecting edges.

Use set_param for shared dimensions and bind feature dimensions with expressions such as Params.hole_pitch.
Check check_interference after meaningful geometry changes. Finish each task with report_result, include
feature names and available geometry evidence, and release locks when others need those features.

Read get_inbox often. Handle a person's steer message before other work, reply briefly, and call ack_messages
for messages you handled. Coordinate through team chat and handoffs when another agent owns a feature. Keep notes
and shared project files in the team file tools, not by editing model files directly.

Legacy FreeCAD documents, arbitrary part scripts, assemblies, drawings, FEA, printability validation, lattice
optimization, and the former FreeCAD-dependent component analysis are not supported in this standalone alpha.
If a tool returns unsupported, do not retry it or claim it ran. Team chat, invites, presence, locks, task
planning, and Claude/Codex/OpenCode collaboration remain available.
{end}
"""

RESUME_PROMPT = ("We're continuing our team's CAD project \"{project}\". Check model_status, your inbox and the "
                 "tasks, then carry on with your part. Keep your replies short.")

# AI helpers are always plain members: the people lead the team, and only a person hosts it.
FIRST_PROMPT = ("I just joined our team's CAD project \"{project}\". Claim a task nobody has started and build it. "
                "Keep your replies short: a line when you start, up to three when you finish.")


def upsert_block(path, block, begin=BEGIN, end=END):
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if begin in text and end in text:
        pre, rest = text.split(begin, 1)
        text = pre + block.strip() + rest.split(end, 1)[1]
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block.strip() + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def hook_command(python):
    return f'"{python}" "{LAUNCHER}" hook'


def _ours(command):
    return "cadswarm_launch" in command or "cadswarm.prompt_hook" in command


class ConfigFileError(ValueError, RuntimeError):
    """A config file of the user's we can't safely edit. ValueError for the CLI, RuntimeError so the
    wizard shows the message as-is."""


def _read_config(path):
    """The user's own JSON config as a dict ({} if absent). Never guesses: a file we can't parse
    (comments, trailing comma, wrong shape) is left untouched and reported, because overwriting it
    would silently delete their settings. We stop rather than keep a .bak and carry on: the person
    can fix or move the file and re-run, and nothing they own changes behind their back."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig") as f:  # utf-8-sig: Notepad likes to add a BOM
            text = f.read()
        data = json.loads(text) if text.strip() else {}
        if not isinstance(data, dict):
            raise ValueError("top level isn't an object")
    except (OSError, ValueError) as e:
        raise ConfigFileError(
            f"CADSwarm didn't change {path} because it couldn't read it ({e}). Your settings are untouched. "
            "Fix the file (check for stray commas or comments), or rename it, then run setup again.") from None
    return data


def _dict_at(parent, key, path):
    """parent[key] as a dict, creating it; refuses (rather than crashes on) a value of the wrong type."""
    v = parent.setdefault(key, {})
    if not isinstance(v, dict):
        raise ConfigFileError(f"CADSwarm didn't change {path} because \"{key}\" in it isn't the kind of "
                              "entry it expected. Your settings are untouched; fix or remove that entry and retry.")
    return v


def _list_at(parent, key, path):
    v = parent.setdefault(key, [])
    if not isinstance(v, list):
        raise ConfigFileError(f"CADSwarm didn't change {path} because \"{key}\" in it isn't a list. "
                              "Your settings are untouched; fix or remove that entry and retry.")
    return v


def setup_claude(project, python):
    mcp_path = os.path.join(project, ".mcp.json")
    mcp_cfg = _read_config(mcp_path)
    settings_path = os.path.join(project, ".claude", "settings.json")
    settings = _read_config(settings_path)
    # Validate both files before writing either, so a refusal never leaves a half-done setup.
    _dict_at(mcp_cfg, "mcpServers", mcp_path)["cadswarm"] = {
        "command": python, "args": [LAUNCHER, "mcp"], "env": {"CADSWARM_PROJECT": project}}
    groups = _list_at(_dict_at(settings, "hooks", settings_path), "UserPromptSubmit", settings_path)
    # Idempotent: drop any earlier cadswarm entry, keep everyone else's hooks.
    groups[:] = [g for g in groups if not (isinstance(g, dict) and isinstance(g.get("hooks"), list) and any(
        isinstance(h, dict) and _ours(str(h.get("command", ""))) for h in g["hooks"]))]
    groups.append({"hooks": [{"type": "command", "command": hook_command(python), "timeout": 5}]})
    enabled = _list_at(settings, "enabledMcpjsonServers", settings_path)
    if "cadswarm" not in enabled:
        enabled.append("cadswarm")
    allow = _list_at(_dict_at(settings, "permissions", settings_path), "allow", settings_path)
    if "mcp__cadswarm" not in allow:
        allow.append("mcp__cadswarm")
    with open(mcp_path, "w", encoding="utf-8") as f:
        json.dump(mcp_cfg, f, indent=2)
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    upsert_block(os.path.join(project, "CLAUDE.md"), AGENT_DOC.format(begin=BEGIN, end=END))
    return [mcp_path, settings_path, os.path.join(project, "CLAUDE.md")]


def toml_string(s):
    if "'" in s or "\n" in s:
        return json.dumps(s)  # basic string; JSON escaping is valid TOML for these paths
    return f"'{s}'"


def setup_codex(project, python):
    path = os.path.join(project, ".codex", "config.toml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if TOML_BEGIN in text:
        pre, rest = text.split(TOML_BEGIN, 1)
        text = pre.rstrip() + rest.split(TOML_END, 1)[1]
    for table in ("[hooks]", "[mcp_servers.cadswarm]"):
        if any(line.strip() == table for line in text.splitlines()):
            raise ValueError(f"{path} already defines {table} outside CADSwarm's block; merge it by hand.")
    block = "\n".join([
        TOML_BEGIN,
        "[mcp_servers.cadswarm]",
        f"command = {toml_string(python)}",
        f"args = [{toml_string(LAUNCHER)}, 'mcp']",
        f"env = {{ CADSWARM_PROJECT = {toml_string(project)} }}",
        # A background run (`codex exec`, how the runner wakes a helper) has nobody to answer Codex's
        # approval prompt, so every CADSwarm call failed with "MCP tool call requires approval, but
        # approval policy is never" and the helper looked stuck on a message it could not touch.
        f"{CODEX_APPROVAL_KEY} = 'approve'",
        "tool_timeout_sec = 300  # FEA waits ~75 s and a heavy part script can take minutes; 120 s cut them off",
        "",
        "# UserPromptSubmit injects team context (who holds which parts, what changed) before each prompt.",
        "[hooks]",
        f"UserPromptSubmit = [{{ hooks = [{{ type = 'command', command = {toml_string(hook_command(python))}, "
        "timeout = 5 }] }]",
        TOML_END, ""])
    with open(path, "w", encoding="utf-8") as f:
        f.write((text.rstrip() + "\n\n" if text.strip() else "") + block)
    upsert_block(os.path.join(project, "AGENTS.md"), AGENT_DOC.format(begin=BEGIN, end=END))
    return [path, os.path.join(project, "AGENTS.md")]


def ensure_codex_approval(project):
    """Give an existing helper folder the MCP approval that setup_codex now writes, so a helper set up by
    an older CADSwarm can use the cadswarm tools again (the runner calls this before a wake). Returns True
    when the file changed. Only the cadswarm table is touched, right under its header: comments, other
    servers and Codex's own settings stay exactly as they are."""
    path = os.path.join(project, ".codex", "config.toml")
    try:
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
    except OSError:
        return False
    lines = text.splitlines()
    header = next((i for i, line in enumerate(lines) if line.strip() == "[mcp_servers.cadswarm]"), None)
    if header is None:
        return False
    end = next((i for i in range(header + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    if any(line.split("=", 1)[0].strip() == CODEX_APPROVAL_KEY for line in lines[header + 1:end]):
        return False
    indent = lines[header][:len(lines[header]) - len(lines[header].lstrip())]
    lines.insert(header + 1, f"{indent}{CODEX_APPROVAL_KEY} = 'approve'")
    newline = "\r\n" if "\r\n" in text else "\n"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(newline.join(lines) + (newline if text.endswith(("\n", "\r")) else ""))
    return True


def codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def codex_is_trusted(project):
    path = os.path.join(codex_home(), "config.toml")
    if not os.path.exists(path):
        return False
    text = open(path, encoding="utf-8").read()
    return any(k in text for k in (f"[projects.{toml_string(project)}]", f"[projects.{json.dumps(project)}]"))


def trust_in_codex(project):
    """Codex silently ignores a project's .codex/config.toml until the folder is trusted."""
    if codex_is_trusted(project):
        return False
    path = os.path.join(codex_home(), "config.toml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    block = f"\n# Added by CADSwarm setup so Codex loads this folder's CADSwarm connection.\n" \
            f"[projects.{toml_string(project)}]\ntrust_level = \"trusted\"\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n" + block if text.strip() else block.lstrip())
    return True


def setup_opencode(project, python):
    """opencode.json: the cadswarm MCP server, and AGENTS.md for the instructions (OpenCode reads it
    natively, so unlike Claude and Codex there is no prompt hook to install)."""
    path = os.path.join(project, "opencode.json")
    cfg = _read_config(path)
    _dict_at(cfg, "mcp", path)["cadswarm"] = {
        "type": "local",
        "command": [python, LAUNCHER, "mcp"],
        "environment": {"CADSWARM_PROJECT": project},
        "enabled": True,
        # Keep the timeout long enough for future long-running CAD analysis calls without hiding a real hang.
        "timeout": 300000,
    }
    cfg.setdefault("$schema", OPENCODE_SCHEMA)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    upsert_block(os.path.join(project, "AGENTS.md"), AGENT_DOC.format(begin=BEGIN, end=END))
    return [path, os.path.join(project, "AGENTS.md")]


def redeem(t, name, client):
    """Create a SpacetimeDB identity and become a member with the invite (single use).
    `t` is a decoded ticket. Returns the identity {token, participant_id, fence, name, role, kind}."""
    from .space import SpaceClient
    ident = SpaceClient.new_identity(t["uri"])
    space = SpaceClient(t["uri"], t["db"], ident["token"])
    space.call("redeem_invite", t["token"], name, client, "")
    me = space.sql("SELECT * FROM v_me")[0]
    return {"token": ident["token"], "identity": ident.get("identity", ""), "participant_id": me["id"],
            "fence": me["fence"], "name": me["name"], "role": me["role"], "kind": me["kind"]}


def identity_matches(project, invite):
    """True if `project` already holds a SpacetimeDB sign-in for the team `invite` points at. A sign-in for
    another team, or one left by the old FastAPI hub, doesn't count (it would only be refused)."""
    ident = load_json(os.path.join(state_dir(project), "identity.json")) or {}
    cfg = load_json(os.path.join(state_dir(project), "config.json")) or {}
    return bool(ident.get("token")) and "hub" not in ident and isinstance(ident.get("participant_id"), int) \
        and cfg.get("db") == invite.get("db") and cfg.get("uri", "").rstrip("/") == invite.get("uri", "").rstrip("/")


def write_agent_config(project, client, invite, name, python=None, fresh=False, ephemeral=False, model=None):
    """Everything an AI helper in `project` needs to join the team. Returns the files written.
    `invite` is a decoded ticket (see invite.read_invite). The invite is redeemed only when this
    folder has no identity yet (invites work once), and only after the agent CLI's own config
    files were validated and written, so a config file we refuse to touch never burns the invite.
    `model` pins the helper's model (see save_helper_model); None keeps the folder's earlier pick."""
    if client not in CLIENTS:
        raise ValueError(f"client must be one of {sorted(CLIENTS)}")
    python = python or sys.executable
    project = os.path.abspath(project)
    os.makedirs(state_dir(project), exist_ok=True)
    ident_path = os.path.join(state_dir(project), "identity.json")
    if fresh and os.path.exists(ident_path):
        os.remove(ident_path)
    with open(os.path.join(state_dir(project), ".gitignore"), "w") as f:
        f.write("*\n")  # identity.json holds a SpacetimeDB token
    writers = {"claude": setup_claude, "codex": setup_codex, "opencode": setup_opencode}
    written = writers[client](project, python)
    if not identity_matches(project, invite):
        if not invite.get("token"):
            raise ValueError("This helper folder isn't signed in to this team yet; it needs a fresh invite code.")
        save_json(ident_path, redeem(invite, name, client))
    kept = helper_model(project)
    if model is None:
        model = kept
    if client == "opencode" and not model:
        # "Default" used to mean the CLI's own pick, which can be a provider this computer never signed
        # in to. Follow the person's configured provider/model instead: it is the one that works.
        model = default_model(client)
    save_json(os.path.join(state_dir(project), "config.json"), {
        "uri": invite["uri"].rstrip("/"), "db": invite["db"], "name": name, "client": client,
        "role": invite.get("role") or "member", **({"ephemeral": True} if ephemeral else {})})
    save_helper_model(project, model)
    return written


def opencode_config_paths():
    """Where OpenCode keeps the person's own config, most specific first. Its CLI merges these, so the
    first provider/model found is one the person actually set up and signed in to."""
    paths = []
    if os.environ.get("OPENCODE_CONFIG"):
        paths.append(os.environ["OPENCODE_CONFIG"])
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    paths.append(os.path.join(xdg, "opencode", "opencode.json"))
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(os.path.join(appdata, "opencode", "opencode.json"))
    return paths


def default_model(client):
    """The model an unpinned helper should run on: the person's own first configured OpenCode model, or ""
    when there is none. OpenCode's built-in default can be a provider that was never signed in to (every
    run then died in ~3 s with "Incorrect API key"), so the runner and setup pin this instead."""
    if client != "opencode":
        return ""
    for path in opencode_config_paths():
        cfg = load_json(path) or {}
        if cfg.get("model"):
            return cfg["model"]
        for provider_id, provider in (cfg.get("provider") or {}).items():
            for model_id in (provider.get("models") or {}):
                return f"{provider_id}/{model_id}"
    return ""


def helper_model(project):
    """The model this helper folder was pinned to, or "" for the AI's own default."""
    return (load_json(os.path.join(state_dir(project), "config.json")) or {}).get("model") or ""


def save_helper_model(project, model):
    """Pin (or with "" unpin) the model this helper runs on. It sticks: the runner prefers it over
    CADSWARM_HELPER_MODEL, and OpenCode's own opencode.json gets it too, so the helper opened in a
    terminal uses the same model."""
    model = (model or "").strip()
    path = os.path.join(state_dir(project), "config.json")
    cfg = load_json(path) or {}
    if model:
        cfg["model"] = model
    else:
        cfg.pop("model", None)
    os.makedirs(state_dir(project), exist_ok=True)
    save_json(path, cfg)
    oc_path = os.path.join(project, "opencode.json")
    if cfg.get("client") == "opencode" and os.path.isfile(oc_path):
        oc = _read_config(oc_path)
        if model:
            oc["model"] = model
        else:
            oc.pop("model", None)
        with open(oc_path, "w", encoding="utf-8") as f:
            json.dump(oc, f, indent=2)


def list_models(client):
    """Models to offer in the picker: OpenCode's `opencode models` (provider/model ids). [] when the
    list can't be had (the picker then takes a typed id)."""
    if client == "opencode":
        exe = find_agent_cli("opencode")
        if not exe:
            return []
        try:
            out = subprocess.run([exe, "models"], capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=60, stdin=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            return []
        ids = []
        for line in (out.stdout or "").splitlines():
            line = line.strip()
            if re.fullmatch(r"[\w.-]+/[\w.:@/-]+", line) and line not in ids:
                ids.append(line)
        return ids
    return []


def _node_version(path):
    """(22, 1, 0) for ~/.nvm/versions/node/v22.1.0/bin/<exe>: newest Node first, not "v9" before "v22"."""
    return tuple(int(n) for n in re.findall(r"\d+", os.path.basename(os.path.dirname(os.path.dirname(path)))))


def find_agent_cli(client):
    """Absolute path of the AI helper's command, or None. Looks beyond PATH because apps
    started from the Start menu don't always see what a terminal sees."""
    exe = CLIENTS[client]["exe"]
    found = shutil.which(exe)
    if found:
        return found
    home = os.path.expanduser("~")
    if os.name == "nt":
        candidates = [os.path.join(home, ".local", "bin", exe + ".exe"),
                      os.path.join(home, ".local", "bin", exe)]
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(os.path.join(appdata, "npm", exe + ".cmd"))
    else:
        # Apps started from Finder/the dock or a desktop launcher get a minimal PATH.
        candidates = [os.path.join(home, ".local", "bin", exe)]
        candidates += sorted(glob.glob(os.path.join(home, ".nvm", "versions", "node", "*", "bin", exe)),
                             key=_node_version, reverse=True)
        candidates += [os.path.join(home, ".claude", "local", exe),
                       os.path.join(home, ".npm-global", "bin", exe),
                       os.path.join(home, ".volta", "bin", exe),
                       os.path.join(home, ".bun", "bin", exe),
                       os.path.join("/opt/homebrew/bin", exe),
                       os.path.join("/usr/local/bin", exe),
                       os.path.join("/usr/bin", exe)]
    candidates += vscode_claude(home) if client == "claude" else []
    candidates += opencode_paths() if client == "opencode" else []
    return next((c for c in candidates
                 if os.path.isfile(c) and (os.name == "nt" or os.access(c, os.X_OK))), None)


def opencode_paths():
    """OpenCode installs that PATH misses: the desktop app's bundled CLI (a different name, and not
    on PATH), ~/.opencode/bin, and the usual bun/homebrew folders on Unix."""
    home = os.path.expanduser("~")
    if os.name == "nt":
        names = ["opencode-cli.exe", "opencode.exe", "opencode.cmd"]
        local = os.environ.get("LOCALAPPDATA")
        dirs = [os.path.join(home, ".opencode", "bin")]
        if local:
            dirs.append(os.path.join(local, "Programs", "@opencodedesktop", "resources"))
    else:
        names = ["opencode"]
        dirs = [os.path.join(home, ".opencode", "bin"), os.path.join(home, ".bun", "bin"),
                "/opt/homebrew/bin", "/usr/local/bin"]
    return [os.path.join(d, n) for d in dirs for n in names]


def agent_ready(client):
    """Whether this computer can run `client` now: its CLI is installed."""
    return bool(find_agent_cli(client))


def vscode_claude(home):
    """Claude Code bundled with its VS Code extension, newest version first (some people only have that one).
    Same lookup as the host's agents.rs."""
    def version(path):
        name = os.path.basename(path)[len("anthropic.claude-code-"):]
        return [int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", name)]
    exts = glob.glob(os.path.join(home, ".vscode", "extensions", "anthropic.claude-code-*"))
    exe = "claude.exe" if os.name == "nt" else "claude"
    return [os.path.join(d, "resources", "native-binary", exe) for d in sorted(exts, key=version, reverse=True)]


def _sh_quote(text):
    return "'" + text.replace("'", "'\"'\"'") + "'"


def launcher_argv(client, text):
    """Arguments that start this helper with `text` as its first message. OpenCode's bare argument
    is a directory, so its prompt goes through the flag its TUI expects."""
    return ["--prompt", text] if client == "opencode" else [text]


def write_launcher(project, client, project_name, role="member"):
    """The start script for this helper: starts the agent CLI with its first message, so nobody has to paste it;
    later starts send a short "carry on" message instead."""
    exe = find_agent_cli(client) or CLIENTS[client]["exe"]
    first = FIRST_PROMPT.format(project=project_name)
    resume = RESUME_PROMPT.format(project=project_name)
    sd = state_dir(project)
    if os.name == "nt":
        q = lambda t: '"' + t.replace('"', "'") + '"'  # noqa: E731  (cmd has no way to escape a quote here)
        args = lambda text: " ".join(q(a) for a in launcher_argv(client, text))  # noqa: E731
        text = "\r\n".join([
            "@echo off", "rem Starts your CADSwarm AI helper (written by the CADSwarm guide).",
            f'cd /d "{project}"',
            f'if exist ".cadswarm\\started" ( "{exe}" {args(resume)} & goto :eof )',
            'type nul > ".cadswarm\\started"', f'"{exe}" {args(first)}', ""])
        path = os.path.join(sd, "start-helper.cmd")
    else:
        args = lambda text: " ".join(_sh_quote(a) for a in launcher_argv(client, text))  # noqa: E731
        text = "\n".join([
            "#!/bin/sh", "# Starts your CADSwarm AI helper (written by the CADSwarm guide).",
            f"cd {_sh_quote(project)} || exit 1",
            f"if [ -f .cadswarm/started ]; then exec {_sh_quote(exe)} {args(resume)}; fi",
            "touch .cadswarm/started", f"exec {_sh_quote(exe)} {args(first)}", ""])
        path = os.path.join(sd, "start-helper.sh")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


def open_agent_terminal(client, project):
    """Open a terminal window in `project` running the AI helper (through its start script when the guide wrote
    one, which also sends the first message). Returns the agent's path."""
    exe = find_agent_cli(client)
    if not exe:
        raise FileNotFoundError(f"{CLIENTS[client]['label']} isn't installed on this computer yet.")
    launcher = os.path.join(state_dir(project), "start-helper.cmd" if os.name == "nt" else "start-helper.sh")
    # Not FreeCAD's bundle settings: an AppImage's PYTHONHOME would break any other Python the agent starts.
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
    if os.name == "nt":
        cmd = ["cmd.exe", "/k", launcher if os.path.exists(launcher) else exe]
        subprocess.Popen(cmd, cwd=project, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif sys.platform == "darwin":
        run = (f"sh {shlex.quote(launcher)}" if os.path.exists(launcher)
               else f"cd {shlex.quote(project)} && {shlex.quote(exe)}")
        as_str = run.replace("\\", "\\\\").replace('"', '\\"')  # an AppleScript string literal
        script = f'tell application "Terminal" to do script "{as_str}"'
        subprocess.Popen(["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'])
    else:
        term = next((p for p in (shutil.which(t) for t in (
            "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
            "kitty", "alacritty", "xterm")) if p), None)
        if not term:
            raise FileNotFoundError("Couldn't find a terminal program to open.")
        argv = ["sh", launcher] if os.path.exists(launcher) else [exe]
        name = os.path.basename(os.path.realpath(term))
        if name in ("gnome-terminal", "gnome-terminal.wrapper", "kgx"):
            args = [term, "--", *argv]
        elif name == "xfce4-terminal":
            args = [term, "-x", *argv]
        elif name == "kitty":
            args = [term, *argv]
        else:  # konsole, alacritty, xterm and the rest
            args = [term, "-e", *argv]
        subprocess.Popen(args, cwd=project, env=env)
    return exe
