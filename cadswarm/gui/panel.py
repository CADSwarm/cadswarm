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
        self.commands = []     # instructions I sent to helpers, with sent/seen/done
        self.briefs = []       # requests handed to the whole team (the host plans and builds them)
        self.editing = {}      # part name -> its open EditPartDialog
        self.synced_pl = {}    # part name -> mirror Placement as the hub last sent it
        self._moved_hint = 0
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
        client.timeout = client.space.timeout = 30  # the quick rejoin timeout is only for the first call
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

    def participant(self, name):
        return next((p for p in self.model.get("participants", []) if p["name"] == name), None)

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
        self.guard_local_moves()

    def guard_local_moves(self):
        """The live model shows the team's parts. A part moved by hand without Edit would silently differ
        from what everyone else sees, so put it back and say how to change it properly."""
        doc = App.listDocuments().get(MIRROR_DOC)
        if doc is None or Gui.Control.activeDialog():
            return  # a Placement/Transform dialog is open: judge the move once it's closed
        moved = []
        for name, pl in self.synced_pl.items():
            if name in self.editing:
                continue
            obj = doc.getObject(name)
            if obj is not None and not same_placement(obj.Placement, pl):
                obj.Placement = pl
                moved.append(obj.Label.split(" [")[0])
        if moved and time.time() - self._moved_hint > 20:
            self._moved_hint = time.time()
            hint(f"{', '.join(moved)} is shared with your team, so the move was undone.\n\n"
                 "To change a part yourself, select it and click \u270e Edit a part\u2026 in the CadSwarm panel "
                 "(or start the edit first, then move it). Your change is sent to everyone when you click Save.")

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
            self.synced_pl[name] = obj.Placement
            owner = info.get("locked_by")
            set_if_changed(obj, "Label", f"{info.get('label') or name} [{owner or 'unlocked'}]")
            vo = getattr(obj, "ViewObject", None)
            if vo:
                set_if_changed(vo, "Visibility", bool(info.get("visible", True)))
                set_if_changed(vo, "ShapeColor", owner_color(info.get("owner_id")))
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
            self.synced_pl.pop(n, None)
            if doc.getObject(n):
                doc.removeObject(n)
        doc.recompute()

    def full_refresh(self):
        self.apply_breps(self.client.post("/brep", {})["objects"])
        if self.panel:
            try:
                self.panel.load_history(self.client.get("/messages", limit=50)["messages"], self.ident["name"])
            except HubRequestError:
                pass
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
            self.commands = self.client.get("/commands").get("commands", [])
            try:
                self.briefs = self.client.get("/briefs").get("briefs", [])
            except HubRequestError:
                self.briefs = []  # an older team server without requests
            owners = {o["name"]: o.get("locked_by") for o in self.model["objects"]}
            ids = {p["name"]: p["id"] for p in self.model["participants"]}
            for p in self.model["participants"]:
                owner_color(p["id"])  # reserve colors in join order
            doc = self.mirror()
            for o in doc.Objects:  # recolor on lock moves (takeover, release) without new geometry
                owner = owners.get(o.Name)
                if getattr(o, "ViewObject", None):
                    set_if_changed(o.ViewObject, "ShapeColor", owner_color(ids.get(owner)))
                editing = " \u00b7 you're editing" if o.Name in self.editing else ""
                set_if_changed(o, "Label", f"{o.Label.split(' [')[0]} [{owner or 'unlocked'}{editing}]")
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


def set_if_changed(obj, prop, value):
    """Only write a property that really changed: every write marks the document modified and makes
    FreeCAD redraw and re-check its panels."""
    try:
        current = getattr(obj, prop)
        if prop == "ShapeColor":
            if all(abs(a - b) < 1e-3 for a, b in zip(current[:3], value[:3])):
                return
        elif current == value:
            return
        setattr(obj, prop, value)
    except Exception:
        pass


SESSION = Session()


# ------------------------------------------------------------------ panel

class TeamPanel(QtGui.QDockWidget):
    """The team at a glance, then four tabs that keep different kinds of talk apart:
    Tasks (what needs doing), Tell a helper (instructions an AI helper must follow), Team chat
    (people talking) and Activity (what happened, written by CadSwarm)."""

    SPINNER = "◐◓◑◒"

    def __init__(self, session):
        super().__init__("CadSwarm Team")
        self.session = session
        self.setObjectName("CadSwarmTeam")
        self.unread = {}
        self.spin_i = 0
        self.busy_rows = []     # (item, column, text) redrawn with a turning spinner
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

        lay.addWidget(QtGui.QLabel("<b>Who's here</b>"))
        self.team = QtGui.QTreeWidget()
        self.team.setHeaderLabels(["Name", "Right now"])
        self.team.setColumnWidth(0, 200)
        self.team.setRootIsDecorated(True)
        self.team.itemDoubleClicked.connect(self.team_double_click)
        self.team.setMinimumHeight(130)  # at least three teammates visible
        lay.addWidget(self.team, 2)

        self.tabs = QtGui.QTabWidget()
        self.tabs.currentChanged.connect(self.tab_opened)
        self.tabs.addTab(self.build_tasks_tab(), "Tasks")
        self.tabs.addTab(self.build_command_tab(), "Tell a helper")
        self.tabs.addTab(self.build_chat_tab(), "Team chat")
        self.tabs.addTab(self.build_activity_tab(), "Activity")
        self.tab_names = ["Tasks", "Tell a helper", "Team chat", "Activity"]
        lay.addWidget(self.tabs, 5)

        edit = QtGui.QGroupBox("Change the model yourself")
        grid = QtGui.QGridLayout(edit)
        buttons = [("✎ Edit a part…", edit_part, "Change a part's size or position yourself; your change is shared "
                                                  "with everyone when you click Save"),
                   ("Change a shared size…", self.set_param, "e.g. make every hole 5 mm: every part that uses it updates"),
                   ("Check for clashes", run_interference, "Find parts that overlap each other"),
                   ("Save as 3D file…", run_export, "Save a STEP file for printing or other CAD programs")]
        for i, (label, fn, tip) in enumerate(buttons):
            b = QtGui.QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            grid.addWidget(b, i // 2, i % 2)
        self.params = QtGui.QLabel("")
        self.params.setWordWrap(True)
        grid.addWidget(self.params, 2, 0, 1, 2)
        lay.addWidget(edit)

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
        w.setObjectName("cadswarmPanel")
        w.setStyleSheet(PANEL_STYLE)
        self.setMinimumWidth(PANEL_MIN_WIDTH)
        self.setWidget(w)
        self.spin_timer = QtCore.QTimer(self)
        self.spin_timer.timeout.connect(self.spin)
        self.spin_timer.start(350)
        self.show_state(session)

    # ------------------------------------------------------------- tab builders

    def build_tasks_tab(self):
        w = QtGui.QWidget()
        lay = QtGui.QVBoxLayout(w)
        new = QtGui.QPushButton("＋  New task…")
        new.setToolTip("Describe what needs doing and pick who does it — an AI helper, or you")
        new.setStyleSheet("QPushButton { font-weight: bold; padding: 6px; }")
        new.clicked.connect(self.new_task)
        lay.addWidget(new)
        self.task_list = QtGui.QTreeWidget()
        self.task_list.setHeaderLabels(["Task", "Status", "Who"])
        self.task_list.setColumnWidth(0, 240)
        self.task_list.setColumnWidth(1, 130)
        self.task_list.itemSelectionChanged.connect(self.task_selected)
        self.task_list.itemDoubleClicked.connect(lambda *_: self.task_details())
        lay.addWidget(self.task_list, 1)
        row = QtGui.QHBoxLayout()
        self.task_start = QtGui.QPushButton("Ask to start")
        self.task_start.setToolTip("Send the assigned helper an instruction to start this task now")
        self.task_start.clicked.connect(self.task_ask_start)
        self.task_mine = QtGui.QPushButton("I'll do it")
        self.task_mine.setToolTip("Take this task yourself and change the parts by hand")
        self.task_mine.clicked.connect(self.task_take)
        self.task_done = QtGui.QPushButton("Mark done")
        self.task_done.clicked.connect(self.task_finish)
        details = QtGui.QPushButton("Details")
        details.clicked.connect(self.task_details)
        for b in (self.task_start, self.task_mine, self.task_done, details):
            row.addWidget(b)
        lay.addLayout(row)
        self.task_selected()
        return w

    def build_command_tab(self):
        w = QtGui.QWidget()
        w.setObjectName("commandTab")
        lay = QtGui.QVBoxLayout(w)
        intro = QtGui.QLabel("<b style='color:#d9822b'>Tell the AI helpers what to do.</b> Pick a helper and it does "
                             "your instruction before anything else, then tells you when it's done. Pick "
                             "<b>The whole team</b> to have it planned into tasks and built for you. To talk with "
                             "people, use Team chat.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QtGui.QHBoxLayout()
        row.addWidget(QtGui.QLabel("Helper:"))
        self.cmd_to = QtGui.QComboBox()
        self.cmd_to.setSizeAdjustPolicy(QtGui.QComboBox.AdjustToContents)
        row.addWidget(self.cmd_to, 1)
        lay.addLayout(row)
        quick = QtGui.QHBoxLayout()
        quick.setSpacing(4)
        for label, text in QUICK_COMMANDS:
            b = QtGui.QToolButton()
            b.setText(label)
            b.setToolTip(text.replace("{parts}", "the selected parts"))
            b.clicked.connect(lambda _=False, t=text: self.fill_command(t))
            quick.addWidget(b)
        quick.addStretch(1)
        lay.addLayout(quick)
        self.cmd_text = QtGui.QPlainTextEdit()
        self.cmd_text.setPlaceholderText("e.g. Make the lid 20 mm tall and check it still clears the posts")
        self.cmd_text.setMaximumHeight(85)
        self.cmd_text.setStyleSheet("QPlainTextEdit { border: 1px solid #d9822b; border-radius: 4px; }")
        lay.addWidget(self.cmd_text)
        send_row = QtGui.QHBoxLayout()
        self.cmd_hint = QtGui.QLabel("<small style='color:#8b949e'>Ctrl+Enter to send</small>")
        send_row.addWidget(self.cmd_hint, 1)
        send = QtGui.QPushButton("Send instruction ▸")
        send.setStyleSheet("QPushButton { background: #d9822b; color: white; font-weight: bold; padding: 5px 12px; }")
        send.clicked.connect(self.send_command)
        send_row.addWidget(send)
        lay.addLayout(send_row)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self.cmd_text, activated=self.send_command)
        lay.addWidget(QtGui.QLabel("<b>Your instructions</b>"))
        self.cmd_list = QtGui.QTreeWidget()
        self.cmd_list.setHeaderLabels(["Instruction", "To", "Status"])
        self.cmd_list.setColumnWidth(0, 230)
        self.cmd_list.setColumnWidth(1, 110)
        self.cmd_list.setRootIsDecorated(False)
        self.cmd_list.setMinimumHeight(110)  # at least three instructions visible
        lay.addWidget(self.cmd_list, 1)
        self.cmd_wait = QtGui.QLabel("")
        self.cmd_wait.setWordWrap(True)
        lay.addWidget(self.cmd_wait)
        return w

    def build_chat_tab(self):
        w = QtGui.QWidget()
        lay = QtGui.QVBoxLayout(w)
        intro = QtGui.QLabel("<b style='color:#3e9be9'>Talk with your teammates.</b> AI helpers can read this, but "
                             "they don't take it as instructions.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.chat = QtGui.QTextEdit()
        self.chat.setReadOnly(True)
        lay.addWidget(self.chat, 1)
        row = QtGui.QHBoxLayout()
        self.to = QtGui.QComboBox()
        self.to.setSizeAdjustPolicy(QtGui.QComboBox.AdjustToContents)  # show whole names, not "te:"
        self.to.setMinimumContentsLength(8)
        self.to.addItem("Everyone", "all")
        self.msg = QtGui.QLineEdit()
        self.msg.setPlaceholderText("Message your team…")
        self.msg.returnPressed.connect(self.send)
        send = QtGui.QPushButton("Send")
        send.clicked.connect(self.send)
        row.addWidget(self.to)
        row.addWidget(self.msg, 1)
        row.addWidget(send)
        lay.addLayout(row)
        return w

    def build_activity_tab(self):
        w = QtGui.QWidget()
        lay = QtGui.QVBoxLayout(w)
        self.activity = QtGui.QTextEdit()
        self.activity.setReadOnly(True)
        lay.addWidget(self.activity, 1)
        return w

    # ------------------------------------------------------------------ state

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
            working = [p for p in others if helper_state(p)[0] == "working"]
            headline = (f"<b>You're in the team session as {me}.</b> "
                        f"{len(others)} teammate{'s' if len(others) != 1 else ''} online"
                        + (f", {len(working)} helper{'s' if len(working) != 1 else ''} working." if working else "."))
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
                      "Start or join a team to watch the shared model being built and work with your "
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
        self.msg.setPlaceholderText("Message your team — press Enter to send" if connected else
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
                        + ("" if parts else " Nothing built yet — click <b>＋ New task</b> to give a helper "
                                            "something to build.") + "</small>")
        self.busy_rows = []
        pending = {}
        for c in s.commands:
            if c["state"] == "sent":
                for name, st in c["by_helper"].items():
                    if st == "sent":
                        pending[name] = pending.get(name, 0) + 1
        expanded = {self.team.topLevelItem(i).data(0, QtCore.Qt.UserRole)
                    for i in range(self.team.topLevelItemCount()) if self.team.topLevelItem(i).isExpanded()}
        self.team.clear()
        chat_to, cmd_to = self.to.currentData(), self.cmd_to.currentData()
        self.to.clear()
        self.to.addItem("Everyone", "all")
        self.cmd_to.clear()
        helpers = [p for p in s.model["participants"] if takes_instructions(p) and p["name"] != me]
        for p in s.model["participants"]:
            state, text = helper_state(p)
            name = f"{p['name']}{' (you)' if p['name'] == me else ''}"
            item = QtGui.QTreeWidgetItem([name, text])
            item.setData(0, QtCore.Qt.UserRole, p["name"])
            item.setToolTip(0, who_is(p))
            item.setToolTip(1, f"{who_is(p)} — {text}")
            r, g, b = owner_color(p["id"])
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(int(r * 200), int(g * 200), int(b * 200))))
            item.setText(0, ("🤖 " if p.get("kind") != "human" else "🧑 ") + name)
            colour = {"working": "#d9822b", "ready": "#1a7f37", "away": "#8b949e"}.get(state)
            if colour:
                item.setForeground(1, QtGui.QBrush(QtGui.QColor(colour)))
            if state == "working":
                self.busy_rows.append((item, 1, text))
            if pending.get(p["name"]):
                n = pending[p["name"]]
                item.setText(1, f"{text} · {n} instruction{'s' if n != 1 else ''} not read yet")
            held = [lock for lock in p["locks"] if lock != "Params"]
            for lock in held:
                QtGui.QTreeWidgetItem(item, [f"🔒 {lock}", "only they can change it"])
            self.team.addTopLevelItem(item)
            item.setExpanded(p["name"] in expanded)
            if held and not item.isExpanded():
                item.setText(1, item.text(1) + f" · holds {len(held)} part{'s' if len(held) != 1 else ''}")
            if p["name"] != me and p.get("kind") == "human":
                self.to.addItem(p["name"], p["name"])
        for p in helpers:
            state, text = helper_state(p)
            self.cmd_to.addItem(f"{p['name']} — {text}", p["name"])
        if len(helpers) > 1:
            self.cmd_to.addItem("All helpers", "all")
        # Always there, even before anyone opens a helper: the team server plans it and starts helpers itself.
        self.cmd_to.addItem("🧠 The whole team — plan it and build it (starts AI helpers for you)", TEAM_REQUEST)
        for combo, want in ((self.to, chat_to), (self.cmd_to, cmd_to)):
            idx = combo.findData(want)
            combo.setCurrentIndex(max(idx, 0))
        self.render_tasks(s)
        self.render_commands(s)
        params = s.model.get("params") or {}
        self.params.setText("<small><b>Shared sizes:</b> " + (", ".join(
            f"{k.replace('_', ' ')} = {v:g} mm" if isinstance(v, (int, float)) else f"{k.replace('_', ' ')} = {v}"
            for k, v in params.items()) or "none yet") + "</small>")

    def render_tasks(self, s):
        keep = self.selected_task_id()
        self.task_list.clear()
        if not s.tasks:
            empty = QtGui.QTreeWidgetItem(["No tasks yet. Click ＋ New task to give your helpers something "
                                           "to build, or to plan work for yourself.", "", ""])
            empty.setFlags(QtCore.Qt.NoItemFlags)
            empty.setFirstColumnSpanned(True)
            self.task_list.addTopLevelItem(empty)
        for t in sorted(s.tasks, key=lambda t: TASK_ORDER.get(t["status"], 9)):
            who = t.get("owner_name") or t.get("assignee") or "anyone"
            word = TASK_WORDS.get(t["status"], t["status"])
            item = QtGui.QTreeWidgetItem([t["title"], word, who])
            item.setData(0, QtCore.Qt.UserRole, t["id"])
            item.setToolTip(0, (t.get("detail") or t["title"])[:600])
            colour = {"claimed": "#d9822b", "done": "#1a7f37", "blocked": "#cf222e",
                      "awaiting_takeover": "#9a6700"}.get(t["status"])
            if colour:
                item.setForeground(1, QtGui.QBrush(QtGui.QColor(colour)))
            if t["status"] == "claimed":
                self.busy_rows.append((item, 1, word))
            self.task_list.addTopLevelItem(item)
            if t["id"] == keep:
                item.setSelected(True)
        self.task_selected()

    def render_commands(self, s):
        self.cmd_list.clear()
        for b in s.briefs:
            word = BRIEF_WORDS.get(b["status"], b["status"])
            if b["status"] in ("building", "reviewing") and b["tasks"]:
                word += f" ({b['done']}/{b['tasks']} parts done)"
            item = QtGui.QTreeWidgetItem([b["text"], "whole team", word])
            item.setToolTip(0, f"{b['text']}\n(asked by {b['by']})")
            item.setForeground(2, QtGui.QBrush(QtGui.QColor(
                {"ready": "#1a7f37", "failed": "#cf222e"}.get(b["status"], "#d9822b"))))
            self.cmd_list.addTopLevelItem(item)
        waiting = []
        team = {p["name"]: p for p in s.model.get("participants", [])}
        for c in s.commands:
            word = COMMAND_WORDS[c["state"]]
            if c["to"] == "all helpers" and c["state"] != "done":
                done = sum(1 for v in c["by_helper"].values() if v == "done")
                word += f" ({done}/{len(c['by_helper'])} done)"
            task = next((t for t in s.tasks if t["id"] == c.get("task_id")), None)
            shown = f"▶ Start “{task['title']}”" if task and c["body"].startswith("Please start task") else c["body"]
            item = QtGui.QTreeWidgetItem([shown, c["to"], word])
            item.setToolTip(0, c["body"])
            item.setToolTip(2, "\n".join(f"{n}: {COMMAND_WORDS[v]}" for n, v in c["by_helper"].items()))
            item.setForeground(2, QtGui.QBrush(QtGui.QColor(
                {"sent": "#9a6700", "seen": "#d9822b", "done": "#1a7f37"}[c["state"]])))
            self.cmd_list.addTopLevelItem(item)
            age = time.time() - c["at"]
            if c["state"] == "sent" and age > 45:
                waiting += [n for n, v in c["by_helper"].items() if v == "sent" and n not in waiting]
        tips = []
        for n in waiting:
            p = team.get(n)
            if p and p.get("status") != "active":
                tips.append(f"<b>{n}</b> is away, so it can't read your instructions until its window is open again.")
            else:
                tips.append(f"<b>{n}</b> hasn't read your instruction yet. It reads new instructions when it finishes "
                            f"a step. If it has stopped, type <i>continue</i> in its Claude Code / Codex window.")
        self.cmd_wait.setText("<small>" + "<br>".join(tips) + "</small>" if tips else "")

    def spin(self):
        if not self.busy_rows:
            return
        self.spin_i = (self.spin_i + 1) % len(self.SPINNER)
        for item, col, text in self.busy_rows:
            try:
                current = item.text(col)
                suffix = current.split(text, 1)[1] if text in current else ""
                item.setText(col, f"{self.SPINNER[self.spin_i]} {text}{suffix}")
            except RuntimeError:  # the row was rebuilt while we spun
                pass

    def tab_opened(self, index):
        self.unread[index] = 0
        self.tabs.setTabText(index, self.tab_names[index] if hasattr(self, "tab_names") else self.tabs.tabText(index))

    def bump(self, index):
        if self.tabs.currentIndex() == index:
            return
        self.unread[index] = self.unread.get(index, 0) + 1
        self.tabs.setTabText(index, f"{self.tab_names[index]} ({self.unread[index]})")

    def on_event(self, ev):
        k = ev["kind"]
        me = self.session.ident["name"] if self.session.ident else None
        stamp = f"<span style='color:#8b949e'>{time.strftime('%H:%M', time.localtime(ev['ts']))}</span>"
        if k == "message":
            body = html.escape(ev["body"])
            if ev.get("message_kind") == "steer":
                if ev["name"] != me:  # someone else's instruction to a helper: worth knowing, not chat
                    self.activity.append(f"{stamp} <b style='color:#d9822b'>{html.escape(ev['name'])}</b> told "
                                         f"<b>{html.escape(ev['to'])}</b>: {body}")
                    self.bump(3)
                return
            sender = self.session.participant(ev["name"])
            helper = sender is not None and sender.get("kind") != "human"
            to = "" if ev["to"] == "all" else f" <span style='color:#8b949e'>to {html.escape(ev['to'])}</span>"
            colour = "#a371f7" if helper else "#3e9be9"
            tag = " <span style='color:#8b949e'>(AI helper)</span>" if helper else ""
            self.chat.append(f"{stamp} <b style='color:{colour}'>{html.escape(ev['name'])}</b>{tag}{to}: {body}")
            if ev["name"] != me:
                self.bump(2)
            return
        line = describe_event(ev)
        if line:
            self.activity.append(f"{stamp} <span style='color:#a78bfa'>{html.escape(line)}</span>")
            self.bump(3)

    def load_history(self, messages, me):
        self.chat.clear()
        for m in messages:
            if m["kind"] in ("steer", "notice", "help", "result"):
                continue
            sender = self.session.participant(m["from"])
            helper = sender is not None and sender.get("kind") != "human"
            stamp = f"<span style='color:#8b949e'>{time.strftime('%H:%M', time.localtime(m['at']))}</span>"
            to = "" if m["to"] == "all" else f" <span style='color:#8b949e'>to {html.escape(m['to'] or '')}</span>"
            colour = "#a371f7" if helper else "#3e9be9"
            self.chat.append(f"{stamp} <b style='color:{colour}'>{html.escape(m['from'] or '?')}</b>{to}: "
                             f"{html.escape(m['body'])}")

    # ---------------------------------------------------------------- actions

    def send(self):
        text = self.msg.text().strip()
        if not text:
            return
        # Not joined yet: keep what they typed, walk them through joining, then send it.
        if not self.session.ensure_connected("send your message"):
            return
        if self.session.act("POST", "/messages", {"to": self.to.currentData() or "all", "body": text, "kind": "chat"},
                            purpose="send your message") is not None:
            self.msg.clear()

    def fill_command(self, text):
        parts = names(self.session.selected_names()) or "the parts you're working on"
        self.cmd_text.setPlainText(text.replace("{parts}", parts))
        self.cmd_text.setFocus()

    def send_command(self):
        text = self.cmd_text.toPlainText().strip()
        if not text:
            hint("Type what the helper should do first, e.g. “Make the lid 20 mm tall”.")
            return
        if not self.session.ensure_connected("give a helper an instruction"):
            return
        to = self.cmd_to.currentData()
        if to == TEAM_REQUEST:
            if self.session.act("POST", "/briefs", {"text": text}, purpose="hand the request to the team") is not None:
                self.cmd_text.clear()
                say("Handed the request to the team: the team server plans it into tasks and starts AI helpers")
                self.session.refresh_meta()
            return
        if not to:
            hint("No AI helper has joined yet. Add one with Team tools ▸ Add an AI helper.")
            return
        if self.session.act("POST", "/messages", {"to": to, "body": text, "kind": "command"},
                            purpose="give a helper an instruction") is not None:
            self.cmd_text.clear()
            say(f"Sent an instruction to {to}")

    def team_double_click(self, item, _col):
        while item.parent() is not None:
            item = item.parent()
        p = self.session.participant(item.data(0, QtCore.Qt.UserRole))
        if p and takes_instructions(p):
            idx = self.cmd_to.findData(p["name"])
            if idx >= 0:
                self.cmd_to.setCurrentIndex(idx)
            self.tabs.setCurrentIndex(1)
            self.cmd_text.setFocus()

    def selected_or_hint(self, verb):
        if not self.session.ensure_connected(f"{verb} parts"):
            return []
        names_ = self.session.selected_names()
        if not names_:
            hint("Select one or more parts in the 3D view first (in the “CadSwarm Live” document), "
                 f"then click this again to {verb} them.")
        return names_

    def lock(self):
        names_ = self.selected_or_hint("lock")
        if names_:
            self.session.act("POST", "/locks", {"names": names_}, f"Locked {names_}", purpose="lock parts")

    def release(self):
        names_ = self.selected_or_hint("release")
        if names_:
            self.session.act("POST", "/locks/release", {"names": names_}, f"Released {names_}", purpose="release parts")

    def force_release(self):
        names_ = self.selected_or_hint("force-release")
        if names_ and confirm(f"Force-release {names_}? Their owners are told; any agent may then edit them."):
            self.session.act("POST", "/locks/release", {"names": names_, "force": True}, f"Force-released {names_}",
                             purpose="release parts")

    def take_over(self):
        if not self.session.ensure_connected("take over a teammate's work"):
            return
        item = self.team.currentItem()
        while item is not None and item.parent() is not None:
            item = item.parent()
        if item is None:
            hint("Click a teammate in the Who's here list first, then choose Take over again.")
            return
        name = item.data(0, QtCore.Qt.UserRole)
        if confirm(f"Take over {name}'s locks and tasks?"):
            self.session.act("POST", "/takeover", {"target": name, "force": True},
                             lambda r: f"Took over {r['from']}: locks {r['locks']}, tasks {r['tasks']}",
                             purpose="take over a teammate's work")

    # tasks
    def selected_task_id(self):
        items = self.task_list.selectedItems()
        return items[0].data(0, QtCore.Qt.UserRole) if items else None

    def selected_task(self):
        tid = self.selected_task_id()
        return next((t for t in self.session.tasks if t["id"] == tid), None)

    def task_selected(self):
        t = self.selected_task() if hasattr(self, "task_list") else None
        me = self.session.ident["name"] if self.session.ident else None
        self.task_start.setEnabled(bool(t and t["status"] in ("open", "awaiting_takeover", "blocked")))
        self.task_mine.setEnabled(bool(t and t["status"] in ("open", "awaiting_takeover")))
        self.task_done.setEnabled(bool(t and t["status"] == "claimed" and t.get("owner_name") == me))

    def new_task(self):
        if not self.session.ensure_connected("create a task"):
            return
        dlg = NewTaskDialog(self.session)
        (getattr(dlg, "exec", None) or dlg.exec_)()

    def task_ask_start(self):
        t = self.selected_task()
        if not t:
            return
        helper = t.get("assignee") or t.get("owner_name")
        p = self.session.participant(helper) if helper else None
        if not p or p.get("kind") == "human":
            helpers = [x["name"] for x in self.session.model["participants"] if takes_instructions(x)]
            if not helpers:
                hint("No AI helper has joined yet. Add one with Team tools ▸ Add an AI helper.")
                return
            helper, ok = QtGui.QInputDialog.getItem(self, "Ask a helper to start", "Which helper should do it?",
                                                    helpers, 0, False)
            if not ok:
                return
        self.session.act("POST", "/messages", {"to": helper, "kind": "command", "task_id": t["id"],
                                                "body": start_instruction(t)},
                         f"Asked {helper} to start “{t['title']}”", purpose="start a task")
        self.tabs.setCurrentIndex(1)

    def task_take(self):
        t = self.selected_task()
        if t and self.session.act("POST", f"/tasks/{t['id']}/claim", {}, f"You took “{t['title']}”",
                                  purpose="take a task") is not None:
            if t.get("objects"):
                Gui.Selection.clearSelection()
                for n in t["objects"]:
                    if App.getDocument(MIRROR_DOC).getObject(n):
                        Gui.Selection.addSelection(MIRROR_DOC, n)
            hint(f"“{t['title']}” is yours. Select a part and click ✎ Edit a part… to change it; click "
                 "Mark done when you're finished.")

    def task_finish(self):
        t = self.selected_task()
        if not t:
            return
        summary, ok = QtGui.QInputDialog.getText(self, "Mark done", "What did you do? (one line, optional)")
        if ok:
            self.session.act("POST", f"/tasks/{t['id']}/result", {"status": "done", "summary": summary or "Done"},
                             f"Marked “{t['title']}” done", purpose="finish a task")

    def task_details(self):
        t = self.selected_task()
        if not t:
            hint("Click a task first.")
            return
        lines = [f"<b>{html.escape(t['title'])}</b>", f"Status: {TASK_WORDS.get(t['status'], t['status'])}",
                 f"Who: {html.escape(t.get('owner_name') or t.get('assignee') or 'anyone')}",
                 f"Created by: {html.escape(t.get('created_by_name') or '?')}"]
        if t.get("objects"):
            lines.append(f"Parts: {html.escape(', '.join(t['objects']))}")
        if t.get("detail") and t["detail"] != t["title"]:
            lines.append("<br>" + html.escape(t["detail"]).replace("\n", "<br>"))
        if t.get("result"):
            lines.append(f"<br><b>Result</b> ({html.escape(t['result'].get('by') or '')}): "
                         f"{html.escape(t['result'].get('summary') or '')}")
        box = QtGui.QMessageBox(QtGui.QMessageBox.Information, "Task", "<br>".join(lines), parent=Gui.getMainWindow())
        box.setTextFormat(QtCore.Qt.RichText)
        (getattr(box, "exec", None) or box.exec_)()

    def set_param(self):
        if not self.session.ensure_connected("change a shared dimension"):
            return
        text, ok = QtGui.QInputDialog.getText(self, "Change a shared size",
                                              "name = value   (e.g. hole_pitch = 40 mm)")
        if not ok or "=" not in text:
            return
        alias, value = (x.strip() for x in text.split("=", 1))
        if self.session.act("POST", "/locks", {"names": ["Params"]}) is None:
            return
        self.session.act("POST", "/ops", {"op": "set_param", "args": {"alias": alias, "value": value}},
                         lambda r: f"Params.{alias} = {value}; recomputed {r['recomputed']}")
        self.session.act("POST", "/locks/release", {"names": ["Params"]})


CLIENT_WORDS = {"claude": "Claude Code helper", "codex": "Codex helper", "droid": "Droid helper"}
QUICK_COMMANDS = [
    ("Clashes", "Check the model for clashes and fix any in parts you own; ask the owner about the rest."),
    ("Strength", "Run a strength check (FEA) on {parts}. Ask me for the material and loads first."),
    ("Status?", "Tell me in two lines what you're working on and what's next."),
    ("Pause", "Stop after your current step and wait for my next instruction."),
    ("Let go", "Release {parts} so I can edit them by hand."),
]
# Big enough to read at a glance next to the 3D view: larger text, taller buttons and rows.
PANEL_MIN_WIDTH = 480
PANEL_WIDTH = 580
PANEL_STYLE = """
#cadswarmPanel { font-size: 11pt; }
#cadswarmPanel QLabel { font-size: 11pt; }
#cadswarmPanel QPushButton, #cadswarmPanel QToolButton { font-size: 11pt; min-height: 30px; padding: 4px 12px; }
#cadswarmPanel QTabBar::tab { font-size: 11pt; min-height: 30px; padding: 4px 14px; }
#cadswarmPanel QTreeWidget, #cadswarmPanel QTextEdit, #cadswarmPanel QPlainTextEdit, #cadswarmPanel QLineEdit,
#cadswarmPanel QComboBox { font-size: 11pt; }
#cadswarmPanel QTreeWidget::item { padding: 4px 2px; }
#cadswarmPanel QComboBox, #cadswarmPanel QLineEdit { min-height: 28px; }
#cadswarmPanel QGroupBox { font-size: 11pt; font-weight: bold; padding-top: 14px; }
"""
TEAM_REQUEST = "__team_request__"


def takes_instructions(p):
    """AI helpers and the reviewer take instructions; people and the team server itself never do."""
    return p.get("kind") not in ("human", "host")
BRIEF_WORDS = {"new": "⏳ waiting for the team server", "planning": "🧠 planning the parts",
               "building": "🔨 building", "reviewing": "🔍 checking the result", "ready": "✓ done",
               "failed": "✗ stopped (see Activity)"}
COMMAND_WORDS = {"sent": "⏳ not read yet", "seen": "👀 read — working on it", "done": "✓ done"}
TASK_ORDER = {"claimed": 0, "open": 1, "awaiting_takeover": 2, "blocked": 3, "done": 4}
TASK_KINDS = [
    ("Build a new part", "Describe the part: what it is, rough size, what it attaches to.\n"
                         "e.g. A lid for the enclosure, 3 mm walls, that sits on the four posts"),
    ("Change an existing part", "Say which part and what to change.\n"
                                "e.g. Make the base plate 10 mm longer and keep the holes centred"),
    ("Check strength (FEA)", "Say which part, what it's made of and what load it carries.\n"
                             "e.g. Will the shelf bracket hold 10 kg? It's printed in PLA"),
    ("Find and fix clashes", "e.g. Check every part for overlaps and fix the ones in your parts"),
    ("Something else", "Describe what needs doing."),
]


def start_instruction(t):
    text = f"Please start task {t['id']} now: “{t['title']}”."
    if t.get("detail") and t["detail"] != t["title"]:
        text += f" Details: {t['detail'][:1500]}"
    if t.get("objects"):
        text += f" Parts: {', '.join(t['objects'])}."
    return text + " Claim it with claim_task, tell me in one line how you'll do it, and report_result when done."


class NewTaskDialog(QtGui.QDialog):
    """A task in one step: what kind, what exactly, who does it, and whether they start right away."""

    def __init__(self, session):
        super().__init__(Gui.getMainWindow())
        self.session = session
        self.setWindowTitle("CadSwarm — new task")
        self.resize(520, 480)
        lay = QtGui.QVBoxLayout(self)
        form = QtGui.QFormLayout()
        self.kind = QtGui.QComboBox()
        for label, _ in TASK_KINDS:
            self.kind.addItem(label)
        self.kind.currentIndexChanged.connect(self.kind_changed)
        form.addRow("What kind of task?", self.kind)
        self.what = QtGui.QPlainTextEdit()
        self.what.setMinimumHeight(110)
        form.addRow("What needs doing?", self.what)
        self.parts = QtGui.QListWidget()
        self.parts.setMaximumHeight(90)
        selected = set(session.selected_names())
        for o in session.model.get("objects", []):
            if not o.get("bbox"):
                continue
            it = QtGui.QListWidgetItem(f"{o.get('label') or o['name']}"
                                       + (f"  ({o['locked_by']})" if o.get("locked_by") else ""))
            it.setData(QtCore.Qt.UserRole, o["name"])
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if o["name"] in selected else QtCore.Qt.Unchecked)
            self.parts.addItem(it)
        if self.parts.count():
            form.addRow("Parts involved (optional)", self.parts)
        self.who = QtGui.QComboBox()
        me = session.ident["name"] if session.ident else None
        helpers = [p for p in session.model.get("participants", []) if takes_instructions(p)]
        ready = sorted(helpers, key=lambda p: {"ready": 0, "working": 1, "away": 2}[helper_state(p)[0]])
        for p in ready:
            self.who.addItem(f"🤖 {p['name']} — {helper_state(p)[1]}", ("helper", p["name"]))
        if len(helpers) > 1:
            self.who.addItem("🤖 Whichever helper is free first", ("any", None))
        self.who.addItem("🧑 Me — I'll do it by hand", ("me", me))
        for p in session.model.get("participants", []):
            if p.get("kind") == "human" and p["name"] != me:
                self.who.addItem(f"🧑 {p['name']}", ("person", p["name"]))
        self.who.currentIndexChanged.connect(self.who_changed)
        form.addRow("Who should do it?", self.who)
        self.start = QtGui.QCheckBox("Start right away (sends the helper an instruction to begin)")
        self.start.setChecked(True)
        form.addRow("", self.start)
        lay.addLayout(form)
        self.note = QtGui.QLabel("")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        row = QtGui.QHBoxLayout()
        row.addStretch(1)
        cancel = QtGui.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok = QtGui.QPushButton("Create task")
        self.ok.setDefault(True)
        self.ok.setStyleSheet("QPushButton { font-weight: bold; padding: 5px 14px; }")
        self.ok.clicked.connect(self.create)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        lay.addLayout(row)
        self.start.toggled.connect(lambda _on: self.who_changed(0))
        self.kind_changed(0)
        self.who_changed(0)

    def kind_changed(self, i):
        self.what.setPlaceholderText(TASK_KINDS[i][1])

    def who_changed(self, _i):
        kind, name = self.who.currentData() or ("me", None)
        helper = kind in ("helper", "any")
        self.start.setVisible(helper)
        self.ok.setText("Create and start" if helper and self.start.isChecked() else "Create task")
        p = self.session.participant(name) if name else None
        if kind == "helper" and p and helper_state(p)[0] == "away":
            self.note.setText(f"<small style='color:#9a6700'>{name} is away right now; it will see the task when "
                              "its window is open again.</small>")
        elif kind == "me":
            self.note.setText("<small>The task is marked as yours. Edit the parts with ✎ Edit a part…, then click "
                              "Mark done.</small>")
        else:
            self.note.setText("")

    def create(self):
        text = self.what.toPlainText().strip()
        if not text:
            hint("Describe what needs doing first.")
            return
        kind, name = self.who.currentData() or ("me", None)
        title = text.splitlines()[0][:90]
        if TASK_KINDS[self.kind.currentIndex()][0] != "Something else" and len(title) < 60 \
                and not title.lower().startswith(("check", "build", "make", "change", "find", "will")):
            title = f"{TASK_KINDS[self.kind.currentIndex()][0]}: {title}"
        objects = [self.parts.item(i).data(QtCore.Qt.UserRole) for i in range(self.parts.count())
                   if self.parts.item(i).checkState() == QtCore.Qt.Checked]
        t = self.session.act("POST", "/tasks", {"title": title, "detail": text, "objects": objects,
                                               "assignee": name if kind in ("helper", "person") else None},
                             purpose="create a task")
        if t is None:
            return
        if kind == "me":
            self.session.act("POST", f"/tasks/{t['id']}/claim", {}, purpose="take the task")
        elif kind in ("helper", "any") and self.start.isChecked():
            self.session.act("POST", "/messages", {"to": name or "all", "kind": "command", "task_id": t["id"],
                                                   "body": start_instruction(t)}, purpose="start the task")
            if self.session.panel:
                self.session.panel.tabs.setCurrentIndex(0)
        say(f"Created task “{title}”")
        self.accept()


def helper_state(p):
    """('working' | 'ready' | 'away' | 'here', words) for one participant."""
    if p.get("status") != "active":
        return "away", "away — may be stuck" if p.get("status") == "stale" else STATUS_WORDS.get(p["status"], p["status"])
    if p.get("kind") == "human":
        return "here", "here"
    task = p.get("task")
    doing = (p.get("doing") or "").strip()
    recent = p.get("idle_s") is not None and p["idle_s"] < 60
    if task:
        return "working", f"Working on “{task['title'][:48]}”"
    if recent and doing and doing not in IDLE_WORDS:
        return "working", f"Working — {doing[:48]}"
    return "ready", "Ready for instructions"


IDLE_WORDS = {"idle", "starting", "watching in FreeCAD", "listening for instructions"}


class EditPartDialog(QtGui.QDialog):
    """Edit one part by hand. While it's open the part is held by you, so no helper can change it under you.
    Type new sizes and positions, or move the part in the 3D view (Edit ▸ Placement, Transform): the dialog
    picks those moves up. Save sends the change to the team; Done gives the part back."""

    def __init__(self, session, name):
        super().__init__(Gui.getMainWindow())
        self.session, self.name = session, name
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Tool)
        self.info = session.client.get(f"/objects/{name}")
        self.was_mine = self.info_owner() == session.ident["name"]
        if not self.was_mine:
            session.client.post("/locks", {"names": [name]})
        session.editing[name] = self
        self.setWindowTitle(f"Editing {self.info.get('label') or name}")
        lay = QtGui.QVBoxLayout(self)
        head = QtGui.QLabel(f"<b>Editing {html.escape(self.info.get('label') or name)}</b><br><small>Only you can "
                            "change it while this is open. Change sizes or position, then click <b>Save to team</b>."
                            "</small>")
        head.setWordWrap(True)
        lay.addWidget(head)
        self.fields = {}
        dims = EDITABLE_DIMENSIONS.get(self.info.get("type"), [])
        bound = self.info.get("expressions") or {}
        if dims:
            box = QtGui.QGroupBox("Size")
            form = QtGui.QFormLayout(box)
            for prop, unit in dims:
                spin = self.spinbox(unit)
                spin.setValue(number(self.info["properties"].get(prop)))
                if prop in bound:
                    spin.setEnabled(False)
                    spin.setToolTip(f"Set by the shared size {bound[prop]} — change it with Change a shared size…")
                    form.addRow(f"{prop} (uses {bound[prop]})", spin)
                else:
                    form.addRow(prop, spin)
                self.fields[prop] = spin
            lay.addWidget(box)
        else:
            note = QtGui.QLabel("<small>This part is built from other parts (or by a script), so you can move it "
                                "but its size comes from what it's made of.</small>")
            note.setWordWrap(True)
            lay.addWidget(note)
        box = QtGui.QGroupBox("Position")
        form = QtGui.QFormLayout(box)
        self.pos = {}
        for axis in "XYZ":
            self.pos[axis] = self.spinbox("mm")
            form.addRow(f"{axis}", self.pos[axis])
        self.rot = {}
        for key, label in (("yaw", "Turn around Z"), ("pitch", "Tilt around Y"), ("roll", "Roll around X")):
            self.rot[key] = self.spinbox("°", -360, 360)
            form.addRow(label, self.rot[key])
        lay.addWidget(box)
        tip = QtGui.QLabel("<small>You can also drag it: select it and use Edit ▸ Placement… or Edit ▸ Transform. "
                           "Moves you make in the 3D view show up here.</small>")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.state = QtGui.QLabel("")
        lay.addWidget(self.state)
        row = QtGui.QHBoxLayout()
        self.revert_b = QtGui.QPushButton("Undo my changes")
        self.revert_b.clicked.connect(self.revert)
        self.save_b = QtGui.QPushButton("Save to team")
        self.save_b.setStyleSheet("QPushButton { font-weight: bold; padding: 5px 12px; }")
        self.save_b.clicked.connect(self.save)
        done = QtGui.QPushButton("Done")
        done.clicked.connect(self.close)
        row.addWidget(self.revert_b)
        row.addStretch(1)
        row.addWidget(self.save_b)
        row.addWidget(done)
        lay.addLayout(row)
        self.load_placement(self.hub_placement())
        for w in list(self.fields.values()) + list(self.pos.values()) + list(self.rot.values()):
            w.valueChanged.connect(self.typed)
        self.watch = QtCore.QTimer(self)
        self.watch.timeout.connect(self.watch_mirror)
        self.watch.start(300)
        self.update_state()

    @staticmethod
    def spinbox(unit, low=-100000, high=100000):
        s = QtGui.QDoubleSpinBox()
        s.setRange(low, high)
        s.setDecimals(2)
        s.setSuffix(f" {unit}")
        s.setKeyboardTracking(False)
        return s

    def info_owner(self):
        o = next((x for x in self.session.model.get("objects", []) if x["name"] == self.name), {})
        return o.get("locked_by")

    def hub_placement(self):
        pl = self.info.get("placement") or {"base": [0, 0, 0], "rotation": {"axis": [0, 0, 1], "angle": 0}}
        r = pl["rotation"]
        return App.Placement(App.Vector(*pl["base"]), App.Rotation(App.Vector(*r["axis"]), r["angle"]))

    def load_placement(self, pl):
        for w in list(self.pos.values()) + list(self.rot.values()):
            w.blockSignals(True)
        for axis, v in zip("XYZ", (pl.Base.x, pl.Base.y, pl.Base.z)):
            self.pos[axis].setValue(v)
        y, p, r = pl.Rotation.getYawPitchRoll()
        self.rot["yaw"].setValue(y)
        self.rot["pitch"].setValue(p)
        self.rot["roll"].setValue(r)
        for w in list(self.pos.values()) + list(self.rot.values()):
            w.blockSignals(False)

    def typed_placement(self):
        return App.Placement(App.Vector(*(self.pos[a].value() for a in "XYZ")),
                             App.Rotation(self.rot["yaw"].value(), self.rot["pitch"].value(), self.rot["roll"].value()))

    def mirror_obj(self):
        doc = App.listDocuments().get(MIRROR_DOC)
        return doc.getObject(self.name) if doc else None

    def typed(self, *_):
        # preview a typed move in the live view: same change applied to the mirror copy
        obj, base = self.mirror_obj(), self.session.synced_pl.get(self.name)
        if obj is not None and base is not None:
            delta = self.typed_placement().multiply(self.hub_placement().inverse())
            obj.Placement = delta.multiply(base)
            self._last_seen = obj.Placement
        self.update_state()

    def watch_mirror(self):
        # a move made with FreeCAD's own tools (Placement, Transform, dragging) lands here
        obj, base = self.mirror_obj(), self.session.synced_pl.get(self.name)
        if obj is None or base is None:
            return
        if getattr(self, "_last_seen", None) is not None and same_placement(obj.Placement, self._last_seen):
            return
        self._last_seen = obj.Placement
        delta = obj.Placement.multiply(base.inverse())
        self.load_placement(delta.multiply(self.hub_placement()))
        self.update_state()

    def changes(self):
        props = {}
        for prop, spin in self.fields.items():
            if spin.isEnabled() and abs(spin.value() - number(self.info["properties"].get(prop))) > 1e-6:
                props[prop] = spin.value()
        moved = not same_placement(self.typed_placement(), self.hub_placement())
        return props, moved

    def update_state(self):
        props, moved = self.changes()
        bits = ([f"size ({', '.join(props)})"] if props else []) + (["position"] if moved else [])
        self.state.setText(f"<small style='color:#d9822b'>Not saved yet: {', '.join(bits)}</small>" if bits else
                           "<small style='color:#1a7f37'>Everyone sees the same as you.</small>")
        self.save_b.setEnabled(bool(bits))
        self.revert_b.setEnabled(bool(bits))

    def save(self):
        props, moved = self.changes()
        ok = True
        if props:
            ok = self.session.act("POST", "/ops", {"op": "set_props", "args": {"name": self.name, "props": props}},
                                  purpose="save your change") is not None
        if ok and moved:
            pl = self.typed_placement()
            y, p, r = pl.Rotation.getYawPitchRoll()
            ok = self.session.act("POST", "/ops", {"op": "set_placement", "args": {"name": self.name, "placement": {
                "base": [pl.Base.x, pl.Base.y, pl.Base.z], "rotation": {"ypr": [y, p, r]}}}},
                                  purpose="save your change") is not None
        if ok:
            self.info = self.session.client.get(f"/objects/{self.name}")
            self._last_seen = None
            say(f"Saved your change to {self.name}; the team sees it now")
        self.update_state()

    def revert(self):
        obj, base = self.mirror_obj(), self.session.synced_pl.get(self.name)
        if obj is not None and base is not None:
            obj.Placement = base
            self._last_seen = obj.Placement
        for prop, spin in self.fields.items():
            spin.blockSignals(True)
            spin.setValue(number(self.info["properties"].get(prop)))
            spin.blockSignals(False)
        self.load_placement(self.hub_placement())
        self.update_state()

    def closeEvent(self, ev):
        props, moved = self.changes()
        if props or moved:
            answer = QtGui.QMessageBox.question(
                self, "CadSwarm", "Save your change to the team before you stop editing?",
                QtGui.QMessageBox.Save | QtGui.QMessageBox.Discard | QtGui.QMessageBox.Cancel)
            if answer == QtGui.QMessageBox.Cancel:
                ev.ignore()
                return
            if answer == QtGui.QMessageBox.Save:
                self.save()
            else:
                self.revert()
        self.watch.stop()
        self.session.editing.pop(self.name, None)
        if not self.was_mine and self.session.client:
            try:
                self.session.client.post("/locks/release", {"names": [self.name]})
            except HubRequestError:
                pass
        self.session.refresh_meta()
        super().closeEvent(ev)


EDITABLE_DIMENSIONS = {
    "Part::Box": [("Length", "mm"), ("Width", "mm"), ("Height", "mm")],
    "Part::Cylinder": [("Radius", "mm"), ("Height", "mm"), ("Angle", "°")],
    "Part::Sphere": [("Radius", "mm")],
    "Part::Cone": [("Radius1", "mm"), ("Radius2", "mm"), ("Height", "mm")],
    "Part::Torus": [("Radius1", "mm"), ("Radius2", "mm")],
}


def number(value):
    """A property value from the hub ('120.0 mm', 120, 'Quantity(...)') as a float."""
    if isinstance(value, (int, float)):
        return float(value)
    import re
    m = re.search(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", str(value or ""))
    return float(m.group()) if m else 0.0


def same_placement(a, b, tol=1e-6):
    ma, mb = a.toMatrix(), b.toMatrix()
    return all(abs(x - y) <= tol for x, y in zip(ma.A, mb.A))


def edit_part():
    if not SESSION.ensure_connected("edit a part"):
        return
    names_ = SESSION.selected_names()
    if len(names_) != 1:
        parts = [o for o in SESSION.model.get("objects", []) if o.get("bbox")]
        if not parts:
            hint("There are no parts in the shared model yet.")
            return
        labels = [f"{o.get('label') or o['name']}" + (f"  (held by {o['locked_by']})" if o.get("locked_by") else "")
                  for o in parts]
        choice, ok = QtGui.QInputDialog.getItem(Gui.getMainWindow(), "Edit a part",
                                                "Which part? (Tip: click it in the 3D view first.)", labels, 0, False)
        if not ok:
            return
        names_ = [parts[labels.index(choice)]["name"]]
    name = names_[0]
    if name in SESSION.editing:
        SESSION.editing[name].raise_()
        return
    obj = next((o for o in SESSION.model.get("objects", []) if o["name"] == name), {})
    holder, me = obj.get("locked_by"), SESSION.ident["name"]
    if holder and holder != me:
        p = SESSION.participant(holder) or {}
        box = QtGui.QMessageBox(QtGui.QMessageBox.Question, "CadSwarm",
                                f"{obj.get('label') or name} is held by {holder}, so only they can change it right now.",
                                parent=Gui.getMainWindow())
        ask = box.addButton(f"Ask {holder} to let go", QtGui.QMessageBox.AcceptRole)
        force = box.addButton("Free it anyway", QtGui.QMessageBox.DestructiveRole)
        box.addButton(QtGui.QMessageBox.Cancel)
        (getattr(box, "exec", None) or box.exec_)()
        if box.clickedButton() == ask:
            kind = "chat" if p.get("kind") == "human" else "command"
            SESSION.act("POST", "/messages", {"to": holder, "kind": kind,
                                              "body": f"Please release {name} so I can edit it by hand."},
                        f"Asked {holder} to let go of {name}", purpose="ask for a part")
            return
        if box.clickedButton() != force or not confirm(
                f"Free {name} from {holder}? They're told, and their next change to it will be refused."):
            return
        if SESSION.act("POST", "/locks/release", {"names": [name], "force": True}, purpose="free the part") is None:
            return
    try:
        dlg = EditPartDialog(SESSION, name)
    except HubRequestError as e:
        hint(friendly_error(e))
        return
    dlg.show()


STATUS_WORDS = {"active": "here", "stale": "away — may be stuck", "left": "left"}
TASK_WORDS = {"open": "not started", "claimed": "in progress", "awaiting_takeover": "needs someone",
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
        mw = Gui.getMainWindow()
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, SESSION.panel)
        try:  # open wide enough to read; people can still drag it narrower down to the minimum
            mw.resizeDocks([SESSION.panel], [PANEL_WIDTH], QtCore.Qt.Horizontal)
        except (AttributeError, TypeError):
            pass
    SESSION.panel.show()
    tidy_task_view()
    if SESSION.connected:
        SESSION.refresh_meta()


def tidy_task_view():
    """FreeCAD 1.1 puts its Tasks panel in an overlay on the right edge of the 3D view, right beside our
    panel, where it pops up and vanishes as the mouse crosses it. Dock it as a tab next to the Model tree
    instead; FreeCAD still brings it forward by itself whenever a real task (Placement, Transform) opens."""
    mw = Gui.getMainWindow()
    docks = {d.windowTitle(): d for d in mw.findChildren(QtGui.QDockWidget)}
    tasks, model = docks.get("Tasks"), docks.get("Model") or docks.get("Combo View")
    if tasks is None or model is None or tasks is model:
        return
    if mw.dockWidgetArea(tasks) == QtCore.Qt.LeftDockWidgetArea and tasks.parent() is mw:
        return  # already docked on the left
    try:
        mw.addDockWidget(QtCore.Qt.LeftDockWidgetArea, tasks)
        mw.tabifyDockWidget(model, tasks)
        model.raise_()
    except Exception as e:
        warn(f"couldn't move the Tasks panel: {e}")


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
    Gui.addCommand("CadSwarm_NewTask", Cmd("New task…", "Give the team (or yourself) something to do", lambda: (
        show_panel(), SESSION.panel.new_task())))
    Gui.addCommand("CadSwarm_EditPart", Cmd("Edit a part…", "Change a part yourself and share the change", edit_part))
    Gui.addCommand("CadSwarm_TellHelper", Cmd("Tell a helper…", "Give an AI helper an instruction", lambda: (
        show_panel(), SESSION.panel.tabs.setCurrentIndex(1), SESSION.panel.cmd_text.setFocus())))
    Gui.addCommand("CadSwarm_Disconnect", Cmd("Leave team", "Leave the session (releases your locks)",
                                              lambda: SESSION.disconnect() if SESSION.connected else None, False))
