"""Prompt the AI from anywhere in CADSwarm: a prompt bar at the top of the panel, and the same bar floating over the
3D view (Ctrl+K), in every workbench.

    [AI Build · whole team ▾] [ Describe what to build or change…  @ for people and parts ] [Send]
    ◆ 2 parts selected: Base, Lid  ·  attached as context

What a prompt does is decided by team_view.route_prompt (unit-tested): the AI build plans and builds it as numbered
steps, a helper gets it as a task, "Every AI helper" tells them all, "Team chat" talks to the people. An @name in the
text wins over the pick, like the chat box; @Part names and the current selection go along as context.
"""
import re
import time

import FreeCADGui as Gui
from PySide import QtCore, QtGui

from ..mentions import mention_prefix
from . import team_view

SHORTCUT = "Ctrl+K"
SELECTION_POLL_MS = 600


def _p():
    from . import panel  # panel imports this module: import it back lazily
    return panel


def colours():
    """The colours the panel is drawn in right now, so everything drawn here matches its sheet: the pro look's
    tokens while it is on, CADSwarm's own panel tokens otherwise (the same choice as workspace.panel_style)."""
    from . import pro_style
    try:
        from . import workspace
        if hasattr(workspace, "panel_tokens"):  # the theme owner's single answer, when there is one
            return dict(pro_style.tokens(getattr(workspace.WORKSPACE, "mode", "light")), **workspace.panel_tokens())
        if workspace.WORKSPACE.active:
            return workspace.WORKSPACE.tokens()
    except Exception:  # noqa: BLE001
        pass
    from . import theme
    base = pro_style.tokens("dark")
    base.update(getattr(theme, "_TOKENS", {}))
    base["ACCENT_SOFT"] = "#2b2116"  # a dark wash of CADSwarm orange
    return base


def current_selection():
    """The selected objects' names as the team knows them: the live model's names, else the label people see."""
    out = []
    try:
        from ..naming import is_mirror_doc
        for sel in Gui.Selection.getSelection():
            out.append(sel.Name if is_mirror_doc(sel.Document.Name) else (sel.Label or sel.Name))
    except Exception:  # noqa: BLE001 - no document, or FreeCAD shutting down
        pass
    return list(dict.fromkeys(out))


def team_names(session):
    """(helpers, people, parts) on the team right now."""
    ps = (getattr(session, "model", None) or {}).get("participants") or []
    helpers = [p["name"] for p in ps if p.get("kind") != "human" and p.get("status") in ("active", "stale", None)]
    people = [p["name"] for p in ps if p.get("kind") == "human"]
    parts = [o["name"] for o in (session.model.get("objects") or []) if o.get("name")]
    return helpers, people, parts


def dispatch(session, r, done=None):
    """Send what route_prompt decided. Returns True when it went (or is going) out."""
    P = _p()
    panel = session.panel
    if r["kind"] == "error":
        P.hint(r["error"])
        return False
    if not session.ensure_connected("send that to your team"):
        return False
    if r["kind"] == "brief":
        def handed(_r):
            if panel:
                panel.handed_to_team(open_build=True)
            if done:
                done()
        session.act_async("POST", "/briefs", {"text": r["text"]}, purpose="hand the request to the team",
                          done=handed)
    elif r["kind"] == "task":
        for helper in r["to"]:
            if panel:
                panel.give_task(helper, r["text"], r["what"], objects=r["objects"])
            else:
                session.act_async("POST", "/messages", {"to": helper, "body": r["text"], "kind": "command"})
        P.say(f"Sent to {', '.join(r['to'])}: it starts now and answers in the chat")
    elif r["kind"] == "all":
        if panel:
            panel.post(r["text"], "command", "all")
        P.say("Told every AI helper")
    else:
        if panel:
            panel.post(r["text"], "chat", "all")
    if panel:
        panel.render_chat(session)
    if done and r["kind"] != "brief":
        done()
    return True


class PromptBar(QtGui.QFrame):
    """The bar itself; the panel shows one at its top and the 3D view floats another (compact=True)."""

    sent = QtCore.Signal()
    escaped = QtCore.Signal()

    def __init__(self, session, compact=False, parent=None):
        super().__init__(parent)
        self.session = session
        self.compact = compact
        self.completed_at = 0.0
        self.selection = None  # forces the first context line
        self.setObjectName("promptBar")
        lay = QtGui.QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(5)
        head = QtGui.QHBoxLayout()
        head.setSpacing(6)
        self.label = QtGui.QLabel("ASK THE AI")
        self.label.setObjectName("promptCaption")
        self.label.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        head.addWidget(self.label, 1)
        self.target = QtGui.QComboBox()
        self.target.setObjectName("promptTarget")
        self.target.setToolTip("Who gets this: the AI build (the team plans numbered steps and builds them), one AI "
                               "helper, every helper, or the people on the team. Typing @name wins.")
        self.target.setSizeAdjustPolicy(QtGui.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.target.setMinimumContentsLength(9)  # compact docks show a short label; the menu has full names
        head.addWidget(self.target)
        self.keys = QtGui.QLabel(SHORTCUT)
        self.keys.setObjectName("kbd")
        self.keys.setToolTip(f"{SHORTCUT} opens this bar over the 3D view from any workbench")
        head.addWidget(self.keys)
        lay.addLayout(head)
        row = QtGui.QHBoxLayout()
        row.setSpacing(6)
        self.edit = QtGui.QLineEdit()
        self.edit.setObjectName("promptEdit")
        self.edit.setPlaceholderText("Describe what to build or change…  @ names a helper, person or part")
        self.edit.setClearButtonEnabled(True)
        self.edit.setMinimumWidth(80)
        self.edit.returnPressed.connect(self.submit)
        self.edit.textEdited.connect(self.maybe_complete)
        self.edit.installEventFilter(self)
        row.addWidget(self.edit, 1)
        self.send_button = QtGui.QPushButton("Send")
        self.send_button.setObjectName("primary")
        self.send_button.setToolTip("Send (Enter)")
        self.send_button.clicked.connect(self.submit)
        row.addWidget(self.send_button)
        lay.addLayout(row)
        self.context = QtGui.QLabel("")
        self.context.setObjectName("promptContext")
        self.context.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        self.context.setToolTip("What you select in the model goes along with the prompt, so the AI knows which "
                                "parts you mean")
        lay.addWidget(self.context)
        self.completer = QtGui.QCompleter(self)
        self.completer.setModel(QtCore.QStringListModel([], self.completer))
        self.completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.completer.setCompletionMode(QtGui.QCompleter.PopupCompletion)
        self.completer.setWidget(self.edit)
        try:
            self.completer.activated[str].connect(self.insert_mention)
        except (KeyError, TypeError, IndexError):
            self.completer.activated.connect(self.insert_mention)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(SELECTION_POLL_MS)
        self.restyle()
        self.refresh()

    # ------------------------------------------------------------------ look

    def restyle(self):
        c = colours()
        self.setStyleSheet(
            f"QFrame#promptBar {{ background: {c['SURFACE']}; border: 1px solid {c['LINE_STRONG']};"
            f" border-radius: 8px; }}"
            f"QLabel#promptCaption {{ color: {c['MUTED']}; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
            " background: transparent; border: none; }"
            f"QLabel#kbd {{ color: {c['MUTED']}; background: {c['SURFACE_2']}; border: 1px solid {c['LINE']};"
            " border-radius: 4px; padding: 0 5px; font-size: 10px; }"
            f"QLabel#promptContext {{ color: {c['MUTED']}; font-size: 11px; background: transparent; border: none; }}"
            f"QLineEdit#promptEdit {{ background: {c['BG']}; color: {c['TEXT']}; border: 1px solid {c['LINE']};"
            f" border-radius: 6px; padding: 6px 8px; selection-background-color: {c['ACCENT']}; }}"
            f"QLineEdit#promptEdit:focus {{ border: 1px solid {c['ACCENT']}; }}"
            f"QComboBox#promptTarget {{ background: {c['SURFACE_2']}; color: {c['TEXT']}; border: 1px solid"
            f" {c['LINE']}; border-radius: 4px; padding: 1px 6px; font-size: 11px; }}"
            f"QPushButton#primary {{ background: {c['ACCENT']}; color: {c['ON_ACCENT']}; border: none;"
            " border-radius: 6px; padding: 6px 14px; font-weight: 600; }"
            f"QPushButton#primary:hover {{ background: {c['ACCENT_HOVER']}; }}")

    # ------------------------------------------------------------------ data

    def refresh(self):
        """Keep the "Send to" list, the @ list and the selection line current (cheap; skips when hidden)."""
        if not self.isVisible():
            return
        helpers, people, parts = team_names(self.session)
        keep = self.target.currentData()
        items = team_view.targets(helpers)
        have = [(self.target.itemData(i), self.target.itemText(i)) for i in range(self.target.count())]
        if have != items:
            self.target.blockSignals(True)
            self.target.clear()
            for key, label in items:
                self.target.addItem(label, key)
            i = self.target.findData(keep) if keep else 0
            self.target.setCurrentIndex(max(i, 0))
            self.target.blockSignals(False)
        me = (self.session.ident or {}).get("name")
        choices = team_view.completions(helpers, people, parts, me)
        if self.completer.model().stringList() != choices:
            self.completer.model().setStringList(choices)
        sel = current_selection()
        if sel != self.selection:
            self.selection = sel
            if sel:
                line = team_view.selection_context(sel, limit=3).replace("Selected in the model: ", "")
                self.context.setText(f"◆ {len(sel)} selected: {line} · goes along as context")
            else:
                self.context.setText("Select parts in the model to point the AI at them")

    def choose(self, key):
        self.refresh()
        i = self.target.findData(key)
        if i >= 0:
            self.target.setCurrentIndex(i)

    def focus(self, text=None):
        if text is not None:
            self.edit.setText(text)
        self.edit.setFocus(QtCore.Qt.ShortcutFocusReason)
        self.edit.setCursorPosition(len(self.edit.text()))

    # ------------------------------------------------------------------ @ completion

    def maybe_complete(self, _text=""):
        prefix = mention_prefix(self.edit.text()[:self.edit.cursorPosition()])
        if prefix is None:
            self.completer.popup().hide()
            return
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0:
            self.completer.popup().hide()
            return
        width = max(200, self.completer.popup().sizeHintForColumn(0) + 30)
        self.completer.complete(QtCore.QRect(0, 0, min(width, self.edit.width()), self.edit.height()))

    def insert_mention(self, name):
        text, pos = self.edit.text(), self.edit.cursorPosition()
        before = re.sub(r"@[A-Za-z0-9_-]*$", "@" + name + " ", text[:pos])
        self.edit.setText(before + text[pos:])
        self.edit.setCursorPosition(len(before))
        self.completed_at = time.time()

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QtCore.QEvent.KeyPress and ev.key() == QtCore.Qt.Key_Escape:
            if not self.completer.popup().isVisible():
                self.escaped.emit()
                return True
        return super().eventFilter(obj, ev)

    # ------------------------------------------------------------------ send

    def route(self):
        helpers, people, parts = team_names(self.session)
        return team_view.route_prompt(self.edit.text(), self.target.currentData() or team_view.TARGET_BUILD,
                                      helpers, people, parts, current_selection())

    def submit(self):
        if self.completer.popup().isVisible() or time.time() - self.completed_at < 0.3:
            return  # that Enter picked a name from the list
        r = self.route()
        if r["kind"] == "error" and not self.edit.text().strip():
            self.edit.setFocus()
            return
        typed = self.edit.text()
        if dispatch(self.session, r):
            self.edit.clear()
            self.sent.emit()
        else:
            self.edit.setText(typed)


# ------------------------------------------------------------------------------------------ over the 3D view

class AskOverlay(QtGui.QWidget):
    """A pill at the bottom of the 3D view ("Ask the AI… Ctrl+K") that opens into the prompt bar. It is a child of
    the main window, not of a workbench's toolbars, so it is there in Part Design, Sketcher, Assembly, TechDraw…"""

    WIDTH = 620

    def __init__(self, session, mw):
        super().__init__(mw)
        self.session = session
        self.mw = mw
        self.setObjectName("askOverlay")
        lay = QtGui.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.pill = QtGui.QPushButton(f"✦  Ask the AI or your team…        {SHORTCUT}")
        self.pill.setObjectName("askPill")
        self.pill.setCursor(QtCore.Qt.PointingHandCursor)
        self.pill.setToolTip("Prompt the AI build or a helper without leaving the model")
        self.pill.clicked.connect(self.open)
        lay.addWidget(self.pill, 0, QtCore.Qt.AlignHCenter)
        self.bar = PromptBar(session, compact=True)
        self.bar.label.setText("ASK THE AI · Esc closes")
        self.bar.sent.connect(self.close_bar)
        self.bar.escaped.connect(self.close_bar)
        self.bar.hide()
        lay.addWidget(self.bar)
        c = colours()
        self.pill.setStyleSheet(
            f"QPushButton#askPill {{ background: {c['SURFACE']}; color: {c['MUTED']}; border: 1px solid "
            f"{c['LINE_STRONG']}; border-radius: 15px; padding: 6px 18px; font-size: 12px; }}"
            f"QPushButton#askPill:hover {{ color: {c['TEXT']}; border-color: {c['ACCENT']}; }}")
        effect = QtGui.QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(18)
        effect.setOffset(0, 3)
        effect.setColor(QtGui.QColor(0, 0, 0, 70))
        self.setGraphicsEffect(effect)
        self.area = None
        self.follow()
        self.show()
        self.raise_()

    def target_area(self):
        if self.area is None:
            self.area = self.mw.findChild(QtGui.QMdiArea)
            if self.area is not None:
                self.area.installEventFilter(self)
        return self.area

    def follow(self):
        """Sit centred near the bottom of the 3D view area, whatever docks and toolbars are around it."""
        area = self.target_area()
        if area is None or not area.isVisible():
            return
        top_left = area.mapTo(self.mw, QtCore.QPoint(0, 0))
        w = min(self.WIDTH, max(260, area.width() - 40))
        self.setFixedWidth(w)
        self.adjustSize()
        h = self.sizeHint().height()
        x = top_left.x() + (area.width() - w) // 2
        y = top_left.y() + area.height() - h - 44  # clear of the document tabs and the axis cross
        self.setGeometry(x, y, w, h)
        self.raise_()

    def eventFilter(self, obj, ev):
        if obj is self.area and ev.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Move, QtCore.QEvent.Show):
            QtCore.QTimer.singleShot(0, self.follow)
        return super().eventFilter(obj, ev)

    def open(self, target=None):
        self.pill.hide()
        self.bar.show()
        if target:
            self.bar.choose(target)
        self.bar.refresh()
        self.follow()
        self.bar.focus()

    def toggle(self):
        if self.bar.isVisible() and self.bar.edit.hasFocus():
            self.close_bar()
        else:
            self.open()

    def close_bar(self):
        self.bar.hide()
        self.pill.show()
        self.follow()
        try:
            mdi = self.target_area()
            if mdi is not None and mdi.activeSubWindow() is not None:
                mdi.activeSubWindow().widget().setFocus()
        except Exception:  # noqa: BLE001
            pass


OVERLAY = None
_SHORTCUTS = []


def install(session):
    """Put the ask bar over the 3D view and bind Ctrl+K in the whole application (every workbench). Idempotent."""
    global OVERLAY
    mw = Gui.getMainWindow()
    if mw is None:
        return None
    if OVERLAY is None:
        OVERLAY = AskOverlay(session, mw)
        mw.installEventFilter(_Follower(mw))
    if not _SHORTCUTS:
        sc = QtGui.QShortcut(QtGui.QKeySequence(SHORTCUT), mw)
        sc.setContext(QtCore.Qt.ApplicationShortcut)
        sc.activated.connect(lambda: OVERLAY.toggle())
        _SHORTCUTS.append(sc)
    return OVERLAY


class _Follower(QtCore.QObject):
    """Re-places the overlay when the main window resizes or a dock opens or closes."""

    def eventFilter(self, obj, ev):
        if ev.type() in (QtCore.QEvent.Resize, QtCore.QEvent.LayoutRequest) and OVERLAY is not None:
            QtCore.QTimer.singleShot(0, OVERLAY.follow)
        return False
