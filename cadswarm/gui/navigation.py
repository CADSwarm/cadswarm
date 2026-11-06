"""Left-drag moves the 3D view and right-drag turns it, in every workbench and on top of whichever
navigation style is picked in FreeCAD's preferences (none of FreeCAD's styles has this pair).

A plain click still reaches FreeCAD: a press is held back until the mouse either moves (a drag, which
FreeCAD never sees) or comes back up (the press and release are replayed, so picking a part and the
right-click menu work as before). A left press on a part, a press with Ctrl/Shift/Alt held, and any
left press while a sketch or other object is being edited go straight to FreeCAD, so dragging handles,
box selection and every modifier gesture keep working.
"""
import math

PARAMS_PATH = "User parameter:BaseApp/Preferences/Mod/CadSwarm"  # keep in step with panel.PARAMS_PATH
ORBIT_RAD_PER_PX = math.radians(0.4)  # a drag across a 900 px view turns it all the way round
UP = (0.0, 0.0, 1.0)


# --------------------------------------------------------------------------- camera math (no FreeCAD)

def _qmul(a, b):
    """Hamilton product of (x, y, z, w) quaternions: b first, then a."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def _qaxis(axis, angle):
    x, y, z = axis
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    s = math.sin(angle / 2.0) / n
    return (x * s, y * s, z * s, math.cos(angle / 2.0))


def _normalized(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def rotate(q, v):
    x, y, z, w = q
    return _qmul(_qmul(q, (v[0], v[1], v[2], 0.0)), (-x, -y, -z, w))[:3]


def orbit(orientation, position, focal_distance, dx, dy, rad_per_px=ORBIT_RAD_PER_PX):
    """Turn the camera round its focal point like a turntable: a sideways drag spins the model about
    the world's up axis, an up/down drag tips it about the screen's horizontal. Dragging right turns
    the front of the model to the right; dragging up brings its underside into view.

    `orientation` is the camera's (x, y, z, w) rotation, dx/dy the mouse move in screen pixels
    (y down, as Qt reports it). Returns the new (orientation, position)."""
    look = rotate(orientation, (0.0, 0.0, -1.0))
    focal = tuple(p + d * focal_distance for p, d in zip(position, look))
    right = rotate(orientation, (1.0, 0.0, 0.0))
    turned = _qmul(_qaxis(UP, -dx * rad_per_px), _qmul(_qaxis(right, -dy * rad_per_px), orientation))
    turned = _normalized(turned)
    look = rotate(turned, (0.0, 0.0, -1.0))
    return turned, tuple(f - d * focal_distance for f, d in zip(focal, look))


# --------------------------------------------------------------------------- mouse handling (FreeCAD)

def _saved_enabled():
    try:
        import FreeCAD as App
        return App.ParamGet(PARAMS_PATH).GetBool("MouseControls", True)
    except Exception:  # noqa: BLE001 - tests run without FreeCAD
        return True


ENABLED = _saved_enabled()


def _pos(event):
    return event.position() if hasattr(event, "position") else event.localPos()


def _global_pos(event):
    return event.globalPosition() if hasattr(event, "globalPosition") else event.screenPos()


_FILTER_CLASS = None


def _filter_class():
    """Built on first use: PySide is only importable inside FreeCAD."""
    global _FILTER_CLASS
    if _FILTER_CLASS is not None:
        return _FILTER_CLASS
    from PySide import QtCore, QtGui, QtWidgets
    from pivy import coin  # noqa: F401 - getCameraNode() fails until pivy's SWIG types are loaded
    Qt = QtCore.Qt
    Ev = QtCore.QEvent

    class MouseControls(QtCore.QObject):
        """Sits on one 3D view's viewport, ahead of FreeCAD's own navigation."""

        def __init__(self, view, doc_name, viewport):
            super().__init__(viewport)
            self.view = view
            self.doc_name = doc_name
            self.viewport = viewport
            self.held = None       # the button we are holding back, while it is down
            self.press = None      # (pos, global pos, modifiers) of that press, for a replay
            self.last = None
            self.dragging = False
            self.cursor = None
            self.replaying = False

        def eventFilter(self, obj, event):
            if self.replaying:
                return False
            try:
                kind = event.type()
                if kind == Ev.MouseButtonPress:
                    return self._pressed(event)
                if kind == Ev.MouseMove and self.held is not None:
                    return self._moved(event)
                if kind == Ev.MouseButtonRelease and self.held is not None and event.button() == self.held:
                    return self._released()
            except Exception as e:  # noqa: BLE001 - a dead view or camera must never eat the mouse
                self._reset()
                try:
                    import FreeCAD as App
                    App.Console.PrintWarning(f"[CADSwarm] mouse controls: {e}\n")
                except Exception:  # noqa: BLE001
                    pass
            return False

        def _pressed(self, event):
            if self.held is not None:
                return True  # a second button during one of our drags: ignore it
            button = event.button()
            if not ENABLED or event.modifiers() != Qt.NoModifier or button not in (Qt.LeftButton, Qt.RightButton):
                return False
            pos = _pos(event)
            if button == Qt.LeftButton and not self._empty_at(pos):
                return False
            self.held = button
            self.press = (QtCore.QPointF(pos), QtCore.QPointF(_global_pos(event)), event.modifiers())
            self.last = QtCore.QPointF(pos)
            self.dragging = False
            return True

        def _moved(self, event):
            if not (event.buttons() & self.held):  # the release went somewhere else (focus lost)
                self._reset()
                return False
            pos = _pos(event)
            if not self.dragging:
                if (pos - self.press[0]).manhattanLength() < QtWidgets.QApplication.startDragDistance():
                    return True
                self.dragging = True
                self.cursor = self.viewport.cursor()
                self.viewport.setCursor(Qt.ClosedHandCursor if self.held == Qt.LeftButton else Qt.SizeAllCursor)
            if self.held == Qt.LeftButton:
                self._pan(self.last, pos)
            else:
                self._orbit(pos.x() - self.last.x(), pos.y() - self.last.y())
            self.last = QtCore.QPointF(pos)
            return True

        def _released(self):
            held, press, dragged = self.held, self.press, self.dragging
            self._reset()
            if not dragged:  # it was a click: let FreeCAD have it after all
                self._replay(held, press)
            return True

        def _reset(self):
            if self.dragging and self.cursor is not None:
                self.viewport.setCursor(self.cursor)
            self.held = self.press = self.last = self.cursor = None
            self.dragging = False

        def _replay(self, button, press):
            pos, global_pos, mods = press
            self.replaying = True
            try:
                for kind, buttons in ((Ev.MouseButtonPress, button), (Ev.MouseButtonRelease, Qt.NoButton)):
                    event = QtGui.QMouseEvent(kind, pos, global_pos, button, buttons, mods)
                    QtWidgets.QApplication.sendEvent(self.viewport, event)
            finally:
                self.replaying = False

        def _coin_xy(self, pos):
            """Qt's logical pixels (y down) to Coin's device pixels (y up)."""
            dpr = self.viewport.devicePixelRatioF()
            height = int(self.view.getSize()[1])
            return int(pos.x() * dpr), height - 1 - int(pos.y() * dpr)

        def _empty_at(self, pos):
            import FreeCADGui as Gui
            doc = Gui.getDocument(self.doc_name)
            if doc is not None and doc.getInEdit() is not None:
                return False  # sketches, transform handles etc. own the left button
            return self.view.getObjectInfo(self._coin_xy(pos)) is None

        def _pan(self, before, after):
            a = self.view.getPointOnFocalPlane(*self._coin_xy(before))
            b = self.view.getPointOnFocalPlane(*self._coin_xy(after))
            cam = self.view.getCameraNode()
            x, y, z = cam.position.getValue().getValue()
            cam.position.setValue(x + a.x - b.x, y + a.y - b.y, z + a.z - b.z)

        def _orbit(self, dx, dy):
            cam = self.view.getCameraNode()
            orientation, position = orbit(tuple(cam.orientation.getValue().getValue()),
                                          tuple(cam.position.getValue().getValue()),
                                          cam.focalDistance.getValue(), dx, dy)
            cam.orientation.setValue(*orientation)
            cam.position.setValue(*position)

    _FILTER_CLASS = MouseControls
    return MouseControls


def attach():
    """Put the mouse controls on every open 3D view that lacks them. Cheap; safe to call often."""
    import FreeCAD as App
    import FreeCADGui as Gui
    cls = _filter_class()
    for name in App.listDocuments():
        doc = Gui.getDocument(name)
        if doc is None:
            continue
        for view in doc.mdiViewsOfType("Gui::View3DInventor"):
            try:
                viewport = view.graphicsView().viewport()
            except Exception:  # noqa: BLE001 - a view without a Coin viewer (still opening)
                continue
            if viewport.property("cadswarmMouse"):
                continue
            viewport.setProperty("cadswarmMouse", True)
            viewport.installEventFilter(cls(view, name, viewport))  # parented to the viewport: dies with it


_INSTALLED = False


def install():
    """Idempotent: called once at FreeCAD start. New views get the controls when they first show."""
    global _INSTALLED
    if _INSTALLED:
        return
    import FreeCADGui as Gui
    from PySide import QtWidgets
    _INSTALLED = True
    # getMainWindow() is a plain QMainWindow to Python (no getMdiArea), so find the MDI area itself.
    Gui.getMainWindow().findChild(QtWidgets.QMdiArea).subWindowActivated.connect(lambda _w: _attach_quietly())
    _attach_quietly()


def _attach_quietly():
    try:
        attach()
    except Exception as e:  # noqa: BLE001 - never break FreeCAD over this
        import FreeCAD as App
        App.Console.PrintWarning(f"[CADSwarm] could not add the mouse controls: {e}\n")


def set_enabled(on):
    global ENABLED
    ENABLED = bool(on)
    try:
        import FreeCAD as App
        App.ParamGet(PARAMS_PATH).SetBool("MouseControls", ENABLED)
    except Exception:  # noqa: BLE001
        pass
    return ENABLED


class _Toggle:
    def GetResources(self):
        return {"MenuText": "Left-drag moves, right-drag turns",
                "ToolTip": "Drag with the left mouse button to move the view and with the right one to "
                           "turn it. Clicks still select and open the menu.",
                "Checkable": ENABLED}

    def Activated(self, checked=None):
        set_enabled(not ENABLED if checked is None else checked)

    def IsActive(self):
        return True


def register_command():
    import FreeCADGui as Gui
    Gui.addCommand("CadSwarm_MouseControls", _Toggle())
