"""The prompt bar's routing, the chat's unread/reply rules, presence, the step list's next step and the Agents page
rows (cadswarm/gui/team_view.py), without FreeCAD."""
import json

from cadswarm.gui import build_view, team_view as tv

HELPERS = ["ann-claude", "bob-codex"]
PEOPLE = ["carol", "dave"]
PARTS = ["Base", "Lid"]


def route(text, target=tv.TARGET_BUILD, selection=()):
    return tv.route_prompt(text, target, HELPERS, PEOPLE, PARTS, selection)


def test_prompt_goes_to_the_ai_build_by_default_with_the_selection_attached():
    r = route("make the walls 3 mm", selection=["Base", "Lid"])
    assert r["kind"] == "brief" and r["to"] == ["team"]
    assert r["text"] == "make the walls 3 mm\n\nSelected in the model: Base, Lid"
    assert r["objects"] == ["Base", "Lid"]


def test_a_mention_in_the_text_wins_over_the_send_to_pick():
    r = route("@bob-codex fillet the edges", target=tv.TARGET_CHAT)
    assert r["kind"] == "task" and r["to"] == ["bob-codex"] and r["what"] == "fillet the edges"
    assert route("@team a phone stand")["kind"] == "brief"
    assert route("@all stop", target=tv.TARGET_BUILD)["kind"] == "all"


def test_picking_a_helper_sends_it_a_task_and_chat_stays_chat():
    r = route("round the corners", target="ann-claude", selection=["Lid"])
    assert r["kind"] == "task" and r["to"] == ["ann-claude"]
    assert r["text"].startswith("@ann-claude round the corners") and "Selected in the model: Lid" in r["text"]
    c = route("lunch?", target=tv.TARGET_CHAT)
    assert c["kind"] == "chat" and c["text"] == "lunch?"
    assert route("@carol can you check this", target=tv.TARGET_CHAT)["kind"] == "chat"  # a person is fine


def test_part_mentions_become_context_and_strangers_are_refused():
    r = route("make @Lid 2 mm thinner")
    assert r["kind"] == "brief" and r["objects"] == ["Lid"]
    assert r["text"].startswith("make Lid 2 mm thinner") and r["text"].endswith("Selected in the model: Lid")
    bad = route("@zed do it")
    assert bad["kind"] == "error" and "@zed" in bad["error"]
    assert route("   ")["kind"] == "error"


def test_selection_context_and_completions():
    assert tv.selection_context([]) == ""
    assert tv.selection_context(list("ABCDEFGH"), limit=3) == "Selected in the model: A, B, C (+5 more)"
    assert tv.completions(HELPERS, ["carol", "me"], ["Base", "Lid", "bad name"], me="me") == \
        ["ann-claude", "bob-codex", "team", "all", "carol", "Base", "Lid"]
    assert [k for k, _ in tv.targets(HELPERS)] == ["build", "ann-claude", "bob-codex", "all", "chat"]


def test_unread_counts_others_and_mentions_of_me():
    msgs = [{"id": 1, "from": "carol", "to": "all", "kind": "chat", "body": "hi", "at": 10},
            {"id": 2, "from": "ann-claude", "to": "all", "kind": "chat", "body": "@dave look", "at": 20},
            {"id": 3, "from": "dave", "to": "all", "kind": "chat", "body": "mine", "at": 30},
            {"id": 4, "from": "bob-codex", "to": "dave", "kind": "chat", "body": "done", "at": 40},
            {"id": 5, "from": "x", "to": "all", "kind": "notice", "body": "n", "at": 50}]
    assert tv.unread(msgs, "dave", 0) == (3, 2)
    assert tv.unread(msgs, "dave", 25) == (1, 1)
    assert tv.chat_tab_text(0, 0) == "Chat" and tv.chat_tab_text(3, 0) == "Chat · 3"
    assert tv.chat_tab_text(3, 1) == "Chat · 3 @"


def test_reply_quotes_the_message():
    m = {"from": "ann-claude", "body": "Done. The base walls are 3 mm now; the lid still clears the posts."}
    assert tv.reply_prefix(m, "dave") == "@ann-claude re “Done. The base walls are 3 mm now; the lid still…”: "
    assert tv.reply_prefix({"from": "dave", "body": "x"}, "dave") == ""


def test_presence_people_first_with_what_they_hold():
    parts = [{"id": 2, "name": "ann-claude", "kind": "agent", "status": "active", "locks": ["Base", "Params"],
              "task": {"title": "Base"}},
             {"id": 1, "name": "carol", "kind": "human", "status": "active", "locks": []},
             {"id": 3, "name": "dave", "kind": "human", "status": "stale", "locks": ["Lid"]}]
    rows = tv.presence(parts, me="dave")
    assert [r["name"] for r in rows] == ["dave", "carol", "ann-claude"]
    assert rows[0]["you"] and not rows[0]["online"] and tv.holds_text(rows[0]) == "holds Lid"
    assert rows[2]["holds"] == ["Base"] and rows[2]["doing"] == "Base"
    assert tv.presence_summary(rows) == "1 person · 1 AI helper online"


def _model():
    tasks = [{"id": "t1", "status": "done", "owner_name": "ann-claude", "brief": "b", "title": "1. Mast",
              "objects": ["Mast"], "created_at": 1},
             {"id": "t2", "status": "open", "assignee": "dave", "brief": "b", "title": "2. Hub", "created_at": 2},
             {"id": "t3", "status": "open", "brief": "b", "title": "3. Grip", "created_at": 3}]
    return build_view.from_snapshot({"id": "b", "text": "Rotor"}, tasks), tasks


def test_step_words_assignees_parts_and_next_step():
    model, tasks = _model()
    s1, s2, s3 = model["steps"]
    assert [tv.step_state_word(s) for s in model["steps"]] == ["Done", "Ready", "Ready"]
    people = [{"name": "dave", "kind": "human"}, {"name": "ann-claude", "kind": "agent"}]
    assert tv.step_assignee(s1, people) == ("ann-claude", "ai")
    assert tv.step_assignee(s2, people) == ("dave", "person")
    assert tv.step_assignee(s3, people) == ("", "")
    assert tv.step_objects(s1, tasks) == ["Mast"]
    assert tv.step_objects(s2, tasks, [{"name": "Hub001", "label": "Hub"}, {"name": "X", "label": "Other"}]) \
        == ["Hub001"]
    step, line = tv.next_step(model)
    assert step["n"] == 2 and line == "Next: step 2 · Hub"
    assert tv.next_step({"steps": []})[0] is None


def test_agent_rows_detected_connected_and_custom():
    clients = {"claude": {"label": "Claude Code", "install_url": "u1"},
               "codex": {"label": "Codex", "install_url": "u2"},
               "opencode": {"label": "OpenCode", "install_url": "u3"}}
    found = {"claude": "C:/bin/claude.exe", "codex": None, "opencode": "C:/oc.exe"}
    parts = [{"name": "ann-claude", "kind": "agent", "client": "claude", "status": "active"},
             {"name": "old-claude", "kind": "agent", "client": "claude", "status": "stale"},
             {"name": "eve-gpt", "kind": "agent", "client": "aider", "status": "active"},
             {"name": "carol", "kind": "human", "client": "freecad", "status": "active"}]
    rows = tv.agent_rows(clients, found, parts, {"ann-claude": {"state": "working"}}, "aider --mcp")
    by = {r["client"]: r for r in rows}
    assert [r["client"] for r in rows] == ["claude", "codex", "opencode", "custom"]
    assert by["claude"]["status"] == "1 of 2 connected" and by["claude"]["helpers"][0]["state"] == "working"
    assert by["codex"]["status"] == "Not installed" and not by["codex"]["installed"]
    assert by["opencode"]["status"] == "Detected · not connected"
    assert by["custom"]["installed"] and by["custom"]["helpers"][0]["name"] == "eve-gpt"


def test_remote_instructions_are_exact():
    r = tv.remote_instructions("claude", "cadswarm-2:abc", "dave-laptop", python="py", launcher="L.py",
                               folder="~/h")
    assert r["setup"] == "cadswarm setup --client claude --invite cadswarm-2:abc --name dave-laptop --dir ~/h"
    assert r["run"] == "cd ~/h && claude"
    assert json.loads(r["mcp"]) == {"mcpServers": {"cadswarm": {"command": "py", "args": ["L.py", "mcp"],
                                                                 "env": {"CADSWARM_PROJECT": "~/h"}}}}
    custom = tv.remote_instructions("custom", "c", "n")
    assert "--client opencode" in custom["setup"] and "mcpServers" in custom["steps"]
    assert tv.helper_name("Carol P", "claude", ["carol-p-claude"]) == "carol-p-claude-2"
