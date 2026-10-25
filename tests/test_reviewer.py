import os
from types import SimpleNamespace

from cadswarm import paths, reviewer
from cadswarm.reviewer import REVIEW_TOOLS, activity_for, brief_objects, review_prompt


def test_prompt_names_the_brief_the_board_the_bed_and_the_round():
    p = review_prompt({"id": "b_1", "text": "case for an ESP32"}, "ESP32-S3-DevKitC-1", [180, 180, 180], 2)
    assert "b_1" in p and "ESP32-S3-DevKitC-1" in p and "180" in p and "round 2" in p
    assert "report_review" in p


def test_tool_events_become_activity_with_targets():
    call = SimpleNamespace(name="check_interference", input={"names": ["Lid", "Base"]})
    kind, summary, targets = activity_for(call)
    assert kind == "tool_call" and "check_interference" in summary and targets == ["Lid", "Base"]


def test_a_frozen_tool_input_does_not_crash_the_feed():
    from types import MappingProxyType
    call = SimpleNamespace(name="Script", input=MappingProxyType({"script": MappingProxyType({"x": 1})}))
    kind, summary, _ = activity_for(call)
    assert kind == "tool_call" and "Script" in summary


def test_a_failed_tool_result_keeps_why_it_failed():
    result = SimpleNamespace(tool_name="cadswarm___report_review", is_error=True,
                             content="Error: Tool execution cancelled by user")
    kind, summary, _ = activity_for(result)
    assert kind == "tool_result" and "failed" in summary and "cancelled by user" in summary


def test_the_reviewer_cannot_reach_geometry_tools():
    assert not any(t.endswith(("create_primitive", "set_props", "run_part_script", "delete_object", "lock_objects"))
                   for t in REVIEW_TOOLS)


def test_the_reviewer_gets_its_own_arrow():
    # point_at only moves the reviewer's own pointer; it cannot touch geometry.
    assert "cadswarm___point_at" in REVIEW_TOOLS
    p = review_prompt({"id": "b_1", "text": "case"}, "Pico", [180, 180, 180], 1)
    assert 'point_at("<part>")' in p


def test_brief_objects_are_in_task_order_without_repeats():
    tasks = [{"brief": "b_1", "objects": ["Base", "Lid"]},
             {"brief": "b_2", "objects": ["Other"]},
             {"brief": "b_1", "objects": '["Lid", "Standoff1"]'},
             {"brief": "b_1", "objects": []}]
    assert brief_objects(tasks, "b_1") == ["Base", "Lid", "Standoff1"]
    assert brief_objects(None, "b_1") == []


def test_the_review_checks_the_team_style_when_there_is_one():
    brief = {"id": "b_1", "text": "case"}
    assert "Team design style" not in review_prompt(brief, "Pico", [180, 180, 180], 1)
    p = review_prompt(brief, "Pico", [180, 180, 180], 1, "Team design style:\n- [confirmed] Walls are 2.4 mm")
    assert "5. Team design style" in p and "Walls are 2.4 mm" in p and '"style: <the rule>' in p
    assert p.index("Walls are 2.4 mm") < p.index("report_review")


# The SDK starts Droid without a shell, so it needs the native binary, not npm's .cmd/.ps1 shim.
def test_droid_exe_takes_an_explicit_override(monkeypatch):
    monkeypatch.setenv("CADSWARM_DROID_EXE", r"C:\tools\droid.exe")
    assert paths.droid_exe() == r"C:\tools\droid.exe"


def test_droid_exe_finds_the_native_binary_npm_unpacks_on_windows(tmp_path, monkeypatch):
    npm = tmp_path / "npm"
    exe = npm / "node_modules" / "droid" / "node_modules" / "@factory" / "cli-win32-x64" / "bin" / "droid.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    shim = npm / "droid.cmd"
    shim.write_text("@echo off\n")
    monkeypatch.setattr(paths, "os", SimpleNamespace(name="nt", environ={}, path=paths.os.path))
    monkeypatch.setattr(paths.shutil, "which", lambda name: str(shim) if name == "droid" else None)
    assert paths.droid_exe() == str(exe)


def test_droid_exe_on_path_wins_over_the_npm_folder(tmp_path, monkeypatch):
    native = tmp_path / "droid.exe"
    native.write_bytes(b"")
    monkeypatch.setattr(paths, "os", SimpleNamespace(name="nt", environ={}, path=paths.os.path))
    monkeypatch.setattr(paths.shutil, "which", lambda name: str(native) if name == "droid.exe" else None)
    assert paths.droid_exe() == str(native)


def test_droid_exe_keeps_the_shim_off_windows(monkeypatch):
    monkeypatch.setattr(paths, "os", SimpleNamespace(name="posix", environ={}, path=paths.os.path))
    monkeypatch.setattr(paths.shutil, "which", lambda name: "/usr/local/bin/droid")
    assert paths.droid_exe() == "/usr/local/bin/droid"


def test_the_reviewer_answers_droids_permission_prompt():
    import pytest
    droid = pytest.importorskip("droid_sdk")
    from cadswarm.reviewer import allow_review_tools
    use = droid.ToolUseBlock(id="t1", name="Script", input={})
    allow = droid.PermissionOption("Allow for cadswarm", droid.ToolConfirmationOutcome.PROCEED_ALWAYS_SERVER)
    cancel = droid.PermissionOption("Cancel", droid.ToolConfirmationOutcome.CANCEL)

    def ask(action):
        return droid.PermissionRequest(actions=[action], options=[allow, cancel])

    mine = droid.McpToolAction(tool_use=use, tool_name="cadswarm___model_status", impact_level="low",
                               server_name="cadswarm", actual_tool_name="model_status")
    theirs = droid.McpToolAction(tool_use=use, tool_name="github___create_issue", impact_level="low",
                                 server_name="github", actual_tool_name="create_issue")
    shell = droid.ExecuteAction(tool_use=use, full_command="rm -rf /", command="rm")
    assert allow_review_tools(ask(mine)).selected_option is droid.ToolConfirmationOutcome.PROCEED_ALWAYS_SERVER
    assert allow_review_tools(ask(theirs)).selected_option is droid.ToolConfirmationOutcome.CANCEL
    assert allow_review_tools(ask(shell)).selected_option is droid.ToolConfirmationOutcome.CANCEL


def test_the_reviewer_reads_the_factory_key_from_cadswarm_env(tmp_path, monkeypatch):
    # The host starts the reviewer from the desktop app, so the key only exists in the env file.
    from cadswarm import creao
    env = tmp_path / "cadswarm.env"
    env.write_text("FACTORY_API_KEY=fk-test\n", encoding="utf-8")
    monkeypatch.setenv("FACTORY_API_KEY", "")  # recorded, so the loader's write is undone after the test
    monkeypatch.delenv("FACTORY_API_KEY")
    monkeypatch.setattr(creao, "env_candidates", lambda: [None, str(env)])
    seen = {}

    async def fake_review(folder, brief, round_no):
        seen["key"] = os.environ.get("FACTORY_API_KEY")
    monkeypatch.setattr(reviewer, "review", fake_review)
    reviewer.main(["--brief", "b_1", "--dir", str(tmp_path)])
    assert seen["key"] == "fk-test"


def test_the_host_sets_up_the_reviewer_only_with_droid_and_a_factory_key(tmp_path, monkeypatch):
    from cadswarm import config, creao
    droid = tmp_path / "droid.exe"
    droid.write_text("")
    monkeypatch.setattr(creao, "env_candidates", lambda: [])
    monkeypatch.setattr(config, "droid_exe", lambda: str(droid))
    monkeypatch.delenv("CADSWARM_AUTO_REVIEWER", raising=False)
    monkeypatch.setenv("FACTORY_API_KEY", "fk-test")
    assert config.reviewer_can_start() is True
    # Opted out, for a team that does not want reviews.
    monkeypatch.setenv("CADSWARM_AUTO_REVIEWER", "0")
    assert config.reviewer_can_start() is False
    monkeypatch.delenv("CADSWARM_AUTO_REVIEWER")
    # No Droid on this computer.
    monkeypatch.setattr(config, "droid_exe", lambda: "droid")
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    assert config.reviewer_can_start() is False
    # Droid but no key: a reviewer that can't sign in exits without a verdict and fails the brief.
    monkeypatch.setattr(config, "droid_exe", lambda: str(droid))
    monkeypatch.delenv("FACTORY_API_KEY")
    assert config.reviewer_can_start() is False
