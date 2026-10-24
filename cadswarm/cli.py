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
import subprocess
import sys

from .agent_setup import write_agent_config
from .config import DEFAULT_PORT, find_freecad_python
from .hosting import lan_ip
from .paths import ROOT


def cmd_setup(a):
    project = os.path.abspath(a.dir)
    try:
        written = write_agent_config(project, a.client, a.hub, a.token, a.name, a.role, sys.executable, a.fresh)
    except ValueError as e:
        raise SystemExit(str(e))
    print(f"CadSwarm set up for {a.client} as '{a.name}' ({a.role}) in {project}")
    for p in written:
        print(f"  wrote {p}")
    if a.client == "claude":
        print("Next: open this folder and run `claude`; the cadswarm MCP server joins the hub on start.")
    else:
        print("Next: run `codex` here. Codex only loads .codex/config.toml for TRUSTED projects: accept the "
              "trust prompt, or add to ~/.codex/config.toml:\n"
              f"  [projects.{json.dumps(project)}]\n  trust_level = \"trusted\"")
        print("Then, in Codex, type /hooks and trust the CadSwarm hook. Codex skips new or changed hooks until "
              "you do, so without this your helper won't see its team's updates. Do it again after updating CadSwarm.")


PROJECT_README = """This folder is a CadSwarm team project.

  model.FCStd   the shared 3D model everyone's AI helpers are building (open a copy in FreeCAD any time)
  files/        notes, plans and scripts the team saved; subfolders appear as the project grows
  exports/      3D files (STEP/STL) saved with "Save as 3D file"; never overwritten
  snapshots/    saved versions of the model
  AI helpers/   workspace folders for the AI helpers on this computer (they hold sign-ins: don't share)
  .cadswarm-team/  the team server's own data and older versions of files (the last 20 of each). Don't edit it.

Please don't edit model.FCStd directly while the team is working: your changes would be lost
the next time an AI helper changes the model. Copy it first, or work through CadSwarm.
"""


def cmd_hub(a):
    import uvicorn

    from .hub.app import create_app
    from .hub.core import Hub
    from .hub.worker_client import WorkerClient
    from .paths import project_layout

    lay = project_layout(a.data)
    for key in ("internal", "files", "exports", "snapshots"):
        os.makedirs(lay[key], exist_ok=True)
    readme = os.path.join(lay["root"], "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(PROJECT_README)
    token = a.token or (open(lay["token"]).read().strip() if os.path.exists(lay["token"]) else secrets.token_urlsafe(12))
    with open(lay["token"], "w") as f:
        f.write(token)
    worker = WorkerClient(lay["model"])
    worker.start()
    hub = Hub(worker, lay["db"], token, layout=lay)
    url = f"http://{lan_ip() if a.host == '0.0.0.0' else a.host}:{a.port}"
    print(f"CadSwarm hub on {url}  (FreeCAD worker: {worker.python})", flush=True)
    print(f"Project folder: {lay['root']}")
    print(f"Workspace token: {token}")
    print("Teammates connect an agent with:")
    print(f"  cadswarm setup --client claude --hub {url} --token {token} --name <you>-claude")
    print(f"  cadswarm setup --client codex  --hub {url} --token {token} --name <you>-codex", flush=True)
    uvicorn.run(create_app(hub, lay["root"]), host=a.host, port=a.port, log_level="warning")


ADDON_IGNORE = shutil.ignore_patterns(".git", ".venv", "*.egg-info", "__pycache__", ".pytest_cache",
                                     "cadswarm-data", "exports", ".cadswarm", "*.pyc", "node_modules", "target")


def freecad_mod_dir(fc_py=None):
    fc_py = fc_py or find_freecad_python()
    user_dir = subprocess.run(
        [fc_py, "-c", "import sys,os; b=os.path.dirname(sys.executable); sys.path += [b, os.path.join(b, '..', 'lib')]; "
                      "import FreeCAD; print(FreeCAD.getUserAppDataDir())"],
        capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
    return os.path.join(user_dir, "Mod")


def install_addon(dest_mod_dir):
    """Copy this checkout into FreeCAD's Mod folder as the 'CadSwarm' add-on."""
    dest = os.path.join(dest_mod_dir, "CadSwarm")
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(ROOT, dest, ignore=ADDON_IGNORE)
    return dest


def cmd_install_workbench(a):
    dest = install_addon(freecad_mod_dir())
    print(f"Installed CadSwarm into FreeCAD at {dest}.")
    print("Restart FreeCAD and pick 'CadSwarm' from the workbench list; a setup guide opens.")


def cmd_invite(a):
    from .invite import make_invite
    print(make_invite(a.hub, a.token, a.project))


def main(argv=None):
    p = argparse.ArgumentParser(prog="cadswarm")
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hub", help="run the shared hub")
    h.add_argument("--data", default="cadswarm-data", help="the project folder (created if missing)")
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
    i = sub.add_parser("invite", help="print an invite code teammates can paste into the FreeCAD guide")
    i.add_argument("--hub", required=True, help="address teammates can reach, e.g. http://100.64.1.5:8765")
    i.add_argument("--token", required=True)
    i.add_argument("--project", default="")
    i.set_defaults(fn=cmd_invite)
    w = sub.add_parser("install-workbench", help="install the FreeCAD workbench")
    w.set_defaults(fn=cmd_install_workbench)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
