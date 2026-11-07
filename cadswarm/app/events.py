"""FreeCAD document and selection observers re-emitted as Qt signals, on the GUI thread, coalesced.

Packages connect to ``ctx.signals``:
  doc_changed(str doc_name)        any object added/removed/changed/recomputed in a document
  doc_opened(str) / doc_closed(str) / active_doc(str)
  selection_changed()              Gui.Selection changed (read it with Gui.Selection.getSelectionEx())
  preselection(str text)           what is under the cursor ("Pad.Face6")
  env_changed(str env)             part / sketch / assembly / drawing
  edit_changed(bool editing)       a task dialog / edit mode opened or closed
  theme_changed(str mode)          "light" / "dark"
  recomputed(str doc_name)
  team_changed() / build_changed()   multiplayer state / AI build progress changed
"""
from PySide6 import QtCore


class Signals(QtCore.QObject):
    doc_changed = QtCore.Signal(str)
    doc_opened = QtCore.Signal(str)
    doc_closed = QtCore.Signal(str)
    active_doc = QtCore.Signal(str)
    selection_changed = QtCore.Signal()
    preselection = QtCore.Signal(str)
    env_changed = QtCore.Signal(str)
    edit_changed = QtCore.Signal(bool)
    theme_changed = QtCore.Signal(str)
    recomputed = QtCore.Signal(str)
    ribbon_changed = QtCore.Signal()
    team_changed = QtCore.Signal()
    build_changed = QtCore.Signal()


class _DocObserver:
    def __init__(self, bridge):
        self.b = bridge

    def slotCreatedDocument(self, doc):
        self.b.post("doc_opened", doc.Name)

    def slotDeletedDocument(self, doc):
        self.b.post("doc_closed", doc.Name)

    def slotActivateDocument(self, doc):
        self.b.post("active_doc", doc.Name)

    def slotCreatedObject(self, obj):
        self.b.touch(obj)

    def slotDeletedObject(self, obj):
        self.b.touch(obj)

    def slotChangedObject(self, obj, prop):
        self.b.touch(obj)

    def slotRecomputedDocument(self, doc):
        self.b.post("recomputed", doc.Name)
        self.b.post_doc(doc.Name)


class _SelObserver:
    def __init__(self, bridge):
        self.b = bridge

    def addSelection(self, doc, obj, sub, pnt):
        self.b.sel()

    def removeSelection(self, doc, obj, sub):
        self.b.sel()

    def setSelection(self, doc):
        self.b.sel()

    def clearSelection(self, doc):
        self.b.sel()

    def setPreselection(self, doc, obj, sub):
        self.b.post("preselection", f"{obj}.{sub}" if sub else obj)

    def removePreselection(self, doc, obj, sub):
        self.b.post("preselection", "")


class Bridge(QtCore.QObject):
    """Collects observer callbacks and emits at most one doc_changed per document per 60 ms."""

    _relay = QtCore.Signal(str, object)

    def __init__(self, App, Gui, signals):
        super().__init__()
        self._relay.connect(self._emit, QtCore.Qt.ConnectionType.QueuedConnection)
        self.App, self.Gui, self.signals = App, Gui, signals
        self._dirty_docs = set()
        self._sel_dirty = False
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._flush)
        self._doc_obs = _DocObserver(self)
        self._sel_obs = _SelObserver(self)
        App.addDocumentObserver(self._doc_obs)
        Gui.Selection.addObserver(self._sel_obs)
        self._editing = False
        self._poll = QtCore.QTimer(self)  # edit mode has no observer slot: poll it cheaply
        self._poll.setInterval(250)
        self._poll.timeout.connect(self._check_edit)
        self._poll.start()

    def post(self, name, *args):
        self._relay.emit(name, list(args))

    def _emit(self, name, args):
        getattr(self.signals, name).emit(*args)

    def touch(self, obj):
        try:
            self.post_doc(obj.Document.Name)
        except Exception:  # noqa: BLE001 - object already gone
            pass

    def post_doc(self, name):
        self._dirty_docs.add(name)
        if not self._timer.isActive():
            self._timer.start()

    def sel(self):
        self._sel_dirty = True
        if not self._timer.isActive():
            self._timer.start()

    def _flush(self):
        docs, self._dirty_docs = self._dirty_docs, set()
        for d in docs:
            self.signals.doc_changed.emit(d)
        if self._sel_dirty:
            self._sel_dirty = False
            self.signals.selection_changed.emit()

    def _check_edit(self):
        try:
            gd = self.Gui.ActiveDocument
            editing = bool(gd and gd.getInEdit()) or bool(self.Gui.Control.activeDialog())
        except Exception:  # noqa: BLE001
            editing = False
        if editing != self._editing:
            self._editing = editing
            self.signals.edit_changed.emit(editing)

    def close(self):
        try:
            self.App.removeDocumentObserver(self._doc_obs)
            self.Gui.Selection.removeObserver(self._sel_obs)
        except Exception:  # noqa: BLE001
            pass
