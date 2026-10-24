"""UserPromptSubmit hook for Claude Code and Codex.

Prints the team-context block (read from SpacetimeDB through SpaceHub) (who holds which parts, open tasks, what
changed since this agent's last prompt, unread messages) to stdout, which both
CLIs add to the model's context. Contract borrowed from Amoeba's prompt hooks:

* FAIL OPEN. Any error, timeout or missing setup prints nothing and exits 0 —
  coordination context must never block a prompt.
* Bounded: one request with a ~1.8s budget, output capped well under Codex's
  ~2,500-token hook limit.
* Project folder: both CLIs' stdin carries `cwd` (a common hook input field, in Codex's docs too);
  fall back to the process cwd if it is missing (both run hooks from the project).
"""
import json
import os
import re
import sys
import threading

# Words that usually mean "simulate this". A false positive only adds a short reminder.
FEA_WORDS = re.compile(r"\b(fea|fem|finite[- ]element|stress(es|ed)?|strain|deflect\w*|deform\w*|bend(s|ing)?|"
                       r"loads?|loaded|loading|strength|strong enough|(will|would|can|does) (it|this|that) hold|holds? (up|\d+)|support \d+|break(s|ing)?|crack\w*|yield\w*|"
                       r"safety factor|simulat\w*|natural freq\w*|vibrat\w*|resonan\w*|modal|eigen\w*|"
                       r"thermal|heat\w*|temperatures?|how hot|overheat\w*)\b", re.I)
FEA_HINT = ("\n## This looks like an FEA (simulation) request\n"
            "Before simulating, ask in ONE message: which analysis — linear static (stress, deflection, safety "
            "factor; recommend it unless they ask about vibration or heat), modal (natural frequencies) or thermal "
            "(steady-state temperatures) — plus the material, the faces held still and the loads/temperatures. "
            "Offer clearly marked placeholders if they don't know. Then list_faces, run_fea, fea_status, explain, "
            "and always publish_fea_report with a summary and 2-5 points of improvement.")


def fea_hint(prompt):
    return FEA_HINT if isinstance(prompt, str) and FEA_WORDS.search(prompt) else ""


# Words that usually mean "this needs a real part". A false positive only adds a short reminder.
PART_WORDS = re.compile(r"\b(screws?|bolts?|nuts?|washers?|inserts?|heat[- ]set|bearings?|608|axles?|shafts?|"
                        r"pins?|dowels?|magnets?|fasten\w*|hinges?|pulleys?|motors?|nema ?\d+|"
                        r"m(2|2\.5|3|4|5|6|8)\b|tolerances?|clearances?|press[- ]fit|slip[- ]fit|fits? (in|on)|"
                        r"library|components?|standard parts?|step files?)", re.I)
LIBRARY_HINT = ("\n## This may need a library part or a fit\n"
                "Check the team's components library first: recommend_components (say what the part must do) "
                "or search_components; standard parts go in with add_standard_part, team parts with "
                "insert_component. Size mating holes with hole_size (it applies the team's fits) and bind gaps "
                "to Params.tol_*. If a part needs a different team setting, suggest_setting with the reason.")


def library_hint(prompt):
    return LIBRARY_HINT if isinstance(prompt, str) and PART_WORDS.search(prompt) else ""


def main():
    raw = ""
    try:
        # Bounded stdin read: a parent that keeps the pipe open must not wedge the prompt.
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("v", sys.stdin.read()), daemon=True)
        t.start()
        t.join(0.5)
        raw = box.get("v", "")
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    start = payload.get("cwd") if isinstance(payload, dict) else None
    try:
        from cadswarm.client import find_project_dir
        from cadswarm.space_hub import project_hub
        project = find_project_dir(start or os.getcwd())
        if not project:
            return
        hub, _cfg = project_hub(project)
        if not hub or not hub.identity.get("participant_id"):
            return  # the MCP server has not joined yet
        if hub.identity.get("fence") is None:
            return  # never rejoin here: that would bump the fence and supersede the live MCP server
        hub.space.timeout = 1.8
        text = hub.get("/context")["context"]
        prompt = payload.get("prompt") if isinstance(payload, dict) else None
        hint = fea_hint(prompt) + library_hint(prompt)
        text = text[:6000 - len(hint)] + hint
        # Windows pipes default to the ANSI codepage; the CLIs read hook output as UTF-8.
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text[:6000] + "\n")
        sys.stdout.flush()
    except Exception:
        return


if __name__ == "__main__":
    main()
    os._exit(0)
