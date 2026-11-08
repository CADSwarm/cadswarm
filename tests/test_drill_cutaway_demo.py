from pathlib import Path
import importlib.util


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "drill_cutaway_demo.py"
SPEC = importlib.util.spec_from_file_location("drill_cutaway_demo", MODULE_PATH)
drill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drill)


def test_cutaway_demo_declares_expected_subassemblies():
    assert drill.SUBASSEMBLIES == (
        "ChuckAndSpindle",
        "PlanetaryGearbox",
        "ElectricMotor",
        "TriggerAndBatteryGrip",
        "CutawayHousing",
        "Fasteners",
    )


def test_cutaway_demo_dimensions_are_positive_and_section_window_is_intended():
    assert all(value > 0 for value in drill.DIMENSIONS.values())
    assert drill.DIMENSIONS["motor_radius"] < drill.DIMENSIONS["gearbox_radius"]
    assert drill.DIMENSIONS["shell_thickness"] < drill.DIMENSIONS["housing_half_width"]


def test_cutaway_demo_brand_palette_is_rgb():
    assert drill.PALETTE["orange"] == (194 / 255, 65 / 255, 12 / 255)
    assert all(len(color) == 3 and all(0 <= c <= 1 for c in color) for color in drill.PALETTE.values())

