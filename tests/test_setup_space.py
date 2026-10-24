"""Setting up an agent folder from an invite redeems once and writes the SpacetimeDB config."""
import json
import os

import pytest

from cadswarm import agent_setup, cli, creao, ticket
from cadswarm.invite import make_invite

T = {"v": 1, "uri": "http://127.0.0.1:3000", "db": "cadswarm", "token": "ab" * 32, "kind": "agent",
     "role": "member", "host_addr": ""}


def fake_redeem(calls):
    def redeem(t, name, client):
        calls.append((t["token"], name, client))
        return {"token": "tok", "participant_id": 4, "fence": 1, "name": name, "role": "member", "kind": "agent"}
    return redeem


def test_write_agent_config_redeems_once_and_writes_the_space_config(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(agent_setup, "redeem", fake_redeem(calls))
    agent_setup.write_agent_config(str(tmp_path), "claude", T, "alice-claude", python="python")
    agent_setup.write_agent_config(str(tmp_path), "claude", T, "alice-claude", python="python")
    assert calls == [("ab" * 32, "alice-claude", "claude")]
    cfg = json.load(open(tmp_path / ".cadswarm" / "config.json"))
    assert cfg == {"uri": "http://127.0.0.1:3000", "db": "cadswarm", "name": "alice-claude",
                   "client": "claude", "role": "member"}
    ident = json.load(open(tmp_path / ".cadswarm" / "identity.json"))
    assert ident["participant_id"] == 4 and ident["token"] == "tok"
    assert open(tmp_path / ".cadswarm" / ".gitignore").read() == "*\n"
    assert os.path.exists(tmp_path / ".mcp.json")


def test_fresh_forgets_the_identity_and_redeems_again(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(agent_setup, "redeem", fake_redeem(calls))
    agent_setup.write_agent_config(str(tmp_path), "claude", T, "alice-claude", python="python")
    agent_setup.write_agent_config(str(tmp_path), "claude", T, "alice-claude", python="python", fresh=True)
    assert len(calls) == 2


def test_droid_gets_a_factory_mcp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_setup, "redeem", lambda t, n, c: {"token": "t", "participant_id": 1, "fence": 1,
                                                                 "name": n, "role": "member", "kind": "agent"})
    t = {"v": 1, "uri": "u", "db": "d", "token": "x", "kind": "agent", "role": "member", "host_addr": ""}
    written = agent_setup.write_agent_config(str(tmp_path), "droid", t, "bob-droid", python="python")
    mcp = json.load(open(tmp_path / ".factory" / "mcp.json"))
    assert mcp["mcpServers"]["cadswarm"]["args"] == [agent_setup.LAUNCHER, "mcp"]
    assert mcp["mcpServers"]["cadswarm"]["command"] == "python"
    assert mcp["mcpServers"]["cadswarm"]["env"]["CADSWARM_PROJECT"] == str(tmp_path)
    assert str(tmp_path / ".factory" / "mcp.json") in written


@pytest.mark.parametrize("code", [lambda: make_invite(T, "Lid"), lambda: ticket.encode(T),
                                  lambda: "https://cadswarm.app/#t=" + ticket.encode(T)])
def test_cli_setup_takes_any_invite_form(tmp_path, monkeypatch, capsys, code):
    calls = []
    monkeypatch.setattr(agent_setup, "redeem", fake_redeem(calls))
    cli.main(["setup", "--client", "claude", "--invite", code(), "--name", "carol-claude", "--dir", str(tmp_path)])
    assert calls == [("ab" * 32, "carol-claude", "claude")]
    assert "carol-claude" in capsys.readouterr().out


def test_cli_setup_explains_a_bad_invite(tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.main(["setup", "--client", "claude", "--invite", "cadswarm-1:abc", "--name", "x", "--dir", str(tmp_path)])
    assert "older CadSwarm" in str(e.value)
    assert not (tmp_path / ".cadswarm" / "config.json").exists()


# ---- helper folders: right-team sign-in, API key, start script with the first message

def test_a_sign_in_from_another_team_or_the_old_hub_is_replaced(tmp_path, monkeypatch):
    from cadswarm import agent_setup
    redeemed = []
    monkeypatch.setattr(agent_setup, "redeem", lambda t, name, client: redeemed.append(t["db"]) or {
        "token": "new", "participant_id": 7, "fence": 1, "name": name, "role": "lead", "kind": "agent"})
    team = {"uri": "http://127.0.0.1:3000", "db": "cadswarm-a-1234", "token": "inv", "role": "lead"}
    state = tmp_path / ".cadswarm"
    state.mkdir()
    (state / "identity.json").write_text(json.dumps({"participant_id": "p_0169f3b6", "token": "old",
                                                     "hub": "http://127.0.0.1:8765"}))
    (state / "config.json").write_text(json.dumps({"uri": team["uri"], "db": team["db"]}))
    assert not agent_setup.identity_matches(str(tmp_path), team)  # the old FastAPI hub's sign-in
    agent_setup.write_agent_config(str(tmp_path), "claude", team, "anegs-claude", python="/py")
    assert redeemed == [team["db"]] and agent_setup.identity_matches(str(tmp_path), team)
    agent_setup.write_agent_config(str(tmp_path), "claude", {**team, "token": ""}, "anegs-claude", python="/py")
    assert redeemed == [team["db"]]  # same team: the sign-in is kept, no invite needed
    other = {**team, "db": "cadswarm-b-9999", "token": ""}
    assert not agent_setup.identity_matches(str(tmp_path), other)
    with pytest.raises(ValueError, match="fresh invite"):
        agent_setup.write_agent_config(str(tmp_path), "claude", other, "anegs-claude", python="/py")


@pytest.mark.skipif(os.name == "nt", reason="POSIX start script")
def test_start_script_sends_the_first_message(tmp_path, monkeypatch):
    import subprocess
    from cadswarm import agent_setup
    (tmp_path / ".cadswarm").mkdir()
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\necho "ARG=$1"\n')
    fake.chmod(0o755)
    monkeypatch.setattr(agent_setup, "find_agent_cli", lambda client: str(fake))
    script = agent_setup.write_launcher(str(tmp_path), "claude", "Robot's arm")
    first = subprocess.run(["sh", script], capture_output=True, text=True).stdout
    assert "joined our team's CAD project \"Robot's arm\"" in first
    assert "leading" not in first  # an AI helper never leads the team: people do
    again = subprocess.run(["sh", script], capture_output=True, text=True).stdout
    assert "continuing our team's CAD project" in again  # later starts carry on instead of re-planning

def test_opencode_gets_its_mcp_config_and_agents_md(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_setup, "redeem", lambda t, n, c: {"token": "t", "participant_id": 1, "fence": 1,
                                                                 "name": n, "role": "member", "kind": "agent"})
    t = {"v": 1, "uri": "u", "db": "d", "token": "x", "kind": "agent", "role": "member", "host_addr": ""}
    written = agent_setup.write_agent_config(str(tmp_path), "opencode", t, "dana-opencode", python="python")
    cfg = json.load(open(tmp_path / "opencode.json"))
    server = cfg["mcp"]["cadswarm"]
    assert server["type"] == "local"
    assert server["command"] == ["python", agent_setup.LAUNCHER, "mcp"]
    assert server["environment"]["CADSWARM_PROJECT"] == str(tmp_path)
    assert server["timeout"] == 120000
    assert cfg["$schema"] == agent_setup.OPENCODE_SCHEMA
    assert "cadswarm:begin" in open(tmp_path / "AGENTS.md").read()
    assert str(tmp_path / "opencode.json") in written
    # A setup run again must not change the file or duplicate the instructions.
    agent_setup.write_agent_config(str(tmp_path), "opencode", {**t, "token": ""}, "dana-opencode", python="python")
    assert json.load(open(tmp_path / "opencode.json")) == cfg
    assert open(tmp_path / "AGENTS.md").read().count("cadswarm:begin") == 1


def test_opencode_config_keeps_other_servers_and_settings(tmp_path):
    (tmp_path / "opencode.json").write_text(json.dumps(
        {"$schema": "https://example.test/schema.json", "theme": "dark",
         "mcp": {"other": {"type": "remote", "url": "https://x.test"}}}))
    agent_setup.setup_opencode(str(tmp_path), "/py")
    cfg = json.load(open(tmp_path / "opencode.json"))
    assert cfg["mcp"]["other"]["url"] == "https://x.test" and "cadswarm" in cfg["mcp"]
    assert cfg["theme"] == "dark" and cfg["$schema"] == "https://example.test/schema.json"


def test_opencode_launcher_puts_the_prompt_behind_its_flag():
    # A bare argument to `opencode` is a directory, so the TUI needs --prompt.
    assert agent_setup.launcher_argv("opencode", "join us") == ["--prompt", "join us"]
    assert agent_setup.launcher_argv("claude", "join us") == ["join us"]


@pytest.fixture(autouse=True)
def keep_creao_account_cache_in_the_test(tmp_path, monkeypatch):
    """The shared Creao agent lives in the user's app folder: no test may write the real one."""
    monkeypatch.setattr(creao, "shared_path", lambda: str(tmp_path / "shared-creao.json"))


class FakeCreaoApi:
    """Stands in for creao.Creao in setup tests, recording every call."""

    instances = []

    def __init__(self, key=None, **kw):
        self.key = key
        self.calls = []
        FakeCreaoApi.instances.append(self)

    def usage(self):
        self.calls.append(("usage",))
        return {"creditBalance": 10}

    def create_agent(self, description, model=None):
        self.calls.append(("create", description, model))
        return "agent-new"

    def install_skill(self, content, files=None, enabled=True):
        self.calls.append(("skill", content, files, enabled))
        return {}

    def update_agent(self, agent_id, **fields):
        self.calls.append(("update", agent_id, fields))
        return {}


def creao_invite():
    return {"v": 1, "uri": "u", "db": "d", "token": "x", "kind": "agent", "role": "member", "host_addr": ""}


def test_creao_setup_checks_the_key_makes_an_agent_and_installs_the_skill(tmp_path, monkeypatch):
    FakeCreaoApi.instances = []
    monkeypatch.setattr(creao, "Creao", FakeCreaoApi)
    monkeypatch.setenv("CREAO_API_KEY", "cr_sk_test")
    calls = []
    monkeypatch.setattr(agent_setup, "redeem", fake_redeem(calls))
    written = agent_setup.write_agent_config(str(tmp_path), "creao", creao_invite(), "alice-creao",
                                             python="python")
    api = FakeCreaoApi.instances[-1]
    assert api.key == "cr_sk_test"
    assert [c[0] for c in api.calls] == ["usage", "create", "skill", "update"]
    assert api.calls[1][1] == creao.AGENT_DESCRIPTION and api.calls[1][2] is None
    assert creao.SKILL_NAME in api.calls[2][1]
    assert "scripts/validate_turn.py" in api.calls[2][2]
    assert api.calls[3] == ("update", "agent-new", {"write_autonomy": "full_auto"})
    saved = creao.load_config(str(tmp_path))
    assert saved == {"agent_id": "agent-new"}
    assert creao.load_shared() == {"agent_id": "agent-new"}
    assert "cr_sk_test" not in open(creao.config_path(str(tmp_path)), encoding="utf-8").read()
    assert calls == [("x", "alice-creao", "creao")]
    assert json.load(open(tmp_path / ".cadswarm" / "config.json"))["client"] == "creao"
    assert "cadswarm:begin" in open(tmp_path / "AGENTS.md").read()
    assert str(tmp_path / "AGENTS.md") in written and creao.config_path(str(tmp_path)) in written


def test_creao_setup_reuses_its_agent_and_keeps_the_conversation(tmp_path, monkeypatch):
    FakeCreaoApi.instances = []
    monkeypatch.setattr(creao, "Creao", FakeCreaoApi)
    monkeypatch.setenv("CREAO_API_KEY", "cr_sk_test")
    monkeypatch.setattr(agent_setup, "redeem", lambda t, n, c: {"token": "t", "participant_id": 1,
                                                               "fence": 1, "name": n, "role": "member"})
    creao.save_config(str(tmp_path), agent_id="agent-old", conversation_id="conv-old")
    agent_setup.write_agent_config(str(tmp_path), "creao", creao_invite(), "alice-creao", python="python")
    api = FakeCreaoApi.instances[-1]
    assert not any(c[0] == "create" for c in api.calls)
    assert ("update", "agent-old", {"write_autonomy": "full_auto"}) in api.calls
    assert creao.load_config(str(tmp_path)) == {"agent_id": "agent-old", "conversation_id": "conv-old"}
    assert creao.load_shared() == {"agent_id": "agent-old"}  # a folder from before the shared cache hands it up


def test_a_second_helper_folder_reuses_the_shared_creao_agent(tmp_path, monkeypatch):
    FakeCreaoApi.instances = []
    monkeypatch.setattr(creao, "Creao", FakeCreaoApi)
    monkeypatch.setenv("CREAO_API_KEY", "cr_sk_test")
    monkeypatch.setattr(agent_setup, "redeem", lambda t, n, c: {"token": "t", "participant_id": 1,
                                                               "fence": 1, "name": n, "role": "member"})
    creao.save_shared("agent-shared")
    folder = tmp_path / "second"
    agent_setup.write_agent_config(str(folder), "creao", creao_invite(), "bob-creao", python="python")
    api = FakeCreaoApi.instances[-1]
    assert not any(c[0] == "create" for c in api.calls)  # one agent per computer, not one per helper
    assert ("update", "agent-shared", {"write_autonomy": "full_auto"}) in api.calls
    assert creao.load_config(str(folder)) == {"agent_id": "agent-shared"}
    assert creao.load_shared() == {"agent_id": "agent-shared"}


def test_creao_fresh_setup_forgets_the_conversation(tmp_path, monkeypatch):
    FakeCreaoApi.instances = []
    monkeypatch.setattr(creao, "Creao", FakeCreaoApi)
    monkeypatch.setenv("CREAO_API_KEY", "cr_sk_test")
    monkeypatch.setattr(agent_setup, "redeem", lambda t, n, c: {"token": "t", "participant_id": 1,
                                                               "fence": 1, "name": n, "role": "member"})
    creao.save_config(str(tmp_path), agent_id="agent-old", conversation_id="conv-old")
    agent_setup.write_agent_config(str(tmp_path), "creao", creao_invite(), "alice-creao", fresh=True,
                                   python="python")
    assert creao.load_config(str(tmp_path)) == {"agent_id": "agent-old"}


def test_a_bad_creao_key_fails_before_the_invite_is_redeemed(tmp_path, monkeypatch):
    class BadKey(FakeCreaoApi):
        def usage(self):
            raise creao.CreaoError("Creao rejected the API key", "AUTHENTICATION_REQUIRED")

    monkeypatch.setattr(creao, "Creao", BadKey)
    monkeypatch.setenv("CREAO_API_KEY", "cr_sk_bad")
    calls = []
    monkeypatch.setattr(agent_setup, "redeem", fake_redeem(calls))
    with pytest.raises(ValueError, match="rejected the API key"):
        agent_setup.write_agent_config(str(tmp_path), "creao", creao_invite(), "alice-creao", python="python")
    assert calls == []  # the single-use invite is still good


def test_creao_setup_without_a_key_never_touches_the_invite(tmp_path, monkeypatch):
    monkeypatch.setattr(creao, "api_key", lambda: "")
    calls = []
    monkeypatch.setattr(agent_setup, "redeem", fake_redeem(calls))
    with pytest.raises(ValueError, match="CREAO_API_KEY"):
        agent_setup.write_agent_config(str(tmp_path), "creao", creao_invite(), "alice-creao", python="python")
    assert calls == [] and not os.path.exists(creao.config_path(str(tmp_path)))


def test_creao_has_no_cli_but_its_bridge_launcher_runs_the_runner(tmp_path, monkeypatch):
    assert agent_setup.find_agent_cli("creao") is None
    monkeypatch.setattr(creao, "api_key", lambda: "cr_sk_test")
    assert agent_setup.agent_ready("creao") is True
    monkeypatch.setattr(creao, "api_key", lambda: "")
    assert agent_setup.agent_ready("creao") is False
    (tmp_path / ".cadswarm").mkdir()
    path = agent_setup.write_launcher(str(tmp_path), "creao", "Team project")
    text = open(path, encoding="utf-8").read()
    assert "runner" in text and str(tmp_path) in text
    with pytest.raises(RuntimeError, match="no terminal"):
        agent_setup.open_agent_terminal("creao", str(tmp_path))


def test_nvm_installs_are_ordered_by_version_not_text():
    paths = [f"/h/.nvm/versions/node/v{v}/bin/claude" for v in ("9.11.2", "22.1.0", "18.20.4")]
    newest = sorted(paths, key=agent_setup._node_version, reverse=True)[0]
    assert "v22.1.0" in newest
