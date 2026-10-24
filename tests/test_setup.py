"""Setup pieces the FreeCAD guide relies on: invite codes, agent wiring, Codex trust, the launcher."""
import json
import os
import subprocess
import sys
import tomllib

import pytest

from cadswarm.agent_setup import codex_is_trusted, trust_in_codex, write_agent_config
from cadswarm.invite import InviteError, make_invite, read_invite
from cadswarm.paths import LAUNCHER


def test_invite_round_trip_survives_chat_app_mangling():
    code = make_invite("http://192.168.1.20:8765/", "tok_ABC-123", "Phone stand")
    mangled = f'  "{code[:20]}\n{code[20:]}"  '  # quotes, spaces and a line break from a chat app
    assert read_invite(mangled) == {"hub": "http://192.168.1.20:8765", "token": "tok_ABC-123",
                                    "project": "Phone stand"}


@pytest.mark.parametrize("text, fragment", [
    ("", "Paste the invite code"),
    ("http://192.168.1.20:8765", "doesn't look like a CadSwarm invite"),
    ("cadswarm-1:eyJoIjoiaHR0", "cut off"),
])
def test_invite_errors_are_plain_language(text, fragment):
    with pytest.raises(InviteError) as e:
        read_invite(text)
    assert fragment in str(e.value)


def test_claude_config_uses_launcher_and_keeps_other_hooks(tmp_path):
    proj = tmp_path / "carol-claude"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(json.dumps(
        {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other-tool"}]}]}}))
    py = r"C:\FreeCAD\bin\python.exe"
    for _ in range(2):  # idempotent
        write_agent_config(str(proj), "claude", "http://h:1", "tok", "carol-claude", "lead", python=py)
    mcp = json.loads((proj / ".mcp.json").read_text())["mcpServers"]["cadswarm"]
    assert mcp["command"] == py and mcp["args"] == [LAUNCHER, "mcp"]
    hooks = json.loads((proj / ".claude" / "settings.json").read_text())["hooks"]["UserPromptSubmit"]
    commands = [h["command"] for g in hooks for h in g["hooks"]]
    assert commands.count(f'"{py}" "{LAUNCHER}" hook') == 1 and "other-tool" in commands
    cfg = json.loads((proj / ".cadswarm" / "config.json").read_text())
    assert cfg == {"hub": "http://h:1", "workspace_token": "tok", "name": "carol-claude", "client": "claude",
                   "role": "lead"}


def test_codex_config_parses_and_trust_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codexhome"))
    proj = tmp_path / "it's dana-codex"  # an apostrophe forces the basic-string TOML path
    write_agent_config(str(proj), "codex", "http://h:1", "tok", "dana-codex", python=r"C:\FC\bin\python.exe")
    cfg = tomllib.loads((proj / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert cfg["mcp_servers"]["cadswarm"]["args"] == [LAUNCHER, "mcp"]
    assert cfg["mcp_servers"]["cadswarm"]["env"]["CADSWARM_PROJECT"] == str(proj)
    assert "hook" in cfg["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert not codex_is_trusted(str(proj))
    assert trust_in_codex(str(proj)) is True and trust_in_codex(str(proj)) is False
    home = tomllib.loads((tmp_path / "codexhome" / "config.toml").read_text(encoding="utf-8"))
    assert home["projects"][str(proj)]["trust_level"] == "trusted"


def test_launcher_hook_fails_open_outside_a_project(tmp_path):
    r = subprocess.run([sys.executable, LAUNCHER, "hook"], input=json.dumps({"cwd": str(tmp_path)}),
                       capture_output=True, text=True, timeout=30, cwd=tmp_path)
    assert r.returncode == 0 and r.stdout == ""


def test_launcher_check_reports_deps(tmp_path):
    r = subprocess.run([sys.executable, LAUNCHER, "check"], capture_output=True, text=True, timeout=60,
                       env={**os.environ, "CADSWARM_DEPS": str(tmp_path / "none")})
    assert r.stdout.strip() == "ok"  # the dev venv already has the packages


def test_resuming_a_saved_signin_without_fence(tmp_path):
    """The FreeCAD guide stores only participant id + token: joining with it yields the fence,
    and acting before that join is refused rather than treated as fence-less."""
    from fastapi.testclient import TestClient
    from cadswarm.client import HubClient
    from cadswarm.hub.app import create_app
    from cadswarm.hub.core import Hub
    from test_core import FakeWorker

    hub = Hub(FakeWorker(), str(tmp_path / "hub.sqlite"), "secret")
    http = TestClient(create_app(hub, str(tmp_path), sweep_seconds=3600))
    first = http.post("/join", json={"workspace_token": "secret", "name": "carol"}).json()

    class Client(HubClient):  # route the stdlib client's requests through the in-process app
        def request(self, method, path, body=None, params=None, timeout=None, raw=False):
            r = http.request(method, path, json=body, params=params, headers=self.headers())
            if r.status_code >= 400:
                from cadswarm.client import HubRequestError
                raise HubRequestError(r.status_code, r.json())
            return r.json()

    c = Client("http://h", {"participant_id": first["participant_id"], "token": first["token"]})
    with pytest.raises(Exception) as e:
        c.get("/model")
    assert e.value.status == 400 and e.value.code == "missing_fence"
    ident = c.join("secret", "carol")
    assert ident["participant_id"] == first["participant_id"] and ident["fence"] == first["fence"] + 1
    assert "objects" in c.get("/model")
