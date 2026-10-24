"""Hardening of the setup layer: never destroy user config, tolerant invite parsing, safe hosting helpers."""
import json
import socket
import subprocess
import sys
import time

import pytest

from cadswarm import hosting
from cadswarm.agent_setup import ConfigFileError, write_agent_config
from cadswarm.invite import InviteError, make_invite, read_invite

PY = r"C:\FreeCAD\bin\python.exe"


def setup(proj):
    return write_agent_config(str(proj), "claude", TICKET, "bob", python=PY)


@pytest.fixture(autouse=True)
def no_network_redeem(monkeypatch):
    """Setup redeems the invite with SpacetimeDB; these tests are about the files it writes."""
    from cadswarm import agent_setup
    monkeypatch.setattr(agent_setup, "redeem", lambda t, name, client: {
        "token": "tok", "participant_id": 4, "fence": 1, "name": name, "role": t["role"], "kind": "agent"})


# ---- agent_setup: existing config is never silently overwritten

def test_settings_with_bom_is_merged_not_replaced(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"model": "opus", "permissions": {"allow": ["Bash(ls)"]}}).encode())
    setup(tmp_path)
    s = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert s["model"] == "opus"
    assert s["permissions"]["allow"] == ["Bash(ls)", "mcp__cadswarm"]


@pytest.mark.parametrize("text", ['{"model": "opus",}', '{"model": "opus" // mine\n}', "[1, 2]", "{broken"])
def test_unparseable_settings_is_left_alone_and_nothing_else_written(tmp_path, text):
    (tmp_path / ".claude").mkdir()
    f = tmp_path / ".claude" / "settings.json"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigFileError) as e:
        setup(tmp_path)
    assert isinstance(e.value, ValueError) and isinstance(e.value, RuntimeError)  # CLI and wizard both catch it
    assert "settings.json" in str(e.value) and "untouched" in str(e.value)
    assert f.read_text(encoding="utf-8") == text
    assert not (tmp_path / ".mcp.json").exists()  # no half-done setup


@pytest.mark.parametrize("mcp", ["[]", '{"mcpServers": []}', '"x"'])
def test_wrong_shaped_mcp_json_is_refused_not_crashed(tmp_path, mcp):
    (tmp_path / ".mcp.json").write_text(mcp)
    with pytest.raises(ConfigFileError):
        setup(tmp_path)
    assert (tmp_path / ".mcp.json").read_text() == mcp


def test_string_user_prompt_submit_hook_is_refused_not_crashed(tmp_path):
    (tmp_path / ".claude").mkdir()
    f = tmp_path / ".claude" / "settings.json"
    f.write_text('{"hooks": {"UserPromptSubmit": "echo hi"}}')
    with pytest.raises(ConfigFileError):
        setup(tmp_path)
    assert json.loads(f.read_text()) == {"hooks": {"UserPromptSubmit": "echo hi"}}


def test_odd_entries_inside_hook_list_are_kept(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(
        {"hooks": {"UserPromptSubmit": ["stray", {"hooks": "x"}, {"hooks": [{"command": 5}]}]}}))
    setup(tmp_path)
    groups = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]["UserPromptSubmit"]
    assert groups[:3] == ["stray", {"hooks": "x"}, {"hooks": [{"command": 5}]}] and len(groups) == 4


# ---- invite: the code is found inside surrounding chat text

TICKET = {"v": 1, "uri": "http://192.168.1.20:3000", "db": "cadswarm", "token": "ab" * 32,
          "kind": "human", "role": "member", "host_addr": ""}
CODE = make_invite(TICKET, "Phone stand")
EXPECT = {**TICKET, "project": "Phone stand"}


@pytest.mark.parametrize("wrap", [
    "join: {c} thanks!", "{c}.", "({c})", "“{c}”", "here you go\n{c}\nsee you soon", "{c}, then say hi",
    "\u200b{c}\u200b", "Invite: {c}\u200b!", "`{c}`", "<{c}>",
])
def test_invite_found_in_surrounding_text(wrap):
    assert read_invite(wrap.format(c=CODE)) == EXPECT


def test_invite_line_wrapped_and_followed_by_words():
    wrapped = CODE[:25] + "\n  " + CODE[25:50] + "\n" + CODE[50:] + "\nthanks a lot"
    assert read_invite(wrapped) == EXPECT


def test_invite_truncated_still_reports_incomplete():
    with pytest.raises(InviteError, match="cut off"):
        read_invite("join: " + CODE[:-12] + " thanks")


# ---- hosting

def test_pid_alive_rejects_garbage():
    for bad in ("abc", "", None, 0, -4, "1.5"):
        assert hosting.pid_alive(bad) is False
    assert hosting.pid_alive(subprocess.os.getpid())


def test_free_port_skips_loopback_listener():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        assert hosting.free_port(start=busy, tries=5) != busy


def _sleeper(tmp_path, name):
    script = tmp_path / name
    script.write_text("import time; time.sleep(60)")
    return subprocess.Popen([sys.executable, str(script)])


def test_stop_hub_refuses_an_unrelated_process(tmp_path):
    p = _sleeper(tmp_path, "innocent_bystander.py")
    try:
        time.sleep(0.5)
        assert hosting.stop_hub(p.pid) is False
        assert p.poll() is None
    finally:
        p.kill()


def test_stop_hub_stops_a_cadswarm_process(tmp_path):
    p = _sleeper(tmp_path, "cadswarm_launch.py")
    time.sleep(0.5)
    try:
        assert hosting.is_our_hub(p.pid)
        assert hosting.stop_hub(p.pid) is True
        assert p.wait(timeout=10) is not None
    finally:
        p.kill()
