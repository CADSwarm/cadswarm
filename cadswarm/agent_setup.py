"""Connecting an AI helper (Claude Code, Codex, Factory Droid or OpenCode) in a project folder to a team.

Also Creao AI, which has no local CLI: its runs go through a small adapter (cadswarm.creao_agent)
rather than a config file for a terminal helper.

Shared by `cadswarm setup` and the FreeCAD setup wizard, so stdlib only. Agent
configs launch `cadswarm_launch.py` by absolute path with an explicit Python
(FreeCAD's bundled one for hobbyists), so nothing has to be on PATH or pip-installed
into the agent's environment.
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

CLIENTS = {
    "claude": {"label": "Claude Code", "exe": "claude",
               "install_url": "https://code.claude.com/docs/en/setup"},
    "codex": {"label": "Codex", "exe": "codex", "install_url": "https://learn.chatgpt.com/docs/codex/cli"},
    "droid": {"label": "Factory Droid", "exe": "droid", "install_url": "https://docs.factory.ai/cli/getting-started/quickstart"},
    # Deliberately last: the first installed client in this order is what the guide preselects, so
    # OpenCode stays opt-in and never displaces someone's Claude Code.
    "opencode": {"label": "OpenCode", "exe": "opencode", "install_url": "https://opencode.ai/docs/"},
    # No local CLI: Creao AI runs through a local bridge (cadswarm.creao_agent) and needs its API key.
    "creao": {"label": "Creao AI", "exe": None, "install_url": "https://developer.creao.ai/",
              "description": "Use various frontier models"},
}

# OpenCode reads AGENTS.md by itself; this is the schema its config wants.
OPENCODE_SCHEMA = "https://opencode.ai/config.json"

AGENT_DOC = """{begin}
## CADSwarm: you are part of a team building one shared FreeCAD model

Your teammates' AI helpers are editing the same live model right now.
All CAD work goes through the `cadswarm` MCP tools: never edit .FCStd files directly.
Your human may be new to CAD: keep dimensions in mm and use plain words.

**Be brief.** People watch the model change live, so don't narrate: no running commentary, no restating tool
calls or their results, no plans-before-acting. Reply in one or two short sentences; when you finish, say what
changed in at most three lines. A person's COMMAND from FreeCAD or the web app gets a one-line answer.

- Start every task with `model_status` and `list_tasks`; `claim_task` before working.
- `lock_objects` before changing anything; what you create is yours. Ask owners, don't override them.
- Shared dimensions go in the `Params` sheet (`set_param`); bind geometry with expressions
  like `Params.hole_pitch` so a change propagates to every part that depends on it. When parts
  already repeat a size, `auto_constrain` previews the shared sizes, equal-spacing patterns and
  shaft/hole fits it can infer; apply the ones you mean.
- After changes run `check_interference`; coordinate clashes with the other part's owner.
- Read `get_inbox` often, `ack_messages` what you handled. A person's message to you (kind `steer`, sent
  from FreeCAD's chat) comes first. Answer like a teammate in a chat: a short `send_message` saying what
  you'll do, do it, then a short one saying what you did. People's own chat never reaches you. When you
  run out of work, keep calling `wait_for_changes` so new messages and tasks reach you. (If FreeCAD started you in the background, stop instead: you're woken when needed.)
- Work with the other AI helpers: `list_helpers` shows who is free; `delegate_task` hands one of them a
  task and wakes it; ask the helper who holds a part (`send_message`) instead of touching it. Free while
  a busy helper has tasks queued? `pick_up_task` takes one off its hands (it's told to skip it).
- Save notes, specs, BOMs and reusable scripts with `save_file` in the shared project folder, in
  subfolders per part (`parts/base/notes.md`, `parts/lid/hinge.py`, `docs/bom.csv`). Check
  `list_files` first. Saving never overwrites by accident: to change a file, `read_file` it and
  save with `if_exists="replace"` and its `expected_version`. `save_snapshot` before risky changes.
  Never write files any other way; your own working folder is not shared with the team.
- Finish with `report_result` (evidence: object names, volumes, interference) and release locks.
- Strength, stiffness, vibration or heat questions ("will it hold?", stress, load, natural frequency,
  temperature) are FEA tasks. First ask, in one message: which analysis (recommend **linear static**
  unless it's about vibration or heat), the material, which faces are held still, and the loads or
  temperatures; offer to use clearly marked placeholders if your human doesn't know. Pick faces with
  `list_faces`, run `run_fea` (then `fea_status` while it runs), explain the result plainly, and always
  `publish_fea_report` with a summary and 2-5 points of improvement: it pops up for the whole team.
- Real parts come from the team's components library: `recommend_components` (say what the part must
  do) or `search_components`, then `insert_component` for team parts or `add_standard_part` for standard
  screws, nuts, washers, heat-set inserts, bearings, dowel pins and magnets. A part file you find online
  goes in with `import_component_from_url` (a direct .step/.stl link, plus its license). Size mating holes
  with `hole_size`, and bind gaps to the team's fits (`Params.tol_clearance`, `Params.tol_press_fit`...).
- Team settings (`get_settings`: fits, bolt-hole class, thread mode, printing, mesh precision) are chosen by
  people. When a part implies a different value, `suggest_setting` with a one-line reason; never assume it.
- Team context (who holds what, what changed) is injected before each of your prompts. If you don't see a
  team block at the start of a turn (Codex only shows it once its human has trusted the hook), call
  `model_status` and `get_inbox` yourself first.
{end}
"""

CREAO_DOC = """{begin}
## CadSwarm: you are part of a team building one shared FreeCAD model

This folder belongs to a Creao AI helper. The CadSwarm bridge on this computer sends Creao one wake-up
prompt per task and executes each tool call Creao asks for against the team's shared model.

Creao has no direct tool access: every reply must be exactly one JSON object,
`{{"tool": "<name>", "args": {{...}}}}` to act or `{{"done": true, "summary": "..."}}` to finish. The
bridge adds the current tool catalog to the first message of each conversation. Never edit .FCStd files
directly: all CAD work goes through the CadSwarm tools.
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
        "tool_timeout_sec = 120  # FEA tool calls wait up to ~75 s for the solver",
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
    block = f"\n# Added by CADSwarm setup so Codex loads this folder's CADSwarm connection.\n" \
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
        # FEA and lattice tool calls block while the solver runs (~75 s); the default 30 s is too short.
        "timeout": 120000,
    }
    cfg.setdefault("$schema", OPENCODE_SCHEMA)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    upsert_block(os.path.join(project, "AGENTS.md"), AGENT_DOC.format(begin=BEGIN, end=END))
    return [path, os.path.join(project, "AGENTS.md")]


def setup_creao(project, python):
    """Creao has no config file for a terminal helper: resolve the Creao agent (one per computer/account,
    shared by every helper; cached ids only, never the key), install the account-level tool skill, and
    let full-auto writes through.

    This runs before the invite is redeemed (write_agent_config writes the client config first), so a
    bad key or a failed agent creation never burns a single-use invite.
    """
    from . import creao
    key = creao.api_key()
    if not key:
        raise ValueError("Creao needs CREAO_API_KEY on this computer: create a cr_sk_ key in Creao's "
                         "Developer Console and add it to cadswarm.env (see .env.example)")
    if creao.is_app_key(key):
        # An agent built on agent.creao.ai: its id, model, skill and autonomy all live on its page.
        agent_id = (os.environ.get("CREAO_AGENT_ID") or "").strip()
        if not agent_id:
            raise ValueError("A capi_ key runs one agent built on agent.creao.ai: add that agent's id "
                             "(shown on its page) to cadswarm.env as CREAO_AGENT_ID")
        creao.save_config(project, agent_id=agent_id)
        upsert_block(os.path.join(project, "AGENTS.md"), CREAO_DOC.format(begin=BEGIN, end=END))
        return [os.path.join(project, "AGENTS.md"), creao.config_path(project)]
    api = creao.Creao(key=key)
    try:
        api.usage()  # a cheap authenticated call: proves the key works before anything is spent
    except creao.CreaoError as e:
        raise ValueError(str(e))
    cfg = creao.load_config(project)
    shared = creao.load_shared()
    agent_id = (os.environ.get("CREAO_AGENT_ID") or cfg.get("agent_id")
                or shared.get("agent_id") or "").strip()
    model = (os.environ.get("CREAO_MODEL") or cfg.get("model") or "").strip() or None
    if not agent_id:
        try:
            agent_id = api.create_agent(creao.AGENT_DESCRIPTION, model=model)
        except creao.CreaoError as e:
            raise ValueError(str(e))
        creao.save_shared(agent_id)  # every Creao helper on this computer shares this account's agent
    elif not shared.get("agent_id"):
        creao.save_shared(agent_id)  # a folder set up before the cache existed hands it up
    creao.save_config(project, agent_id=agent_id, model=model)
    # Both are best-effort and idempotent: a team whose account can't install skills still runs on the
    # protocol the bridge sends with every task.
    try:
        api.install_skill(creao.SKILL_MD, files={"scripts/validate_turn.py": creao.VALIDATE_TURN})
    except creao.CreaoError as e:
        print(f"cadswarm: warning: couldn't install the {creao.SKILL_NAME} Creao skill ({e}); tasks still "
              "carry the tool protocol", file=sys.stderr)
    try:
        # A /v1 run cannot collect approvals, so approval_required would silently skip guarded writes.
        api.update_agent(agent_id, write_autonomy="full_auto")
    except creao.CreaoError as e:
        print(f"cadswarm: warning: couldn't set full_auto write autonomy on the Creao agent ({e})", file=sys.stderr)
    upsert_block(os.path.join(project, "AGENTS.md"), CREAO_DOC.format(begin=BEGIN, end=END))
    return [os.path.join(project, "AGENTS.md"), creao.config_path(project)]


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
    if fresh and client == "creao":
        from .creao import clear_conversation  # a new identity gets a fresh Creao conversation too
        clear_conversation(project)
    with open(os.path.join(state_dir(project), ".gitignore"), "w") as f:
        f.write("*\n")  # identity.json holds a SpacetimeDB token
    writers = {"claude": setup_claude, "codex": setup_codex, "droid": setup_droid,
               "opencode": setup_opencode, "creao": setup_creao}
    written = writers[client](project, python)
    if not identity_matches(project, invite):
        if not invite.get("token"):
            raise ValueError("This helper folder isn't signed in to this team yet; it needs a fresh invite code.")
        save_json(ident_path, redeem(invite, name, client))
    kept = helper_model(project)
    save_json(os.path.join(state_dir(project), "config.json"), {
        "uri": invite["uri"].rstrip("/"), "db": invite["db"], "name": name, "client": client,
        "role": invite.get("role") or "member", **({"ephemeral": True} if ephemeral else {})})
    save_helper_model(project, kept if model is None else model)
    return written


def helper_model(project):
    """The model this helper folder was pinned to, or "" for the AI's own default."""
    return (load_json(os.path.join(state_dir(project), "config.json")) or {}).get("model") or ""


def save_helper_model(project, model):
    """Pin (or with "" unpin) the model this helper runs on. It sticks: the runner and the Creao bridge
    prefer it over CADSWARM_HELPER_MODEL / CREAO_MODEL, and OpenCode's own opencode.json gets it too,
    so the helper opened in a terminal uses the same model."""
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
    """Models to offer in the picker: OpenCode's `opencode models` (provider/model ids) or the Creao
    account's models. [] when the list can't be had (the picker then takes a typed id)."""
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
    if client == "creao":
        from . import creao
        try:
            return creao.client().list_models()
        except (creao.CreaoError, OSError, ValueError, KeyError, AttributeError):
            return []
    return []


def _node_version(path):
    """(22, 1, 0) for ~/.nvm/versions/node/v22.1.0/bin/<exe>: newest Node first, not "v9" before "v22"."""
    return tuple(int(n) for n in re.findall(r"\d+", os.path.basename(os.path.dirname(os.path.dirname(path)))))


def find_agent_cli(client):
    """Absolute path of the AI helper's command, or None. Looks beyond PATH because apps
    started from the Start menu don't always see what a terminal sees."""
    exe = CLIENTS[client]["exe"]
    if not exe:  # Creao AI has no local CLI: it runs through the bridge, and needs only a key
        return None
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


def creao_configured():
    """Creao has no binary to detect: a CREAO_API_KEY is what makes it usable on this computer."""
    from .creao import api_key
    return bool(api_key())


def agent_ready(client):
    """Whether this computer can run `client` now: its CLI is installed, or Creao has its key."""
    if client == "creao":
        return creao_configured()
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
    if client == "creao":
        return _write_runner_launcher(project, project_name)
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


def _write_runner_launcher(project, project_name):
    """Creao has no terminal CLI to open: its start script runs the background runner, which wakes the
    bridge (cadswarm.creao_agent) whenever the team has something for this helper."""
    python = sys.executable
    sd = state_dir(project)
    if os.name == "nt":
        q = lambda t: '"' + t.replace('"', "'") + '"'  # noqa: E731
        text = "\r\n".join([
            "@echo off", "rem Keeps this CadSwarm Creao AI helper running (written by the CadSwarm guide).",
            f'cd /d "{project}"',
            f'"{python}" "{LAUNCHER}" runner {q(project)} {q(project_name)}', ""])
        path = os.path.join(sd, "start-helper.cmd")
    else:
        text = "\n".join([
            "#!/bin/sh", "# Keeps this CadSwarm Creao AI helper running (written by the CadSwarm guide).",
            f"cd {_sh_quote(project)} || exit 1",
            f"exec {_sh_quote(python)} {_sh_quote(LAUNCHER)} runner {_sh_quote(project)} "
            f"{_sh_quote(project_name)}", ""])
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
        if client == "creao":
            raise RuntimeError("Creao AI runs in the background through CadSwarm's runner; there's no terminal "
                               "to open. Use the CadSwarm panel's Tools tab to restart it.")
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
