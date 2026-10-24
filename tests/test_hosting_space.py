"""Hosting a team on SpacetimeDB from the FreeCAD guide (plan 03 Task 6), with processes and HTTP mocked."""
import json
import os

import pytest

from cadswarm import hosting


@pytest.fixture
def fake(tmp_path, monkeypatch):
    calls = {"popen": [], "run": [], "http": []}
    host = tmp_path / "bin" / "cadswarm-host"
    host.parent.mkdir()
    host.write_text("")
    wasm = tmp_path / "module.wasm"
    wasm.write_bytes(b"\0asm")
    monkeypatch.setenv("CADSWARM_HOST_BIN", str(host))
    monkeypatch.setenv("CADSWARM_MODULE_WASM", str(wasm))
    monkeypatch.delenv("CADSWARM_HOST_TOKEN", raising=False)
    up = {"v": False}
    monkeypatch.setattr(hosting, "space_up", lambda uri, timeout=1.5: up["v"])

    def background(cmd, log, env=None):
        calls["popen"].append((cmd, env))
        if "start" in cmd:
            up["v"] = True
        return 4000 + len(calls["popen"])
    monkeypatch.setattr(hosting, "_background", background)
    monkeypatch.setattr(hosting, "find_tool", lambda name, env=None: str(host) if name == "cadswarm-host" else "/x/spacetime")
    monkeypatch.setattr(hosting, "pid_alive", lambda pid: True)

    def http(method, url, data=None, token=None, content_type=None, timeout=120):
        calls["http"].append((method, url, token))
        return json.dumps({"token": "tok-local"}) if url.endswith("/v1/identity") else '{"Success": {}}'
    monkeypatch.setattr(hosting, "_http", http)

    class Done:
        returncode, stdout, stderr = 0, "wrote host.json", ""

    def run(cmd, **kw):
        calls["run"].append((cmd, kw.get("env", {})))
        (tmp_path / "proj" / "host.json").write_text(json.dumps({"mode": "local", "db": cmd[cmd.index("--db") + 1],
                                                                   "token": "tok-local"}))
        return Done()
    monkeypatch.setattr(hosting.subprocess, "run", run)
    return calls, up, tmp_path


def test_find_tool_prefers_the_env_variable(tmp_path, monkeypatch):
    exe = tmp_path / "my-host"
    exe.write_text("")
    monkeypatch.setenv("CADSWARM_HOST_BIN", str(exe))
    assert hosting.find_tool("cadswarm-host") == str(exe)


def test_local_team_starts_spacetime_publishes_then_inits_and_runs_the_host(fake):
    calls, up, tmp = fake
    proj = str(tmp / "proj")
    info = hosting.start_team("/fc/python", proj, "local")
    assert info["mode"] == "local" and info["uri"] == "http://127.0.0.1:3000" and info["db"].startswith("cadswarm-proj-")
    assert calls["popen"][0][0][1:] == ["start", "--listen-addr", "0.0.0.0:3000", "--non-interactive"]
    assert [m for m, _, _ in calls["http"]] == ["POST", "PUT"]  # a host identity, then the module published with it
    assert calls["http"][1][1].endswith(f"/v1/database/{info['db']}") and calls["http"][1][2] == "tok-local"
    init, env = calls["run"][0]
    assert init[init.index("init") + 1:] == ["--mode", "local", "--db", info["db"], "--python", "/fc/python"]
    assert env["CADSWARM_HOST_TOKEN"] == "tok-local" and os.path.isdir(env["CADSWARM_ROOT"])
    run_cmd, run_env = calls["popen"][1]
    assert run_cmd[-1] == "run" and info["host_pid"] == 4002 and info["spacetime_pid"] == 4001
    assert "CADSWARM_ROOT" in run_env


def test_a_project_with_host_json_keeps_its_database_and_identity(fake):
    calls, up, tmp = fake
    up["v"] = True  # SpacetimeDB is already running: don't start another
    proj = tmp / "proj"
    proj.mkdir()
    (proj / "host.json").write_text(json.dumps({"mode": "local", "db": "cadswarm-old-1234", "token": "tok-old"}))
    info = hosting.start_team("/fc/python", str(proj), "local")
    assert info["db"] == "cadswarm-old-1234" and info["spacetime_pid"] is None
    assert not calls["run"]  # no second init
    assert calls["http"] == [("PUT", "http://127.0.0.1:3000/v1/database/cadswarm-old-1234", "tok-old")]


def test_team_ready_needs_a_ready_status_with_a_live_pid(tmp_path, monkeypatch):
    internal = tmp_path / ".cadswarm-team"
    internal.mkdir()
    assert not hosting.team_ready(str(tmp_path))
    (internal / "host-status.json").write_text(json.dumps({"ready": True, "pid": 77}))
    monkeypatch.setattr(hosting, "pid_alive", lambda pid: pid == 77)
    assert hosting.team_ready(str(tmp_path)) and not hosting.team_ready(str(tmp_path), pid=78)
    monkeypatch.setattr(hosting, "pid_alive", lambda pid: False)
    assert not hosting.team_ready(str(tmp_path))


def test_missing_tools_say_what_to_install(tmp_path, monkeypatch):
    monkeypatch.setattr(hosting, "find_tool", lambda name, env=None: None)
    with pytest.raises(RuntimeError, match="cadswarm-host"):
        hosting.start_team("/fc/python", str(tmp_path), "local")
    with pytest.raises(RuntimeError, match="spacetimedb.com/install"):
        hosting.start_spacetime(str(tmp_path))


def test_db_names_are_slugged_and_unique():
    a, b = hosting.db_name("Phone stand!"), hosting.db_name("Phone stand!")
    assert a.startswith("cadswarm-phone-stand-") and a != b
