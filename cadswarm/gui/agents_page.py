"""The Agents tab: "where do I connect my agents?"

    CONNECT YOUR AGENTS
    ┌ Claude Code        ● 1 of 2 connected            [Add another] ┐
    │ C:\\Users\\…\\claude.exe                                          │
    │   ● carol-claude  working   ○ old-claude  away                  │
    ├ Codex              Not installed                       [Install] ┤
    ├ OpenCode           Detected · not connected            [Connect] ┤
    └ Custom command     [ aider --mcp …            ]          [Save]  ┘
    ON ANOTHER DEVICE
    [Claude Code ▾] [name]  [Make connection code]
    1. Install CADSwarm …  2. cadswarm setup --client … --invite …  3. cd … && claude      [Copy]
    MCP SERVER  {"mcpServers": {"cadswarm": {...}}}                                           [Copy]

The rows come from team_view.agent_rows (unit-tested); detection is agent_setup.find_agent_cli.
"""
import html
import os
import time

import FreeCADGui as Gui
from PySide import QtCore, QtGui

from . import team_view
from .prompt_bar import colours

DETECT_SECONDS = 30


def _p():
    from . import panel
    return panel


def detect():
    """{client: path or None} for every agent CLI CADSwarm knows."""
    from ..agent_setup import CLIENTS, find_agent_cli
    out = {}
    for key in CLIENTS:
        try:
            out[key] = find_agent_cli(key)
        except Exception:  # noqa: BLE001
            out[key] = None
    return out


class AgentRow(QtGui.QFrame):
    def __init__(self, row, page):
        super().__init__()
        self.row, self.page = row, page
        c = colours()
        self.setObjectName("agentRow")
        lay = QtGui.QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(3)
        top = QtGui.QHBoxLayout()
        top.setSpacing(8)
        online = any(h["online"] for h in row["helpers"])
        dot = c["OK"] if online else c["ACCENT"] if row["installed"] else c["DISABLED_TEXT"]
        name = QtGui.QLabel(f"<span style='color:{dot}'>●</span>&nbsp; <b>{html.escape(row['label'])}</b>")
        top.addWidget(name)
        status = QtGui.QLabel(html.escape(row["status"]))
        status.setStyleSheet(f"color: {c['OK'] if online else c['MUTED']}; font-size: 11px;")
        status.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        top.addWidget(status, 1)
        if row["client"] == team_view.CUSTOM:
            pass
        elif row["installed"]:
            b = QtGui.QPushButton("Add another" if row["helpers"] else "Connect")
            b.setObjectName("quiet" if row["helpers"] else "primary")
            b.setToolTip(f"Start a {row['label']} helper on this computer. It joins the team, takes build steps "
                         "and answers in the chat. Runs in the background: no terminal to keep open.")
            b.clicked.connect(lambda: page.connect_local(row["client"]))
            top.addWidget(b)
        else:
            b = QtGui.QPushButton("Install…")
            b.setObjectName("quiet")
            b.setToolTip(f"Opens {row['install_url']}. Install it, sign in once, then come back and Connect.")
            b.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(row["install_url"])))
            top.addWidget(b)
        lay.addLayout(top)
        if row["client"] == team_view.CUSTOM:
            line = QtGui.QHBoxLayout()
            self.command = QtGui.QLineEdit(row["path"])
            self.command.setPlaceholderText("Any MCP agent's command, e.g. aider or goose")
            line.addWidget(self.command, 1)
            save = QtGui.QPushButton("Save")
            save.setObjectName("quiet")
            save.setToolTip("Remember this command; the MCP details below connect it to the team")
            save.clicked.connect(lambda: page.save_custom(self.command.text()))
            line.addWidget(save)
            lay.addLayout(line)
        elif row["path"]:
            path = QtGui.QLabel(html.escape(row["path"]))
            path.setStyleSheet(f"color: {c['MUTED']}; font-size: 10px;")
            path.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
            path.setToolTip(row["path"])
            lay.addWidget(path)
        if row["helpers"]:
            words = []
            for h in row["helpers"]:
                colour = c["WARN"] if h["state"] == "working" else c["OK"] if h["online"] else c["DISABLED_TEXT"]
                words.append(f"<span style='color:{colour}'>{'●' if h['online'] else '○'}</span> "
                             f"{html.escape(h['name'])} <span style='color:{c['MUTED']}'>"
                             f"{html.escape(h['state'])}{' · this computer' if h['here'] else ''}</span>")
            helpers = QtGui.QLabel("&nbsp;&nbsp; ".join(words))
            helpers.setWordWrap(True)
            helpers.setStyleSheet("font-size: 11px;")
            helpers.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
            lay.addWidget(helpers)


class AgentsPage(QtGui.QWidget):
    def heightForWidth(self, width):  # a hidden tab must not set the panel's scroll height (panel.TabPage)
        return super().heightForWidth(width) if self.isVisible() else -1

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.found = {}
        self.found_at = 0.0
        self.key = None
        self.instructions = {}
        lay = QtGui.QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self.caption("Connect your agents"))
        lede = QtGui.QLabel("AI agents join your team as helpers: they take build steps and tasks, hold the parts "
                            "they change and answer in the chat. Agents on this computer run in the background.")
        lede.setWordWrap(True)
        lede.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        self.lede = lede
        lay.addWidget(lede)
        self.rows_box = QtGui.QFrame()
        self.rows_box.setObjectName("agentRows")
        self.rows_lay = QtGui.QVBoxLayout(self.rows_box)
        self.rows_lay.setContentsMargins(0, 0, 0, 0)
        self.rows_lay.setSpacing(0)
        lay.addWidget(self.rows_box)
        refresh = QtGui.QPushButton("Look for agents again")
        refresh.setObjectName("quiet")
        refresh.clicked.connect(lambda: self.render(force=True))
        lay.addWidget(refresh, 0, QtCore.Qt.AlignRight)

        lay.addSpacing(6)
        lay.addWidget(self.caption("On another device"))
        other = QtGui.QLabel("Run an agent on a laptop, a server or a teammate's machine: make a one-time code, "
                             "then run the steps below there.")
        other.setWordWrap(True)
        other.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        lay.addWidget(other)
        form = QtGui.QHBoxLayout()
        self.remote_client = QtGui.QComboBox()
        for key, label in (("claude", "Claude Code"), ("codex", "Codex"), ("opencode", "OpenCode"),
                           (team_view.CUSTOM, "Other MCP agent")):
            self.remote_client.addItem(label, key)
        form.addWidget(self.remote_client)
        self.remote_name = QtGui.QLineEdit()
        self.remote_name.setPlaceholderText("its name, e.g. laptop-claude")
        self.remote_name.setMaxLength(40)
        form.addWidget(self.remote_name, 1)
        lay.addLayout(form)
        self.make = QtGui.QPushButton("Make connection code")
        self.make.setObjectName("primary")
        self.make.setToolTip("A single-use invite for one agent (valid 24 hours), with the exact commands to run")
        self.make.clicked.connect(self.make_code)
        lay.addWidget(self.make)
        self.steps = QtGui.QPlainTextEdit()
        self.steps.setReadOnly(True)
        self.steps.setObjectName("mono")
        self.steps.setPlaceholderText("The steps for the other device appear here.")
        self.steps.setFixedHeight(150)
        lay.addWidget(self.steps)
        row = QtGui.QHBoxLayout()
        self.copy_setup = QtGui.QPushButton("Copy setup command")
        self.copy_setup.setObjectName("quiet")
        self.copy_setup.clicked.connect(lambda: self.copy("setup"))
        self.copy_steps = QtGui.QPushButton("Copy all steps")
        self.copy_steps.setObjectName("quiet")
        self.copy_steps.clicked.connect(lambda: self.copy("steps"))
        row.addWidget(self.copy_setup)
        row.addWidget(self.copy_steps)
        lay.addLayout(row)

        lay.addSpacing(6)
        lay.addWidget(self.caption("MCP server details"))
        mcp_note = QtGui.QLabel("Every CADSwarm agent talks to the team through the <b>cadswarm</b> MCP server "
                                "(stdio). This is the entry an MCP agent's settings need, for a folder that ran "
                                "<i>cadswarm setup</i>:")
        mcp_note.setWordWrap(True)
        mcp_note.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        lay.addWidget(mcp_note)
        self.mcp = QtGui.QPlainTextEdit()
        self.mcp.setReadOnly(True)
        self.mcp.setObjectName("mono")
        self.mcp.setFixedHeight(130)
        lay.addWidget(self.mcp)
        copy_mcp = QtGui.QPushButton("Copy MCP config")
        copy_mcp.setObjectName("quiet")
        copy_mcp.clicked.connect(lambda: self.copy("mcp"))
        lay.addWidget(copy_mcp, 0, QtCore.Qt.AlignRight)
        lay.addStretch(1)
        self.restyle()
        self.show_mcp()

    def caption(self, text):
        label = QtGui.QLabel(text.upper())
        label.setObjectName("caption")
        return label

    def restyle(self):
        c = colours()
        self.setStyleSheet(
            f"QFrame#agentRow {{ background: {c['SURFACE']}; border: 1px solid {c['LINE']}; border-top: none; }}"
            f"QFrame#agentRow QLabel {{ color: {c['TEXT']}; background: transparent; }}"
            f"QFrame#agentRow QLineEdit {{ background: {c['BG']}; color: {c['TEXT']}; border: 1px solid {c['LINE']};"
            " padding: 3px 6px; }"
            f"QFrame#agentRows {{ border-top: 1px solid {c['LINE']}; }}"
            f"QLabel#caption {{ color: {c['MUTED']}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}"
            f"QPlainTextEdit#mono {{ font-family: Consolas, 'Cascadia Mono', Menlo, monospace; font-size: 11px;"
            f" background: {c['SURFACE_2']}; color: {c['TEXT']}; border: 1px solid {c['LINE']}; }}")
        self.lede.setStyleSheet(f"color: {c['MUTED']};")

    # ------------------------------------------------------------------ data

    def custom_command(self):
        try:
            return _p().prefs().GetString("CustomAgentCommand")
        except Exception:  # noqa: BLE001
            return ""

    def render(self, s=None, force=False):
        s = s or self.session
        if force or time.time() - self.found_at > DETECT_SECONDS:
            self.found, self.found_at = detect(), time.time()
        from ..agent_setup import CLIENTS
        try:
            local = {name: {"state": st.get("state") or ""} for name, st in _p().local_helper_status(s).items()}
        except Exception:  # noqa: BLE001
            local = {}
        rows = team_view.agent_rows(CLIENTS, self.found, (s.model or {}).get("participants") or [], local,
                                    self.custom_command())
        key = repr(rows)
        if key == self.key and not force:
            return
        self.key = key
        while self.rows_lay.count():
            w = self.rows_lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for r in rows:
            self.rows_lay.addWidget(AgentRow(r, self))

    def show_mcp(self):
        from ..paths import LAUNCHER, freecad_python
        try:
            python = freecad_python()
        except Exception:  # noqa: BLE001
            python = "python"
        folder = ""
        try:
            folders = _p().local_helper_folders()
            folder = folders[-1] if folders else ""
        except Exception:  # noqa: BLE001
            pass
        import json
        entry = team_view.mcp_server_entry(python, LAUNCHER, folder or "<the helper folder>")
        self.instructions["mcp"] = json.dumps({"mcpServers": {"cadswarm": entry}}, indent=2)
        self.mcp.setPlainText(self.instructions["mcp"])

    # ------------------------------------------------------------------ actions

    def connect_local(self, client):
        P = _p()
        if not self.session.ensure_connected("connect an AI agent"):
            return
        P.NewHelperDialog(self.session, client=client).exec_()
        self.render(force=True)

    def save_custom(self, text):
        _p().prefs().SetString("CustomAgentCommand", text.strip())
        _p().say("Saved. Give that agent the MCP details below (or run its setup on the other device).")
        self.render(force=True)

    def make_code(self):
        P = _p()
        s = self.session
        if not s.ensure_connected("connect an agent on another device"):
            return
        client = self.remote_client.currentData()
        taken = [p["name"] for p in s.model.get("participants") or []]
        me = (s.ident or {}).get("name") or "me"
        typed = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in self.remote_name.text().strip())[:40]
        name = typed or team_view.helper_name(me + "-remote", client if client != team_view.CUSTOM else "agent",
                                              taken)
        project = P.prefs().GetString("ProjectName")
        try:
            code = s.client.create_invite("agent", "member", 86400, project,
                                          uri=P.hosting.invite_uri(s.client.hub))
        except Exception as e:  # noqa: BLE001
            QtGui.QMessageBox.warning(Gui.getMainWindow(), "CADSwarm", P.friendly_error(e, s.client.hub)
                                      if isinstance(e, P.HubRequestError) else str(e))
            return
        self.instructions.update(team_view.remote_instructions(client, code, name, folder=f"~/cadswarm-{name}"))
        self.steps.setPlainText(self.instructions["steps"])
        QtGui.QApplication.clipboard().setText(self.instructions["setup"])
        P.say(f"Connection code made for {name}: the setup command is copied")

    def copy(self, what):
        text = self.instructions.get(what)
        if not text:
            _p().hint("Make a connection code first." if what != "mcp" else "Nothing to copy yet.")
            return
        QtGui.QApplication.clipboard().setText(text)
        _p().say("Copied")
