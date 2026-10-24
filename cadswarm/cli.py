"""`cadswarm` command line.

  cadswarm setup --client claude|codex|droid|opencode|creao --invite CODE --name NAME [--dir .] [--fresh]
      Join the team with an invite (a cadswarm-2: code, a base32 ticket from `cadswarm-host invite`,
      or a phone link with #t=) and wire an agent CLI in a folder: MCP server, prompt hook, instructions.

  cadswarm creao reset [--dir .]
      Forget the Creao AI helper's conversation (its next task starts a fresh one); keep its Creao agent.

  cadswarm brief [--dir .] [--board ID] TEXT
      Submit a design brief from a set-up folder.

  cadswarm install-workbench
      Install the CadSwarm workbench into the local FreeCAD for humans to watch and steer.

The team server is `cadswarm-host` (Rust) on SpacetimeDB; `cadswarm-host invite` mints invites.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

from .agent_setup import write_agent_config
from .config import LINUX_FREECAD_LIBS, find_freecad_python
from .paths import ROOT


def cmd_setup(a):
    from .invite import InviteError, read_invite
    project = os.path.abspath(a.dir)
    try:
        t = read_invite(a.invite)
        written = write_agent_config(project, a.client, t, a.name, sys.executable, a.fresh, ephemeral=a.ephemeral)
    except (InviteError, ValueError) as e:
        raise SystemExit(f"cadswarm: {e}")
    except Exception as e:  # SpaceError from redeeming: used, expired or unreachable
        raise SystemExit(f"cadswarm: could not join with this invite: {e}")
    print(f"CadSwarm set up for {a.client} as '{a.name}' in {project}")
    for p in written:
        print(f"  wrote {p}")
    if a.client == "claude":
        print("Next: open this folder and run `claude`; the cadswarm MCP server joins the team on start.")
    elif a.client == "droid":
        print("Next: run `droid` here; it loads .factory/mcp.json and joins the team on start.")
    elif a.client == "opencode":
        print("Next: run `opencode` here; it loads opencode.json and joins the team on start.")
    elif a.client == "creao":
        print("Next: open CadSwarm in FreeCAD; the background runner wakes this helper with Creao AI. "
              "`cadswarm creao reset` starts its next task in a new conversation.")
    else:
        print("Next: run `codex` here. Codex only loads .codex/config.toml for TRUSTED projects: accept the "
              "trust prompt, or add to ~/.codex/config.toml:\n"
              f"  [projects.{json.dumps(project)}]\n  trust_level = \"trusted\"")
        print("Then, in Codex, type /hooks and trust the CadSwarm hook. Codex skips new or changed hooks until "
              "you do, so without this your helper won't see its team's updates. Do it again after updating CadSwarm.")


def cmd_creao_reset(a):
    from .creao import clear_conversation
    project = os.path.abspath(a.dir)
    if clear_conversation(project):
        print(f"Creao AI will start a new conversation for the helper in {project}.")
    else:
        print(f"The helper in {project} has no saved Creao conversation yet; nothing to reset.")


def cmd_brief(a):
    from .space_hub import project_hub
    hub, _cfg = project_hub(os.path.abspath(a.dir))
    if not hub:
        raise SystemExit("cadswarm: run `cadswarm setup --invite ...` in that folder first")
    # Reuse the stored fence: a rejoin here would supersede that folder's live MCP server (plan 00).
    if not hub.identity.get("fence"):
        hub.join()
    print(hub.post("/briefs", {"text": a.text, "board": a.board or ""})["brief"])


ADDON_IGNORE = shutil.ignore_patterns(".git", ".venv", "*.egg-info", "__pycache__", ".pytest_cache",
                                     "cadswarm-data", "exports", ".cadswarm", "*.pyc", "node_modules", "target")


def freecad_mod_dir(fc_py=None):
    fc_py = fc_py or find_freecad_python()
    # FreeCAD's module sits next to its Python (Windows), in the sibling lib folder (macOS .app),
    # or in a distro lib folder (Linux, system python3).
    dirs = [os.path.dirname(fc_py), os.path.join(os.path.dirname(os.path.dirname(fc_py)), "lib"),
            *LINUX_FREECAD_LIBS]
    user_dir = subprocess.run(
        [fc_py, "-c", f"import sys; sys.path += {dirs!r}; import FreeCAD; print(FreeCAD.getUserAppDataDir())"],
        capture_output=True, text=True, check=True, timeout=120).stdout.strip().splitlines()[-1]
    return os.path.join(user_dir, "Mod")


def install_addon(dest_mod_dir):
    """Copy this checkout into FreeCAD's Mod folder as the 'CadSwarm' add-on."""
    dest = os.path.join(dest_mod_dir, "CadSwarm")
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(ROOT, dest, ignore=ADDON_IGNORE)
    wasm = os.path.join(ROOT, "module", "target", "wasm32-unknown-unknown", "release", "cadswarm_module.wasm")
    if os.path.isfile(wasm):  # the team server's database module (build folders aren't copied)
        shutil.copy2(wasm, os.path.join(dest, "module", "cadswarm_module.wasm"))
    return dest


def cmd_install_workbench(a):
    dest = install_addon(freecad_mod_dir())
    print(f"Installed CadSwarm into FreeCAD at {dest}.")
    print("Restart FreeCAD and pick 'CadSwarm' from the workbench list; a setup guide opens.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="cadswarm")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="join with an invite and connect an agent CLI in a folder")
    s.add_argument("--client", choices=["claude", "codex", "droid", "opencode", "creao"], required=True)
    s.add_argument("--invite", required=True, help="a cadswarm-2: code, a ticket, or a phone link")
    s.add_argument("--name", required=True)
    s.add_argument("--dir", default=".")
    s.add_argument("--fresh", action="store_true", help="forget the stored identity and join as a new member")
    s.add_argument("--ephemeral", action="store_true",
                   help="a one-job agent (the host's planners and workers): it leaves the team when its session ends")
    s.set_defaults(fn=cmd_setup)
    r = sub.add_parser("creao", help="Creao AI helper housekeeping")
    rs = r.add_subparsers(dest="creao_cmd", required=True)
    rr = rs.add_parser("reset", help="start the helper's next task in a new Creao conversation")
    rr.add_argument("--dir", default=".")
    rr.set_defaults(fn=cmd_creao_reset)
    b = sub.add_parser("brief", help="submit a brief")
    b.add_argument("--dir", default=".")
    b.add_argument("--board")
    b.add_argument("text")
    b.set_defaults(fn=cmd_brief)
    w = sub.add_parser("install-workbench", help="install the FreeCAD workbench")
    w.set_defaults(fn=cmd_install_workbench)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
