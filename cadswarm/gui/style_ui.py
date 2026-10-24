"""The Learned style dialog: what CADSwarm learned about how this team designs, and the person's say in it.

Rules come from the host's style memory (cadswarm/style_memory.py): the values people use, the corrections
they keep making to agent work, preferred parts and recurring review problems. Strong rules already guide
the agents; weaker ones (and rules agents proposed) wait here for a person to confirm or dismiss. A person
can also teach a rule in their own words; it is confirmed straight away.

Runs on FreeCAD's GUI thread with the same blocking client calls the other dialogs use.
"""
import html

import FreeCADGui as Gui
from PySide import QtCore, QtGui

from ..client import HubRequestError

TYPE_WORDS = {"param_convention": "shared size", "correction_pattern": "people's corrections",
              "preferred_component": "preferred part", "modelling_practice": "modelling habit",
              "recurring_problem": "recurring problem", "taught": "taught by a person"}


def _p():
    from . import panel  # panel imports this module; import it back lazily
    return panel


def _exec(dlg):
    return (getattr(dlg, "exec", None) or dlg.exec_)()


def evidence_text(r):
    ev = r.get("evidence") or {}
    if r.get("source") in ("taught", "proposed"):
        who = ev.get("taught_by") or "someone"
        return f"taught by {who}" if r["source"] == "taught" else f"proposed by {who}"
    bits = [f"seen {ev['n']:g}×" if isinstance(ev.get("n"), (int, float)) else ""]
    if ev.get("people"):
        bits.append(f"{ev['people']} {'person' if ev['people'] == 1 else 'people'}")
    bits.append(f"confidence {r.get('confidence', 0):.0%}")
    return ", ".join(b for b in bits if b)


class StyleDialog(QtGui.QDialog):
    """What the agents follow, what waits for a person, and a box to teach a rule."""

    def __init__(self):
        super().__init__(Gui.getMainWindow())
        self.setWindowTitle("CADSwarm: learned design style")
        self.resize(680, 620)
        lay = QtGui.QVBoxLayout(self)
        intro = QtGui.QLabel("CADSwarm watches how your team builds, the sizes you use and what you change "
                             "after an AI helper, and turns it into rules every helper follows. "
                             "Confirm the rules you agree with, dismiss the ones you don't.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.pending_box = QtGui.QGroupBox("Waiting for you")
        self.pending_lay = QtGui.QVBoxLayout(self.pending_box)
        lay.addWidget(self.pending_box)
        used = QtGui.QGroupBox("What the helpers follow now")
        used_lay = QtGui.QVBoxLayout(used)
        scroll = QtGui.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtGui.QWidget()
        self.used_lay = QtGui.QVBoxLayout(inner)
        scroll.setWidget(inner)
        used_lay.addWidget(scroll)
        lay.addWidget(used, 1)
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

    @staticmethod
    def _clear(layout):
        while layout.count():
            w = layout.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _row(self, r, buttons):
        row = QtGui.QWidget()
        rl = QtGui.QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        tag = "✔ " if r["status"] == "confirmed" else ""
        text = QtGui.QLabel(f"{tag}<b>{html.escape(r['statement'])}</b><br><small style='color:#8b949e'>"
                            f"{html.escape(TYPE_WORDS.get(r['type'], r['type']))}: "
                            f"{html.escape(evidence_text(r))}</small>")
        text.setWordWrap(True)
        text.setTextFormat(QtCore.Qt.RichText)
        rl.addWidget(text, 1)
        for label, decision in buttons:
            b = QtGui.QPushButton(label)
            b.clicked.connect(lambda _=False, i=r["id"], d=decision: self.resolve(i, d))
            rl.addWidget(b)
        return row

    def load(self):
        try:
            rules = _p().SESSION.client.post("/style/rules", {})["rules"]
        except HubRequestError as e:
            _p().hint(_p().friendly_error(e))
            return
        self._clear(self.pending_lay)
        self._clear(self.used_lay)
        pending = [r for r in rules if r["status"] == "candidate"]
        used = [r for r in rules if r["status"] in ("confirmed", "active")]
        self.pending_box.setVisible(bool(pending))
        for r in pending[:12]:
            self.pending_lay.addWidget(self._row(r, [("Confirm", "confirm"), ("Dismiss", "dismiss")]))
        if not used:
            none = QtGui.QLabel("Nothing yet: rules appear once the team has built a few parts.")
            none.setWordWrap(True)
            self.used_lay.addWidget(none)
        for r in used:
            buttons = [("Remove", "dismiss")] if r["status"] == "confirmed" else [("Confirm", "confirm"),
                                                                                  ("Dismiss", "dismiss")]
            self.used_lay.addWidget(self._row(r, buttons))
        self.used_lay.addStretch(1)

    def resolve(self, rid, decision):
        if _p().SESSION.act("POST", "/style/resolve", {"rule_id": rid, "decision": decision},
                            "Confirmed the style rule" if decision == "confirm" else "Dismissed the style rule",
                            purpose="answer a style rule") is not None:
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
