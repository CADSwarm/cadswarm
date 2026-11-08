"""End-to-end check of CADSwarm's multiplayer on a real local team. Documented in tests/e2e/README.md.

    python tests/e2e/team_e2e.py [--out DIR] [--scenario team|build|drill|remote|handoff|all] [--runs N]
                               [--agent claude|codex|opencode|fake]

What it does, the way a user would:
  * starts a SpacetimeDB on this computer (a private data folder, port 3000) unless one is already there,
  * hosts a team with hosting.start_team (the same call the setup guide makes: publish the module,
    `cadswarm-host init`, `cadswarm-host run`), then mints invites with hosting.mint_invite;
  * team:   two CADSwarm app windows (`python -m cadswarm.app`, each its own profile) join with invites plus
            a headless member; checks presence, chat, locks (and refusal), pointers; screenshots both windows;
  * build:  hands a short request to the team (`@team ...` = POST /briefs) and waits while the host plans
            numbered steps and spawns agent CLIs that build them through the cadswarm MCP server; checks the
            step plan, the order the steps were done in, the parts in the shared model and the reviewer;
  * remote: drives the web app's remote view and remote control (tests/e2e/web_remote.mjs) against the team.

It needs: host/target/release/cadswarm-host(.exe) and the module wasm built from this checkout, the
`spacetime` CLI (2.13.0) signed in (`spacetime login`), FreeCAD 1.1 for the app windows, an agent CLI
for `build` (or --agent fake), and `npm ci` in web/ for `remote`. Prints a JSON summary and writes it to
<out>/summary.json. Exit code 0 only if every scenario passed.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))
EXE = ".exe" if os.name == "nt" else ""

from cadswarm import hosting, invite as invite_mod  # noqa: E402
from cadswarm.agent_setup import redeem  # noqa: E402
from cadswarm.space_hub import SpaceHub  # noqa: E402

PORT = hosting.LOCAL_PORT


def log(*a):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[e2e]", *a, flush=True)


def built_tools():
    host = os.path.join(ROOT, "host", "target", "release", "cadswarm-host" + EXE)
    wasm = os.path.join(ROOT, "module", "target", "wasm32-unknown-unknown", "release", "cadswarm_module.wasm")
    missing = [p for p in (host, wasm) if not os.path.isfile(p)]
    return host, wasm, missing


def freecad_python():
    from cadswarm.config import find_freecad_python
    return find_freecad_python()


def port_open(port=PORT):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


class Team:
    """A local team hosted from `out/host`, the way the setup guide hosts one."""

    def __init__(self, out, agent="claude", agent_command=None):
        self.out = out
        self.data = os.path.join(out, "host")
        self.db = "cadswarm-e2e-" + uuid.uuid4().hex[:8]
        self.agent = agent
        self.agent_command = agent_command
        self.procs = []
        self.info = None

    def start(self):
        host, wasm, missing = built_tools()
        if missing:
            raise RuntimeError(f"build these first: {missing}")
        os.environ["CADSWARM_HOST_BIN"] = host
        os.environ["CADSWARM_MODULE_WASM"] = wasm
        if not port_open():
            st = hosting.spacetime_bin()
            stdb = os.path.join(self.out, "stdb")
            os.makedirs(stdb, exist_ok=True)
            logf = open(os.path.join(self.out, "spacetime.log"), "a")
            self.procs.append(subprocess.Popen([st, "start", "--listen-addr", f"0.0.0.0:{PORT}", "--data-dir", stdb],
                                               stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL))
            deadline = time.time() + 60
            while not port_open() and time.time() < deadline:
                time.sleep(0.3)
        python = os.path.join(os.path.dirname(sys.executable), "python" + EXE)
        t0 = time.time()
        self.info = hosting.start_team(python, self.data, "local", self.db, agent=self.agent if self.agent != "fake" else "claude",
                                       on_log=lambda line: log(line))
        if self.agent_command:
            cfg = hosting.host_config(self.data)
            cfg["agent_command"] = self.agent_command
            with open(os.path.join(self.data, "host.json"), "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        hosting.wait_ready(self.data, timeout=120, pids=[self.info["host_pid"]])
        log(f"team {self.db} hosted in {time.time() - t0:.1f}s (host pid {self.info['host_pid']})")
        return self

    def invite(self, kind="human", role="member"):
        return invite_mod.make_invite(hosting.mint_invite(self.data, kind, role), "e2e")

    def member(self, name, kind="human", client="script"):
        """A headless member (what an MCP server or the web app is to the team): redeem an invite, join."""
        t = hosting.mint_invite(self.data, kind, "member")
        ident = redeem(t, name, client)
        hub = SpaceHub({"uri": t["uri"], "db": t["db"], "name": name}, ident, timeout=60)
        hub.identity = hub.join(client=client)
        return hub

    def host_log(self, lines=60):
        try:
            with open(os.path.join(self.data, ".cadswarm-team", "host.log"), encoding="utf-8", errors="replace") as f:
                return f.read().splitlines()[-lines:]
        except OSError:
            return []

    def stop(self):
        if self.info:
            hosting.stop_team(self.info)
        for p in self.procs:
            p.terminate()


def wait(pred, timeout, every=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(every)
    return None


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------------------------------- team

def scenario_team(team, out):
    """Two app windows + a headless member: presence, chat, locks, pointers, screenshots."""
    fc = freecad_python()
    if not fc:
        return {"ok": False, "skipped": "FreeCAD not found"}
    win = os.path.join(out, "windows")
    os.makedirs(win, exist_ok=True)
    part = "E2EBox"
    procs = []
    for name, peer, locker in (("alice", "bob", True), ("bob", "alice", False)):
        argv = [fc, os.path.join(HERE, "app_client.py"), "--out", win, "--invite", team.invite(), "--name", name,
                "--peer", peer, "--part", part] + (["--locker"] if locker else [])
        logf = open(os.path.join(win, f"{name}.log"), "w")
        procs.append(subprocess.Popen(argv, cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT))
    carol = team.member("carol-headless", "agent", "script")
    result = {"windows": {}}
    try:
        # Both windows are in before the part appears, so each sees it arrive in its live model.
        both = wait(lambda: all("joined" in ((read_json(os.path.join(win, f"{n}.json")) or {}).get("steps") or {})
                                for n in ("alice", "bob")), 180, 1)
        result["both_joined"] = bool(both)
        carol.post("/locks", {"names": [part]})
        carol.post("/ops", {"op": "create_primitive", "args": {"kind": "box", "name": part,
                                                             "props": {"Length": 40, "Width": 30, "Height": 20}}})
        carol.post("/locks/release", {"names": [part]})
        # Once alice holds it, carol points at it (an agent's arrow) and then tries to take it: refused.
        held = wait(lambda: next((o for o in carol.get("/model")["objects"] if o["name"] == part
                                  and o.get("locked_by") == "alice"), None), 120, 1)
        result["alice_holds_part"] = bool(held)
        carol.post("/pointer", {"object": part, "mode": "inspecting", "label": "checking E2EBox"})
        try:
            carol.post("/locks", {"names": [part]})
            result["headless_lock_refused"] = False
        except Exception as e:  # noqa: BLE001
            result["headless_lock_refused"] = "lock" in str(e).lower()
        carol.post("/messages", {"to": "all", "body": "hello from carol-headless", "kind": "chat"})
        heard = wait(lambda: [m for m in carol.get("/messages").get("messages", [])
                              if m.get("body", "").startswith("hello from ")], 60, 1)
        result["headless_heard"] = sorted({m["body"] for m in heard or []})
        done = wait(lambda: all("screenshot" in ((read_json(os.path.join(win, f"{n}.json")) or {}).get("done") or [])
                                or ((read_json(os.path.join(win, f"{n}.json")) or {}).get("errors"))
                                for n in ("alice", "bob")), 240, 1)
        presence = carol.get("/participants")
        result["presence_headless_view"] = {p["name"]: p.get("status") for p in presence.get("participants", presence)} \
            if isinstance(presence, dict) else presence
    finally:
        open(os.path.join(win, "stop"), "w").close()
        for p in procs:
            try:
                p.wait(60)
            except subprocess.TimeoutExpired:
                p.kill()
        try:
            carol.post("/leave")
        except Exception:  # noqa: BLE001
            pass
    for n in ("alice", "bob"):
        result["windows"][n] = read_json(os.path.join(win, f"{n}.json"))
    a, b = result["windows"]["alice"] or {}, result["windows"]["bob"] or {}
    checks = {
        "both_joined": result["both_joined"],
        "presence": "bob" in (a.get("steps", {}).get("presence") or {}) and "alice" in (b.get("steps", {}).get("presence") or {}),
        "chat": bool(a.get("steps", {}).get("chat", {}).get("heard_peer")) and bool(b.get("steps", {}).get("chat", {}).get("heard_peer")),
        "headless_heard_both": {"hello from alice", "hello from bob"} <= set(result["headless_heard"]),
        "lock_via_panel": result["alice_holds_part"],
        "lock_shown_in_both": "lock_shown" in a.get("done", []) and "lock_shown" in b.get("done", []),
        "second_lock_refused": bool(b.get("steps", {}).get("refused", {}).get("refused")) and result.get("headless_lock_refused"),
        "pointer_seen_in_both": "pointer" in a.get("done", []) and "pointer" in b.get("done", []),
        "screenshots": len(a.get("shots", [])) + len(b.get("shots", [])) >= 2,
        "no_window_errors": not a.get("errors") and not b.get("errors"),
    }
    result["checks"] = checks
    result["ok"] = all(checks.values())
    return result


# ------------------------------------------------------------------------------------------ build

BUILD_REQUEST = ("a desk pen holder: a 60 x 60 x 6 mm square base plate, then a hollow cylinder on top of it "
                 "(outer diameter 50 mm, 3 mm wall, 80 mm tall). Two parts, no screws, no board.")


def check_order(steps):
    """Each step started only after every step it depends on was done. Returns a list of violations."""
    by_id = {s.get("id"): s for s in steps}
    bad = []
    for s in steps:
        for dep in s.get("depends_on") or []:
            d = by_id.get(dep)
            if not d:
                continue
            if s.get("started_at") and d.get("finished_at") and s["started_at"] + 0.5 < d["finished_at"]:
                bad.append(f"step {s['n']} started {d['finished_at'] - s['started_at']:.1f}s before step {d['n']} finished")
    return bad


def scenario_build(team, out, agent="claude", request=BUILD_REQUEST, timeout=1500):
    """@team <request>: the host plans numbered steps, spawns agents that build them in order via MCP."""
    dana = team.member("dana", "human", "script")
    t0 = time.time()
    bid = dana.post("/briefs", {"text": request})["brief"]
    log(f"brief {bid} handed to the team ({agent})")
    seen, last = [], None
    final = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = next((x for x in dana.get("/briefs")["briefs"] if x["id"] == bid), None)
        status = b and b["status"]
        line = (b or {}).get("progress") and b["progress"]["line"]
        if (status, line) != last:
            last = (status, line)
            seen.append({"t": round(time.time() - t0), "status": status, "progress": line})
            log(f"  {round(time.time() - t0):>4}s {status} {line or ''}")
        if status in ("ready", "failed", "cancelled"):
            final = b
            break
        time.sleep(3)
    prog = {}
    try:
        prog = dana.get(f"/briefs/{bid}/progress")
    except Exception as e:  # noqa: BLE001
        prog = {"error": str(e)}
    steps = prog.get("steps") or []
    model = dana.get("/model")
    objects = {o["name"]: o for o in model["objects"]}
    reviews = [r for r in (dana._rows("v_review") or []) if r.get("brief") == bid]
    members = [m["name"] for m in dana._rows("v_member")]
    step_objects = [n for s in steps if s.get("kind") == "part" for n in s.get("objects") or []]
    built = [n for n in step_objects if n in objects and (objects[n].get("volume") or 0) > 0]
    checks = {
        "finished_ready": bool(final) and final["status"] == "ready",
        "numbered_step_plan": prog.get("version") == 2 and len(steps) >= 2
                              and [s.get("n") for s in steps] == list(range(1, len(steps) + 1)),
        "all_steps_done": bool(steps) and all(s.get("status") == "done" for s in steps),
        "built_in_dependency_order": bool(steps) and not check_order(steps),
        "parts_in_shared_model": bool(step_objects) and len(built) == len(step_objects),
        "reviewed": any(r.get("passed") for r in reviews),
        "agents_retired": not any(m.startswith(("planner-", "worker", "reviewer-")) for m in members
                                  if any(x["name"] == m and x["status"] == "active" for x in dana._rows("v_member"))),
    }
    try:
        dana.post("/leave")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": all(checks.values()), "checks": checks, "brief": bid, "seconds_to_finish": round(time.time() - t0),
            "timeline": seen, "error": (final or {}).get("error") if final and final["status"] != "ready" else None,
            "step_objects": step_objects,
            "steps": [{k: s.get(k) for k in ("n", "name", "kind", "status", "owner", "objects", "depends_on",
                                             "started_at", "finished_at")} for s in steps],
            "order_violations": check_order(steps),
            "objects": {n: {"volume": o.get("volume"), "bbox": o.get("bbox"), "locked_by": o.get("locked_by")}
                        for n, o in objects.items()},
            "reviews": [{k: r.get(k) for k in ("round", "passed", "findings_json")} for r in reviews],
            "host_log": [l for l in team.host_log(200) if bid in l or "brief" in l][-30:]}


# ----------------------------------------------------------------------------------------- remote

def scenario_remote(team, out, part="PhoneBox"):
    """The web app's api.ts (the phone's code) joins with an invite and drives the live team; a desktop-side
    member checks that what the phone did arrived."""
    web = os.path.join(ROOT, "web")
    if not os.path.isdir(os.path.join(web, "node_modules")):
        return {"ok": False, "skipped": "run `npm ci` in web/ first"}
    watcher = team.member("erin", "human", "script")
    env = dict(os.environ, CADSWARM_E2E_INVITE=team.invite(), CADSWARM_E2E_OUT=out, CADSWARM_E2E_PART=part)
    npx = "npx.cmd" if os.name == "nt" else "npx"
    r = subprocess.run([npx, "vitest", "run", "test/remote.live.test.ts"], cwd=web, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300, shell=os.name == "nt")
    with open(os.path.join(out, "web-remote.log"), "w", encoding="utf-8") as f:
        f.write(r.stdout + r.stderr)
    model = watcher.get("/model")
    obj = next((o for o in model["objects"] if o["name"] == part), None)
    names = {p["name"]: p.get("status") for p in model["participants"]}
    heard = [m["body"] for m in watcher.get("/messages").get("messages", [])]
    pointers = [p for p in watcher._rows("v_pointer") if p.get("object") == part]
    checks = {
        "web_test_passed": r.returncode == 0 and "1 passed" in r.stdout,
        "phone_is_a_member": names.get("phone") == "active",
        "desktop_heard_phone_chat": "hello from the phone" in heard,
        "phone_built_part_on_host": bool(obj) and (obj.get("volume") or 0) > 0,
        "phone_holds_lock": bool(obj) and obj.get("locked_by") == "phone",
        "phone_pointer_visible": bool(pointers),
    }
    try:
        watcher.post("/leave")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": all(checks.values()), "checks": checks, "web": read_json(os.path.join(out, "web-remote.json")),
            "vitest_tail": (r.stdout + r.stderr)[-1500:] if r.returncode else ""}


# ---------------------------------------------------------------------------------------- handoff

def scenario_handoff(team, out, part="HandoffBlock"):
    """A person holding a part hands off now: the host starts an agent that calls accept_handoff first and
    takes over the person's locks and task."""
    fay = team.member("fay", "human", "script")
    fay.post("/locks", {"names": [part]})
    fay.post("/ops", {"op": "create_primitive", "args": {"kind": "box", "name": part,
                                                         "props": {"Length": 10, "Width": 10, "Height": 10}}})
    t = fay.post("/tasks", {"title": "finish the block", "objects": [part]})
    fay.post(f"/tasks/{t['id']}/claim", {})
    hid = "h_" + uuid.uuid4().hex[:8]
    fay._f("request_handoff", hid, "please finish the block: add nothing, just keep it", "now")
    t0 = time.time()
    row = wait(lambda: next((h for h in fay._rows("v_handoff") if h["id"] == hid and h["status"] == "accepted"), None),
               600, 2)
    names = {m["id"]: m["name"] for m in fay._rows("v_member")}
    taker = names.get(row["taker"]) if row else None
    # The lock moved to the agent the moment it accepted (it may already be off the team and the lock freed).
    events = [a for a in fay._rows("v_activity") if "handoff" in (a.get("kind") or "") or "picked up" in (a.get("summary") or "")]
    checks = {"accepted": bool(row), "taken_by_a_handoff_agent": bool(taker and taker.startswith("handoff-")),
              "agent_retired_after": bool(wait(lambda: not any(m["name"] == taker and m["status"] == "active"
                                                                for m in fay._rows("v_member")), 300, 2)) if taker else False}
    try:
        fay.post("/leave")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": all(checks.values()), "checks": checks, "taker": taker, "seconds": round(time.time() - t0),
            "events": [a.get("summary") for a in events][-5:]}


# ------------------------------------------------------------------------------------------ main

DRILL_REQUEST = (
    "Build a cordless drill cutaway as named subassemblies in dependency order: ChuckAssembly, "
    "SpindleAssembly, PlanetaryGearTrain, ElectricMotorAssembly, PistolGripHousing, "
    "TriggerSwitchAssembly, and BatteryPack. Use separate valid solids and keep each component named exactly."
)


def scenario_drill(team, out, agent="claude"):
    """Exercise the same planner/worker/reviewer path with the named cordless-drill components."""
    result = scenario_build(team, out, agent, request=DRILL_REQUEST)
    expected = {"ChuckAssembly", "SpindleAssembly", "PlanetaryGearTrain", "ElectricMotorAssembly",
                "PistolGripHousing", "TriggerSwitchAssembly", "BatteryPack"}
    result["checks"]["named_drill_subassemblies"] = expected <= set(result.get("step_objects") or [])
    result["ok"] = all(result["checks"].values())
    result["expected_drill_subassemblies"] = sorted(expected)
    return result


SCENARIOS = {"team": scenario_team, "build": scenario_build, "drill": scenario_drill,
             "remote": scenario_remote, "handoff": scenario_handoff}
ALL_SCENARIOS = ("team", "build", "remote", "handoff")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "/tmp"), "cadswarm-e2e-" + time.strftime("%H%M%S")))
    ap.add_argument("--scenario", default="team", choices=list(SCENARIOS) + ["all"])
    ap.add_argument("--agent", default="claude")
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    names = list(ALL_SCENARIOS) if args.scenario == "all" else [args.scenario]
    summary = {"out": args.out, "runs": []}
    for run in range(args.runs):
        out = os.path.join(args.out, f"run{run + 1}")
        os.makedirs(out, exist_ok=True)
        fake = [sys.executable, os.path.join(HERE, "fake_agent.py"), "{prompt}"] if args.agent == "fake" else None
        team = Team(out, args.agent, fake)
        res = {}
        try:
            team.start()
            for n in names:
                t0 = time.time()
                try:
                    res[n] = (SCENARIOS[n](team, out, args.agent) if n in ("build", "drill")
                              else SCENARIOS[n](team, out))
                except Exception as e:  # noqa: BLE001
                    import traceback
                    res[n] = {"ok": False, "error": traceback.format_exc()[-1500:]}
                res[n]["seconds"] = round(time.time() - t0, 1)
                log(n, "PASS" if res[n].get("ok") else "FAIL", res[n].get("checks") or res[n].get("error", ""))
        finally:
            res["host_log_tail"] = team.host_log(40)
            team.stop()
        summary["runs"].append(res)
        with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)
    ok = all(r.get(n, {}).get("ok") for r in summary["runs"] for n in names)
    log("summary:", os.path.join(args.out, "summary.json"), "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
