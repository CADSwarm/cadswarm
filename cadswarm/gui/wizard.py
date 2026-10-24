"""Step-by-step setup inside FreeCAD, for people who have never used a terminal or a CAD tool.

    Welcome ──► Start a new team ─► name it ─► (download helper software, start the team
       │                                        server, join) ─► share invite code ─┐
       └──────► Join with an invite code ─► paste code + name ─────────────────────┤
                                                                                    ▼
        Connect your AI helper (Claude Code / Codex / just watch) ─► (wire it up) ─►
        Open it, paste the first message ─► "✓ your helper joined" ─► Finish

Vocabulary is deliberately plain: "team", "invite code", "AI helper" — never hub,
token or MCP. Slow work runs off the GUI thread behind a checklist; every failure
says what to do and offers Try again.
"""
import getpass
import os
import queue
import re
import secrets
import threading
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

from .. import hosting
from ..agent_setup import CLIENTS, FIRST_PROMPT, find_agent_cli, open_agent_terminal, trust_in_codex, \
    write_agent_config
from ..client import HubRequestError
from ..invite import InviteError, make_invite, read_invite
from ..paths import freecad_python, project_layout, projects_dir, resolve_project_folder
from . import panel

STYLE = """
QDialog { font-size: 11pt; }
QLabel#title { font-size: 17pt; font-weight: 600; }
QLabel#lead { font-size: 11.5pt; }
QLabel#hint { color: #8b949e; font-size: 10pt; }
QLabel#error { color: #e5484d; font-weight: 600; }
QLabel#ok { color: #30a46c; font-weight: 600; }
QFrame#card { border: 1px solid palette(mid); border-radius: 8px; padding: 8px; }
QPushButton#primary { font-weight: 600; padding: 7px 18px; }
QPlainTextEdit#code { font-family: Consolas, monospace; font-size: 10.5pt; }
"""


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "me"


def default_name():
    try:
        user = getpass.getuser()
    except Exception:
        return ""
    return re.split(r"[ ._-]", user)[0].capitalize()


def open_folder(path):
    if os.name == "nt":
        os.startfile(path)
    else:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))


def label(text, kind=None, wrap=True):
    lab = QtGui.QLabel(text)
    lab.setWordWrap(wrap)
    lab.setTextFormat(QtCore.Qt.RichText)
    lab.setOpenExternalLinks(True)
    if kind:
        lab.setObjectName(kind)
    return lab


def copy_to_clipboard(text, button=None, done="Copied ✓"):
    QtGui.QApplication.clipboard().setText(text)
    if button is not None:
        original = button.text()
        button.setText(done)
        QtCore.QTimer.singleShot(2000, lambda: button.setText(original))


class Checklist(QtGui.QWidget):
    """Runs steps in order, showing ○ → ⏳ → ✓/✗ for each, with a plain error and Try again on failure.

    steps: [(text, hint_or_None, fn, on_gui_thread)] where fn(log) may raise; log(str) adds detail lines.
    """

    ICONS = {"todo": "○", "run": "⏳", "done": "✓", "fail": "✗"}

    def __init__(self, on_done):
        super().__init__()
        self.on_done = on_done
        self.steps, self.rows, self.i = [], [], 0
        self.q = queue.Queue()
        self.lay = QtGui.QVBoxLayout(self)
        self.list = QtGui.QVBoxLayout()
        self.lay.addLayout(self.list)
        self.error = label("", "error")
        self.error.hide()
        self.lay.addWidget(self.error)
        row = QtGui.QHBoxLayout()
        self.retry = QtGui.QPushButton("Try again")
        self.retry.hide()
        self.retry.clicked.connect(self.resume)
        self.toggle = QtGui.QPushButton("Show details")
        self.toggle.setFlat(True)
        self.toggle.clicked.connect(lambda: (self.details.setVisible(not self.details.isVisible()),
                                             self.toggle.setText("Hide details" if self.details.isVisible()
                                                                 else "Show details")))
        row.addWidget(self.retry)
        row.addStretch(1)
        row.addWidget(self.toggle)
        self.lay.addLayout(row)
        self.details = QtGui.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(140)
        self.details.hide()
        self.lay.addWidget(self.details)
        self.lay.addStretch(1)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.pump)

    def set_steps(self, steps):
        while self.list.count():
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.steps, self.rows, self.i = steps, [], 0
        for text, hint, _fn, _gui in steps:
            w = QtGui.QWidget()
            v = QtGui.QVBoxLayout(w)
            v.setContentsMargins(0, 2, 0, 2)
            main = label(f"{self.ICONS['todo']}  {text}")
            v.addWidget(main)
            h = label(hint, "hint") if hint else None
            if h:
                h.hide()
                v.addWidget(h)
            self.list.addWidget(w)
            self.rows.append((main, h, text))
        self.error.hide()
        self.retry.hide()
        self.details.clear()

    def mark(self, i, state):
        main, hint, text = self.rows[i]
        main.setText(f"{self.ICONS[state]}  {'<b>' + text + '</b>' if state == 'run' else text}")
        if hint:
            hint.setVisible(state == "run")

    def start(self):
        self.i = 0
        self.timer.start(100)
        self.run_current()

    def resume(self):
        self.error.hide()
        self.retry.hide()
        self.timer.start(100)
        self.run_current()

    def log(self, line):
        self.q.put(("log", line))

    def run_current(self):
        if self.i >= len(self.steps):
            self.timer.stop()
            QtCore.QTimer.singleShot(400, self.on_done)
            return
        self.mark(self.i, "run")
        _text, _hint, fn, on_gui = self.steps[self.i]

        def work():
            try:
                fn(self.log)
                self.q.put(("done", None))
            except HubRequestError as e:
                self.q.put(("fail", panel.friendly_error(e)))
            except RuntimeError as e:  # our own plain-language failures (download, server start)
                self.q.put(("fail", str(e)))
            except Exception:  # a bug: never show raw Python to a hobbyist
                self.q.put(("fail", "Something unexpected went wrong. Click “Try again”. If it keeps "
                                    "happening, click “Show details” and send what it says to your team."
                                    "\n\nDetails:\n" + traceback.format_exc()))

        if on_gui:
            QtCore.QTimer.singleShot(50, work)
        else:
            threading.Thread(target=work, daemon=True).start()

    def pump(self):
        while True:
            try:
                kind, data = self.q.get_nowait()
            except queue.Empty:
                return
            if kind == "log":
                self.details.appendPlainText(data)
            elif kind == "done":
                self.mark(self.i, "done")
                self.i += 1
                self.run_current()
            elif kind == "fail":
                self.mark(self.i, "fail")
                short, _, more = data.partition("\n\nDetails:\n")
                self.error.setText(short)
                self.error.show()
                if more:
                    self.details.appendPlainText(more)
                self.retry.show()
                self.timer.stop()


class SetupWizard(QtGui.QDialog):
    def __init__(self, reason=None, start=None):
        super().__init__(Gui.getMainWindow())
        self.setWindowTitle("CadSwarm — get started")
        self.setMinimumSize(640, 520)
        self.setStyleSheet(STYLE)
        self.reason = reason
        self.p = panel.prefs()
        self.python = freecad_python()
        self.mode = None          # "host" | "join"
        self.project = self.p.GetString("ProjectName")
        self.name = self.p.GetString("Name") or default_name()
        self.hub_for_agent = None  # the address this computer's AI helper should use
        self.token = None
        self.client = None
        self.folder_user_set = False
        self.agent_name = None

        outer = QtGui.QVBoxLayout(self)
        self.stack = QtGui.QStackedWidget()
        outer.addWidget(self.stack, 1)
        bar = QtGui.QHBoxLayout()
        self.back = QtGui.QPushButton("‹ Back")
        self.back.clicked.connect(self.go_back)
        self.secondary = QtGui.QPushButton()
        self.secondary.setFlat(True)
        self.primary = QtGui.QPushButton("Next")
        self.primary.setObjectName("primary")
        self.primary.setDefault(True)
        bar.addWidget(self.back)
        bar.addStretch(1)
        bar.addWidget(self.secondary)
        bar.addWidget(self.primary)
        outer.addLayout(bar)
        self.pages, self.history, self.current = {}, [], None
        self.primary_action = self.secondary_action = None
        self.primary.clicked.connect(lambda: self.primary_action and self.primary_action())
        self.secondary.clicked.connect(lambda: self.secondary_action and self.secondary_action())
        self.poll = QtCore.QTimer(self)
        self.poll.timeout.connect(self.check_helper_joined)

        if start == "agent" and panel.SESSION.connected:
            self.mode = "host" if self.p.GetString("HostDataDir") else "join"
            self.hub_for_agent = self.p.GetString("HubUrl")
            self.token = self.p.GetString("WorkspaceToken")
            self.show_page("agent")
        elif start == "reconnect":
            self.show_page("reconnect")
        else:
            self.show_page("welcome")

    # ----------------------------------------------------------- navigation

    def show_page(self, name, remember=True):
        if remember and self.stack.currentWidget() is not None and self.history[-1:] != [self.current]:
            self.history.append(self.current)
        self.current = name
        if name not in self.pages or name in ("agent", "launch", "invite"):  # rebuilt with fresh data
            old = self.pages.pop(name, None)
            if old is not None:
                self.stack.removeWidget(old)
                old.deleteLater()
            page = getattr(self, f"page_{name}")()
            self.pages[name] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(self.pages[name])
        self.back.setVisible(bool(self.history) and name not in ("host_work", "agent_work", "reconnect", "launch"))
        enter = getattr(self, f"enter_{name}", None)
        if enter:
            enter()

    def go_back(self):
        if self.history:
            self.show_page(self.history.pop(), remember=False)

    def buttons(self, primary=None, action=None, secondary=None, secondary_action=None):
        self.primary.setVisible(primary is not None)
        if primary:
            self.primary.setText(primary)
            self.primary.setEnabled(True)
        self.primary_action = action
        self.secondary.setVisible(secondary is not None)
        if secondary:
            self.secondary.setText(secondary)
        self.secondary_action = secondary_action

    def page(self, title, lead=None):
        w = QtGui.QWidget()
        lay = QtGui.QVBoxLayout(w)
        lay.setSpacing(10)
        lay.addWidget(label(title, "title"))
        if lead:
            lay.addWidget(label(lead, "lead"))
        return w, lay

    # --------------------------------------------------------------- welcome

    def page_welcome(self):
        w, lay = self.page("Build 3D designs with your team's AI helpers",
                           "Each teammate's AI helper (like Claude Code) builds part of one shared 3D model. "
                           "You'll watch the parts appear here and can chat with the helpers to steer them. "
                           "No CAD experience needed.")
        if self.reason:
            lay.addWidget(label(f"<b>{self.reason}</b>"))
        lay.addSpacing(8)
        if self.project and (self.p.GetString("HostDataDir") or self.p.GetString("HubUrl")):
            again = QtGui.QCommandLinkButton(f"Reconnect to “{self.project}”",
                                             "Pick up where you left off with your team.")
            again.clicked.connect(lambda: self.show_page("reconnect"))
            lay.addWidget(again)
        host = QtGui.QCommandLinkButton("Start a new team project",
                                        "Choose this if you're the first one. You'll get an invite code to send "
                                        "your teammates.")
        host.clicked.connect(lambda: self.choose("host"))
        join = QtGui.QCommandLinkButton("Join a teammate's project",
                                        "Choose this if someone sent you an invite code.")
        join.clicked.connect(lambda: self.choose("join"))
        watch = QtGui.QCommandLinkButton("Just exploring? Read how it works",
                                         "Opens the CadSwarm guide in your browser.")
        watch.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl("https://github.com/SachchitBalamurugan/cadswarm#readme")))
        for b in (host, join, watch):
            lay.addWidget(b)
        lay.addStretch(1)
        return w

    def enter_welcome(self):
        self.buttons(secondary="Close", secondary_action=self.reject)

    def choose(self, mode):
        self.mode = mode
        self.show_page("host_name" if mode == "host" else "join")

    # ------------------------------------------------------------------ host

    def page_host_name(self):
        w, lay = self.page("What are you building?",
                           "Give your project a name your teammates will recognize. You can describe the "
                           "details to your AI helper later.")
        form = QtGui.QFormLayout()
        self.project_edit = QtGui.QLineEdit(self.project)
        self.project_edit.setPlaceholderText("e.g. Phone stand, Plant pot, Robot arm")
        self.host_name_edit = QtGui.QLineEdit(self.name)
        self.host_name_edit.setPlaceholderText("e.g. Sam")
        form.addRow("Project name", self.project_edit)
        form.addRow("Your name", self.host_name_edit)
        lay.addLayout(form)
        lay.addWidget(label("Your name is shown to teammates and their AI helpers.", "hint"))
        lay.addSpacing(6)
        lay.addWidget(label("<b>Where should the project be saved?</b> The model, your team's notes and "
                            "exported 3D files all go in this folder, organised into subfolders as it grows."))
        row = QtGui.QHBoxLayout()
        self.where_label = label("", "hint")
        pick = QtGui.QPushButton("Choose folder…")
        pick.clicked.connect(self.choose_project_folder)
        row.addWidget(self.where_label, 1)
        row.addWidget(pick)
        lay.addLayout(row)
        self.chosen_parent = None  # None = the default Documents\CadSwarm\<project>
        self.project_edit.textChanged.connect(lambda _t: self.update_where())
        self.host_error = label("", "error")
        lay.addWidget(self.host_error)
        lay.addStretch(1)
        return w

    def enter_host_name(self):
        self.buttons("Create my team ›", self.create_team)
        self.update_where()
        self.project_edit.setFocus()

    def planned_folder(self):
        project = self.project_edit.text().strip() or "My project"
        if self.chosen_parent:
            return resolve_project_folder(self.chosen_parent, project)
        return resolve_project_folder(os.path.join(projects_dir(), project), project)

    def update_where(self):
        folder, how = self.planned_folder()
        note = {"existing": "A CadSwarm project is already here — you'll continue it (nothing is overwritten).",
                "new": "A new folder will be created for it.",
                "inside": "That folder already has other things in it, so the project gets its own "
                          "subfolder there. Nothing in it is touched."}[how]
        self.where_label.setText(f"<b>{folder}</b><br>{note}")

    def choose_project_folder(self):
        d = QtGui.QFileDialog.getExistingDirectory(self, "Choose where to save the project",
                                                   self.chosen_parent or projects_dir())
        if d:
            self.chosen_parent = os.path.normpath(d)
            self.update_where()

    def create_team(self):
        project, name = self.project_edit.text().strip(), self.host_name_edit.text().strip()
        if not project or not name:
            self.host_error.setText("Please fill in " + " and ".join(
                n for n, v in (("a project name", project), ("your name", name)) if not v) + ".")
            return
        self.project, self.name = project, name
        self.project_dir, self.project_how = self.planned_folder()
        self.show_page("host_work")

    def page_host_work(self):
        w, lay = self.page("Setting up your team…", "This only happens once. You can leave this window open.")
        self.host_steps = Checklist(on_done=lambda: self.show_page("invite"))
        lay.addWidget(self.host_steps, 1)
        return w

    def enter_host_work(self):
        self.buttons(secondary="Cancel", secondary_action=self.reject)
        self.host_steps.set_steps([
            ("Get the helper software (one time, about 2 minutes)",
             "Downloading the small programs that run your team. Needs an internet connection.",
             self.step_deps, False),
            ("Start your team's server",
             "If Windows asks whether to allow network access, click “Allow” so teammates can connect.",
             self.step_start_hub, False),
            ("Join your team", None, self.step_host_join, True),
        ])
        self.host_steps.start()

    def step_deps(self, log):
        if hosting.deps_ready(self.python):
            log("Helper software already installed.")
            return
        hosting.install_deps(self.python, log)

    def step_start_hub(self, log):
        data_dir = self.project_dir
        token_file = project_layout(data_dir)["token"]
        # Continuing a project keeps its team code, so helpers already connected to it keep working.
        self.token = open(token_file).read().strip() if os.path.exists(token_file) else secrets.token_urlsafe(9)
        port = hosting.free_port()
        url = f"http://127.0.0.1:{port}"
        log(f"Starting the team server on port {port} (model saved in {data_dir})")
        pid = hosting.start_hub(self.python, data_dir, self.token, port)
        self.p.SetString("HostPid", str(pid))  # saved at once, so "Stop hosting" finds it whatever happens next
        try:
            hosting.wait_healthy(url, timeout=120, pid=pid, data_dir=data_dir)
        except Exception:
            hosting.stop_hub(pid)  # else "Try again" would start a second server on the same project folder
            raise
        self.hub_for_agent = url
        self.host_info = {"HostDataDir": data_dir, "HostToken": self.token, "HostPort": str(port),
                          "HostPid": str(pid)}

    def step_host_join(self, log):
        panel.SESSION.connect(self.hub_for_agent, self.token, self.name, timeout=10)
        for k, v in self.host_info.items():
            self.p.SetString(k, v)
        lan = f"http://{hosting.lan_ip()}:{self.host_info['HostPort']}"
        self.p.SetString("InviteCode", make_invite(lan, self.token, self.project))
        self.p.SetString("ProjectName", self.project)

    def page_invite(self):
        code = self.p.GetString("InviteCode")
        w, lay = self.page("Your team is ready! 🎉",
                           "Send this invite code to your teammates — by email, Discord, text, anything. "
                           "They paste it into CadSwarm to join.")
        box = QtGui.QPlainTextEdit(code)
        box.setObjectName("code")
        box.setReadOnly(True)
        box.setMaximumHeight(80)
        lay.addWidget(box)
        copy = QtGui.QPushButton("Copy invite code")
        copy.clicked.connect(lambda: copy_to_clipboard(code, copy))
        lay.addWidget(copy, 0, QtCore.Qt.AlignLeft)
        folder = self.p.GetString("HostDataDir")
        if folder:
            row = QtGui.QHBoxLayout()
            row.addWidget(label(f"Your project is saved in <b>{folder}</b>", "hint"), 1)
            show = QtGui.QPushButton("Open folder")
            show.clicked.connect(lambda: open_folder(folder))
            row.addWidget(show)
            lay.addLayout(row)
        lay.addWidget(label("<b>Good to know</b><br>"
                            "• Teammates need to be on the same Wi-Fi or network as this computer.<br>"
                            "• Keep this computer on while your team works. Your project is saved as you go.<br>"
                            "• You can copy the code again any time from the team panel (“Invite”).", "hint"))
        lay.addStretch(1)
        return w

    def enter_invite(self):
        self.buttons("Next: connect my AI helper ›", lambda: self.show_page("agent"),
                     "Skip — I'll just watch", self.finish)

    # ------------------------------------------------------------------ join

    def page_join(self):
        w, lay = self.page("Join your teammate's project",
                           "Paste the invite code they sent you. It starts with “cadswarm-1:”.")
        self.code_edit = QtGui.QPlainTextEdit()
        self.code_edit.setObjectName("code")
        self.code_edit.setPlaceholderText("cadswarm-1:…")
        self.code_edit.setMaximumHeight(80)
        lay.addWidget(self.code_edit)
        paste = QtGui.QPushButton("Paste from clipboard")
        paste.clicked.connect(lambda: self.code_edit.setPlainText(QtGui.QApplication.clipboard().text()))
        lay.addWidget(paste, 0, QtCore.Qt.AlignLeft)
        form = QtGui.QFormLayout()
        self.join_name_edit = QtGui.QLineEdit(self.name)
        self.join_name_edit.setPlaceholderText("e.g. Sam")
        form.addRow("Your name", self.join_name_edit)
        lay.addLayout(form)
        self.join_error = label("", "error")
        lay.addWidget(self.join_error)
        lay.addStretch(1)
        return w

    def enter_join(self):
        self.buttons("Join team ›", self.join_team)
        clip = QtGui.QApplication.clipboard().text()
        if "cadswarm-1:" in clip and not self.code_edit.toPlainText().strip():
            self.code_edit.setPlainText(clip.strip())  # they just copied it: save them a step

    def join_team(self):
        name = self.join_name_edit.text().strip()
        try:
            inv = read_invite(self.code_edit.toPlainText())
        except InviteError as e:
            self.join_error.setText(str(e))
            return
        if not name:
            self.join_error.setText("Please type your name.")
            return
        self.join_error.setText("<span style='color: palette(text)'>Joining…</span>")
        QtGui.QApplication.processEvents()
        try:
            panel.SESSION.connect(inv["hub"], inv["token"], name, timeout=10)
        except HubRequestError as e:
            if e.code == "name_in_use":
                self.join_error.setText(f"Someone named “{name}” is already in this team. Please use a different name.")
            elif e.code == "unreachable":
                self.join_error.setText("Couldn't reach your teammate's computer. Check that:<br>"
                                        "• you're on the same Wi-Fi or network,<br>"
                                        "• their computer is on and CadSwarm is running there,<br>"
                                        "• the invite code is their latest one.")
            else:
                self.join_error.setText(panel.friendly_error(e, inv["hub"]))
            return
        except Exception as e:  # never leave the dialog stuck on "Joining…"
            App.Console.PrintError(f"[CadSwarm] Join failed: {e!r}\n")
            self.join_error.setText("Something went wrong while joining. Please try again; if it keeps "
                                    "happening, ask your teammate to send a fresh invite code.")
            return
        self.name, self.project, self.token = name, inv["project"] or "Team project", inv["token"]
        self.hub_for_agent = inv["hub"]
        self.p.SetString("InviteCode", self.code_edit.toPlainText().strip())
        self.p.SetString("ProjectName", self.project)
        for k in ("HostDataDir", "HostToken", "HostPort", "HostPid"):
            self.p.SetString(k, "")  # this computer is not the host
        self.show_page("agent")

    # ----------------------------------------------------------------- agent

    def page_agent(self):
        w, lay = self.page("Connect your AI helper",
                           "Pick the AI helper you use. It will build parts of the model for your team.")
        self.client_buttons = {}
        group = QtGui.QButtonGroup(w)
        found_any = None
        for key, info in CLIENTS.items():
            card = QtGui.QFrame()
            card.setObjectName("card")
            row = QtGui.QHBoxLayout(card)
            rb = QtGui.QRadioButton(info["label"])
            rb.setStyleSheet("font-weight: 600;")
            group.addButton(rb)
            row.addWidget(rb)
            path = find_agent_cli(key)
            if path:
                found_any = found_any or key
                row.addWidget(label("✓ Found on this computer", "ok"), 1)
            else:
                row.addWidget(label("Not installed yet", "hint"), 1)
                how = QtGui.QPushButton("How to install")
                how.clicked.connect(lambda _=False, u=info["install_url"]: QtGui.QDesktopServices.openUrl(QtCore.QUrl(u)))
                row.addWidget(how)
            rb.toggled.connect(lambda on, k=key: on and self.pick_client(k))
            self.client_buttons[key] = (rb, bool(path))
            lay.addWidget(card)
        none_card = QtGui.QFrame()
        none_card.setObjectName("card")
        nrow = QtGui.QHBoxLayout(none_card)
        none_rb = QtGui.QRadioButton("No AI helper — I'll just watch and chat")
        group.addButton(none_rb)
        none_rb.toggled.connect(lambda on: on and self.pick_client(None))
        nrow.addWidget(none_rb)
        lay.addWidget(none_card)
        self.client_buttons[None] = (none_rb, True)

        self.missing_note = label("", "hint")
        lay.addWidget(self.missing_note)
        refresh = QtGui.QPushButton("I installed it — check again")
        refresh.clicked.connect(lambda: self.show_page("agent", remember=False))
        lay.addWidget(refresh, 0, QtCore.Qt.AlignLeft)

        self.folder_row = QtGui.QWidget()
        fr = QtGui.QHBoxLayout(self.folder_row)
        fr.setContentsMargins(0, 0, 0, 0)
        self.folder_label = label("", "hint")
        change = QtGui.QPushButton("Change…")
        change.clicked.connect(self.change_folder)
        fr.addWidget(self.folder_label, 1)
        fr.addWidget(change)
        lay.addWidget(self.folder_row)
        self.trust_box = QtGui.QCheckBox("Let Codex use CadSwarm in this folder (recommended — Codex ignores "
                                         "folders it hasn't been told to trust)")
        self.trust_box.setChecked(True)
        lay.addWidget(self.trust_box)
        lay.addStretch(1)
        (self.client_buttons[found_any][0] if found_any else none_rb).setChecked(True)
        return w

    def enter_agent(self):
        self.pick_client(self.client)

    def default_folder(self):
        # The helper's own workspace (it holds its sign-in), next to — never inside — the shared files/.
        base = self.p.GetString("HostDataDir") if self.mode == "host" else ""
        base = base or os.path.join(projects_dir(), self.project or "Team project")
        return os.path.join(base, "AI helpers", f"{slug(self.name)}-{self.client}")

    def pick_client(self, key):
        self.client = key
        if not hasattr(self, "folder_row"):
            return
        installed = self.client_buttons.get(key, (None, True))[1]
        self.folder_row.setVisible(key is not None)
        self.trust_box.setVisible(key == "codex")
        if key and not self.folder_user_set:
            self.folder = self.default_folder()
        if key:
            self.folder_label.setText(f"Your helper's workspace folder:<br><b>{getattr(self, 'folder', '')}</b>")
        if key and not installed:
            self.missing_note.setText(f"Install {CLIENTS[key]['label']} first (click “How to install”), "
                                      "then click “check again”.")
            self.buttons("Connect my AI helper ›", None)
            self.primary.setEnabled(False)
        elif key:
            self.missing_note.setText("")
            self.buttons("Connect my AI helper ›", lambda: self.show_page("agent_work"))
        else:
            self.missing_note.setText("You can add an AI helper later from the CadSwarm toolbar (“Add AI helper”).")
            self.buttons("Finish", self.finish)

    def change_folder(self):
        d = QtGui.QFileDialog.getExistingDirectory(self, "Choose your helper's workspace folder",
                                                   getattr(self, "folder", projects_dir()))
        if d:
            self.folder, self.folder_user_set = os.path.normpath(d), True
            self.pick_client(self.client)

    def page_agent_work(self):
        w, lay = self.page("Connecting your AI helper…")
        self.agent_steps = Checklist(on_done=lambda: self.show_page("launch"))
        lay.addWidget(self.agent_steps, 1)
        return w

    def enter_agent_work(self):
        self.buttons(secondary="Cancel", secondary_action=self.reject)
        label_ = CLIENTS[self.client]["label"]
        steps = [("Get the helper software (one time, about 2 minutes)",
                  "Downloading the small programs your AI helper uses to reach the team. Needs internet.",
                  self.step_deps, False),
                 (f"Connect {label_} to your team", None, self.step_write_config, False)]
        if self.client == "codex" and self.trust_box.isChecked():
            steps.append(("Let Codex use this folder", None, lambda log: trust_in_codex(self.folder), False))
        self.agent_steps.set_steps(steps)
        self.agent_steps.start()

    def step_write_config(self, log):
        self.agent_name = f"{slug(self.name)}-{self.client}"
        role = "lead" if self.mode == "host" else "member"
        written = write_agent_config(self.folder, self.client, self.hub_for_agent, self.token, self.agent_name,
                                     role, python=self.python)
        for f in written:
            log(f"wrote {f}")
        self.p.SetString("AgentClient", self.client)
        self.p.SetString("AgentFolder", self.folder)

    # ---------------------------------------------------------------- launch

    def page_launch(self):
        label_ = CLIENTS[self.client]["label"]
        w, lay = self.page(f"Start {label_}", "Three quick steps and your helper is on the team.")
        lay.addWidget(label("<b>1.</b> Open your helper in its workspace folder:"))
        open_btn = QtGui.QPushButton(f"Open {label_}")
        open_btn.setObjectName("primary")
        self.open_status = label("", "hint")
        open_btn.clicked.connect(self.open_helper)
        lay.addWidget(open_btn, 0, QtCore.Qt.AlignLeft)
        lay.addWidget(self.open_status)
        trust = ("If it asks whether you trust this folder, choose <b>Yes</b>."
                 if self.client == "claude" else "If it asks to trust or approve this folder, choose <b>Yes</b>.")
        lay.addWidget(label(f"<b>2.</b> A window opens. {trust} If it asks you to sign in, follow its steps."))
        role = "lead" if self.mode == "host" else "member"
        prompt = FIRST_PROMPT[role].format(project=self.project)
        lay.addWidget(label("<b>3.</b> Paste this as your first message and press Enter:"))
        box = QtGui.QPlainTextEdit(prompt)
        box.setReadOnly(True)
        box.setMaximumHeight(70)
        lay.addWidget(box)
        copy = QtGui.QPushButton("Copy message")
        copy.clicked.connect(lambda: copy_to_clipboard(prompt, copy))
        lay.addWidget(copy, 0, QtCore.Qt.AlignLeft)
        lay.addSpacing(6)
        self.helper_status = label("⏳ Waiting for your AI helper to join the team…")
        lay.addWidget(self.helper_status)
        lay.addStretch(1)
        return w

    def enter_launch(self):
        self.buttons("Finish", self.finish)
        self.poll.start(2000)

    def open_helper(self):
        try:
            open_agent_terminal(self.client, self.folder)
            self.open_status.setText("Opened ✓ — look for a new window on your screen.")
        except Exception as e:
            self.open_status.setText(f"<span style='color:#e5484d'>{e}</span> You can also open a terminal in "
                                     f"<b>{self.folder}</b> and type <code>{CLIENTS[self.client]['exe']}</code>.")

    def check_helper_joined(self):
        if not panel.SESSION.connected or not self.agent_name:
            return
        try:
            people = panel.SESSION.client.get("/participants")["participants"]
        except HubRequestError:
            return
        if any(p["name"] == self.agent_name and p["status"] == "active" for p in people):
            self.poll.stop()
            self.helper_status.setObjectName("ok")
            self.helper_status.setStyleSheet("color: #30a46c; font-weight: 600;")
            self.helper_status.setText(f"✓ Your AI helper “{self.agent_name}” joined the team! "
                                       "Watch the model build in the CadSwarm Live view.")
            self.primary.setText("Done — show me the model")

    # ------------------------------------------------------------- reconnect

    def page_reconnect(self):
        w, lay = self.page(f"Reconnecting to “{self.project or 'your team'}”…")
        self.re_steps = Checklist(on_done=self.finish)
        lay.addWidget(self.re_steps, 1)
        return w

    def enter_reconnect(self):
        self.buttons(secondary="Cancel", secondary_action=self.reject)
        steps = []
        if self.p.GetString("HostDataDir"):
            steps += [("Check the helper software", None, self.step_deps, False),
                      ("Start your team's server", None, self.step_restart_hub, False)]
        steps.append(("Join your team", None, self.step_rejoin, True))
        self.re_steps.set_steps(steps)
        self.re_steps.start()

    def step_restart_hub(self, log):
        port = self.p.GetString("HostPort")
        url = f"http://127.0.0.1:{port}"
        if hosting.health(url):
            log("Team server is already running.")
            pid = hosting.pid_on_port(port)
            if pid:
                self.p.SetString("HostPid", str(pid))  # a stale PID would make "Stop hosting" do nothing
            return
        data_dir, token = self.p.GetString("HostDataDir"), self.p.GetString("HostToken")
        try:
            pid = hosting.start_hub(self.python, data_dir, token, int(port))
        except OSError:
            raise RuntimeError("Couldn't start the team server.")
        hosting.wait_healthy(url, timeout=120, pid=pid, data_dir=data_dir)
        self.p.SetString("HostPid", str(pid))
        lan = f"http://{hosting.lan_ip()}:{port}"
        self.p.SetString("InviteCode", make_invite(lan, token, self.project))  # the network address may change

    def step_rejoin(self, log):
        hub = (f"http://127.0.0.1:{self.p.GetString('HostPort')}" if self.p.GetString("HostDataDir")
               else self.p.GetString("HubUrl"))
        panel.SESSION.connect(hub, self.p.GetString("HostToken") or self.p.GetString("WorkspaceToken"),
                              self.p.GetString("Name"), timeout=10)

    # ---------------------------------------------------------------- finish

    def finish(self):
        self.poll.stop()
        self.accept()
        panel.show_panel()
        if panel.SESSION.connected:
            panel.SESSION.refresh_meta()


def run_wizard(reason=None, start=None):
    """Open the setup guide. Returns True if the user ends up in a team session."""
    panel.show_panel()
    dlg = SetupWizard(reason=reason, start=start)
    run = getattr(dlg, "exec", None) or dlg.exec_
    run()
    return panel.SESSION.connected


def stop_hosting():
    p = panel.prefs()
    pid = p.GetString("HostPid")
    if not p.GetString("HostDataDir"):
        QtGui.QMessageBox.information(Gui.getMainWindow(), "CadSwarm", "This computer isn't hosting a team.")
        return
    if not panel.confirm("Stop hosting your team? Everyone's AI helpers will lose their connection until you "
                         "start it again (CadSwarm ▸ Get started ▸ Reconnect). Your project is saved."):
        return
    panel.SESSION.disconnect()
    hosting.stop_hub(pid)
    App.Console.PrintMessage("[CadSwarm] Stopped hosting. Your project is saved.\n")

