"""Local MCP tools for the standalone CADSwarm model engine."""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from cadswarm.app.standalone_engine import ModelError, StandaloneModel, active_project_path

INSTRUCTIONS = """You are editing a CADSwarm `.cadswarm` project through its standalone parametric model engine.
All dimensions are millimetres. Use model_status before changing the feature history. Create constrained
line/circle/arc sketches, then extrude, revolve, pocket, or create holes; use fillets/chamfers, patterns,
primitives, placements, and booleans as needed. Solver status and degrees of freedom are included in the
feature tree. STEP and STL are supported for exchange; imported STL remains a mesh reference. This server
refuses model writes while a multiplayer host is active. Use the `cadswarm` team MCP server for shared edits,
task plans, and object locks. It does not start or require FreeCAD."""

mcp = MCPServer("cadswarm-standalone", instructions=INSTRUCTIONS)
PROJECT = active_project_path()
MODEL = StandaloneModel(PROJECT)


def current_model():
    global MODEL, PROJECT
    active = active_project_path()
    if os.path.abspath(active) != os.path.abspath(PROJECT):
        PROJECT = os.path.abspath(active)
        MODEL = StandaloneModel(PROJECT)
    return MODEL


def require_solo_edit(model):
    """Do not let the solo MCP server bypass collaboration locks on a hosted team document."""
    from cadswarm import hosting

    if hosting.team_ready(os.path.dirname(model.path)):
        raise ModelError("This project is hosted by a multiplayer team. Use the team's `cadswarm` MCP server "
                         "so shared object locks and task planning are enforced.")


@mcp.tool()
def model_status() -> dict:
    """Return the project path, revision, units and feature tree."""
    try:
        model = current_model()
        return {"path": PROJECT, **model.feature_tree()}
    except ModelError as exc:
        return {"error": str(exc)}


@mcp.tool()
def undo() -> dict:
    """Undo the last feature or parameter change in the shared standalone project."""
    try:
        model = current_model()
        require_solo_edit(model)
        return {"revision": model.undo()}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def redo() -> dict:
    """Redo the most recently undone change in the shared standalone project."""
    try:
        model = current_model()
        require_solo_edit(model)
        return {"revision": model.redo()}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def create_primitive(kind: str, name: str, dimensions: dict) -> dict:
    """Add a parametric box, cylinder or sphere. Box: length/width/height; cylinder: radius/height; sphere: radius."""
    try:
        model = current_model()
        require_solo_edit(model)
        feature = model.create_primitive(kind, name, dimensions)
        return {"feature": feature, "revision": model.revision}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def create_extrude(name: str, profile: str, dimensions: dict) -> dict:
    """Create a rectangle or circle sketch profile and extrude it along +Z. Rectangle: width/height/depth; circle: radius/depth."""
    try:
        model = current_model()
        require_solo_edit(model)
        feature = model.create_extrude(name, profile, dimensions)
        return {"feature": feature, "revision": model.revision}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def create_sketch(name: str, plane: str, entities: list[dict], constraints: list[dict] | None = None,
                  label: str | None = None) -> dict:
    """Create a solver-backed sketch on XY, XZ or YZ. Lengths are mm and constraint angles are radians."""
    try:
        model = current_model()
        require_solo_edit(model)
        feature = model.create_sketch(name, plane, entities, constraints or [], label)
        return {"feature": feature, "revision": model.revision}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def edit_sketch(name: str, entities: list[dict] | None = None,
                constraints: list[dict] | None = None) -> dict:
    """Replace sketch geometry/constraints and recompute its dependent features."""
    try:
        model = current_model()
        require_solo_edit(model)
        feature = model.update_sketch(name, entities, constraints)
        return {"feature": feature, "revision": model.revision}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def create_sketch_feature(kind: str, name: str, sketch_name: str | None = None,
                          parameters: dict | None = None, source_name: str | None = None,
                          selection: list[int] | None = None, label: str | None = None) -> dict:
    """Create extrude/revolve/pocket/hole/fillet/chamfer or linear/circular pattern features."""
    try:
        model = current_model()
        require_solo_edit(model)
        feature = model.create_sketch_feature(kind, name, sketch_name, parameters or {}, source_name,
                                              selection or [], label)
        return {"feature": feature, "revision": model.revision}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def boolean_feature(operation: str, name: str, left: str, right: str) -> dict:
    """Create a union, subtract or intersect feature from two existing feature names."""
    try:
        model = current_model()
        require_solo_edit(model)
        feature = model.combine(operation, name, left, right)
        return {"feature": feature, "revision": model.revision}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def set_dimension(feature: str, dimension: str, value_mm: float) -> dict:
    """Change one primitive or extrusion dimension and rebuild dependent features."""
    try:
        model = current_model()
        require_solo_edit(model)
        changed = model.set_dimension(feature, dimension, value_mm)
        return {"feature": changed, "revision": model.revision}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def set_expression(feature: str, dimension: str, expression: str | None) -> dict:
    """Bind a feature dimension to safe arithmetic, such as `Params.hole_pitch / 2`, or clear the expression."""
    try:
        model = current_model()
        require_solo_edit(model)
        changed = model.set_expression(feature, dimension, expression)
        return {"feature": changed, "revision": model.revision}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def set_param(alias: str, value_mm: float) -> dict:
    """Set a shared positive dimension in Params, in millimetres."""
    try:
        model = current_model()
        require_solo_edit(model)
        changed = model.set_param(alias, value_mm)
        return {**changed, "revision": model.revision}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def set_feature_parameters(feature: str, parameters: dict) -> dict:
    """Edit feature parameters such as depth, revolve angle, fillet radius, or pattern count."""
    try:
        model = current_model()
        require_solo_edit(model)
        changed = model.set_feature_parameters(feature, parameters)
        return {"feature": changed, "revision": model.revision}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def translate_feature(name: str, source: str, x_mm: float, y_mm: float, z_mm: float) -> dict:
    """Create a positioned copy of a feature, offset along X/Y/Z in millimetres."""
    try:
        model = current_model()
        require_solo_edit(model)
        feature = model.translate_feature(name, source, x_mm, y_mm, z_mm)
        return {"feature": feature, "revision": model.revision}
    except (ModelError, RuntimeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def import_step(path: str, name: str) -> dict:
    """Import a STEP file into the project and keep a project-local copy of the source geometry."""
    try:
        model = current_model()
        require_solo_edit(model)
        feature = model.import_step(path, name)
        return {"feature": feature, "revision": model.revision}
    except (ModelError, RuntimeError, OSError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def import_stl(path: str, name: str) -> dict:
    """Import an STL mesh as a project-owned reference; mesh references are not editable B-rep solids."""
    try:
        model = current_model()
        require_solo_edit(model)
        feature = model.import_stl(path, name)
        return {"feature": feature, "revision": model.revision}
    except (ModelError, RuntimeError, OSError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def export_model(filename: str, file_format: str = "step") -> dict:
    """Export visible solids as STEP or STL. filename must be a simple name in the project's exports folder."""
    name = os.path.basename(filename)
    if not name or name in (".", ".."):
        return {"error": "Provide a filename for the export."}
    if name != filename or "/" in filename or "\\" in filename:
        return {"error": "Use a filename only; exports are restricted to the project exports folder."}
    project = current_model()
    path = os.path.join(os.path.dirname(project.path), "exports", name)
    try:
        return project.export(path, file_format)
    except (ModelError, RuntimeError, OSError) as exc:
        return {"error": str(exc)}


def main():
    mcp.run("stdio")


if __name__ == "__main__":
    main()
