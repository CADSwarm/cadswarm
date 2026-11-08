"""What the prompt bar, the chat, the step list and the Agents page decide, as plain data (no Qt, no FreeCAD).

The Qt pieces (prompt_bar.py, agents_page.py, build_pane.py, panel.py) only draw what these functions return,
so every rule here is unit-tested without starting FreeCAD (tests/test_team_view.py).
"""
import json
import re
import time

from ..mentions import MENTION, parse_mentions, strip_mentions
from . import build_view

# ------------------------------------------------------------------------------------------ prompt bar

TARGET_BUILD = "build"   # the whole team plans the request as numbered steps and builds it (a brief)
TARGET_ALL = "all"       # every AI helper is told
TARGET_CHAT = "chat"     # a message for the people on the team
SELECTION_MAX = 6        # parts named in the context line; the rest are counted


def selection_context(names, limit=SELECTION_MAX):
    """"Selected in the model: Base, Lid" (or "" with nothing selected), the line added to a prompt."""
    names = [n for n in dict.fromkeys(names or []) if n]
    if not names:
        return ""
    shown = ", ".join(names[:limit])
    more = len(names) - limit
    return f"Selected in the model: {shown}" + (f" (+{more} more)" if more > 0 else "")


def targets(helper_names):
    """The prompt bar's "Send to" choices, as (key, label): the AI build first, then each helper."""
    out = [(TARGET_BUILD, "AI Build · whole team")]
    out += [(n, f"@{n}") for n in helper_names]
    out += [(TARGET_ALL, "Every AI helper"), (TARGET_CHAT, "Team chat (people)")]
    return out


def route_prompt(text, target, helper_names, people_names=(), part_names=(), selection=()):
    """Where one prompt goes. An @name typed in the text wins over the "Send to" pick, like the chat box.

    Returns {"kind": "brief" | "task" | "all" | "chat" | "error", "to": [names], "text": what is sent,
    "what": the request without its @names, "objects": parts it is about, "error": why it can't be sent}.
    @Part names (a part in the model) are not people: they become objects the request is about."""
    text = (text or "").strip()
    out = {"kind": "error", "to": [], "text": text, "what": "", "objects": [], "error": ""}
    if not text:
        out["error"] = "Type what to build or change first."
        return out
    parts_lower = {p.lower(): p for p in part_names}
    people_lower = {p.lower() for p in people_names}
    helpers, special, unknown = parse_mentions(text, helper_names)
    named_parts = [parts_lower[u.lower()] for u in unknown if u.lower() in parts_lower]
    strangers = [u for u in unknown if u.lower() not in parts_lower and u.lower() not in people_lower]
    objects = list(dict.fromkeys(list(selection or []) + named_parts))
    out["objects"] = objects
    what = strip_mentions(text) or text
    if named_parts:  # "@Base" is about a part: keep its name in the words, without the @
        what = MENTION.sub(lambda m: m.group(1) if m.group(1).lower() in parts_lower else "", text)
        what = strip_mentions(what) or what
    out["what"] = what
    if strangers and not helpers and not special:
        out["error"] = f"There's no helper, person or part called @{strangers[0]}. Type @ to pick one."
        return out
    context = selection_context(objects)
    with_context = what + (f"\n\n{context}" if context else "")
    if "team" in special or (not helpers and not special and target == TARGET_BUILD):
        out.update(kind="brief", text=with_context, to=["team"])
    elif helpers:
        body = text + (f"\n\n{context}" if context else "")
        out.update(kind="task", to=helpers, text=body)
    elif "all" in special or target == TARGET_ALL:
        out.update(kind="all", to=["all"], text=text + (f"\n\n{context}" if context else ""))
    elif target in helper_names:
        out.update(kind="task", to=[target], text=f"@{target} {text}" + (f"\n\n{context}" if context else ""))
    else:
        out.update(kind="chat", to=["all"], text=text)
    return out


def completions(helper_names, people_names, part_names, me=None):
    """What @ offers in the prompt bar and the chat: helpers, @team, @all, people, then parts."""
    seen, out = set(), []
    for n in list(helper_names) + ["team", "all"] + [p for p in people_names if p != me] + list(part_names):
        if n and n.lower() not in seen and re.fullmatch(r"[A-Za-z0-9_-]+", n):
            seen.add(n.lower())
            out.append(n)
    return out


# ------------------------------------------------------------------------------------------ chat


def chat_messages(messages):
    """The rows the chat shows (system rows such as notices and results stay in Activity)."""
    return [m for m in messages or [] if m.get("kind") not in ("notice", "help", "result")]


def unread(messages, me, seen_at):
    """(new messages from others since `seen_at`, how many of them name @me)."""
    new = [m for m in chat_messages(messages) if m.get("from") != me and (m.get("at") or 0) > (seen_at or 0)]
    mine = 0
    if me:
        pat = re.compile(rf"(?<![\w@])@{re.escape(me)}\b", re.IGNORECASE)
        mine = sum(1 for m in new if m.get("to") == me or pat.search(m.get("body") or ""))
    return len(new), mine


def chat_tab_text(count, mentions):
    """"Chat", "Chat · 3", or "Chat · 3 @" when one of them names you."""
    if not count:
        return "Chat"
    return f"Chat · {count}" + (" @" if mentions else "")


def reply_prefix(message, me=None, quote_chars=48):
    """What Reply puts in the message box: "@ann-claude re “Done. The base walls are…”: "."""
    who = message.get("from") or ""
    if not who or who == me:
        return ""
    body = " ".join((message.get("body") or "").split())
    quote = body if len(body) <= quote_chars else body[:quote_chars].rstrip() + "…"
    return f"@{who} re “{quote}”: " if quote else f"@{who} "


# ------------------------------------------------------------------------------------------ presence


def presence(participants, me=None, now=None):
    """Everyone on the team as {"name", "id", "kind", "online", "you", "holds", "doing", "client"}: people first,
    then helpers; the online ones first within each."""
    rows = []
    for p in participants or []:
        holds = [lock for lock in p.get("locks") or [] if lock != "Params"]
        task = (p.get("task") or {}).get("title") or ""
        rows.append({"name": p.get("name") or "?", "id": p.get("id"), "kind": "person" if p.get("kind") == "human"
                     else "ai", "online": p.get("status") == "active", "you": p.get("name") == me,
                     "holds": holds, "doing": task or (p.get("doing") or "").strip(), "client": p.get("client") or ""})
    rows.sort(key=lambda r: (r["kind"] != "person", not r["you"], not r["online"], r["name"].lower()))
    return rows


def presence_summary(rows):
    """"2 people · 3 AI helpers online"."""
    people = sum(1 for r in rows if r["kind"] == "person" and r["online"])
    ai = sum(1 for r in rows if r["kind"] == "ai" and r["online"])
    return (f"{people} {'person' if people == 1 else 'people'} · {ai} AI helper{'' if ai == 1 else 's'} online")


def holds_text(row, limit=3):
    if not row["holds"]:
        return ""
    more = len(row["holds"]) - limit
    return "holds " + ", ".join(row["holds"][:limit]) + (f" +{more}" if more > 0 else "")


# ------------------------------------------------------------------------------------------ steps

STATE_WORDS = {build_view.STATE_DONE: "Done", build_view.STATE_CURRENT: "Running",
               build_view.STATE_BLOCKED: "Stuck", build_view.STATE_CANCELLED: "Cancelled"}


def step_state_word(step):
    """Done / Running / Ready / Waiting / Stuck / Cancelled: the word a step's state chip shows."""
    word = STATE_WORDS.get(step.get("state"))
    if word:
        return word
    return "Ready" if step.get("ready") or step.get("note") == "Ready" else "Waiting"


def step_assignee(step, participants=()):
    """(name, "person" | "ai" | "") of who builds a step; ("", "") when nobody is assigned yet."""
    who = step.get("who") or ""
    if not who:
        return "", ""
    p = next((p for p in participants or [] if p.get("name") == who), None)
    if p is None:
        return who, "ai" if re.search(r"-(claude|codex|opencode)\b", who) else ""
    return who, "person" if p.get("kind") == "human" else "ai"


def step_objects(step, tasks=(), model_objects=()):
    """The parts a step made or will make: the plan's objects, its task's objects, else a part named like it."""
    names = list(step.get("objects") or [])
    task = next((t for t in tasks or [] if step.get("task") and t.get("id") == step.get("task")), None)
    if task:
        names += list(task.get("objects") or [])
    names = list(dict.fromkeys(n for n in names if n))
    if names:
        return names
    key = re.sub(r"[^a-z0-9]", "", (step.get("name") or "").lower())
    if not key:
        return []
    hits = []
    for o in model_objects or []:
        for field in ("name", "label"):
            v = re.sub(r"[^a-z0-9]", "", str(o.get(field) or "").lower())
            if v and (v == key or v.startswith(key) or key.startswith(v) and len(v) >= 4):
                hits.append(o.get("name"))
                break
    return hits


def next_step(model):
    """The step "Run next step" starts: the first ready one. Returns (step, sentence) or (None, sentence)."""
    steps = model.get("steps") or []
    if not steps:
        return None, "No plan yet: describe what to build and press Build it."
    running = [s for s in steps if s["state"] == build_view.STATE_CURRENT]
    ready = [s for s in steps if s["state"] == build_view.STATE_PENDING and step_state_word(s) == "Ready"]
    if ready:
        s = ready[0]
        return s, f"Next: step {s['n']} · {s['name']}"
    if running:
        return None, f"Building step {running[0]['n']} · {running[0]['name']}"
    if all(s["state"] in (build_view.STATE_DONE, build_view.STATE_CANCELLED) for s in steps):
        return None, "Every step is done."
    stuck = [s for s in steps if s["state"] == build_view.STATE_BLOCKED]
    if stuck:
        return None, f"Step {stuck[0]['n']} · {stuck[0]['name']} is stuck and needs a person."
    return None, "Waiting: the remaining steps need earlier ones first."


# ------------------------------------------------------------------------------------------ agents

CUSTOM = "custom"


def agent_rows(clients, found, participants=(), local=None, custom_command=""):
    """One row per agent CLI for the Agents page: {"client", "label", "installed", "path", "helpers": [{"name",
    "online", "state"}], "status"}. `clients` is agent_setup.CLIENTS, `found` {client: path or None}, `local`
    {helper name: {"client", "state"}} for the helpers this computer runs."""
    local = local or {}
    by_client = {}
    for p in participants or []:
        if p.get("kind") == "human":
            continue
        by_client.setdefault(p.get("client") or "", []).append(p)
    rows = []
    for key, info in clients.items():
        path = found.get(key)
        helpers = [{"name": p["name"], "online": p.get("status") == "active",
                    "state": (local.get(p["name"]) or {}).get("state") or ("online" if p.get("status") == "active"
                                                                             else "away"),
                    "here": p["name"] in local}
                   for p in by_client.get(key, [])]
        online = sum(1 for h in helpers if h["online"])
        if helpers:
            status = f"{online} of {len(helpers)} connected" if len(helpers) > 1 else \
                ("Connected" if online else "Offline")
        else:
            status = "Detected · not connected" if path else "Not installed"
        rows.append({"client": key, "label": info["label"], "installed": bool(path), "path": path or "",
                     "install_url": info.get("install_url", ""), "helpers": helpers, "status": status})
    custom = (custom_command or "").strip()
    others = [p for c, ps in by_client.items() if c not in clients for p in ps]
    rows.append({"client": CUSTOM, "label": "Custom command", "installed": bool(custom), "path": custom,
                 "install_url": "", "helpers": [{"name": p["name"], "online": p.get("status") == "active",
                                                 "state": "online" if p.get("status") == "active" else "away",
                                                 "here": False} for p in others],
                 "status": ("Saved · " + custom) if custom else "Any MCP agent: set its command"})
    return rows


def mcp_server_entry(python, launcher, folder):
    """The cadswarm MCP server as an agent config names it (stdio)."""
    return {"command": python, "args": [launcher, "mcp"], "env": {"CADSWARM_PROJECT": folder}}


def remote_instructions(client, code, name, python="python", launcher="cadswarm_launch.py",
                        folder="~/cadswarm-helper"):
    """The exact steps to connect an agent on another computer: install, join with the invite, run it.

    Returns {"setup": the one command that joins (it writes the agent's MCP config), "run": how to start the agent,
    "mcp": the MCP server entry as JSON for agents configured by hand, "steps": the numbered steps as text}."""
    cli_client = client if client in ("claude", "codex", "opencode") else "opencode"
    setup = (f"cadswarm setup --client {cli_client} --invite {code} --name {name} --dir {folder}")
    run = {"claude": "claude", "codex": "codex", "opencode": "opencode"}.get(client, "<your agent's command>")
    mcp = json.dumps({"mcpServers": {"cadswarm": mcp_server_entry(python, launcher, folder)}}, indent=2)
    steps = [
        "Install CADSwarm on that computer (Install CadSwarm.cmd / .command / .sh from the download).",
        f"In a terminal there, run:\n{setup}",
        f"Start the agent in that folder: cd {folder} && {run}",
    ]
    if client not in ("claude", "codex", "opencode"):
        steps.append("For any other MCP agent, add this server to its MCP settings (after step 2):\n" + mcp)
    return {"setup": setup, "run": f"cd {folder} && {run}", "mcp": mcp,
            "steps": "\n\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))}


def helper_name(me, client, taken):
    """A free name for a new helper: "carol-claude", then "carol-claude-2"..."""
    base = re.sub(r"[^a-z0-9]+", "-", (me or "me").lower()).strip("-") or "me"
    name, n = f"{base}-{client}"[:36], 2
    lowered = {t.lower() for t in taken}
    while name.lower() in lowered:
        name, n = f"{base}-{client}-{n}"[:40], n + 1
    return name


def ago(at, now=None):
    """"just now", "4 min ago", "2 h ago"."""
    if not at:
        return ""
    s = max(0, (now or time.time()) - at)
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    return f"{int(s // 3600)} h ago"
