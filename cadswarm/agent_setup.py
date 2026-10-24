"""Connecting an AI helper (Claude Code, Codex or Factory Droid) in a project folder to a team.

Shared by `cadswarm setup` and the FreeCAD setup wizard, so stdlib only. Agent
configs launch `cadswarm_launch.py` by absolute path with an explicit Python —
FreeCAD's bundled one for hobbyists — so nothing has to be on PATH or pip-installed
into the agent's environment.
"""
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys

from .client import load_json, save_json, state_dir
from .paths import LAUNCHER

BEGIN, END = "<!-- cadswarm:begin -->", "<!-- cadswarm:end -->"
TOML_BEGIN, TOML_END = "# >>> cadswarm (managed by `cadswarm setup`)", "# <<< cadswarm"

CLIENTS = {
    "claude": {"label": "Claude Code", "exe": "claude",
               "install_url": "https://code.claude.com/docs/en/setup"},
    "codex": {"label": "Codex", "exe": "codex", "install_url": "https://learn.chatgpt.com/docs/codex/cli"},
    "droid": {"label": "Factory Droid", "exe": "droid", "install_url": "https://docs.factory.ai/cli/getting-started/quickstart"},
}

AGENT_DOC = """{begin}
## CadSwarm: you are part of a team building one shared FreeCAD model

Your teammates' agents (Claude Code, Codex, Droid) are editing the same live model right now.
All CAD work goes through the `cadswarm` MCP tools — never edit .FCStd files directly.
Your human may be new to CAD: explain what you build in plain words and keep dimensions in mm.

- Start every task with `model_status` and `list_tasks`; `claim_task` before working.
- `lock_objects` before changing anything; what you create is yours. Ask owners, don't override them.
- Shared dimensions go in the `Params` sheet (`set_param`); bind geometry with expressions
  like `Params.hole_pitch` so a change propagates to every part that depends on it.
- After changes run `check_interference`; coordinate clashes with the other part's owner.
- Read `get_inbox` often, `ack_messages` what you handled. Human `steer` messages come first.
- Save notes, specs, BOMs and reusable scripts with `save_file` in the shared project folder, in
  subfolders per part (`parts/base/notes.md`, `parts/lid/hinge.py`, `docs/bom.csv`). Check
  `list_files` first. Saving never overwrites by accident: to change a file, `read_file` it and
  save with `if_exists="replace"` and its `expected_version`. `save_snapshot` before risky changes.
  Never write files any other way; your own working folder is not shared with the team.
- Finish with `report_result` (evidence: object names, volumes, interference) and release locks.
- Team context (who holds what, what changed) is injected before each of your prompts. If you don't see a
  team block at the start of a turn (Codex only shows it once its human has trusted the hook), call
  `model_status` and `get_inbox` yourself first.
{end}
"""

FIRST_PROMPT = {
    "lead": "You're leading our team's CAD project \"{project}\". Ask me what we're building, then plan the "
            "parts, put the shared sizes in Params, create one task per part for the team, and start on yours.",
    "member": "I just joined our team's CAD project \"{project}\". Check the team's tasks, claim one that "
              "nobody has started, and build it. Tell me in plain words what you're doing.",
}


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
            f"CadSwarm didn't change {path} because it couldn't read it ({e}). Your settings are untouched. "
            "Fix the file (check for stray commas or comments), or rename it, then run setup again.") from None
    return data


def _dict_at(parent, key, path):
    """parent[key] as a dict, creating it; refuses (rather than crashes on) a value of the wrong type."""
    v = parent.setdefault(key, {})
    if not isinstance(v, dict):
        raise ConfigFileError(f"CadSwarm didn't change {path} because \"{key}\" in it isn't the kind of "
                              "entry it expected. Your settings are untouched; fix or remove that entry and retry.")
    return v


def _list_at(parent, key, path):
    v = parent.setdefault(key, [])
    if not isinstance(v, list):
        raise ConfigFileError(f"CadSwarm didn't change {path} because \"{key}\" in it isn't a list. "
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
            raise ValueError(f"{path} already defines {table} outside CadSwarm's block; merge it by hand.")
    block = "\n".join([
        TOML_BEGIN,
        "[mcp_servers.cadswarm]",
        f"command = {toml_string(python)}",
        f"args = [{toml_string(LAUNCHER)}, 'mcp']",
        f"env = {{ CADSWARM_PROJECT = {toml_string(project)} }}",
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
    block = f"\n# Added by CadSwarm setup so Codex loads this folder's CadSwarm connection.\n" \
            f"[projects.{toml_string(project)}]\ntrust_level = \"trusted\"\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n" + block if text.strip() else block.lstrip())
    return True


def setup_droid(project, python):
    path = os.path.join(project, ".factory", "mcp.json")
    cfg = _read_config(path)
    _dict_at(cfg, "mcpServers", path)["cadswarm"] = {
        "type": "stdio", "command": python, "args": [LAUNCHER, "mcp"],
        "env": {"CADSWARM_PROJECT": project}}
    os.makedirs(os.path.dirname(path), exist_ok=True)
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


def write_agent_config(project, client, invite, name, python=None, fresh=False):
    """Everything an AI helper in `project` needs to join the team. Returns the files written.
    `invite` is a decoded ticket (see invite.read_invite). The invite is redeemed only when this
    folder has no identity yet (invites work once), and only after the agent CLI's own config
    files were validated and written, so a config file we refuse to touch never burns the invite."""
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
    writers = {"claude": setup_claude, "codex": setup_codex, "droid": setup_droid}
    written = writers[client](project, python)
    if not load_json(ident_path):
        save_json(ident_path, redeem(invite, name, client))
    save_json(os.path.join(state_dir(project), "config.json"), {
        "uri": invite["uri"].rstrip("/"), "db": invite["db"], "name": name, "client": client,
        "role": invite.get("role") or "member"})
    return written


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
        candidates += sorted(glob.glob(os.path.join(home, ".nvm", "versions", "node", "*", "bin", exe)), reverse=True)
        candidates += [os.path.join(home, ".claude", "local", exe),
                       os.path.join(home, ".npm-global", "bin", exe),
                       os.path.join(home, ".volta", "bin", exe),
                       os.path.join(home, ".bun", "bin", exe),
                       os.path.join("/opt/homebrew/bin", exe),
                       os.path.join("/usr/local/bin", exe),
                       os.path.join("/usr/bin", exe)]
    return next((c for c in candidates
                 if os.path.isfile(c) and (os.name == "nt" or os.access(c, os.X_OK))), None)


def open_agent_terminal(client, project):
    """Open a terminal window in `project` running the AI helper. Returns a short status string."""
    exe = find_agent_cli(client)
    if not exe:
        raise FileNotFoundError(f"{CLIENTS[client]['label']} isn't installed on this computer yet.")
    if os.name == "nt":
        subprocess.Popen(["cmd.exe", "/k", exe], cwd=project, creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif sys.platform == "darwin":
        cmd = f"cd {shlex.quote(project)} && {shlex.quote(exe)}"
        as_str = cmd.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "Terminal" to do script "{as_str}"'
        subprocess.Popen(["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'])
    else:
        term = next((p for p in (shutil.which(t) for t in (
            "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
            "kitty", "alacritty", "xterm")) if p), None)
        if not term:
            raise FileNotFoundError("Couldn't find a terminal program to open.")
        name = os.path.basename(os.path.realpath(term))
        if name in ("gnome-terminal", "gnome-terminal.wrapper", "kgx"):
            args = [term, "--", exe]
        elif name == "konsole":
            args = [term, "-e", exe]
        elif name == "xfce4-terminal":
            args = [term, "-x", exe]
        elif name == "kitty":
            args = [term, exe]
        else:
            args = [term, "-e", exe]
        subprocess.Popen(args, cwd=project)
    return exe
