"""CadSwarm workbench: a human's seat at the table.

* A live, read-only MIRROR document ("CadSwarm Live") shows the hub's shared
  model, each part colored by whoever holds its lock, updated as agents work.
* A TEAM PANEL shows presence, locks, tasks and the session chat, and lets the
  human steer: message agents (human messages outrank peer requests in every
  agent's inbox), lock/release parts, force a takeover, create tasks, set
  shared Params, run an interference check and export STEP.

Networking runs on background threads (event long-poll, heartbeats, BREP
fetches) that only ever push into a queue; a QTimer drains it on the GUI
thread, so FreeCAD's document and Qt widgets are touched from one thread only.
"""
import os
import queue
import threading
import time
from urllib.parse import urlencode

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui

from ..client import HubClient, HubRequestError

PARAMS_PATH = "User parameter:BaseApp/Preferences/Mod/CadSwarm"
MIRROR_DOC = "CadSwarmLive"
HEARTBEAT_SECONDS = 10


def prefs():
    return App.ParamGet(PARAMS_PATH)


# Distinct, colorblind-friendlier hues (Tableau 10), handed out in join order so two
# teammates never end up with near-identical colors the way hashed hues can.
PALETTE = [(0.12, 0.47, 0.71), (1.0, 0.50, 0.05), (0.17, 0.63, 0.17), (0.84, 0.15, 0.16), (0.58, 0.40, 0.74),
           (0.55, 0.34, 0.29), (0.89, 0.47, 0.76), (0.09, 0.75, 0.81), (0.74, 0.74, 0.13), (0.50, 0.50, 0.50)]
ASSIGNED = {}


def owner_color(owner_id):
    if not owner_id:
        return (0.80, 0.80, 0.80)  # unlocked: neutral grey
    if owner_id not in ASSIGNED:
        ASSIGNED[owner_id] = PALETTE[len(ASSIGNED) % len(PALETTE)]
    return ASSIGNED[owner_id]


def say(msg):
    App.Console.PrintMessage(f"[CadSwarm] {msg}\n")


def warn(msg):
    App.Console.PrintWarning(f"[CadSwarm] {msg}\n")


def friendly_error(e, hub=""):
    """What went wrong, in words a teammate can act on (never just 'not connected')."""
    code = getattr(e, "code", None)
    if code == "unreachable" or getattr(e, "status", None) == 0:
        return (f"Couldn't reach your team's hub at {hub or 'that address'}.\n"
                "Check that whoever is hosting has it running (cadswarm hub) and that the address is right.")
    return {
        "bad_workspace_token": "That team code doesn't match. Ask the person hosting the hub for the "
                               "workspace token it printed when it started.",
        "taken_over": "A teammate took over your session, so you were signed out. Join again to keep working.",
        "fence_stale": "You joined from another FreeCAD window, so this one was signed out.",
        "left": "You left the team session.",
        "bad_token": "Your saved sign-in is no longer valid. Join again.",
    }.get(code, str(e))


class Session:
    def __init__(self):
        self.client = None
        self.ident = None
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.seq = 0
        self.model = {"objects": [], "participants": [], "params": {}}
        self.tasks = []
        self.panel = None
        self.timer = None
        # What the panel's banner tells the user: disconnected | connected | offline | ended
        self.state, self.state_detail = "disconnected", ""

    # ------------------------------------------------------------ lifecycle

    @property
    def connected(self):
        return self.client is not None and not self.stop.is_set()

    def set_state(self, state, detail=""):
        self.state, self.state_detail = state, detail
        if self.panel:
            self.panel.show_state(self)

    def ensure_connected(self, purpose):
        """True if connected; otherwise walk the user through joining (purpose explains why)."""
        if self.connected:
            return True
        return connect_dialog(reason=f"To {purpose}, first join your team's session.")

    def try_auto_rejoin(self):
        """Opening the workbench should just work: first time -> the setup guide; a host whose team
        server stopped (e.g. after a reboot) -> the guide restarts it; otherwise quietly rejoin."""
        p = prefs()
        hub, token, name = p.GetString("HubUrl"), p.GetString("WorkspaceToken"), p.GetString("Name")
        if self.connected:
            return
        from .wizard import run_wizard
        if not (hub and token and name):
            QtCore.QTimer.singleShot(300, run_wizard)
            return
        if p.GetString("HostDataDir"):
            from .. import hosting
            if not hosting.health(f"http://127.0.0.1:{p.GetString('HostPort')}", timeout=1.5):
                QtCore.QTimer.singleShot(300, lambda: run_wizard(start="reconnect"))
                return
        try:
            self.connect(hub, token, name, timeout=3)
        except HubRequestError as e:
            self.set_state("disconnected", friendly_error(e, hub))

    def connect(self, hub, token, name, timeout=30):
        p = prefs()
        resume = {"participant_id": p.GetString("ParticipantId"), "token": p.GetString("ParticipantToken")}
        same_hub = p.GetString("HubUrl") == hub and p.GetString("Name") == name
        self._end_session()  # a previous session's threads must never act on this one
        client = HubClient(hub, resume if same_hub and resume["participant_id"] else {}, timeout=timeout)
        try:
            self.ident = client.join(token, name, kind="human", client="freecad")
        except HubRequestError as e:
            if e.code not in ("bad_workspace_token", "unreachable") and client.identity:
                client.identity = {}  # stored identity is no longer valid: join fresh
                self.ident = client.join(token, name, kind="human", client="freecad")
            else:
                raise
        client.timeout = 30
        self.client = client
        p.SetString("HubUrl", hub)
        p.SetString("WorkspaceToken", token)
        p.SetString("Name", name)
        p.SetString("ParticipantId", self.ident["participant_id"])
        p.SetString("ParticipantToken", self.ident["token"])
        # Fresh stop flag and queue per connection: a worker still blocked in an old long-poll can
        # only ever signal its own (already stopped) session. Workers never touch Qt; they only queue.
        self.stop, self.events = threading.Event(), queue.Queue()
        self.seq = self.ident.get("seq", 0)
        if self.timer is None:
            self.timer = QtCore.QTimer()
            self.timer.timeout.connect(self.drain)
        try:
            self.set_state("connected")
            self.full_refresh()
        except Exception:
            self.disconnect()  # don't stay "connected" to a session that failed to load
            raise
        for loop in (self._poll_loop, self._heartbeat_loop):
            threading.Thread(target=loop, args=(self.stop, self.events, self._bg_client(), self.seq),
                             daemon=True).start()
        self.timer.start(250)
        say(f"Joined the team at {hub} as {name}")

    def disconnect(self, reason=None):
        """Leave the session. `reason` set means it ended on us (takeover, other window)."""
        if not self.client:
            return
        client = self.client
        self._end_session()
        if reason is None:
            try:
                client.post("/leave")
            except HubRequestError:
                pass
        self.set_state("ended" if reason else "disconnected", reason or "You left the team session.")

    def _end_session(self):
        """Stop this session's workers (they exit on their own stop flag) and forget its client."""
        self.stop.set()
        self.client = None
        if self.timer:
            self.timer.stop()

    def _bg_client(self):
        return HubClient(self.client.hub, dict(self.client.identity), timeout=40)

    def _heartbeat_loop(self, stop, events, c, _seq):
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                c.post("/heartbeat", {"doing": "watching in FreeCAD"})
            except HubRequestError as e:
                if e.code in ("taken_over", "fence_stale", "left", "bad_token"):
                    events.put(("fatal", friendly_error(e)))
                    return

    def _poll_loop(self, stop, events, c, seq):
        offline = False
        while not stop.is_set():
            try:
                r = c.get("/events", since=seq, wait=20)
            except HubRequestError as e:
                if e.code in ("taken_over", "fence_stale", "left", "bad_token"):
                    events.put(("fatal", friendly_error(e)))
                    return
                if not offline:
                    offline = True
                    events.put(("offline", friendly_error(e, c.hub)))
                time.sleep(3)
                continue
            if offline:
                offline = False
                events.put(("online", None))
            if stop.is_set():
                return  # session ended while we waited: never report into a newer one
            if not r["events"]:
                continue
            seq = r["seq"]
            changed, deleted = set(), set()
            for ev in r["events"]:
                if ev["kind"] == "op":
                    changed |= set(ev["created"] + ev["modified"] + ev["recomputed"])
                    deleted |= set(ev["deleted"])
                events.put(("event", ev))
            if changed:
                try:
                    events.put(("brep", c.post("/brep", {"names": sorted(changed)})["objects"]))
                except HubRequestError as e:
                    events.put(("offline", str(e)))
            if deleted:
                events.put(("deleted", sorted(deleted)))
            events.put(("refresh", None))

    # ------------------------------------------------------- GUI-thread work

    def drain(self):
        refresh = False
        for _ in range(200):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "brep":
                self.apply_breps(data)
            elif kind == "deleted":
                self.remove_objects(data)
            elif kind == "event":
                if self.panel:
                    self.panel.on_event(data)
            elif kind == "refresh":
                refresh = True
            elif kind == "offline":
                self.set_state("offline", data)
            elif kind == "online":
                self.set_state("connected")
                refresh = True
            elif kind == "fatal":
                warn(data)
                self.disconnect(reason=data)
        if refresh:
            self.refresh_meta()

    def mirror(self):
        doc = App.listDocuments().get(MIRROR_DOC)
        if doc is None:
            doc = App.newDocument(MIRROR_DOC)
            doc.Label = "CadSwarm Live (read-only mirror)"
        return doc

    def apply_breps(self, objects):
        doc = self.mirror()
        added = False
        for name, info in objects.items():
            if info is None:
                continue
            obj = doc.getObject(name)
            if info.get("brep") is None:
                continue  # e.g. the Params spreadsheet: shown in the panel instead
            shape = Part.Shape()
            shape.importBrepFromString(info["brep"])
            if obj is None:
                obj = doc.addObject("Part::Feature", name)
                added = True
            obj.Shape = shape
            owner = info.get("locked_by")
            obj.Label = f"{info.get('label') or name} [{owner or 'unlocked'}]"
            vo = getattr(obj, "ViewObject", None)
            if vo:
                vo.Visibility = bool(info.get("visible", True))
                try:
                    vo.ShapeColor = owner_color(info.get("owner_id"))
                except Exception:
                    pass
        doc.recompute()
        if added:
            self.frame_model()

    def frame_model(self):
        """Show the live model and fit it in view, so new parts are never off-screen or hidden
        behind another tab (beginners don't know to press 'Fit all')."""
        try:
            gdoc = Gui.getDocument(MIRROR_DOC)
            if Gui.ActiveDocument is None or Gui.ActiveDocument.Document.Name != MIRROR_DOC:
                Gui.setActiveDocument(MIRROR_DOC)  # bring the live model's tab to the front
            if not getattr(self, "_iso_done", False):
                gdoc.ActiveView.viewIsometric()
                self._iso_done = True
            gdoc.ActiveView.fitAll()
        except Exception as e:
            warn(f"couldn't frame the model: {e}")

    def remove_objects(self, names):
        doc = self.mirror()
        for n in names:
            if doc.getObject(n):
                doc.removeObject(n)
        doc.recompute()

    def full_refresh(self):
        self.apply_breps(self.client.post("/brep", {})["objects"])
        mirror = self.mirror()
        live = {o["name"] for o in self.client.get("/model")["objects"]}
        self.remove_objects([o.Name for o in mirror.Objects if o.Name not in live])
        self.refresh_meta()
        self.frame_model()

    def refresh_meta(self):
        if not self.client:
            return
        try:
            self.model = self.client.get("/model")
            self.tasks = self.client.get("/tasks")["tasks"]
            owners = {o["name"]: o.get("locked_by") for o in self.model["objects"]}
            ids = {p["name"]: p["id"] for p in self.model["participants"]}
            for p in self.model["participants"]:
                owner_color(p["id"])  # reserve colors in join order
            doc = self.mirror()
            for o in doc.Objects:  # recolor on lock moves (takeover, release) without new geometry
                owner = owners.get(o.Name)
                if getattr(o, "ViewObject", None):
                    try:
                        o.ViewObject.ShapeColor = owner_color(ids.get(owner))
                    except Exception:
                        pass
                base = o.Label.split(" [")[0]
                o.Label = f"{base} [{owner or 'unlocked'}]"
        except HubRequestError as e:
            warn(f"refresh failed: {e}")
            return
        if self.panel:
            self.panel.render(self)

    # --------------------------------------------------------------- actions

    def act(self, method, path, body=None, done=None, purpose="do that"):
        if not self.ensure_connected(purpose):
            return None
        try:
            r = self.client.request(method, path, body if method != "GET" else None)
            if done:
                say(done if isinstance(done, str) else done(r))
            self.refresh_meta()
            return r
        except HubRequestError as e:
            QtGui.QMessageBox.warning(Gui.getMainWindow(), "CadSwarm", friendly_error(e, self.client.hub if self.client else ""))
            return None

    def selected_names(self):
        names = []
        for sel in Gui.Selection.getSelection():
            if sel.Document.Name == MIRROR_DOC:
                names.append(sel.Name)
        return names


SESSION = Session()


# ------------------------------------------------------------------ panel

class TeamPanel(QtGui.QDockWidget):
    def __init__(self, session):
        super().__init__("CadSwarm Team")
        self.session = session
        self.setObjectName("CadSwarmTeam")
        w = QtGui.QWidget()
        lay = QtGui.QVBoxLayout(w)
        # The banner always answers "where do I stand, and what do I do next?"
        self.banner = QtGui.QFrame()
        self.banner.setFrameShape(QtGui.QFrame.StyledPanel)
        brow = QtGui.QHBoxLayout(self.banner)
        self.banner_text = QtGui.QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_button = QtGui.QPushButton()
        self.banner_button.clicked.connect(self.banner_action)
        self.invite_button = QtGui.QPushButton("Invite")
        self.invite_button.setToolTip("Copy the invite code for teammates")
        self.invite_button.clicked.connect(invite_teammates)
        brow.addWidget(self.banner_text, 1)
        bcol = QtGui.QVBoxLayout()
        bcol.addWidget(self.banner_button)
        bcol.addWidget(self.invite_button)
        brow.addLayout(bcol)
        lay.addWidget(self.banner)
        self.status = QtGui.QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        lay.addWidget(QtGui.QLabel("<b>Who's here</b> (people and their AI helpers)"))
        self.team = QtGui.QTreeWidget()
        self.team.setHeaderLabels(["Name", "What they're doing / parts they hold"])
        self.team.setColumnWidth(0, 170)
        lay.addWidget(self.team, 2)

        lay.addWidget(QtGui.QLabel("<b>To-do list</b>"))
        self.task_list = QtGui.QTreeWidget()
        self.task_list.setHeaderLabels(["Task", "Status", "Who"])
        self.task_list.setColumnWidth(0, 200)
        lay.addWidget(self.task_list, 2)

        lay.addWidget(QtGui.QLabel("<b>Shared sizes</b> (every part that uses one updates when it changes)"))
        self.params = QtGui.QLabel("-")
        self.params.setWordWrap(True)
        lay.addWidget(self.params)

        lay.addWidget(QtGui.QLabel("<b>Chat</b> — AI helpers read your messages first"))
        self.chat = QtGui.QTextEdit()
        self.chat.setReadOnly(True)
        lay.addWidget(self.chat, 3)
        row = QtGui.QHBoxLayout()
        self.to = QtGui.QComboBox()
        self.to.addItem("all")
        self.msg = QtGui.QLineEdit()
        self.msg.setPlaceholderText("Message / steer an agent…")
        self.msg.returnPressed.connect(self.send)
        send = QtGui.QPushButton("Send")
        send.clicked.connect(self.send)
        row.addWidget(self.to)
        row.addWidget(self.msg, 1)
        row.addWidget(send)
        lay.addLayout(row)

        grid = QtGui.QGridLayout()
        buttons = [("Add a task…", self.new_task, "Add something to the team's to-do list"),
                   ("Change a shared size…", self.set_param, "e.g. make every hole 5 mm"),
                   ("Check for clashes", run_interference, "Find parts that overlap each other"),
                   ("Save as 3D file…", run_export, "Save a STEP file for printing or other CAD programs")]
        for i, (label, fn, tip) in enumerate(buttons):
            b = QtGui.QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)
        more = QtGui.QToolButton()
        more.setText("Team tools ▾")
        more.setPopupMode(QtGui.QToolButton.InstantPopup)
        menu = QtGui.QMenu(more)
        for label, fn in (("Project files…", show_project_files), ("Open project folder", open_project_folder),
                          ("Save a snapshot of the model", save_snapshot),
                          (None, None),
                          ("Add an AI helper…", add_helper), ("Invite teammates…", invite_teammates),
                          (None, None),
                          ("Hold the selected parts (only you can change them)", self.lock),
                          ("Let go of the selected parts", self.release),
                          ("Force-free the selected parts", self.force_release),
                          ("Take over a teammate's work", self.take_over),
                          (None, None), ("Stop hosting the team", stop_hosting)):
            if label is None:
                menu.addSeparator()
            else:
                menu.addAction(label).triggered.connect(lambda _=False, f=fn: f())
        more.setMenu(menu)
        lay.addWidget(more, 0, QtCore.Qt.AlignRight)
        self.setWidget(w)
        self.show_state(session)

    BANNERS = {
        # state: (background, text color, headline, button)
        "disconnected": ("#fff4d6", "#5c4400", "You haven't joined a team session yet.",
                         "Get started…"),
        "offline": ("#fde2e1", "#7a1712", "Lost contact with your team. Retrying automatically…", "Retry now"),
        "ended": ("#fde2e1", "#7a1712", "You're no longer in the team session.", "Join again…"),
        "connected": ("#e3f6e8", "#14532d", "", "Leave"),
    }

    def show_state(self, s):
        bg, fg, headline, button = self.BANNERS[s.state]
        if s.state == "connected":
            me = s.ident["name"] if s.ident else "you"
            others = [p for p in s.model.get("participants", []) if p.get("name") != me and p.get("status") == "active"]
            headline = (f"<b>You're in the team session as {me}.</b> "
                        f"{len(others)} teammate{'s' if len(others) != 1 else ''} online.")
        detail = ""
        if s.state == "disconnected":
            left = s.state_detail == "You left the team session."
            if left:
                headline = "You left the team session. Your locks were released."
            detail = ("Join again to keep working with your team." if left else
                      "Start or join a team to watch the shared model being built and chat with your "
                      "team's AI helpers."
                      + (f"<br><i>{s.state_detail}</i>".replace("\n", " ") if s.state_detail else ""))
        elif s.state in ("offline", "ended"):
            detail = s.state_detail.replace("\n", "<br>")
        self.banner_text.setText(f"<b>{headline}</b>" + (f"<br>{detail}" if detail else "")
                                 if s.state != "connected" else headline)
        self.banner.setStyleSheet(f"QFrame {{ background: {bg}; border-radius: 6px; }} "
                                  f"QLabel {{ color: {fg}; background: transparent; }}")
        self.banner_button.setText(button)
        self.invite_button.setVisible(s.state == "connected" and bool(prefs().GetString("InviteCode")))
        connected = s.state in ("connected", "offline")
        self.msg.setPlaceholderText("Message your team's AI helpers — press Enter to send" if connected else
                                    "Type a message — you'll be asked to join your team first")
        if not connected:
            self.status.setText("")

    def banner_action(self):
        s = self.session
        if s.state == "connected":
            if confirm("Leave the team session? Your locks will be released so others can edit those parts."):
                s.disconnect()
        elif s.state == "offline":
            s.refresh_meta()
        else:
            connect_dialog()

    def set_status(self, text):
        self.status.setText(text)

    def render(self, s):
        me = s.ident["name"] if s.ident else "?"
        self.show_state(s)
        parts = [o for o in s.model["objects"] if o.get("bbox")]
        self.set_status(f"<small>{len(parts)} part{'s' if len(parts) != 1 else ''} in the shared model so far."
                        + ("" if parts else " Nothing built yet — ask an AI helper to start!") + "</small>")
        self.team.clear()
        current = self.to.currentText()
        self.to.clear()
        self.to.addItem("all")
        for p in s.model["participants"]:
            item = QtGui.QTreeWidgetItem([f"{p['name']} ({who_is(p)})",
                                          STATUS_WORDS.get(p["status"], p["status"])
                                          + (f" — {p['doing']}" if p.get("doing") else "")])
            item.setData(0, QtCore.Qt.UserRole, p["name"])
            r, g, b = owner_color(p["id"])
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(int(r * 200), int(g * 200), int(b * 200))))
            for lock in p["locks"]:
                if lock != "Params":
                    QtGui.QTreeWidgetItem(item, [f"🔒 {lock}", "only they can change this part"])
            self.team.addTopLevelItem(item)
            item.setExpanded(True)
            if p["name"] != me:
                self.to.addItem(p["name"])
        idx = self.to.findText(current)
        self.to.setCurrentIndex(max(idx, 0))
        self.task_list.clear()
        for t in s.tasks:
            self.task_list.addTopLevelItem(QtGui.QTreeWidgetItem([t["title"], TASK_WORDS.get(t["status"], t["status"]),
                                                                  t.get("owner_name") or t.get("assignee") or ""]))
        params = s.model.get("params") or {}
        self.params.setText(", ".join(f"{k.replace('_', ' ')} = {v:g} mm" if isinstance(v, (int, float))
                                      else f"{k.replace('_', ' ')} = {v}" for k, v in params.items()) or "(none yet)")

    def on_event(self, ev):
        k = ev["kind"]
        stamp = f"<span style='color:#8b949e'>{time.strftime('%H:%M', time.localtime(ev['ts']))}</span>"
        if k == "message":
            steer = ev.get("message_kind") == "steer"
            to = "" if ev["to"] == "all" else f" <span style='color:#8b949e'>to {ev['to']}</span>"
            color = "#e5484d" if steer else "#3e9be9"
            self.chat.append(f"{stamp} <b style='color:{color}'>{ev['name']}</b>{to}: {ev['body']}")
            return
        line = describe_event(ev)
        if line:
            self.chat.append(f"{stamp} <span style='color:#a78bfa'>{line}</span>")

    # --- actions
    def send(self):
        text = self.msg.text().strip()
        if not text:
            return
        # Not joined yet: keep what they typed, walk them through joining, then send it.
        if not self.session.ensure_connected("send your message"):
            return
        if self.session.act("POST", "/messages", {"to": self.to.currentText(), "body": text},
                            purpose="send your message") is not None:
            self.msg.clear()

    def selected_or_hint(self, verb):
        if not self.session.ensure_connected(f"{verb} parts"):
            return []
        names = self.session.selected_names()
        if not names:
            hint("Select one or more parts in the 3D view first (in the “CadSwarm Live” document), "
                 f"then click this again to {verb} them.")
        return names

    def lock(self):
        names = self.selected_or_hint("lock")
        if names:
            self.session.act("POST", "/locks", {"names": names}, f"Locked {names}", purpose="lock parts")

    def release(self):
        names = self.selected_or_hint("release")
        if names:
            self.session.act("POST", "/locks/release", {"names": names}, f"Released {names}", purpose="release parts")

    def force_release(self):
        names = self.selected_or_hint("force-release")
        if names and confirm(f"Force-release {names}? Their owners are told; any agent may then edit them."):
            self.session.act("POST", "/locks/release", {"names": names, "force": True}, f"Force-released {names}",
                             purpose="release parts")

    def take_over(self):
        if not self.session.ensure_connected("take over a teammate's work"):
            return
        item = self.team.currentItem()
        while item is not None and item.parent() is not None:
            item = item.parent()
        if item is None:
            hint("Click a teammate in the Team list first, then click “Take over member”.")
            return
        name = item.data(0, QtCore.Qt.UserRole)
        if confirm(f"Take over {name}'s locks and tasks?"):
            self.session.act("POST", "/takeover", {"target": name, "force": True},
                             lambda r: f"Took over {r['from']}: locks {r['locks']}, tasks {r['tasks']}",
                             purpose="take over a teammate's work")

    def new_task(self):
        if not self.session.ensure_connected("create a task"):
            return
        title, ok = QtGui.QInputDialog.getText(self, "New task", "Title (e.g. 'Motor mount, 4x M3 on Params.hole_pitch'):")
        if not ok or not title.strip():
            return
        who = [self.to.itemText(i) for i in range(self.to.count())]
        assignee, ok = QtGui.QInputDialog.getItem(self, "New task", "Assign to:", ["(nobody)"] + who[1:], 0, False)
        if not ok:
            return
        self.session.act("POST", "/tasks", {"title": title.strip(),
                                           "assignee": None if assignee == "(nobody)" else assignee},
                         lambda r: f"Created task {r['id']}")

    def set_param(self):
        if not self.session.ensure_connected("change a shared dimension"):
            return
        text, ok = QtGui.QInputDialog.getText(self, "Set shared param", "alias = value   (e.g. hole_pitch = 40 mm)")
        if not ok or "=" not in text:
            return
        alias, value = (x.strip() for x in text.split("=", 1))
        if self.session.act("POST", "/locks", {"names": ["Params"]}) is None:
            return
        self.session.act("POST", "/ops", {"op": "set_param", "args": {"alias": alias, "value": value}},
                         lambda r: f"Params.{alias} = {value}; recomputed {r['recomputed']}")
        self.session.act("POST", "/locks/release", {"names": ["Params"]})


CLIENT_WORDS = {"claude": "Claude Code helper", "codex": "Codex helper"}
STATUS_WORDS = {"active": "here", "stale": "away — may be stuck", "left": "left"}
TASK_WORDS = {"open": "not started", "claimed": "in progress", "awaiting_takeover": "waiting for someone",
              "done": "done ✓", "blocked": "stuck"}


def who_is(p):
    if p.get("kind") == "human":
        return "person"
    return CLIENT_WORDS.get(p.get("client"), "AI helper")


def names(items):
    items = [i for i in items if i != "Params"]
    return ", ".join(items[:4]) + (f" and {len(items) - 4} more" if len(items) > 4 else "")


def describe_event(ev):
    """One friendly sentence per team event (None = not worth showing)."""
    k, who = ev["kind"], ev.get("name") or ev.get("taker") or "Someone"
    if k == "op":
        bits = []
        if ev.get("created"):
            bits.append(f"added {names(ev['created'])}")
        changed = [n for n in ev.get("modified", []) if n != "Params"]
        if changed:
            bits.append(f"changed {names(changed)}")
        if ev.get("deleted"):
            bits.append(f"removed {names(ev['deleted'])}")
        if "Params" in ev.get("modified", []):
            bits.append("changed a shared size")
        if ev.get("recomputed"):
            bits.append(f"which updated {names(ev['recomputed'])}")
        return f"{who} {', '.join(bits)}" if bits else None
    if k == "joined":
        kind = "person" if ev.get("participant_kind") == "human" else CLIENT_WORDS.get(ev.get("client"), "AI helper")
        return f"{who} ({kind}) joined the team"
    if k == "left":
        return f"{who} left the team"
    if k == "stale":
        held = names(ev.get("locks") or [])
        return f"{who} went quiet" + (f" while working on {held}" if held else "") + \
            " — a teammate can take over their work (Team tools ▸ Take over)"
    if k == "resumed":
        return f"{who} is back"
    if k == "taken_over":
        return f"{ev.get('taker', who)} took over {ev.get('target')}'s work"
    if k == "task":
        return f"New task: “{ev.get('title')}”" + (f" for {ev['assignee']}" if ev.get("assignee") else "")
    if k == "task_claimed":
        return f"{who} started a task"
    if k == "task_reported":
        return f"{who} finished a task ✓" if ev.get("status") == "done" else f"{who} is stuck on a task"
    if k == "file_saved":
        return f"{who} {'updated' if ev.get('replaced') else 'saved'} files/{ev.get('path')}"
    if k == "file_moved":
        return f"{who} moved files/{ev.get('from')} to files/{ev.get('to')}"
    if k == "file_deleted":
        return f"{who} deleted files/{ev.get('path')} (it can be restored)"
    if k == "snapshot":
        return f"{who} saved a snapshot of the model ({ev.get('file')})"
    if k == "exported":
        return f"{who} saved a 3D file ({ev.get('file')})"
    return None


def hint(text):
    QtGui.QMessageBox.information(Gui.getMainWindow(), "CadSwarm", text)


def confirm(text):
    return QtGui.QMessageBox.question(Gui.getMainWindow(), "CadSwarm", text,
                                      QtGui.QMessageBox.Yes | QtGui.QMessageBox.No) == QtGui.QMessageBox.Yes


def show_panel():
    if SESSION.panel is None:
        SESSION.panel = TeamPanel(SESSION)
        Gui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, SESSION.panel)
    SESSION.panel.show()
    if SESSION.connected:
        SESSION.refresh_meta()


def run_interference():
    r = SESSION.act("POST", "/interference", {}, purpose="check for clashes")
    if r is None:
        return
    Gui.Selection.clearSelection()
    if not r["clashes"]:
        say(f"No interference among {len(r['checked'])} parts")
        QtGui.QMessageBox.information(Gui.getMainWindow(), "CadSwarm", "No interference found.")
        return
    lines = []
    for c in r["clashes"]:
        lines.append(f"{c['a']} ∩ {c['b']}: {c['volume']} mm³")
        for n in (c["a"], c["b"]):
            if App.getDocument(MIRROR_DOC).getObject(n):
                Gui.Selection.addSelection(MIRROR_DOC, n)
    QtGui.QMessageBox.warning(Gui.getMainWindow(), "CadSwarm — interference", "\n".join(lines))


def run_export():
    r = SESSION.act("POST", "/export", {"format": "step"}, purpose="export the shared model")
    if r is None:
        return
    path, _ = QtGui.QFileDialog.getSaveFileName(Gui.getMainWindow(), "Save STEP", r["file"], "STEP (*.step *.stp)")
    if path:
        with open(path, "wb") as f:
            f.write(SESSION.client.request("GET", r["url"], raw=True))
        say(f"Exported {r['objects']} to {path}")


# --------------------------------------------------------------- commands

class Cmd:
    """Toolbar/menu command. Always clickable: if you're not in a session yet, clicking
    explains that and offers to join, instead of being mysteriously greyed out."""

    def __init__(self, text, tip, fn, needs_connection=True):
        self.text, self.tip, self.fn, self.needs = text, tip, fn, needs_connection

    def GetResources(self):
        return {"MenuText": self.text, "ToolTip": self.tip}

    def Activated(self):
        if self.needs and not SESSION.ensure_connected(self.text.rstrip("…").lower()):
            return
        self.fn()

    def IsActive(self):
        return True


def connect_dialog(reason=None):
    """Join (or set up) a team through the step-by-step guide. Returns True once joined."""
    from .wizard import run_wizard
    return run_wizard(reason=reason)


def add_helper():
    from .wizard import run_wizard
    run_wizard(start="agent")


def invite_teammates():
    code = prefs().GetString("InviteCode")
    if not code:
        QtGui.QMessageBox.information(Gui.getMainWindow(), "CadSwarm",
                                      "Ask whoever started the team for the invite code.")
        return
    QtGui.QApplication.clipboard().setText(code)
    QtGui.QMessageBox.information(
        Gui.getMainWindow(), "Invite teammates",
        "The invite code is copied. Paste it to your teammates (email, Discord, text...).\n\n"
        f"{code}\n\nThey open FreeCAD, pick CadSwarm, click Get started, then "
        "\u201cJoin a teammate\u2019s project\u201d.")


def stop_hosting():
    from .wizard import stop_hosting as stop
    stop()


def hosted_project_folder():
    folder = prefs().GetString("HostDataDir")
    return folder if folder and os.path.isdir(folder) else None


def open_project_folder():
    folder = hosted_project_folder()
    if not folder:
        hint("The project folder is on the computer of the teammate who started the team. "
             "Use Team tools ▸ Project files to see and copy what's in it.")
        return
    from .wizard import open_folder
    open_folder(folder)


def save_snapshot():
    label, ok = QtGui.QInputDialog.getText(Gui.getMainWindow(), "Save a snapshot",
                                           "Name this version of the model (optional):")
    if ok:
        r = SESSION.act("POST", "/project/snapshot", {"label": label.strip()}, purpose="save a snapshot")
        if r:
            hint(f"Saved {r['file']} in the project folder. Snapshots are never overwritten.")


class ProjectFilesDialog(QtGui.QDialog):
    """What the team has saved: browse, see versions and authors, save a copy."""

    def __init__(self):
        super().__init__(Gui.getMainWindow())
        self.setWindowTitle("CadSwarm — project files")
        self.resize(640, 460)
        lay = QtGui.QVBoxLayout(self)
        where = hosted_project_folder()
        lay.addWidget(QtGui.QLabel(
            "Notes, plans and scripts your team's AI helpers saved. Older versions are always kept."
            + (f"<br><small>On this computer: {where}\\files</small>" if where else
               "<br><small>They live on the computer of the teammate who started the team.</small>")))
        self.tree = QtGui.QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Version", "Saved by", "When"])
        self.tree.setColumnWidth(0, 280)
        lay.addWidget(self.tree, 1)
        row = QtGui.QHBoxLayout()
        copy = QtGui.QPushButton("Save a copy…")
        copy.clicked.connect(self.save_copy)
        row.addWidget(copy)
        if where:
            folder = QtGui.QPushButton("Open project folder")
            folder.clicked.connect(open_project_folder)
            row.addWidget(folder)
        row.addStretch(1)
        close = QtGui.QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.load()

    def load(self):
        try:
            listing = SESSION.client.get("/project/files")
        except HubRequestError as e:
            hint(friendly_error(e))
            return
        nodes = {"": self.tree.invisibleRootItem()}

        def folder_node(path):
            if path in nodes:
                return nodes[path]
            parent, _, name = path.rpartition("/")
            item = QtGui.QTreeWidgetItem(folder_node(parent), [f"📁 {name}", "", "", ""])
            item.setExpanded(True)
            nodes[path] = item
            return item

        for f in sorted(listing.get("folders", [])):
            folder_node(f)
        for f in listing.get("files", []):
            parent, _, name = f["path"].rpartition("/")
            when = time.strftime("%b %d %H:%M", time.localtime(f["updated_at"]))
            item = QtGui.QTreeWidgetItem(folder_node(parent), [name, str(f["version"]), f["author"] or "", when])
            item.setData(0, QtCore.Qt.UserRole, f["path"])
        if not listing.get("files"):
            QtGui.QTreeWidgetItem(self.tree, ["(nothing saved yet)", "", "", ""])

    def save_copy(self):
        item = self.tree.currentItem()
        path = item.data(0, QtCore.Qt.UserRole) if item else None
        if not path:
            hint("Click a file first, then “Save a copy”.")
            return
        dest, _ = QtGui.QFileDialog.getSaveFileName(self, "Save a copy", path.split("/")[-1])
        if not dest:
            return  # the system dialog already asked before replacing an existing file
        try:
            data = SESSION.client.request("GET", "/project/files/download?" + urlencode({"path": path}), raw=True)
        except HubRequestError as e:
            hint(friendly_error(e))
            return
        with open(dest, "wb") as f:
            f.write(data)
        say(f"Saved a copy of {path} to {dest}")


def show_project_files():
    if not SESSION.ensure_connected("see the project's files"):
        return
    dlg = ProjectFilesDialog()
    (getattr(dlg, "exec", None) or dlg.exec_)()


def lock_selected():
    show_panel()
    SESSION.panel.lock()


def release_selected():
    show_panel()
    SESSION.panel.release()


def register_commands():
    Gui.addCommand("CadSwarm_Connect", Cmd("Get started…", "Start or join a team, step by step",
                                           lambda: connect_dialog(), False))
    Gui.addCommand("CadSwarm_AddHelper", Cmd("Add AI helper…", "Connect Claude Code or Codex to your team",
                                             add_helper))
    Gui.addCommand("CadSwarm_Invite", Cmd("Invite…", "Copy the invite code for teammates", invite_teammates))
    Gui.addCommand("CadSwarm_StopHosting", Cmd("Stop hosting", "Stop the team server on this computer",
                                               stop_hosting, False))
    Gui.addCommand("CadSwarm_Panel", Cmd("Team panel", "Show presence, locks, tasks and chat", show_panel, False))
    Gui.addCommand("CadSwarm_Lock", Cmd("Lock selected", "Lock the selected mirror parts to you", lock_selected))
    Gui.addCommand("CadSwarm_Release", Cmd("Release selected", "Release your locks on the selected parts", release_selected))
    Gui.addCommand("CadSwarm_Interference", Cmd("Check for clashes", "Find parts that overlap", run_interference))
    Gui.addCommand("CadSwarm_Export", Cmd("Save as 3D file…", "Save the shared model as a STEP file", run_export))
    Gui.addCommand("CadSwarm_Disconnect", Cmd("Leave team", "Leave the session (releases your locks)",
                                              lambda: SESSION.disconnect() if SESSION.connected else None, False))
