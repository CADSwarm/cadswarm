"""The CadSwarm panel in a real FreeCAD window, joined to a live team, exercising the patched flows.

    freecad_panel_live.py <out dir> <project folder> <host data folder>

Runs under FreeCAD's own python (it calls Gui.showMainWindow()). It imports the panel from the
checkout, not from the installed workbench, so the patch under test is what runs. It then:

  1. joins the team as the person, shows the docked panel and grabs a screenshot;
  2. sends "@team <brief>" through the real send path and records what the chat and the outbox hold
     (the bug left a "sending..." row that never resolved);
  3. waits for the host to plan, build and review the brief with real FreeCAD ops, and grabs the
     finished state;
  4. calls the same hosting.set_agent_client the setup guide's picker calls and reads host.json back.

Writes freecad-report.json and freecad-<n>-*.png. Prints JSON so the caller can act on it.
"""
import json
import os
import sys
import time
from pathlib import Path

import FreeCADGui as Gui
Gui.showMainWindow()

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT)
# FreeCAD puts its Mod folders ahead of PYTHONPATH; an installed CadSwarm must not shadow this checkout.
for _name in [n for n in sys.modules if n == "cadswarm" or n.startswith("cadswarm.")]:
    del sys.modules[_name]

from PySide import QtCore, QtGui  # noqa: E402

from cadswarm import hosting  # noqa: E402
from cadswarm.client import load_json  # noqa: E402
from cadswarm.gui import panel  # noqa: E402

out, project, host_dir = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
report = {}


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        QtGui.QApplication.processEvents()
        time.sleep(0.05)


def flush():
    """Write the report so far: a crash later must not lose the evidence earlier steps gathered."""
    (out / "freecad-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def shot(name):
    path = out / name
    Gui.getMainWindow().grab().save(str(path))
    report.setdefault("shots", []).append(str(path))
    # The dock on its own, so the chat and the tracker can be read in the evidence.
    if s.panel is not None:
        panel_path = out / name.replace(".png", "-panel.png")
        s.panel.grab().save(str(panel_path))
        report["shots"].append(str(panel_path))
    return str(path)


cfg = load_json(str(project / ".cadswarm" / "config.json"), {})
ident = load_json(str(project / ".cadswarm" / "identity.json"), {})
s = panel.SESSION
s.connect({"uri": cfg["uri"], "db": cfg["db"]}, ident, ident.get("name") or "Sam")
panel.show_panel()
p = s.panel
try:
    p.resize(430, 860)
    Gui.getMainWindow().resizeDocks([p], [430], QtCore.Qt.Horizontal)
except (AttributeError, TypeError):
    pass
pump(4)
report["joined"] = {"state": s.state, "name": s.ident.get("name"), "db": cfg["db"]}
report["members"] = [m["name"] for m in s.model.get("participants", [])]
report["tabs"] = [p.tabs.tabText(i) for i in range(p.tabs.count())]
shot("freecad-1-joined.png")
flush()

# --- the @team bug: a brief must not leave an optimistic "sending..." row -------------------
text = "@team a 60 by 40 mm phone stand with a lip"
expected = text.split(" ", 1)[1]
p.msg.setText(text)
p.send()
pump(0.6)
outbox = [o["body"] for o in p.outbox]
chat_now = p.chat.toPlainText()
report["team_send"] = {"outbox": outbox, "chat_mentions_sending": "sending" in chat_now.lower(),
                       "chat_mentions_handed": "Handed to the team" in chat_now,
                       "notice": [n["text"] for n in s.notices], "chat_tail": chat_now[-300:]}
pump(3)
report["team_send"]["outbox_after_3s"] = [o["body"] for o in p.outbox]
report["team_send"]["chat_tail_after_3s"] = p.chat.toPlainText()[-300:]
flush()

# --- wait for the host to plan, build and review it ---------------------------------------
deadline = time.time() + 240
mine = []
while time.time() < deadline:
    pump(1)
    mine = [b for b in s.briefs if (b.get("text") or "").strip() == expected]
    if mine and mine[0].get("status") in ("ready", "failed"):
        break

mine = [b for b in s.briefs if (b.get("text") or "").strip() == expected]
report["brief"] = {"count": len(mine), "rows": mine,
                   "all_briefs": [(b.get("text"), b.get("status")) for b in s.briefs]}
report["tasks"] = [(t.get("title"), t.get("status"), t.get("owner_name")) for t in s.tasks]
report["review"] = {"rows": s.reviews[:2], "box": p.review_box.text()}
report["chat_after"] = p.chat.toPlainText()[-800:]
shot("freecad-2-built.png")
flush()

# --- the guide's CLI picker path: hosting.set_agent_client writes host.json in place --------
before = load_json(str(host_dir / "host.json"), {}).get("agent_client")
written = hosting.set_agent_client(str(host_dir), "codex")
pump(3)  # the host re-reads host.json every couple of seconds
after = load_json(str(host_dir / "host.json"), {}).get("agent_client")
restored = hosting.set_agent_client(str(host_dir), before)
pump(3)
report["cli_picker"] = {"written": written, "before": before, "after": after,
                        "restored": restored,
                        "now": load_json(str(host_dir / "host.json"), {}).get("agent_client")}
shot("freecad-3-final.png")

report["panel"] = {"chat_chars": len(p.chat.toPlainText()), "chat_blocks": p.chat.document().blockCount(),
                   "brief_rows": p.task_list.topLevelItemCount(), "state": s.state}

# --- the storm fix, from the host's own log: one start per agent, no repeats -----------------
try:
    log = (host_dir / "host.log").read_text(encoding="utf-8", errors="replace").splitlines()
except OSError as exc:
    report["host_log"] = {"error": str(exc)}
else:
    starts = [line.split("starting ", 1)[1] for line in log if "starting " in line and "brief " in line]
    problems = [line for line in log if "could not start" in line or "failed:" in line or "unusable" in line]
    report["host_log"] = {"starts": starts, "unique_starts": len(set(starts)), "problems": problems[-5:]}
flush()
(out / "freecad-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: report[k] for k in ("joined", "team_send", "cli_picker")}, ensure_ascii=False))
try:
    s.disconnect()
except Exception:  # noqa: BLE001 - the report is already on disk
    pass
QtCore.QTimer.singleShot(0, QtGui.QApplication.quit)
sys.exit(0)
