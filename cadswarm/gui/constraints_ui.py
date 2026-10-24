"""The Auto-constrain dialog: review the relationships the model already implies, then bind them.

Nothing is written until a person presses Apply. The dialog is deliberately a list of plain
sentences with the exact expression each part will follow, because a shared size is a promise
about every part that reads it, and it should be read before it is made.
"""
import html

from PySide import QtGui


def _p():
    from . import panel
    return panel


def _g(v):
    return f"{float(v):g}"


def _scope_names(session):
    """The selection, plus the parts it directly uses or is used by (a cut's hole tools matter)."""
    chosen = session.selected_names()
    if not chosen:
        return None, "the visible model"
    model = (session.model or {}).get("objects") or []
    by_name = {o["name"]: o for o in model}
    names = list(chosen)
    for name in chosen:
        o = by_name.get(name) or {}
        for other in list(o.get("uses") or []) + list(o.get("used_by") or []):
            if other in by_name and other not in names:
                names.append(other)
    return names, "the selected parts"


def _binding_text(b):
    if abs(float(b.get("old", 0)) - float(b.get("new", 0))) > 1e-9:
        return (f"<code>{html.escape(b['object'])}.{html.escape(b['property'])}</code> "
                f"{_g(b['old'])} → <b>{_g(b['new'])}</b> "
                f"<span style='color:#8b949e'>({html.escape(b['expression'])})</span>")
    return (f"<code>{html.escape(b['object'])}.{html.escape(b['property'])}</code> = "
            f"{html.escape(b['expression'])}")


class AutoConstrainDialog(QtGui.QDialog):
    def __init__(self, session, result, scope_label):
        super().__init__(_p().Gui.getMainWindow())
        self.session = session
        self.plan = (result or {}).get("plan") or {}
        self.names = (result or {}).get("names")
        self.setWindowTitle("CadSwarm - auto-constrain")
        self.checks = []

        lay = QtGui.QVBoxLayout(self)
        intro = QtGui.QLabel(
            f"CadSwarm found <b>{len(self.plan.get('constraints') or [])}</b> relationship(s) in "
            f"{html.escape(scope_label)}. Ticking one adds a shared size and makes the parts read it; "
            "nothing changes until you apply.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        fits = self.plan.get("fits") or {}
        if fits:
            lay.addWidget(QtGui.QLabel(
                "<small><span style='color:#8b949e'>Team fits</span> slip "
                f"{_g(fits.get('tol_slip_fit', 0))} mm · press {_g(fits.get('tol_press_fit', 0))} mm "
                "(Precision settings)</small>"))

        box = QtGui.QWidget()
        rows = QtGui.QVBoxLayout(box)
        for c in self.plan.get("constraints") or []:
            cb = QtGui.QCheckBox(c["title"])
            cb.setChecked(True)
            self.checks.append((c["id"], cb))
            rows.addWidget(cb)
            detail = QtGui.QLabel(c["detail"])
            detail.setWordWrap(True)
            detail.setContentsMargins(22, 0, 0, 0)
            rows.addWidget(detail)
            for b in c.get("bindings") or []:
                line = QtGui.QLabel(_binding_text(b))
                line.setWordWrap(True)
                line.setContentsMargins(22, 0, 0, 0)
                rows.addWidget(line)
            rows.addSpacing(8)
        rows.addStretch(1)
        area = QtGui.QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(box)
        lay.addWidget(area, 1)

        skipped = self.plan.get("skipped") or []
        if skipped:
            note = QtGui.QLabel(
                f"<small><span style='color:#8b949e'>Left alone: "
                + "; ".join(f"{html.escape(s['object'])} {html.escape(s['reason'])}" for s in skipped[:4])
                + ("…" if len(skipped) > 4 else "") + "</span></small>")
            note.setWordWrap(True)
            lay.addWidget(note)

        buttons = QtGui.QHBoxLayout()
        buttons.addStretch(1)
        cancel = QtGui.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        apply = QtGui.QPushButton("Apply selected")
        apply.setDefault(True)
        apply.clicked.connect(self.apply_selected)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        lay.addLayout(buttons)
        _p().fit_to_screen(self, 620, 560)

    def apply_selected(self):
        ids = [cid for cid, cb in self.checks if cb.isChecked()]
        if not ids:
            self.reject()
            return
        self.setEnabled(False)
        body = {"apply": True, "ids": ids}
        if self.names:
            body["names"] = self.names
        self.session.run_async(lambda c: c.post("/constrain", body), done=self.applied,
                               purpose="bind the shared sizes")

    def applied(self, out):
        out = out or {}
        params = out.get("params") or []
        parts = out.get("objects") or []
        done = len(out.get("applied") or [])
        _p().say(f"Auto-constrain: {done} constraint(s)"
                 + (f"; shared sizes {', '.join(params)}" if params else "")
                 + (f"; recomputed {len(parts)} part(s)" if parts else ""))
        self.accept()


def show():
    p = _p()
    session = p.SESSION
    if not session.ensure_connected("bind the model's shared sizes"):
        return
    names, label = _scope_names(session)

    def preview(result):
        plan = (result or {}).get("plan") or {}
        if not plan.get("constraints"):
            p.say("Auto-constrain: nothing to add — every repeated size already follows a shared size.")
            QtGui.QMessageBox.information(
                p.Gui.getMainWindow(), "CadSwarm: auto-constrain",
                "Nothing to constrain.\n\nA size is only shared when two or more parts carry the same "
                "value within a micron, three aligned parts sit on an equal pitch, or a shaft sits in "
                "a hole. Parts someone else has locked are left alone.")
            return
        result["names"] = names
        dlg = AutoConstrainDialog(session, result, label)
        (getattr(dlg, "exec", None) or dlg.exec_)()

    body = {"apply": False}
    if names:
        body["names"] = names
    session.run_async(lambda c: c.post("/constrain", body), done=preview,
                      purpose="look for shared sizes")
