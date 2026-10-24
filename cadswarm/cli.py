"""`cadswarm` command line.

  cadswarm hub [--data DIR] [--port 8765] [--host 0.0.0.0] [--token SECRET]
      Run the shared hub + FreeCAD worker (one per team / model).

  cadswarm setup --client claude|codex --hub URL --token SECRET --name NAME [--role lead] [--dir .]
      Wire an agent CLI in a project folder to the hub: MCP server, prompt hook, instructions.

  cadswarm install-workbench
      Install the CadSwarm workbench into the local FreeCAD for humans to watch and steer.
"""
import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys

from .client import load_json, save_json, state_dir
from .config import DEFAULT_PORT, find_freecad_python

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BEGIN, END = "<!-- cadswarm:begin -->", "<!-- cadswarm:end -->"
TOML_BEGIN, TOML_END = "# >>> cadswarm (managed by `cadswarm setup`)", "# <<< cadswarm"

AGENT_DOC = """{begin}
## CadSwarm: you are part of a team building one shared FreeCAD model

Your teammates' agents (Claude Code, Codex) are editing the same live model right now.
All CAD work goes through the `cadswarm` MCP tools — never edit .FCStd files directly.

- Start every task with `model_status` and `list_tasks`; `claim_task` before working.
- `lock_objects` before changing anything; what you create is yours. Ask owners, don't override them.
- Shared dimensions go in the `Params` sheet (`set_param`); bind geometry with expressions
  like `Params.hole_pitch` so a change propagates to every part that depends on it.
- After changes run `check_interference`; coordinate clashes with the other part's owner.
- Read `get_inbox` often, `ack_messages` what you handled. Human `steer` messages come first.
- Finish with `report_result` (evidence: object names, volumes, interference) and release locks.
- Team context (who holds what, what changed) is injected before each of your prompts.
{end}
"""


def upsert_block(path, block, begin=BEGIN, end=END):
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if begin in text and end in text:
        pre, rest = text.split(begin, 1)
        text = pre + block.strip() + rest.split(end, 1)[1]
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block.strip() + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def python_cmd():
    return sys.executable


def setup_claude(project):
    py = python_cmd()
    mcp_path = os.path.join(project, ".mcp.json")
    mcp_cfg = load_json(mcp_path, {}) or {}
    mcp_cfg.setdefault("mcpServers", {})["cadswarm"] = {
        "command": py, "args": ["-m", "cadswarm.mcp_server"], "env": {"CADSWARM_PROJECT": project}}
    with open(mcp_path, "w", encoding="utf-8") as f:
        json.dump(mcp_cfg, f, indent=2)

    settings_path = os.path.join(project, ".claude", "settings.json")
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    settings = load_json(settings_path, {}) or {}
    hook_cmd = f'"{py}" -m cadswarm.prompt_hook'
    groups = settings.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
    # Idempotent: drop any earlier cadswarm entry, keep everyone else's hooks.
    groups[:] = [g for g in groups if not any("cadswarm.prompt_hook" in h.get("command", "") for h in g.get("hooks", []))]
    groups.append({"hooks": [{"type": "command", "command": hook_cmd, "timeout": 5}]})
    enabled = settings.setdefault("enabledMcpjsonServers", [])
    if "cadswarm" not in enabled:
        enabled.append("cadswarm")
    allow = settings.setdefault("permissions", {}).setdefault("allow", [])
    if "mcp__cadswarm" not in allow:
        allow.append("mcp__cadswarm")
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    upsert_block(os.path.join(project, "CLAUDE.md"), AGENT_DOC.format(begin=BEGIN, end=END))
    return [mcp_path, settings_path, os.path.join(project, "CLAUDE.md")]


def toml_literal(s):
    if "'" in s:
        return json.dumps(s)  # basic string; JSON escaping is valid TOML for these paths
    return f"'{s}'"


def setup_codex(project):
    py = python_cmd()
    path = os.path.join(project, ".codex", "config.toml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if TOML_BEGIN in text:
        pre, rest = text.split(TOML_BEGIN, 1)
        text = pre.rstrip() + rest.split(TOML_END, 1)[1]
    for table in ("[hooks]", "[mcp_servers.cadswarm]"):
        if any(line.strip() == table for line in text.splitlines()):
            raise SystemExit(f"{path} already defines {table} outside cadswarm's block; merge it by hand.")
    hook_cmd = f'"{py}" -m cadswarm.prompt_hook'
    block = "\n".join([
        TOML_BEGIN,
        "[mcp_servers.cadswarm]",
        f"command = {toml_literal(py)}",
        "args = ['-m', 'cadswarm.mcp_server']",
        f"env = {{ CADSWARM_PROJECT = {toml_literal(project)} }}",
        "",
        "# UserPromptSubmit injects team context (who holds which parts, what changed) before each prompt.",
        "[hooks]",
        f"UserPromptSubmit = [{{ hooks = [{{ type = 'command', command = {toml_literal(hook_cmd)}, timeout = 5 }}] }}]",
        TOML_END, ""])
    with open(path, "w", encoding="utf-8") as f:
        f.write((text.rstrip() + "\n\n" if text.strip() else "") + block)
    upsert_block(os.path.join(project, "AGENTS.md"), AGENT_DOC.format(begin=BEGIN, end=END))
    return [path, os.path.join(project, "AGENTS.md")]


def cmd_setup(a):
    project = os.path.abspath(a.dir)
    os.makedirs(project, exist_ok=True)
    save_json(os.path.join(state_dir(project), "config.json"), {
        "hub": a.hub.rstrip("/"), "workspace_token": a.token, "name": a.name,
        "client": a.client, "role": a.role})
    ident = os.path.join(state_dir(project), "identity.json")
    if a.fresh and os.path.exists(ident):
        os.remove(ident)
    written = setup_claude(project) if a.client == "claude" else setup_codex(project)
    gi = os.path.join(state_dir(project), ".gitignore")
    with open(gi, "w") as f:
        f.write("*\n")  # identity.json holds a bearer token
    print(f"CadSwarm set up for {a.client} as '{a.name}' ({a.role}) in {project}")
    for p in written:
        print(f"  wrote {p}")
    if a.client == "claude":
        print("Next: open this folder and run `claude`; the cadswarm MCP server joins the hub on start.")
    else:
        print("Next: run `codex` here. Codex only loads .codex/config.toml for TRUSTED projects: accept the "
              "trust prompt, or add to ~/.codex/config.toml:\n"
              f"  [projects.{json.dumps(project)}]\n  trust_level = \"trusted\"")


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))  # UDP connect sends nothing; it just picks the outbound interface
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def cmd_hub(a):
    import uvicorn

    from .hub.app import create_app
    from .hub.core import Hub
    from .hub.worker_client import WorkerClient

    data = os.path.abspath(a.data)
    os.makedirs(data, exist_ok=True)
    token_file = os.path.join(data, "workspace_token")
    token = a.token or (open(token_file).read().strip() if os.path.exists(token_file) else secrets.token_urlsafe(12))
    with open(token_file, "w") as f:
        f.write(token)
    worker = WorkerClient(os.path.join(data, "model.FCStd"))
    worker.start()
    hub = Hub(worker, os.path.join(data, "hub.sqlite"), token)
    url = f"http://{lan_ip() if a.host == '0.0.0.0' else a.host}:{a.port}"
    print(f"CadSwarm hub on {url}  (FreeCAD worker: {worker.python})", flush=True)
    print(f"Model: {worker.doc_path}")
    print(f"Workspace token: {token}")
    print("Teammates connect an agent with:")
    print(f"  cadswarm setup --client claude --hub {url} --token {token} --name <you>-claude")
    print(f"  cadswarm setup --client codex  --hub {url} --token {token} --name <you>-codex", flush=True)
    uvicorn.run(create_app(hub, data), host=a.host, port=a.port, log_level="warning")


def cmd_install_workbench(a):
    fc_py = find_freecad_python()
    user_dir = subprocess.run(
        [fc_py, "-c", "import sys,os; sys.path.append(os.path.dirname(sys.executable)); "
                      "import FreeCAD; print(FreeCAD.getUserAppDataDir())"],
        capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
    dest = os.path.join(user_dir, "Mod", "CadSwarm")
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(os.path.join(ROOT, "workbench", "CadSwarm"), dest)
    shutil.copy(os.path.join(ROOT, "cadswarm", "client.py"), os.path.join(dest, "cadswarm_client.py"))
    print(f"Installed CadSwarm workbench to {dest}. Restart FreeCAD and pick the 'CadSwarm' workbench.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="cadswarm")
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hub", help="run the shared hub")
    h.add_argument("--data", default="cadswarm-data")
    h.add_argument("--port", type=int, default=DEFAULT_PORT)
    h.add_argument("--host", default="0.0.0.0")
    h.add_argument("--token")
    h.set_defaults(fn=cmd_hub)
    s = sub.add_parser("setup", help="connect an agent CLI in a project folder")
    s.add_argument("--client", choices=["claude", "codex"], required=True)
    s.add_argument("--hub", required=True)
    s.add_argument("--token", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--role", choices=["lead", "member"], default="member")
    s.add_argument("--dir", default=".")
    s.add_argument("--fresh", action="store_true", help="forget the stored identity and join as a new participant")
    s.set_defaults(fn=cmd_setup)
    w = sub.add_parser("install-workbench", help="install the FreeCAD workbench")
    w.set_defaults(fn=cmd_install_workbench)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
