"""The Creao client: run polling, rate limits, and the cached ids we keep (never the key)."""
import json

import pytest

from cadswarm import creao


class Stub:
    """An HTTP opener with scripted (status, headers, body) replies, recording what was asked."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request, timeout):
        body = json.loads(request.data) if request.data else None
        self.requests.append((request.get_method(), request.full_url, body, timeout))
        status, headers, payload = self.replies.pop(0)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, headers, raw


def api(replies, **kw):
    stub = Stub(replies)
    sleeps = []
    return creao.Creao(key=kw.pop("key", "cr_sk_test"), opener=stub, sleep=sleeps.append, **kw), stub, sleeps


def test_usage_is_the_cheap_authenticated_call():
    client, stub, _ = api([(200, {}, {"creditBalance": 12})])
    assert client.usage() == {"creditBalance": 12}
    method, url, body, _ = stub.requests[0]
    assert (method, url, body) == ("GET", "https://developer.creao.ai/v1/account/usage", None)


def test_create_agent_polls_its_creation_run_for_the_agent_id():
    client, stub, _ = api([
        (202, {}, {"run_id": "run-1", "status": "pending"}),
        (200, {}, {"run_id": "run-1", "status": "running"}),
        (200, {}, {"run_id": "run-1", "status": "completed", "result": {"agent_id": "agent-9"}}),
    ])
    assert client.create_agent("a CadSwarm helper", model="pro") == "agent-9"
    assert stub.requests[0][:3] == ("POST", "https://developer.creao.ai/v1/agents",
                                    {"input": "a CadSwarm helper", "model": "pro"})
    assert stub.requests[1][1].endswith("/v1/runs/run-1?raw_output=true")


def test_run_sends_the_conversation_and_returns_the_id_creao_made():
    client, stub, _ = api([(202, {}, {"run_id": "run-2", "conversation_id": "conv-2"})])
    assert client.run("agent-9", {"message": "hello"}, conversation_id="conv-1") == ("run-2", "conv-2")
    assert stub.requests[0][2] == {"agent_id": "agent-9", "input": {"message": "hello"},
                                   "conversation_id": "conv-1"}


def test_wait_returns_completed_failed_and_cancelled_runs_without_raising():
    for status in ("completed", "failed", "cancelled"):
        client, _, _ = api([(200, {}, {"run_id": "r", "status": status})])
        assert client.wait("r")["status"] == status


def test_a_failed_creation_run_reports_creaos_code():
    client, _, _ = api([
        (202, {}, {"run_id": "run-1"}),
        (200, {}, {"status": "failed", "error": {"code": "INSUFFICIENT_CREDITS", "available": 0,
                                                 "required": 4}}),
    ])
    with pytest.raises(creao.CreaoError) as e:
        client.create_agent("helper")
    assert e.value.code == "INSUFFICIENT_CREDITS" and "INSUFFICIENT_CREDITS" in str(e.value)


def test_a_rate_limit_waits_for_retry_after_and_tries_again():
    client, stub, sleeps = api([
        (429, {"Retry-After": "0"}, {"error": "RATE_LIMITED"}),
        (200, {}, {"creditBalance": 1}),
    ])
    assert client.usage() == {"creditBalance": 1}
    assert sleeps == [0.0] and len(stub.requests) == 2


def test_a_rate_limit_that_never_clears_is_a_clear_error():
    client, _, _ = api([(429, {}, {"error": "RATE_LIMITED"})] * 3)
    with pytest.raises(creao.CreaoError) as e:
        client.usage()
    assert e.value.code == "RATE_LIMITED" and "rate limiting" in str(e.value)


def test_a_bad_key_says_what_to_fix():
    client, _, _ = api([(401, {}, {"error": "AUTHENTICATION_REQUIRED"})])
    with pytest.raises(creao.CreaoError) as e:
        client.usage()
    assert e.value.code == "AUTHENTICATION_REQUIRED" and "CREAO_API_KEY" in str(e.value)


def test_no_key_means_no_request_at_all():
    client, stub, _ = api([], key="")
    with pytest.raises(creao.CreaoError) as e:
        client.usage()
    assert "CREAO_API_KEY" in str(e.value) and stub.requests == []


def test_a_run_that_never_finishes_times_out_with_its_id():
    times = iter([0.0, 200.0])
    client, stub, _ = api([(200, {}, {"run_id": "slow", "status": "running"})], clock=lambda: next(times))
    with pytest.raises(creao.CreaoError) as e:
        client.wait("slow", timeout=60)
    assert "slow" in str(e.value) and "didn't finish" in str(e.value)


def test_the_cached_ids_round_trip_without_the_key(tmp_path):
    project = str(tmp_path)
    creao.save_config(project, agent_id="agent-9", conversation_id="conv-1", model="pro")
    assert creao.load_config(project) == {"agent_id": "agent-9", "conversation_id": "conv-1", "model": "pro"}
    assert "cr_sk" not in open(creao.config_path(project), encoding="utf-8").read()
    assert creao.clear_conversation(project) is True
    assert creao.load_config(project) == {"agent_id": "agent-9", "model": "pro"}
    assert creao.clear_conversation(project) is False


def test_the_env_file_supplies_the_key_without_overriding_a_real_one(tmp_path, monkeypatch):
    env_file = tmp_path / "cadswarm.env"
    env_file.write_text("CREAO_API_KEY=cr_sk_from_file\nCADSWARM_OPENCODE=keep\n", encoding="utf-8")
    monkeypatch.delenv("CREAO_API_KEY", raising=False)
    monkeypatch.setenv("CADSWARM_ENV_FILE", str(env_file))
    try:
        assert creao.api_key() == "cr_sk_from_file"
    finally:
        monkeypatch.delenv("CREAO_API_KEY", raising=False)
    monkeypatch.setenv("CREAO_API_KEY", "cr_sk_real")
    assert creao.api_key() == "cr_sk_real"


def test_the_per_user_env_file_is_read_when_no_checkout_file_exists(tmp_path, monkeypatch):
    target = tmp_path / "app" / "cadswarm.env"
    target.parent.mkdir()
    target.write_text("CREAO_API_KEY=cr_sk_app\n", encoding="utf-8")
    monkeypatch.delenv("CREAO_API_KEY", raising=False)
    monkeypatch.delenv("CADSWARM_ENV_FILE", raising=False)
    monkeypatch.setattr(creao, "env_candidates",
                        lambda: [None, str(target), str(tmp_path / ".env"), str(tmp_path / "sibling.env")])
    assert creao.api_key() == "cr_sk_app"


def test_saving_a_key_replaces_it_in_place_and_keeps_the_other_keys(tmp_path, monkeypatch):
    env_file = tmp_path / "cadswarm.env"
    env_file.write_text("# shared keys\nELASTIC_URL=https://x\nCREAO_API_KEY=cr_sk_old\n", encoding="utf-8")
    monkeypatch.setenv("CADSWARM_ENV_FILE", str(env_file))
    monkeypatch.setenv("CREAO_API_KEY", "cr_sk_old")
    assert creao.save_api_key("  cr_sk_new  ") == str(env_file)
    text = env_file.read_text(encoding="utf-8")
    assert "CREAO_API_KEY=cr_sk_new" in text and "cr_sk_old" not in text
    assert "ELASTIC_URL=https://x" in text and "# shared keys" in text
    assert creao.api_key() == "cr_sk_new"


def test_saving_a_key_creates_the_per_user_env_file(tmp_path, monkeypatch):
    target = tmp_path / "app" / "cadswarm.env"
    monkeypatch.delenv("CADSWARM_ENV_FILE", raising=False)
    monkeypatch.delenv("CREAO_API_KEY", raising=False)
    monkeypatch.setattr(creao, "env_candidates",
                        lambda: [None, str(target), str(tmp_path / ".env"), str(tmp_path / "sibling.env")])
    assert creao.env_file() == str(target)
    assert creao.save_api_key("cr_sk_new") == str(target)
    assert target.read_text(encoding="utf-8") == "CREAO_API_KEY=cr_sk_new\n"
    assert creao.api_key() == "cr_sk_new"
    with pytest.raises(ValueError, match="Paste"):
        creao.save_api_key("  ")


def test_the_shared_agent_cache_keeps_only_the_agent_id(tmp_path, monkeypatch):
    monkeypatch.setattr(creao, "shared_path", lambda: str(tmp_path / "creao.json"))
    assert creao.load_shared() == {}
    creao.save_shared("agent-9")
    assert creao.load_shared() == {"agent_id": "agent-9"}


def test_creao_reset_forgets_the_conversation_and_keeps_the_agent(tmp_path, capsys):
    from cadswarm import cli
    creao.save_config(str(tmp_path), agent_id="agent-9", conversation_id="conv-1")
    cli.main(["creao", "reset", "--dir", str(tmp_path)])
    assert creao.load_config(str(tmp_path)) == {"agent_id": "agent-9"}
    assert "new conversation" in capsys.readouterr().out
