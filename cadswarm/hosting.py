"""Hosting a team from a normal person's computer: no terminal, no Python install.

The FreeCAD setup guide uses these to (1) download the agent packages once into a private
folder with FreeCAD's own pip, (2) create the team's SpacetimeDB database and start
`cadswarm-host` as a quiet background process that survives closing FreeCAD, and (3) check
on it. Stdlib only.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import time

from .paths import DEPENDENCIES, LAUNCHER, ROOT, deps_dir, project_layout

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOCAL_URI = "http://127.0.0.1:3000"
LOCAL_PORT = 3000
MAINCLOUD_URI = "https://maincloud.spacetimedb.com"
# Only ever stopped or reported on when the command line names one of these (never a blanket kill).
HOST_PROCESS_NAMES = ("cadswarm-host", "spacetime")

PROJECT_README = """This folder is a CadSwarm team project.

  model.FCStd   the shared 3D model everyone's AI helpers are building (open a copy in FreeCAD any time)
  files/        notes, plans and scripts the team saved; subfolders appear as the project grows
  exports/      3D files (STEP/STL) saved with "Save as 3D file"; never overwritten
  snapshots/    saved versions of the model
  AI helpers/   workspace folders for the AI helpers on this computer (they hold sign-ins: don't share)
  .cadswarm-team/  the team server's own data and older versions of files (the last 20 of each). Don't edit it.

Please don't edit model.FCStd directly while the team is working: your changes would be lost
the next time an AI helper changes the model. Copy it first, or work through CadSwarm.
"""


def deps_ready(python):
    try:
        r = subprocess.run([python, LAUNCHER, "check"], capture_output=True, text=True, timeout=120,
                           creationflags=NO_WINDOW)
        return r.returncode == 0 and r.stdout.strip().endswith("ok")
    except (OSError, subprocess.TimeoutExpired):
        return False


def install_deps(python, on_line=None):
    """pip-install the agent packages into deps_dir() with FreeCAD's own Python. Raises on failure."""
    target = deps_dir()
    os.makedirs(target, exist_ok=True)
    cmd = [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade",
           "--target", target, *DEPENDENCIES]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-15:]
        if on_line and line:
            on_line(line)
    if proc.wait() != 0:
        text = "\n".join(tail)
        hint = ("It looks like this computer is offline." if "connection" in text.lower() or "resolve" in text.lower()
                else "The download didn't finish.")
        raise RuntimeError(f"{hint} Check your internet connection and try again.\n\nDetails:\n{text}")
    if not deps_ready(python):
        raise RuntimeError("The helper software downloaded but won't start. Try again; if it keeps failing, "
                           f"delete the folder {target} and retry.")


def local_ips():
    """This computer's IPv4 addresses a teammate might reach, the default-route one (`lan_ip()`) first.

    Skips loopback and link-local (169.254.x.x) addresses.
    """
    found = [lan_ip()]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in found:
        if ip not in out and not ip.startswith(("127.", "169.254.")):
            out.append(ip)
    return out or [lan_ip()]


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))  # UDP connect sends nothing; it just picks the outbound interface
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def free_port(start=8765, tries=50):
    for port in range(start, start + tries):
        # On Windows a plain wildcard bind succeeds even while something listens on 127.0.0.1:port,
        # so ask for exclusive use there (a connect probe is unreliable on loopback).
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if os.name == "nt":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Couldn't find a free network port on this computer.")


def port_open(port, host="127.0.0.1", timeout=0.5):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, int(port))) == 0


# ------------------------------------------------------------------- programs

def find_tool(name, env):
    """Absolute path of `name`, or None: the `env` variable, then PATH, then the usual install folders."""
    explicit = (os.environ.get(env) or "").strip() if env else ""
    if explicit and os.path.isfile(explicit):
        return explicit
    found = shutil.which(name)
    if found:
        return found
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", "")
    dirs = [os.path.join(local, "SpacetimeDB"), os.path.join(local, "SpacetimeDB", "bin", "current"),
            os.path.join(home, ".cargo", "bin"), os.path.join(ROOT, "host", "target", "release"),
            "/usr/local/bin", os.path.join(home, ".local", "bin")]
    exts = ("", ".exe") if os.name == "nt" else ("",)
    for d in filter(None, dirs):
        for ext in exts:
            cand = os.path.join(d, name + ext)
            if os.path.isfile(cand):
                return cand
    return None


def host_bin():
    return find_tool("cadswarm-host", "CADSWARM_HOST_BIN")


def spacetime_bin():
    return find_tool("spacetime", "SPACETIME_BIN")


def module_wasm():
    """The built module to publish: the environment's, else this checkout's wasm build."""
    explicit = os.environ.get("CADSWARM_MODULE_WASM")
    if explicit and os.path.isfile(explicit):
        return explicit
    built = os.path.join(ROOT, "module", "target", "wasm32-unknown-unknown", "release", "cadswarm_module.wasm")
    return built if os.path.isfile(built) else None


def host_env():
    """What `cadswarm-host` and `spacetime` run with: this checkout on PYTHONPATH (inside FreeCAD the
    package lives in the add-on folder, not site-packages) and the CLI's folder on PATH."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (ROOT, env.get("PYTHONPATH")) if p)
    st = spacetime_bin()
    if st:
        env["PATH"] = os.path.dirname(st) + os.pathsep + env.get("PATH", "")
    return env


def host_config(data_dir):
    """The host's own settings in <data_dir>/host.json ({} when this folder has never hosted)."""
    try:
        with open(os.path.join(data_dir, "host.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _details(r):
    text = ((r.stderr or "") + (r.stdout or "")).strip()
    tail = [ln for ln in text.splitlines() if ln.strip()][-12:]
    return ("\n\nDetails:\n" + "\n".join(tail)) if tail else ""


def write_project_readme(root):
    readme = os.path.join(root, "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(PROJECT_README)


def publish(db, mode="maincloud"):
    """Create or update the team's database with this build's module. Raises RuntimeError in plain words."""
    st = spacetime_bin()
    if not st:
        raise RuntimeError("CadSwarm needs the SpacetimeDB program to run your team's database, and this "
                           "computer doesn't have it yet. Install it from https://spacetimedb.com/install, "
                           "then click “Try again”.")
    wasm = module_wasm()
    # Re-publishing an existing team (Reconnect) must never touch its data. --delete-data=never
    # makes that explicit, whatever the CLI's default is.
    args = [st, "publish", "--server", "local" if mode == "local" else "maincloud", "--yes=all",
            "--delete-data=never"]
    args += ["--bin-path", wasm] if wasm else ["--module-path", os.path.join(ROOT, "module")]
    args.append(db)
    try:
        r = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace", timeout=300,
                           creationflags=NO_WINDOW, env=host_env())
    except subprocess.TimeoutExpired:
        raise RuntimeError("Creating your team's database took too long. Check your internet connection and "
                           "try again.")
    if r.returncode != 0:
        raise RuntimeError("Couldn't create your team's database on SpacetimeDB. If this computer hasn't "
                           "signed in there yet, run `spacetime login` once in a terminal." + _details(r))


def _spawn(argv, lay, log_name, env=None):
    """Start `argv` quietly in the background, appending its output to <internal>/<log_name>. Returns its PID."""
    log = open(os.path.join(lay["internal"], log_name), "a", encoding="utf-8")
    kwargs = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL,
              "env": host_env() if env is None else env, "cwd": lay["root"]}
    if os.name == "nt":
        kwargs["creationflags"] = NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # keep running after FreeCAD closes
    return subprocess.Popen(argv, **kwargs).pid


def _wait_port(port, timeout=30, pid=None, data_dir=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            return True
        if pid is not None and not pid_alive(pid):
            raise RuntimeError("The local database server stopped while starting." + log_tail(data_dir))
        time.sleep(0.3)
    raise RuntimeError("The local database server is taking too long to start." + log_tail(data_dir))


def start_team(python, data_dir, mode="maincloud", db=None, web_url="", agent="claude", on_log=None):
    """Publish the team's database and start its host in the background.

    Returns {"host_pid", "spacetime_pid", "uri", "db", "mode"}. `db` must already be chosen; `init`
    runs only the first time this folder hosts. Raises RuntimeError with plain-language messages.
    """
    if mode not in ("local", "maincloud"):
        raise ValueError("mode must be 'local' or 'maincloud'")
    if not db:
        raise ValueError("a database name is required")
    host = host_bin()
    if not host:
        raise RuntimeError("CadSwarm needs its team server program (cadswarm-host), and this computer "
                           "doesn't have it yet. Install the CadSwarm build for your platform, then click "
                           "“Try again”.")
    lay = project_layout(data_dir)
    for key in ("files", "exports", "snapshots", "internal"):
        os.makedirs(lay[key], exist_ok=True)
    write_project_readme(lay["root"])
    log = on_log or (lambda _line: None)
    env = host_env()
    info = {"host_pid": None, "spacetime_pid": None, "db": db, "mode": mode,
            "uri": LOCAL_URI if mode == "local" else MAINCLOUD_URI}
    try:
        if mode == "local" and not port_open(LOCAL_PORT):
            st = spacetime_bin()
            if not st:
                raise RuntimeError("CadSwarm needs the SpacetimeDB program for a local team, and this computer "
                                   "doesn't have it yet. Install it from https://spacetimedb.com/install, then "
                                   "click “Try again”.")
            log(f"Starting the local database on port {LOCAL_PORT}")
            info["spacetime_pid"] = _spawn([st, "start", "--listen-addr", f"127.0.0.1:{LOCAL_PORT}"],
                                           lay, "spacetime.log", env)
            _wait_port(LOCAL_PORT, 30, info["spacetime_pid"], data_dir)
        log(f"Creating your team's database “{db}” ({mode})")
        publish(db, mode)
        if not host_config(data_dir):
            log("Setting up the team server (one time)")
            argv = [host, "--data", data_dir, "init", "--mode", mode, "--db", db, "--python", python,
                    "--agent", agent]
            if web_url:
                argv += ["--web-url", web_url]
            try:
                with open(os.path.join(lay["internal"], "host.log"), "a", encoding="utf-8") as out:
                    r = subprocess.run(argv, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       env=env, cwd=lay["root"], timeout=300, creationflags=NO_WINDOW)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Setting up the team server took too long." + log_tail(data_dir))
            if r.returncode != 0:
                raise RuntimeError("Couldn't set up the team server." + log_tail(data_dir))
        log("Starting the team server")
        info["host_pid"] = _spawn([host, "--data", data_dir, "run"], lay, "host.log", env)
    except Exception:
        # Never leave a database server we started running on its own (the guide's "Try again"
        # would start a second one). Only processes we started are in `info`.
        stop_team(info)
        raise
    return info


def mint_invite(data_dir, kind="human", role="member"):
    """Ask the host on this computer for a fresh single-use invite. Returns the decoded ticket."""
    host = host_bin()
    if not host:
        raise RuntimeError("CadSwarm's team server program is missing, so it can't make an invite code.")
    lay = project_layout(data_dir)
    try:
        r = subprocess.run([host, "--data", data_dir, "invite", "--kind", kind, "--role", role],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=120,
                           creationflags=NO_WINDOW, env=host_env(), cwd=lay["root"])
    except subprocess.TimeoutExpired:
        raise RuntimeError("Making an invite code took too long. Try again.")
    if r.returncode != 0:
        raise RuntimeError("Couldn't make an invite code." + _details(r))
    from . import ticket
    for line in (r.stdout or "").splitlines():
        if line.startswith("ticket:"):
            return ticket.decode(line.split(":", 1)[1].strip())
    raise RuntimeError("The team server didn't print an invite code." + _details(r))


# ------------------------------------------------------------------- checking

def host_status(data_dir):
    """The status file the running host writes (plan 02 Task 5), or {} when it hasn't."""
    try:
        with open(os.path.join(project_layout(data_dir)["internal"], "host-status.json"), encoding="utf-8") as f:
            status = json.load(f)
    except (OSError, ValueError, TypeError):
        return {}
    return status if isinstance(status, dict) else {}


def team_ready(data_dir):
    """True once the host wrote host-status.json and its process is still alive."""
    status = host_status(data_dir)
    return status.get("ready") is True and pid_alive(status.get("pid"))


def wait_ready(data_dir, timeout=90, pids=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if team_ready(data_dir):
            return True
        for pid in pids or []:
            if pid and not pid_alive(pid):
                raise RuntimeError("The team server stopped while starting." + log_tail(data_dir))
        time.sleep(0.7)
    raise RuntimeError("The team server is taking too long to start." + log_tail(data_dir))


def log_tail(data_dir, lines=12):
    try:
        with open(os.path.join(project_layout(data_dir)["internal"], "host.log"), encoding="utf-8",
                  errors="replace") as f:
            return "\n\nDetails:\n" + "".join(f.readlines()[-lines:])
    except (OSError, TypeError):
        return ""


def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True,
                             text=True, creationflags=NO_WINDOW).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _command_line(pid):
    """The process's command line, or "" if unknown. PowerShell's CIM query because wmic is gone on new Windows."""
    try:
        if os.name == "nt":
            ps = f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]
        else:
            cmd = ["ps", "-p", str(int(pid)), "-o", "command="]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""


def is_our_process(pid, names=HOST_PROCESS_NAMES):
    """True only if `pid` is one of our programs. A saved PID can be reused by an unrelated program
    after a reboot, and we must never kill that."""
    return pid_alive(pid) and any(n in _command_line(pid).lower() for n in names)


def _kill(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    else:
        os.kill(int(pid), signal.SIGTERM)


def stop_team(info):
    """Stop the host and (in local mode) the database server the guide started. Never a blanket kill."""
    stopped = False
    for key, names in (("host_pid", ("cadswarm-host",)), ("spacetime_pid", ("spacetime",))):
        pid = (info or {}).get(key)
        if pid and is_our_process(pid, names):
            _kill(int(pid))
            stopped = True
    return stopped


def pid_on_port(port):
    """PID of the SpacetimeDB server listening on `port`, or None. For a server we didn't just start."""
    try:
        port = int(port)
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20,
                                 creationflags=NO_WINDOW).stdout
            pids = [int(cols[-1]) for cols in (ln.split() for ln in out.splitlines())
                    if len(cols) >= 5 and cols[3] == "LISTENING" and cols[1].rsplit(":", 1)[-1] == str(port)
                    and cols[-1].isdigit()]
        else:
            out = subprocess.run(["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True,
                                 timeout=20).stdout
            pids = [int(x) for x in out.split() if x.isdigit()]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return next((p for p in pids if is_our_process(p, ("spacetime",))), None)
