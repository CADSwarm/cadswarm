"""Two Team panel tabs that make the shared sheet and the project folder visible.

* Shared sizes: the Params sheet every part reads from, with the value of each size and a
  one-click way to change it (the same `set_param` op the Team tab's button sends).
* Project files: the team's notes, plans and scripts, newest version and full history for
  each file, plus "Save a copy" for the file you select.

This module also draws the refusal card used by the chat and Activity feed: a change the
team refused because somebody else holds the part is shown as a card that says who holds
it and that nothing changed, rather than as an ordinary line of chat.

Qt runs on FreeCAD's GUI thread, like library_ui.py: browsing uses the same blocking client
calls the dialogs use (they are quick), and changing a size goes through session.run_async.
"""
import html
import os
import re
import time
from urllib.parse import urlencode

import FreeCADGui as Gui
from PySide import QtCore, QtGui

from ..client import HubRequestError


def _p():
    from . import panel  # panel imports this module; import it back lazily
    return panel


def _err(e):
    return _p().friendly_error(e)


def _exec(dlg):
    return (getattr(dlg, "exec", None) or dlg.exec_)()


class _ValueEditDelegate(QtGui.QStyledItemDelegate):
    """Edits the Value column in place; the size's name stays a label."""

    def createEditor(self, parent, option, index):
        if index.column() != 1:
            return None
        return super().createEditor(parent, option, index)


# ------------------------------------------------------------------ refusal card

# What an agent's message or an op error says when a lock stopped it. Deliberately broad:
# the helpers phrase it a few ways, and a card is always the clearer presentation.
REFUSAL_WORDS = ("refused", "not_locked", "lock_violation", "already locked", "current holders")
HOLDER_RE = ("Current holders:", "held by ", "already locked: ", "locked by ")


def is_refusal(text):
    low = (text or "").lower()
    if any(word in low for word in REFUSAL_WORDS):
        return True
    # "held by ann-claude" is a refusal; "a lid held by four inserts" is design talk.
    return bool(re.search(r"\bheld by\s+[\w.-]+\s*(?:\([^)]*\)|$)", low))


def _holder_name(tail):
    """The name alone: "Base by ann (active)." and "ann (active)" both give "ann"."""
    tail = tail.splitlines()[0]
    low = tail.lower()
    if " by " in low:
        tail = tail[low.find(" by ") + 4:]
    return re.split(r"[,.(]", tail, 1)[0].strip().strip("'\"")


def parse_refusal(text):
    """Pull the holder out of whatever the agent or op error wrote, so the card can name them."""
    text = (text or "").strip()
    holder = ""
    low = text.lower()
    for marker in HOLDER_RE:
        i = low.find(marker.lower())
        if i >= 0:
            holder = _holder_name(text[i + len(marker):].strip())
            break
    title = "Refused"
    if holder:
        title += f" · held by {holder}"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    detail = " ".join(lines[1:]) if len(lines) > 1 else (lines[0] if lines else "")
    # The lock statement becomes the card's title; keep only the explanation under it.
    detail = re.sub(r"^(?:refused|locked|already locked|not_locked|lock_violation)\b\s*[:·—-]?\s*", "",
                    detail, flags=re.IGNORECASE)
    if holder:
        h = re.escape(holder)
        detail = re.sub(rf"^(?:[^\n]*?\bby\s+)?{h}\b\s*(?:\([^)]*\))?\s*[.·:,—-]?\s*", "", detail,
                        flags=re.IGNORECASE)
        detail = re.sub(rf"\b(?:current holders?|holders?)\s*:\s*{h}(?:\s*\([^)]*\))?\s*[.·:,—-]?\s*", "",
                        detail, flags=re.IGNORECASE)
    detail = re.sub(r"^message the holder\b\s*[.·:,—-]?\s*", "", detail, flags=re.IGNORECASE)
    detail = re.sub(r"\s{2,}", " ", detail).strip()
    if detail.lower().startswith("lock ") or "current holders" in detail.lower():
        detail = ""
    if "nothing changed" not in detail.lower():
        detail = (detail + " " if detail else "") + "Nothing changed. Ask them instead."
    return title, detail, holder


def refusal_html(stamp, who, colour, body, status="", op=""):
    """A red-bordered card, like the refusal card on the site: who, what was refused, and what to do."""
    title, detail, holder = parse_refusal(body)
    what = f"<span style='color:#8b949e'>{html.escape(op)}</span><br>" if op else ""
    return (f"<table width='100%' cellspacing='0' cellpadding='8' style='margin:0 0 8px 0;"
            f" border:1px solid #f0a29d; background:#2a1414'>"
            f"<tr><td><span style='color:#8b949e'>{html.escape(stamp)}</span> "
            f"<b style='color:{colour}'>{who}</b>{status}<br>{what}"
            f"<b style='color:#ff7b72'>{html.escape(title)}</b><br>"
            f"<span style='color:#b9b5af'>{html.escape(detail)}</span>"
            + (f"<br><span style='color:#8b949e'>Message {html.escape(holder)} if you need it changed.</span>"
               if holder else "")
            + "</td></tr></table>")


# ------------------------------------------------------------------ shared sizes

def pretty_alias(alias):
    return str(alias).replace("_", " ")


def value_text(value):
    """A Params value the way the sheet shows it: 120 mm for numbers, plain words otherwise."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:g} mm"
    text = str(value).strip()
    return text or "—"


class SharedSizesTab(QtGui.QWidget):
    """One sheet every part reads from: each size, its value, and a change that reaches the team."""

    def __init__(self, session):
        super().__init__()
        self.session = session
        self.key = None
        self.last_change = ""
        self.shown = {}
        self.filling = False
        lay = _p().page_layout(self)

        intro = QtGui.QFrame()
        intro.setObjectName("card")
        il = QtGui.QVBoxLayout(intro)
        il.setContentsMargins(12, 10, 12, 10)
        il.setSpacing(4)
        self.title = QtGui.QLabel("One sheet, read by every part")
        self.title.setObjectName("title")
        self.title.setWordWrap(True)
        il.addWidget(self.title)
        self.blurb = QtGui.QLabel(
            "Change a size here once and every part that reads it updates for the whole team. "
            "The helper that owns those parts is told, so it can check the fit.")
        self.blurb.setWordWrap(True)
        self.blurb.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        il.addWidget(self.blurb)
        self.holder = QtGui.QLabel("")
        self.holder.setWordWrap(True)
        il.addWidget(self.holder)
        lay.addWidget(intro)

        head = QtGui.QHBoxLayout()
        head.addWidget(_p().caption("Shared sizes"), 1)
        self.count = QtGui.QLabel("")
        self.count.setStyleSheet("color:#8b949e;")
        head.addWidget(self.count)
        lay.addLayout(head)

        self.rows = QtGui.QTreeWidget()
        self.rows.setObjectName("list")
        self.rows.setHeaderLabels(["Size", "Value"])
        self.rows.setRootIsDecorated(False)
        self.rows.setAlternatingRowColors(False)
        self.rows.setSelectionMode(QtGui.QAbstractItemView.SingleSelection)
        self.rows.setMinimumHeight(150)
        h = self.rows.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QtGui.QHeaderView.Stretch)
        h.setSectionResizeMode(1, QtGui.QHeaderView.Interactive)
        self.rows.setColumnWidth(1, 110)
        # The value edits in place, like the site's size fields: one double-click, type, Enter.
        self.rows.setEditTriggers(QtGui.QAbstractItemView.DoubleClicked
                                  | QtGui.QAbstractItemView.SelectedClicked
                                  | QtGui.QAbstractItemView.EditKeyPressed)
        self.rows.setItemDelegate(_ValueEditDelegate(self.rows))
        self.rows.itemChanged.connect(self.value_edited)
        self.rows.setToolTip("Double-click a value to edit it. Every part that reads it updates.")
        lay.addWidget(self.rows, 1)

        buttons = QtGui.QGridLayout()
        buttons.setSpacing(6)
        buttons.addWidget(_p().action_button("Change a shared size…", "Change a size every part reads from",
                                            self.change_any), 0, 0)
        buttons.addWidget(_p().action_button("Change selected size…", "Change the highlighted size",
                                            self.change_selected), 0, 1)
        lay.addLayout(buttons)

        self.note = QtGui.QLabel("")
        self.note.setWordWrap(True)
        self.note.setTextFormat(QtCore.Qt.RichText)
        self.note.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        lay.addWidget(self.note)
        self.empty = QtGui.QLabel("No shared sizes yet. They appear as soon as a build starts.")
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color:#8b949e;")
        lay.addWidget(self.empty)
        lay.addStretch(1)

    # ---------------------------------------------------------------- render

    def render(self, s):
        params = s.model.get("params") or {}
        holder = next((p["name"] for p in s.model.get("participants", [])
                       if "Params" in (p.get("locks") or [])), "")
        key = (tuple(sorted((str(k), value_text(v)) for k, v in params.items())), holder)
        if key == self.key:
            return
        self.key = key
        self.filling = True
        try:
            self.rows.clear()
            self.shown = {}
            for alias, value in sorted(params.items()):
                item = QtGui.QTreeWidgetItem([pretty_alias(alias).capitalize(), value_text(value)])
                item.setData(0, QtCore.Qt.UserRole, alias)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
                item.setToolTip(0, f"Params.{alias}")
                item.setToolTip(1, "Double-click to edit this size")
                self.rows.addTopLevelItem(item)
                self.shown[alias] = value_text(value)
        finally:
            self.filling = False
        self.empty.setVisible(not params)
        self.rows.setVisible(bool(params))
        self.count.setText(f"{len(params)} size{'s' if len(params) != 1 else ''}" if params else "")
        if holder:
            self.holder.setText(f"<span style='color:#8b949e'>Params are held by "
                                f"<b>{html.escape(holder)}</b>. You can still change a size: CadSwarm "
                                "asks them to let go for a moment.</span>")
        else:
            self.holder.setText("<span style='color:#8b949e'>Nobody holds the Params sheet right now, "
                                "so your change can go straight through.</span>")

    # ---------------------------------------------------------------- actions

    def selected_alias(self):
        item = self.rows.currentItem()
        return item.data(0, QtCore.Qt.UserRole) if item else None

    def value_edited(self, item, column):
        """A value typed straight into the tree, the same change the site's field sends."""
        if self.filling or column != 1:
            return
        alias = item.data(0, QtCore.Qt.UserRole)
        if not alias:
            return
        before = self.shown.get(alias, "")
        text = item.text(1).strip()
        if text == before:
            return  # a tooltip or colour change, not an edit
        if not text:
            self.restore(item, alias, before)
            return
        self.apply(alias, text, item)

    def restore(self, item, alias, before=None):
        self.filling = True
        try:
            item.setText(1, self.shown.get(alias, "") if before is None else before)
        finally:
            self.filling = False

    def change_any(self):
        if not self.session.ensure_connected("change a shared size"):
            return
        text, ok = QtGui.QInputDialog.getText(self, "Change a shared size",
                                              "name = value   (e.g. hole_pitch = 40 mm)")
        if not ok or "=" not in text:
            return
        alias, value = (part.strip() for part in text.split("=", 1))
        if alias and value:
            self.apply(alias, value)

    def change_selected(self):
        alias = self.selected_alias()
        if not alias:
            _p().hint("Click a size first, then “Change selected size”.")
            return
        if not self.session.ensure_connected("change a shared size"):
            return
        current = next((v for k, v in (self.session.model.get("params") or {}).items() if k == alias), "")
        text, ok = QtGui.QInputDialog.getText(self, f"Change {pretty_alias(alias)}",
                                              f"New value for {alias} (now {value_text(current)}):",
                                              text=value_text(current))
        if ok and text.strip():
            self.apply(alias, text.strip())

    def apply(self, alias, value, item=None):
        before = self.shown.get(alias, "")

        def done(r):
            changed = (r or {}).get("recomputed") or 0
            self.shown[alias] = value_text(value)
            self.last_change = (f"<span style='color:#4fb35a'>✓</span> <b>{html.escape(pretty_alias(alias))}</b> "
                                f"is now <b>{html.escape(value)}</b>"
                                + (f" · {changed} part{'s' if changed != 1 else ''} updated" if changed else ""))
            self.note.setText(self.last_change)
            _p().say(f"{pretty_alias(alias)} is now {value}")

        def failed():
            if item is not None:
                try:
                    self.restore(item, alias, before)
                except RuntimeError:  # the row was rebuilt while the request was away
                    pass
            self.note.setText(f"<span style='color:#ff7b72'>Couldn't change "
                              f"{html.escape(pretty_alias(alias))}. The sheet is still "
                              f"{html.escape(before or 'unchanged')}.</span>")

        change_param(self.session, alias, value, done, failed)


def change_param(session, alias, value, done=None, failed=None):
    """Hold Params for one set_param op, then let go (the same sequence the Team tab uses)."""
    def work(c):
        c.post("/locks", {"names": ["Params"]})
        try:
            return c.post("/ops", {"op": "set_param", "args": {"alias": alias, "value": value}})
        finally:
            c.post("/locks/release", {"names": ["Params"]})
    session.run_async(work, done=done, failed=failed, purpose="change a shared size")


# ------------------------------------------------------------------ project files

def _when(ts):
    try:
        return time.strftime("%b %d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return ""


def _size(n):
    """A file size the way the site writes it: 920 B, 1.8 KB, 4 MB."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    for unit, scale in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= scale:
            value = n / scale
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
    return f"{int(n)} B"


def _folder_icon():
    return "📁"


def _file_icon(path):
    ext = os.path.splitext(path)[1].lower()
    return {".md": "📝", ".txt": "📝", ".csv": "📊", ".json": "🧾", ".step": "📦", ".stp": "📦",
            ".stl": "📦", ".fcstd": "📐", ".py": "🐍", ".png": "🖼", ".jpg": "🖼", ".pdf": "📄"}.get(ext, "📄")


class FilesTab(QtGui.QWidget):
    """The team's project folder: every saved file, its version and who saved it, plus history."""

    def __init__(self, session):
        super().__init__()
        self.session = session
        self.listing = None
        self.loading = False
        self.loaded_at = 0.0
        self.history_path = ""
        self.history_loading = False
        lay = _p().page_layout(self)

        intro = QtGui.QFrame()
        intro.setObjectName("card")
        il = QtGui.QVBoxLayout(intro)
        il.setContentsMargins(12, 10, 12, 10)
        il.setSpacing(4)
        title = QtGui.QLabel("One project folder, nothing lost")
        title.setObjectName("title")
        title.setWordWrap(True)
        il.addWidget(title)
        self.blurb = QtGui.QLabel(
            "Notes, plans and scripts your team's AI helpers save. Agents can't write outside this "
            "folder, and every replaced file keeps its old versions so you can look back.")
        self.blurb.setWordWrap(True)
        self.blurb.setSizePolicy(QtGui.QSizePolicy.Ignored, QtGui.QSizePolicy.Preferred)
        il.addWidget(self.blurb)
        self.status = QtGui.QLabel("Open this tab to load the team's files.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#8b949e;")
        il.addWidget(self.status)
        lay.addWidget(intro)

        head = QtGui.QHBoxLayout()
        head.addWidget(_p().caption("Project files"), 1)
        self.refresh_button = QtGui.QPushButton("Refresh")
        self.refresh_button.setObjectName("quiet")
        self.refresh_button.setToolTip("Read the project folder again")
        self.refresh_button.clicked.connect(lambda: self.reload(force=True))
        head.addWidget(self.refresh_button)
        lay.addLayout(head)

        self.tree = QtGui.QTreeWidget()
        self.tree.setObjectName("list")
        self.tree.setHeaderLabels(["Name", "Version", "Size", "Saved by", "When"])
        self.tree.setRootIsDecorated(True)
        self.tree.setMinimumHeight(190)
        th = self.tree.header()
        th.setStretchLastSection(False)
        th.setSectionResizeMode(0, QtGui.QHeaderView.Stretch)
        th.setSectionResizeMode(1, QtGui.QHeaderView.Interactive)
        th.setSectionResizeMode(2, QtGui.QHeaderView.Interactive)
        th.setSectionResizeMode(3, QtGui.QHeaderView.Interactive)
        th.setSectionResizeMode(4, QtGui.QHeaderView.Interactive)
        self.tree.setColumnWidth(1, 52)
        self.tree.setColumnWidth(2, 64)
        self.tree.setColumnWidth(3, 88)
        self.tree.setColumnWidth(4, 96)
        self.tree.currentItemChanged.connect(lambda *_: self.selected())
        self.tree.itemDoubleClicked.connect(lambda *_: self.save_copy())
        self.tree.setToolTip("Click a file to see its history. Double-click to save a copy.")
        lay.addWidget(self.tree, 2)

        self.history_caption = _p().caption("History")
        lay.addWidget(self.history_caption)
        self.history = QtGui.QTreeWidget()
        self.history.setObjectName("list")
        self.history.setHeaderLabels(["Version", "Saved by", "When", "Why"])
        self.history.setRootIsDecorated(False)
        self.history.setMinimumHeight(120)
        hh = self.history.header()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QtGui.QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QtGui.QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QtGui.QHeaderView.Interactive)
        hh.setSectionResizeMode(3, QtGui.QHeaderView.Stretch)
        self.history.setColumnWidth(0, 62)
        self.history.setColumnWidth(1, 104)
        self.history.setColumnWidth(2, 104)
        self.history.setToolTip("Every replaced version is kept. Pick a file above to see its history.")
        lay.addWidget(self.history, 1)
        self.history.hide()
        self.history_caption.hide()

        buttons = QtGui.QGridLayout()
        buttons.setSpacing(6)
        buttons.addWidget(_p().action_button("Save a copy…", "Save the selected file to this computer",
                                            self.save_copy), 0, 0)
        self.folder_button = _p().action_button("Open project folder",
                                                "Open the folder the team saves into", self.open_folder)
        buttons.addWidget(self.folder_button, 0, 1)
        lay.addLayout(buttons)
        lay.addStretch(1)

    # ---------------------------------------------------------------- loading

    def showEvent(self, ev):
        super().showEvent(ev)
        self.refresh_if_stale()

    def refresh_if_stale(self):
        if not self.session.connected:
            self.status.setText("Join a team to see its shared project files.")
            return
        if time.time() - self.loaded_at > 20 or self.listing is None:
            self.reload()

    def reload(self, force=False):
        if not self.session.connected or self.loading:
            return
        if not force and self.listing is not None and time.time() - self.loaded_at <= 5:
            return
        self.loading = True
        self.refresh_button.setEnabled(False)
        try:
            self.listing = self.session.client.get("/project/files")
            self.loaded_at = time.time()
        except HubRequestError as e:
            self.status.setText(html.escape(_err(e)))
            return
        except Exception as e:  # a pane must never take the panel down
            self.status.setText(html.escape(str(e)))
            return
        finally:
            self.loading = False
            self.refresh_button.setEnabled(True)
        self.render_listing()

    def render_listing(self):
        listing = self.listing or {}
        files = listing.get("files") or []
        folders = listing.get("folders") or []
        keep = self.selected_path()
        self.tree.clear()
        nodes = {"": self.tree.invisibleRootItem()}

        def folder_node(path):
            if path in nodes:
                return nodes[path]
            parent, _, name = path.rpartition("/")
            item = QtGui.QTreeWidgetItem(folder_node(parent), [f"{_folder_icon()} {name}", "", "", "", ""])
            item.setExpanded(True)
            item.setData(0, QtCore.Qt.UserRole, None)
            nodes[path] = item
            return item

        for folder in sorted(folders):
            folder_node(folder)
        for f in sorted(files, key=lambda f: f.get("path", "").lower()):
            path = f.get("path", "")
            parent, _, name = path.rpartition("/")
            item = QtGui.QTreeWidgetItem(folder_node(parent),
                                         [f"{_file_icon(path)} {name}", str(f.get("version", "")),
                                          _size(f.get("size")), f.get("author") or "",
                                          _when(f.get("updated_at"))])
            item.setData(0, QtCore.Qt.UserRole, path)
            item.setToolTip(0, path)
            item.setToolTip(1, f"Version {f.get('version')}")
        if not files:
            empty = QtGui.QTreeWidgetItem(["(nothing saved yet)", "", "", "", ""])
            empty.setFlags(QtCore.Qt.NoItemFlags)
            self.tree.addTopLevelItem(empty)
        self.fit_file_columns()
        self.status.setText(f"{len(files)} file{'s' if len(files) != 1 else ''} · last read "
                            f"{time.strftime('%H:%M', time.localtime(self.loaded_at))}"
                            + (" · agents save into this folder from their tools" if files else ""))
        if keep:
            found = self.find_path(keep)
            if found is not None:
                self.tree.setCurrentItem(found)
        self.folder_button.setVisible(bool(self._host_folder()))

    def fit_file_columns(self):
        self.tree.setColumnWidth(1, max(48, self.tree.sizeHintForColumn(1)))
        self.tree.setColumnWidth(2, max(56, self.tree.sizeHintForColumn(2)))
        self.tree.setColumnWidth(3, max(76, min(130, self.tree.sizeHintForColumn(3))))
        self.tree.setColumnWidth(4, max(88, min(130, self.tree.sizeHintForColumn(4))))

    def find_path(self, path):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            if item.data(0, QtCore.Qt.UserRole) == path:
                return item
            stack.extend(item.child(i) for i in range(item.childCount()))
        return None

    def selected_path(self):
        item = self.tree.currentItem()
        if not item or item.isDisabled():
            return ""
        return item.data(0, QtCore.Qt.UserRole) or ""

    # ---------------------------------------------------------------- history

    def selected(self):
        path = self.selected_path()
        if not path:
            self.history.hide()
            self.history_caption.hide()
            return
        if path == self.history_path:
            self.history.show()
            self.history_caption.show()
            return
        self.history_path = path
        if not self.session.connected or self.history_loading:
            return
        self.history_loading = True
        try:
            found = self.session.client.post("/project/files/history", {"path": path})
        except HubRequestError as e:
            self.status.setText(html.escape(_err(e)))
            return
        except Exception as e:
            self.status.setText(html.escape(str(e)))
            return
        finally:
            self.history_loading = False
        if path != self.selected_path():
            return  # the person moved on while it loaded
        self.show_history(found)

    def show_history(self, found):
        path = found.get("path") or ""
        current = found.get("current") or {}
        older = list(found.get("older_versions") or [])
        self.history.clear()
        self.history_caption.setText(f"History · {path}".upper())
        rows = []
        if current:
            rows.append((f"{current.get('version')} ✓", current.get("author") or "", _when(current.get("updated_at")),
                         "current version"))
        for h in sorted(older, key=lambda h: h.get("version") or 0, reverse=True):
            rows.append((str(h.get("version")), h.get("author") or "", _when(h.get("saved_at")),
                         h.get("reason") or "replaced"))
        for version, author, when, why in rows:
            item = QtGui.QTreeWidgetItem([version, author, when, why])
            item.setToolTip(0, f"Version {version}")
            item.setToolTip(3, why)
            self.history.addTopLevelItem(item)
        if not rows:
            item = QtGui.QTreeWidgetItem(["", "", "", "(no saved versions yet)"])
            item.setFlags(QtCore.Qt.NoItemFlags)
            self.history.addTopLevelItem(item)
        self.history.setColumnWidth(0, 62)
        self.history.setColumnWidth(1, max(80, min(130, self.history.sizeHintForColumn(1))))
        self.history.setColumnWidth(2, max(92, min(130, self.history.sizeHintForColumn(2))))
        self.history.show()
        self.history_caption.show()

    # ---------------------------------------------------------------- actions

    def save_copy(self):
        path = self.selected_path()
        if not path:
            _p().hint("Click a file first, then “Save a copy”.")
            return
        dest, _ = QtGui.QFileDialog.getSaveFileName(self, "Save a copy", path.split("/")[-1])
        if not dest:
            return  # the system dialog already asked before replacing an existing file
        try:
            data = self.session.client.request("GET", "/project/files/download?" + urlencode({"path": path}),
                                               raw=True)
        except HubRequestError as e:
            _p().hint(_err(e))
            return
        with open(dest, "wb") as f:
            f.write(data)
        _p().say(f"Saved a copy of {path} to {dest}")

    def _host_folder(self):
        try:
            return _p().hosted_project_folder()
        except Exception:
            return None

    def open_folder(self):
        _p().open_project_folder()

    # ---------------------------------------------------------------- render hook

    def render(self, s):
        if not s.connected:
            self.status.setText("Join a team to see its shared project files.")
            return
        if self.isVisible():
            self.refresh_if_stale()
