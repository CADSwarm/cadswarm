"""A CadSwarm helper whose AI is Creao AI, bridged from this computer.

Creao has no CLI to spawn and no way to attach a custom MCP server, so this module is the adapter: it
joins the team with the folder's CadSwarm identity (exactly like `mcp_server.main`), sends Creao the
wake-up prompt, and asks it for one JSON tool call per turn. Every call runs in-process through the
same MCP server object (`mcp_server.mcp.call_tool`), so tool behaviour, activity logging and fencing
are identical to a Claude Code, Codex, Droid or OpenCode helper.

    python -m cadswarm.creao_agent --dir <helper folder>     # the prompt arrives on stdin

The conversation id lives in `.cadswarm/creao.json` and is reused across tasks; `cadswarm creao reset`
(or `cadswarm setup --fresh`) starts a new one. The agent itself is this computer's shared Creao agent
(`creao.load_shared`), so every Creao helper on the account uses one. The API key is never written.
"""
import argparse
import asyncio
import json
import os
import sys
import threading
import time

from . import creao
from .client import HubRequestError

TURN_LIMIT = 60
WALL_CLOCK_SECONDS = 15 * 60
MAX_RESULT_CHARS = 60_000  # a run's input is capped at 256 KB, and model_status can be large


class BridgeError(Exception):
    """The helper could not finish: no agent id, an unparsable reply twice, or no turns left."""


def agent_ready_message(project):
    """Why this folder can't run a Creao helper right now, or '' when it can."""
    if not creao.api_key():
        return "Creao AI needs CREAO_API_KEY on this computer (add it to cadswarm.env, see .env.example)"
    cfg = creao.load_config(project)
    agent_id = (os.environ.get("CREAO_AGENT_ID") or cfg.get("agent_id")
                or creao.load_shared().get("agent_id") or "").strip()
    if not agent_id:
        return "this helper has no Creao agent yet: run `cadswarm setup --client creao --invite ... --name ...`"
    return ""


def catalogue_text(tools):
    """The tool catalog for the first turn of a conversation: names, descriptions and argument schemas."""
    rows = []
    for tool in tools:
        schema = getattr(tool, "input_schema", None)
        if schema is not None and hasattr(schema, "model_dump"):
            schema = schema.model_dump(exclude_none=True)
        try:
            schema = json.dumps(schema or {}, ensure_ascii=False)
        except (TypeError, ValueError):
            schema = "{}"
        rows.append(f"- {tool.name}: {getattr(tool, 'description', '') or ''}\n  args: {schema}")
    return "CadSwarm tools:\n" + "\n".join(rows)


def extract_json(text):
    """The JSON turn in a reply, even if Creao wrapped it in prose or a fence. Returns the text as-is
    when no object parses (so parse_turn can report the real problem)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```")).strip()
    try:
        json.loads(text)
        return text
    except ValueError:
        pass
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        depth, in_string, escaped = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        if isinstance(json.loads(candidate), dict):
                            return candidate
                    except ValueError:
                        pass
                    break
    return text


def parse_turn(text):
    """{"tool": name, "args": {...}} or {"done": true, "summary": ...}; ValueError says what is wrong."""
    try:
        turn = json.loads(extract_json(text))
    except ValueError:
        raise ValueError("your reply wasn't one JSON object")
    if not isinstance(turn, dict):
        raise ValueError("your reply must be a JSON object")
    if turn.get("done") is True:
        return {"done": True, "summary": str(turn.get("summary") or "").strip()}
    name = turn.get("tool")
    if not isinstance(name, str) or not name:
        raise ValueError('expected {"tool": "<name>", "args": {...}} or {"done": true, "summary": "..."}')
    args = turn.get("args", {})
    if not isinstance(args, dict):
        raise ValueError('"args" must be a JSON object')
    return {"tool": name, "args": args}


def tool_result(result):
    """A CallToolResult as plain JSON for the next Creao turn."""
    if getattr(result, "is_error", False):
        text = _content_text(result)
        return {"error": text or "the tool returned an error"}
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return {"result": structured}
    text = _content_text(result)
    return {"result": text or "(no output)"}


def _content_text(result):
    parts = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _first_message(prompt, catalog):
    return (f"{creao.PROTOCOL}\n\n{catalog}\n\nThe task CadSwarm gave you:\n{prompt}")


async def run_bridge(project, prompt, client=None, tools=None, clock=time.monotonic,
                     log=None, turn_limit=TURN_LIMIT, wall_clock=WALL_CLOCK_SECONDS):
    """Drive the conversation until Creao says it is done. Returns its summary."""
    log = log or (lambda text: print(f"cadswarm: {text}", file=sys.stderr))
    problem = agent_ready_message(project)
    if problem:
        raise BridgeError(problem)
    cfg = creao.load_config(project)
    agent_id = (os.environ.get("CREAO_AGENT_ID") or cfg.get("agent_id")
                or creao.load_shared().get("agent_id") or "").strip()
    from .agent_setup import helper_model  # the model picked for this helper sticks, over CREAO_MODEL
    model = (helper_model(project) or os.environ.get("CREAO_MODEL") or cfg.get("model") or "").strip() or None
    api = client or creao.Creao()
    tool_set = tools
    if tool_set is None:
        from . import mcp_server
        tool_set = mcp_server.mcp
    available = await tool_set.list_tools()
    catalog = catalogue_text(available)
    names = sorted(tool.name for tool in available)
    conversation_id = cfg.get("conversation_id") or None
    first = _first_message(prompt, catalog)
    message = first
    if conversation_id:
        message = f"{creao.PROTOCOL}\n\nThe task CadSwarm gave you:\n{prompt}"
    started = clock()
    repairs = 0
    for turn in range(1, turn_limit + 1):
        if clock() - started >= wall_clock:
            raise BridgeError(f"Creao used the whole {wall_clock // 60} minute budget without finishing "
                              f"(after {turn - 1} turns); its work so far is in the team's activity log")
        try:
            reply, conversation_id = _ask(api, agent_id, conversation_id, message, model, log)
        except creao.CreaoError as e:
            if e.code != "CONVERSATION_NOT_FOUND" or not conversation_id:
                raise
            log("Creao forgot this helper's conversation; starting a new one")
            creao.clear_conversation(project)
            conversation_id, message = None, first
            continue
        creao.save_config(project, agent_id=agent_id, conversation_id=conversation_id, model=model)
        try:
            action = parse_turn(reply)
        except ValueError as e:
            repairs += 1
            if repairs > 1:
                raise BridgeError(f"Creao stopped replying with CadSwarm turns ({e}); the last reply was: "
                                  f"{reply.strip()[:300]!r}")
            log(f"turn {turn}: unparsable reply ({e}); asking for one repair")
            message = (f"Your last reply was not a valid CadSwarm turn: {e}. Reply with exactly one JSON "
                       "object, as described at the top, and nothing else.")
            continue
        if action.get("done"):
            summary = action["summary"] or "(no summary)"
            log(f"turn {turn}: done")
            return summary
        repairs = 0
        name, args = action["tool"], action["args"]
        if name not in names:
            log(f"turn {turn}: refused unknown tool {name!r}")
            message = (f"There is no CadSwarm tool called {name!r}. Available tools: {', '.join(names)}.\n\n"
                       "Reply with the next JSON turn.")
            continue
        log(f"turn {turn}: {name}({_short_args(args)})")
        try:
            result = tool_result(await tool_set.call_tool(name, args))
        except Exception as e:  # a broken tool must not kill the helper: report it and let Creao recover
            result = {"error": f"{type(e).__name__}: {e}"}
        text = json.dumps(result, ensure_ascii=False)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "… (truncated)"
        message = f"Tool {name} returned:\n{text}\n\nReply with the next JSON turn."
    raise BridgeError(f"Creao used its {turn_limit} turns without finishing; see the team's activity log")


def _ask(api, agent_id, conversation_id, message, model, log):
    run_id, new_id = api.run(agent_id, {"message": message}, conversation_id=conversation_id,
                             model=model)
    conversation_id = new_id or conversation_id  # a response without one keeps the conversation we had
    log(f"asked Creao (run {run_id})")
    done = api.wait(run_id)
    conversation_id = conversation_id or done.get("conversation_id")  # a new conversation's id may arrive here
    credits = (done.get("usage") or {}).get("credits")
    log(f"run {run_id} ended {done.get('status')}" + (f" ({credits} credits)" if credits is not None else ""))
    if done.get("status") != "completed":
        code = str(((done.get("error") or {}).get("code")) or "")
        raise creao.CreaoError(f"Creao run {run_id} ended {done.get('status')}"
                               + (f": {code}" if code else ""), code or None)
    result = done.get("result") or {}
    return (result.get("text") or result.get("chat_text") or ""), conversation_id


def _short_args(args):
    text = json.dumps(args, ensure_ascii=False)
    return text if len(text) <= 200 else text[:200] + "…"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cadswarm.creao_agent",
                                     description="Run one Creao AI helper task for this CadSwarm folder.")
    parser.add_argument("--dir", default="", help="the helper folder (default: this folder)")
    a = parser.parse_args(argv)
    project = os.path.abspath(a.dir or os.getcwd())
    os.environ["CADSWARM_PROJECT"] = project
    prompt = "" if sys.stdin.isatty() else sys.stdin.read()
    if not prompt.strip():
        print("cadswarm.creao_agent: no prompt on stdin", file=sys.stderr)
        return 2

    from . import mcp_server
    mcp_server.S = mcp_server.Session()
    try:
        ident = mcp_server.S.connect()
    except HubRequestError as e:
        print(f"cadswarm: could not join the team at {mcp_server.S.cfg['uri']} / "
              f"{mcp_server.S.cfg['db']}: {e}", file=sys.stderr)
        return 1
    print(f"cadswarm: Creao helper joined as {ident['name']} ({ident['participant_id']}, "
          f"fence {ident['fence']})", file=sys.stderr)
    threading.Thread(target=mcp_server.S.heartbeat_loop, daemon=True).start()
    try:
        summary = asyncio.run(run_bridge(project, prompt))
        print(f"cadswarm: {summary}", file=sys.stderr)
        return 0
    except (BridgeError, creao.CreaoError) as e:
        print(f"cadswarm: {e}", file=sys.stderr)
        return 1
    finally:
        # A one-job agent leaves so finished agents don't linger in the team list (same as mcp_server).
        if mcp_server.S.cfg.get("ephemeral"):
            mcp_server.S.dead = mcp_server.S.dead or "left"
            try:
                mcp_server.S.client.post("/leave")
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
