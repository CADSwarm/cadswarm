"""The Droid design reviewer, started by cadswarm-host when a brief's parts are all built.

It is not a chat agent: the host starts it, it may only inspect the model and report (Droid's
`restrict_tools` allowlist, and the module refuses lock/submit_op from a `reviewer` member), and
every step it takes is written to the `activity` table so the phone shows it live.
"""
import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping

from .client import HubRequestError, load_json, state_dir
from .paths import LAUNCHER
from .space_hub import project_hub

MCP_SERVER = "cadswarm"

# What the reviewer may do: read, run the checks, file tasks, talk, and record its verdict.
ALLOWED = (
    "model_status", "get_object", "check_interference", "check_printability",
    "create_task", "send_message", "report_review",
)

# Droid names an MCP tool "<server>___<tool>"; the reviewer resolves the real ids from
# `session.list_tools()` before it starts and only falls back to this prefix if that fails.
REVIEW_TOOLS = [f"{MCP_SERVER}___{t}" for t in ALLOWED]


def review_prompt(brief, board_label, bed, round_no, style=""):
    style_check = (
        "5. Team design style: the rules below are how this team designs. Each part that breaks one is a problem;\n"
        "   write its `problem` as \"style: <the rule> (<what the part does instead>)\".\n" + style + "\n"
    ) if style else ""
    return (
        f"You are the design REVIEWER for CadSwarm brief {brief['id']} (review round {round_no}).\n"
        f"The person asked: \"{brief['text']}\". Board: {board_label}. Printer bed: {bed[0]} x {bed[1]} x {bed[2]} mm.\n"
        "Check, using the tools only (you cannot change geometry):\n"
        "1. model_status: read the Params sheet (the board's measured dimensions) and the parts.\n"
        "2. check_interference on all parts: any clash between separate parts is a problem.\n"
        "3. Fit: the enclosure's inner size must exceed pcb_l/pcb_w by at least 2*gap; standoffs must sit at\n"
        "   hole_N_x/hole_N_y; each port cutout must be centred on its <port>_pos and <port>_z.\n"
        "4. check_printability: every part must fit the bed, be a closed solid, and have no wall under 1.2 mm.\n"
        + style_check +
        "For each problem: create_task assigned to the part's owner (see locked_by in model_status) with a\n"
        "precise fix, brief set to this brief, and send_message to that owner.\n"
        f"Finish by calling report_review(brief_id=\"{brief['id']}\", round={round_no}, passed=<true only if no problems>,\n"
        "findings=[{\"object\", \"problem\", \"task_id\"}])."
    )


def team_style(hub, brief):
    """The style rules for this brief, or "" (a review never waits on, or fails for, the style memory)."""
    try:
        return hub.get("/style", topic=brief.get("text") or "", board=brief.get("board") or "").get("text") or ""
    except Exception:
        return ""


def activity_for(event):
    """Map a Droid stream event to (kind, summary, targets) for the activity feed, or None."""
    name = getattr(event, "name", None)
    if name is not None and hasattr(event, "input"):
        raw = event.input if isinstance(event.input, Mapping) else {}
        targets = raw.get("names") or ([raw["name"]] if isinstance(raw.get("name"), str) else [])
        return "tool_call", f"{str(name).split('___')[-1]} {json.dumps(dict(raw))[:200]}", list(targets or [])
    if hasattr(event, "tool_name") and hasattr(event, "is_error"):
        return "tool_result", f"{str(event.tool_name).split('___')[-1]} {'failed' if event.is_error else 'ok'}", []
    text = getattr(event, "text", None)
    if text:
        return "say", text[:300], []
    return None


async def _review_tool_ids(session):
    """Ask the CLI for its real MCP tool ids and narrow the session to them.

    The ids carry the CLI's own server prefix, so a hard-coded list can silently match nothing
    (which is safe but useless). Anything outside ALLOWED is never returned.
    """
    try:
        tools = await session.list_tools()
        ids = [t.id for t in tools if str(t.id).split("___")[-1] in ALLOWED]
        if ids and set(ids) != set(REVIEW_TOOLS):
            await session.update_settings(restrict_tools=ids)
            print(f"reviewer: using tool ids {ids}", file=sys.stderr)
        return ids or REVIEW_TOOLS
    except Exception as e:  # noqa: BLE001 - an older CLI may not list tools; the constant is the safe default
        print(f"reviewer: could not list tools ({e}); keeping the {MCP_SERVER}___ allowlist", file=sys.stderr)
        return REVIEW_TOOLS


def _adopt_fence(hub, project):
    """The MCP server Droid starts rejoins this same identity, which bumps the fence and makes ours
    stale; it saves the new one to identity.json, so the reviewer's own writes pick it up from there."""
    fence = load_json(os.path.join(state_dir(project), "identity.json"), {}).get("fence")
    if isinstance(fence, (int, float)) and fence > (hub.identity.get("fence") or 0):
        hub.identity["fence"] = int(fence)


async def review(project, brief_id, round_no):
    from droid_sdk import (AssistantMessage, Autonomy, Session, SessionConfig, StdioMcpServerConfig,
                           ToolCall, ToolResult)

    hub, _cfg = project_hub(project)
    if hub is None:
        raise SystemExit(f"{project} is not set up for CadSwarm; run cadswarm setup --invite first")
    hub.join()
    briefs = [b for b in hub.space.sql("SELECT * FROM v_brief") if b["id"] == brief_id]
    if not briefs:
        raise SystemExit(f"no brief {brief_id} in this workspace")
    brief = briefs[0]
    ws = hub.space.sql("SELECT * FROM v_workspace")[0]
    bed = [ws["bed_x_mm"], ws["bed_y_mm"], ws["bed_z_mm"]]
    config = SessionConfig(
        autonomy=Autonomy.LOW,
        # The launcher, like every agent config: it finds the package and the private deps folder
        # whether or not the child inherits this process's PYTHONPATH.
        mcp_servers=[StdioMcpServerConfig(name=MCP_SERVER, command=sys.executable,
                                          args=[LAUNCHER, "mcp"],
                                          env={"CADSWARM_PROJECT": project})],
        restrict_tools=REVIEW_TOOLS,
    )
    hub.log_activity("say", f"reviewing brief {brief_id}, round {round_no}", [])
    async with Session(cwd=project, config=config) as session:
        await _review_tool_ids(session)
        prompt = review_prompt(brief, brief.get("board") or "unspecified", bed, round_no, team_style(hub, brief))
        async with session.stream(prompt) as stream:
            async for ev in stream:
                if isinstance(ev, (ToolCall, ToolResult, AssistantMessage)):
                    mapped = activity_for(ev)
                    if mapped:
                        _adopt_fence(hub, project)
                        hub.log_activity(*mapped)
    # If Droid never recorded a verdict, record a failing one so the brief does not hang.
    reviews = [r for r in hub.space.sql("SELECT * FROM v_review") if r["brief"] == brief_id and r["round"] == round_no]
    if not reviews:
        _adopt_fence(hub, project)
        try:
            hub.post("/reviews", {"brief_id": brief_id, "round": round_no, "passed": False,
                                  "findings": [{"object": "", "problem": "the reviewer did not report a verdict",
                                                "task_id": ""}]})
        except HubRequestError as e:  # the host's watchdog fails the brief when no verdict arrives
            print(f"reviewer: could not record the fallback verdict: {e}", file=sys.stderr)


def main(argv=None):
    p = argparse.ArgumentParser(prog="cadswarm.reviewer")
    p.add_argument("--brief", required=True)
    p.add_argument("--round", type=int, default=1)
    p.add_argument("--dir", required=True)
    a = p.parse_args(argv)
    asyncio.run(review(os.path.abspath(a.dir), a.brief, a.round))


if __name__ == "__main__":
    main()
