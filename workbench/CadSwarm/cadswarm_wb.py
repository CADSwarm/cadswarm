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
import queue
import threading
import time

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui

from cadswarm_client import HubClient, HubRequestError

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
        """Quietly rejoin the team used last time, so returning to the workbench just works."""
        p = prefs()
        hub, token, name = p.GetString("HubUrl"), p.GetString("WorkspaceToken"), p.GetString("Name")
        if self.connected or not (hub and token and name):
            return
        try:
            self.connect(hub, token, name, timeout=3)
        except HubRequestError as e:
            self.set_state("disconnected", friendly_error(e, hub))

    def connect(self, hub, token, name, timeout=30):
        p = prefs()
        resume = {"participant_id": p.GetString("ParticipantId"), "token": p.GetString("ParticipantToken")}
        same_hub = p.GetString("HubUrl") == hub and p.GetString("Name") == name
        self.client = HubClient(hub, resume if same_hub and resume["participant_id"] else {}, timeout=timeout)
        try:
            self.ident = self.client.join(token, name, kind="human", client="freecad")
        except HubRequestError as e:
            if e.code not in ("bad_workspace_token", "unreachable") and self.client.identity:
                self.client.identity = {}  # stored identity is no longer valid: join fresh
                try:
                    self.ident = self.client.join(token, name, kind="human", client="freecad")
                except HubRequestError:
                    self.client = None
                    raise
            else:
                self.client = None
                raise
        self.client.timeout = 30
        p.SetString("HubUrl", hub)
        p.SetString("WorkspaceToken", token)
        p.SetString("Name", name)
        p.SetString("ParticipantId", self.ident["participant_id"])
        p.SetString("ParticipantToken", self.ident["token"])
        self.stop.clear()
        self.seq = self.ident.get("seq", 0)
        threading.Thread(target=self._poll_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        if self.timer is None:
            self.timer = QtCore.QTimer()
            self.timer.timeout.connect(self.drain)
        self.timer.start(250)
        self.set_state("connected")
        self.full_refresh()
        say(f"Joined the team at {hub} as {name}")

    def disconnect(self, reason=None):
        """Leave the session. `reason` set means it ended on us (takeover, other window)."""
        if not self.client:
            return
        self.stop.set()
        if reason is None:
            try:
                self.client.post("/leave")
            except HubRequestError:
                pass
        self.client = None
        if self.timer:
            self.timer.stop()
        self.set_state("ended" if reason else "disconnected", reason or "You left the team session.")

    def _bg_client(self):
        return HubClient(self.client.hub, dict(self.client.identity), timeout=40)

    def _heartbeat_loop(self):
        c = self._bg_client()
        while not self.stop.wait(HEARTBEAT_SECONDS):
            try:
                c.post("/heartbeat", {"doing": "watching in FreeCAD"})
            except HubRequestError as e:
                if e.code in ("taken_over", "fence_stale", "left", "bad_token"):
                    self.events.put(("fatal", friendly_error(e)))
                    return

    def _poll_loop(self):
        c = self._bg_client()
        offline = False
        while not self.stop.is_set():
            try:
                r = c.get("/events", since=self.seq, wait=20)
            except HubRequestError as e:
                if e.code in ("taken_over", "fence_stale", "left", "bad_token"):
                    self.events.put(("fatal", friendly_error(e)))
                    return
                if not offline:
                    offline = True
                    self.events.put(("offline", friendly_error(e, c.hub)))
                time.sleep(3)
                continue
            if offline:
                offline = False
                self.events.put(("online", None))
            if not r["events"]:
                continue
            self.seq = r["seq"]
            changed, deleted = set(), set()
            for ev in r["events"]:
                if ev["kind"] == "op":
                    changed |= set(ev["created"] + ev["modified"] + ev["recomputed"])
                    deleted |= set(ev["deleted"])
                self.events.put(("event", ev))
            if changed:
                try:
                    self.events.put(("brep", c.post("/brep", {"names": sorted(changed)})["objects"]))
                except HubRequestError as e:
                    self.events.put(("offline", str(e)))
            if deleted:
                self.events.put(("deleted", sorted(deleted)))
            self.events.put(("refresh", None))

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
        try:
            Gui.getDocument(MIRROR_DOC).ActiveView.viewIsometric()
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            pass

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
        brow.addWidget(self.banner_text, 1)
        brow.addWidget(self.banner_button)
        lay.addWidget(self.banner)
        self.status = QtGui.QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        lay.addWidget(QtGui.QLabel("<b>Team</b> (select a member to take over)"))
        self.team = QtGui.QTreeWidget()
        self.team.setHeaderLabels(["Member", "Status / locks"])
        self.team.setColumnWidth(0, 170)
        lay.addWidget(self.team, 2)

        lay.addWidget(QtGui.QLabel("<b>Tasks</b>"))
        self.task_list = QtGui.QTreeWidget()
        self.task_list.setHeaderLabels(["Task", "Status", "Owner"])
        self.task_list.setColumnWidth(0, 200)
        lay.addWidget(self.task_list, 2)

        lay.addWidget(QtGui.QLabel("<b>Params</b> (shared dimensions)"))
        self.params = QtGui.QLabel("-")
        self.params.setWordWrap(True)
        lay.addWidget(self.params)

        lay.addWidget(QtGui.QLabel("<b>Chat</b> — your messages steer agents"))
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
        buttons = [("Lock selected", self.lock), ("Release selected", self.release),
                   ("Force release", self.force_release), ("Take over member", self.take_over),
                   ("New task…", self.new_task), ("Set param…", self.set_param),
                   ("Interference", run_interference), ("Export STEP", run_export)]
        for i, (label, fn) in enumerate(buttons):
            b = QtGui.QPushButton(label)
            b.clicked.connect(fn)
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)
        self.setWidget(w)
        self.show_state(session)

    BANNERS = {
        # state: (background, text color, headline, button)
        "disconnected": ("#fff4d6", "#5c4400", "You haven't joined a team session yet.",
                         "Join team…"),
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
                      "Join to see the shared model, chat with your team's agents and steer their work."
                      + (f"<br><i>{s.state_detail}</i>".replace("\n", " ") if s.state_detail else ""))
        elif s.state in ("offline", "ended"):
            detail = s.state_detail.replace("\n", "<br>")
        self.banner_text.setText(f"<b>{headline}</b>" + (f"<br>{detail}" if detail else "")
                                 if s.state != "connected" else headline)
        self.banner.setStyleSheet(f"QFrame {{ background: {bg}; border-radius: 6px; }} "
                                  f"QLabel {{ color: {fg}; background: transparent; }}")
        self.banner_button.setText(button)
        connected = s.state in ("connected", "offline")
        self.msg.setPlaceholderText("Message your team's agents — press Enter to send" if connected else
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
        self.set_status(f"<small>Team hub {s.client.hub} · revision {s.model.get('seq')} · "
                        f"{len(s.model['objects'])} objects in the shared model</small>")
        self.team.clear()
        current = self.to.currentText()
        self.to.clear()
        self.to.addItem("all")
        for p in s.model["participants"]:
            item = QtGui.QTreeWidgetItem([f"{p['name']} [{p['kind']}/{p['client'] or '?'}]",
                                          f"{p['status']}" + (f" — {p['doing']}" if p.get("doing") else "")])
            item.setData(0, QtCore.Qt.UserRole, p["name"])
            r, g, b = owner_color(p["id"])
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(int(r * 200), int(g * 200), int(b * 200))))
            for lock in p["locks"]:
                QtGui.QTreeWidgetItem(item, [f"🔒 {lock}", ""])
            self.team.addTopLevelItem(item)
            item.setExpanded(True)
            if p["name"] != me:
                self.to.addItem(p["name"])
        idx = self.to.findText(current)
        self.to.setCurrentIndex(max(idx, 0))
        self.task_list.clear()
        for t in s.tasks:
            self.task_list.addTopLevelItem(QtGui.QTreeWidgetItem([f"{t['id']} {t['title']}", t["status"],
                                                                  t.get("owner_name") or ""]))
        params = s.model.get("params") or {}
        self.params.setText(", ".join(f"{k}={v}" for k, v in params.items()) or "(none yet)")

    def on_event(self, ev):
        k = ev["kind"]
        stamp = time.strftime("%H:%M:%S", time.localtime(ev["ts"]))
        if k == "message":
            color = "#c0392b" if ev.get("message_kind") == "steer" else "#2c3e50"
            self.chat.append(f"<span style='color:gray'>{stamp}</span> <b style='color:{color}'>{ev['name']}</b>"
                             f" → {ev['to']}: {ev['body']}")
        elif k == "op":
            parts = [f"{label} {ev[key]}" for key, label in (("created", "created"), ("modified", "changed"),
                     ("deleted", "deleted"), ("recomputed", "recomputed")) if ev.get(key)]
            self.chat.append(f"<span style='color:gray'>{stamp} {ev['name']} {ev['op']}: {'; '.join(parts)}</span>")
        elif k in ("stale", "taken_over", "joined", "left", "task", "task_claimed", "task_reported", "resumed"):
            detail = ", ".join(f"{a}={b}" for a, b in ev.items() if a not in ("seq", "ts", "kind", "actor"))
            self.chat.append(f"<span style='color:#8e44ad'>{stamp} {k}: {detail}</span>")

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
    """Join a team session. Returns True once joined. Errors stay in the dialog so a typo
    can be fixed in place."""
    show_panel()
    p = prefs()
    dlg = QtGui.QDialog(Gui.getMainWindow())
    dlg.setWindowTitle("Join your team's CadSwarm session")
    dlg.setMinimumWidth(460)
    lay = QtGui.QVBoxLayout(dlg)
    if reason:
        why = QtGui.QLabel(f"<b>{reason}</b>")
        why.setWordWrap(True)
        lay.addWidget(why)
    intro = QtGui.QLabel("Ask the teammate who started the hub (<code>cadswarm hub</code>) for its address and "
                         "team code. Both are printed when it starts.")
    intro.setWordWrap(True)
    lay.addWidget(intro)
    form = QtGui.QFormLayout()
    hub = QtGui.QLineEdit(p.GetString("HubUrl") or "http://127.0.0.1:8765")
    hub.setPlaceholderText("e.g. http://192.168.1.20:8765")
    token = QtGui.QLineEdit(p.GetString("WorkspaceToken"))
    token.setPlaceholderText("the workspace token the hub printed")
    name = QtGui.QLineEdit(p.GetString("Name"))
    name.setPlaceholderText("e.g. carol (shown to teammates and agents)")
    form.addRow("Team hub address", hub)
    form.addRow("Team code", token)
    form.addRow("Your name", name)
    lay.addLayout(form)
    error = QtGui.QLabel("")
    error.setWordWrap(True)
    error.setStyleSheet("color: #b42318;")
    lay.addWidget(error)
    bb = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel)
    bb.button(QtGui.QDialogButtonBox.Ok).setText("Join team")
    lay.addWidget(bb)
    joined = {"ok": False}

    def attempt():
        values = hub.text().strip(), token.text().strip(), name.text().strip()
        missing = [label for label, v in zip(("team hub address", "team code", "your name"), values) if not v]
        if missing:
            error.setText("Please fill in " + ", ".join(missing) + ".")
            return
        error.setText("Joining…")
        QtGui.QApplication.processEvents()
        try:
            SESSION.connect(*values)
        except HubRequestError as e:
            error.setText(friendly_error(e, values[0]))
            return
        joined["ok"] = True
        dlg.accept()

    bb.accepted.connect(attempt)
    bb.rejected.connect(dlg.reject)
    run = getattr(dlg, "exec", None) or dlg.exec_  # PySide6 (FreeCAD 1.1) vs PySide2
    run()
    return joined["ok"]


def lock_selected():
    show_panel()
    SESSION.panel.lock()


def release_selected():
    show_panel()
    SESSION.panel.release()


def register_commands():
    Gui.addCommand("CadSwarm_Connect", Cmd("Join team…", "Join your team's shared CAD session", connect_dialog, False))
    Gui.addCommand("CadSwarm_Panel", Cmd("Team panel", "Show presence, locks, tasks and chat", show_panel, False))
    Gui.addCommand("CadSwarm_Lock", Cmd("Lock selected", "Lock the selected mirror parts to you", lock_selected))
    Gui.addCommand("CadSwarm_Release", Cmd("Release selected", "Release your locks on the selected parts", release_selected))
    Gui.addCommand("CadSwarm_Interference", Cmd("Interference check", "Find overlapping solids", run_interference))
    Gui.addCommand("CadSwarm_Export", Cmd("Export STEP", "Export the shared model to STEP", run_export))
    Gui.addCommand("CadSwarm_Disconnect", Cmd("Leave team", "Leave the session (releases your locks)",
                                              lambda: SESSION.disconnect() if SESSION.connected else None, False))
