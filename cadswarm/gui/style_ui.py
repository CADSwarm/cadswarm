"""The Learned style dialog: what CADSwarm learned about how this team designs, and the person's say in it.

Rules come from the host's style memory (cadswarm/style_memory.py): the values people use, the corrections
they keep making to agent work, preferred parts and recurring review problems. Strong rules already guide
the agents; weaker ones (and rules agents proposed) wait here for a person to confirm or dismiss.

The inbox is built for a backlog: rules that describe the same thing are one decision, each decision has a
deterministic "ready / look / weak" recommendation, and a person can answer a whole section or the whole
recommended set in one visible action. Weak rules nothing supports for two weeks leave on their own, but
stay recoverable under "Expired or dismissed". A person can also teach a rule in their own words; it is
confirmed straight away.

Runs on FreeCAD's GUI thread with the same blocking client calls the other dialogs use.
"""
import html

import FreeCADGui as Gui
from PySide import QtCore, QtGui

from ..client import HubRequestError
from . import theme

BADGES = {"confirm": ("Ready", theme.OK), "look": ("Look", theme.WARN), "dismiss": ("Weak", theme.MUTED)}


def _p():
    from . import panel  # panel imports this module; import it back lazily
    return panel


def _exec(dlg):
    return (getattr(dlg, "exec", None) or dlg.exec_)()


def _clear(layout):
    while layout.count():
        w = layout.takeAt(0).widget()
        if w:
            w.deleteLater()


def _muted(text):
    return f"<small style='color:{theme.MUTED}'>{html.escape(text)}</small>"


class BulkBlock(QtGui.QWidget):
    """One bulk action, with the exact rules it will change listed before it is applied."""

    def __init__(self, review_text, apply_text, on_apply):
        super().__init__()
        self._on_apply, self._boxes, self._review_text, self._apply_text = on_apply, {}, review_text, apply_text
        lay = QtGui.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.toggle = QtGui.QPushButton()
        self.toggle.setCheckable(True)
        self.body = QtGui.QWidget()
        self.body_lay = QtGui.QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(10, 2, 0, 2)
        self.body_lay.setSpacing(2)
        self.body_scroll = QtGui.QScrollArea()
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setMaximumHeight(180)
        self.body_scroll.setWidget(self.body)
        self.body_scroll.hide()
        self.apply_button = QtGui.QPushButton()
        self.apply_button.setObjectName("primary")
        row = QtGui.QHBoxLayout()
        row.addWidget(self.apply_button)
        row.addStretch(1)
        lay.addWidget(self.toggle)
        lay.addWidget(self.body_scroll)
        lay.addLayout(row)
        self.toggle.toggled.connect(self.body_scroll.setVisible)
        self.apply_button.clicked.connect(self._apply)
        self.hide()

    def load(self, decisions):
        _clear(self.body_lay)
        self._boxes = {}
        for d in decisions:
            cb = QtGui.QCheckBox(d["title"])
            cb.setChecked(True)
            cb.setToolTip(d.get("detail") or "")
            cb.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
            self._boxes[cb] = list(d["ids"])
            self.body_lay.addWidget(cb)
        n = len(decisions)
        self.setVisible(bool(n))
        self.toggle.setChecked(False)
        self.toggle.setText(f"{self._review_text} · {n}")
        self.apply_button.setText(f"{self._apply_text} · {n}")

    def ids(self):
        return [i for cb, ids in self._boxes.items() if cb.isChecked() for i in ids]

    def _apply(self):
        found = self.ids()
        if found:
            self._on_apply(found)


class StyleDialog(QtGui.QDialog):
    """The triage inbox: what the helpers follow, what waits for a person, and a box to teach a rule."""

    def __init__(self):
        super().__init__(Gui.getMainWindow())
        theme.apply(self)
        self.setWindowTitle("CADSwarm: learned design style")
        self.resize(720, 680)
        lay = QtGui.QVBoxLayout(self)
        intro = QtGui.QLabel("CADSwarm watches how your team builds, the sizes you use and what you change "
                             "after an AI helper, and turns it into rules every helper follows. Rules that "
                             "describe the same thing are one decision here.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.summary = QtGui.QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.ready = BulkBlock("Review the ready rules", "Make these rules",
                               lambda ids: self.resolve(ids, "confirm"))
        self.weak = BulkBlock("Review the weak ones", "Dismiss these",
                              lambda ids: self.resolve(ids, "dismiss"))
        lay.addWidget(self.ready)
        lay.addWidget(self.weak)

        self.pending = QtGui.QWidget()
        self.pending_lay = QtGui.QVBoxLayout(self.pending)
        self.pending_lay.setContentsMargins(0, 0, 0, 0)
        self.pending_lay.setSpacing(10)
        scroll = QtGui.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.pending)
        lay.addWidget(scroll, 1)

        self.used = QtGui.QGroupBox("In use now")
        used_lay = QtGui.QVBoxLayout(self.used)
        self.used_body = QtGui.QWidget()
        self.used_lay = QtGui.QVBoxLayout(self.used_body)
        self.used_lay.setContentsMargins(0, 0, 0, 0)
        used_scroll = QtGui.QScrollArea()
        used_scroll.setWidgetResizable(True)
        used_scroll.setMaximumHeight(150)
        used_scroll.setWidget(self.used_body)
        used_lay.addWidget(used_scroll)
        lay.addWidget(self.used)

        self.history_toggle = QtGui.QPushButton()
        self.history_toggle.setObjectName("quiet")
        self.history_toggle.setCheckable(True)
        self.history = QtGui.QWidget()
        self.history_lay = QtGui.QVBoxLayout(self.history)
        self.history_lay.setContentsMargins(10, 0, 0, 0)
        self.history.hide()
        self.history_toggle.toggled.connect(self.history.setVisible)
        lay.addWidget(self.history_toggle)
        lay.addWidget(self.history)

        teach = QtGui.QGroupBox("Teach a rule")
        tl = QtGui.QHBoxLayout(teach)
        self.teach_text = QtGui.QLineEdit()
        self.teach_text.setPlaceholderText("e.g. Lids are held by four M3 heat-set inserts")
        self.teach_text.setMaxLength(300)
        add = QtGui.QPushButton("Save rule")
        add.clicked.connect(self.teach)
        self.teach_text.returnPressed.connect(self.teach)
        tl.addWidget(self.teach_text, 1)
        tl.addWidget(add)
        lay.addWidget(teach)
        row = QtGui.QHBoxLayout()
        relearn = QtGui.QPushButton("Learn again now")
        relearn.setToolTip("Re-read everything the team has done and update the rules")
        relearn.clicked.connect(self.relearn)
        close = QtGui.QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(relearn)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.load()

    # ------------------------------------------------------------------ the inbox

    def load(self):
        try:
            box = _p().SESSION.client.post("/style/inbox", {})
        except HubRequestError as e:
            _p().hint(_p().friendly_error(e))
            return
        self.box = box
        decisions = [d for s in box.get("sections") or [] for d in s["decisions"]]
        self.summary.setText(self._summary(box))
        self.ready.load([d for d in decisions if d["recommendation"] == "confirm"])
        self.weak.load([d for d in decisions if d["recommendation"] == "dismiss"])
        _clear(self.pending_lay)
        for section in box.get("sections") or []:
            self.pending_lay.addWidget(self._section(section))
        if not decisions:
            none = QtGui.QLabel("Nothing waits for you. Rules that need a person appear here.")
            none.setWordWrap(True)
            self.pending_lay.addWidget(none)
        self.pending_lay.addStretch(1)
        self._fill_used(box.get("used") or [])
        self._fill_history(box.get("history") or [])

    @staticmethod
    def _summary(box):
        if not box.get("decisions"):
            return "Nothing waits for you."
        kinds = ", ".join(f"{s['count']} {s['label'].lower()}" for s in (box.get("sections") or [])[:3])
        text = (f"{box['rules']} things learned · {box['decisions']} decisions"
                f" · {box['ready']} ready to use")
        if box.get("weak"):
            text += f", {box['weak']} weak"
        return text + (f". Mostly {kinds}." if kinds else ".")

    def _section(self, section):
        box = QtGui.QGroupBox(f"{section['label']} · {section['count']}")
        lay = QtGui.QVBoxLayout(box)
        ids = [i for d in section["decisions"] for i in d["ids"]]
        actions = QtGui.QHBoxLayout()
        actions.addStretch(1)
        yes = QtGui.QPushButton("Use all")
        yes.setObjectName("quiet")
        yes.setToolTip(f"Make all {section['count']} rules in this section")
        yes.clicked.connect(lambda _=False, i=ids: self.resolve(i, "confirm"))
        no = QtGui.QPushButton("Dismiss all")
        no.setObjectName("quiet")
        no.clicked.connect(lambda _=False, i=ids: self.resolve(i, "dismiss"))
        actions.addWidget(yes)
        actions.addWidget(no)
        lay.addLayout(actions)
        for d in section["decisions"]:
            lay.addWidget(self._decision(d))
        return box

    def _decision(self, d):
        frame = QtGui.QFrame()
        frame.setObjectName("card")
        lay = QtGui.QVBoxLayout(frame)
        top = QtGui.QHBoxLayout()
        word, colour = BADGES[d["recommendation"]]
        badge = QtGui.QLabel(word)
        badge.setStyleSheet(f"color:{colour}; font-weight:bold;")
        badge.setFixedWidth(46)
        title = QtGui.QLabel(f"<b>{html.escape(d['title'])}</b>")
        title.setWordWrap(True)
        title.setTextFormat(QtCore.Qt.RichText)
        title.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        top.addWidget(badge, 0, QtCore.Qt.AlignTop)
        top.addWidget(title, 1)
        yes = QtGui.QPushButton("Make it a rule")
        if d["recommendation"] == "confirm":
            yes.setObjectName("primary")
        yes.clicked.connect(lambda _=False, i=list(d["ids"]): self.resolve(i, "confirm"))
        no = QtGui.QPushButton("Dismiss")
        no.clicked.connect(lambda _=False, i=list(d["ids"]): self.resolve(i, "dismiss"))
        top.addWidget(no)
        top.addWidget(yes)
        lay.addLayout(top)
        detail = QtGui.QLabel(_muted(d.get("detail") or ""))
        detail.setWordWrap(True)
        lay.addWidget(detail)
        if d.get("also"):
            also = QtGui.QLabel(_muted("Also: " + " · ".join(d["also"])))
            also.setWordWrap(True)
            lay.addWidget(also)
        return frame

    def _fill_used(self, used):
        _clear(self.used_lay)
        self.used.setVisible(bool(used))
        for d in used:
            row = QtGui.QWidget()
            rl = QtGui.QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            word = "confirmed" if d["status"] == "confirmed" else "in use"
            text = QtGui.QLabel(f"<b>{html.escape(d['title'])}</b><br>{_muted(word + ' · ' + (d.get('detail') or ''))}")
            text.setWordWrap(True)
            text.setTextFormat(QtCore.Qt.RichText)
            text.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
            rl.addWidget(text, 1)
            if d["status"] == "active":
                pin = QtGui.QPushButton("Keep")
                pin.setToolTip("Pin this rule: confirmed rules are never demoted")
                pin.clicked.connect(lambda _=False, i=list(d["ids"]): self.resolve(i, "confirm"))
                rl.addWidget(pin)
            remove = QtGui.QPushButton("Remove")
            remove.setToolTip("Stop using this rule; it stays under Expired or dismissed")
            remove.clicked.connect(lambda _=False, i=list(d["ids"]): self.resolve(i, "dismiss"))
            rl.addWidget(remove)
            self.used_lay.addWidget(row)
        self.used_lay.addStretch(1)

    def _fill_history(self, history):
        _clear(self.history_lay)
        self.history_toggle.setText(f"Expired or dismissed · {len(history)}")
        self.history_toggle.setVisible(bool(history))
        self.history.setVisible(self.history_toggle.isChecked() and bool(history))
        for d in history:
            row = QtGui.QWidget()
            rl = QtGui.QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            text = QtGui.QLabel(f"{html.escape(d['title'])} <small style='color:{theme.MUTED}'>"
                                f"({d['status']})</small>")
            text.setWordWrap(True)
            text.setTextFormat(QtCore.Qt.RichText)
            text.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
            rl.addWidget(text, 1)
            back = QtGui.QPushButton("Make it a rule")
            back.setObjectName("quiet")
            back.clicked.connect(lambda _=False, i=list(d["ids"]): self.resolve(i, "confirm"))
            rl.addWidget(back)
            self.history_lay.addWidget(row)

    def resolve(self, ids, decision):
        n = len(ids)
        message = (f"Confirmed {n} style rule{'s' if n != 1 else ''}" if decision == "confirm"
                   else f"Dismissed {n} style rule{'s' if n != 1 else ''}")
        if _p().SESSION.act("POST", "/style/resolve", {"rule_ids": list(ids), "decision": decision},
                            message, purpose="answer a style rule") is not None:
            self.load()

    def teach(self):
        text = self.teach_text.text().strip()
        if not text:
            _p().hint("Write the rule first.")
            return
        if _p().SESSION.act("POST", "/style/teach", {"statement": text}, "Saved a team style rule",
                            purpose="teach a style rule") is not None:
            self.teach_text.clear()
            self.load()

    def relearn(self):
        if _p().SESSION.act("POST", "/style/learn", {}, "Updated the learned style",
                            purpose="re-learn the team style") is not None:
            self.load()


def show_style():
    if not _p().SESSION.ensure_connected("see the team's learned style"):
        return
    _exec(StyleDialog())
