"""A sample team for screenshots and trying the panel without a server: CADSWARM_UX_DEMO=1 python -m cadswarm.app.

Fills the panel with a small team (two people, three AI helpers), a request planned as numbered steps half way
through, a chat with replies and a held part, all served by an in-process stand-in for the team server that
echoes what you send (chat, tasks, build requests) so the prompt bar and the chat can be tried for real.
"""
import itertools
import time

from PySide import QtCore

_ids = itertools.count(100)


def member(i, name, kind="agent", client="claude", status="active", doing="", task=None, locks=()):
    return {"id": i, "name": name, "kind": kind, "client": client, "role": "member", "status": status,
            "doing": doing, "last_seen": time.time(), "locks": list(locks), "task": task, "idle_s": 5}


def snapshot():
    now = time.time()
    steps = [("Mast", "done", "ann-claude", ["Pad"]), ("Hub", "done", "ann-claude", []),
             ("Blade grip", "claimed", "bob-codex", ["Holes"]), ("Pitch link", "open", "dave", []),
             ("Swashplate", "open", "", []), ("M3 screws", "open", "", []), ("Assemble", "open", "", [])]
    tasks = []
    for i, (name, status, who, objects) in enumerate(steps, 1):
        tasks.append({"id": f"t{i}", "title": f"{i}. {name}", "status": status, "brief": "b1",
                      "owner_name": who if status != "open" else None, "assignee": who or None,
                      "objects": objects, "detail": "", "created_at": now - 900 + i})
    return {
        "model": {"objects": [{"name": "Pad", "label": "Mast", "bbox": [0, 0, 0, 1, 1, 1], "locked_by": "ann-claude"},
                              {"name": "Holes", "label": "Blade grip", "bbox": [0, 0, 0, 1, 1, 1],
                               "locked_by": "bob-codex"}],
                  "params": {"mast_d": 6.0, "wall": 2.4},
                  "participants": [member(1, "carol", kind="human", client="freecad"),
                                   member(5, "dave", kind="human", client="web", locks=["Pad"]),
                                   member(2, "ann-claude", doing="listening for instructions"),
                                   member(3, "bob-codex", client="codex",
                                          task={"id": "t3", "title": "3. Blade grip"}, locks=["Holes"]),
                                   member(4, "eve-opencode", client="opencode", status="stale")]},
        "tasks": tasks,
        "commands": [],
        "briefs": [{"id": "b1", "text": "A four-blade rotor head: 6 mm mast, hub, blade grips, pitch links and a "
                                        "swashplate", "status": "building", "by": "carol", "at": now - 900,
                    "tasks": len(tasks), "done": 2}],
        "reviews": [],
        "workspace": {"bed": [256.0, 256.0, 256.0], "bed_x_mm": 256.0, "bed_y_mm": 256.0, "bed_z_mm": 256.0},
        "messages": [
            {"id": "m1", "from": "carol", "to": "all", "kind": "chat", "at": now - 800,
             "body": "Plan looks right. Dave, can you take the pitch links?"},
            {"id": "m2", "from": "dave", "to": "all", "kind": "chat", "at": now - 760,
             "body": "Yes, I'll do step 4 by hand once the grip is in."},
            {"id": "m3", "from": "ann-claude", "to": "all", "kind": "chat", "at": now - 420,
             "body": "Step 2 done: hub is 32 mm across with four M3 bosses, bound to Params.mast_d."},
            {"id": "m4", "from": "bob-codex", "to": "carol", "kind": "chat", "at": now - 90,
             "body": "@carol building the blade grip now. Should the grip bolt be M3 or M4?"}],
    }


class DemoClient:
    """Answers like the team server for what the panel sends; keeps it in the snapshot."""

    hub = "http://localhost:3000"
    cfg = {"db": "demo-team", "uri": "http://localhost:3000"}

    def __init__(self, session, snap):
        self.session, self.snap = session, snap

    def _echo(self):
        # Called from the panel's worker threads: hand the new state to the GUI thread through the event queue.
        self.session.events.put(("snapshot", self.snap))

    def request(self, method, path, body=None, params=None):
        if method == "GET":
            raise RuntimeError("No route in the demo")
        return self.post(path, body)

    def post(self, path, body=None):
        body = body or {}
        me = (self.session.ident or {}).get("name") or "carol"
        if path == "/messages":
            self.snap["messages"].append({"id": f"m{next(_ids)}", "from": me, "to": body.get("to") or "all",
                                          "kind": body.get("kind") or "chat", "body": body.get("body") or "",
                                          "at": time.time()})
        elif path == "/tasks":
            t = {"id": f"t{next(_ids)}", "title": body.get("title") or "Task", "status": "open",
                 "assignee": body.get("assignee"), "owner_name": None, "objects": body.get("objects") or [],
                 "brief": None, "detail": body.get("detail") or "", "created_at": time.time()}
            self.snap["tasks"].append(t)
            self._echo()
            return t
        elif path not in ("/briefs",):
            raise RuntimeError(f"No route in the demo: {path}")
        if path == "/briefs":
            self.snap["briefs"].append({"id": f"b{next(_ids)}", "text": body.get("text") or "", "status": "planning",
                                        "by": me, "at": time.time(), "tasks": 0, "done": 0})
        self._echo()
        return {"ok": True}

    def get(self, path):
        raise RuntimeError("No route in the demo")

    def create_invite(self, kind, role, ttl, project, uri=None):
        return "cadswarm-2:DEMO-" + kind.upper() + "-7Q4K-PX2M"


def seed(session):
    """Put the sample team in the panel (call once the panel exists)."""
    from . import panel
    snap = snapshot()
    panel.start_local_helpers = lambda s, restart=False: []
    session.client = DemoClient(session, snap)
    session.ident = {"name": "carol", "participant_id": 1}
    session.project = "Rotor head"
    session.state = "connected"
    session.mirror = lambda: type("Doc", (), {"Objects": []})()
    session.apply_snapshot(snap)
    if session.timer is None:  # what a real join starts: the GUI thread drains what the workers hand back
        session.timer = QtCore.QTimer()
        session.timer.timeout.connect(session.drain)
    session.timer.start(250)
    if session.panel:
        session.panel.show_state(session)
    return snap


def tour(session, folder, start_ms=6000, step_ms=1500):
    """Screenshot the main window on each main tab, the open Ctrl+K bar and a step clicked, into `folder`
    (CADSWARM_UX_TOUR=<folder>): the milestone pictures, taken by the real app."""
    import os

    import FreeCADGui as Gui
    from . import prompt_bar
    os.makedirs(folder, exist_ok=True)
    p = session.panel
    mw = Gui.getMainWindow()
    shots = [("build", lambda: p.show_tab(p.build_page)),
             ("chat", lambda: p.show_tab(p.chat_page)),
             ("team", lambda: p.show_tab(p.team_page)),
             ("agents", lambda: p.show_tab(p.agents_page)),
             ("more", lambda: p.show_tab(p.tasks_page)),
             ("verify", lambda: verify(session, folder)),
             ("ask", lambda: open_ask(p))]
    t = start_ms

    def run(name, act):
        try:
            act()
        except Exception as e:  # noqa: BLE001
            import FreeCAD
            FreeCAD.Console.PrintError(f"[CADSwarm] tour {name}: {e!r}" + chr(10))
            print(f"[CADSwarm] tour {name}: {e!r}", flush=True)
    for name, act in shots:
        QtCore.QTimer.singleShot(t, lambda n=name, a=act: run(n, a))
        QtCore.QTimer.singleShot(t + step_ms - 200, lambda n=name: mw.grab().save(os.path.join(folder, f"{n}.png")))
        t += step_ms
    return t


def open_ask(p):
    from . import prompt_bar
    p.show_tab(p.build_page)
    overlay = prompt_bar.OVERLAY or prompt_bar.install(p.session)
    overlay.open()
    overlay.bar.edit.setText("@bob-codex make the grip bolt M4")


def verify(session, folder=None):
    """What the tour checks in the real app: Ctrl+K opens the ask bar, clicking a step selects its parts.
    Printed as one VERIFY line (the milestone log)."""
    import FreeCADGui as Gui
    from PySide import QtGui
    from . import prompt_bar
    out = {}
    try:
        from PySide6 import QtTest
        overlay = prompt_bar.OVERLAY
        overlay.close_bar()
        target = QtGui.QApplication.focusWidget() or Gui.getMainWindow()
        QtTest.QTest.keyClick(target, QtCore.Qt.Key_K, QtCore.Qt.ControlModifier)
        QtGui.QApplication.processEvents()
        out["ctrl_k_opens_bar"] = overlay.bar.isVisible() and overlay.bar.edit.hasFocus()
        overlay.close_bar()
    except Exception as e:  # noqa: BLE001
        out["ctrl_k_opens_bar"] = repr(e)
    try:
        page = session.panel.build_page
        step = next(s for s in page.model["steps"] if s["name"] == "Blade grip")
        Gui.Selection.clearSelection()
        page.toggle(step)
        out["step_click_selects"] = [o.Name for o in Gui.Selection.getSelection()]
    except Exception as e:  # noqa: BLE001
        out["step_click_selects"] = repr(e)
    print("VERIFY", out, flush=True)
    if folder:
        import json
        import os
        with open(os.path.join(folder, "verify.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
    return out
