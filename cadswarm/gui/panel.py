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
import html
import json
import os
import queue
import re
import threading
import time
from urllib.parse import urlencode

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui

from ..client import HubRequestError, load_json, save_json
from ..paths import app_dir
from ..space_hub import SpaceHub

PARAMS_PATH = "User parameter:BaseApp/Preferences/Mod/CadSwarm"
MIRROR_DOC = "CadSwarmLive"
HEARTBEAT_SECONDS = 10


def prefs():
    return App.ParamGet(PARAMS_PATH)


def identity_path(db):
    """This computer's sign-in for one team, kept outside every project folder (it is a secret)."""
    return os.path.join(app_dir(), "gui", re.sub(r"[^A-Za-z0-9_.-]+", "_", db or "team") + ".json")


def load_identity(db):
    return load_json(identity_path(db), {}) or {}


def save_identity(db, ident):
    save_json(identity_path(db), ident)


def host_dir_for(db):
    """The folder this computer hosts for `db`, or "" when it hosts another team (or none)."""
    folder = prefs().GetString("HostDataDir")
    if not folder or not db:
        return ""
    try:
        with open(os.path.join(folder, "host.json"), encoding="utf-8") as f:
            return folder if (json.load(f) or {}).get("db") == db else ""
    except (OSError, ValueError, TypeError):
        return ""


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
        return (f"Couldn't reach your team's server at {hub or 'that address'}.\n"
                "Check your internet connection, and that whoever is hosting the team has it running "
                "(CadSwarm ▸ Get started ▸ Reconnect).")
    return {
        "bad_invite": "That invite code has already been used or has expired. Ask your teammate for a new one.",
        "not_member": "Your saved sign-in isn't part of this team any more. Join again with a new invite code.",
        "taken_over": "A teammate took over your session, so you were signed out. Join again to keep working.",
        "fence_stale": "You joined from another FreeCAD window, so this one was signed out.",
        "left": "You left the team session.",
        "bad_token": "Your saved sign-in is no longer valid. Join again.",
        "name_in_use": "Someone with that name is already in the team. Join again with a different name "
                       "(letters, numbers, - and _ only).",
        "not_allowed": "Only whoever started the team can do that.",
        "host_only": "Only the computer hosting the team can change its storage limits.",
        "host_timeout": "The team's server didn't pick that up. Check that whoever is hosting the team has "
                        "CadSwarm running, then try again.",
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
        self.fea_dialogs = []  # open report popups (kept referenced so Qt doesn't drop them)
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
        uri, db = p.GetString("Uri"), p.GetString("Db")
        ident = load_identity(db) if db else {}
        if self.connected:
            return
        from .wizard import run_wizard
        if not (uri and db and ident.get("token")):
            QtCore.QTimer.singleShot(300, run_wizard)
            return
        if p.GetString("HostDataDir"):
            from .. import hosting
            if not hosting.team_ready(p.GetString("HostDataDir")):
                QtCore.QTimer.singleShot(300, lambda: run_wizard(start="reconnect"))
                return
        try:
            self.connect({"uri": uri, "db": db}, ident, p.GetString("Name"), timeout=3)
        except HubRequestError as e:
            self.set_state("disconnected", friendly_error(e, uri))

    def connect(self, cfg, identity=None, name=None, timeout=30):
        """Join (or rejoin) the team in `cfg` {"uri", "db"} with this person's SpacetimeDB `identity`.

        The identity is saved under the app folder per team, so opening FreeCAD again just rejoins."""
        p = prefs()
        name = name or (identity or {}).get("name") or p.GetString("Name")
        self._end_session()  # a previous session's threads must never act on this one
        client = SpaceHub({"uri": cfg["uri"], "db": cfg["db"], "name": name,
                           "host_dir": host_dir_for(cfg["db"])}, dict(identity or {}), timeout=timeout)
        self.ident = client.join(client="freecad")  # a new fence: an older window can no longer act
        client.timeout = 30
        client.identity = self.ident
        self.client = client
        p.SetString("Uri", cfg["uri"])
        p.SetString("Db", cfg["db"])
        p.SetString("Name", self.ident.get("name") or name)
        save_identity(cfg["db"], self.ident)
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
        say(f"Joined the team in {cfg['db']} as {self.ident.get('name')}")

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
        return SpaceHub(dict(self.client.cfg), dict(self.client.identity), timeout=40)

    def _heartbeat_loop(self, stop, events, c, _seq):
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                c.post("/heartbeat", {"doing": "watching in FreeCAD"})
            except HubRequestError as e:
                if e.code in ("taken_over", "fence_stale", "left", "bad_token", "not_member"):
                    events.put(("fatal", friendly_error(e)))
                    return

    def _poll_loop(self, stop, events, c, seq):
        offline = False
        while not stop.is_set():
            try:
                r = c.get("/events", since=seq, wait=20)
            except HubRequestError as e:
                if e.code in ("taken_over", "fence_stale", "left", "bad_token", "not_member"):
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
                if data.get("kind") == "fea_report":
                    open_fea_report(data["folder"])
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
        self.invite_button.setToolTip("Copy a new single-use invite code for a teammate")
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
        self.to.setSizeAdjustPolicy(QtGui.QComboBox.AdjustToContents)  # show whole names, not "te:"
        self.to.setMinimumContentsLength(8)
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
                          ("Simulation (FEA) reports…", show_fea_reports),
                          ("Storage and free up space…", show_storage),
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
            stopped = s.state_detail == STOPPED_HOSTING
            if left:
                headline = "You left the team session. Your locks were released."
            elif stopped:
                headline = "You stopped hosting the team. Your project is saved."
            detail = ("Join again to keep working with your team." if left else
                      "Your team's AI helpers are offline until you start it again "
                      "(Get started ▸ Reconnect)." if stopped else
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
        self.invite_button.setVisible(s.state == "connected")
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


CLIENT_WORDS = {"claude": "Claude Code helper", "codex": "Codex helper", "droid": "Droid helper"}
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
    if k == "fea_started":
        what = {"static": "a strength check", "modal": "a vibration check", "thermal": "a heat check"}
        return f"{who} started {what.get(ev.get('analysis'), 'a simulation')} of {names(ev.get('target') or [])}…"
    if k == "fea_report":
        return f"{who} published a simulation report: {ev.get('title')} — {ev.get('headline')} " \
               "(Team tools ▸ Simulation reports)"
    if k == "fea_progress":
        return f"{who}'s simulation: {ev.get('text')}"
    if k == "fea_failed":
        return f"{who}'s simulation of {names(ev.get('target') or [])} failed: {ev.get('error')}"
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
    """Mint a fresh single-use code for one teammate (a used code can never be handed on)."""
    if not SESSION.ensure_connected("invite teammates"):
        return
    try:
        code = SESSION.client.create_invite("human", "member", 86400, prefs().GetString("ProjectName"))
    except HubRequestError as e:
        QtGui.QMessageBox.warning(Gui.getMainWindow(), "CadSwarm", friendly_error(e, SESSION.client.hub))
        return
    QtGui.QApplication.clipboard().setText(code)
    QtGui.QMessageBox.information(
        Gui.getMainWindow(), "Invite teammates",
        "The invite code is copied. Paste it to one teammate (email, Discord, text...).\n\n"
        f"{code}\n\nThis code works once. Make a new one for each teammate.\n\nThey open FreeCAD, pick "
        "CadSwarm, click Get started, then \u201cJoin a teammate\u2019s project\u201d.")


STOPPED_HOSTING = "You stopped hosting the team."


def stop_hosting():
    from .wizard import stop_hosting as stop
    stop()


def hosted_project_folder():
    """The project folder this computer hosts for the team it is in now, or None."""
    folder = host_dir_for(prefs().GetString("Db"))
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
            "Notes, plans and scripts your team's AI helpers saved. The last 20 versions of each file are kept."
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


def size_text(n):
    """Bytes the way people say them: '320 MB', '1.5 GB'."""
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            v = n / size
            return f"{v:.1f} {unit}" if v < 10 else f"{v:.0f} {unit}"
    return f"{n} bytes"


class StorageDialog(QtGui.QDialog):
    """What is using the project's room, and a careful way to free some. Everything runs on the GUI
    thread through the same blocking client calls the other dialogs use (they are quick)."""

    def __init__(self):
        super().__init__(Gui.getMainWindow())
        self.setWindowTitle("CadSwarm — storage")
        self.resize(560, 520)
        self.is_host = hosted_project_folder() is not None
        lay = QtGui.QVBoxLayout(self)
        self.summary = QtGui.QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(QtCore.Qt.RichText)
        lay.addWidget(self.summary)

        box = QtGui.QGroupBox("Free up space (this deletes things for good)")
        form = QtGui.QFormLayout(box)
        self.versions = self.spin(1, 1000, 5, " newest")
        self.snap_days = self.spin(1, 3650, 30, " days")
        self.snap_keep = self.spin(0, 1000, 3, " newest")
        self.exp_days = self.spin(1, 3650, 30, " days")
        self.do_versions = QtGui.QCheckBox("Trim older versions of files, keeping only the")
        self.do_snaps = QtGui.QCheckBox("Delete model snapshots older than")
        self.do_exports = QtGui.QCheckBox("Delete exported 3D files older than")
        for check in (self.do_versions, self.do_snaps, self.do_exports):
            check.setChecked(True)
        form.addRow(self.do_versions, self.versions)
        form.addRow(self.do_snaps, self.snap_days)
        form.addRow("   …but always keep the", self.snap_keep)
        form.addRow(self.do_exports, self.exp_days)
        free = QtGui.QPushButton("Free up space…")
        free.clicked.connect(self.free_up)
        form.addRow(free)
        lay.addWidget(box)

        if self.is_host:  # only the person running the team server sets its limits
            sbox = QtGui.QGroupBox("Limits (host only)")
            sform = QtGui.QFormLayout(sbox)
            self.limit = QtGui.QDoubleSpinBox()
            self.limit.setRange(0, 100000)
            self.limit.setDecimals(1)
            self.limit.setSuffix(" GB")
            self.limit.setSpecialValueText("No project limit")
            self.keep = self.spin(1, 1000, 20, " versions per file")
            self.hours = self.spin(0, 24 * 365, 24, " hours")
            sform.addRow("Project size limit", self.limit)
            sform.addRow("Keep older versions of each file:", self.keep)
            sform.addRow("…and everything saved in the last", self.hours)
            save = QtGui.QPushButton("Save limits")
            save.clicked.connect(self.save_limits)
            sform.addRow(save)
            lay.addWidget(sbox)
        row = QtGui.QHBoxLayout()
        row.addStretch(1)
        close = QtGui.QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.load()

    @staticmethod
    def spin(low, high, value, suffix):
        box = QtGui.QSpinBox()
        box.setRange(low, high)
        box.setValue(value)
        box.setSuffix(suffix)
        return box

    def load(self):
        try:
            st = SESSION.client.get("/project/storage")
        except HubRequestError as e:
            hint(friendly_error(e))
            return
        u, s = st["usage"], st["settings"]
        limit, free = st["limit_bytes"], st["free_disk_bytes"]
        lines = [f"<b>The project uses {size_text(u['total'])}</b>"
                 + (f" of its {size_text(limit)} limit" if limit else " (no project limit set)"),
                 f"&bull; Your team's files: {size_text(u['files'])}",
                 f"&bull; Older versions of those files: {size_text(u['old_versions'])}",
                 f"&bull; Model snapshots: {size_text(u['snapshots'])}",
                 f"&bull; Exported 3D files: {size_text(u['exports'])}",
                 f"&bull; The live model: {size_text(u['model'])} (not counted)"]
        if free is not None:
            lines.append(f"<br>The host's drive has {size_text(free)} free; CadSwarm stops saving when less than "
                         f"{size_text(st['reserve_bytes'])} would be left.")
        lines.append(f"<small>Older versions: the newest {s['history_keep']} of each file are kept, plus everything from "
                     f"the last {s['history_hours']} hours. Snapshots and exports are only deleted here, by a person.</small>")
        self.summary.setText("<br>".join(lines))
        if self.is_host:
            self.limit.setValue(s["project_limit_gb"])
            self.keep.setValue(s["history_keep"])
            self.hours.setValue(int(s["history_hours"]))
            if st.get("limit_source") == "env":
                self.limit.setEnabled(False)
                self.limit.setToolTip("Set by the CADSWARM_PROJECT_LIMIT_GB environment variable.")

    def options(self):
        o = {}
        if self.do_versions.isChecked():
            o["keep_versions"] = self.versions.value()
        if self.do_snaps.isChecked():
            o["snapshots_older_than_days"] = self.snap_days.value()
            o["keep_newest_snapshots"] = self.snap_keep.value()
        if self.do_exports.isChecked():
            o["exports_older_than_days"] = self.exp_days.value()
        return o

    def free_up(self):
        o = self.options()
        if not o:
            hint("Tick at least one thing to clean up.")
            return
        will = []
        if "keep_versions" in o:
            will.append(f"- older versions of files: all but the newest {o['keep_versions']} of each file")
        if "snapshots_older_than_days" in o:
            will.append(f"- model snapshots older than {o['snapshots_older_than_days']} days "
                        f"(the newest {o['keep_newest_snapshots']} stay)")
        if "exports_older_than_days" in o:
            will.append(f"- exported 3D files older than {o['exports_older_than_days']} days")
        if not confirm("This will delete, for good:\n\n" + "\n".join(will) +
                       "\n\nYour current files and the live model are not touched. Continue?"):
            return
        try:
            r = SESSION.client.post("/project/storage/prune", o)
        except HubRequestError as e:
            hint(friendly_error(e))
            return
        f = r["freed"]
        say(f"Freed {size_text(f['total'])} of project storage")
        hint(f"Freed {size_text(f['total'])}: older versions {size_text(f['old_versions'])}, "
             f"snapshots {size_text(f['snapshots'])}, exports {size_text(f['exports'])}.")
        self.load()

    def save_limits(self):
        try:
            SESSION.client.post("/project/storage/settings", {
                "project_limit_gb": self.limit.value(), "history_keep": self.keep.value(),
                "history_hours": self.hours.value()})
        except HubRequestError as e:
            hint(friendly_error(e))
            return
        hint("Saved. The new limits apply to the next save.")
        self.load()


def show_storage():
    if not SESSION.ensure_connected("see the project's storage"):
        return
    dlg = StorageDialog()
    (getattr(dlg, "exec", None) or dlg.exec_)()


VERDICT_WORDS = {"ok": ("#1a7f37", "Looks OK"), "check": ("#9a6700", "Check the warnings"),
                 "fail": ("#cf222e", "Needs changes")}


class FeaReportDialog(QtGui.QDialog):
    """One simulation report, as it pops up for everyone on the team. Non-modal, so work can go on."""

    def __init__(self, folder):
        super().__init__(Gui.getMainWindow())
        self.folder = folder
        self.report = json.loads(SESSION.client.post("/project/files/read", {"path": f"{folder}/report.json"})["text"])
        page = SESSION.client.post("/project/files/read", {"path": f"{folder}/report.html"})["text"]
        self.png = None
        if self.report["files"].get("plot"):
            try:
                self.png = SESSION.client.request(
                    "GET", "/project/files/download?" + urlencode({"path": self.report["files"]["plot"]}), raw=True)
            except HubRequestError:
                pass
        r = self.report
        self.setWindowTitle(f"CadSwarm — simulation report: {r['title']}")
        self.resize(780, 720)
        lay = QtGui.QVBoxLayout(self)
        color, word = VERDICT_WORDS.get(r["verdict"], VERDICT_WORDS["check"])
        head = QtGui.QLabel(f"<b style='font-size:13pt'>{html.escape(r['title'])}</b><br>"
                            f"<span style='color:#8b949e'>{html.escape(r['analysis_name'])} by {html.escape(r['by'])}"
                            f"</span><br><span style='background:{color};color:white;font-weight:bold'>&nbsp;{word}&nbsp;"
                            f"</span>&nbsp; {html.escape(r['headline'])}"
                            + ("<br><span style='color:#9a6700'><b>Uses placeholder values</b> — see the yellow box"
                               "</span>" if r["inputs"]["placeholders"] else ""))
        head.setTextFormat(QtCore.Qt.RichText)
        lay.addWidget(head)
        self.view = QtGui.QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setStyleSheet("QTextBrowser { background: white; color: #1f2328; }")  # paper, even in dark themes
        if self.png:
            img = QtGui.QImage.fromData(self.png)
            self.view.document().addResource(QtGui.QTextDocument.ImageResource, QtCore.QUrl("plot.png"), img)
        self.view.setHtml(page)
        lay.addWidget(self.view, 1)
        row = QtGui.QHBoxLayout()
        for label, fn in (("Export HTML…", self.export_html), ("Export JSON…", self.export_json)):
            b = QtGui.QPushButton(label)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addWidget(QtGui.QLabel(f"<small style='color:#8b949e'>Saved in the project: files/{html.escape(folder)}"
                                   "</small>"))
        row.addStretch(1)
        close = QtGui.QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)

    def _save(self, title, ext, pattern, data):
        name = f"{safe_name(self.report['title'])} report.{ext}"
        dest, _ = QtGui.QFileDialog.getSaveFileName(self, title, name, pattern)
        if not dest:
            return
        with open(dest, "wb") as f:
            f.write(data)
        say(f"Saved the simulation report to {dest}")

    def export_html(self):
        from ..fea import standalone_html
        self._save("Export report as HTML", "html", "Web page (*.html)",
                   standalone_html(self.report, self.png).encode("utf-8"))

    def export_json(self):
        self._save("Export report data as JSON", "json", "JSON (*.json)",
                   json.dumps(self.report, indent=2).encode("utf-8"))

    def closeEvent(self, ev):
        if self in SESSION.fea_dialogs:
            SESSION.fea_dialogs.remove(self)
        super().closeEvent(ev)


def safe_name(text):
    return "".join(c if c.isalnum() or c in " -_." else " " for c in text).strip()[:60] or "simulation"


def open_fea_report(folder):
    """Pop up a report (from the team's event stream or the reports list). At most 5 stay open."""
    for d in SESSION.fea_dialogs:
        if d.folder == folder:
            d.raise_()
            d.activateWindow()
            return
    try:
        dlg = FeaReportDialog(folder)
    except (HubRequestError, ValueError, KeyError) as e:
        warn(f"Couldn't open the simulation report in {folder}: {e}")
        return
    while len(SESSION.fea_dialogs) >= 5:
        SESSION.fea_dialogs.pop(0).close()
    SESSION.fea_dialogs.append(dlg)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


class FeaReportsDialog(QtGui.QDialog):
    """Every simulation report the team has published; double-click to open one."""

    def __init__(self):
        super().__init__(Gui.getMainWindow())
        self.setWindowTitle("CadSwarm — simulation reports")
        self.resize(720, 400)
        lay = QtGui.QVBoxLayout(self)
        lay.addWidget(QtGui.QLabel("Simulations (FEA) your team's AI helpers ran. Double-click one to open it."))
        self.tree = QtGui.QTreeWidget()
        self.tree.setHeaderLabels(["When", "Report", "Result", "Verdict", "By"])
        self.tree.setColumnWidth(0, 110)
        self.tree.setColumnWidth(1, 220)
        self.tree.setColumnWidth(2, 220)
        self.tree.itemDoubleClicked.connect(lambda item, _col: self.open(item))
        lay.addWidget(self.tree, 1)
        row = QtGui.QHBoxLayout()
        b = QtGui.QPushButton("Open")
        b.clicked.connect(lambda: self.open(self.tree.currentItem()))
        row.addWidget(b)
        row.addStretch(1)
        close = QtGui.QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        try:
            reports = SESSION.client.get("/fea/reports")["reports"]
        except HubRequestError as e:
            hint(friendly_error(e))
            reports = []
        for r in reports:
            when = time.strftime("%b %d %H:%M", time.localtime(r["published_at"]))
            item = QtGui.QTreeWidgetItem([when, r["title"], r["headline"],
                                          VERDICT_WORDS.get(r["verdict"], ("", r["verdict"]))[1]
                                          + (" (placeholders)" if r["placeholders"] else ""), r["by"]])
            item.setData(0, QtCore.Qt.UserRole, r["folder"])
            self.tree.addTopLevelItem(item)
        if not reports:
            QtGui.QTreeWidgetItem(self.tree, ["", "(no simulations yet — ask an AI helper \"will this hold 5 kg?\")"])

    def open(self, item):
        folder = item.data(0, QtCore.Qt.UserRole) if item else None
        if folder:
            open_fea_report(folder)


def show_fea_reports():
    if not SESSION.ensure_connected("see the team's simulation reports"):
        return
    dlg = FeaReportsDialog()
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
    Gui.addCommand("CadSwarm_AddHelper", Cmd("Add AI helper…", "Connect an AI helper (Claude Code, Codex or "
                                                              "Droid) to your team", add_helper))
    Gui.addCommand("CadSwarm_Invite", Cmd("Invite…", "Copy a new invite code for a teammate", invite_teammates))
    Gui.addCommand("CadSwarm_StopHosting", Cmd("Stop hosting", "Stop the team server on this computer",
                                               stop_hosting, False))
    Gui.addCommand("CadSwarm_Panel", Cmd("Team panel", "Show presence, locks, tasks and chat", show_panel, False))
    Gui.addCommand("CadSwarm_Lock", Cmd("Lock selected", "Lock the selected mirror parts to you", lock_selected))
    Gui.addCommand("CadSwarm_Release", Cmd("Release selected", "Release your locks on the selected parts", release_selected))
    Gui.addCommand("CadSwarm_Interference", Cmd("Check for clashes", "Find parts that overlap", run_interference))
    Gui.addCommand("CadSwarm_Export", Cmd("Save as 3D file…", "Save the shared model as a STEP file", run_export))
    Gui.addCommand("CadSwarm_FeaReports", Cmd("Simulation reports…", "Open the team's FEA (simulation) reports",
                                              show_fea_reports))
    Gui.addCommand("CadSwarm_Disconnect", Cmd("Leave team", "Leave the session (releases your locks)",
                                              lambda: SESSION.disconnect() if SESSION.connected else None, False))
