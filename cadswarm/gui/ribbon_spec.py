"""The command ribbon's tabs, groups and buttons, as plain data (no Qt, no FreeCAD: unit-testable).

A button runs the first FreeCAD command in ``cmds`` that exists (FreeCAD 1.0 renamed a few, so some list a
fallback), loading ``wb``'s commands first when none is registered yet; or a CADSwarm ``action`` (a panel
action: the AI Build pane, the team chat, the library...). Tab and group order follow the shared layout
(docs/superpowers/research/2026-11-06-pro-cad-ui-and-ai-build.md, item 13), with generic CAD vocabulary.
"""

PD, SK, PART, ASM = "PartDesignWorkbench", "SketcherWorkbench", "PartWorkbench", "AssemblyWorkbench"


def B(id_, label, icon, tip, cmds=(), wb="", action="", arg=""):
    return {"id": id_, "label": label, "icon": icon, "tip": tip, "cmds": tuple(cmds), "wb": wb,
            "action": action, "arg": arg}


RIBBON = [
    ("Features", [
        ("Sketch", [
            B("sketch", "Sketch", "sketch", "Start a sketch on a plane or a flat face", ["Sketcher_NewSketch"], SK),
            B("body", "Body", "body", "A new body: the solid your features build", ["PartDesign_Body"], PD)]),
        ("Sketch-based", [
            B("extrude", "Extrude", "extrude", "Extrude the selected sketch into a solid (boss)",
              ["PartDesign_Pad"], PD),
            B("cut", "Extruded\nCut", "cut", "Cut the selected sketch through the solid", ["PartDesign_Pocket"], PD),
            B("revolve", "Revolve", "revolve", "Revolve the selected sketch around an axis",
              ["PartDesign_Revolution"], PD),
            B("groove", "Revolved\nCut", "groove", "Cut by revolving the sketch", ["PartDesign_Groove"], PD),
            B("sweep", "Sweep", "sweep", "Sweep a profile along a path", ["PartDesign_AdditivePipe"], PD),
            B("loft", "Loft", "loft", "Loft between two or more profiles", ["PartDesign_AdditiveLoft"], PD)]),
        ("Dress-up", [
            B("fillet", "Fillet", "fillet", "Round the selected edges", ["PartDesign_Fillet"], PD),
            B("chamfer", "Chamfer", "chamfer", "Bevel the selected edges", ["PartDesign_Chamfer"], PD),
            B("shell", "Shell", "shell", "Hollow the solid, removing the selected faces", ["PartDesign_Thickness"], PD),
            B("draft", "Draft", "draft", "Taper the selected faces", ["PartDesign_Draft"], PD)]),
        ("Holes", [
            B("hole", "Hole", "hole", "Holes at the sketch's circles (clearance, tapped, counterbored)",
              ["PartDesign_Hole"], PD)]),
        ("Patterns", [
            B("linear_pattern", "Linear\nPattern", "linear_pattern", "Repeat features along a direction",
              ["PartDesign_LinearPattern"], PD),
            B("polar_pattern", "Circular\nPattern", "polar_pattern", "Repeat features around an axis",
              ["PartDesign_PolarPattern"], PD),
            B("mirror", "Mirror", "mirror", "Mirror features about a plane", ["PartDesign_Mirrored"], PD)]),
        ("Bodies", [
            B("boolean", "Boolean", "boolean", "Combine, subtract or intersect bodies", ["Part_Boolean"], PART),
            B("fuse", "Union", "fuse", "Join the selected shapes into one", ["Part_Fuse"], PART),
            B("common", "Intersect", "common", "Keep only where the selected shapes overlap", ["Part_Common"], PART)]),
        ("Reference", [
            B("plane", "Plane", "plane", "A reference plane", ["PartDesign_Plane"], PD),
            B("axis", "Axis", "axis", "A reference axis", ["PartDesign_Line"], PD),
            B("point", "Point", "point", "A reference point", ["PartDesign_Point"], PD)]),
    ]),
    ("Sketch", [
        ("Sketch", [
            B("sk_new", "Sketch", "sketch", "Start a sketch on a plane or a flat face", ["Sketcher_NewSketch"], SK),
            B("sk_finish", "Finish\nSketch", "finish", "Close the sketch you are editing", ["Sketcher_LeaveSketch"], SK)]),
        ("Draw", [
            B("sk_line", "Line", "line", "Draw lines", ["Sketcher_CreateLine"], SK),
            B("sk_poly", "Polyline", "polyline", "Draw connected lines and arcs", ["Sketcher_CreatePolyline"], SK),
            B("sk_rect", "Rectangle", "rectangle", "Draw a rectangle", ["Sketcher_CreateRectangle"], SK),
            B("sk_circle", "Circle", "circle", "Draw a circle from its centre", ["Sketcher_CreateCircle"], SK),
            B("sk_arc", "Arc", "arc", "Draw an arc from its centre", ["Sketcher_CreateArc"], SK),
            B("sk_slot", "Slot", "slot", "Draw a slot", ["Sketcher_CreateSlot"], SK),
            B("sk_polygon", "Polygon", "polygon", "Draw a regular polygon", ["Sketcher_CreateRegularPolygon",
                                                                           "Sketcher_CreateHexagon"], SK)]),
        ("Modify", [
            B("sk_trim", "Trim", "trim", "Trim an edge back to its intersections", ["Sketcher_Trimming"], SK),
            B("sk_offset", "Offset", "offset", "Offset the selected edges", ["Sketcher_Offset"], SK),
            B("sk_external", "Use Edges", "external", "Bring in edges of the model as references",
              ["Sketcher_External"], SK),
            B("sk_construction", "Construction", "construction", "Toggle construction geometry",
              ["Sketcher_ToggleConstruction"], SK)]),
        ("Constrain", [
            B("sk_dimension", "Dimension", "dimension", "Dimension the selection (length, radius, angle)",
              ["Sketcher_Dimension", "Sketcher_ConstrainDistance"], SK),
            B("sk_coincident", "Coincident", "coincident", "Make points meet",
              ["Sketcher_ConstrainCoincidentUnified", "Sketcher_ConstrainCoincident"], SK),
            B("sk_horizontal", "Horizontal", "horizontal", "Make lines horizontal",
              ["Sketcher_ConstrainHorizontal"], SK),
            B("sk_vertical", "Vertical", "vertical", "Make lines vertical", ["Sketcher_ConstrainVertical"], SK)]),
    ]),
    ("Evaluate", [
        ("Measure", [
            B("measure", "Measure", "measure", "Measure distances, angles, lengths and areas",
              ["Std_Measure", "Part_Measure_Linear"], PART),
            B("geometry", "Check\nGeometry", "geometry", "Check the selected shapes for defects",
              ["Part_CheckGeometry"], PART)]),
        ("Interference", [
            B("interference", "Interference", "interference", "Find parts that overlap",
              ["CadSwarm_Interference"])]),
        ("Section", [
            B("section", "Section\nView", "section", "Cut the view with a clipping plane",
              ["Std_ToggleClipPlane"])]),
        ("Printability", [
            B("printability", "Printability", "printability", "Check the parts fit the printer, are closed and "
              "have printable walls", action="printability")]),
        ("Simulation", [
            B("simulate", "Simulate", "simulate", "Check how a part holds up under a load", ["CadSwarm_RunFea"]),
            B("reports", "Reports", "plan", "The strength checks your team ran", ["CadSwarm_FeaReports"]),
            B("lighten", "Lighten", "lighten", "Fill the part with a lattice that keeps its stiffness",
              ["CadSwarm_Lighten"]),
            B("optimize", "Optimize", "check", "Optimize the selected part's material", ["CadSwarm_OptimizePart"])]),
    ]),
    ("Assembly", [
        ("Components", [
            B("asm_new", "Assembly", "assembly", "Start a new assembly", ["Assembly_CreateAssembly"], ASM),
            B("asm_insert", "Insert\nComponent", "insert", "Insert a part into the assembly",
              ["Assembly_InsertLink", "Assembly_InsertNewPart"], ASM),
            B("asm_fix", "Fix", "fixed", "Fix a component in place", ["Assembly_CreateJointFixed",
                                                                    "Assembly_ToggleGrounded"], ASM)]),
        ("Mate", [
            B("asm_revolute", "Revolute", "revolute", "A turning mate between two components",
              ["Assembly_CreateJointRevolute"], ASM),
            B("asm_slider", "Slider", "slider", "A sliding mate between two components",
              ["Assembly_CreateJointSlider"], ASM),
            B("asm_auto", "Auto\nConstrain", "constrain", "Bind the sizes and fits the model repeats to shared sizes",
              ["CadSwarm_Constrain"])]),
        ("Move", [
            B("asm_move", "Move\nComponent", "move", "Move or rotate the selection", ["Std_TransformManip",
                                                                                      "Std_Placement"]),
            B("asm_solve", "Solve", "solve", "Solve the assembly's mates", ["Assembly_SolveAssembly"], ASM),
            B("asm_explode", "Exploded\nView", "explode", "Explode the assembly", ["Assembly_CreateView"], ASM)]),
        ("Check", [
            B("asm_interference", "Interference", "interference", "Find parts that overlap",
              ["CadSwarm_Interference"])]),
    ]),
    ("Library", [
        ("Find", [
            B("lib_open", "Library", "library", "Place screws, bearings, inserts or your team's own parts",
              ["CadSwarm_Library"]),
            B("lib_recommend", "Recommend", "recommend", "Search the library for what a part needs",
              action="library", arg="")]),
        ("Standard parts", [
            B("lib_fasteners", "Fasteners", "screw", "Screws, bolts, nuts and washers", action="library",
              arg="fastener"),
            B("lib_bearings", "Bearings", "bearing", "Ball bearings and bushings", action="library", arg="bearing"),
            B("lib_inserts", "Inserts", "insert_nut", "Heat-set inserts", action="library", arg="insert"),
            B("lib_magnets", "Magnets", "magnet", "Magnets and other hardware", action="library", arg="hardware"),
            B("lib_boards", "Boards", "board", "Circuit boards and electronics", action="library", arg="board"),
            B("lib_motors", "Motors", "motor", "Motors and motion parts", action="library", arg="motion")]),
        ("Team rules", [
            B("lib_settings", "Precision", "settings", "Clearances, fits and precision for the whole team",
              ["CadSwarm_Settings"]),
            B("lib_style", "Design\nStyle", "style", "The design rules CADSwarm learned from your team",
              ["CadSwarm_Style"])]),
    ]),
    ("AI Build", [
        ("Plan", [
            B("ai_plan", "New Plan", "plan", "Describe what to build; the team plans it step by step",
              action="ai_plan"),
            B("ai_next", "Run Next\nStep", "run_next", "Ask a free helper to build the next ready step",
              action="ai_next"),
            B("ai_all", "Run All\nReady", "run_all", "Every free helper takes a ready step", action="ai_all")]),
        ("Control", [
            B("ai_pause", "Pause", "pause", "Helpers finish the op they are on and take no new step",
              action="ai_pause"),
            B("ai_replan", "Re-plan\nfrom Step", "replan", "Change the plan from one step on", action="ai_replan")]),
        ("Assign", [
            B("ai_assign", "Assign\nStep", "assign", "Give one step to a helper or a teammate", action="ai_assign")]),
        ("Review", [
            B("ai_review", "Review", "review", "Ask the reviewer to check the model", action="ai_review")]),
        ("Team", [
            B("team_chat", "Chat", "chat", "Tell an AI helper what to do; it answers in the chat",
              ["CadSwarm_TellHelper"]),
            B("team_tasks", "Tasks", "tasks", "Every task and who has it", action="tasks"),
            B("team_helper", "New\nHelper", "helper", "Connect another AI helper", ["CadSwarm_AddHelper"]),
            B("team_invite", "Invite", "invite", "Copy an invite code for a teammate", ["CadSwarm_Invite"]),
            B("team_lock", "Hold", "lock", "Only you can change the selected parts", ["CadSwarm_Lock"]),
            B("team_release", "Let Go", "release", "Anyone can change the selected parts again",
              ["CadSwarm_Release"]),
            B("team_edit", "Edit\nPart", "edit_part", "Change a part yourself and share the change",
              ["CadSwarm_EditPart"]),
            B("team_export", "Save\nSTL/STEP", "export", "Save the shared model as STL or STEP",
              ["CadSwarm_Export"])]),
    ]),
    ("View", [
        ("Orientation", [
            B("v_fit", "Zoom to\nFit", "fit", "Fit the whole model in the view", ["Std_ViewFitAll"]),
            B("v_iso", "Isometric", "iso", "Isometric view", ["Std_ViewIsometric"]),
            B("v_front", "Front", "front", "Front view", ["Std_ViewFront"]),
            B("v_top", "Top", "top", "Top view", ["Std_ViewTop"]),
            B("v_right", "Right", "right", "Right view", ["Std_ViewRight"]),
            B("v_rear", "Back", "rear", "Back view", ["Std_ViewRear"]),
            B("v_bottom", "Bottom", "bottom", "Bottom view", ["Std_ViewBottom"]),
            B("v_left", "Left", "left", "Left view", ["Std_ViewLeft"])]),
        ("Display", [
            B("v_shaded_edges", "Shaded\nEdges", "shaded_edges", "Shaded with edges",
              ["Std_DrawStyleFlatLines"]),
            B("v_shaded", "Shaded", "shaded", "Shaded, no edges", ["Std_DrawStyleShaded"]),
            B("v_hidden", "Hidden\nLines", "hidden_lines", "Hidden lines removed", ["Std_DrawStyleHiddenLine"]),
            B("v_wire", "Wireframe", "wireframe", "Wireframe", ["Std_DrawStyleWireframe"]),
            B("v_section", "Section", "section", "Cut the view with a clipping plane", ["Std_ToggleClipPlane"])]),
        ("Show", [
            B("v_hide", "Hide /\nShow", "visibility", "Hide or show the selection", ["Std_ToggleVisibility"]),
            B("v_showall", "Show\nAll", "show", "Show every object", ["Std_AllOnDisplay", "Std_ShowObjects"]),
            B("v_pointers", "Helper\nArrows", "pointers", "Show where each helper is working",
              ["CadSwarm_Pointers"])]),
        ("Camera", [
            B("v_persp", "Perspective", "perspective", "Perspective camera", ["Std_PerspectiveCamera"]),
            B("v_ortho", "Orthographic", "ortho", "Orthographic camera", ["Std_OrthographicCamera"])]),
        ("Workspace", [
            B("v_panel", "Task\nPane", "panel", "Show the CADSwarm task pane", ["CadSwarm_Panel"]),
            B("v_theme", "Light /\nDark", "theme", "Switch between the light and the dark look", action="toggle_dark"),
            B("v_classic", "Classic\nLook", "classic", "Go back to FreeCAD's own look while CADSwarm is active",
              action="toggle_pro")]),
    ]),
]

QUICK = [
    B("q_new", "New", "new", "New document", ["Std_New"]),
    B("q_open", "Open", "open", "Open a document", ["Std_Open"]),
    B("q_save", "Save", "save", "Save", ["Std_Save"]),
    B("q_undo", "Undo", "undo", "Undo", ["Std_Undo"]),
    B("q_redo", "Redo", "redo", "Redo", ["Std_Redo"]),
    B("q_rebuild", "Rebuild", "rebuild", "Rebuild (recompute) the model", ["Std_Refresh"]),
]

TAB_NAMES = [name for name, _ in RIBBON]


def buttons():
    """Every ribbon button, with the tab and group it sits in."""
    out = []
    for tab, groups in RIBBON:
        for group, items in groups:
            for b in items:
                out.append(dict(b, tab=tab, group=group))
    return out


def resolve(button, available):
    """The FreeCAD command a button runs, given the registered command names ("" = none of them)."""
    have = set(available or ())
    return next((c for c in button.get("cmds") or () if c in have), "")


def workbenches_needed(available):
    """The workbenches whose commands the ribbon wants but which have not registered them yet."""
    out = []
    for b in buttons():
        if b["cmds"] and b["wb"] and not resolve(b, available) and b["wb"] not in out:
            out.append(b["wb"])
    return out


def search(query, limit=12):
    """Ribbon buttons matching a command-search query (label, tip or tab), best first."""
    q = " ".join((query or "").lower().split())
    if not q:
        return []
    scored = []
    for i, b in enumerate(buttons()):
        label = " ".join(b["label"].lower().split())
        hay = f"{label} {b['tip'].lower()} {b['tab'].lower()} {b['group'].lower()}"
        if label.startswith(q):
            score = 0
        elif q in label:
            score = 1
        elif all(w in hay for w in q.split()):
            score = 2
        else:
            continue
        scored.append((score, i, b))
    scored.sort(key=lambda x: (x[0], x[1]))
    seen, out = set(), []
    for _, _, b in scored:
        key = (b["label"], b["cmds"], b["action"], b["arg"])
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out[:limit]
