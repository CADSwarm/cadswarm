"""CADSwarm's own Qt CAD workspace. FreeCAD is not started or imported."""

from __future__ import annotations

import math
import json
import os
import sys
from array import array

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from cadswarm.app.standalone_engine import ModelError, StandaloneModel, default_project_path, set_active_project


class TeamStartWorker(QtCore.QThread):
    progress = QtCore.Signal(str)
    succeeded = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, python, project_path, db, mode="local"):
        super().__init__()
        self.python = python
        self.project_path = os.path.abspath(project_path)
        self.db = db
        self.mode = mode

    def run(self):
        try:
            from cadswarm import hosting

            project_dir = os.path.dirname(self.project_path)
            hosting.start_team(self.python, project_dir, mode=self.mode, db=self.db, engine="standalone",
                               model_path=self.project_path, on_log=self.progress.emit)
            ticket = hosting.mint_invite(project_dir)
            self.succeeded.emit(ticket)
        except Exception as exc:
            self.failed.emit(str(exc))


class ModelView(QOpenGLWidget):
    """Depth-buffered OpenGL viewport with screen-space picking for tessellated B-rep meshes."""

    selectionChanged = QtCore.Signal(str)

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.yaw = -0.65
        self.pitch = 0.55
        self.zoom = 1.0
        self.offset = QtCore.QPointF(0, 0)
        self.selected = None
        self._mesh_revision = -1
        self._meshes = []
        self._last_mouse = None
        self._dragged = False
        self._camera_center = (0.0, 0.0, 0.0)
        self._camera_span = 1.0
        self._fit_scale = 1.0
        self._gl_ready = False
        self._gl_error = ""
        self._program = None
        self._vbo = None
        self._position_location = -1
        self._color_location = -1
        self._uniforms = {}
        surface_format = QtGui.QSurfaceFormat()
        surface_format.setVersion(2, 1)
        surface_format.setProfile(QtGui.QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
        surface_format.setDepthBufferSize(24)
        surface_format.setSamples(4)
        self.setFormat(surface_format)
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def refresh(self):
        data = self.model.mesh_data()
        self._mesh_revision = data["revision"]
        self._meshes = data["objects"]
        self.update()

    def _rotate(self, p):
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        x, y, z = p
        x, y = x * cy - y * sy, x * sy + y * cy
        y, z = y * cp - z * sp, y * sp + z * cp
        return x, y, z

    def _projector(self):
        raw_points = [p for mesh in self._meshes for p in mesh["vertices"]]
        points = [self._rotate(p) for p in raw_points]
        if not points:
            return lambda p: QtCore.QPointF(self.width() / 2, self.height() / 2), 1.0
        mins = [min(p[i] for p in points) for i in range(3)]
        maxs = [max(p[i] for p in points) for i in range(3)]
        raw_mins = [min(p[i] for p in raw_points) for i in range(3)]
        raw_maxs = [max(p[i] for p in raw_points) for i in range(3)]
        world_center = [(raw_mins[i] + raw_maxs[i]) / 2 for i in range(3)]
        center = self._rotate(world_center)
        self._camera_center = tuple(world_center)
        span = max(maxs[i] - mins[i] for i in range(3)) or 1.0
        self._camera_span = span
        self._fit_scale = min(self.width(), self.height()) * 0.78 / span
        scale = self._fit_scale * self.zoom

        def project(p):
            x, y, z = self._rotate(p)
            return QtCore.QPointF(self.width() / 2 + (x - center[0]) * scale + self.offset.x(),
                                  self.height() / 2 - (z - center[2]) * scale + self.offset.y())

        return project, scale

    def initializeGL(self):
        functions = self.context().functions()
        functions.glEnable(0x0B71)  # GL_DEPTH_TEST
        self._program = QOpenGLShaderProgram(self)
        vertex = """
            attribute vec3 position;
            attribute vec3 color;
            uniform vec3 center;
            uniform float cy;
            uniform float sy;
            uniform float cp;
            uniform float sp;
            uniform float scaleX;
            uniform float scaleY;
            uniform float depthScale;
            uniform vec2 pan;
            varying vec3 vColor;
            void main() {
                vec3 p = position - center;
                float x = p.x * cy - p.y * sy;
                float y = p.x * sy + p.y * cy;
                float z = y * sp + p.z * cp;
                float d = y * cp - p.z * sp;
                gl_Position = vec4(x * scaleX + pan.x, z * scaleY + pan.y, -d * depthScale, 1.0);
                vColor = color;
            }
        """
        fragment = "varying vec3 vColor; void main() { gl_FragColor = vec4(vColor, 1.0); }"
        if not self._program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex):
            self._gl_error = self._program.log()
            return
        if not self._program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment):
            self._gl_error = self._program.log()
            return
        if not self._program.link():
            self._gl_error = self._program.log()
            return
        self._position_location = self._program.attributeLocation("position")
        self._color_location = self._program.attributeLocation("color")
        self._uniforms = {name: self._program.uniformLocation(name) for name in
                          ("center", "cy", "sy", "cp", "sp", "scaleX", "scaleY", "depthScale", "pan")}
        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
        self._gl_ready = True

    def resizeGL(self, width, height):
        self.context().functions().glViewport(0, 0, max(1, width), max(1, height))

    def paintGL(self):
        functions = self.context().functions()
        functions.glClearColor(0.91, 0.93, 0.95, 1.0)
        functions.glClear(0x00004000 | 0x00000100)  # color + depth
        self._projector()
        if not self._gl_ready:
            self._paint_fallback()
            return
        line_data, triangle_data = array("f"), array("f")
        grid_step = self._grid_step()
        extent = max(100, int(max(self.width(), self.height()) / max(1.0, self._fit_scale * self.zoom)))
        extent = min(10000, int(math.ceil(extent / grid_step) * grid_step))
        line_color = (0.81, 0.85, 0.88)
        def vertex(target, point, color):
            target.extend((float(point[0]), float(point[1]), float(point[2]), *color))
        for coord in range(-extent, extent + grid_step, grid_step):
            vertex(line_data, (coord, -extent, 0), line_color)
            vertex(line_data, (coord, extent, 0), line_color)
            vertex(line_data, (-extent, coord, 0), line_color)
            vertex(line_data, (extent, coord, 0), line_color)
        for start, end, color in (((-extent, 0, 0), (extent, 0, 0), (0.82, 0.33, 0.27)),
                                  ((0, -extent, 0), (0, extent, 0), (0.28, 0.58, 0.37))):
            vertex(line_data, start, color)
            vertex(line_data, end, color)
        for mesh in self._meshes:
            color = (0.95, 0.40, 0.12) if mesh["name"] == self.selected else (0.66, 0.73, 0.79)
            for tri in mesh["triangles"]:
                for index in tri:
                    vertex(triangle_data, mesh["vertices"][int(index)], color)
        data = line_data + triangle_data
        self._program.bind()
        self._program.setUniformValue(self._uniforms["center"], QtGui.QVector3D(*self._camera_center))
        self._program.setUniformValue(self._uniforms["cy"], math.cos(self.yaw))
        self._program.setUniformValue(self._uniforms["sy"], math.sin(self.yaw))
        self._program.setUniformValue(self._uniforms["cp"], math.cos(self.pitch))
        self._program.setUniformValue(self._uniforms["sp"], math.sin(self.pitch))
        scale = self._fit_scale * self.zoom
        self._program.setUniformValue(self._uniforms["scaleX"], 2.0 * scale / max(1, self.width()))
        self._program.setUniformValue(self._uniforms["scaleY"], 2.0 * scale / max(1, self.height()))
        self._program.setUniformValue(self._uniforms["depthScale"], 1.0 / max(1.0, 2.0 * self._camera_span))
        self._program.setUniformValue(self._uniforms["pan"], QtGui.QVector2D(2.0 * self.offset.x() / max(1, self.width()),
                                                                            -2.0 * self.offset.y() / max(1, self.height())))
        self._vbo.bind()
        raw = data.tobytes()
        self._vbo.allocate(raw, len(raw))
        stride = 6 * 4
        self._program.enableAttributeArray(self._position_location)
        self._program.enableAttributeArray(self._color_location)
        self._program.setAttributeBuffer(self._position_location, 0x1406, 0, 3, stride)
        self._program.setAttributeBuffer(self._color_location, 0x1406, 3 * 4, 3, stride)
        functions.glDrawArrays(0x0001, 0, len(line_data) // 6)  # GL_LINES
        functions.glDrawArrays(0x0004, len(line_data) // 6, len(triangle_data) // 6)  # GL_TRIANGLES
        self._vbo.release()
        self._program.release()

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self._draw_triad(painter)
        painter.setPen(QtGui.QColor("#46535e"))
        painter.drawText(14, 24, "MODEL SPACE  /  MM")
        painter.setPen(QtGui.QColor("#64737d"))
        painter.drawText(14, self.height() - 14, "CLICK SELECT  |  MIDDLE DRAG ORBIT  |  SHIFT+MIDDLE PAN  |  WHEEL ZOOM")
        painter.end()

    def _paint_fallback(self):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        gradient = QtGui.QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QtGui.QColor("#eef2f6"))
        gradient.setColorAt(1, QtGui.QColor("#dbe2e9"))
        painter.fillRect(self.rect(), gradient)
        project, _ = self._projector()

        # Keep the workplane grid legible at any model scale.
        grid_step = self._grid_step()
        grid_extent = max(100, int(max(self.width(), self.height()) / max(1.0, self._fit_scale * self.zoom)))
        grid_extent = min(10000, int(math.ceil(grid_extent / grid_step) * grid_step))
        painter.setPen(QtGui.QPen(QtGui.QColor("#d4dce2"), 1))
        for coord in range(-grid_extent, grid_extent + grid_step, grid_step):
            painter.drawLine(project((coord, -grid_extent, 0)), project((coord, grid_extent, 0)))
            painter.drawLine(project((-grid_extent, coord, 0)), project((grid_extent, coord, 0)))
        painter.setPen(QtGui.QPen(QtGui.QColor("#8495a2"), 1.5))
        painter.drawLine(project((-grid_extent, 0, 0)), project((grid_extent, 0, 0)))
        painter.drawLine(project((0, -grid_extent, 0)), project((0, grid_extent, 0)))

        faces = []
        for mesh in self._meshes:
            verts = mesh["vertices"]
            rotated = [self._rotate(p) for p in verts]
            for tri in mesh["triangles"]:
                a, b, c = (verts[i] for i in tri)
                depth = sum(rotated[i][1] for i in tri) / 3
                faces.append((depth, mesh["name"], a, b, c))
        faces.sort(key=lambda face: face[0])
        for _, name, a, b, c in faces:
            p1, p2, p3 = project(a), project(b), project(c)
            polygon = QtGui.QPolygonF([p1, p2, p3])
            color = QtGui.QColor("#ff7a24" if name == self.selected else "#aebcca")
            painter.setBrush(color)
            painter.setPen(QtGui.QPen(QtGui.QColor("#394550"), 0.8))
            painter.drawPolygon(polygon)

        self._draw_triad(painter)
        painter.setPen(QtGui.QColor("#46535e"))
        painter.drawText(14, 24, "CADSWARM  /  MODEL SPACE")
        painter.drawText(14, self.height() - 14, "MM  ·  DRAG TO ORBIT  ·  WHEEL TO ZOOM")

    def _grid_step(self):
        scale = max(1e-6, self._fit_scale * self.zoom)
        target = 54.0 / scale
        magnitude = 10 ** math.floor(math.log10(max(target, 1e-6)))
        return min((1, 2, 5, 10), key=lambda mult: abs(mult * magnitude * scale - 54.0)) * magnitude

    def _draw_triad(self, painter):
        origin = QtCore.QPointF(self.width() - 70, self.height() - 62)
        for axis, color, label in (((30, 0, 0), "#d95040", "X"),
                                   ((0, 30, 0), "#4a9a61", "Y"),
                                   ((0, 0, 30), "#4776bc", "Z")):
            rotated = self._rotate(axis)
            end = origin + QtCore.QPointF(rotated[0], -rotated[2])
            painter.setPen(QtGui.QPen(QtGui.QColor(color), 2.5))
            painter.drawLine(origin, end)
            painter.drawText(end + QtCore.QPointF(3, -3), label)

    @staticmethod
    def _barycentric(point, a, b, c):
        v0x, v0y = b.x() - a.x(), b.y() - a.y()
        v1x, v1y = c.x() - a.x(), c.y() - a.y()
        v2x, v2y = point.x() - a.x(), point.y() - a.y()
        den = v0x * v1y - v1x * v0y
        if abs(den) < 1e-9:
            return None
        u = (v2x * v1y - v1x * v2y) / den
        v = (v0x * v2y - v2x * v0y) / den
        w = 1.0 - u - v
        return (u, v, w) if min(u, v, w) >= -1e-6 else None

    def _pick(self, point):
        project, _ = self._projector()
        best = None
        for mesh in self._meshes:
            verts = mesh["vertices"]
            for tri in mesh["triangles"]:
                indices = tuple(int(i) for i in tri)
                projected = [project(verts[i]) for i in indices]
                weights = self._barycentric(point, *projected)
                if weights is None:
                    continue
                depth = sum(self._rotate(verts[i])[1] * weights[j] for j, i in enumerate(indices))
                if best is None or depth > best[0]:
                    best = (depth, mesh["name"])
        return best[1] if best else ""

    def mousePressEvent(self, event):
        self._last_mouse = event.position().toPoint()
        self._dragged = False

    def mouseMoveEvent(self, event):
        point = event.position().toPoint()
        if self._last_mouse is not None and event.buttons() & QtCore.Qt.MouseButton.MiddleButton:
            delta = point - self._last_mouse
            if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
                self.offset += QtCore.QPointF(delta)
            else:
                self.yaw += delta.x() * 0.008
                self.pitch = max(-1.45, min(1.45, self.pitch + delta.y() * 0.008))
            self._dragged = True
            self.update()
        self._last_mouse = point

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton and not self._dragged:
            self.selectionChanged.emit(self._pick(event.position()))
        self._last_mouse = None
        self._dragged = False

    def wheelEvent(self, event):
        self.zoom = max(0.15, min(12.0, self.zoom * (1.12 if event.angleDelta().y() > 0 else 1 / 1.12)))
        self.update()

    def fit(self):
        self.zoom = 1.0
        self.offset = QtCore.QPointF(0, 0)
        self.update()


class SketchCanvas(QtWidgets.QWidget):
    """Small, millimetre-based sketch surface used by the standalone constraint solver."""

    def __init__(self, entities=None, constraints=None, parent=None):
        super().__init__(parent)
        self.entities = [dict(item) for item in (entities or [])]
        self.constraints = [dict(item) for item in (constraints or [])]
        self.tool = "select"
        self.selected = None
        self.pending = None
        self._counter = 1
        self.setMinimumSize(620, 420)
        self.setMouseTracking(True)

    def set_tool(self, tool):
        self.tool = tool
        self.pending = None
        self.update()

    def _scale(self):
        return max(0.35, min(self.width(), self.height()) / 300.0)

    def _to_model(self, point):
        scale = self._scale()
        x = (point.x() - self.width() / 2) / scale
        y = (self.height() / 2 - point.y()) / scale
        return [round(x * 2) / 2, round(y * 2) / 2]

    def _to_screen(self, point):
        scale = self._scale()
        return QtCore.QPointF(self.width() / 2 + point[0] * scale,
                              self.height() / 2 - point[1] * scale)

    def _id(self, prefix="e"):
        existing = {item.get("id") for item in self.entities + self.constraints}
        while True:
            value = f"{prefix}{self._counter}"
            self._counter += 1
            if value not in existing:
                return value

    def _constraint(self, kind, entities, points=None, value=None):
        if points and kind in ("fixed", "coincident"):
            if any(row.get("type") == kind and row.get("points") == list(points)
                   for row in self.constraints):
                return
        row = {"id": self._id("c"), "type": kind, "entities": list(entities)}
        if points:
            row["points"] = list(points)
        if value is not None:
            row["value"] = value
        self.constraints.append(row)

    def _add_line(self, start, end, forced=None):
        entity_id = self._id()
        self.entities.append({"id": entity_id, "type": "line", "start": start, "end": end})
        dx, dy = end[0] - start[0], end[1] - start[1]
        kind = forced or ("horizontal" if abs(dy) < 0.5 else "vertical" if abs(dx) < 0.5 else None)
        if kind:
            self._constraint(kind, [entity_id])
        return entity_id

    def _finish_rectangle(self, corner_a, corner_b):
        x1, y1 = corner_a
        x2, y2 = corner_b
        if abs(x2 - x1) < 0.5 or abs(y2 - y1) < 0.5:
            return
        points = ([x1, y1], [x2, y1], [x2, y2], [x1, y2])
        ids = [self._add_line(points[i], points[(i + 1) % 4], "horizontal" if i % 2 == 0 else "vertical")
               for i in range(4)]
        for i in range(4):
            self._constraint("coincident", [ids[i], ids[(i + 1) % 4]],
                             [ids[i] + ".end", ids[(i + 1) % 4] + ".start"])

    def _entity_distance(self, point, entity):
        if entity["type"] in ("circle", "arc"):
            return abs(math.hypot(point.x() - self._to_screen(entity["center"]).x(),
                                  point.y() - self._to_screen(entity["center"]).y()) - entity["radius"] * self._scale())
        if entity["type"] != "line":
            return 1e9
        a, b = self._to_screen(entity["start"]), self._to_screen(entity["end"])
        dx, dy = b.x() - a.x(), b.y() - a.y()
        denominator = dx * dx + dy * dy or 1
        t = max(0.0, min(1.0, ((point.x() - a.x()) * dx + (point.y() - a.y()) * dy) / denominator))
        return math.hypot(point.x() - (a.x() + t * dx), point.y() - (a.y() + t * dy))

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor("#fbfcfd"))
        scale = self._scale()
        step = max(1, int(10 * scale))
        painter.setPen(QtGui.QPen(QtGui.QColor("#e8edf0"), 1))
        x = int(self.width() / 2) % step
        while x < self.width():
            painter.drawLine(x, 0, x, self.height())
            x += step
        y = int(self.height() / 2) % step
        while y < self.height():
            painter.drawLine(0, y, self.width(), y)
            y += step
        painter.setPen(QtGui.QPen(QtGui.QColor("#75848d"), 1.4))
        painter.drawLine(0, self.height() / 2, self.width(), self.height() / 2)
        painter.drawLine(self.width() / 2, 0, self.width() / 2, self.height())
        for entity in self.entities:
            color = QtGui.QColor("#f27b36" if entity["id"] == self.selected else "#26343b")
            painter.setPen(QtGui.QPen(color, 2.2))
            if entity["type"] == "line":
                painter.drawLine(self._to_screen(entity["start"]), self._to_screen(entity["end"]))
            elif entity["type"] == "circle":
                center = self._to_screen(entity["center"])
                radius = float(entity["radius"]) * scale
                painter.drawEllipse(center, radius, radius)
            elif entity["type"] == "arc":
                center = self._to_screen(entity["center"])
                radius = float(entity["radius"]) * scale
                rect = QtCore.QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
                painter.drawArc(rect, int(-math.degrees(entity["start_angle"]) * 16),
                                int(-math.degrees(entity["end_angle"] - entity["start_angle"]) * 16))
        painter.setPen(QtGui.QColor("#64737d"))
        painter.drawText(12, 22, f"SKETCH  /  GRID 10 MM  /  {self.tool.upper()}")

    def mousePressEvent(self, event):
        point = event.position()
        model_point = self._to_model(point)
        if self.tool == "select":
            nearest = min(self.entities, key=lambda e: self._entity_distance(point, e), default=None)
            self.selected = nearest["id"] if nearest and self._entity_distance(point, nearest) <= 12 else None
            self.update()
            return
        if self.tool == "arc":
            if self.pending is None:
                self.pending = [model_point]
            elif len(self.pending) == 1:
                self.pending.append(model_point)
            else:
                center, start = self.pending
                self.pending = None
                radius = math.dist(center, start)
                if radius > 0:
                    arc_id = self._id()
                    self.entities.append({"id": arc_id, "type": "arc", "center": center, "radius": radius,
                                         "start_angle": math.atan2(start[1] - center[1], start[0] - center[0]),
                                         "end_angle": math.atan2(model_point[1] - center[1],
                                                                  model_point[0] - center[0])})
            self.update()
            return
        if self.pending is None:
            self.pending = model_point
            self.update()
            return
        first, self.pending = self.pending, None
        if self.tool == "line":
            self._add_line(first, model_point)
        elif self.tool == "rectangle":
            self._finish_rectangle(first, model_point)
        elif self.tool == "circle":
            radius = round(math.hypot(model_point[0] - first[0], model_point[1] - first[1]), 2)
            if radius > 0:
                circle_id = self._id()
                self.entities.append({"id": circle_id, "type": "circle", "center": first, "radius": radius})
        self.update()


class SketchDialog(QtWidgets.QDialog):
    def __init__(self, name, plane="XY", entities=None, constraints=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit sketch")
        self.resize(900, 620)
        self.name = name
        layout = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Sketch name"))
        self.name_edit = QtWidgets.QLineEdit(name)
        top.addWidget(self.name_edit, 2)
        top.addWidget(QtWidgets.QLabel("Plane"))
        self.plane = QtWidgets.QComboBox()
        self.plane.addItems(["XY", "XZ", "YZ"])
        self.plane.setCurrentText(plane)
        top.addWidget(self.plane)
        layout.addLayout(top)
        tools = QtWidgets.QHBoxLayout()
        self.canvas = SketchCanvas(entities, constraints)
        for label, key in (("Select", "select"), ("Line", "line"), ("Rectangle", "rectangle"),
                           ("Circle", "circle"), ("Arc", "arc")):
            button = QtWidgets.QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, value=key: self._set_tool(value))
            tools.addWidget(button)
            if key == "select":
                button.setChecked(True)
        tools.addStretch(1)
        fix = QtWidgets.QPushButton("Fix selected")
        fix.clicked.connect(self._fix_selected)
        dimension = QtWidgets.QPushButton("Add dimension")
        dimension.clicked.connect(self._dimension_selected)
        tools.addWidget(fix)
        tools.addWidget(dimension)
        layout.addLayout(tools)
        layout.addWidget(self.canvas, 1)
        solver_help = QtWidgets.QLabel("Click two points to draw. Geometry snaps to 0.5 mm; rectangles receive horizontal, vertical, and coincident constraints.")
        solver_help.setWordWrap(True)
        layout.addWidget(solver_help)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel |
                                             QtWidgets.QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_tool(self, value):
        self.canvas.set_tool(value)
        for button in self.findChildren(QtWidgets.QToolButton):
            button.setChecked(button.text().lower() == value)

    def _fix_selected(self):
        entity = next((e for e in self.canvas.entities if e["id"] == self.canvas.selected), None)
        if not entity:
            return
        if entity["type"] == "line":
            points = (("start", entity["start"]), ("end", entity["end"]))
        elif entity["type"] in ("circle", "arc"):
            points = (("center", entity["center"]),)
        else:
            points = ()
        for point_name, point_value in points:
            self.canvas._constraint("fixed", [entity["id"]],
                                    [entity["id"] + "." + point_name], list(point_value))
        self.canvas.update()

    def _dimension_selected(self):
        entity = next((e for e in self.canvas.entities if e["id"] == self.canvas.selected), None)
        if not entity:
            return
        if entity["type"] in ("circle", "arc"):
            value, ok = QtWidgets.QInputDialog.getDouble(self, "Curve radius", "Radius (mm)",
                                                          entity["radius"], 0.01, 100000, 2)
            kind = "radius"
        elif entity["type"] == "line":
            current = math.dist(entity["start"], entity["end"])
            value, ok = QtWidgets.QInputDialog.getDouble(self, "Line length", "Length (mm)",
                                                          current, 0.01, 100000, 2)
            kind = "distance"
        else:
            return
        if ok:
            points = [entity["id"] + ".start", entity["id"] + ".end"] if kind == "distance" else None
            self.canvas.constraints = [row for row in self.canvas.constraints
                                       if not (row.get("type") == kind and row.get("entities") == [entity["id"]])]
            self.canvas._constraint(kind, [entity["id"]], points=points, value=value)
            self.canvas.update()

    def result_data(self):
        return self.name_edit.text().strip(), self.plane.currentText(), self.canvas.entities, self.canvas.constraints


class EdgeSelectionDialog(QtWidgets.QDialog):
    def __init__(self, edges, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select edges")
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Choose one or more edges for the fillet or chamfer."))
        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        for edge in edges:
            center = ", ".join(f"{value:.2f}" for value in edge["center"])
            item = QtWidgets.QListWidgetItem(f"Edge {edge['index']}    |    {edge['length']:.2f} mm    |    ({center})")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, int(edge["index"]))
            self.list.addItem(item)
        layout.addWidget(self.list)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel |
                                             QtWidgets.QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_edges(self):
        return [item.data(QtCore.Qt.ItemDataRole.UserRole) for item in self.list.selectedItems()]


class DesktopWindow(QtWidgets.QMainWindow):
    def __init__(self, project_path):
        super().__init__()
        self.model = StandaloneModel(project_path)
        set_active_project(project_path)
        self.setWindowTitle(f"CADSwarm — {os.path.basename(project_path)}")
        self.resize(1440, 900)
        self.setMinimumSize(960, 620)
        self._seen_revision = -1
        self._build_ui()
        self.refresh()
        self._watch = QtCore.QTimer(self)
        self._watch.setInterval(1200)
        self._watch.timeout.connect(self._refresh_external)
        self._watch.start()

    def _build_ui(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f4f5f4; color: #202629; font-family: 'Segoe UI'; }
            QToolBar { background: #202629; border: 0; spacing: 4px; padding: 8px; }
            QToolBar QToolButton { color: #f4f5f4; background: #30393d; border: 1px solid #657177;
              padding: 7px 12px; font-weight: 600; }
            QToolBar QToolButton:hover { background: #f27b36; color: #202629; }
            QTreeWidget { background: #fff; border: 0; alternate-background-color: #f3f5f6; }
            QTreeWidget::item { padding: 6px; }
            QTreeWidget::item:selected { background: #f8d7c2; color: #202629; }
            #propertiesPanel { background: #ffffff; border-left: 1px solid #c9d0d2; }
            #panelTitle { font-size: 11px; font-weight: 700; letter-spacing: 1px; padding-bottom: 8px; }
            QDoubleSpinBox, QLineEdit, QComboBox { background: #ffffff; border: 1px solid #b8c1c4; padding: 4px; }
            QStatusBar { background: #202629; color: #f4f5f4; }
            QMenuBar { background: #e5e9e9; }
        """)
        toolbar = QtWidgets.QToolBar("Commands", self)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(toolbar)
        for label, callback in (("UNDO", self.undo), ("REDO", self.redo),
                                ("IMPORT STEP", self.import_step), ("IMPORT STL", self.import_stl),
                                ("NEW BOX", self.add_box),
                                ("NEW CYLINDER", self.add_cylinder),
                                ("NEW SPHERE", self.add_sphere), ("MOVE", self.move_feature),
                                ("SKETCH", self.create_sketch),
                                ("EXTRUDE", lambda: self.create_sketch_feature("extrude")),
                                ("EDIT DIMENSION", self.edit_dimension),
                                ("UNION", lambda: self.combine("union")),
                                ("SUBTRACT", lambda: self.combine("subtract")),
                                ("INTERSECT", lambda: self.combine("intersect")),
                                ("FIT", self.view_fit), ("EXPORT STEP", lambda: self.export("step")),
                                ("EXPORT STL", lambda: self.export("stl"))):
            action = QtGui.QAction(label, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)
        toolbar.addSeparator()
        self.command_search = QtWidgets.QLineEdit(self)
        self.command_search.setPlaceholderText("Search commands  (Ctrl+K)")
        self.command_search.setClearButtonEnabled(True)
        self.command_search.setMaximumWidth(260)
        self.command_search.returnPressed.connect(self._run_command)
        toolbar.addWidget(self.command_search)

        file_menu = self.menuBar().addMenu("File")
        self._menu_action(file_menu, "New project…", self.new_project)
        self._menu_action(file_menu, "Open project…", self.open_project)
        self._menu_action(file_menu, "Save", self.save)
        self._menu_action(file_menu, "Import STEP…", self.import_step)
        self._menu_action(file_menu, "Import STL mesh…", self.import_stl)
        self._menu_action(file_menu, "Export STEP…", lambda: self.export("step"))
        self._menu_action(file_menu, "Export STL…", lambda: self.export("stl"))

        team_menu = self.menuBar().addMenu("Team")
        self._menu_action(team_menu, "Host multiplayer team…", self.host_team)

        sketch_menu = self.menuBar().addMenu("Sketch")
        self._menu_action(sketch_menu, "New sketch...", self.create_sketch)
        self._menu_action(sketch_menu, "Edit selected sketch...", self.edit_sketch)
        feature_menu = self.menuBar().addMenu("Feature")
        self._menu_action(feature_menu, "Extrude...", lambda: self.create_sketch_feature("extrude"))
        self._menu_action(feature_menu, "Revolve...", lambda: self.create_sketch_feature("revolve"))
        self._menu_action(feature_menu, "Pocket...", lambda: self.create_sketch_feature("pocket"))
        self._menu_action(feature_menu, "Hole...", lambda: self.create_sketch_feature("hole"))
        self._menu_action(feature_menu, "Fillet...", lambda: self.create_sketch_feature("fillet"))
        self._menu_action(feature_menu, "Chamfer...", lambda: self.create_sketch_feature("chamfer"))
        self._menu_action(feature_menu, "Linear pattern...", lambda: self.create_sketch_feature("linear_pattern"))
        self._menu_action(feature_menu, "Circular pattern...", lambda: self.create_sketch_feature("circular_pattern"))

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["FEATURE HISTORY"])
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumWidth(210)
        self.tree.setMaximumWidth(330)
        self.tree.itemSelectionChanged.connect(self._select_feature)
        self.tree.itemSelectionChanged.connect(self._render_properties)
        self.view = ModelView(self.model)
        self.view.selectionChanged.connect(self._select_by_view)
        self.properties = QtWidgets.QWidget()
        self.properties.setObjectName("propertiesPanel")
        self.properties.setMinimumWidth(220)
        self.properties.setMaximumWidth(320)
        self.properties_layout = QtWidgets.QVBoxLayout(self.properties)
        self.properties_layout.setContentsMargins(12, 12, 12, 12)
        self.properties_title = QtWidgets.QLabel("PROPERTIES")
        self.properties_title.setObjectName("panelTitle")
        self.properties_layout.addWidget(self.properties_title)
        self.properties_body = QtWidgets.QWidget()
        self.properties_form = QtWidgets.QFormLayout(self.properties_body)
        self.properties_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.properties_layout.addWidget(self.properties_body)
        self.properties_layout.addStretch(1)
        split.addWidget(self.tree)
        split.addWidget(self.view)
        split.addWidget(self.properties)
        split.setSizes([250, 900, 270])
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)
        self._add_shortcut("Ctrl+K", self._focus_command_search)
        self._add_shortcut("Ctrl+S", self.save)
        self._add_shortcut("Ctrl+Z", self.undo)
        self._add_shortcut("Ctrl+Y", self.redo)
        self._add_shortcut("Ctrl+0", self.view_fit)
        self.team_state = QtWidgets.QLabel()
        self.team_state.setMargin(6)
        self.statusBar().addPermanentWidget(self.team_state)
        self._update_team_state()
        edit_menu = self.menuBar().addMenu("Edit")
        self._menu_action(edit_menu, "Undo", self.undo)
        self._menu_action(edit_menu, "Redo", self.redo)
        self._menu_action(edit_menu, "Delete selected feature...", self.delete_selected)
        view_menu = self.menuBar().addMenu("View")
        self._menu_action(view_menu, "Fit model", self.view_fit)
        self.statusBar().showMessage("Ready  ·  Units: mm  ·  Local MCP project")

    def _menu_action(self, menu, label, callback):
        action = menu.addAction(label)
        action.triggered.connect(callback)

    def _add_shortcut(self, sequence, callback):
        shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), self)
        shortcut.activated.connect(callback)
        if not hasattr(self, "_shortcuts"):
            self._shortcuts = []
        self._shortcuts.append(shortcut)

    def _focus_command_search(self):
        self.command_search.setFocus()
        self.command_search.selectAll()

    def _run_command(self):
        commands = {
            "new box": self.add_box, "new cylinder": self.add_cylinder, "new sphere": self.add_sphere,
            "new sketch": self.create_sketch, "edit sketch": self.edit_sketch,
            "extrude": lambda: self.create_sketch_feature("extrude"),
            "revolve": lambda: self.create_sketch_feature("revolve"),
            "pocket": lambda: self.create_sketch_feature("pocket"),
            "hole": lambda: self.create_sketch_feature("hole"),
            "fillet": lambda: self.create_sketch_feature("fillet"),
            "chamfer": lambda: self.create_sketch_feature("chamfer"),
            "linear pattern": lambda: self.create_sketch_feature("linear_pattern"),
            "circular pattern": lambda: self.create_sketch_feature("circular_pattern"),
            "import step": self.import_step, "import stl": self.import_stl,
            "export step": lambda: self.export("step"), "export stl": lambda: self.export("stl"),
            "undo": self.undo, "redo": self.redo, "save": self.save, "fit": self.view_fit,
            "move": self.move_feature, "union": lambda: self.combine("union"),
            "subtract": lambda: self.combine("subtract"), "intersect": lambda: self.combine("intersect"),
            "host team": self.host_team,
        }
        query = self.command_search.text().strip().lower()
        if not query:
            return
        matches = [(label, callback) for label, callback in commands.items() if query in label]
        exact = next((callback for label, callback in matches if label == query), None)
        if exact:
            self.command_search.clear()
            exact()
        elif len(matches) == 1:
            self.command_search.clear()
            matches[0][1]()
        elif matches:
            menu = QtWidgets.QMenu(self)
            for label, callback in matches:
                action = menu.addAction(label.title())
                action.triggered.connect(callback)
            menu.exec(self.command_search.mapToGlobal(QtCore.QPoint(0, self.command_search.height())))
        else:
            self.statusBar().showMessage(f"No command found for '{query}'.", 4000)

    def delete_selected(self):
        item = self.tree.currentItem()
        if not item:
            return
        feature = next((f for f in self.model.feature_tree()["features"]
                        if f["id"] == item.data(0, QtCore.Qt.ItemDataRole.UserRole)), None)
        if not feature:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Delete feature", f"Delete {feature['name']} and any dependent features?",
            QtWidgets.QMessageBox.StandardButton.Cancel | QtWidgets.QMessageBox.StandardButton.Yes,
            QtWidgets.QMessageBox.StandardButton.Cancel)
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self._run(lambda: self.model.delete_feature(feature["name"], force=True))

    def _selected_feature(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        feature_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        return next((f for f in self.model.feature_tree()["features"] if f["id"] == feature_id), None)

    def _choose_feature(self, title, prompt, features):
        if not features:
            QtWidgets.QMessageBox.information(self, title, "Create the required sketch or solid first.")
            return None
        labels = [f["name"] for f in features]
        selected = self._selected_feature()
        default = labels.index(selected["name"]) if selected and selected["name"] in labels else 0
        name, ok = QtWidgets.QInputDialog.getItem(self, title, prompt, labels, default, False)
        return next((f for f in features if f["name"] == name), None) if ok else None

    def create_sketch(self):
        dialog = SketchDialog(self._unique_name("Sketch"), parent=self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        name, plane, entities, constraints = dialog.result_data()
        if not entities:
            QtWidgets.QMessageBox.information(self, "Empty sketch", "Draw at least one line or circle before saving.")
            return
        self._run(lambda: self.model.create_sketch(name, plane, entities, constraints))

    def edit_sketch(self):
        feature = self._selected_feature()
        if not feature or feature.get("kind") != "sketch":
            sketches = [f for f in self.model.feature_tree()["features"] if f.get("kind") == "sketch"]
            feature = self._choose_feature("Edit sketch", "Sketch", sketches)
        if not feature:
            return
        dialog = SketchDialog(feature["name"], feature.get("plane", "XY"), feature.get("entities"),
                              feature.get("constraints"), self)
        dialog.name_edit.setEnabled(False)
        dialog.plane.setEnabled(False)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        _, _, entities, constraints = dialog.result_data()
        self._run(lambda: self.model.update_sketch(feature["name"], entities, constraints))

    def create_sketch_feature(self, kind):
        features = self.model.feature_tree()["features"]
        sketch = None
        source = None
        if kind in ("extrude", "revolve", "pocket", "hole"):
            sketches = [f for f in features if f.get("kind") == "sketch"]
            selected = self._selected_feature()
            sketch = selected if selected and selected.get("kind") == "sketch" else self._choose_feature(
                kind.title(), "Profile sketch", sketches)
            if not sketch:
                return
        if kind in ("pocket", "hole", "fillet", "chamfer", "linear_pattern", "circular_pattern"):
            solids = [f for f in features if f.get("visible", True) and
                      f.get("kind") not in ("sketch", "import_stl")]
            source = self._choose_feature(kind.title(), "Target solid", solids)
            if not source:
                return

        parameters = {}
        selection = None
        edge_revision = None
        if kind in ("extrude", "pocket", "hole"):
            default = 10 if kind != "hole" else 100
            depth, ok = QtWidgets.QInputDialog.getDouble(self, kind.title(), "Depth (mm)", default,
                                                          0.01, 100000, 2)
            if not ok:
                return
            parameters["depth"] = depth
            if kind == "pocket":
                direction, ok = QtWidgets.QInputDialog.getItem(self, "Pocket direction", "Direction",
                                                                 ["Into -Z", "Into +Z"], 0, False)
                if not ok:
                    return
                parameters["direction"] = -1 if direction == "Into -Z" else 1
        elif kind == "revolve":
            angle, ok = QtWidgets.QInputDialog.getDouble(self, "Revolve", "Angle (degrees)", 360, 1, 360, 1)
            if not ok:
                return
            axis, ok = QtWidgets.QInputDialog.getItem(self, "Revolve", "Axis", ["X", "Y", "Z"], 2, False)
            if not ok:
                return
            parameters.update(angle=angle, axis=axis)
        elif kind in ("fillet", "chamfer"):
            radius, ok = QtWidgets.QInputDialog.getDouble(self, kind.title(), "Radius (mm)", 1, 0.01, 100000, 2)
            if not ok:
                return
            mesh_snapshot = self.model.mesh_data()
            edge_revision = mesh_snapshot["revision"]
            edges = next((m.get("edges", []) for m in mesh_snapshot["objects"]
                          if m["name"] == source["name"]), [])
            dialog = EdgeSelectionDialog(edges, self)
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            selection = dialog.selected_edges()
            if not selection:
                QtWidgets.QMessageBox.information(self, "Select edges", "Choose at least one edge.")
                return
            parameters["radius"] = radius
        elif kind == "linear_pattern":
            count, ok = QtWidgets.QInputDialog.getInt(self, "Linear pattern", "Instances", 2, 2, 1000)
            if not ok:
                return
            spacing, ok = QtWidgets.QInputDialog.getDouble(self, "Linear pattern", "Spacing (mm)", 10, 0.01,
                                                            100000, 2)
            if not ok:
                return
            direction, ok = QtWidgets.QInputDialog.getItem(self, "Linear pattern", "Direction",
                                                             ["X", "Y", "Z"], 0, False)
            if not ok:
                return
            parameters.update(count=count, spacing=spacing,
                              direction={"X": [1, 0, 0], "Y": [0, 1, 0], "Z": [0, 0, 1]}[direction])
        elif kind == "circular_pattern":
            count, ok = QtWidgets.QInputDialog.getInt(self, "Circular pattern", "Instances", 6, 2, 1000)
            if not ok:
                return
            angle, ok = QtWidgets.QInputDialog.getDouble(self, "Circular pattern", "Total angle (degrees)",
                                                          360, 1, 360, 1)
            if not ok:
                return
            axis, ok = QtWidgets.QInputDialog.getItem(self, "Circular pattern", "Axis", ["X", "Y", "Z"], 2, False)
            if not ok:
                return
            parameters.update(count=count, angle=angle,
                              axis={"X": [1, 0, 0], "Y": [0, 1, 0], "Z": [0, 0, 1]}[axis])

        name = self._unique_name(kind.title().replace("_", ""))
        source_name = source["name"] if source else None
        sketch_name = sketch["name"] if sketch else None
        if edge_revision is not None and self.model.reload() != edge_revision:
            QtWidgets.QMessageBox.warning(self, "Model changed", "The model changed while selecting edges. Reopen the feature and select again.")
            return
        self._run(lambda: self.model.create_sketch_feature(kind, name, sketch_name, parameters,
                                                            source_name, selection))

    def refresh(self):
        tree = self.model.feature_tree()
        if tree["revision"] == self._seen_revision:
            return
        current = self.tree.currentItem()
        selected = current.data(0, QtCore.Qt.ItemDataRole.UserRole) if current else None
        self.tree.clear()
        for feature in tree["features"]:
            details = feature["kind"].upper()
            if feature["kind"] == "sketch":
                solver = feature.get("solver", {})
                details += f"  /  {feature.get('plane', 'XY')}  /  {len(feature.get('entities', []))} entities"
                details += f"  /  {solver.get('status', 'ready')}  /  {solver.get('dof', 0)} DOF"
            elif "parameters" in feature:
                details += "  /  " + ", ".join(f"{key} {value:g}" for key, value in feature["parameters"].items()
                                                 if isinstance(value, (int, float)))
            elif feature["kind"] == "boolean":
                details += "  /  " + feature["operation"]
            elif "dimensions" in feature:
                details += "  /  " + " × ".join(f"{v:g}" for v in feature["dimensions"].values()) + " mm"
            elif feature["kind"] == "translate":
                details += "  /  MOVE " + ", ".join(f"{v:g}" for v in feature["offset"].values()) + " mm"
            elif feature["kind"] == "import_step":
                details += "  /  imported STEP"
            elif feature["kind"] == "import_stl":
                details += "  /  imported STL mesh"
            item = QtWidgets.QTreeWidgetItem([f"{feature['name']}    ·    {details}"])
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, feature["id"])
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, feature["name"])
            self.tree.addTopLevelItem(item)
            if feature["id"] == selected:
                self.tree.setCurrentItem(item)
        self._seen_revision = tree["revision"]
        self.view.refresh()
        self._render_properties()
        self.statusBar().showMessage(f"Revision {tree['revision']}  ·  {len(tree['features'])} features  ·  mm")

    def _refresh_external(self):
        self._update_team_state()
        try:
            self.model.reload()
            self.refresh()
        except (ModelError, RuntimeError) as exc:
            self.statusBar().showMessage(str(exc), 8000)

    def _update_team_state(self):
        running = self._team_host_running()
        if running:
            self.team_state.setText("TEAM HOST  |  EDIT THROUGH CADSWARM MCP")
            self.team_state.setStyleSheet("color: #7a3418; background: #ffe6d4; font-weight: 700;")
            self.team_state.setToolTip("Desktop model editing is read-only while the team host is active. Use the team-connected cadswarm MCP server so locks are enforced.")
        else:
            self.team_state.setText("LOCAL PROJECT")
            self.team_state.setStyleSheet("color: #3d555f; background: #e5ecef; font-weight: 600;")
            self.team_state.setToolTip("Edits to this project are local.")

    def _select_feature(self):
        item = self.tree.currentItem()
        self.view.selected = item.text(0).split("    ·")[0] if item else None
        self.view.update()

    def _select_by_view(self, name):
        match = None
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1) == name:
                match = item
                break
        self.tree.setCurrentItem(match)

    def _render_properties(self):
        while self.properties_form.rowCount():
            self.properties_form.removeRow(0)
        item = self.tree.currentItem()
        if item is None:
            self.properties_title.setText("PROPERTIES")
            self.properties_form.addRow(QtWidgets.QLabel("Select a feature to inspect its parameters."))
            return
        feature_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        feature = next((f for f in self.model.feature_tree()["features"] if f["id"] == feature_id), None)
        if not feature:
            return
        self.properties_title.setText(feature.get("name", "FEATURE").upper())
        self.properties_form.addRow("Type", QtWidgets.QLabel(feature.get("kind", "").replace("_", " ").title()))
        if feature.get("kind") == "sketch":
            self.properties_form.addRow("Plane", QtWidgets.QLabel(str(feature.get("plane", "XY"))))
            self.properties_form.addRow("Entities", QtWidgets.QLabel(str(len(feature.get("entities", [])))))
            solver = feature.get("solver", {})
            self.properties_form.addRow("Solver", QtWidgets.QLabel(str(solver.get("status", "Ready"))))
            self.properties_form.addRow("Degrees of freedom", QtWidgets.QLabel(str(solver.get("dof", "-"))))
        for key, value in (feature.get("dimensions") or feature.get("parameters") or {}).items():
            if isinstance(value, (int, float)):
                editor = QtWidgets.QDoubleSpinBox()
                editor.setEnabled(not self._team_host_running())
                editor.setRange(-1e6 if key in ("angle", "depth") else 0.0001, 1e6)
                editor.setDecimals(3)
                editor.setValue(float(value))
                editor.setSuffix(" deg" if "angle" in key else " mm")
                editor.editingFinished.connect(
                    lambda f=feature.get("name"), k=key, w=editor: self._edit_feature_parameter(f, k, w.value()))
                self.properties_form.addRow(key.replace("_", " ").title(), editor)
            else:
                self.properties_form.addRow(key.replace("_", " ").title(), QtWidgets.QLabel(str(value)))
        if feature.get("solver", {}).get("status") in ("underconstrained", "partial"):
            self.properties_form.addRow("Constraint state", QtWidgets.QLabel("Underconstrained"))

    def _edit_feature_parameter(self, feature_name, dimension, value):
        feature = next((f for f in self.model.feature_tree()["features"] if f["name"] == feature_name), None)
        if not feature:
            return
        if dimension in feature.get("dimensions", {}):
            self._run(lambda: self.model.set_dimension(feature_name, dimension, value))
        elif dimension in feature.get("parameters", {}):
            parameters = dict(feature["parameters"])
            parameters[dimension] = value
            self._run(lambda: self.model.set_feature_parameters(feature_name, parameters))
        else:
            self.statusBar().showMessage("This feature parameter is read-only in the current editor.", 5000)

    def _unique_name(self, prefix):
        names = {item.text(0).split("    ·")[0] for i in range(self.tree.topLevelItemCount())
                 if (item := self.tree.topLevelItem(i))}
        n = 1
        while f"{prefix}_{n}" in names:
            n += 1
        return f"{prefix}_{n}"

    def _run(self, callback):
        if self._team_host_running():
            QtWidgets.QMessageBox.information(
                self, "Team model is locked to multiplayer",
                "This project is being edited by the multiplayer host. Use the team's cadswarm MCP tools so "
                "object locks and the shared task plan are enforced. The desktop viewport will refresh as the "
                "team changes the model.")
            return
        try:
            result = callback()
            self.refresh()
            if isinstance(result, dict) and result.get("id"):
                for index in range(self.tree.topLevelItemCount()):
                    item = self.tree.topLevelItem(index)
                    if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == result["id"]:
                        self.tree.setCurrentItem(item)
                        break
            if result:
                self.statusBar().showMessage(str(result), 6000)
        except (ModelError, RuntimeError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "CADSwarm", str(exc))

    def add_box(self):
        name = self._unique_name("Box")
        dims = {}
        for key, label in (("length", "Length"), ("width", "Width"), ("height", "Height")):
            value, ok = QtWidgets.QInputDialog.getDouble(self, "New box", f"{label} (mm)", 20, 0.01, 10000, 2)
            if not ok:
                return
            dims[key] = value
        self._run(lambda: self.model.create_primitive("box", name, dims))

    def add_cylinder(self):
        name = self._unique_name("Cylinder")
        radius, ok = QtWidgets.QInputDialog.getDouble(self, "New cylinder", "Radius (mm)", 5, 0.01, 10000, 2)
        if not ok:
            return
        height, ok = QtWidgets.QInputDialog.getDouble(self, "New cylinder", "Height (mm)", 20, 0.01, 10000, 2)
        if not ok:
            return
        self._run(lambda: self.model.create_primitive("cylinder", name, {"radius": radius, "height": height}))

    def add_sphere(self):
        name = self._unique_name("Sphere")
        radius, ok = QtWidgets.QInputDialog.getDouble(self, "New sphere", "Radius (mm)", 10, 0.01, 10000, 2)
        if ok:
            self._run(lambda: self.model.create_primitive("sphere", name, {"radius": radius}))

    def add_extrude(self):
        profile, ok = QtWidgets.QInputDialog.getItem(self, "New extrude", "Sketch profile",
                                                     ["rectangle", "circle"], 0, False)
        if not ok:
            return
        name = self._unique_name("Extrude")
        if profile == "rectangle":
            dimensions = {}
            for key, label in (("width", "Width"), ("height", "Height"), ("depth", "Extrusion depth")):
                value, ok = QtWidgets.QInputDialog.getDouble(self, "Rectangle extrude", f"{label} (mm)",
                                                             20, 0.01, 10000, 2)
                if not ok:
                    return
                dimensions[key] = value
        else:
            radius, ok = QtWidgets.QInputDialog.getDouble(self, "Circle extrude", "Radius (mm)",
                                                          10, 0.01, 10000, 2)
            if not ok:
                return
            depth, ok = QtWidgets.QInputDialog.getDouble(self, "Circle extrude", "Extrusion depth (mm)",
                                                         20, 0.01, 10000, 2)
            if not ok:
                return
            dimensions = {"radius": radius, "depth": depth}
        self._run(lambda: self.model.create_extrude(name, profile, dimensions))

    def move_feature(self):
        selected = self.tree.selectedItems()
        if len(selected) != 1:
            QtWidgets.QMessageBox.information(self, "Select a feature", "Select one feature in the tree first.")
            return
        source = selected[0].data(0, QtCore.Qt.ItemDataRole.UserRole)
        offsets = []
        for axis in "XYZ":
            value, ok = QtWidgets.QInputDialog.getDouble(self, "Move feature", f"{axis} offset (mm)",
                                                         0, -10000, 10000, 2)
            if not ok:
                return
            offsets.append(value)
        name = self._unique_name("Moved")
        self._run(lambda: self.model.translate_feature(name, source, *offsets))

    def edit_dimension(self):
        selected = self.tree.selectedItems()
        if len(selected) != 1:
            QtWidgets.QMessageBox.information(self, "Select a feature", "Select one dimensioned feature first.")
            return
        feature_id = selected[0].data(0, QtCore.Qt.ItemDataRole.UserRole)
        feature = next((f for f in self.model.feature_tree()["features"] if f["id"] == feature_id), None)
        if not feature or not feature.get("dimensions"):
            QtWidgets.QMessageBox.information(self, "No dimensions", "This feature has no editable dimensions.")
            return
        dimension, ok = QtWidgets.QInputDialog.getItem(self, "Edit dimension", "Dimension",
                                                       list(feature["dimensions"]), 0, False)
        if not ok:
            return
        value, ok = QtWidgets.QInputDialog.getDouble(self, "Edit dimension", f"{dimension} (mm)",
                                                     feature["dimensions"][dimension], 0.01, 10000, 2)
        if ok:
            self._run(lambda: self.model.set_dimension(feature_id, dimension, value))

    def import_step(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import STEP", "", "STEP files (*.step *.stp)")
        if not path:
            return
        stem = os.path.splitext(os.path.basename(path))[0]
        safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in stem)
        if not safe or not safe[0].isalpha():
            safe = "Imported_" + safe
        name = self._unique_name(safe[:64])
        self._run(lambda: self.model.import_step(path, name))

    def import_stl(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import STL mesh", "", "STL meshes (*.stl)")
        if not path:
            return
        stem = os.path.splitext(os.path.basename(path))[0]
        safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in stem)
        if not safe or not safe[0].isalpha():
            safe = "Mesh_" + safe
        name = self._unique_name(safe[:64])
        self._run(lambda: self.model.import_stl(path, name))

    def combine(self, operation):
        selected = self.tree.selectedItems()
        if len(selected) != 2:
            QtWidgets.QMessageBox.information(self, "Select two features", "Select two features in the tree first.")
            return
        left, right = [item.data(0, QtCore.Qt.ItemDataRole.UserRole) for item in selected]
        self._run(lambda: self.model.combine(operation, self._unique_name(operation.title()), left, right))

    def view_fit(self):
        self.view.fit()

    def save(self):
        self._run(self.model.save)
        self.statusBar().showMessage(f"Saved {self.model.path}", 6000)

    def undo(self):
        self._run(self.model.undo)

    def redo(self):
        self._run(self.model.redo)

    def new_project(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "New CADSwarm project", "project.cadswarm",
                                                        "CADSwarm project (*.cadswarm)")
        if path:
            self.model = StandaloneModel(path)
            set_active_project(path)
            self.view.model = self.model
            self.setWindowTitle(f"CADSwarm - {os.path.basename(path)}")
            self._seen_revision = -1
            self.refresh()

    def open_project(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open CADSwarm project", "",
                                                        "CADSwarm project (*.cadswarm)")
        if path:
            self.model = StandaloneModel(path)
            set_active_project(path)
            self.view.model = self.model
            self.setWindowTitle(f"CADSwarm - {os.path.basename(path)}")
            self._seen_revision = -1
            self.refresh()

    def export(self, file_format):
        suffix = ".step" if file_format == "step" else ".stl"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, f"Export {file_format.upper()}",
                                                        os.path.join(os.path.dirname(self.model.path), "exports",
                                                                     "model" + suffix),
                                                        f"{file_format.upper()} (*{suffix})")
        if path:
            self._run(lambda: self.model.export(path, file_format))

    def host_team(self):
        if self._team_host_running():
            QtWidgets.QMessageBox.information(self, "Team is already running",
                                               "Use the existing team-connected cadswarm MCP server for changes.")
            return
        mode_label, ok = QtWidgets.QInputDialog.getItem(self, "Host multiplayer team", "Hosting location",
                                                         ["Local network", "CADSwarm cloud"], 0, False)
        if not ok:
            return
        mode = "local" if mode_label == "Local network" else "maincloud"
        default_db = "cadswarm-" + "".join(ch.lower() if ch.isalnum() else "-"
                                            for ch in os.path.splitext(os.path.basename(self.model.path))[0]).strip("-")
        db, ok = QtWidgets.QInputDialog.getText(self, "Host multiplayer team", "Team database name", text=default_db)
        if not ok:
            return
        db = "".join(ch.lower() if ch.isalnum() else "-" for ch in db).strip("-")
        if not db:
            QtWidgets.QMessageBox.warning(self, "Team name required", "Enter a database name for your team.")
            return
        if getattr(self, "_team_worker", None) and self._team_worker.isRunning():
            QtWidgets.QMessageBox.information(self, "Team setup in progress", "Team setup is already running.")
            return
        self.statusBar().showMessage("Starting the local multiplayer team…")
        self._team_worker = TeamStartWorker(sys.executable, self.model.path, db, mode)
        self._team_worker.progress.connect(lambda line: self.statusBar().showMessage(line, 10000))
        self._team_worker.succeeded.connect(self._team_started)
        self._team_worker.failed.connect(self._team_failed)
        self._team_worker.start()

    def _team_started(self, ticket):
        self._update_team_state()
        self.statusBar().showMessage("Multiplayer team is ready", 10000)
        QtWidgets.QMessageBox.information(
            self, "Team ready",
            "Your multiplayer team is running against this .cadswarm project. This desktop window is now "
            "read-only so it cannot bypass team locks. Edit through the team-connected cadswarm MCP server; "
            "this window will continue to show live model updates.\n\n"
            "Share this invite with teammates or use it with `cadswarm setup` to connect an AI helper:\n\n" + ticket)

    def _team_failed(self, message):
        self.statusBar().showMessage("Team setup failed", 10000)
        QtWidgets.QMessageBox.warning(self, "Could not start team", message)

    def _team_host_running(self):
        status_path = os.path.join(os.path.dirname(self.model.path), ".cadswarm-team", "host-status.json")
        try:
            with open(status_path, encoding="utf-8") as source:
                status = json.load(source)
            pid = int(status.get("pid", 0))
            if not status.get("ready") or not pid:
                return False
            if os.name == "nt":
                import ctypes

                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
                kernel.WaitForSingleObject.restype = ctypes.c_ulong
                kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
                kernel.CloseHandle.restype = ctypes.c_int
                handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                if not handle:
                    return False
                try:
                    return kernel.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT
                finally:
                    kernel.CloseHandle(handle)
            os.kill(pid, 0)
            return True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    project_path = args[0] if args else os.getenv("CADSWARM_STANDALONE_PROJECT", default_project_path())
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("CADSwarm")
    app.setOrganizationName("CADSwarm")
    window = DesktopWindow(project_path)
    window.show()
    return app.exec()
