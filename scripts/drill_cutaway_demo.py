r"""Build a parametric cutaway cordless driver as a CADSwarm demo and integration fixture.

Run from CADSwarm's FreeCAD Python console:
    import sys
    sys.path.insert(0, r"C:\Users\sachc\Projects\cadswarm-fc\scripts")
    import drill_cutaway_demo
    report = drill_cutaway_demo.main(r"C:\Users\sachc\Projects\cadswarm-fc\demo\cordless_driver_cutaway.FCStd")

The model is a representative mechanical study based on the supplied image, not manufacturing data or a
reverse-engineered product. It uses native Part shapes with a named assembly/component tree.
"""
from __future__ import annotations

import json
import os


BUILD_VERSION = "1.0"
DIMENSIONS = {
    "bit_length": 62.0,
    "housing_length": 203.0,
    "housing_half_width": 34.0,
    "motor_radius": 22.0,
    "motor_length": 47.0,
    "gearbox_radius": 31.0,
    "gearbox_length": 60.0,
    "handle_drop": 88.0,
    "shell_thickness": 7.0,
}
SUBASSEMBLIES = (
    "ChuckAndSpindle",
    "PlanetaryGearbox",
    "ElectricMotor",
    "TriggerAndBatteryGrip",
    "CutawayHousing",
    "Fasteners",
)


def _rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


PALETTE = {
    "graphite": _rgb("#343A40"),
    "dark": _rgb("#20262B"),
    "steel": _rgb("#8C969F"),
    "light_steel": _rgb("#C2C9CE"),
    "copper": _rgb("#C98A4B"),
    "orange": _rgb("#C2410C"),
    "sage": _rgb("#77927D"),
    "rubber": _rgb("#454B50"),
    "yellow": _rgb("#E4BB55"),
}


def _cyl_x(radius, length, x, y=0.0, z=0.0, App=None, Part=None):
    return Part.makeCylinder(radius, length, App.Vector(x, y, z), App.Vector(1, 0, 0))


def _cyl_y(radius, length, x, y, z=0.0, App=None, Part=None):
    return Part.makeCylinder(radius, length, App.Vector(x, y, z), App.Vector(0, 1, 0))


def _profile(points, z, thickness, App, Part):
    vertices = [App.Vector(x, y, z) for x, y in points]
    vertices.append(vertices[0])
    return Part.Face(Part.makePolygon(vertices)).extrude(App.Vector(0, 0, thickness))


def _group(doc, parent, name, label):
    group = doc.addObject("App::Part", name)
    group.Label = label
    parent.addObject(group)
    return group


def _feature(doc, group, name, label, shape, color, role, transparency=0):
    obj = doc.addObject("Part::Feature", name)
    obj.Label = label
    obj.Shape = shape
    group.addObject(obj)
    view = obj.ViewObject
    if view is not None:
        view.ShapeColor = color
        view.LineColor = (0.12, 0.14, 0.16)
        view.DisplayMode = "Flat Lines"
        view.Transparency = transparency
    obj.addProperty("App::PropertyString", "ComponentRole", "CADSwarm")
    obj.ComponentRole = role
    return obj


def _gear(x, cy, cz, radius, length, teeth, App, Part, tooth_depth=1.0, bore_radius=0.0):
    """Simplified visible gear with editable teeth and an optional clear axle bore."""
    pieces = [_cyl_x(radius, length, x, cy, cz, App, Part)]
    tooth_width = max(2.0, radius * 0.34)
    for index in range(teeth):
        tooth = Part.makeBox(
            length, tooth_depth, tooth_width,
            App.Vector(x, radius - tooth_depth / 2.0, -tooth_width / 2.0),
        )
        tooth.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), index * 360.0 / teeth)
        tooth.translate(App.Vector(0, cy, cz))
        pieces.append(tooth)
    shape = Part.makeCompound(pieces)
    if bore_radius:
        cutter = _cyl_x(bore_radius, length + 2, x - 1, cy, cz, App, Part)
        shape = shape.cut(cutter)
    return shape


def build_document(document_name="CordlessDriverCutaway"):
    """Create a new native FreeCAD document and return it."""
    try:
        import FreeCAD as App
        import Part
    except ImportError as exc:
        raise RuntimeError("Run inside FreeCAD or CADSwarm's bundled Python environment.") from exc

    doc = App.newDocument(document_name)
    root = doc.addObject("App::Part", "CordlessDriver")
    root.Label = "Cordless Driver · Cutaway Study"
    root.addProperty("App::PropertyString", "ModelPurpose", "CADSwarm")
    root.ModelPurpose = "Parametric CADSwarm smoke model inspired by the supplied cutaway reference."
    root.addProperty("App::PropertyString", "BuildVersion", "CADSwarm")
    root.BuildVersion = BUILD_VERSION
    root.addProperty("App::PropertyLength", "OverallLength", "Design")
    root.OverallLength = 314.0
    root.addProperty("App::PropertyLength", "HousingWidth", "Design")
    root.HousingWidth = 68.0

    chuck = _group(doc, root, "ChuckAndSpindle", "01 · Chuck and spindle")
    gearbox = _group(doc, root, "PlanetaryGearbox", "02 · Planetary gearbox")
    motor = _group(doc, root, "ElectricMotor", "03 · Electric motor")
    grip = _group(doc, root, "TriggerAndBatteryGrip", "04 · Trigger and battery grip")
    housing = _group(doc, root, "CutawayHousing", "05 · Cutaway housing")
    fasteners = _group(doc, root, "Fasteners", "06 · Fasteners")

    # Chuck, collars and spindle. The bit points toward -X.
    _feature(doc, chuck, "DriverBit", "Driver bit · 62 mm",
             _cyl_x(3, 62, -62, App=App, Part=Part), PALETTE["steel"], "Hex driver bit")
    _feature(doc, chuck, "BitHex", "Hex shank",
             Part.makeBox(14, 6, 6, App.Vector(-14, -3, -3)), PALETTE["light_steel"], "Bit retention shank")
    _feature(doc, chuck, "ChuckBody", "Keyless chuck body",
             Part.makeCone(12, 17, 24, App.Vector(0, 0, 0), App.Vector(1, 0, 0)),
             PALETTE["graphite"], "Three-jaw chuck envelope")
    _feature(doc, chuck, "ChuckSleeve", "Grip sleeve",
             _cyl_x(18.5, 9, 12, App=App, Part=Part), PALETTE["rubber"], "Textured chuck sleeve")
    _feature(doc, chuck, "ChuckCollar", "Torque collar",
             _cyl_x(20, 5, 35, App=App, Part=Part), PALETTE["steel"], "Torque clutch collar")
    _feature(doc, chuck, "OutputSpindle", "Output spindle",
             _cyl_x(5, 77, 26, App=App, Part=Part), PALETTE["light_steel"], "Output shaft")
    _feature(doc, chuck, "SpindleBearing", "Spindle bearing",
             _cyl_x(12, 8, 44, App=App, Part=Part), PALETTE["dark"], "Spindle support bearing")

    # Three-stage visual planetary reduction.
    ring_outer = _cyl_x(30, 58, 53, App=App, Part=Part)
    ring_inner = _cyl_x(28, 60, 52, App=App, Part=Part)
    _feature(doc, gearbox, "GearcaseRing", "Gearcase ring",
             ring_outer.cut(ring_inner), PALETTE["steel"], "Sectioned gearbox ring")
    _feature(doc, gearbox, "GearStageFront", "Front carrier plate",
             _cyl_x(27, 3.5, 57, App=App, Part=Part), PALETTE["dark"], "Planet carrier")
    _feature(doc, gearbox, "GearStageRear", "Rear carrier plate",
             _cyl_x(27, 3.5, 105, App=App, Part=Part), PALETTE["dark"], "Planet carrier")
    _feature(doc, gearbox, "SunGear", "Sun gear · 12 tooth",
             _gear(66, 0, 0, 6, 44, 12, App, Part, tooth_depth=1.0, bore_radius=4.8), PALETTE["orange"], "First-stage sun gear")
    planet_centers = ((16.0, 0.0), (-8.0, 13.86), (-8.0, -13.86))
    for index, (cy, cz) in enumerate(planet_centers, 1):
        _feature(doc, gearbox, "PlanetGear" + str(index), "Planet gear " + str(index),
                 _gear(70, cy, cz, 7.5, 34, 10, App, Part, tooth_depth=1.0, bore_radius=2.8), PALETTE["copper"], "Planetary reduction gear")
    pins = [_cyl_x(2.2, 39, 67, y, z, App, Part) for y, z in planet_centers]
    _feature(doc, gearbox, "CarrierPins", "Carrier pins", Part.makeCompound(pins),
             PALETTE["light_steel"], "Planet gear pins")
    _feature(doc, gearbox, "ReductionOutput", "Reduction output hub",
             _cyl_x(10, 14, 108, App=App, Part=Part), PALETTE["light_steel"], "Gearbox output hub")

    # Motor can, rotor, copper windings and commutator.
    motor_can = _cyl_x(22, 45, 140, App=App, Part=Part).cut(_cyl_x(20.8, 45.2, 139.9, App=App, Part=Part))
    _feature(doc, motor, "MotorCan", "Motor stator can · hollow",
             motor_can, PALETTE["dark"], "Hollow stator can exposes the internal rotor")
    front_bell = _cyl_x(24, 4, 136, App=App, Part=Part).cut(_cyl_x(12.5, 4.2, 135.9, App=App, Part=Part))
    rear_bell = _cyl_x(24, 4, 185, App=App, Part=Part).cut(_cyl_x(12.5, 4.2, 184.9, App=App, Part=Part))
    _feature(doc, motor, "MotorEndBellFront", "Front end bell",
             front_bell, PALETTE["steel"], "Hollow motor bearing plate")
    _feature(doc, motor, "MotorEndBellRear", "Rear end bell",
             rear_bell, PALETTE["steel"], "Hollow motor bearing plate")
    rotor_core = _cyl_x(12, 41, 142, App=App, Part=Part).cut(_cyl_x(4.8, 41.2, 141.9, App=App, Part=Part))
    _feature(doc, motor, "RotorCore", "Laminated rotor · bored",
             rotor_core, PALETTE["steel"], "Hollow rotor stack around the armature shaft")
    _feature(doc, motor, "ArmatureShaft", "Armature shaft",
             _cyl_x(4, 83, 130, App=App, Part=Part), PALETTE["light_steel"], "Motor shaft to gearbox")
    coils = [_cyl_x(2.7, 35, 145, y, z, App, Part)
             for y, z in ((17.8, 0), (8.9, 15.4), (-8.9, 15.4), (-17.8, 0), (-8.9, -15.4), (8.9, -15.4))]
    _feature(doc, motor, "CopperWindings", "Copper field windings",
             Part.makeCompound(coils), PALETTE["copper"], "Six visible motor windings")
    _feature(doc, motor, "Commutator", "Commutator",
             _cyl_x(8, 8, 181, App=App, Part=Part).cut(_cyl_x(4.8, 8.2, 180.9, App=App, Part=Part)), PALETTE["orange"], "Hollow commutator around the shaft")
    fan_pieces = [_cyl_x(15, 3, 190, App=App, Part=Part)]
    for angle in (0, 90, 180, 270):
        blade = Part.makeBox(3, 2, 25, App.Vector(190, -1, -12.5))
        blade.rotate(App.Vector(190, 0, 0), App.Vector(1, 0, 0), angle)
        fan_pieces.append(blade)
    _feature(doc, motor, "CoolingFan", "Motor cooling fan",
             Part.makeCompound(fan_pieces), PALETTE["light_steel"], "Cooling fan")

    # Trigger switch, direction selector and three-cell grip mockup.
    _feature(doc, grip, "TriggerSwitch", "Trigger switch",
             Part.makeBox(20, 12, 25, App.Vector(192, -39, -12.5)),
             PALETTE["dark"], "Variable-speed switch block")
    trigger = Part.makeBox(5, 24, 24, App.Vector(201, -47, -12))
    trigger.rotate(App.Vector(201, -47, 0), App.Vector(0, 0, 1), -13)
    _feature(doc, grip, "TriggerPaddle", "Trigger paddle",
             trigger, PALETTE["orange"], "User speed trigger")
    _feature(doc, grip, "ReverseSelector", "Forward / reverse selector",
             Part.makeBox(13, 4, 9, App.Vector(192, -29, -4.5)),
             PALETTE["yellow"], "Direction selector")
    cells = [_cyl_y(6.8, 39, 224, -83, z, App, Part) for z in (-14, 0, 14)]
    _feature(doc, grip, "BatteryCells", "Battery cells × 3",
             Part.makeCompound(cells), PALETTE["steel"], "Three-cell battery mockup")
    _feature(doc, grip, "BatteryTerminal", "Battery terminal block",
             Part.makeBox(27, 7, 31, App.Vector(211, -94, -15.5)),
             PALETTE["yellow"], "Battery contacts")
    grip_outline = [
        (202, -38), (226, -39), (244, -57), (246, -77),
        (235, -87), (210, -84), (198, -65), (196, -47),
    ]
    _feature(doc, grip, "GripInsertNear", "Soft grip · near side",
             _profile(grip_outline, 24, 4, App, Part), PALETTE["rubber"], "Elastomer grip insert")
    _feature(doc, grip, "GripInsertFar", "Soft grip · far side",
             _profile(grip_outline, -28, 4, App, Part), PALETTE["rubber"], "Elastomer grip insert")

    # Two side shells, with a large near-side window exposing the mechanism in the default view.
    outline = [
        (49, -21), (58, -33), (118, -36), (139, -30), (158, -28), (181, -32),
        (194, -40), (196, -56), (205, -70), (222, -87), (241, -84), (252, -69),
        (250, -52), (236, -35), (238, -17), (239, 22), (229, 37), (207, 42),
        (183, 37), (163, 34), (146, 38), (128, 42), (73, 39), (57, 31),
    ]
    window = [
        (62, -15), (72, -25), (118, -28), (138, -23), (158, -20), (184, -24),
        (190, -34), (193, -52), (203, -66), (220, -78), (234, -74), (241, -65),
        (238, -55), (224, -37), (225, -16), (226, 18), (218, 27), (202, 29),
        (182, 25), (162, 22), (145, 27), (127, 32), (77, 29), (65, 20),
    ]
    far_shell = _profile(outline, -34, 7, App, Part)
    near_shell = _profile(outline, 27, 7, App, Part)
    near_cutaway = near_shell.cut(_profile(window, 26, 9, App, Part))
    _feature(doc, housing, "HousingFarShell", "Housing · far side", far_shell,
             PALETTE["graphite"], "Closed far-side shell", 12)
    _feature(doc, housing, "HousingNearCutaway", "Housing · near-side cutaway", near_cutaway,
             PALETTE["graphite"], "Open window exposes the internals", 4)
    webs = Part.makeCompound([
        Part.makeBox(48, 5, 4, App.Vector(65, 26, -2)),
        Part.makeBox(42, 5, 4, App.Vector(113, -31, -2)),
    ])
    _feature(doc, housing, "GearboxSupportWeb", "Gearbox support webs", webs,
             PALETTE["sage"], "Internal structural webbing")
    cradle = Part.makeCompound([
        Part.makeBox(44, 4, 5, App.Vector(139, 22, -2.5)),
        Part.makeBox(44, 4, 5, App.Vector(139, -26, -2.5)),
    ])
    _feature(doc, housing, "MotorCradle", "Motor cradle rails", cradle,
             PALETTE["sage"], "Motor location rails")

    screws = [_cyl_x(2.2, 3, x, y, 31.2, App, Part)
              for x, y in ((72, 33), (130, 35), (185, 31), (233, 23), (217, -48), (232, -72))]
    _feature(doc, fasteners, "HousingScrews", "Housing fasteners × 6",
             Part.makeCompound(screws), PALETTE["light_steel"], "Case fasteners")
    rings = [
        _cyl_x(18.9, 1.6, 23, App=App, Part=Part).cut(_cyl_x(17.4, 2, 22.8, App=App, Part=Part)),
        _cyl_x(20.5, 1.5, 40, App=App, Part=Part).cut(_cyl_x(18.7, 2, 39.8, App=App, Part=Part)),
    ]
    _feature(doc, fasteners, "RetainingRings", "Chuck retaining rings",
             Part.makeCompound(rings), PALETTE["steel"], "Chuck retaining rings")

    doc.recompute()
    try:
        import FreeCADGui as Gui
        active = Gui.activeDocument() if App.GuiUp else None
        if active is not None and active.activeView() is not None:
            active.activeView().viewIsometric()
            active.activeView().fitAll()
    except ImportError:
        pass
    return doc


def inspect_document(doc):
    """Validate editable leaf solids, assembly membership and selected internal clearances."""
    components = []
    invalid = []
    leaf_names = set()
    for obj in doc.Objects:
        if obj.TypeId != "Part::Feature":
            continue
        leaf_names.add(obj.Name)
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            invalid.append(obj.Name)
            continue
        volume = float(shape.Volume)
        solids = len(shape.Solids)
        valid = bool(shape.isValid()) and solids > 0 and volume > 0
        if not valid:
            invalid.append(obj.Name)
        components.append({
            "name": obj.Name,
            "label": obj.Label,
            "solids": solids,
            "volume_mm3": round(volume, 3),
            "valid": valid,
        })

    missing = []
    groups = []
    for name in SUBASSEMBLIES:
        group = doc.getObject(name)
        if group is None:
            missing.append(name)
            continue
        children = [child for child in group.Group if child.TypeId == "Part::Feature"]
        bad_children = [child.Name for child in children if child.Name in invalid]
        valid = bool(children) and not bad_children
        groups.append({"name": name, "children": [child.Name for child in children], "valid": valid})

    clearance_checks = []

    def check_clearance(first, second, label=None):
        a = doc.getObject(first)
        b = doc.getObject(second)
        try:
            common = a.Shape.common(b.Shape)
            volume = float(common.Volume)
            passed = volume <= 0.01
            clearance_checks.append({
                "pair": label or first + " / " + second,
                "common_volume_mm3": round(volume, 6),
                "passed": passed,
            })
        except Exception as exc:
            clearance_checks.append({
                "pair": label or first + " / " + second,
                "common_volume_mm3": None,
                "passed": False,
                "error": str(exc),
            })

    for index in range(1, 4):
        check_clearance("SunGear", "PlanetGear" + str(index))
        check_clearance("PlanetGear" + str(index), "CarrierPins")
    for first, second in ((1, 2), (1, 3), (2, 3)):
        check_clearance("PlanetGear" + str(first), "PlanetGear" + str(second))
    for other in ("RotorCore", "ArmatureShaft", "CopperWindings"):
        check_clearance("MotorCan", other)
    for other in ("MotorCan", "BatteryCells"):
        check_clearance("HousingNearCutaway", other)
    check_clearance("BatteryCells", "BatteryTerminal")
    cell_solids = doc.getObject("BatteryCells").Shape.Solids
    for first, second in ((0, 1), (0, 2), (1, 2)):
        volume = float(cell_solids[first].common(cell_solids[second]).Volume)
        clearance_checks.append({
            "pair": "Battery cell %d / %d" % (first + 1, second + 1),
            "common_volume_mm3": round(volume, 6),
            "passed": volume <= 0.01,
        })

    failed_clearances = [item["pair"] for item in clearance_checks if not item["passed"]]
    return {
        "build_version": BUILD_VERSION,
        "document": doc.Name,
        "component_count": len(components),
        "missing_subassemblies": missing,
        "invalid_components": invalid,
        "groups": groups,
        "clearance_checks": clearance_checks,
        "failed_clearances": failed_clearances,
        "components": components,
        "ok": not invalid and not missing and all(group["valid"] for group in groups)
              and not failed_clearances and len(components) >= 30,
    }

def main(output_path=None, report_path=None):
    """Build and inspect the model; optionally save FCStd and a JSON validity report."""
    doc = build_document()
    report = inspect_document(doc)
    if output_path and report["ok"]:
        output_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        doc.saveAs(output_path)
        report["output"] = output_path
    if report_path:
        report_path = os.path.abspath(report_path)
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
    summary = {key: report[key] for key in (
        "build_version", "document", "component_count", "invalid_components",
        "missing_subassemblies", "failed_clearances", "ok", "output",
    ) if key in report}
    print(json.dumps(summary, indent=2))
    return report


