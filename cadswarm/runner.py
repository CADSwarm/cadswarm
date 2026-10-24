"""Keeps one AI helper on the team without anyone opening a terminal.

    python cadswarm_launch.py runner <helper folder>

FreeCAD starts this in the background for each AI helper set up on this computer. While the helper has
nothing to do, the runner keeps it on the team as "Ready for instructions". When a person messages it,
another helper asks it for something, a task is handed to it, or a task it handed out is finished, the
runner starts the agent CLI headless in the helper's folder (`claude -p`, `codex exec`, `droid exec`,
`opencode run`) with a
short note of what's new. The agent does the work with the cadswarm MCP tools, answers in the team chat and
exits; the runner goes back to waiting.

One runner per folder: it writes .cadswarm/runner.json every few seconds, and a second runner exits while
that is fresh. Runs are rate limited, so two helpers can't keep waking each other.

Stdlib only: it runs with FreeCAD's Python.
"""
import json
import os
import subprocess
import sys
import time

if __package__ in (None, ""):  # run as a script: make `cadswarm` importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cadswarm.agent_setup import agent_ready, find_agent_cli  # noqa: E402
from cadswarm.client import HubRequestError, load_json, save_json, state_dir  # noqa: E402
from cadswarm.paths import LAUNCHER  # noqa: E402
from cadswarm.space_hub import SpaceHub  # noqa: E402

POLL_SECONDS = 3
HEARTBEAT_SECONDS = 10
ALIVE_SECONDS = 30          # runner.json older than this means no runner is looking after the folder
RUN_TIMEOUT = 20 * 60       # one wake-up may take this long before it is stopped
MAX_RUNS_PER_HOUR = 30
MIN_GAP_SECONDS = 5
LOG_LIMIT = 2_000_000
IDLE_DOING = "listening for instructions"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GONE = ("not_member", "left", "taken_over", "bad_token")



def status_path(folder):
    return os.path.join(state_dir(folder), "runner.json")


def runner_alive(folder, now=None):
    """True while a runner looks after `folder` (it rewrites runner.json every few seconds)."""
    st = load_json(status_path(folder)) or {}
    return (now or time.time()) - float(st.get("beat") or 0) < ALIVE_SECONDS


def runner_status(folder):
    """{"state": "listening" | "working" | "stopped" | ..., "detail", "beat", "pid"} as the runner last wrote it."""
    st = load_json(status_path(folder)) or {}
    if not runner_alive(folder):
        st["state"] = "stopped"
    return st


def start_runner(folder, python, launcher, project=""):
    """Start the runner for `folder` quietly in the background (no window). Returns its PID, or None when one
    is already running."""
    if runner_alive(folder):
        return None
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "cwd": folder, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # keeps running after FreeCAD closes
    pid = subprocess.Popen([python, launcher, "runner", folder, project], **kwargs).pid
    # Claim the folder right away so a second click (or the next panel refresh) doesn't start another.
    save_json(status_path(folder), {"pid": pid, "beat": time.time(), "state": "starting", "detail": ""})
    return pid


def off_path(folder):
    return os.path.join(state_dir(folder), "runner.off")


def set_background(folder, on):
    """Background starts on or off for `folder`. Off while someone runs the helper in a terminal: two copies
    would keep taking the helper's seat from each other."""
    if on:
        if os.path.exists(off_path(folder)):
            os.remove(off_path(folder))
    else:
        with open(off_path(folder), "w") as f:
            f.write("The helper runs in a terminal; FreeCAD won't start it in the background.\n")


def background_on(folder):
    return not os.path.exists(off_path(folder))


def stop_runner(folder):
    """Ask the runner to stop (it checks for the stop file between polls)."""
    with open(os.path.join(state_dir(folder), "runner.stop"), "w") as f:
        f.write("stop\n")


def _json(text, default):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def pending_work(hub, seen):
    """What's new for this helper that it hasn't been woken for yet: [(key, line)]. `key` goes in `seen`
    once the helper is woken for it, so each thing wakes it once."""
    me, my_name = hub.me_id, (hub.identity.get("name") or "").lower()
    names = hub._names()
    kinds = {m["id"]: m["kind"] for m in hub._rows("v_member")}
    out = []
    for m in hub._unconsumed():
        if m["kind"] == "chat" and m["to"] == "all":
            continue  # people talking among themselves
        key = f"m:{m['id']}"
        if key in seen:
            continue
        who = names.get(m["from"], "someone")
        if m["kind"] == "steer":
            what = f"{who} (a person on your team) asks you"
        elif m["kind"] == "handoff":
            what = f"{who} handed their work to you"
        elif kinds.get(m["from"]) == "human":
            what = f"{who} (a person) says to you"
        else:
            what = f"{who} (another AI helper) asks you"
        out.append((key, f"- {what}: \"{m['body'][:600]}\" (message {m['id']})"))
    tasks = hub._rows("v_task")
    for t in tasks:
        if t["status"] == "open" and (t.get("assignee") or "").lower() == my_name:
            key = f"t:{t['id']}"
            if key not in seen:
                out.append((key, f"- Task {t['id']} is waiting for you: \"{t['title']}\""))
        elif t["created_by"] == me and t["owner"] and t["owner"] != me and t["status"] in ("done", "blocked"):
            key = f"d:{t['id']}:{t['status']}"
            if key not in seen:
                summary = (_json(t.get("result_json"), {}) or {}).get("summary") or ""
                word = "finished" if t["status"] == "done" else "got stuck on"
                out.append((key, f"- {names.get(t['owner'], 'A teammate')} {word} the task you handed out, "
                                 f"\"{t['title']}\" ({t['id']}). {summary[:300]}".rstrip()))
    free = not any((t["status"] == "claimed" and t["owner"] == me)
                   or (t["status"] == "open" and (t.get("assignee") or "").lower() == my_name) for t in tasks)
    if free:  # rebalancing: a busy helper's queue is work this idle one can take
        for t in hub.spare_tasks():
            key = f"s:{t['id']}"
            if key not in seen:
                out.append((key, f"- {t['assignee']} is busy and has task {t['id']} queued: \"{t['title']}\". "
                                 "You're free, so you can pick it up."))
    return out


def finished_keys(hub):
    """Keys for handed-out tasks that were already finished before this runner started (nothing to wake for)."""
    return {f"d:{t['id']}:{t['status']}" for t in hub._rows("v_task")
            if t["created_by"] == hub.me_id and t["status"] in ("done", "blocked")}


def wake_prompt(name, project, lines, first):
    news = "\n".join(lines) if lines else "- Nothing specific yet."
    start = ("Say hello to the team with send_message to \"all\" in one short, friendly sentence (who you "
             "are and that you're ready). Then look at list_tasks: if a task is open, unassigned and fits you, "
             "claim it and build it.\n" if first else "")
    return f"""You are {name}, an AI helper on the team building "{project}" in one shared FreeCAD model.
You run in the background: nobody is watching a terminal, so never ask questions here and never wait for
input. Talk to people only with send_message; they read it as a chat in FreeCAD.

What's new for you:
{news}

What to do:
{start}1. Call get_inbox. Answer each person's message like a teammate in a chat: first a short reply saying
   what you'll do (send_message to them), then do it, then a short reply with what you did, in plain words
   with sizes in mm. Then ack_messages. If you need something from them (a size, a material), ask in one
   message and stop; their answer wakes you again.
2. A request from another AI helper: help if it doesn't clash with what a person asked, and answer them
   with send_message. You may say no and explain why.
3. Tasks waiting for you: claim_task, build, check_interference, report_result. A busy helper's queued task:
   pick_up_task (no task_id) takes the next one and tells them; if it says no_spare_work, someone was quicker.
4. Work that splits well can be shared: list_helpers shows who is free, and delegate_task hands one of them
   a task (it wakes them). When they finish, you're woken with the result.
When everything above is handled, stop. Don't call wait_for_changes: you're woken when something new arrives.
"""


def agent_command(client, exe, folder, prompt, resume):
    """(argv, stdin text). The prompt goes in on stdin where the CLI accepts that: a .cmd launcher would
    mangle a multi-line argument."""
    if client == "claude":
        argv = [exe] + (["--continue"] if resume else []) + [
            "-p", "--max-turns", "80", "--allowedTools", "mcp__cadswarm",
            "--mcp-config", os.path.join(folder, ".mcp.json"),
            # Sonnet handles the CAD tools well at a fraction of the cost of the largest model.
            "--model", os.environ.get("CADSWARM_HELPER_MODEL") or "sonnet"]
        return argv, prompt
    if client == "codex":
        return [exe, "exec", "--full-auto", "--skip-git-repo-check", "-"], prompt
    if client == "opencode":
        # --standalone: a private server per run, not whatever the person's desktop app is running.
        # The prompt goes in on stdin, like claude and codex: an npm .cmd shim would split a
        # multi-line argument.
        argv = [exe, "run", "--standalone", "--auto"]
        if resume:
            argv.append("--continue")
        model = os.environ.get("CADSWARM_HELPER_MODEL")
        if model:
            argv += ["--model", model]
        return argv, prompt
    if client == "droid":
        return [exe, "exec", "--auto", "low", " ".join(prompt.split())], None
    if client == "creao":
        # No CLI: the bridge joins the team itself and asks Creao for one JSON tool call per turn.
        # LAUNCHER, not `-m`: FreeCAD's Python finds the package through the launcher.
        return [sys.executable, LAUNCHER, "creao", "--dir", folder], prompt
    raise ValueError(f"unknown AI helper {client!r}")


class Runner:
    def __init__(self, folder, project="", clock=time.time, spawn=None):
        self.folder = os.path.abspath(folder)
        self.project = project
        self.cfg = load_json(os.path.join(state_dir(self.folder), "config.json")) or {}
        if not self.cfg.get("db"):
            raise SystemExit(f"cadswarm runner: {self.folder} isn't connected to a team")
        self.ident_path = os.path.join(state_dir(self.folder), "identity.json")
        self.hub = SpaceHub(self.cfg, load_json(self.ident_path, {}), timeout=30)
        self.clock = clock
        self.spawn = spawn or self._spawn
        self.child = None
        self.child_started = 0.0
        self.state = load_json(os.path.join(state_dir(self.folder), "runner-state.json")) or {}
        self.seen = set(self.state.get("seen") or [])
        self.runs = [t for t in self.state.get("runs") or [] if clock() - t < 3600]
        self.present = False
        self.last_beat = 0.0
        self.status = ("starting", "")
        self.last_error = ""  # why the last run failed, shown in FreeCAD until a run succeeds

    # ------------------------------------------------------------------ bookkeeping

    def log(self, text):
        path = os.path.join(state_dir(self.folder), "helper.log")
        try:
            if os.path.exists(path) and os.path.getsize(path) > LOG_LIMIT:
                os.replace(path, path + ".old")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except OSError:
            pass

    def save_state(self):
        self.state.update({"seen": sorted(self.seen)[-2000:], "runs": self.runs})
        save_json(os.path.join(state_dir(self.folder), "runner-state.json"), self.state)

    def write_status(self, state=None, detail=None):
        if state is not None:
            self.status = (state, detail or "")
        save_json(status_path(self.folder), {"pid": os.getpid(), "beat": self.clock(),
                                             "state": self.status[0], "detail": self.status[1]})

    def other_runner(self):
        st = load_json(status_path(self.folder)) or {}
        return st.get("pid") not in (None, os.getpid()) and runner_alive(self.folder, self.clock()) \
            and st.get("state") != "starting"

    # -------------------------------------------------------------------- presence

    def be_present(self):
        """Hold the helper's seat while no agent runs: rejoin (a new fence, so a finished agent's leftover
        MCP server can't act) and heartbeat, so the team sees it as ready."""
        now = self.clock()
        if not self.present:
            self.hub.join(client=self.cfg.get("client"))
            self.present, self.last_beat = True, 0.0
        if now - self.last_beat >= HEARTBEAT_SECONDS:
            self.hub._f("heartbeat", IDLE_DOING)
            self.last_beat = now

    # ------------------------------------------------------------------------ runs

    def allowed(self):
        now = self.clock()
        self.runs = [t for t in self.runs if now - t < 3600]
        if len(self.runs) >= MAX_RUNS_PER_HOUR:
            return False, f"paused: it already ran {MAX_RUNS_PER_HOUR} times this hour"
        if self.runs and now - self.runs[-1] < MIN_GAP_SECONDS:
            return False, ""
        return True, ""

    def _spawn(self, argv, stdin_text):
        log = open(os.path.join(state_dir(self.folder), "helper.log"), "a", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
        env["CADSWARM_BACKGROUND"] = "1"
        kwargs = {"cwd": self.folder, "env": env, "stdout": log, "stderr": subprocess.STDOUT,
                  "stdin": subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = NO_WINDOW
        child = subprocess.Popen(argv, **kwargs)
        if stdin_text is not None:
            try:
                child.stdin.write(stdin_text.encode("utf-8"))
                child.stdin.close()
            except OSError:
                pass
        return child

    def wake(self, work, first):
        client = self.cfg.get("client") or "claude"
        exe = find_agent_cli(client)
        # Creao has no CLI (exe is None) and needs only its key; every other helper needs the CLI just found.
        if not (agent_ready(client) if client == "creao" else exe):
            detail = ("Creao AI needs CREAO_API_KEY on this computer" if client == "creao"
                      else f"{client} isn't installed on this computer")
            self.write_status("missing", detail)
            return False
        prompt = wake_prompt(self.cfg.get("name") or "an AI helper", self.project or "your team's project",
                             [line for _, line in work], first)
        argv, stdin_text = agent_command(client, exe, self.folder, prompt, resume=bool(self.state.get("started")))
        self.log(f"waking the helper for {len(work)} new thing(s): {', '.join(k for k, _ in work) or 'first start'}")
        self.child = self.spawn(argv, stdin_text)
        self.child_started = self.clock()
        self.present = False  # the agent's MCP server takes the seat (a newer fence)
        self.runs.append(self.clock())
        self.seen |= {k for k, _ in work}
        self.state["started"] = True
        self.save_state()
        self.write_status("working", work[0][1][2:120] if work else "saying hello")
        return True

    def child_running(self):
        if self.child is None:
            return False
        if self.child.poll() is not None:
            code = self.child.returncode
            self.log(f"the helper finished (exit {code})")
            self.last_error = "" if code == 0 else (
                f"its last run stopped with an error (exit {code}). If it isn't signed in yet, open it once in a "
                "terminal and sign in. Details: the Tools tab ▸ Show my AI helper's log")
            self.child = None
            return False
        if self.clock() - self.child_started > RUN_TIMEOUT:
            self.log("the helper ran too long and was stopped")
            self.child.kill()
            self.child = None
            return False
        return True

    # ------------------------------------------------------------------------ loop

    def step(self):
        """One poll. Returns False when the runner should exit."""
        if os.path.exists(os.path.join(state_dir(self.folder), "runner.stop")):
            os.remove(os.path.join(state_dir(self.folder), "runner.stop"))
            if self.child is not None:
                self.child.kill()
            self.log("stopped from FreeCAD")
            return False
        if self.child_running():
            self.write_status()
            return True
        try:
            self.be_present()
            self.hub.identity["name"] = self.hub.me()["name"]  # a person may have renamed it
            if "primed" not in self.state:
                self.seen |= finished_keys(self.hub)
                self.state["primed"] = True
                self.save_state()
            work = pending_work(self.hub, self.seen)
        except HubRequestError as e:
            if e.code in GONE:
                self.log(f"this helper is no longer on the team ({e.code}); stopping")
                self.write_status("gone", str(e))
                return False
            self.present = False
            self.write_status("offline", "can't reach the team right now; retrying")
            return True
        first = not self.state.get("started")
        if work or first:
            ok, why = self.allowed()
            if ok:
                self.wake(work, first)
                return True
            if why:
                self.write_status("paused", why)
                return True
        self.write_status("error" if self.last_error else "listening", self.last_error)
        return True

    def run(self):
        if self.other_runner():
            return
        self.write_status("starting")
        self.log(f"runner started for {self.cfg.get('name')} ({self.cfg.get('client')})")
        while self.step():
            time.sleep(POLL_SECONDS)
        if os.path.exists(status_path(self.folder)):
            save_json(status_path(self.folder), {"pid": os.getpid(), "beat": 0, "state": "stopped", "detail": ""})


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        raise SystemExit("usage: cadswarm_launch.py runner <helper folder> [project name]")
    Runner(argv[0], argv[1] if len(argv) > 1 else "").run()


if __name__ == "__main__":
    main()
