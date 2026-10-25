"""Presses a panel action button offscreen and checks the press feedback and duplicate-press guard.

    python press_gui_check.py <out dir>

Writes press-verified.json. Run by test_press_gui.py.

FreeCAD's offscreen build deadlocks inside QApplication.processEvents on some desktops, so this check
drives the button's state transitions directly instead of pumping the event loop.
"""
import json
import sys
from pathlib import Path

import FreeCADGui as Gui
Gui.showMainWindow()
# FreeCAD puts its Mod folders ahead of PYTHONPATH when it starts, so an installed CadSwarm
# would shadow this checkout. Test the repository, not whatever happens to be installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _name in [n for n in sys.modules if n == "cadswarm" or n.startswith("cadswarm.")]:
    del sys.modules[_name]
from PySide import QtGui  # noqa: E402

from cadswarm.gui import constraints_ui, panel  # noqa: E402

out = Path(sys.argv[1])
checks = {}


def check(name, ok):
    checks[name] = bool(ok)


# A press is one action: a second click before the flash clears is dropped.
pressed = []
b = panel.action_button("Do a thing", "tip", lambda: pressed.append("x"))
b.click()
b.click()
b.click()
check("three_fast_presses_run_once", pressed == ["x"])
check("lit_while_busy", b.property("busy") == "true")
b._clear_busy()  # what the flash timer does
check("clears_when_flash_ends", b.property("busy") != "true")
b.click()
check("later_press_runs_again", pressed == ["x", "x"])

# While a request is in flight the button stays held until it finishes.
async_pressed = []


def start_async():
    async_pressed.append("x")
    panel.SESSION._begin_work()  # what run_async does when an action posts


b2 = panel.action_button("Async", "tip", start_async)
b2.click()
b2.click()
b2.click()
check("held_press_runs_once", async_pressed == ["x"])
check("held_button_is_lit", b2.property("busy") == "true")
panel.SESSION._end_work()
check("clears_when_request_ends", b2.property("busy") != "true")
b2.click()
check("runs_again_after_finish", async_pressed == ["x", "x"])

# Auto-constrain: an impatient double press looks for shared sizes once, not once per click.
requests = []
panel.SESSION.ensure_connected = lambda purpose: True
panel.SESSION.selected_names = lambda: []
panel.SESSION.model = {"objects": []}


def fake_run_async(work, done=None, purpose="do that", failed=None):
    requests.append((done, failed))


panel.SESSION.run_async = fake_run_async
constraints_ui._looking = False
QtGui.QMessageBox.information = staticmethod(lambda *a, **k: None)

constraints_ui.show()
constraints_ui.show()
constraints_ui.show()
check("spam_starts_one_look", len(requests) == 1)
requests[0][0]({"plan": {}})  # the "nothing to constrain" answer must not lock the button forever
constraints_ui.show()
check("can_run_again_after_answer", len(requests) == 2)

(out / "press-verified.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
print(json.dumps(checks, indent=2))
sys.exit(0 if all(checks.values()) else 1)
