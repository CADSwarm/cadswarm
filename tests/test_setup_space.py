"""Setting up an agent folder from an invite redeems once and writes the SpacetimeDB config."""
import json
import os

import pytest

from cadswarm import agent_setup, cli, ticket
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
