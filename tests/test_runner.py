"""The background runner wakes an AI helper only for new things meant for it, and never in a loop."""
import json
import os

from cadswarm import runner
from cadswarm.space_hub import SpaceHub
from test_space_hub import FakeSpace, member


def tables():
    return {
        "v_me": [member(1, "ann-claude", fence=2)],
        "v_member": [member(1, "ann-claude", fence=2), member(2, "bob-codex"), member(3, "carol", kind="human")],
        "v_lock": [], "v_object": [], "v_param": [], "v_receipt": [], "v_event": [], "v_brief": [],
        "v_task": [
            task("t_mine", "lid", assignee="ann-claude"),
            task("t_other", "base", assignee="bob-codex"),
            task("t_gave", "standoffs", created_by=1, owner=2, status="done", result_json='{"summary": "4 posts"}'),
        ],
        "v_message": [
            msg("m_steer", 3, "member:1", "steer", "make the lid 20 mm"),
            msg("m_ask", 2, "member:1", "message", "can you widen the slot?"),
            msg("m_chat", 3, "all", "chat", "lunch?"),
            msg("m_bob", 3, "member:2", "steer", "not for ann"),
        ],
    }


def task(tid, title, assignee="", created_by=3, owner=0, status="open", result_json=""):
    return {"id": tid, "title": title, "detail": "", "objects": [], "status": status, "owner": owner,
            "created_by": created_by, "rev": 1, "result_json": result_json, "assignee": assignee, "brief": "",
            "created_ms": 1, "updated_ms": 1}


def msg(mid, frm, to, kind, body):
    return {"id": mid, "from": frm, "to": to, "kind": kind, "body": body, "task_id": "", "created_ms": 1}


def hub(t=None):
    return SpaceHub({"uri": "http://x", "db": "d", "name": "ann-claude"},
                    {"participant_id": 1, "fence": 2, "name": "ann-claude"}, space=FakeSpace(t or tables()))


def test_wakes_for_messages_tasks_and_finished_handoffs_but_not_team_chat():
    work = dict(runner.pending_work(hub(), set()))
    assert set(work) == {"m:m_steer", "m:m_ask", "t:t_mine", "d:t_gave:done"}
    assert "a person on your team" in work["m:m_steer"] and "make the lid 20 mm" in work["m:m_steer"]
    assert "another AI helper" in work["m:m_ask"]
    assert "bob-codex finished the task you handed out" in work["d:t_gave:done"] and "4 posts" in work["d:t_gave:done"]


def test_each_thing_wakes_it_once_and_handled_messages_never():
    t = tables()
    t["v_receipt"] = [{"message_id": "m_ask", "member": 1, "state": "consumed"}]
    seen = {"m:m_steer", "t:t_mine"} | runner.finished_keys(hub(t))
    assert runner.pending_work(hub(t), seen) == []


def test_a_free_helper_wakes_for_a_busy_helpers_queue_once():
    t = tables()
    t["v_task"] = [task("t_now", "base", assignee="bob-codex", owner=2, status="claimed"),
                   task("t_next", "lid", assignee="bob-codex"),
                   task("t_free", "clip", assignee="carol")]  # carol isn't busy: she'll start it herself
    work = dict(runner.pending_work(hub(t), set()))
    assert set(work) == {"m:m_steer", "m:m_ask", "s:t_next"}
    assert "bob-codex is busy" in work["s:t_next"]
    assert "s:t_next" not in dict(runner.pending_work(hub(t), {"s:t_next"}))


def test_a_helper_with_its_own_work_or_held_parts_isnt_offered_spare_work():
    t = tables()
    t["v_task"] = [task("t_now", "base", assignee="bob-codex", owner=2, status="claimed"),
                   task("t_next", "lid", assignee="bob-codex"), task("t_mine", "clip", assignee="ann-claude")]
    assert "s:t_next" not in dict(runner.pending_work(hub(t), set()))
    t["v_task"] = t["v_task"][:2]
    t["v_task"][1]["objects"] = ["Lid"]
    t["v_lock"] = [{"object": "Lid", "holder": 2}]
    assert "s:t_next" not in dict(runner.pending_work(hub(t), set()))


def make_runner(tmp_path, client="claude", t=None):
    folder = tmp_path / "helper"
    (folder / ".cadswarm").mkdir(parents=True)
    (folder / ".cadswarm" / "config.json").write_text(json.dumps(
        {"uri": "http://x", "db": "d", "name": "ann-claude", "client": client}))
    (folder / ".cadswarm" / "identity.json").write_text(json.dumps({"participant_id": 1, "fence": 2, "token": "t"}))
    clock = [1000.0]
    spawned = []

    class Child:
        returncode = 0

        def poll(self):
            return 0

    r = runner.Runner(str(folder), "Phone stand", clock=lambda: clock[0],
                      spawn=lambda argv, stdin: spawned.append((argv, stdin)) or Child())
    r.hub = hub(t)
    return r, clock, spawned


def test_an_idle_poll_is_one_cheap_read_and_news_gets_a_full_look(tmp_path, monkeypatch):
    """A poll used to be ~10 round trips, ~3 s, so a message could sit for 6 s before it was even seen."""
    monkeypatch.setattr(runner, "find_agent_cli", lambda c: f"/bin/{c}")
    calm = tables()
    calm["v_message"] = []
    calm["v_task"] = []
    calm["v_receipt"] = []
    calm["v_event"] = [{"seq": 5, "kind": "op", "actor": 2, "at_ms": 1, "data_json": "{}"}]
    r, clock, spawned = make_runner(tmp_path, t=calm)
    r.state["started"] = True  # don't wake it just to say hello
    r.save_state()
    queries = []
    r.hub.space.on_sql = queries.append

    r.step()  # the first poll looks properly and sets the event cursor
    assert spawned == [] and r.seq == 5
    queries.clear()

    clock[0] += 1
    r.step()  # nothing happened: only the event cursor is read
    assert len(queries) == 1 and "v_event" in queries[0], queries
    assert spawned == []

    # A message arrives (which the module records as an event): the full look wakes the helper.
    r.hub.space.tables["v_event"].append({"seq": 6, "kind": "message", "actor": 3, "at_ms": 2, "data_json": "{}"})
    r.hub.space.tables["v_message"].append(msg("m_new", 3, "member:1", "steer", "now the base"))
    clock[0] += 1
    r.step()
    assert len(spawned) == 1 and "now the base" in spawned[0][1]
    assert r.seq == 6


def test_a_full_look_also_happens_now_and_then_in_case_work_raised_no_event(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "find_agent_cli", lambda c: f"/bin/{c}")
    calm = tables()
    calm["v_task"] = []
    calm["v_message"] = []
    calm["v_receipt"] = []
    r, clock, spawned = make_runner(tmp_path, t=calm)
    r.state["started"] = True
    r.save_state()
    queries = []
    r.hub.space.on_sql = queries.append
    r.step()
    queries.clear()
    clock[0] += runner.FULL_CHECK_SECONDS
    r.step()
    assert len(queries) > 1, "an idle poll every few seconds still reads the tables"


def test_a_step_rejoins_heartbeats_and_wakes_the_agent_with_whats_new(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "find_agent_cli", lambda c: f"/bin/{c}")
    r, clock, spawned = make_runner(tmp_path)
    assert r.step()
    calls = [c[0] for c in r.hub.space.calls]
    assert calls[:2] == ["rejoin", "heartbeat"]  # present as "ready" while no agent runs
    (argv, prompt), = spawned
    assert argv[:2] == ["/bin/claude", "-p"] and "--continue" not in argv  # the first run starts a conversation
    assert "make the lid 20 mm" in prompt and "Phone stand" in prompt
    assert "Say hello" not in prompt  # the runner greets instead, so the model spends no turn on it
    assert "never ask questions here" in prompt
    # The runner itself says hello, as a chat to "all" (peers ignore it, so it wakes nobody).
    # send_message args are (fence, id, to, body, kind, task_id).
    sends = [args for reducer, args in r.hub.space.calls if reducer == "send_message"]
    assert sends and sends[-1][2] == "all" and sends[-1][4] == "chat" and "ann-claude" in sends[-1][3]
    status = json.load(open(os.path.join(r.folder, ".cadswarm", "runner.json")))
    assert status["state"] == "working"
    # The agent exits; nothing new arrived: the runner rejoins and listens, without waking it again.
    clock[0] += 60
    assert r.step() and r.step()
    assert len(spawned) == 1
    assert json.load(open(os.path.join(r.folder, ".cadswarm", "runner.json")))["state"] == "listening"
    # Something new: a second run continues the same conversation.
    r.hub.space.tables["v_message"].append(msg("m_new", 3, "member:1", "steer", "now the base"))
    clock[0] += 60
    r.step()
    assert len(spawned) == 2 and "--continue" in spawned[1][0] and "now the base" in spawned[1][1]
    assert len([1 for reducer, _ in r.hub.space.calls if reducer == "send_message"]) == 1  # greeted only once


def test_runs_are_rate_limited_so_helpers_cant_wake_each_other_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "find_agent_cli", lambda c: f"/bin/{c}")
    r, clock, spawned = make_runner(tmp_path)
    r.runs = [clock[0] - 10 * i for i in range(runner.MAX_RUNS_PER_HOUR)]
    r.state["started"] = True
    r.step()
    assert spawned == []
    assert "paused" in json.load(open(os.path.join(r.folder, ".cadswarm", "runner.json")))["state"]


def test_a_missing_cli_is_reported_not_retried_in_a_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "find_agent_cli", lambda c: None)
    r, _clock, spawned = make_runner(tmp_path, client="codex")
    assert r.step()
    assert spawned == []
    st = json.load(open(os.path.join(r.folder, ".cadswarm", "runner.json")))
    assert st["state"] == "missing" and "codex" in st["detail"]


def test_a_helper_removed_from_the_team_stops_its_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "find_agent_cli", lambda c: f"/bin/{c}")
    r, _clock, _spawned = make_runner(tmp_path)
    r.hub.space.fail["rejoin"] = "not_member: gone"
    assert r.step() is False


def test_the_stop_file_ends_the_runner(tmp_path):
    r, _clock, _spawned = make_runner(tmp_path)
    runner.stop_runner(r.folder)
    assert r.step() is False


def test_each_cli_gets_a_headless_command_with_the_prompt_on_stdin_where_it_can():
    argv, stdin = runner.agent_command("claude", "claude", "/h", "do it\nnow", resume=True)
    assert argv[:2] == ["claude", "--continue"] and "-p" in argv and "mcp__cadswarm" in argv and stdin == "do it\nnow"
    argv, stdin = runner.agent_command("codex", "codex", "/h", "do it", resume=False)
    assert argv[-1] == "-" and stdin == "do it"
    argv, stdin = runner.agent_command("droid", "droid", "/h", "do it\nnow", resume=False)
    assert argv[-1] == "do it now" and stdin is None
    argv, stdin = runner.agent_command("opencode", "opencode", "/h", "do it\nnow", resume=True)
    assert argv[:4] == ["opencode", "run", "--standalone", "--auto"] and "--continue" in argv
    assert stdin == "do it\nnow" and not any("do it" in a for a in argv)
    # Creao has no CLI: the launcher runs its bridge, which takes the prompt on stdin.
    argv, stdin = runner.agent_command("creao", "", "/h", "do it", resume=False)
    assert argv[-3:] == ["creao", "--dir", "/h"] and argv[1].endswith("cadswarm_launch.py")
    assert stdin == "do it"


def test_a_helpers_picked_model_beats_the_environment(monkeypatch):
    monkeypatch.setenv("CADSWARM_HELPER_MODEL", "env/model")
    argv, _ = runner.agent_command("opencode", "opencode", "/h", "x", resume=False, model="anthropic/claude-sonnet-5-5")
    assert argv[argv.index("--model") + 1] == "anthropic/claude-sonnet-5-5"
    argv, _ = runner.agent_command("opencode", "opencode", "/h", "x", resume=False)
    assert argv[argv.index("--model") + 1] == "env/model"


def test_the_runner_wakes_opencode_with_the_folders_model(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "find_agent_cli", lambda c: f"/bin/{c}")
    monkeypatch.delenv("CADSWARM_HELPER_MODEL", raising=False)
    r, _clock, spawned = make_runner(tmp_path, client="opencode")
    r.cfg["model"] = "openai/gpt-5.1"
    assert r.wake([("m:x", "- hi")], first=True)
    argv = spawned[-1][0]
    assert argv[argv.index("--model") + 1] == "openai/gpt-5.1"


def test_runner_alive_follows_its_heartbeat_file(tmp_path):
    (tmp_path / ".cadswarm").mkdir()
    assert not runner.runner_alive(str(tmp_path))
    (tmp_path / ".cadswarm" / "runner.json").write_text(json.dumps({"beat": 1000.0, "pid": 5}))
    assert runner.runner_alive(str(tmp_path), now=1010.0)
    assert not runner.runner_alive(str(tmp_path), now=1000.0 + runner.ALIVE_SECONDS + 1)


def test_panel_snapshot_reads_each_view_once():
    t = tables()
    space = FakeSpace(t)
    seen = []
    space.on_sql = seen.append
    h = SpaceHub({"uri": "http://x", "db": "d", "name": "ann-claude"},
                 {"participant_id": 1, "fence": 2, "name": "ann-claude"}, space=space)
    snap = h.panel_snapshot()
    assert set(snap) == {"model", "tasks", "commands", "briefs", "messages", "reviews", "workspace"}
    assert len(seen) == len(set(seen))  # no view read twice in one refresh
    assert [m["id"] for m in snap["messages"]] == ["m_steer", "m_ask", "m_chat", "m_bob"]
    h.tasks()
    assert len(seen) > len(set(seen))  # outside a snapshot, reads are fresh again


def test_a_failed_run_is_reported_until_one_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "find_agent_cli", lambda c: f"/bin/{c}")
    r, clock, spawned = make_runner(tmp_path)

    class Failed:
        returncode = 1

        def poll(self):
            return 1
    r.spawn = lambda argv, stdin: spawned.append(argv) or Failed()
    r.step()
    clock[0] += 60
    r.step()
    st = json.load(open(os.path.join(r.folder, ".cadswarm", "runner.json")))
    assert st["state"] == "error" and "sign in" in st["detail"]


def test_background_starts_can_be_switched_off_for_a_terminal_session(tmp_path):
    (tmp_path / ".cadswarm").mkdir()
    assert runner.background_on(str(tmp_path))
    runner.set_background(str(tmp_path), False)
    assert not runner.background_on(str(tmp_path))
    runner.set_background(str(tmp_path), True)
    assert runner.background_on(str(tmp_path))
