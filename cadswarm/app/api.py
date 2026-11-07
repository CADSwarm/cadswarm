"""The contract every part of the desktop app builds against. No Qt or FreeCAD at import time.

The shell (shell.py) owns the window regions. Feature packages under ``cadswarm/app/`` each expose
``install(ctx)`` and register what they contribute through the ``Context`` (see shell.py):

    ctx.commands.add(Command(...))            commands: ribbon buttons, menus, keymap, command search
    ctx.add_ribbon_tab(RibbonTab(...))        ribbon tabs per environment
    ctx.add_left_page / add_right_page        left pane (tree, parameters) and task pane (AI build, team)
    ctx.add_status_widget, ctx.add_overlay    status bar items, widgets floating over the 3D view
    ctx.show_property_page / close_...        property panel replaces the tree while a command runs
    ctx.signals.<name>.connect(...)           document / selection / environment / theme events

Cross-package services (``ctx.services[name]``), all optional; callers must cope with a missing service:

    services["files"]  .open_path(path) .new_document(kind) .confirm_close_all() -> bool
    services["team"]   .owner_of(obj_name) -> {"name", "color", "locked": bool, "me": bool} | None
                       .people() -> [{"name", "color", "kind": "person"|"helper", "status"}]
                       .follow(name)  (signal: ctx.signals.team_changed)
    services["build"]  .step_of(obj_name) -> {"n", "name", "state": "done"|"running"|"ready"|"waiting", "assignee"} | None
                       .progress() -> {"done", "total", "now": [names]}       (signal: ctx.signals.build_changed)
    services["tree"]   .select(obj_name)  .refresh()
    services["ribbon"] .show_tab(tab_id)

Keep this file small and stable: it is what lets the packages be built in parallel.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional

ENVIRONMENTS = ("part", "sketch", "assembly", "drawing")


@dataclass
class Command:
    id: str                                   # "part.extrude", "file.save", "build.run_next"
    label: str                                # "Extrude" (ribbon label; "\n" allowed)
    run: Callable[[], object]                 # what it does
    icon: str = ""                            # icon name in cadswarm/app/icons
    tip: str = ""
    shortcut: str = ""                        # Qt key sequence text, e.g. "Ctrl+B", "Shift+E"
    enabled: Optional[Callable[[], bool]] = None
    checked: Optional[Callable[[], bool]] = None   # for toggles
    ai: bool = False                          # AI command: icon carries the signal dot
    envs: tuple = ()                          # environments where the shortcut is live; () = all
    keywords: str = ""                        # extra words for command search

    def is_enabled(self):
        try:
            return True if self.enabled is None else bool(self.enabled())
        except Exception:  # noqa: BLE001 - a broken predicate must never take the UI down
            return False


class CommandRegistry:
    def __init__(self):
        self._cmds = {}
        self._listeners = []
        self.last = None

    def add(self, cmd):
        self._cmds[cmd.id] = cmd
        for fn in self._listeners:
            fn(cmd)
        return cmd

    def on_add(self, fn):
        self._listeners.append(fn)

    def get(self, cid):
        return self._cmds.get(cid)

    def __contains__(self, cid):
        return cid in self._cmds

    def all(self):
        return list(self._cmds.values())

    def run(self, cid):
        cmd = self._cmds.get(cid)
        if cmd is None or not cmd.is_enabled():
            return False
        self.last = cid
        cmd.run()
        return True

    def search(self, text, limit=20):
        """Ranked command search: label prefix > word prefix > substring in label/keywords/tip."""
        q = text.strip().lower()
        if not q:
            return []
        scored = []
        for c in self._cmds.values():
            label = c.label.replace("\n", " ").lower()
            hay = " ".join([label, c.keywords.lower(), c.tip.lower(), c.id.lower()])
            if label.startswith(q):
                s = 0
            elif any(w.startswith(q) for w in label.split()):
                s = 1
            elif q in hay:
                s = 2
            else:
                continue
            scored.append((s, len(label), label, c))
        scored.sort(key=lambda t: t[:3])
        return [t[3] for t in scored[:limit]]


@dataclass
class RibbonGroup:
    label: str
    commands: list                            # command ids; a nested list = split/drop-down button


@dataclass
class RibbonTab:
    id: str                                   # "features", "sketch", "evaluate"...
    label: str                                # "FEATURES"
    groups: list = field(default_factory=list)
    envs: tuple = ENVIRONMENTS                # environments that show this tab
    order: int = 50
    contextual: bool = False                  # e.g. the SKETCH tab while a sketch is open (signal top bar)


def tabs_for(tabs, env):
    """Tabs visible in an environment, contextual first, then by order."""
    out = [t for t in tabs if env in t.envs]
    out.sort(key=lambda t: (not t.contextual, t.order, t.label))
    return out
