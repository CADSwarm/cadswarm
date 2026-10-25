"""The Sponsors tab: what each outside service does for CADSwarm, with the numbers the panel already holds.

The stamped evidence is the same snapshot the web panel shows (web/src/ui/sponsor-evidence.ts). It stays labelled
"checked <time>" so a number we verified once is never read as a live one.
"""

# Keep in step with web/src/ui/sponsor-evidence.ts.
STAMPED = {
    "at": "24 Oct, 20:35",
    "elastic": {"boards": 6, "designs": 1, "lessons": 4},
    "factory": {"droid": "0.249.0", "sdk": "0.5.0"},
}


def evidence_line():
    """The stamped facts, in one line a person can read beside the live numbers."""
    e = STAMPED
    return (f"checked {e['at']} · Elastic {e['elastic']['boards']} board docs, "
            f"{e['elastic']['designs']} designs, {e['elastic']['lessons']} lessons · "
            f"Droid {e['factory']['droid']} / droid-sdk {e['factory']['sdk']}")


def _review(reviews, brief_id=""):
    """The newest round of this brief, not the newest row or another brief's round."""
    rows = [r for r in (reviews or []) if brief_id and r.get("brief") == brief_id]
    return sorted(rows, key=lambda r: (int(r.get("round") or 0), r.get("at") or 0))[-1] if rows else None


def _findings(review):
    return [str(f) for f in (review.get("findings") or []) if f] if review else []


def _people(members):
    return ", ".join(f"{m['name']} · {m.get('status')}" for m in members) or "not joined"


def _builder_card(card_id, name, role, client, members, tasks, extra, calls):
    """Who is running for one partner and what they finished. FreeCAD has no activity feed, so no trace:
    the same rule as the Factory card. The live trace is on the second screen, in the web panel."""
    people = [m for m in members if (m.get("client") or "") == client]
    ids = {m["id"] for m in people}
    mine = [t for t in tasks if t.get("owner") in ids]
    done = sum(1 for t in mine if t.get("status") == "done")
    party = "agents" if client == "claude" else "helper"
    facts = [(party, _people(people)), ("tasks done", f"{done}/{len(mine)}")]
    facts += extra
    facts.append(("live trace", "the web panel"))
    return {"id": card_id, "name": name, "role": role, "facts": facts, "calls": calls}


def cards(session, database="", server="", sign_in=""):
    """The five cards, built from the snapshot the panel already has, plus the stamped snapshot.
    `server` and `sign_in` say where the team's SpacetimeDB lives and whether this computer is signed in
    (the panel reads both cheaply: the address names the server, and the sign-in is only for Maincloud)."""
    model = session.model or {}
    members = model.get("participants") or []
    objects = model.get("objects") or []
    parts = [o for o in objects if o.get("name") != "Params"]
    params = model.get("params") or {}
    tasks = session.tasks or []
    briefs = session.briefs or []
    reviews = session.reviews or []
    active = sum(1 for m in members if m.get("status") == "active")
    held = sum(1 for m in members for lock in (m.get("locks") or []) if lock != "Params")
    done = sum(1 for t in tasks if t.get("status") == "done")
    brief = sorted(briefs, key=lambda b: b.get("at") or 0, reverse=True)[0] if briefs else None
    review = _review(reviews, (brief or {}).get("id") or "")
    findings = _findings(review)
    reviewer = next((m for m in members if m.get("kind") == "reviewer"), None)
    filed = sum(1 for t in tasks if reviewer and t.get("created_by_name") == reviewer.get("name"))
    board = (brief or {}).get("board") or ""
    seq = model.get("seq") or 0

    space_facts = []
    if server:
        space_facts.append(("server", server))
    if sign_in:
        space_facts.append(("sign-in", sign_in))
    space_facts += [
        ("database", database or "this workspace"),
        ("members", f"{active} active · {len(members)} joined"),
        ("model", f"{len(parts)} parts · {len(params)} params · {held} held"),
        ("work", f"{sum(1 for t in tasks if t.get('status') == 'open')} open tasks · {done}/{len(tasks)} done"),
        ("events", f"seq {seq} · the Activity tab is the live feed"),
    ]

    return [
        {
            "id": "spacetime",
            "name": "SpacetimeDB",
            "role": "the team's shared state",
            "facts": space_facts,
            "calls": ["submit_op(kind, args) -> host runs it alone -> complete_op(result)",
                      "views: v_member · v_object · v_task · v_op · v_review"],
        },
        _builder_card(
            "anthropic", "Anthropic", "the planners and builders (Claude Code)", "claude", members, tasks,
            [("surface", "Claude Code → MCP over stdio → the host runs the op"),
             ("mcp ops", "the web panel counts them live")],
            ["Session(mcp_servers=[cadswarm]) → model_status, ops, tasks, recall_designs",
             "claude → MCP over stdio → submit_op → host → complete_op"]),
        _builder_card(
            "creao", "Creao AI", "the helper that drives its own conversation", "creao", members, tasks,
            [("mode", "local bridge · one JSON tool call per turn"),
             ("thread", "one Creao conversation per helper folder")],
            ["cadswarm.creao_agent → CREAO_API_KEY → one tool call per turn",
             "a stale card is the takeover proof: the fence moved and the old thread stops"]),
        {
            "id": "factory",
            "name": "Factory.ai",
            "role": "the design reviewer (Droid)",
            "facts": [
                ("agent", f"{reviewer['name']} · {reviewer.get('client')} · {reviewer.get('status')}"
                          if reviewer else "not set up"),
                ("mode", "autonomy HIGH · 8 MCP tools, geometry read-only"),
                ("last review", f"round {int(review.get('round') or 0)} · "
                                f"{'approved' if review.get('passed') else f'{len(findings)} findings'}"
                                if review else "no review yet"),
                ("tasks filed", str(filed)),
                ("pointer", "own arrow, in its own colour" if reviewer else "none until the reviewer joins"),
            ],
            "list": {"title": "what it found", "rows": findings[:3]} if findings else None,
            "calls": ["Session(mcp_servers=[cadswarm], restrict_tools=REVIEW_TOOLS, runtime=Runtime(executable=droid.exe))",
                      "Droid -> MCP over stdio -> model_status, point_at, check_interference, check_printability, report_review"],
        },
        {
            "id": "elastic",
            "name": "Elastic",
            "role": "board knowledge, design memory, lessons",
            "facts": [
                ("board", board or "none"),
                ("design memory", f"{brief['id']} · {len(findings)} problems" if brief else "no designs yet"),
                ("lessons", "distil-lessons · every 10 min"),
                ("agent tools", "lookup_board · recall_designs · board_spec"),
            ],
            "list": {
                "title": "what the Workflow distils",
                "rows": [f"{board or 'board'}::{f}" for f in findings[:4]],
            } if findings else None,
            "calls": ["POST /cadswarm-boards/_search   hybrid + jina rerank",
                      "PUT  /cadswarm-designs/_doc/{brief}",
                      "ES|QL over cadswarm-designs -> cadswarm-lessons"],
        },
    ]
