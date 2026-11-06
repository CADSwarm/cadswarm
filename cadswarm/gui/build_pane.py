"""The AI Build tab of the CADSwarm task pane: one prompt becomes a numbered plan, built one step at a time.

    [ Describe what to build…                       ]  [Build it]
    Four-blade rotor head                     [request ▾]
    ████████░░░░░░░░░░░░  3 of 20 done · now: Pitch link
    (1) ✓ Mast                       Part   · ann-claude
    (2) ✓ Base                       Part
    (3) ● Pitch link                 Part   · bob-codex is building it
         The pitch link: a short rod with ball ends…
         Create sketch  SK_PitchLink       1.9 s
         Extrude        PitchLink          Running
         Set color                         (planned)
    (4) ○ Servo screws               Library · Waiting for Base

The view-model is build_view.py (pure). Data: the panel's snapshot (briefs and tasks) right away, then the
hub's ``GET /briefs/<id>/progress?ops=1`` answer (MCP: build_progress), read off the GUI thread.
"""
import html
import threading
import time

from PySide import QtCore, QtGui

from . import build_view

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


class StepRow(QtGui.QFrame):
    """One step: badge, name, kind chip, who; the summary and the op feed when expanded."""

    def __init__(self, step, colours, expanded, toggled, parent=None):
        super().__init__(parent)
        self.step, self.c = step, colours
        self.setObjectName("stepCurrent" if step["state"] == build_view.STATE_CURRENT else "step")
        self.setCursor(QtCore.Qt.PointingHandCursor)
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
        chip = QtGui.QLabel(step["kind_word"])
        chip.setObjectName("chip")
        chip.setStyleSheet(f"color: {colours['MUTED']}; border: 1px solid {colours['LINE']}; border-radius: 8px;"
                           f"padding: 0 6px; font-size: 10px; background: {colours['SURFACE_2']};")
        head.addWidget(chip)
        col.addLayout(head)
        sub = step["note"] or (step["who"] if step["state"] == build_view.STATE_DONE else "")
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

        cap = QtGui.QLabel("DESCRIBE WHAT TO BUILD")
        cap.setObjectName("caption")
        lay.addWidget(cap)
        self.caption_labels = [cap]
        self.prompt = QtGui.QPlainTextEdit()
        self.prompt.setObjectName("prompt")
        self.prompt.setPlaceholderText("A four-blade rotor head: 6 mm steel mast, a hub with four blade grips, "
                                       "pitch links and a swashplate. Printable, M3 hardware.")
        self.prompt.setFixedHeight(72)
        lay.addWidget(self.prompt)
        row = QtGui.QHBoxLayout()
        self.hint = QtGui.QLabel("The team plans it as numbered steps, one component each, then builds them in order.")
        self.hint.setWordWrap(True)
        self.hint.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        self.hint.setStyleSheet("font-size: 11px;")
        row.addWidget(self.hint, 1)
        self.build_button = QtGui.QPushButton("Build it")
        self.build_button.setObjectName("primary")
        self.build_button.setToolTip("Hand the request to the team: it plans the steps and starts AI helpers")
        self.build_button.clicked.connect(self.submit)
        row.addWidget(self.build_button)
        lay.addLayout(row)

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
        try:
            from . import workspace
            return workspace.WORKSPACE.tokens()
        except Exception:  # noqa: BLE001
            from . import pro_style
            return pro_style.tokens("light")

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
            f"QProgressBar::chunk {{ background: {c['ACCENT']}; border-radius: 3px; }}")
        self.hint.setStyleSheet(f"color: {c['MUTED']}; font-size: 11px;")
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
            row = StepRow(step, c, expanded, self.toggle)
            self.list_lay.addWidget(row)
            self.rows.append(row)

    def toggle(self, step):
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
        self.prompt.setFocus()

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
        ready = [s for s in self.steps((build_view.STATE_PENDING,)) if s["ready"] or s["note"] == "Ready"]
        if not ready:
            _p().hint("No step is ready right now: each waiting step needs the steps it depends on first."
                      if self.model["total"] else "There is no plan yet. Describe what to build and click Build it.")
            return
        step = ready[0]
        helpers = self._helpers(free_only=True) or self._helpers()
        if not helpers:
            _p().hint("No AI helper has joined yet. Add one from the AI Build tab (New Helper).")
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

    def assign(self):
        if not self.session.ensure_connected("assign a step"):
            return
        step = self._pick_step("Assign a step", (build_view.STATE_PENDING, build_view.STATE_BLOCKED))
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
