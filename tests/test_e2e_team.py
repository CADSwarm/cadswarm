"""End-to-end multiplayer on a real local team (tests/e2e/team_e2e.py). Marked `e2e` and skipped unless
CADSWARM_E2E=1, so the normal suite stays fast:

    $env:CADSWARM_E2E = "1"; python -m pytest -m e2e tests/test_e2e_team.py -q
    $env:CADSWARM_E2E_AGENT = "claude"   # the build/handoff scenarios' agent CLI; default `fake` (scripted MCP agent)

Each test hosts its own throwaway team database on the local SpacetimeDB (port 3000, started if needed).
"""
import importlib.util
import os
import shutil

import pytest

pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(os.environ.get("CADSWARM_E2E") != "1", reason="set CADSWARM_E2E=1 (needs SpacetimeDB, "
                                 "FreeCAD and the host/module builds; see tests/e2e/README.md)")]

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("team_e2e", os.path.join(HERE, "e2e", "team_e2e.py"))
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)

AGENT = os.environ.get("CADSWARM_E2E_AGENT", "fake")


@pytest.fixture
def team(tmp_path):
    host, wasm, missing = e2e.built_tools()
    if missing:
        pytest.skip(f"build first: {missing}")
    if not e2e.port_open() and not shutil.which("spacetime") and not e2e.hosting.spacetime_bin():
        pytest.skip("no SpacetimeDB CLI")
    fake = [os.path.join(os.path.dirname(os.sys.executable), "python"), os.path.join(HERE, "e2e", "fake_agent.py"),
            "{prompt}"] if AGENT == "fake" else None
    t = e2e.Team(str(tmp_path), AGENT, fake).start()
    yield t
    t.stop()


def _check(result):
    if result.get("skipped"):
        pytest.skip(result["skipped"])
    assert result["ok"], result.get("checks") or result


def test_two_app_windows_presence_chat_locks_pointers(team, tmp_path):
    _check(e2e.scenario_team(team, str(tmp_path)))


def test_step_build_by_agents_over_mcp(team, tmp_path):
    _check(e2e.scenario_build(team, str(tmp_path), AGENT))


def test_web_remote_view_and_control(team, tmp_path):
    _check(e2e.scenario_remote(team, str(tmp_path)))


def test_handoff_to_an_agent(team, tmp_path):
    _check(e2e.scenario_handoff(team, str(tmp_path)))
