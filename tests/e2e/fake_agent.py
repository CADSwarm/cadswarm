"""A scripted stand-in for an agent CLI (Claude Code / Codex / OpenCode) that speaks MCP to the real cadswarm
MCP server, so the host's whole step-build path runs without an AI account and the same way every time.

The host starts it exactly like a real CLI: `agent_command` in host.json is
    [<python>, tests/e2e/fake_agent.py, "{prompt}"]
in the agent's own folder, after `cadswarm setup` wrote that folder's .mcp.json. This script reads the
.mcp.json, starts the server it names (stdio), does the MCP handshake and calls tools/call, acting on the
role the prompt names: PLANNER (two numbered steps), WORKER (next_step loop), REVIEWER (checks and a
verdict) or a handoff pick-up. It never touches the team except through MCP tools.

The default build is a base plate and post. The opt-in drill scenario builds seven named subassemblies and
uses independent first steps so the host can start up to three workers in parallel.
"""
import json
import os
import re
import subprocess
import sys
import time

LOG = []


def say(*a):
    print("[fake-agent]", *a, flush=True)


class Mcp:
    def __init__(self, folder):
        cfg = json.load(open(os.path.join(folder, ".mcp.json"), encoding="utf-8"))["mcpServers"]["cadswarm"]
        env = dict(os.environ, **(cfg.get("env") or {}))
        self.p = subprocess.Popen([cfg["command"], *cfg.get("args", [])], cwd=folder, env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        self.n = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "cadswarm-fake-agent", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = {t["name"] for t in self.rpc("tools/list", {}).get("tools", [])}

    def _send(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def rpc(self, method, params):
        self.n += 1
        self._send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP server exited during {method}")
            msg = json.loads(line)
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result") or {}

    def call(self, tool, **args):
        r = self.rpc("tools/call", {"name": tool, "arguments": args})
        text = "".join(c.get("text", "") for c in r.get("content", []) if c.get("type") == "text")
        try:
            out = json.loads(text) if text else (r.get("structuredContent") or {})
        except ValueError:
            out = {"text": text}
        if isinstance(out, dict) and "result" in out and len(out) == 1 and isinstance(out["result"], dict):
            out = out["result"]
        say(tool, json.dumps(args)[:120], "->", ("ERROR " if r.get("isError") else "") + json.dumps(out)[:200])
        if r.get("isError"):
            raise RuntimeError(f"{tool} failed: {text[:300]}")
        return out

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(10)
        except Exception:  # noqa: BLE001
            self.p.kill()


BASE_STEPS = [
    {"n": 1, "id": "s01", "name": "Base plate", "kind": "part", "objects": ["FakeBase"], "depends_on": [],
     "summary": "The base plate: a 60 x 60 x 6 mm slab every other part stands on.",
     "ops": ["Extrude FakeBase", "Save part"],
     "build": {"kind": "box", "props": {"Length": 60, "Width": 60, "Height": 6},
               "placement": {"base": [-30, -30, 0]}}},
    {"n": 2, "id": "s02", "name": "Post", "kind": "part", "objects": ["FakePost"], "depends_on": ["s01"],
     "summary": "The post: a 20 mm diameter, 50 mm tall cylinder standing on the base plate's centre.",
     "ops": ["Extrude FakePost", "Save part"],
     "build": {"kind": "cylinder", "props": {"Radius": 10, "Height": 50}, "placement": {"base": [0, 0, 6]}}},
]


DRILL_STEPS = [
    {"n": 1, "id": "d01", "name": "Chuck assembly", "kind": "part", "objects": ["ChuckAssembly"],
     "depends_on": [], "summary": "A stepped chuck shell aligned to the drill axis.",
     "build": {"kind": "cylinder", "props": {"Radius": 20, "Height": 34},
               "placement": {"base": [0, 0, 0], "rotation": {"axis": [0, 1, 0], "angle": 90}}}},
    {"n": 2, "id": "d02", "name": "Planetary gear train", "kind": "part", "objects": ["PlanetaryGearTrain"],
     "depends_on": [], "summary": "A compact planetary gear stage reduces motor speed at the spindle.",
     "build": {"kind": "cylinder", "props": {"Radius": 25, "Height": 32},
               "placement": {"base": [72, 0, 0], "rotation": {"axis": [0, 1, 0], "angle": 90}}}},
    {"n": 3, "id": "d03", "name": "Electric motor assembly", "kind": "part",
     "objects": ["ElectricMotorAssembly"], "depends_on": [],
     "summary": "A cylindrical electric motor follows the gear train on the drill axis.",
     "build": {"kind": "cylinder", "props": {"Radius": 22, "Height": 50},
               "placement": {"base": [106, 0, 0], "rotation": {"axis": [0, 1, 0], "angle": 90}}}},
    {"n": 4, "id": "d04", "name": "Pistol grip housing", "kind": "part", "objects": ["PistolGripHousing"],
     "depends_on": [], "summary": "A pistol grip housing hangs below the motor and gearbox.",
     "build": {"kind": "box", "props": {"Length": 42, "Width": 40, "Height": 70},
               "placement": {"base": [158, -20, -88]}}},
    {"n": 5, "id": "d05", "name": "Spindle assembly", "kind": "part", "objects": ["SpindleAssembly"],
     "depends_on": ["d01", "d02"], "summary": "The spindle transmits chuck torque into the gearbox.",
     "build": {"kind": "cylinder", "props": {"Radius": 6, "Height": 36},
               "placement": {"base": [35, 0, 0], "rotation": {"axis": [0, 1, 0], "angle": 90}}}},
    {"n": 6, "id": "d06", "name": "Trigger switch assembly", "kind": "part",
     "objects": ["TriggerSwitchAssembly"], "depends_on": ["d04"],
     "summary": "A separate trigger switch sits along the front face of the pistol grip.",
     "build": {"kind": "box", "props": {"Length": 14, "Width": 18, "Height": 22},
               "placement": {"base": [172, 21, -44]}}},
    {"n": 7, "id": "d07", "name": "Battery pack", "kind": "part", "objects": ["BatteryPack"],
     "depends_on": ["d04"], "summary": "A removable battery pack attaches beneath the pistol grip.",
     "build": {"kind": "box", "props": {"Length": 50, "Width": 50, "Height": 40},
               "placement": {"base": [154, -25, -129]}}},
]


def steps_for_prompt(prompt):
    return DRILL_STEPS if "drill" in prompt.lower() else BASE_STEPS


def plan(m, brief, steps):
    plan_steps = []
    for s in steps:
        t = m.call("create_task", title=f"{s['n']}. {s['name']}", detail=json.dumps(s["build"]), objects=s["objects"],
                   brief=brief)
        plan_steps.append({k: v for k, v in s.items() if k != "build"} | {"task": t.get("id") or t.get("task_id"),
                                                                           "assignee": ""})
    title = "Cordless drill cutaway" if "drill" in m.last_prompt.lower() else "Fake post"
    m.call("set_brief_plan", brief_id=brief, plan_json={"version": 2, "title": title, "steps": plan_steps})


def work(m, brief):
    deadline = time.time() + 600
    while time.time() < deadline:
        nxt = m.call("next_step", brief_id=brief)
        if nxt.get("done") or nxt.get("stuck"):
            return
        if nxt.get("wait"):
            m.call("wait_for_changes", seconds=5)
            continue
        step, task = nxt["step"], nxt["task"]
        tid = task.get("id") if isinstance(task, dict) else task
        try:
            m.call("claim_task", task_id=tid)
        except RuntimeError as e:
            if "already" not in str(e).lower():
                continue
        name = step["objects"][0]
        detail = task.get("detail") or "{}"
        spec = json.loads(detail) if isinstance(detail, str) else detail
        m.call("lock_objects", names=[name])
        m.call("get_inbox")
        m.call("create_primitive", kind=spec["kind"], name=name, props=spec["props"], placement=spec["placement"])
        m.call("point_at", name=name, mode="editing")
        clash = m.call("check_interference")
        model = m.call("model_status", detail=True)
        built = next((o for o in model.get("objects", []) if o.get("name") == name), {})
        if float(built.get("volume") or 0) <= 0:
            raise RuntimeError(f"{name} missing or has zero volume in model_status")
        m.call("report_result", task_id=tid, status="done",
               summary=f"{name} built ({spec['kind']} {spec['props']}); volume={built.get('volume')}; "
                       f"interference: {json.dumps(clash)[:200]}",
               evidence=[name])
        m.call("release_objects", names=[name])
    raise RuntimeError("worker ran out of time")


def review(m, brief, rnd):
    progress = m.call("build_progress", brief_id=brief)
    names = [name for s in progress.get("steps", []) for name in s.get("objects", [])]
    model = m.call("model_status", detail=True)
    objects = {o.get("name"): o for o in model.get("objects", [])}
    missing = [name for name in names if name not in objects or float(objects[name].get("volume") or 0) <= 0]
    for name in names:
        if name in objects:
            m.call("point_at", name=name, mode="inspecting")
    interference = m.call("check_interference", names=names) if names else {"clashes": []}
    clashes = interference.get("clashes", [])
    passed = not missing and not clashes
    findings = ([{"object": name, "problem": "missing or zero-volume shared model object"} for name in missing]
                + [{"object": c.get("a"), "problem": f"interferes with {c.get('b')} by {c.get('volume')} mm^3"}
                   for c in clashes])
    m.call("report_review", brief_id=brief, round=rnd, passed=passed, findings=findings)


def main():
    prompt = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    folder = os.getcwd()
    say("role prompt:", prompt[:100].replace("\n", " "))
    m = Mcp(folder)
    try:
        brief = (re.search(r"brief (b_[0-9a-f]+)", prompt) or [None, None])[1]
        if "accept_handoff(handoff_id=" in prompt:
            hid = re.search(r'accept_handoff\(handoff_id="([^"]+)"', prompt).group(1)
            m.call("accept_handoff", handoff_id=hid)
            m.call("model_status")
        elif "PLANNER" in prompt:
            m.last_prompt = prompt
            plan(m, brief, steps_for_prompt(prompt))
        elif "WORKER" in prompt:
            work(m, brief)
        elif "REVIEWER" in prompt:
            rnd = int((re.search(r"review round (\d+)", prompt) or [0, 1])[1])
            review(m, brief, rnd)
        else:
            say("no role in the prompt")
            return 2
    finally:
        m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
