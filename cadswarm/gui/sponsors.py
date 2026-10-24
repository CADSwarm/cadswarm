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


def _review(reviews):
    """The newest round, not the newest row: what the brief's state reflects."""
    rows = reviews or []
    return sorted(rows, key=lambda r: int(r.get("round") or 0))[-1] if rows else None


def _findings(review):
    return [str(f) for f in (review.get("findings") or []) if f] if review else []


def cards(session, database=""):
    """The three cards, built from the snapshot the panel already has, plus the stamped snapshot."""
    model = session.model or {}
    members = model.get("participants") or []
    objects = model.get("objects") or []
    params = model.get("params") or {}
    tasks = session.tasks or []
    briefs = session.briefs or []
    reviews = session.reviews or []
    active = sum(1 for m in members if m.get("status") == "active")
    held = sum(1 for m in members for lock in (m.get("locks") or []) if lock != "Params")
    done = sum(1 for t in tasks if t.get("status") == "done")
    brief = sorted(briefs, key=lambda b: b.get("at") or 0, reverse=True)[0] if briefs else None
    review = _review(reviews)
    findings = _findings(review)
    reviewer = next((m for m in members if m.get("kind") == "reviewer"), None)
    filed = sum(1 for t in tasks if reviewer and t.get("created_by_name") == reviewer.get("name"))
    board = (brief or {}).get("board") or ""
    seq = model.get("seq") or 0

    return [
        {
            "id": "spacetime",
            "name": "SpacetimeDB",
            "role": "the team's shared state",
            "facts": [
                ("database", database or "this workspace"),
                ("members", f"{active} active · {len(members)} joined"),
                ("model", f"{len(objects)} parts · {len(params)} params · {held} held"),
                ("work", f"{sum(1 for t in tasks if t.get('status') == 'open')} open tasks · {done}/{len(tasks)} done"),
                ("events", f"seq {seq} · the Activity tab is the live feed"),
            ],
            "calls": ["submit_op(kind, args) -> host runs it alone -> complete_op(result)",
                      "views: v_member · v_object · v_task · v_op · v_review"],
        },
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
