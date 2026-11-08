"""One CADSwarm app window (the real `python -m cadswarm.app`, FreeCAD 1.1 branded) joined to a live team.

    <FreeCAD python> tests/e2e/app_client.py --out DIR --invite CODE --name NAME [--role a|b]

Run by tests/e2e/team_e2e.py, not by pytest directly (it needs FreeCAD's own Python and a display).
Each window gets its own app folder (APPDATA) and engine profile (CADSWARM_ENGINE_HOME), so two windows on
one computer are two separate people with their own sign-in, exactly as on two computers.

Before the app starts, the invite is redeemed the way the setup guide does it (agent_setup.redeem) and the
sign-in is stored where the panel keeps it; the panel's own start-up rejoin (InitGui.open_on_start ->
try_auto_rejoin) then joins the team. No panel internals are bypassed for joining.

The scripted part runs inside the app's event loop and talks to the team only through the panel
(its chat box and Send, its Lock action on the selection) and reads back what the panel shows. It
coordinates with the orchestrator through files in --out:
  <out>/<name>.json      report, rewritten after every step (the orchestrator polls it)
  <out>/go-<step>        the orchestrator's go-ahead for a step that needs another client first
  <out>/stop             quit
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--invite", required=True)
ap.add_argument("--name", required=True)
ap.add_argument("--peer", default="")
ap.add_argument("--part", default="E2EBox")
ap.add_argument("--locker", action="store_true", help="this window locks the part through the panel")
ap.add_argument("--timeout", type=float, default=240)
args = ap.parse_args()

out = os.path.abspath(args.out)
home = os.path.join(out, args.name)
os.makedirs(home, exist_ok=True)
os.environ["APPDATA"] = os.path.join(home, "appdata")            # app_dir(): sign-ins, recent teams
os.environ["CADSWARM_ENGINE_HOME"] = os.path.join(home, "engine")  # FreeCAD's settings for this window
os.makedirs(os.environ["APPDATA"], exist_ok=True)
sys.path.insert(0, ROOT)

from cadswarm import invite as invite_mod  # noqa: E402
from cadswarm.agent_setup import redeem  # noqa: E402

REPORT = {"name": args.name, "steps": {}, "errors": []}
REPORT_PATH = os.path.join(out, f"{args.name}.json")


def flush():
    tmp = REPORT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(REPORT, f, indent=2, default=str)
    os.replace(tmp, REPORT_PATH)


t = invite_mod.read_invite(args.invite)
ident = redeem(t, args.name, "freecad")
REPORT["db"] = t["db"]
flush()

# The panel's sign-in store (panel.identity_path) and its preferences, written before the app starts.
gui_dir = os.path.join(os.environ["APPDATA"], "CadSwarm", "gui")
os.makedirs(gui_dir, exist_ok=True)
import re  # noqa: E402
with open(os.path.join(gui_dir, re.sub(r"[^A-Za-z0-9_.-]+", "_", t["db"]) + ".json"), "w", encoding="utf-8") as f:
    json.dump(ident, f)

from cadswarm.app import __main__ as app_main  # noqa: E402
from cadswarm.app import engine  # noqa: E402

from cadswarm.app import branded  # noqa: E402

root = app_main.find_root()
home_dir = engine.prepare(root)
# FreeCAD finds its Mod folders when it is first imported: the add-on must be linked before that, as main() does.
branded.link_addon(home_dir)
import FreeCAD as App  # noqa: E402
p = App.ParamGet("User parameter:BaseApp/Preferences/Mod/CadSwarm")
p.SetString("Uri", t["uri"])
p.SetString("Db", t["db"])
p.SetString("Name", args.name)
p.SetString("ProjectName", "e2e")
p.SetString("HostDataDir", "")

import FreeCADGui as Gui  # noqa: E402

_show = Gui.showMainWindow


def show_and_script(*a, **k):
    r = _show(*a, **k)
    from PySide6 import QtCore
    QtCore.QTimer.singleShot(500, Script().start)
    return r


Gui.showMainWindow = show_and_script


class Script:
    """A small state machine on a QTimer: the event loop keeps running between checks."""

    def __init__(self):
        self.t0 = time.time()
        self.steps = iter(self.plan())
        self.current = None
        self.started = None

    def start(self):
        from PySide6 import QtCore
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(300)

    # ---- helpers
    def session(self):
        from cadswarm.gui import panel
        return panel.SESSION

    def shot(self, name):
        path = os.path.join(out, f"{args.name}-{name}.png")
        Gui.getMainWindow().grab().save(path)
        REPORT.setdefault("shots", []).append(path)

    def chat_text(self):
        s = self.session()
        return s.panel.chat.toPlainText() if s.panel else ""

    def labels(self):
        doc = self.session().mirror()
        return {o.Name: o.Label for o in doc.Objects}

    # ---- the plan: (name, function returning True when done, timeout s)
    def plan(self):
        s = self.session

        def joined():
            ses = s()
            if not ses.connected:
                return False
            names = [m["name"] for m in ses.model.get("participants", [])]
            REPORT["steps"]["joined"] = {"state": ses.state, "me": ses.ident.get("name"), "members": names,
                                         "seconds": round(time.time() - self.t0, 1)}
            return True

        def sees_peer():
            names = {m["name"]: m.get("status") for m in s().model.get("participants", [])}
            REPORT["steps"]["presence"] = names
            return not args.peer or args.peer in names

        def say_hello():
            pnl = s().panel
            pnl.msg.setText(f"hello from {args.name}")
            pnl.send()
            REPORT["steps"]["said"] = f"hello from {args.name}"
            return True

        def hears_peer():
            text = self.chat_text()
            ok = not args.peer or f"hello from {args.peer}" in text
            REPORT["steps"]["chat"] = {"heard_peer": ok, "tail": text[-400:]}
            return ok

        def part_arrives():
            names = self.labels()
            REPORT["steps"]["part"] = {"objects": sorted(names)}
            return args.part in names

        def lock_it():
            if not args.locker:
                return True
            doc = s().mirror()
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc.Name, args.part)
            s().panel.lock()
            REPORT["steps"]["lock_requested"] = s().selected_names()
            return True

        def lock_shown():
            owner = next((o.get("locked_by") for o in s().model.get("objects", []) if o["name"] == args.part), None)
            label = self.labels().get(args.part, "")
            want = args.name if args.locker else args.peer
            REPORT["steps"]["lock"] = {"locked_by": owner, "label": label}
            return owner == want and f"[{want}" in label

        def refused_while_locked():
            if args.locker:
                return True
            try:
                s().client.post("/locks", {"names": [args.part]})
                REPORT["steps"]["refused"] = {"refused": False}
            except Exception as e:  # noqa: BLE001 - the refusal is what we want to see
                REPORT["steps"]["refused"] = {"refused": True, "error": str(e)[:200],
                                              "code": getattr(e, "code", "")}
            return True

        def pointer_seen():
            try:
                rows = s().client.space.sql("SELECT * FROM v_pointer")
            except Exception as e:  # noqa: BLE001
                REPORT["steps"]["pointer"] = {"error": str(e)}
                return False
            on = [r for r in rows if r.get("object") == args.part]
            REPORT["steps"]["pointer"] = {"rows_on_part": len(on)}
            return bool(on)

        def screenshot():
            Gui.SendMsgToActiveView("ViewFit")
            self.shot("team")
            return True

        def wait_stop():
            return os.path.exists(os.path.join(out, "stop"))

        return [("joined", joined, 120), ("presence", sees_peer, 60), ("say_hello", say_hello, 5),
                ("chat", hears_peer, 60), ("part", part_arrives, 90), ("lock", lock_it, 5),
                ("lock_shown", lock_shown, 60), ("refused", refused_while_locked, 20),
                ("pointer", pointer_seen, 60), ("screenshot", screenshot, 10), ("stop", wait_stop, args.timeout)]

    def tick(self):
        try:
            if self.current is None:
                self.current = next(self.steps, None)
                self.started = time.time()
                if self.current is None:
                    return self.finish()
            name, fn, limit = self.current
            if fn():
                REPORT.setdefault("done", []).append(name)
                self.current = None
            elif time.time() - self.started > limit:
                REPORT["errors"].append(f"timed out at step {name}")
                try:
                    self.shot(f"fail-{name}")
                except Exception:  # noqa: BLE001
                    pass
                return self.finish()
        except Exception as e:  # noqa: BLE001 - the report must say what broke
            import traceback
            REPORT["errors"].append(traceback.format_exc()[-800:])
            self.current = None
        finally:
            flush()

    def finish(self):
        self.timer.stop()
        REPORT["finished"] = True
        flush()
        from PySide6 import QtWidgets
        try:
            self.session().disconnect()
        except Exception:  # noqa: BLE001
            pass
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        QtWidgets.QApplication.instance().quit()


sys.argv = [sys.argv[0]]
code = app_main.main([])
flush()
os._exit(code or 0)
