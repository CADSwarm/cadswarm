"""The guide starts cadswarm-host (and a local SpacetimeDB) instead of the old Python hub."""
import json
from types import SimpleNamespace

import pytest

from cadswarm import hosting, ticket


def ok(argv, **kw):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_find_tool_prefers_the_env_var(tmp_path, monkeypatch):
    tool = tmp_path / "cadswarm-host.exe"
    tool.write_text("")
    monkeypatch.setenv("CADSWARM_HOST_BIN", str(tool))
    monkeypatch.setattr(hosting.shutil, "which", lambda name: "elsewhere/" + name)
    assert hosting.find_tool("cadswarm-host", "CADSWARM_HOST_BIN") == str(tool)


def test_find_tool_falls_back_to_the_install_folders(tmp_path, monkeypatch):
    local = tmp_path / "local"
    (local / "SpacetimeDB").mkdir(parents=True)
    exe = local / "SpacetimeDB" / ("spacetime.exe" if hosting.os.name == "nt" else "spacetime")
    exe.write_text("")
    monkeypatch.delenv("SPACETIME_BIN", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(hosting.shutil, "which", lambda name: None)
    monkeypatch.setattr(hosting, "ROOT", str(tmp_path / "checkout"))
    assert hosting.find_tool("spacetime", "SPACETIME_BIN") == str(exe)


def test_start_team_says_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(hosting, "find_tool", lambda name, env: None)
    with pytest.raises(RuntimeError, match="cadswarm-host"):
        hosting.start_team("python", str(tmp_path / "p"), "maincloud", "cadswarm-x-0001")


def test_publish_uses_the_built_wasm_and_never_prompts(tmp_path, monkeypatch):
    wasm = tmp_path / "cadswarm_module.wasm"
    wasm.write_text("x")
    seen = []
    monkeypatch.setattr(hosting, "spacetime_bin", lambda: "spacetime")
    monkeypatch.setattr(hosting, "module_wasm", lambda: str(wasm))
    monkeypatch.setattr(hosting.subprocess, "run", lambda argv, **kw: (seen.append(argv), ok(argv))[1])
    hosting.publish("cadswarm-phone-1a2b", "maincloud")
    argv = seen[0]
    assert argv[:4] == ["spacetime", "publish", "--server", "maincloud"]
    assert "--yes=all" in argv and "--delete-data=never" in argv and "--bin-path" in argv and str(wasm) in argv
    assert argv[-1] == "cadswarm-phone-1a2b"


def test_publish_explains_a_missing_login(monkeypatch):
    monkeypatch.setattr(hosting, "spacetime_bin", lambda: "spacetime")
    monkeypatch.setattr(hosting, "module_wasm", lambda: None)
    monkeypatch.setattr(hosting.subprocess, "run",
                        lambda argv, **kw: SimpleNamespace(returncode=1, stdout="", stderr="not logged in"))
    with pytest.raises(RuntimeError, match="spacetime login"):
        hosting.publish("cadswarm-x-0001", "maincloud")


def test_start_team_maincloud_starts_only_the_host(tmp_path, monkeypatch):
    data = tmp_path / "proj"
    spawns, runs, published = [], [], []

    def spawn(argv, lay, log_name, env=None):
        spawns.append(argv)
        return 111

    monkeypatch.setattr(hosting, "find_tool", lambda name, env: name)
    monkeypatch.setattr(hosting, "publish", lambda db, mode: published.append((db, mode)))
    monkeypatch.setattr(hosting, "_spawn", spawn)
    monkeypatch.setattr(hosting.subprocess, "run", lambda argv, **kw: (runs.append(argv), ok(argv))[1])

    info = hosting.start_team("python.exe", str(data), "maincloud", "cadswarm-phone-1a2b")
    init = [a for a in runs if "init" in a]
    assert len(init) == 1 and init[0][:3] == ["cadswarm-host", "--data", str(data)]
    assert "maincloud" in init[0] and "cadswarm-phone-1a2b" in init[0]
    assert "python.exe" in init[0] and "--agent" in init[0]
    assert [a[3] for a in spawns] == ["run"]  # never `spacetime start` in maincloud mode
    assert published == [("cadswarm-phone-1a2b", "maincloud")]
    assert info["host_pid"] == 111 and info["spacetime_pid"] is None
    assert info["uri"] == hosting.MAINCLOUD_URI and info["db"] == "cadswarm-phone-1a2b"
    assert (data / "README.txt").exists() and (data / "files").is_dir()

    (data / "host.json").write_text(json.dumps({"mode": "maincloud", "db": "cadswarm-phone-1a2b"}))
    runs.clear()
    hosting.start_team("python.exe", str(data), "maincloud", "cadswarm-phone-1a2b")
    assert not [a for a in runs if "init" in a]  # an existing team is never re-initialized


def test_start_team_local_starts_spacetime_only_when_nothing_listens(tmp_path, monkeypatch):
    data = tmp_path / "proj"
    spawns = []
    monkeypatch.setattr(hosting, "find_tool", lambda name, env: name)
    monkeypatch.setattr(hosting, "publish", lambda db, mode: None)
    monkeypatch.setattr(hosting, "port_open", lambda port, host="127.0.0.1", timeout=0.5: False)
    monkeypatch.setattr(hosting, "_wait_port", lambda *a, **k: True)
    monkeypatch.setattr(hosting.subprocess, "run", lambda argv, **kw: ok(argv))
    monkeypatch.setattr(hosting, "_spawn",
                        lambda argv, lay, log_name, env=None: (spawns.append(argv) or (7 if argv[0] == "spacetime" else 8)))

    info = hosting.start_team("python.exe", str(data), "local", "cadswarm-x-0001")
    assert spawns[0][:4] == ["spacetime", "start", "--listen-addr", "127.0.0.1:3000"]
    assert spawns[1][3] == "run"
    assert info["spacetime_pid"] == 7 and info["host_pid"] == 8 and info["uri"] == hosting.LOCAL_URI

    spawns.clear()
    monkeypatch.setattr(hosting, "port_open", lambda port, host="127.0.0.1", timeout=0.5: True)
    info = hosting.start_team("python.exe", str(data), "local", "cadswarm-x-0001")
    assert [a[3] for a in spawns] == ["run"] and info["spacetime_pid"] is None


def test_team_ready_needs_a_ready_status_and_a_live_pid(tmp_path, monkeypatch):
    data = tmp_path / "proj"
    internal = data / ".cadswarm-team"
    internal.mkdir(parents=True)
    assert hosting.team_ready(str(data)) is False
    (internal / "host-status.json").write_text(json.dumps({"ready": False, "pid": 999}))
    monkeypatch.setattr(hosting, "pid_alive", lambda pid: True)
    assert hosting.team_ready(str(data)) is False
    (internal / "host-status.json").write_text(json.dumps({"ready": True, "pid": 999}))
    assert hosting.team_ready(str(data)) is True
    monkeypatch.setattr(hosting, "pid_alive", lambda pid: False)
    assert hosting.team_ready(str(data)) is False


def test_wait_ready_reports_a_server_that_died(tmp_path, monkeypatch):
    monkeypatch.setattr(hosting, "team_ready", lambda data_dir: False)
    monkeypatch.setattr(hosting, "pid_alive", lambda pid: False)
    with pytest.raises(RuntimeError, match="stopped while starting"):
        hosting.wait_ready(str(tmp_path), timeout=5, pids=[123])


def test_stop_team_only_stops_our_programs(monkeypatch):
    killed = []
    monkeypatch.setattr(hosting, "is_our_process",
                        lambda pid, names=hosting.HOST_PROCESS_NAMES: str(pid) in ("5", "6"))
    monkeypatch.setattr(hosting, "_kill", lambda pid: killed.append(pid))
    assert hosting.stop_team({"host_pid": 5, "spacetime_pid": 6}) is True
    assert killed == [5, 6]
    assert hosting.stop_team({"host_pid": "nonsense", "spacetime_pid": 9}) is False


def test_mint_invite_reads_the_ticket_line(tmp_path, monkeypatch):
    t = {"v": 1, "uri": "http://127.0.0.1:3000", "db": "cadswarm", "token": "ab" * 32,
         "kind": "human", "role": "lead", "host_addr": ""}
    out = SimpleNamespace(returncode=0, stdout="ticket: " + ticket.encode(t) + "\nphone:  http://x/#t=y\n",
                          stderr="")
    seen = []
    monkeypatch.setattr(hosting, "find_tool", lambda name, env: "cadswarm-host")
    monkeypatch.setattr(hosting.subprocess, "run", lambda argv, **kw: (seen.append(argv), out)[1])
    assert hosting.mint_invite(str(tmp_path), "human", "lead") == t
    assert seen[0][:2] == ["cadswarm-host", "--data"] and seen[0][3:] == ["invite", "--kind", "human",
                                                                          "--role", "lead"]


def test_mint_invite_explains_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(hosting, "find_tool", lambda name, env: "cadswarm-host")
    monkeypatch.setattr(hosting.subprocess, "run",
                        lambda argv, **kw: SimpleNamespace(returncode=1, stdout="", stderr="no host.json"))
    with pytest.raises(RuntimeError, match="Couldn't make an invite code"):
        hosting.mint_invite(str(tmp_path))
