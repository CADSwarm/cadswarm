"""The Build tab of the CADSwarm panel: one prompt (the prompt bar above the tabs) becomes a numbered plan, built
one step at a time.

    Four-blade rotor head                     [request ▾]
    ████████░░░░░░░░░░░░  3 of 20 done · now: Pitch link
    NEXT  Step 4 · Servo screws        [▶ Run next step] [Run all ready] [⋯]
    (1) ✓ Mast              Done      ● ann-claude · AI
    (2) ✓ Base              Done      ● ann-claude · AI
    (3) ● Pitch link        Running   ● bob-codex · AI
         The pitch link: a short rod with ball ends…
         Create sketch  SK_PitchLink       1.9 s
         Extrude        PitchLink          Running
         Set color                         (planned)
    (4) ○ Servo screws      Ready     Unassigned                  [Assign]
    (5) ○ Lid               Waiting   ● dave · person             [Assign]

Click a step to select its parts in the model tree (and open its op feed); Assign hands it to a person or helper.

The view-model is build_view.py (pure). Data: the panel's snapshot (briefs and tasks) right away, then the
hub's ``GET /briefs/<id>/progress?ops=1`` answer (MCP: build_progress), read off the GUI thread.
"""
import html
import threading
import time

from PySide import QtCore, QtGui

from . import build_view, team_view

FETCH_SECONDS = 2.5        # at most this often while the pane is showing
RETRY_ROUTE_SECONDS = 60   # a hub without build_progress is asked again this rarely
FEED_SHOWN = 8             # op-feed rows per expanded step (the newest ones)


def _p():
    from . import panel  # panel imports this module; import it back lazily
    return panel


class Badge(QtGui.QWidget):
    """The step's number in a circle: filled with a check when done, an accent ring when current."""

    def __init__(self, n, state, colours, parent=None):
        super().__init__(parent)
        self.n, self.state, self.c = n, state, colours
        self.phase = 0
        self.setFixedSize(24, 24)

    def sizeHint(self):
        return QtCore.QSize(24, 24)

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(2, 2, 20, 20)
        c = self.c
        if self.state == build_view.STATE_DONE:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(c["OK"]))
            p.drawEllipse(r)
            pen = QtGui.QPen(QtGui.QColor("#ffffff"), 2)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            pen.setJoinStyle(QtCore.Qt.RoundJoin)
            p.setPen(pen)
            p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(7.5, 12.5), QtCore.QPointF(10.5, 15.5),
                                            QtCore.QPointF(16.5, 9)]))
            return
        colour = {build_view.STATE_CURRENT: c["ACCENT"], build_view.STATE_BLOCKED: c["ERR"]}.get(
            self.state, c["LINE_STRONG"])
        p.setBrush(QtGui.QColor(c["ACCENT_SOFT"]) if self.state == build_view.STATE_CURRENT else QtCore.Qt.NoBrush)
        p.setPen(QtGui.QPen(QtGui.QColor(colour), 1.5))
        p.drawEllipse(r)
        if self.state == build_view.STATE_CURRENT:  # a turning arc: this step is being built
            pen = QtGui.QPen(QtGui.QColor(c["ACCENT"]), 2.5)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawArc(QtCore.QRectF(1, 1, 22, 22), int(-self.phase * 16), 90 * 16)
        font = p.font()
        font.setPixelSize(10 if (self.n or 0) < 100 else 8)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QtGui.QColor(c["ACCENT"] if self.state == build_view.STATE_CURRENT else
                              c["ERR"] if self.state == build_view.STATE_BLOCKED else c["MUTED"]))
        p.drawText(r, QtCore.Qt.AlignCenter, str(self.n if self.n is not None else "·"))

    def tick(self):
        if self.state == build_view.STATE_CURRENT:
            self.phase = (self.phase + 30) % 360
            self.update()


STATE_TONES = {"Done": "OK", "Running": "ACCENT", "Ready": "TEXT", "Waiting": "MUTED", "Stuck": "ERR",
               "Cancelled": "DISABLED_TEXT"}


class StepRow(QtGui.QFrame):
    """One step: badge, name, state chip, who builds it, Assign; the summary and the op feed when expanded."""

    def __init__(self, step, colours, expanded, toggled, parent=None, assignee=("", ""), assign=None):
        super().__init__(parent)
        self.step, self.c = step, colours
        self.setObjectName("stepCurrent" if step["state"] == build_view.STATE_CURRENT else "step")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Click to select this step's parts in the model")
        self.toggled = toggled
        lay = QtGui.QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)
        self.badge = Badge(step["n"], step["state"], colours)
        top = QtGui.QVBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.badge, 0, QtCore.Qt.AlignTop)
        lay.addLayout(top, 1)
        col = QtGui.QVBoxLayout()
        col.setSpacing(2)
        head = QtGui.QHBoxLayout()
        head.setSpacing(6)
        name = QtGui.QLabel(html.escape(step["name"]))
        name.setObjectName("stepName")
        name.setTextFormat(QtCore.Qt.RichText)
        if step["state"] in (build_view.STATE_PENDING, build_view.STATE_CANCELLED):
            name.setStyleSheet(f"color: {colours['BODY']};")
        name.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        head.addWidget(name, 1)
        word = team_view.step_state_word(step)
        tone = colours[STATE_TONES.get(word, "MUTED")]
        chip = QtGui.QLabel(word)
        chip.setObjectName("chip")
        chip.setToolTip(f"{step['kind_word']} step · {word}")
        chip.setStyleSheet(f"color: {tone}; border: 1px solid {tone}; border-radius: 8px;"
                           f"padding: 0 7px; font-size: 10px; font-weight: 600; background: transparent;")
        head.addWidget(chip)
        col.addLayout(head)
        who, kind = assignee
        line = QtGui.QHBoxLayout()
        line.setSpacing(6)
        if who:
            words = {"ai": "AI helper", "person": "person"}.get(kind, "")
            owner = QtGui.QLabel(f"<span style='color:{colours['ACCENT'] if kind == 'ai' else colours['OK']}'>"
                                 f"{'◆' if kind == 'ai' else '●'}</span> {html.escape(who)}"
                                 + (f" <span style='color:{colours['MUTED']}'>· {words}</span>" if words else ""))
        else:
            owner = QtGui.QLabel(f"<span style='color:{colours['MUTED']}'>Unassigned · the next free helper "
                                 "takes it</span>" if step["state"] == build_view.STATE_PENDING else "")
        owner.setStyleSheet(f"color: {colours['BODY']}; font-size: 11px;")
        owner.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        line.addWidget(owner, 1)
        if assign and step["state"] in (build_view.STATE_PENDING, build_view.STATE_BLOCKED):
            b = QtGui.QPushButton("Reassign" if who else "Assign")
            b.setObjectName("stepAssign")
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setToolTip("Hand this step to a person or an AI helper")
            b.clicked.connect(lambda: assign(step))
            line.addWidget(b)
        col.addLayout(line)
        # The state chip and the owner line say the rest; only "Waiting for Hub" and "Stuck" need words.
        waiting = step["state"] == build_view.STATE_PENDING and step["note"].startswith("Waiting for")
        sub = step["note"] if waiting or step["state"] == build_view.STATE_BLOCKED else ""
        if sub:
            note = QtGui.QLabel(html.escape(sub))
            colour = colours["ACCENT"] if step["state"] == build_view.STATE_CURRENT else \
                colours["ERR"] if step["state"] == build_view.STATE_BLOCKED else colours["MUTED"]
            note.setStyleSheet(f"color: {colour}; font-size: 11px;")
            note.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
            col.addWidget(note)
        if expanded:
            if step["summary"]:
                summary = QtGui.QLabel(html.escape(step["summary"]))
                summary.setWordWrap(True)
                summary.setStyleSheet(f"color: {colours['BODY']}; font-size: 11px;")
                summary.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
                col.addWidget(summary)
            rows = step["feed"][-FEED_SHOWN:] + step["planned"][:4]
            if rows:
                feed = QtGui.QFrame()
                feed.setObjectName("feed")
                fl = QtGui.QGridLayout(feed)
                fl.setContentsMargins(8, 4, 8, 4)
                fl.setHorizontalSpacing(8)
                fl.setVerticalSpacing(1)
                for i, f in enumerate(rows):
                    tone = colours["DISABLED_TEXT"] if f.get("planned") else colours["TEXT"]
                    label = QtGui.QLabel(html.escape(f["label"]))
                    label.setStyleSheet(f"color: {tone}; font-size: 11px;")
                    target = QtGui.QLabel(html.escape(f["target"]))
                    target.setStyleSheet(f"color: {colours['MUTED'] if not f.get('planned') else tone};"
                                         "font-size: 11px;")
                    target.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
                    when = QtGui.QLabel(f["time"])
                    when.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                    tcol = colours["ERR"] if f["failed"] else colours["ACCENT"] if f["running"] else colours["MUTED"]
                    when.setStyleSheet(f"color: {tcol}; font-size: 11px;" + (" font-weight: 600;" if f["running"]
                                                                              else ""))
                    fl.addWidget(label, i, 0)
                    fl.addWidget(target, i, 1)
                    fl.addWidget(when, i, 2)
                fl.setColumnStretch(1, 1)
                col.addWidget(feed)
        top.addLayout(col)

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton and self.toggled:
            self.toggled(self.step)
        super().mousePressEvent(ev)


class BuildPage(QtGui.QWidget):
    """The AI Build tab (lives in the TeamPanel's tabs)."""

    def heightForWidth(self, width):  # a hidden tab must not set the panel's scroll height (panel.TabPage)
        return super().heightForWidth(width) if self.isVisible() else -1

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.brief_id = None       # the request picked in the combo (None = follow the newest live one)
        self.model = build_view.view({"steps": []})
        self.route_model = {}      # brief id -> (time, model) from build_progress
        self.route_missing_at = 0  # when the hub said it has no build_progress
        self.fetching = False
        self.fetched_at = 0.0
        self.expanded = set()      # step ids opened by hand
        self.collapsed = set()     # current steps closed by hand
        self.key = None
        self.rows = []
        lay = QtGui.QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(8)

        self.prompt = QtGui.QPlainTextEdit()   # kept for submit(); the prompt bar above the tabs is what people use
        self.prompt.hide()
        self.hint = QtGui.QLabel("")
        self.hint.hide()
        self.build_button = QtGui.QPushButton("Build it")
        self.build_button.clicked.connect(self.submit)
        self.build_button.hide()
        self.prompt_bar = None  # the panel's PromptBar (set by the panel)

        self.card = QtGui.QFrame()
        self.card.setObjectName("card")
        cl = QtGui.QVBoxLayout(self.card)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(6)
        top = QtGui.QHBoxLayout()
        self.title = QtGui.QLabel("No build yet")
        self.title.setObjectName("title")
        self.title.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        top.addWidget(self.title, 1)
        self.pick = QtGui.QComboBox()
        self.pick.setToolTip("Which request to follow")
        self.pick.setSizeAdjustPolicy(QtGui.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.pick.setMinimumContentsLength(10)
        self.pick.activated.connect(self.picked)
        top.addWidget(self.pick)
        cl.addLayout(top)
        self.bar = QtGui.QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        cl.addWidget(self.bar)
        self.line = QtGui.QLabel("Steps appear here once the team has planned the request.")
        self.line.setObjectName("progressLine")
        self.line.setWordWrap(True)
        self.line.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        cl.addWidget(self.line)
        lay.addWidget(self.card)

        # What happens next, and the button that makes it happen.
        self.next_box = QtGui.QFrame()
        self.next_box.setObjectName("nextBox")
        nl = QtGui.QVBoxLayout(self.next_box)
        nl.setContentsMargins(12, 8, 12, 8)
        nl.setSpacing(6)
        self.next_line = QtGui.QLabel("")
        self.next_line.setObjectName("nextLine")
        self.next_line.setWordWrap(True)
        self.next_line.setTextFormat(QtCore.Qt.RichText)
        self.next_line.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        nl.addWidget(self.next_line)
        buttons = QtGui.QHBoxLayout()
        buttons.setSpacing(6)
        self.next_button = QtGui.QPushButton("▶  Run next step")
        self.next_button.setObjectName("primary")
        self.next_button.setToolTip("Ask a free AI helper (or the step's assignee) to build the next ready step now")
        self.next_button.clicked.connect(self.run_next)
        buttons.addWidget(self.next_button)
        self.all_button = QtGui.QPushButton("Run all ready")
        self.all_button.setObjectName("quiet")
        self.all_button.setToolTip("Every free helper takes a ready step, then the next, until the plan is built")
        self.all_button.clicked.connect(self.run_all)
        buttons.addWidget(self.all_button)
        buttons.addStretch(1)
        self.more_button = QtGui.QToolButton()
        self.more_button.setText("More ▾")
        self.more_button.setObjectName("quietTool")
        self.more_button.setPopupMode(QtGui.QToolButton.InstantPopup)
        menu = QtGui.QMenu(self.more_button)
        menu.addAction("Assign a step…", self.assign)
        menu.addAction("Re-plan from a step…", self.replan)
        menu.addAction("Pause the build", self.pause)
        menu.addAction("Ask for a review", self.review)
        self.more_button.setMenu(menu)
        buttons.addWidget(self.more_button)
        nl.addLayout(buttons)
        lay.addWidget(self.next_box)
        steps_cap = QtGui.QLabel("STEPS")
        steps_cap.setObjectName("caption")
        lay.addWidget(steps_cap)
        self.caption_labels = [steps_cap]

        self.list_box = QtGui.QWidget()
        self.list_lay = QtGui.QVBoxLayout(self.list_box)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(2)
        lay.addWidget(self.list_box)
        lay.addStretch(1)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(120)
        self.restyle()

    # ------------------------------------------------------------------ look

    def colours(self):
        from .prompt_bar import colours
        return colours()

    def restyle(self):
        c = self.colours()
        self.setStyleSheet(
            f"QFrame#step {{ background: transparent; border: 1px solid transparent; border-radius: 6px; }}"
            f"QFrame#step:hover {{ background: {c['SURFACE_2']}; border-color: {c['LINE']}; }}"
            f"QFrame#stepCurrent {{ background: {c['ACCENT_SOFT']}; border: 1px solid {c['ACCENT']};"
            f" border-radius: 6px; }}"
            f"QLabel#stepName {{ font-weight: 600; color: {c['TEXT']}; }}"
            f"QFrame#feed {{ background: {c['SURFACE']}; border: 1px solid {c['LINE']}; border-radius: 4px; }}"
            f"QLabel#progressLine {{ color: {c['TEXT']}; font-weight: 600; }}"
            f"QLabel#caption {{ color: {c['MUTED']}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}"
            f"QProgressBar {{ background: {c['LINE']}; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {c['ACCENT']}; border-radius: 3px; }}"
            f"QFrame#nextBox {{ background: {c['SURFACE']}; border: 1px solid {c['LINE']};"
            f" border-left: 3px solid {c['ACCENT']}; border-radius: 6px; }}"
            f"QLabel#nextLine {{ color: {c['TEXT']}; }}"
            f"QPushButton#stepAssign {{ color: {c['ACCENT']}; background: transparent; border: 1px solid {c['LINE']};"
            f" border-radius: 4px; padding: 1px 8px; font-size: 11px; }}"
            f"QPushButton#stepAssign:hover {{ border-color: {c['ACCENT']}; }}"
            f"QToolButton#quietTool {{ color: {c['BODY']}; background: transparent; border: 1px solid {c['LINE']};"
            f" border-radius: 4px; padding: 3px 8px; }}"
            f"QToolButton#quietTool::menu-indicator {{ image: none; }}")
        self.key = None
        self.draw(self.model)

    # ------------------------------------------------------------------ data

    def briefs(self):
        return list(getattr(self.session, "briefs", None) or [])

    def current_brief(self):
        return build_view.pick_brief(self.briefs(), self.brief_id)

    def render(self, s):
        """From the snapshot (called on every panel refresh); asks the hub for the full progress too."""
        self.fill_picker()
        brief = self.current_brief()
        cached = self.route_model.get(brief["id"]) if brief else None
        model = cached[1] if cached else build_view.from_snapshot(brief, getattr(s, "tasks", []))
        self.draw(model)
        self.maybe_fetch(brief)

    def fill_picker(self):
        briefs = sorted(self.briefs(), key=lambda b: b.get("at") or 0, reverse=True)
        items = [(b["id"], (b.get("text") or "Request")[:60]) for b in briefs]
        have = [(self.pick.itemData(i), self.pick.itemText(i)) for i in range(self.pick.count())]
        if have != items:
            self.pick.blockSignals(True)
            self.pick.clear()
            for bid, text in items:
                self.pick.addItem(text, bid)
            self.pick.blockSignals(False)
        brief = self.current_brief()
        if brief:
            i = self.pick.findData(brief["id"])
            if i >= 0 and i != self.pick.currentIndex():
                self.pick.setCurrentIndex(i)
        self.pick.setVisible(len(items) > 1)

    def picked(self, index):
        self.brief_id = self.pick.itemData(index)
        self.key = None
        self.fetched_at = 0
        self.render(self.session)

    def maybe_fetch(self, brief):
        s = self.session
        if not brief or self.fetching or not getattr(s, "connected", False) or not getattr(s, "client", None):
            return
        now = time.time()
        if now - self.fetched_at < FETCH_SECONDS:
            return
        if self.route_missing_at and now - self.route_missing_at < RETRY_ROUTE_SECONDS:
            return
        if not self.isVisible():
            return
        self.fetching, self.fetched_at = True, now
        client, events, bid = s.client, s.events, brief["id"]

        def go():
            try:
                answer = client.request("GET", f"/briefs/{bid}/progress", params={"ops": 1})
                events.put(("build", (bid, answer, None)))
            except Exception as e:  # noqa: BLE001 - an older hub, or offline: the snapshot view stays
                events.put(("build", (bid, None, e)))
        threading.Thread(target=go, daemon=True).start()

    def show_route(self, data):
        """The hub's build_progress answer (on the GUI thread, from Session.drain)."""
        bid, answer, error = data
        self.fetching = False
        if error is not None:
            if "No route" in str(error):  # a hub from before step plans: the snapshot view stays
                self.route_missing_at = time.time()
            return
        brief = next((b for b in self.briefs() if b["id"] == bid), None)
        model = build_view.from_route(answer, brief, getattr(self.session, "tasks", []))
        self.route_model[bid] = (time.time(), model)
        current = self.current_brief()
        if current and current["id"] == bid:
            self.draw(model)

    # ------------------------------------------------------------------ drawing

    def draw(self, model):
        self.model = model
        key = (build_view.view_key(model), tuple(sorted(self.expanded)), tuple(sorted(self.collapsed)))
        self.bar.setValue(int(round(model["fraction"] * 1000)))
        brief = self.current_brief()
        self.title.setText(html.escape(model["title"] or (brief or {}).get("text") or "No build yet"))
        if model["total"]:
            self.line.setText(html.escape(model["line"]))
        elif brief:
            self.line.setText("Planning: the team is splitting the request into steps…"
                              if brief.get("status") in ("planning", "open", "new", "queued")
                              else html.escape(build_view.view({"steps": []})["line"]))
        else:
            self.line.setText("Steps appear here once the team has planned the request.")
        self.card.setVisible(bool(brief or model["total"]))  # no request yet: the next-step box explains
        for cap in self.caption_labels:
            cap.setVisible(bool(model["total"]))
        self.show_next(model, brief)
        participants = (getattr(self.session, "model", None) or {}).get("participants") or []
        key = key + (tuple((p.get("name"), p.get("kind")) for p in participants),)
        if key == self.key:
            return
        self.key = key
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.rows = []
        c = self.colours()
        for step in model["steps"]:
            sid = step["id"]
            expanded = sid in self.expanded or (step["state"] == build_view.STATE_CURRENT and sid not in self.collapsed)
            row = StepRow(step, c, expanded, self.toggle, assignee=team_view.step_assignee(step, participants),
                          assign=self.assign)
            self.list_lay.addWidget(row)
            self.rows.append(row)

    def show_next(self, model, brief):
        """The "what happens next" box: the next ready step and its button, or what the build waits on."""
        step, sentence = team_view.next_step(model)
        c = self.colours()
        if step is not None:
            who, kind = team_view.step_assignee(step, (self.session.model or {}).get("participants") or [])
            by = (f" <span style='color:{c['MUTED']}'>· {html.escape(who)} builds it</span>" if who else
                  f" <span style='color:{c['MUTED']}'>· the next free helper builds it</span>")
            self.next_line.setText(f"<span style='color:{c['ACCENT']}; font-weight:700; letter-spacing:1px; "
                                   f"font-size:10px'>NEXT</span>&nbsp; <b>Step {step['n']} · "
                                   f"{html.escape(step['name'])}</b>{by}")
        elif model["total"] or brief:
            self.next_line.setText(html.escape(sentence) if model["total"] else
                                   "The team is planning the steps. They appear below in a moment.")
        else:
            self.next_line.setText("<b>No build yet.</b> Type what to build in <b>Ask the AI</b> above (or press "
                                   "Ctrl+K anywhere): the team plans it as numbered steps, one part each, then "
                                   "builds them in order.")
        self.next_button.setEnabled(step is not None)
        person = step is not None and team_view.step_assignee(
            step, (self.session.model or {}).get("participants") or [])[1] == "person"
        self.next_button.setText(("✉  Tell " + step["who"] + " it's ready") if person else
                                 f"▶  Run step {step['n']}" if step is not None else "▶  Run next step")
        has_steps = bool(model["total"])
        self.all_button.setVisible(has_steps)
        self.more_button.setVisible(has_steps)
        self.next_button.setVisible(has_steps)

    def select_parts(self, step):
        """Select the step's parts in the model tree and the 3D view (the live model first, else the open file)."""
        import FreeCAD as App
        import FreeCADGui as Gui
        from ..naming import is_mirror_doc
        names = team_view.step_objects(step, getattr(self.session, "tasks", []),
                                       (getattr(self.session, "model", None) or {}).get("objects") or [])
        if not names:
            return 0
        docs = [d for n, d in App.listDocuments().items() if is_mirror_doc(n)]
        if App.ActiveDocument is not None and App.ActiveDocument not in docs:
            docs.append(App.ActiveDocument)
        found = []
        for name in names:
            for doc in docs:
                obj = doc.getObject(name) or next((o for o in doc.Objects if o.Label.split(" [")[0] == name), None)
                if obj is not None:
                    found.append((doc.Name, obj.Name))
                    break
        if not found:
            return 0
        Gui.Selection.clearSelection()
        for doc_name, obj_name in found:
            Gui.Selection.addSelection(doc_name, obj_name)
        return len(found)

    def toggle(self, step):
        try:
            n = self.select_parts(step)
            if n:
                _p().say(f"Selected step {step['n']}'s part{'s' if n != 1 else ''} in the model")
        except Exception as e:  # noqa: BLE001 - selecting is a nicety; the step still opens
            _p().warn(f"couldn't select the step's parts: {e}")
        sid = step["id"]
        if step["state"] == build_view.STATE_CURRENT:
            self.collapsed.symmetric_difference_update({sid})
        else:
            self.expanded.symmetric_difference_update({sid})
        QtCore.QTimer.singleShot(0, lambda: self.draw(self.model))

    def tick(self):
        for r in self.rows:
            r.badge.tick()

    # ------------------------------------------------------------------ actions (the AI Build ribbon tab)

    def focus_prompt(self):
        """The ribbon's Plan button: the prompt bar, set to the AI build."""
        bar = self.prompt_bar
        if bar is None:
            panel = getattr(self.session, "panel", None)
            bar = getattr(panel, "prompt_bar", None)
        if bar is not None:
            bar.choose(team_view.TARGET_BUILD)
            bar.focus()

    def submit(self):
        text = self.prompt.toPlainText().strip()
        if not text:
            self.prompt.setFocus()
            _p().hint("Describe what to build first, for example “a wall bracket for a 40 mm pipe, M4 screws”.")
            return
        P = _p()
        if not self.session.ensure_connected("hand the request to the team"):
            return

        def done(_r):
            self.prompt.clear()
            self.brief_id = None
            self.session.notice(P.TEAM_HANDED)
            P.say("Handed to the team: it plans the steps, then helpers build them one at a time.")
        self.session.act_async("POST", "/briefs", {"text": text}, purpose="hand the request to the team", done=done)

    def steps(self, states=None):
        return [s for s in self.model["steps"] if states is None or s["state"] in states]

    def _command(self, to, body, task_id=None, done=None):
        msg = {"to": to, "kind": "command", "body": body}
        if task_id:
            msg["task_id"] = task_id
        self.session.act_async("POST", "/messages", msg, purpose="tell the team", done=done)

    def _helpers(self, free_only=False):
        P = _p()
        out = []
        for p in self.session.model.get("participants") or []:
            if not P.takes_instructions(p):
                continue
            if free_only and p.get("task"):
                continue
            out.append(p["name"])
        return out

    def run_next(self):
        if not self.session.ensure_connected("run the next step"):
            return
        step, sentence = team_view.next_step(self.model)
        if step is None:
            _p().hint(sentence if self.model["total"] else
                      "There is no plan yet. Type what to build in Ask the AI above and press Send.")
            return
        who, kind = team_view.step_assignee(step, (self.session.model or {}).get("participants") or [])
        if kind == "person":  # a person's step: tell them it's their turn instead of handing it to an AI
            msg = f"@{who} step {step['n']} “{step['name']}” is ready for you."
            self.session.act_async("POST", "/messages", {"to": who, "kind": "chat", "body": msg},
                                   purpose="tell your teammate", done=f"Told {who} that step {step['n']} is ready")
            return
        helpers = self._helpers(free_only=True) or self._helpers()
        if not helpers:
            _p().hint("No AI helper has joined yet. Connect one on the Agents tab.")
            return
        who = step["who"] if step["who"] in helpers else helpers[0]
        self._command(who, f"Build step {step['n']} now: “{step['name']}” (task {step['task']}). Claim it with "
                           "claim_task, build it completely (sketch, feature, colour, verify), then report_result.",
                      step["task"] or None, done=f"Asked {who} to build step {step['n']}: {step['name']}")

    def run_all(self):
        if self.session.ensure_connected("run every ready step"):
            self._command("all", "Every free helper: call next_step, build the step it gives you completely "
                                 "(sketch, feature, colour, verify, report), then call next_step again until it "
                                 "says wait or done.", done="Asked every free helper to take a ready step")

    def pause(self):
        if self.session.ensure_connected("pause the build"):
            self._command("all", "Pause the build: finish the op you are running, report where you are, and do "
                                 "not take a new step until someone says go on.", done="Asked the helpers to pause")

    def _pick_step(self, title, states):
        steps = self.steps(states)
        if not steps:
            _p().hint("There is no step to pick." if self.model["total"] else
                      "There is no plan yet. Describe what to build and click Build it.")
            return None
        names = [f"{s['n']}. {s['name']}" for s in steps]
        choice, ok = QtGui.QInputDialog.getItem(self, title, "Step:", names, 0, False)
        return steps[names.index(choice)] if ok and choice in names else None

    def replan(self):
        if not self.session.ensure_connected("re-plan"):
            return
        step = self._pick_step("Re-plan from a step", None)
        if not step:
            return
        change, ok = QtGui.QInputDialog.getText(self, "Re-plan from step", f"What should change from step "
                                                f"{step['n']} ({step['name']}) on?")
        if not ok or not change.strip():
            return
        title = self.model["title"] or "the current build"
        self._command("all", f"Re-plan “{title}” from step {step['n']} ({step['name']}): {change.strip()}. "
                             f"Keep steps 1 to {step['n'] - 1 if isinstance(step['n'], int) else '…'} as they are, "
                             "update the plan (version 2, numbered steps with depends_on) and its tasks.",
                      done=f"Asked the team to re-plan from step {step['n']}")

    def assign(self, step=None):
        if not self.session.ensure_connected("assign a step"):
            return
        step = step or self._pick_step("Assign a step", (build_view.STATE_PENDING, build_view.STATE_BLOCKED))
        if not step:
            return
        people = [p["name"] for p in self.session.model.get("participants") or []
                  if p.get("status") in ("active", "stale", None)]
        if not people:
            _p().hint("Nobody else is in the team yet.")
            return
        who, ok = QtGui.QInputDialog.getItem(self, "Assign a step", f"Who builds step {step['n']} "
                                             f"({step['name']})?", people, 0, False)
        if not ok:
            return
        self._command(who, f"Step {step['n']} “{step['name']}” (task {step['task']}) is yours: build it as soon as "
                           "its dependencies are done (next_step returns it to you first).",
                      step["task"] or None, done=f"Step {step['n']} is assigned to {who}")

    def review(self):
        if self.session.ensure_connected("ask for a review"):
            self._command("all", "Reviewer: check the model now (interference, printability and the request) and "
                                 "report a review round.", done="Asked for a review")
