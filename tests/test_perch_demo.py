"""The Perch demo driver: the rules a judge can check on screen, as tests. No network, no FreeCAD."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo" / "perch"))

import driver  # noqa: E402


class FakeIO:
    """The team, canned: the runner sees exactly the module-shaped data it will see live."""

    def __init__(self, snapshot=None, ops=None, events=None, checks=None):
        self.snap = snapshot or {"model": {"objects": [], "participants": [], "params": {}},
                                 "tasks": [], "briefs": [], "reviews": []}
        self.op_rows = ops or []
        self.ev = events or []
        self.checks = checks if checks is not None else [
            {"name": "Claude", "ok": True, "detail": "lead-claude · active"}]
        self.sent = []
        self.seq = 0

    def snapshot(self):
        return self.snap

    def ops(self):
        return self.op_rows

    def events(self, since):
        rows = [e for e in self.ev if int(e["seq"]) > since]
        if rows:
            self.seq = int(rows[-1]["seq"])
        return {"seq": self.seq, "events": rows}

    def send_brief(self, text, board):
        self.sent.append(("brief", text, board))
        return {"brief": "b_demo"}

    def send_steer(self, to, body):
        self.sent.append(("steer", to, body))
        return {"id": "m_demo"}

    def probe(self):
        return self.checks


class Clock:
    def __init__(self, t=1_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def a_step(sid="brief", predicate="brief_sent", args=None, **over):
    base = {"id": sid, "label": sid, "predicate": predicate, "at": 0, "deadline": 30, "args": args or {}}
    base.update(over)
    return driver.Step(**base)


def a_view(**over):
    base = dict(snapshot={"model": {"objects": [], "participants": [], "params": {}},
                          "tasks": [], "briefs": [], "reviews": []},
                ops=[], events=[], run_events=[], seq=0, started=1_000.0, after=1_000.0, now=1_010.0)
    base.update(over)
    return driver.View(**base)


def a_plan(steps=None, **over):
    base = {
        "name": "test",
        "brief": {"from": "phone", "board": "esp32-s3-devkitc-1", "marker": "Podium head",
                  "text": "Make the Podium head."},
        "gate": {"first_op_by": 105, "if_missed": "No part has started yet."},
        "ready": {"parts_any": ["Clamp_Base"], "forbid_prefix": ["Podium_"], "no_open_brief": True},
        "cast": {"lead-claude": "claude"},
        "probe": ["lead-claude"],
        "steps": steps or [{"id": "brief", "label": "the brief", "at": 0, "deadline": 10,
                            "predicate": "brief_sent", "args": {"marker": "Podium head"}}],
    }
    base.update(over)
    return base


def ready_snapshot(**over):
    snap = {"model": {"objects": [{"name": "Clamp_Base", "locked_by": None},
                                  {"name": "Params", "locked_by": None}],
                      "participants": [{"id": 1, "name": "presenter", "client": "presenter", "status": "active"}],
                      "params": {}},
            "tasks": [], "briefs": [], "reviews": []}
    snap.update(over)
    return snap


# --------------------------------------------------------------------------- plans

def test_a_plan_loads_from_json(tmp_path):
    p = tmp_path / "table.json"
    p.write_text(json.dumps(a_plan()), encoding="utf-8")
    assert driver.load_plan(p)["name"] == "test"


def test_a_step_with_an_unknown_predicate_is_refused():
    with pytest.raises(driver.PlanError, match="unknown predicate"):
        driver.validate(a_plan(steps=[{"id": "x", "label": "x", "predicate": "vibes", "at": 0, "deadline": 5}]))


def test_a_step_without_a_deadline_is_refused():
    with pytest.raises(driver.PlanError, match="deadline"):
        driver.validate(a_plan(steps=[{"id": "x", "label": "x", "predicate": "confirm", "at": 0}]))


def test_steps_out_of_order_are_refused():
    steps = [{"id": "a", "label": "a", "predicate": "confirm", "at": 30, "deadline": 5},
             {"id": "b", "label": "b", "predicate": "confirm", "at": 10, "deadline": 5}]
    with pytest.raises(driver.PlanError, match="out of order"):
        driver.validate(a_plan(steps=steps))


def test_a_predicate_missing_its_arguments_is_refused():
    with pytest.raises(driver.PlanError, match="args.marker"):
        driver.validate(a_plan(steps=[{"id": "brief", "label": "b", "predicate": "brief_sent",
                                       "at": 0, "deadline": 5}]))


def test_a_duplicate_step_id_is_refused():
    steps = [{"id": "a", "label": "a", "predicate": "confirm", "at": 10, "deadline": 5},
             {"id": "a", "label": "a again", "predicate": "confirm", "at": 20, "deadline": 5}]
    with pytest.raises(driver.PlanError, match="twice"):
        driver.validate(a_plan(steps=steps))


def test_a_plan_without_a_brief_is_refused():
    with pytest.raises(driver.PlanError, match="brief"):
        driver.validate(a_plan(brief={"from": "phone", "text": "  "}))


# --------------------------------------------------------------------------- predicates

def test_brief_sent_names_the_phone_and_rejects_the_presenter():
    event = {"kind": "brief", "actor": 7, "seq": 3, "text": "Make the Podium head for the same Perch"}
    people = [{"id": 7, "name": "sam-phone", "client": "phone", "status": "active"}]
    v = a_view(run_events=[event], snapshot={"model": {"objects": [], "participants": people}, "tasks": [],
                                             "briefs": [], "reviews": []})
    ok, detail = driver.brief_sent(a_step(args={"marker": "Podium head", "by_not": "presenter"}), v)
    assert ok and "sam-phone" in detail
    ok, detail = driver.brief_sent(a_step(args={"marker": "Podium head", "by_not": "sam-phone"}), v)
    assert not ok and "sam-phone" in detail


def test_recall_needs_act1_its_three_problems_and_lessons():
    result = {"designs": [{"brief_id": "b_1", "request": "Build The Perch: a clip-on articulated arm",
                           "problems": ["missing OTG cutout", "cradle 8.0 mm", "lid wall 1.0 mm"]}],
              "lessons": ["Walls are 2.4 mm", "M3 inserts", "3 mm fillets"]}
    ops = [{"kind": "recall_designs", "status": "done", "finished_ms": 1_005_000, "result": result}]
    step = a_step("recall", "recall", {"marker": "clip-on articulated arm", "problems": 3})
    ok, detail = driver.recall(step, a_view(ops=ops))
    assert ok and "3 problems" in detail and "3 lessons" in detail
    thin = {"designs": [{**result["designs"][0], "problems": ["one"]}], "lessons": ["one"]}
    ok, detail = driver.recall(step, a_view(ops=[{**ops[0], "result": thin}]))
    assert not ok and "need 3" in detail


def test_recall_ignores_an_op_that_finished_before_the_step():
    result = {"designs": [{"request": "clip-on articulated arm", "problems": ["a", "b", "c"]}], "lessons": ["l"]}
    ops = [{"kind": "recall_designs", "status": "done", "finished_ms": 900_000, "result": result}]
    ok, detail = driver.recall(a_step("recall", "recall", {"marker": "clip-on", "problems": 3}),
                               a_view(ops=ops, started=1_000.0))
    assert not ok and "waiting" in detail


def test_tasks_land_with_their_named_owners_and_not_a_poacher():
    owners = {"Podium_Body": "lead-claude", "Podium_Lid": "lid-creao"}
    tasks = [{"title": "Podium_Body: upright", "status": "open", "assignee": "lead-claude", "owner": 2,
              "owner_name": "lead-claude"},
             {"title": "Podium_Lid: back cover", "status": "open", "assignee": "lid-creao", "owner": None,
              "owner_name": None}]
    snap = {"model": {"objects": [], "participants": []}, "tasks": tasks, "briefs": [], "reviews": []}
    ok, detail = driver.tasks_landed(a_step("tasks", "tasks_landed", {"owners": owners}), a_view(snapshot=snap))
    assert ok and "Podium_Body → lead-claude" in detail
    tasks[1] = {**tasks[1], "owner": 9, "owner_name": "host-worker"}
    snap = {**snap, "tasks": tasks}
    ok, detail = driver.tasks_landed(a_step("tasks", "tasks_landed", {"owners": owners}), a_view(snapshot=snap))
    assert not ok and "claimed by host-worker" in detail


def test_a_refusal_is_the_modules_lock_violation_not_a_sentence():
    events = [{"kind": "op_failed", "code": "lock_violation", "name": "lid-creao", "seq": 9,
               "error": "Podium_Body is held by lead-claude"}]
    step = a_step("refusal", "refusal", {"target": "Podium_Body", "by": "lid-creao"})
    assert driver.refusal(step, a_view(events=events))[0] is True
    assert driver.refusal(step, a_view(events=[{**events[0], "code": "bad_args"}]))[0] is False
    assert driver.refusal(step, a_view(events=[{**events[0], "name": "arm-opencode"}]))[0] is False


def test_the_first_part_is_an_op_that_names_a_part():
    events = [{"kind": "op", "op": "create_extrusion", "seq": 11,
               "created": ["Podium_Body"], "modified": [], "deleted": [], "recomputed": [], "broken": []}]
    step = a_step("first_part", "first_part", {"parts": ["Podium_Body", "Podium_Lid"]})
    ok, detail = driver.first_part(step, a_view(run_events=events))
    assert ok and "Podium_Body" in detail
    params = [{**events[0], "created": ["Params"], "op": "set_param"}]
    assert driver.first_part(step, a_view(run_events=params))[0] is False


def test_parts_landed_params_and_reviews_are_read_from_the_module():
    snap = ready_snapshot()
    snap["model"]["objects"] = [{"name": n, "locked_by": None} for n in
                                ["Clamp_Base", "Shoulder_Hub", "Arm_Proximal", "Arm_Distal", "Cradle_Base"]]
    ok, detail = driver.parts_landed(a_step("build", "parts_landed", {"parts": ["Clamp_Base", "Cradle_Lid"]}),
                                     a_view(snapshot=snap))
    assert not ok and "Cradle_Lid" in detail
    snap["model"]["objects"].append({"name": "Cradle_Lid", "locked_by": None})
    assert driver.parts_landed(a_step("build", "parts_landed", {"parts": ["Cradle_Lid"]}), a_view(snapshot=snap))[0]

    snap["model"]["params"] = {"arm_reach": 160.0}
    assert driver.param_changed(a_step("s", "param_changed", {"alias": "arm_reach", "value": 160}),
                                a_view(snapshot=snap))[0]
    assert not driver.param_changed(a_step("s", "param_changed", {"alias": "arm_reach", "value": 150}),
                                    a_view(snapshot=snap))[0]

    review = [{"kind": "review", "round": 1, "passed": False, "seq": 20, "brief": "b_2"}]
    assert driver.review_round(a_step("r1", "review_round", {"round": 1, "passed": False}),
                               a_view(run_events=review))[0]
    assert not driver.review_round(a_step("r2", "review_round", {"round": 2, "passed": True}),
                                   a_view(run_events=review))[0]

    fea = [{"kind": "fea_report", "seq": 21, "folder": "fea/2026-10-24 arm-distal"}]
    assert driver.report(a_step("fea", "report", {"kind": "fea_report"}), a_view(run_events=fea))[0]

    assert driver.takeover(a_step("t", "takeover"),
                           a_view(run_events=[{"kind": "taken_over", "target": "lid-creao",
                                               "taker": "lead-claude", "locks": ["Cradle_Lid"], "seq": 22}]))[0]
    assert driver.handoff(a_step("h", "handoff"),
                          a_view(run_events=[{"kind": "handoff_accepted", "from": "sam", "taker": "base-claude",
                                              "seq": 23}]))[0]
    assert driver.confirm(a_step("c", "confirm"), a_view())[0] is False


# --------------------------------------------------------------------------- runner

def a_runner(io, clock, steps=None, log=None, **plan_over):
    r = driver.Runner(a_plan(steps=steps, **plan_over), io, log=log, clock=clock)
    r.probe()
    return r


def test_start_refuses_without_a_passing_probe():
    io = FakeIO(snapshot=ready_snapshot(), checks=[{"name": "Elastic", "ok": False, "detail": "no index"}])
    r = a_runner(io, Clock())
    with pytest.raises(driver.RunnerError, match="Elastic"):
        r.start()


def test_start_refuses_until_the_team_is_back_at_act1_ready():
    snap = ready_snapshot(model={"objects": [{"name": "Params", "locked_by": None}],
                                 "participants": [], "params": {}})
    r = a_runner(FakeIO(snapshot=snap), Clock())
    with pytest.raises(driver.RunnerError, match="missing Clamp_Base"):
        r.start()


def test_a_leftover_act2_part_also_refuses_start():
    snap = ready_snapshot()
    snap["model"]["objects"].append({"name": "Podium_Body", "locked_by": None})
    r = a_runner(FakeIO(snapshot=snap), Clock())
    assert not r.reset_ok() and "Podium_Body" in r.reset_why


def test_a_stale_helper_refuses_start_but_a_stale_reviewer_does_not():
    # `init-reviewer` mints an HTTP-only member that never heartbeats: it is stale by T-5 either way.
    snap = ready_snapshot()
    snap["model"]["participants"] = [
        {"id": 1, "name": "presenter", "client": "presenter", "kind": "human", "status": "active"},
        {"id": 2, "name": "dee-droid", "client": "droid", "kind": "reviewer", "status": "stale"},
    ]
    r = a_runner(FakeIO(snapshot=snap), Clock())
    assert r.reset_ok(), r.reset_why
    snap["model"]["participants"].append(
        {"id": 3, "name": "lead-claude", "client": "claude", "kind": "agent", "status": "stale"})
    r = a_runner(FakeIO(snapshot=snap), Clock())
    assert not r.reset_ok() and "lead-claude" in r.reset_why and "dee-droid" not in r.reset_why


def test_laptop_mode_sends_the_brief_and_says_the_tunnel_is_down():
    io = FakeIO(snapshot=ready_snapshot())
    r = a_runner(io, Clock(), brief={**a_plan()["brief"], "from": "laptop"})
    r.start()
    assert io.sent[0][0] == "brief" and "Podium head" in io.sent[0][1]
    assert r.steps[0].state == "done" and "tunnel" in r.steps[0].detail
    assert r.index == 1


def test_a_step_that_misses_its_deadline_is_skipped_out_loud(tmp_path):
    clock = Clock()
    r = a_runner(FakeIO(snapshot=ready_snapshot()), clock, log=driver.RunLog(tmp_path, "test"))
    r.start()
    clock.advance(11)
    r.tick()
    assert r.steps[0].state == "skipped" and "no result in 10s" in r.steps[0].detail
    assert "skipped" in r.status


def test_the_mention_waits_for_the_body_and_goes_out_once():
    clock = Clock()
    snap = ready_snapshot()
    io = FakeIO(snapshot=snap)
    steps = [
        {"id": "refusal", "label": "r", "at": 0, "deadline": 40, "predicate": "refusal",
         "args": {"target": "Podium_Body", "by": "lid-creao"},
         "guard": {"held_by": {"object": "Podium_Body", "name": "lead-claude"}},
         "on_start": {"steer": {"to": "lid-creao", "body": "thicken Podium_Body by 2 mm"}}},
        {"id": "first_part", "label": "f", "at": 10, "deadline": 45, "predicate": "first_part",
         "args": {"parts": ["Podium_Body"]}},
    ]
    r = a_runner(io, clock, steps=steps)
    r.start()
    # The body is an Act 2 part, so it arrives after Start: a leftover would be refused
    # (test_a_leftover_act2_part_also_refuses_start). This step only needs to watch its lock.
    snap["model"]["objects"].append({"name": "Podium_Body", "locked_by": None})
    clock.advance(2)
    r.tick()
    assert io.sent == [] and "nobody" in r.steps[0].detail
    snap["model"]["objects"][-1]["locked_by"] = "lead-claude"
    clock.advance(2)
    r.tick()
    assert io.sent == [("steer", "lid-creao", "thicken Podium_Body by 2 mm")]
    clock.advance(2)
    r.tick()
    assert io.sent == [("steer", "lid-creao", "thicken Podium_Body by 2 mm")]  # sent once, not every tick
    io.ev.append({"kind": "op_failed", "code": "lock_violation", "name": "lid-creao", "seq": 4,
                  "error": "Podium_Body is held by lead-claude"})
    clock.advance(2)
    r.tick()
    assert r.steps[0].state == "done"


def test_a_body_that_is_never_held_skips_the_mention_at_its_deadline():
    clock = Clock()
    snap = ready_snapshot()
    steps = [{"id": "refusal", "label": "r", "at": 0, "deadline": 40, "predicate": "refusal",
              "args": {"target": "Podium_Body", "by": "lid-creao"},
              "guard": {"held_by": {"object": "Podium_Body", "name": "lead-claude"}},
              "on_start": {"steer": {"to": "lid-creao", "body": "thicken"}}}]
    io = FakeIO(snapshot=snap)
    r = a_runner(io, clock, steps=steps)
    r.start()
    # Act 2 creates the body; the guard waits for the wrong holder (see the leftover-part reset test).
    snap["model"]["objects"].append({"name": "Podium_Body", "locked_by": "arm-opencode"})
    clock.advance(41)
    r.tick()
    assert io.sent == [] and r.steps[0].state == "skipped" and "arm-opencode" in r.steps[0].detail


def test_the_absolute_gate_fires_once_and_shows_the_fallback_line(tmp_path):
    clock = Clock()
    io = FakeIO(snapshot=ready_snapshot())
    steps = [{"id": "first_part", "label": "f", "at": 0, "deadline": 300, "predicate": "first_part",
              "args": {"parts": ["Podium_Body"]}}]
    r = a_runner(io, clock, steps=steps, log=driver.RunLog(tmp_path, "test"))
    r.start()
    clock.advance(106)
    r.tick()
    assert r.gate_failed and "No part has started" in r.status
    clock.advance(10)
    r.tick()
    assert [row["kind"] for row in r.log.rows].count("gate.failed") == 1


def test_pause_holds_the_sequence_and_the_build_keeps_running():
    clock = Clock()
    io = FakeIO(snapshot=ready_snapshot())
    r = a_runner(io, clock)
    r.start()
    assert r.toggle_pause() is True
    clock.advance(30)
    r.tick()
    assert r.steps[0].state == "waiting" and r.state()["paused"] is True
    assert r.toggle_pause() is False


def test_the_say_box_routes_a_steer_or_a_real_brief():
    io = FakeIO(snapshot=ready_snapshot())
    r = a_runner(io, clock=Clock())
    r.say("@lid-creao thicken Podium_Body by 2 mm")
    r.say("Make the medallion 55 mm and keep the same bearing.")
    assert io.sent[0] == ("steer", "lid-creao", "thicken Podium_Body by 2 mm")
    assert io.sent[1] == ("brief", "Make the medallion 55 mm and keep the same bearing.", "esp32-s3-devkitc-1")


def test_skip_confirms_a_manual_step_and_logs_the_decision(tmp_path):
    io = FakeIO(snapshot=ready_snapshot())
    log = driver.RunLog(tmp_path, "test")
    steps = [{"id": "constrain", "label": "auto-constrain", "at": 0, "deadline": 600, "predicate": "confirm"},
             {"id": "close", "label": "close", "at": 10, "deadline": 600, "predicate": "confirm"}]
    r = a_runner(io, Clock(), steps=steps, log=log)
    r.start()
    r.skip()
    assert r.steps[0].state == "confirmed"
    assert any(row["kind"] == "step.confirmed" for row in log.rows)


def test_the_run_log_records_each_step_and_the_numbers_the_pitch_quotes(tmp_path):
    clock = Clock()
    result = {"designs": [{"request": "clip-on arm", "problems": ["a", "b", "c"]}], "lessons": ["l"]}
    ops = [{"kind": "recall_designs", "status": "done", "finished_ms": 1_006_000, "result": result}]
    events = [
        {"kind": "brief", "actor": 1, "seq": 2, "text": "Make the Podium head"},
        {"kind": "op", "op": "create_extrusion", "seq": 5, "created": ["Podium_Body"],
         "modified": [], "deleted": [], "recomputed": [], "broken": []},
    ]
    io = FakeIO(snapshot=ready_snapshot(), ops=ops, events=events)
    log = driver.RunLog(tmp_path, "test")
    steps = [
        {"id": "brief", "label": "b", "at": 0, "deadline": 10, "predicate": "brief_sent",
         "args": {"marker": "Podium head"}},
        {"id": "recall", "label": "r", "at": 10, "deadline": 25, "predicate": "recall",
         "args": {"marker": "clip-on", "problems": 3}},
        {"id": "first_part", "label": "f", "at": 20, "deadline": 45, "predicate": "first_part",
         "args": {"parts": ["Podium_Body"]}},
    ]
    r = a_runner(io, clock, steps=steps, log=log)
    r.start()
    clock.advance(4)
    r.tick()
    clock.advance(6)
    r.tick()
    clock.advance(9)
    r.tick()
    assert r.done
    out = json.loads(log.summary_path.read_text(encoding="utf-8"))
    assert out["t_recall"] == pytest.approx(10.0)
    assert out["t_first_op"] == pytest.approx(19.0)
    assert out["t_finish"] == pytest.approx(19.0)
    assert [s["state"] for s in out["steps"]] == ["done", "done", "done"]
    rows = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    assert any(row["kind"] == "step.done" and "recall" in row["detail"] for row in rows)


# --------------------------------------------------------------------------- review fixes

def test_a_previous_runs_events_cannot_satisfy_this_run():
    clock = Clock()
    snap = ready_snapshot()
    snap["model"]["participants"].append({"id": 9, "name": "sam", "client": "phone", "status": "active"})
    snap["model"]["seq"] = 100
    io = FakeIO(snapshot=snap, events=[{"kind": "brief", "actor": 9, "seq": 90,
                                       "text": "Make the Podium head"}])
    r = a_runner(io, clock)
    r.start()
    clock.advance(2)
    r.tick()
    assert r.steps[0].state == "waiting"  # the seq-90 brief came before the press
    io.ev.append({"kind": "brief", "actor": 9, "seq": 101, "text": "Make the Podium head"})
    clock.advance(2)
    r.tick()
    assert r.steps[0].state == "done"


def test_the_gate_ignores_a_previous_runs_part():
    clock = Clock()
    snap = ready_snapshot()
    snap["model"]["seq"] = 100
    io = FakeIO(snapshot=snap, events=[{"kind": "op", "op": "create_extrusion", "seq": 90,
                                       "created": ["Podium_Body"], "modified": [], "deleted": [],
                                       "recomputed": [], "broken": []}])
    steps = [{"id": "first_part", "label": "f", "at": 0, "deadline": 300, "predicate": "first_part",
              "args": {"parts": ["Podium_Body"]}}]
    r = a_runner(io, clock, steps=steps,
                 gate={"first_op_by": 10, "if_missed": "No part has started yet."})
    r.start()
    clock.advance(11)
    r.tick()
    assert r.gate_failed is True


def test_a_cancelled_task_is_not_a_landed_task():
    tasks = [{"title": "Podium_Body", "status": "cancelled", "assignee": "lead-claude"}]
    snap = {"model": {"objects": [], "participants": []}, "tasks": tasks, "briefs": [], "reviews": []}
    ok, detail = driver.tasks_landed(a_step("tasks", "tasks_landed",
                                            {"owners": {"Podium_Body": "lead-claude"}}),
                                     a_view(snapshot=snap))
    assert ok is False and "Podium_Body" in detail


def test_a_skipped_step_is_not_a_number_for_the_pitch(tmp_path):
    clock = Clock()
    steps = [{"id": "first_part", "label": "f", "at": 0, "deadline": 30, "predicate": "first_part",
              "args": {"parts": ["Podium_Body"]}}]
    log = driver.RunLog(tmp_path, "test")
    r = a_runner(FakeIO(snapshot=ready_snapshot()), clock, steps=steps, log=log)
    r.start()
    clock.advance(31)
    r.tick()
    assert r.steps[0].state == "skipped"
    out = json.loads(log.summary_path.read_text(encoding="utf-8"))
    assert out["t_first_op"] is None


# --------------------------------------------------------------------------- the presenter, the page, the cli

import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from cadswarm.client import load_json


def test_the_presenter_writes_the_same_files_setup_does(tmp_path):
    ticket = {"uri": "https://maincloud.spacetimedb.com/", "db": "team-db", "role": "member"}
    written = driver.write_presenter_config(
        tmp_path, ticket, "presenter", {"token": "t_demo", "participant_id": 9, "fence": 1})
    assert len(written) == 2
    cfg = load_json(tmp_path / ".cadswarm" / "config.json")
    assert cfg["client"] == "presenter" and cfg["db"] == "team-db"
    assert cfg["uri"] == "https://maincloud.spacetimedb.com"
    assert load_json(tmp_path / ".cadswarm" / "identity.json")["participant_id"] == 9
    hub, cfg = driver.project_hub(tmp_path)
    assert hub is not None and cfg["client"] == "presenter"


def test_the_probe_names_every_owner_and_the_four_partners():
    members = [
        {"id": 1, "name": "lead-claude", "client": "claude", "kind": "agent", "status": "active", "last_seen": 1_000},
        {"id": 2, "name": "lid-creao", "client": "creao", "kind": "agent", "status": "stale", "last_seen": 900},
        {"id": 3, "name": "arm-opencode", "client": "opencode", "kind": "agent", "status": "active", "last_seen": 1_000},
        {"id": 4, "name": "dee-droid", "client": "droid", "kind": "reviewer", "status": "away", "last_seen": 700},
    ]
    cast = {"lead-claude": "claude", "lid-creao": "creao", "arm-opencode": "opencode"}
    checks = driver.probe_checks(cast, members, now=1_050, which=lambda name: "C:/bin/droid.exe",
                                elastic=lambda: True)
    by = {c["name"]: c for c in checks}
    assert by["Claude"]["ok"] is True
    assert by["Creao"]["ok"] is False and "stale" in by["Creao"]["detail"]
    assert by["Droid"]["ok"] is True
    assert by["Elastic"]["ok"] is True
    assert by["arm-opencode"]["ok"] is True
    assert len(checks) == 5


def test_a_missing_helper_or_an_elastic_failure_fails_the_probe():
    checks = driver.probe_checks({"lead-claude": "claude"}, [], now=1_000,
                                 which=lambda name: None, elastic=lambda: (_ for _ in ()).throw(RuntimeError("no key")))
    by = {c["name"]: c for c in checks}
    assert by["Claude"]["ok"] is False and "not joined" in by["Claude"]["detail"]
    assert by["Droid"]["ok"] is False
    assert by["Elastic"]["ok"] is False and "no key" in by["Elastic"]["detail"]


def test_the_page_shows_the_brief_and_the_buttons():
    html = driver.page(a_plan())
    assert "Start" in html and "Next" in html and "Probe" in html
    assert "Make the Podium head." in html
    assert "the brief to send from the phone" in html
    assert "fetch('/status')" in html


def test_the_page_names_the_booth_brief_as_panel_text():
    plan = a_plan()
    plan["brief"]["from"] = "panel"
    html = driver.page(plan)
    assert "the brief to type in the panel" in html
    assert "Sam's phone" not in html


def test_the_status_endpoint_answers_and_start_is_a_post():
    io = FakeIO(snapshot=ready_snapshot())
    r = a_runner(io, Clock())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), driver.make_handler(r))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "Make the Podium head." in html
        status = json.loads(urllib.request.urlopen(base + "/status", timeout=5).read())
        assert status["probe_ok"] is True and status["steps"][0]["state"] == "pending"
        req = urllib.request.Request(base + "/start", data=b"{}", method="POST",
                                     headers={"content-type": "application/json"})
        assert json.loads(urllib.request.urlopen(req, timeout=5).read())["started"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_refused_start_answers_409_with_the_reason():
    snap = ready_snapshot(model={"objects": [], "participants": [], "params": {}})
    r = a_runner(FakeIO(snapshot=snap), Clock())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), driver.make_handler(r))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        req = urllib.request.Request(base + "/start", data=b"{}", method="POST",
                                     headers={"content-type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 409
        assert "act-1-ready" in json.loads(err.value.read())["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_cli_takes_the_timeline_and_the_laptop_fallback():
    a = driver.parse_args(["--timeline", "table-safe.json", "--laptop", "--port", "8899"])
    assert a.timeline == "table-safe.json" and a.laptop is True and a.port == 8899
    assert driver.parse_args([]).timeline == "table.json"


# --------------------------------------------------------------------------- review fixes: a bad network, a double click, a reused folder

def test_a_hub_failure_answers_502_and_keeps_the_page_talking(tmp_path):
    class UnreachableIO(FakeIO):
        def snapshot(self):  # the probe never asks for a snapshot; Start does
            raise RuntimeError("hub unreachable")

    r = a_runner(UnreachableIO(snapshot=ready_snapshot()), Clock(),
                 log=driver.RunLog(tmp_path, "test"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), driver.make_handler(r))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        req = urllib.request.Request(base + "/start", data=b"{}", method="POST",
                                     headers={"content-type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 502
        body = json.loads(err.value.read())["error"]
        assert "hub unreachable" in body and "RuntimeError" in body
        # The handler logged it through the run log, and the server is still serving.
        assert any(row["kind"] == "page.error" and "hub unreachable" in row["detail"]
                   for row in r.log.rows)
        status = json.loads(urllib.request.urlopen(base + "/status", timeout=5).read())
        assert status["started"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_two_starts_at_once_send_the_brief_once_and_refuse_the_second():
    class GatedIO(FakeIO):
        """Both presses reach Start's guard before either can go on: the race, made repeatable."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.barrier = threading.Barrier(2, timeout=0.5)
            self.arrived = set()

        def snapshot(self):
            me = threading.get_ident()
            if me not in self.arrived:
                self.arrived.add(me)
                try:
                    self.barrier.wait()
                except threading.BrokenBarrierError:
                    pass
            return super().snapshot()

    io = GatedIO(snapshot=ready_snapshot())
    r = a_runner(io, Clock(), brief={**a_plan()["brief"], "from": "laptop"})
    started, refused = [], []

    def press():
        try:
            r.start()
            started.append(True)
        except driver.RunnerError as e:
            refused.append(str(e))

    threads = [threading.Thread(target=press) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not any(t.is_alive() for t in threads)
    assert len(started) == 1
    assert refused == ["this run has already started"]
    assert len([s for s in io.sent if s[0] == "brief"]) == 1


def test_a_folder_that_belongs_to_a_helper_cannot_rejoin_as_the_presenter(tmp_path):
    folder = tmp_path / ".cadswarm"
    folder.mkdir()
    (folder / "config.json").write_text(json.dumps({
        "uri": "http://127.0.0.1:9", "db": "team-db", "name": "lid-creao",
        "client": "claude", "role": "member"}), encoding="utf-8")
    (folder / "identity.json").write_text(json.dumps({
        "token": "t_helper", "participant_id": 3, "fence": 1}), encoding="utf-8")
    with pytest.raises(driver.RunnerError, match="presenter"):
        driver.connect(tmp_path)
    # Another client's config is refused before any join, and left exactly as it was.
    assert load_json(folder / "config.json")["client"] == "claude"
    assert load_json(folder / "identity.json")["token"] == "t_helper"


def test_the_heartbeat_retries_with_a_bounded_backoff_and_stops_quietly():
    class DeadHub:
        def __init__(self):
            self.calls = 0

        def heartbeat(self, body):
            self.calls += 1
            raise RuntimeError("venue wifi")

    class Stop:
        def __init__(self, after):
            self.waits, self.after, self.flag = [], after, False

        def is_set(self):
            return self.flag

        def wait(self, seconds):
            self.waits.append(seconds)
            self.flag = len(self.waits) >= self.after

    hub, stop = DeadHub(), Stop(after=7)
    driver._heartbeat(hub, every=10.0, stop=stop)
    assert stop.waits == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert hub.calls == 7


def test_serve_prints_and_opens_the_port_it_actually_bound(monkeypatch, capsys):
    class BoundHTTPD:
        server_address = ("127.0.0.1", 54321)

        def __init__(self):
            self.closed = False

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            self.closed = True

    httpd = BoundHTTPD()
    monkeypatch.setattr(driver, "ThreadingHTTPServer", lambda addr, handler: httpd)
    stop = threading.Event()
    driver.serve(a_runner(FakeIO(snapshot=ready_snapshot()), Clock()), 0,
                 open_browser=False, stop=stop)
    assert "http://127.0.0.1:54321/" in capsys.readouterr().out
    assert httpd.closed and stop.is_set()


# --------------------------------------------------------------------------- the shipped timelines

TIMELINES = ["table.json", "table-safe.json", "timeline.json"]


@pytest.mark.parametrize("name", TIMELINES)
def test_every_shipped_timeline_validates(name):
    plan = driver.load_plan(ROOT / "demo" / "perch" / name)
    assert plan["steps"] and plan["cast"]


@pytest.mark.parametrize("name", TIMELINES)
def test_every_named_owner_and_steer_is_in_the_cast(name):
    plan = driver.load_plan(ROOT / "demo" / "perch" / name)
    assert plan["probe"] and set(plan["probe"]) <= set(plan["cast"])
    for s in plan["steps"]:
        for owner in (s.get("args") or {}).get("owners", {}).values():
            assert owner in plan["cast"], f"{s['id']} names {owner}"
        steer = ((s.get("on_start") or {}).get("steer") or {}).get("to")
        if steer:
            assert steer in plan["cast"], f"{s['id']} steers {steer}"


def test_the_table_run_is_the_five_beats_in_order():
    plan = driver.load_plan(ROOT / "demo" / "perch" / "table.json")
    assert [s["id"] for s in plan["steps"]] == ["brief", "recall", "tasks", "refusal", "first_part"]
    assert [s["predicate"] for s in plan["steps"]] == [
        "brief_sent", "recall", "tasks_landed", "refusal", "first_part"]
    assert plan["gate"]["first_op_by"] == 75
    assert plan["brief"]["from"] == "phone"
    assert plan["steps"][0]["deadline"] == 10
    assert plan["steps"][-1]["at"] < plan["gate"]["first_op_by"]
    assert plan["steps"][3]["guard"] == {"held_by": {"object": "Podium_Body",
                                                    "name": "lead-claude"}}
    assert plan["steps"][4]["args"]["parts"] == ["Podium_Body", "Podium_Lid"]
    assert plan["steps"][0]["args"]["phone"] is True
    assert plan["steps"][0]["args"]["by_not"] == "presenter"


def test_the_table_brief_asks_for_two_parts_and_recalls_act1():
    text = driver.load_plan(ROOT / "demo" / "perch" / "table.json")["brief"]["text"]
    assert "Podium_Body" in text and "Podium_Lid" in text and "Medallion" not in text
    assert "Recall the Perch" in text and "180 mm bed" in text


def test_the_safe_timeline_is_lid_only_and_has_no_refusal_beat():
    plan = driver.load_plan(ROOT / "demo" / "perch" / "table-safe.json")
    assert "Podium_Lid" in plan["brief"]["text"] and "Podium_Body" not in plan["brief"]["text"]
    assert plan["steps"][2]["args"]["owners"] == {"Podium_Lid": "lid-creao"}
    assert [s["id"] for s in plan["steps"]] == ["brief", "recall", "tasks", "first_part"]
    assert [s["predicate"] for s in plan["steps"]] == [
        "brief_sent", "recall", "tasks_landed", "first_part"]
    assert plan["steps"][-1]["args"]["parts"] == ["Podium_Lid"]
    assert plan["gate"]["first_op_by"] == 75


def test_the_booth_timeline_covers_every_beat_the_window_claims():
    plan = driver.load_plan(ROOT / "demo" / "perch" / "timeline.json")
    predicates = {s["predicate"] for s in plan["steps"]}
    assert {"brief_sent", "recall", "tasks_landed", "refusal", "parts_landed", "param_changed",
            "confirm", "report", "takeover", "handoff", "review_round", "op_done"} <= predicates
    reports = {s["args"].get("kind") for s in plan["steps"] if s["predicate"] == "report"}
    assert {"fea_report", "lattice_report"} <= reports
    assert "planner-claude" not in plan["probe"]
    assert "planner-claude" in plan["cast"]
    assert plan["brief"]["from"] == "panel"
