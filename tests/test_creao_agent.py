"""The Creao bridge: one JSON tool call per turn, executed in-process with the MCP tools."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from cadswarm import creao, creao_agent


class Tool:
    def __init__(self, name, description="does a thing"):
        self.name = name
        self.description = description
        self.input_schema = {"type": "object", "properties": {"detail": {"type": "boolean"}}}


class Tools:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def list_tools(self):
        return [Tool("model_status"), Tool("send_message")]

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if self.error:
            raise self.error
        return SimpleNamespace(structured_content={"ok": True, "tool": name}, content=[],
                               is_error=False)


class FakeCreao:
    """Stands in for creao.Creao: scripted reply texts (or exceptions) per run."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.inputs = []

    def run(self, agent_id, input, conversation_id=None, model=None):
        self.inputs.append({"agent_id": agent_id, "input": input,
                            "conversation_id": conversation_id, "model": model})
        return f"run-{len(self.inputs)}", conversation_id or "conv-new"

    def wait(self, run_id):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict):
            return reply
        return {"status": "completed", "result": {"text": reply}, "usage": {"credits": 3}}


def helper(tmp_path, monkeypatch, conversation_id=None):
    monkeypatch.setenv("CREAO_API_KEY", "cr_sk_test")
    monkeypatch.delenv("CREAO_AGENT_ID", raising=False)
    monkeypatch.delenv("CREAO_MODEL", raising=False)
    monkeypatch.setattr(creao, "shared_path", lambda: str(tmp_path / "shared-creao.json"))
    creao.save_config(str(tmp_path), agent_id="agent-9", conversation_id=conversation_id)
    return str(tmp_path)


def bridge(project, replies, tools=None, **kw):
    client = FakeCreao(replies)
    logs = []
    summary = asyncio.run(creao_agent.run_bridge(project, kw.pop("prompt", "do the task"), client=client,
                                                 tools=tools or Tools(), log=logs.append, **kw))
    return summary, client, logs


def test_a_tool_call_runs_in_process_and_done_returns_the_summary(tmp_path, monkeypatch):
    project = helper(tmp_path, monkeypatch)
    tools = Tools()
    summary, client, logs = bridge(project, [
        '{"tool": "model_status", "args": {"detail": true}}',
        '{"done": true, "summary": "The bracket is done."}',
    ], tools)
    assert summary == "The bracket is done."
    assert tools.calls == [("model_status", {"detail": True})]
    first = text(client, 0)
    assert "CadSwarm tools:" in first and "model_status:" in first and "do the task" in first
    assert client.inputs[0]["agent_id"] == "agent-9"
    assert "Tool model_status returned:" in text(client, 1)
    assert json_of(text(client, 1))["result"]["ok"] is True
    assert any("turn 1: model_status" in line for line in logs)
    assert creao.load_config(project)["conversation_id"] == "conv-new"


def json_of(text):
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def text(client, i):
    return client.inputs[i]["input"]["message"]


def test_a_cached_conversation_gets_the_task_but_not_the_catalog_again(tmp_path, monkeypatch):
    project = helper(tmp_path, monkeypatch, conversation_id="conv-old")
    summary, client, _ = bridge(project, ['{"done": true, "summary": "ok"}'])
    assert summary == "ok"
    assert client.inputs[0]["conversation_id"] == "conv-old"
    assert "CadSwarm tools:" not in text(client, 0)
    assert "do the task" in text(client, 0)


def test_a_new_conversations_id_can_arrive_with_the_finished_run(tmp_path, monkeypatch):
    project = helper(tmp_path, monkeypatch)

    class QuietCreate(FakeCreao):
        def run(self, agent_id, input, conversation_id=None, model=None):
            self.inputs.append({"agent_id": agent_id, "input": input,
                                "conversation_id": conversation_id, "model": model})
            return f"run-{len(self.inputs)}", conversation_id  # the create response carries no id

    client = QuietCreate([{"status": "completed", "conversation_id": "conv-late",
                           "result": {"text": '{"done": true, "summary": "ok"}'}}])
    summary = asyncio.run(creao_agent.run_bridge(project, "do it", client=client, tools=Tools(),
                                                 log=lambda _t: None))
    assert summary == "ok"
    assert creao.load_config(project)["conversation_id"] == "conv-late"


def test_a_helper_without_its_own_agent_uses_the_one_this_computer_shares(tmp_path, monkeypatch):
    monkeypatch.setenv("CREAO_API_KEY", "cr_sk_test")
    monkeypatch.delenv("CREAO_AGENT_ID", raising=False)
    monkeypatch.setattr(creao, "shared_path", lambda: str(tmp_path / "shared-creao.json"))
    creao.save_shared("agent-shared")
    summary, client, _ = bridge(str(tmp_path), ['{"done": true, "summary": "ok"}'])
    assert summary == "ok" and client.inputs[0]["agent_id"] == "agent-shared"


def test_an_unknown_tool_is_refused_without_calling_anything(tmp_path, monkeypatch):
    project = helper(tmp_path, monkeypatch)
    tools = Tools()
    summary, client, logs = bridge(project, [
        '{"tool": "delete_everything", "args": {}}',
        '{"done": true, "summary": "never mind"}',
    ], tools)
    assert summary == "never mind" and tools.calls == []
    assert "no CadSwarm tool called 'delete_everything'" in text(client, 1)
    assert any("refused unknown tool" in line for line in logs)


def test_one_unparsable_reply_gets_exactly_one_repair(tmp_path, monkeypatch):
    project = helper(tmp_path, monkeypatch)
    summary, client, logs = bridge(project, [
        "Sure! I will look at the model now.",
        '{"done": true, "summary": "looked"}',
    ])
    assert summary == "looked"
    assert "was not a valid CadSwarm turn" in text(client, 1)
    assert any("asking for one repair" in line for line in logs)


def test_two_unparsable_replies_stop_the_helper(tmp_path, monkeypatch):
    project = helper(tmp_path, monkeypatch)
    with pytest.raises(creao_agent.BridgeError) as e:
        bridge(project, ["nope", "still nope"])
    assert "stopped replying" in str(e.value)


def test_the_turn_cap_stops_a_loop_that_never_finishes(tmp_path, monkeypatch):
    project = helper(tmp_path, monkeypatch)
    tools = Tools()
    with pytest.raises(creao_agent.BridgeError) as e:
        bridge(project, ['{"tool": "model_status", "args": {}}'] * 3, tools, turn_limit=2)
    assert "2 turns" in str(e.value) and len(tools.calls) == 2


def test_the_wall_clock_stops_the_helper_even_between_turns(tmp_path, monkeypatch):
    project = helper(tmp_path, monkeypatch)
    times = iter([0.0, 200.0])
    with pytest.raises(creao_agent.BridgeError) as e:
        bridge(project, [], wall_clock=120, clock=lambda: next(times))
    assert "2 minute budget" in str(e.value)


def test_a_broken_tool_is_reported_to_creao_instead_of_killing_it(tmp_path, monkeypatch):
    project = helper(tmp_path, monkeypatch)
    summary, client, _ = bridge(project, [
        '{"tool": "send_message", "args": {"text": "hi"}}',
        '{"done": true, "summary": "recovered"}',
    ], Tools(error=RuntimeError("the hub is down")))
    assert summary == "recovered"
    assert "RuntimeError: the hub is down" in text(client, 1)


def test_a_forgotten_conversation_is_replaced_and_the_task_replayed(tmp_path, monkeypatch):
    project = helper(tmp_path, monkeypatch, conversation_id="conv-gone")
    summary, client, logs = bridge(project, [
        creao.CreaoError("gone", "CONVERSATION_NOT_FOUND"),
        '{"done": true, "summary": "started over"}',
    ])
    assert summary == "started over"
    assert client.inputs[0]["conversation_id"] == "conv-gone"
    assert client.inputs[1]["conversation_id"] is None
    assert "CadSwarm tools:" in text(client, 1)
    assert any("forgot this helper's conversation" in line for line in logs)


def test_a_failed_run_is_a_clear_error_with_its_code(tmp_path, monkeypatch):
    project = helper(tmp_path, monkeypatch)
    with pytest.raises(creao.CreaoError) as e:
        bridge(project, [{"status": "failed", "error": {"code": "INSUFFICIENT_CREDITS"}}])
    assert e.value.code == "INSUFFICIENT_CREDITS" and "failed" in str(e.value)


def test_no_key_or_agent_means_the_bridge_refuses_before_any_request(tmp_path, monkeypatch):
    monkeypatch.setattr(creao, "shared_path", lambda: str(tmp_path / "shared.json"))
    monkeypatch.setattr(creao, "api_key", lambda: "")
    with pytest.raises(creao_agent.BridgeError, match="CREAO_API_KEY"):
        asyncio.run(creao_agent.run_bridge(str(tmp_path), "x", client=FakeCreao([]), tools=Tools(),
                                           log=lambda _t: None))
    monkeypatch.setattr(creao, "api_key", lambda: "cr_sk_test")
    with pytest.raises(creao_agent.BridgeError, match="no Creao agent"):
        asyncio.run(creao_agent.run_bridge(str(tmp_path), "x", client=FakeCreao([]), tools=Tools(),
                                           log=lambda _t: None))


def test_parse_turn_accepts_the_two_shapes_and_explains_anything_else():
    assert creao_agent.parse_turn('{"tool": "a", "args": {"n": 1}}') == {"tool": "a", "args": {"n": 1}}
    assert creao_agent.parse_turn('{"done": true, "summary": " ok "}') == {"done": True, "summary": "ok"}
    assert creao_agent.parse_turn('{"tool": "a"}') == {"tool": "a", "args": {}}
    with pytest.raises(ValueError, match="JSON object"):
        creao_agent.parse_turn("hello")
    with pytest.raises(ValueError, match='"args" must be'):
        creao_agent.parse_turn('{"tool": "a", "args": []}')


def test_a_fenced_or_chatty_reply_is_still_read_as_json():
    assert creao_agent.parse_turn('```json\n{"done": true, "summary": "x"}\n```')["done"] is True
    assert creao_agent.parse_turn('I will do it: {"tool": "a", "args": {}} ok')["tool"] == "a"


def test_catalogue_lists_names_descriptions_and_argument_schemas():
    text = creao_agent.catalogue_text([Tool("model_status")])
    assert "- model_status: does a thing" in text and '"detail"' in text
