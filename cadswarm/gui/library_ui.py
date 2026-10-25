"""The Team panel's Library tab and the precision Settings dialog.

Library: browse the team's parts and the built-in standard parts, place one in the shared model, preview it
in 3D, upload a STEP/FCStd/STL, add one from a link, export or import a pack, and ask for recommendations.
Settings: the team's fits, bolt-hole class, thread mode, printing and precision values, with the
suggestions AI helpers made, each accepted or dismissed by a person.

Runs on FreeCAD's GUI thread with the same blocking client calls the other dialogs use. Browsing reads
SpacetimeDB directly (fast); placing, uploading and importing are host ops that take a few seconds.
"""
import html
import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

from ..client import HubRequestError
from ..library.present import SOURCE_WORDS, decimals_for, describe, setting_value_text, size_label
from ..library.store import CATEGORIES
from . import theme

PREVIEW_DOC = "CadSwarmLibraryPreview"
PART_FILTER = "Part files (*.step *.stp *.STEP *.STP *.FCStd *.fcstd *.stl *.STL)"


def _p():
    from . import panel  # panel imports this module; import it back lazily
    return panel


def _exec(dlg):
    return (getattr(dlg, "exec", None) or dlg.exec_)()


def _client():
    return _p().SESSION.client


def _err(e):
    _p().hint(_p().friendly_error(e))


class LibraryTab(QtGui.QWidget):
    """Search box and category filter, the parts list, details, and what you can do with a part."""

    def __init__(self, session):
        super().__init__()
        self.session = session
        self.items = []
        lay = QtGui.QVBoxLayout(self)
        row = QtGui.QHBoxLayout()
        self.query = QtGui.QLineEdit()
        self.query.setPlaceholderText("Search: bearing, M3 screw, motor mount…")
        self.query.returnPressed.connect(self.reload)
        self.query.textChanged.connect(lambda _t: self.timer.start(300))
        self.category = QtGui.QComboBox()
        self.category.addItem("All kinds", "")
        for c in CATEGORIES:
            self.category.addItem(c.capitalize(), c)
        self.category.currentIndexChanged.connect(lambda _i: self.reload())
        row.addWidget(self.query, 1)
        row.addWidget(self.category)
        lay.addLayout(row)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.reload)

        self.tree = QtGui.QTreeWidget()
        self.tree.setHeaderLabels(["Part", "Kind", "Size"])
        self.tree.setColumnWidth(0, 230)
        self.tree.setRootIsDecorated(False)
        self.tree.currentItemChanged.connect(lambda *_: self.selected())
        self.tree.itemDoubleClicked.connect(lambda *_: self.place())
        lay.addWidget(self.tree, 3)

        self.details = QtGui.QLabel("Pick a part to see its details.")
        self.details.setWordWrap(True)
        self.details.setTextFormat(QtCore.Qt.RichText)
        self.details.setOpenExternalLinks(True)
        self.details.setMinimumHeight(60)
        lay.addWidget(self.details, 1)

        self.std_row = QtGui.QWidget()
        srow = QtGui.QHBoxLayout(self.std_row)
        srow.setContentsMargins(0, 0, 0, 0)
        self.size = QtGui.QComboBox()
        self.length = QtGui.QComboBox()
        self.size.currentIndexChanged.connect(lambda _i: self.show_hole())
        srow.addWidget(QtGui.QLabel("Size"))
        srow.addWidget(self.size)
        self.length_label = QtGui.QLabel("Length")
        srow.addWidget(self.length_label)
        srow.addWidget(self.length)
        srow.addStretch(1)
        lay.addWidget(self.std_row)
        self.std_row.hide()

        buttons = QtGui.QGridLayout()
        for i, (label, fn, tip) in enumerate((
                ("Place in model", self.place, "Add the selected part to the shared model (you hold it), then position it"),
                ("Preview in 3D", self.preview, "Open the part in its own preview window"),
                ("Upload a part…", self.upload, "Add a STEP, FreeCAD or STL file from this computer to the team library"),
                ("Add from a link…", self.from_link, "Add a part file from a web link (vendor CAD download, GitHub…)"),
                ("Which part should I use?", self.recommend, "Describe what the part must do; get suggestions"))):
            b = QtGui.QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            buttons.addWidget(b, i // 2, i % 2)
        more = QtGui.QToolButton()
        more.setText("More ▾")
        more.setPopupMode(QtGui.QToolButton.InstantPopup)
        menu = QtGui.QMenu(more)
        for label, fn in (("Edit the part's details…", self.edit_details),
                          ("Remove from the library…", self.remove), (None, None),
                          ("Export the library as a pack…", self.export_pack),
                          ("Import a pack…", self.import_pack), (None, None),
                          ("Precision settings…", show_settings)):
            if label is None:
                menu.addSeparator()
            else:
                menu.addAction(label).triggered.connect(lambda _=False, f=fn: f())
        more.setMenu(menu)
        buttons.addWidget(more, 2, 1)
        lay.addLayout(buttons)
        self.loaded = False
        self.lengths = []  # the selected standard family's lengths (empty: it has none)

    # ---------------------------------------------------------------- listing

    def showEvent(self, ev):
        super().showEvent(ev)
        if not self.loaded and self.session.connected:
            self.reload()

    def reload(self):
        if not self.session.connected:
            return
        q, cat = self.query.text().strip(), self.category.currentData()
        try:
            if q:
                found = self.session.client.post("/library/search", {"query": q, "category": cat or None, "limit": 30})
                by_id = {c["id"]: c for c in self.session.client.get("/library")["components"]}
                self.items = [by_id.get(r["id"], r) for r in found["results"]]
            else:
                lib = self.session.client.get("/library", category=cat or None)
                self.items = lib["components"]
        except HubRequestError as e:
            self.details.setText(html.escape(_p().friendly_error(e)))
            return
        self.loaded = True
        current = self.current_id()
        self.tree.clear()
        for c in self.items:
            size = size_label(c.get("bbox")) if c.get("bbox") else (
                f"{len((c.get('std') or {}).get('sizes', []))} sizes" if c.get("std") else "")
            item = QtGui.QTreeWidgetItem(self.tree, [c["name"], SOURCE_WORDS.get(c.get("source"), ""), size])
            item.setData(0, QtCore.Qt.UserRole, c["id"])
            if c["id"] == current:
                self.tree.setCurrentItem(item)
        if not self.items:
            QtGui.QTreeWidgetItem(self.tree, ["(nothing matches: try another word, or Upload a part)", "", ""])

    def current_id(self):
        item = self.tree.currentItem()
        return item.data(0, QtCore.Qt.UserRole) if item else None

    def current(self):
        cid = self.current_id()
        return next((c for c in self.items if c["id"] == cid), None)

    def selected(self):
        c = self.current()
        if not c:
            self.std_row.hide()
            return
        self.details.setText(describe(c))
        std = c.get("std") or {}
        if std.get("family"):
            self.size.blockSignals(True)
            self.size.clear()
            self.size.addItems(list(std.get("sizes") or []))
            if "M3" in (std.get("sizes") or []):
                self.size.setCurrentText("M3")
            elif "608" in (std.get("sizes") or []):
                self.size.setCurrentText("608")
            self.size.blockSignals(False)
            self.length.clear()
            lengths = self.lengths = std.get("lengths") or []
            self.length.addItems([f"{x:g}" for x in lengths])
            if 10 in lengths:
                self.length.setCurrentText("10")
            self.length.setVisible(bool(lengths))
            self.length_label.setVisible(bool(lengths))
            self.std_row.show()
            self.show_hole()
        else:
            self.std_row.hide()

    def show_hole(self):
        c = self.current()
        if not c or not (c.get("std") or {}).get("family") or not self.size.currentText():
            return
        try:
            h = self.session.client.post("/library/hole", {"family": c["std"]["family"], "size": self.size.currentText()})
        except HubRequestError:
            return
        depth = f", {h['depth']:g} mm deep" if h.get("depth") else ""
        self.details.setText(describe(c) + f"<br><small><b>Hole for it with the team's settings:</b> "
                                           f"Ø{h['diameter']:g} mm{depth} ({html.escape(h['why'])})</small>")

    # ---------------------------------------------------------------- actions

    def place(self):
        c = self.current()
        if not c:
            _p().hint("Pick a part in the list first.")
            return
        body = {"id": c["id"]}
        std = c.get("std") or {}
        if std.get("family"):
            body.update({"family": std["family"], "size": self.size.currentText()})
            if self.lengths and self.length.currentText():
                body["length"] = float(self.length.currentText())
        r = self.session.act("POST", "/library/insert", body, purpose="place a library part")
        if r is None:
            return
        created = (r.get("created") or [])
        _p().say(f"Placed {c['name']} as {', '.join(created) or 'a new part'}")
        if created and _p().confirm(f"Placed it at the model's origin. Position {created[0]} now?"):
            QtCore.QTimer.singleShot(800, lambda: self.edit_new(created[0]))

    def edit_new(self, name):
        P = _p()
        try:
            dlg = P.EditPartDialog(self.session, name)
        except HubRequestError as e:
            _err(e)
            return
        dlg.show()

    def preview(self):
        c = self.current()
        if not c:
            _p().hint("Pick a part in the list first.")
            return
        if c.get("source") == "builtin":
            _p().hint("Standard parts are built when you place them. Place one, then look at it in the model.")
            return
        try:
            data = self.session.client.request("GET", f"/library/{c['id']}/preview", raw=True)
        except HubRequestError:
            _p().hint("FreeCAD hasn't measured this part yet (it takes a few seconds after it's added). Try again soon.")
            return
        import tempfile

        import Mesh
        path = os.path.join(tempfile.gettempdir(), f"cadswarm-preview-{c['id']}.stl")
        with open(path, "wb") as f:
            f.write(data)
        doc = App.listDocuments().get(PREVIEW_DOC) or App.newDocument(PREVIEW_DOC)
        for o in list(doc.Objects):
            doc.removeObject(o.Name)
        feat = doc.addObject("Mesh::Feature", "Preview")
        feat.Mesh = Mesh.Mesh(path)
        feat.Label = c["name"]
        doc.recompute()
        Gui.ActiveDocument = Gui.getDocument(PREVIEW_DOC)
        Gui.SendMsgToActiveView("ViewFit")
        Gui.activeDocument().activeView().viewIsometric()
        _p().say(f"Previewing {c['name']} (window “{PREVIEW_DOC}”; close it any time, nothing is shared from it)")

    def upload(self):
        path, _ = QtGui.QFileDialog.getOpenFileName(self, "Add a part to the team library", "", PART_FILTER)
        if not path:
            return
        dlg = PartDetailsDialog(os.path.splitext(os.path.basename(path))[0])
        if not _exec(dlg):
            return
        body = {"local_path": path, **dlg.values()}
        r = self.session.act("POST", "/library/upload", body, purpose="add a part to the library")
        self.after_add(r)

    def from_link(self):
        dlg = PartDetailsDialog("", with_url=True)
        if not _exec(dlg):
            return
        r = self.session.act("POST", "/library/import-url", dlg.values(), purpose="add a part from a link")
        self.after_add(r)

    def after_add(self, r):
        if r is None:
            return
        if r.get("duplicates") and not r.get("_components"):
            _p().hint(r.get("note") or "That file is already in the library.")
        else:
            names = ", ".join(c["name"] for c in r.get("components", []))
            _p().say(f"Added {names} to the team library")
            suggestions = r.get("suggest") or {}
            if suggestions:
                _p().hint("This part suggests some team settings (fits, hole sizes). Review them in "
                          "Precision settings: AI helpers can suggest more, and you decide.")
        self.reload()

    def recommend(self):
        need, ok = QtGui.QInputDialog.getText(self, "Which part should I use?",
                                              "What must the part do? e.g. “an 8 mm axle that spins”, "
                                              "“screw the lid on with M3”, “hold the lid shut”")
        if not ok or not need.strip():
            return
        try:
            r = self.session.client.post("/library/recommend", {"need": need.strip()})
        except HubRequestError as e:
            _err(e)
            return
        RecommendDialog(self, need.strip(), r).show()

    def edit_details(self):
        c = self.current()
        if not c or c.get("source") == "builtin":
            _p().hint("Pick one of your team's parts first (standard parts can't be edited).")
            return
        dlg = PartDetailsDialog(c["name"], category=c.get("category"), tags=", ".join(c.get("tags") or []),
                                description=c.get("description", ""), license_text=c.get("license", ""))
        if not _exec(dlg):
            return
        if self.session.act("POST", f"/library/{c['id']}/update", {"fields": dlg.values()},
                            "Updated the part's details", purpose="edit a library part") is not None:
            self.reload()

    def remove(self):
        c = self.current()
        if not c or c.get("source") == "builtin":
            _p().hint("Pick one of your team's parts first (standard parts can't be removed).")
            return
        if not _p().confirm(f"Remove {c['name']} from the team library? Copies already placed in the model stay, "
                            "and the file is kept in the host's trash."):
            return
        if self.session.act("POST", f"/library/{c['id']}/delete", {}, f"Removed {c['name']} from the library",
                            purpose="remove a library part") is not None:
            self.reload()

    def export_pack(self):
        name, ok = QtGui.QInputDialog.getText(self, "Export the library", "Name for the pack:", text="Our parts")
        if not ok:
            return
        r = self.session.act("POST", "/library/export-pack", {"name": name or "library"}, purpose="export the library")
        if r is not None:
            _p().hint(f"Saved {r['parts']} part(s) and the team's settings as files/{r['file']}.\n\nGet it from Team "
                      "tools ▸ Project files (Save a copy), then use Import a pack in another project.")

    def import_pack(self):
        path, _ = QtGui.QFileDialog.getOpenFileName(self, "Import a library pack", "", "CADSwarm library packs (*.zip)")
        if not path:
            return
        r = self.session.act("POST", "/library/import-pack", {"local_path": path}, purpose="import a library pack")
        if r is None:
            return
        added = len(r.get("components") or [])
        msg = f"Imported {added} part(s)" + (f"; {len(r['duplicates'])} were already here" if r.get("duplicates") else "")
        if r.get("pack_settings"):
            msg += ".\n\nThe pack also carries settings from its project. They were not applied: ask a helper to " \
                   "suggest the ones this project should use, or set them in Precision settings."
        _p().hint(msg)
        self.reload()


class PartDetailsDialog(QtGui.QDialog):
    """Name, kind, tags, description and license for a part being added (and its link, for a download)."""

    def __init__(self, name, with_url=False, category="other", tags="", description="", license_text=""):
        super().__init__(Gui.getMainWindow())
        theme.apply(self)
        self.setWindowTitle("Add a link to the library" if with_url else "Part details")
        form = QtGui.QFormLayout(self)
        self.url = None
        if with_url:
            self.url = QtGui.QLineEdit()
            self.url.setPlaceholderText("https://… link straight to a .step, .stp, .stl or .FCStd file")
            form.addRow("Link", self.url)
        self.name = QtGui.QLineEdit(name)
        self.category = QtGui.QComboBox()
        for c in CATEGORIES:
            self.category.addItem(c.capitalize(), c)
        self.category.setCurrentIndex(max(0, list(CATEGORIES).index(category) if category in CATEGORIES else 0))
        self.tags = QtGui.QLineEdit(tags)
        self.tags.setPlaceholderText("words people would search for, separated by commas")
        self.description = QtGui.QLineEdit(description)
        self.license = QtGui.QLineEdit(license_text)
        self.license.setPlaceholderText("e.g. CC-BY-4.0, vendor terms, our own design")
        form.addRow("Name", self.name)
        form.addRow("Kind", self.category)
        form.addRow("Tags", self.tags)
        form.addRow("What it is", self.description)
        form.addRow("License", self.license)
        row = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel)
        row.accepted.connect(self.check)
        row.rejected.connect(self.reject)
        form.addRow(row)

    def check(self):
        if self.url is not None and not self.url.text().strip().startswith("https://"):
            _p().hint("Paste an https:// link to the part file itself.")
            return
        self.accept()

    def values(self):
        out = {"name": self.name.text().strip(), "category": self.category.currentData(),
               "tags": [t.strip() for t in self.tags.text().split(",") if t.strip()],
               "description": self.description.text().strip(), "license": self.license.text().strip()}
        if self.url is not None:
            out["url"] = self.url.text().strip()
        return out


class RecommendDialog(QtGui.QDialog):
    """Suggested parts for a need, each with why, the size and the hole it needs; place one, or hand it to a helper."""

    def __init__(self, tab, need, result):
        super().__init__(Gui.getMainWindow())
        theme.apply(self)
        self.tab, self.need = tab, need
        self.recs = result.get("recommendations") or []
        self.setWindowTitle("Parts for: " + need[:60])
        self.resize(560, 420)
        lay = QtGui.QVBoxLayout(self)
        self.list = QtGui.QListWidget()
        for r in self.recs:
            hole = r.get("hole") or {}
            text = (r.get("label") or r.get("name")) + f"  ·  {r.get('why')}"
            if hole.get("diameter"):
                text += f"\n    hole for it: Ø{hole['diameter']:g} mm ({hole.get('why')})"
            for s in r.get("suggest") or []:
                text += f"\n    suggests {s['setting']} → {s['value']}: {s['reason']}"
            self.list.addItem(text)
        if not self.recs:
            self.list.addItem(result.get("note") or "Nothing in the library fits.")
        self.list.setWordWrap(True)
        lay.addWidget(self.list, 1)
        row = QtGui.QHBoxLayout()
        place = QtGui.QPushButton("Place the selected part")
        place.clicked.connect(self.place)
        ask = QtGui.QPushButton("Ask a helper to do it")
        ask.setToolTip("Hand this to an AI helper: it picks the part, sizes the holes and places it")
        ask.clicked.connect(self.ask)
        close = QtGui.QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(place)
        row.addWidget(ask)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)

    def place(self):
        i = self.list.currentRow()
        if i < 0 or i >= len(self.recs):
            _p().hint("Click a suggestion first.")
            return
        r = self.recs[i]
        body = {"id": r["id"]}
        if r.get("family"):
            body.update({"family": r["family"], "size": r["size"]})
            if r.get("length"):
                body["length"] = r["length"]
        if self.tab.session.act("POST", "/library/insert", body, purpose="place a library part") is not None:
            _p().say(f"Placed {r.get('label') or r.get('name')}")
            self.accept()

    def ask(self):
        P = _p()
        panel = P.SESSION.panel
        if panel is None:
            return
        # "@" then the instruction, cursor after the "@": picking a helper from the list completes it.
        panel.msg.setText(f"@Find the best part in our components library for: {self.need}. Use "
                          "recommend_components, tell me which one you chose and why, size the holes in the mating "
                          "part with hole_size, place it, and suggest any team setting it needs.")
        panel.msg.setFocus()
        panel.msg.setCursorPosition(1)
        panel.maybe_complete()
        self.accept()


class SettingsDialog(QtGui.QDialog):
    """The team's precision settings, grouped, with what AI helpers suggested and why."""

    def __init__(self):
        super().__init__(Gui.getMainWindow())
        theme.apply(self)
        self.setWindowTitle("CADSwarm: precision settings")
        self.resize(640, 640)
        self.widgets = {}
        self.specs = {}
        lay = QtGui.QVBoxLayout(self)
        intro = QtGui.QLabel("These apply to the whole team. Fits are also shared sizes (Params.tol_clearance…), "
                             "so parts built with them update when you change them. AI helpers can only "
                             "<i>suggest</i> changes; you decide below.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.sugg_box = QtGui.QGroupBox("Suggestions waiting for you")
        self.sugg_lay = QtGui.QVBoxLayout(self.sugg_box)
        lay.addWidget(self.sugg_box)
        scroll = QtGui.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtGui.QWidget()
        self.form_lay = QtGui.QVBoxLayout(inner)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        row = QtGui.QHBoxLayout()
        save = QtGui.QPushButton("Save changes")
        save.setStyleSheet("QPushButton { font-weight: bold; padding: 5px 12px; }")
        save.clicked.connect(self.save)
        close = QtGui.QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(save)
        row.addWidget(close)
        lay.addLayout(row)
        self.load()

    def load(self):
        try:
            data = _client().get("/settings")
        except HubRequestError as e:
            _err(e)
            return
        self.data = data
        while self.form_lay.count():
            w = self.form_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        groups = {}
        for s in data["settings"]:
            self.specs[s["key"]] = s
            if s["group"] not in groups:
                box = QtGui.QGroupBox(s["group"])
                groups[s["group"]] = QtGui.QFormLayout(box)
                self.form_lay.addWidget(box)
            if s["kind"] == "choice":
                w = QtGui.QComboBox()
                for c in s["choices"]:
                    w.addItem(c.replace("_", " "), c)
                w.setCurrentIndex(max(0, s["choices"].index(s["value"]) if s["value"] in s["choices"] else 0))
            else:
                w = QtGui.QDoubleSpinBox()
                w.setDecimals(decimals_for(s.get("step")))
                w.setRange(s["min"], s["max"])
                w.setSingleStep(s.get("step") or 0.1)
                w.setValue(float(s["value"]))
                if s.get("unit"):
                    w.setSuffix(f" {s['unit']}")
            w.setToolTip(s.get("help", ""))
            label = QtGui.QLabel(s["label"] + ("" if s["is_default"] else
                                               f"  <small style='color:#8b949e'>(set by {html.escape(s['changed_by'] or '?')})</small>"))
            label.setTextFormat(QtCore.Qt.RichText)
            label.setToolTip(s.get("help", ""))
            label.setWordWrap(True)
            cell = QtGui.QWidget()
            cl = QtGui.QHBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.addWidget(w, 1)
            if not s["is_default"]:
                reset = QtGui.QPushButton("Default")
                reset.setToolTip(f"Back to {setting_value_text(s, s['default'])}")
                reset.clicked.connect(lambda _=False, k=s["key"]: self.reset(k))
                cl.addWidget(reset)
            groups[s["group"]].addRow(label, cell)
            self.widgets[s["key"]] = w
        self.form_lay.addStretch(1)
        self.load_suggestions(data.get("suggestions") or [])

    def load_suggestions(self, suggestions):
        while self.sugg_lay.count():
            w = self.sugg_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.sugg_box.setVisible(bool(suggestions))
        for g in suggestions:
            spec = self.specs.get(g["setting"], {})
            row = QtGui.QWidget()
            rl = QtGui.QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            text = QtGui.QLabel(
                f"<b>{html.escape(g['label'])}</b>: {html.escape(setting_value_text(spec, g['current']))} → "
                f"<b>{html.escape(setting_value_text(spec, g['value']))}</b><br><small>{html.escape(g['reason'])} "
                f"by {html.escape(g['by'])}" + (f", for {html.escape(g['component_name'])}" if g.get("component") else "")
                + "</small>")
            text.setWordWrap(True)
            text.setTextFormat(QtCore.Qt.RichText)
            ok = QtGui.QPushButton("Accept")
            no = QtGui.QPushButton("Dismiss")
            ok.clicked.connect(lambda _=False, i=g["id"]: self.resolve(i, True))
            no.clicked.connect(lambda _=False, i=g["id"]: self.resolve(i, False))
            rl.addWidget(text, 1)
            rl.addWidget(ok)
            rl.addWidget(no)
            self.sugg_lay.addWidget(row)

    def value_of(self, key):
        w = self.widgets[key]
        return w.currentData() if isinstance(w, QtGui.QComboBox) else round(w.value(), 6)

    def save(self):
        changed = [(k, self.value_of(k)) for k, s in self.specs.items() if self.value_of(k) != s["value"]]
        if not changed:
            _p().hint("Nothing changed.")
            return
        for key, value in changed:
            if _p().SESSION.act("POST", "/settings", {"setting": key, "value": value},
                                purpose="change a team setting") is None:
                break
        else:
            _p().say("Saved team settings: " + ", ".join(f"{k}={v}" for k, v in changed))
        self.load()

    def reset(self, key):
        if _p().SESSION.act("POST", "/settings/reset", {"setting": key}, purpose="reset a team setting") is not None:
            self.load()

    def resolve(self, sid, accept):
        if _p().SESSION.act("POST", f"/settings/suggestions/{sid}", {"accept": accept},
                            "Accepted the suggestion" if accept else "Dismissed the suggestion",
                            purpose="answer a settings suggestion") is not None:
            self.load()


def show_settings():
    if not _p().SESSION.ensure_connected("see the team's settings"):
        return
    _exec(SettingsDialog())


def show_library():
    P = _p()
    P.show_panel()
    panel = P.SESSION.panel
    if panel is not None and getattr(panel, "library", None) is not None:
        panel.show_tab(panel.library)
        panel.library.query.setFocus()


def editor_precision(session):
    """(decimals, step) for the part editor from the team's settings; the old 2 decimals if unreadable."""
    try:
        vals = {s["key"]: s["value"] for s in session.client.get("/settings")["settings"]}
        return int(vals.get("edit_decimals", 2)), float(vals.get("edit_step", 0.1))
    except Exception:
        return 2, 0.1

