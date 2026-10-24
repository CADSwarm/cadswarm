"""Hosting a team from a normal person's computer: no terminal, no Python install.

The FreeCAD setup wizard uses these to (1) download the agents' packages once
into a private folder with FreeCAD's own pip, (2) start the team: SpacetimeDB on
this computer (local mode) or Maincloud, the CadSwarm module, and `cadswarm-host`
as quiet background processes that survive closing FreeCAD, and (3) check on it.
Stdlib only.
"""
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

from .paths import DEPENDENCIES, LAUNCHER, ROOT, app_dir, deps_dir, project_layout

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def deps_ready(python):
    try:
        r = subprocess.run([python, LAUNCHER, "check"], capture_output=True, text=True, timeout=120,
                           creationflags=NO_WINDOW)
        return r.returncode == 0 and r.stdout.strip().endswith("ok")
    except (OSError, subprocess.TimeoutExpired):
        return False


def install_deps(python, on_line=None):
    """pip-install the server packages into deps_dir() with FreeCAD's own Python. Raises on failure."""
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


LOCAL_PORT = 3000
MAINCLOUD_URI = "https://maincloud.spacetimedb.com"
TOOL_ENV = {"cadswarm-host": "CADSWARM_HOST_BIN", "spacetime": "SPACETIME_BIN"}


def find_tool(name, env=None):
    """Path of `cadswarm-host` or `spacetime`: the env variable, then PATH, then the usual install folders."""
    env = env if env is not None else TOOL_ENV.get(name)
    if env and os.environ.get(env) and os.path.isfile(os.environ[env]):
        return os.environ[env]
    exe = name + (".exe" if os.name == "nt" else "")
    if shutil.which(name):
        return shutil.which(name)
    home = os.path.expanduser("~")
    folders = {"spacetime": [os.path.join(os.environ.get("LOCALAPPDATA", ""), "SpacetimeDB"),
                             os.path.join(home, ".local", "bin")],
               "cadswarm-host": [os.path.join(home, ".cargo", "bin"),
                                 os.path.join(ROOT, "host", "target", "release"),
                                 os.path.join(app_dir(), "bin")]}.get(name, [])
    return next((os.path.join(d, exe) for d in folders if os.path.isfile(os.path.join(d, exe))), None)


def module_wasm():
    """The SpacetimeDB module to publish: CADSWARM_MODULE_WASM, else the one built in this checkout."""
    for path in (os.environ.get("CADSWARM_MODULE_WASM"),
                 os.path.join(ROOT, "module", "cadswarm_module.wasm"),  # copied there by the installers
                 os.path.join(ROOT, "module", "target", "wasm32-unknown-unknown", "release", "cadswarm_module.wasm")):
        if path and os.path.isfile(path):
            return path
    return None


def db_name(project):
    """A fresh database name for a team: cadswarm-<slug>-<4 hex>."""
    slug = re.sub(r"[^a-z0-9]+", "-", (project or "team").lower()).strip("-")[:24] or "team"
    return f"cadswarm-{slug}-{secrets.token_hex(2)}"


def space_up(uri, timeout=1.5):
    try:
        with urllib.request.urlopen(uri.rstrip("/") + "/v1/ping", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def clean_env(extra=None):
    """Our environment minus what FreeCAD's bundle set for its own Python (an AppImage exports PYTHONHOME), which
    would break any other Python the team server starts. FreeCAD's bundled Python doesn't need it."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
    env.update(extra or {})
    return env


def _background(cmd, log_path, env=None):
    log = open(log_path, "a", encoding="utf-8")
    kwargs = {"creationflags": NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" \
        else {"start_new_session": True}  # keep running after FreeCAD closes
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            env=clean_env(env), **kwargs).pid


def start_spacetime(data_dir, port=LOCAL_PORT):
    """`spacetime start` on this computer (all network addresses, so teammates on the LAN can join)."""
    tool = find_tool("spacetime")
    if not tool:
        raise RuntimeError("SpacetimeDB isn't installed on this computer. Install it from https://spacetimedb.com/install "
                           "(or set SPACETIME_BIN), then try again.")
    lay = project_layout(data_dir)
    pid = _background([tool, "start", "--listen-addr", f"0.0.0.0:{port}", "--non-interactive"],
                      os.path.join(lay["internal"], "spacetime.log"))
    uri = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while not space_up(uri):
        if not pid_alive(pid) or time.time() > deadline:
            raise RuntimeError("SpacetimeDB didn't start." + log_tail(data_dir, name="spacetime.log"))
        time.sleep(0.5)
    return pid


def _http(method, url, data=None, token=None, content_type=None, timeout=120):
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"SpacetimeDB refused ({e.code}): {e.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Couldn't reach SpacetimeDB at {url}: {e.reason}")


def host_token(uri, data_dir, mode):
    """The host's SpacetimeDB identity: kept in host.json; a new local one is issued by the server itself."""
    try:
        with open(os.path.join(data_dir, "host.json"), encoding="utf-8") as f:
            return json.load(f)["token"]
    except (OSError, ValueError, KeyError):
        pass
    if os.environ.get("CADSWARM_HOST_TOKEN"):
        return os.environ["CADSWARM_HOST_TOKEN"]
    if mode == "local":
        return json.loads(_http("POST", uri.rstrip("/") + "/v1/identity"))["token"]
    tool = find_tool("spacetime")
    out = subprocess.run([tool, "login", "show", "--token"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout if tool else ""
    token = next((w for w in out.split() if w.count(".") == 2 and len(w) > 40), None)
    if not token:
        raise RuntimeError("To host over the internet, sign in to SpacetimeDB first: run `spacetime login` in a "
                           "terminal, then try again.")
    return token


def publish_module(uri, db, token, wasm=None):
    """Create or update the team's database with the CadSwarm module (the publisher becomes the team's host)."""
    wasm = wasm or module_wasm()
    if not wasm:
        raise RuntimeError("The CadSwarm SpacetimeDB module isn't built. Build it with "
                           "`cargo build --release --target wasm32-unknown-unknown` in module/, or set CADSWARM_MODULE_WASM.")
    with open(wasm, "rb") as f:
        return _http("PUT", f"{uri.rstrip('/')}/v1/database/{db}", f.read(), token, "application/wasm")


def start_team(python, data_dir, mode="local", db=None, port=LOCAL_PORT):
    """Start the team: SpacetimeDB (local mode), the module, and `cadswarm-host` in the background.

    Returns {"host_pid", "spacetime_pid", "db", "uri", "mode"}. A project folder that already has host.json
    keeps its database and host identity, so helpers already connected to it keep working.
    """
    host = find_tool("cadswarm-host")
    if not host:
        raise RuntimeError("The CadSwarm team server (cadswarm-host) isn't installed on this computer. Build it "
                           "with `cargo build --release` in host/ (or set CADSWARM_HOST_BIN), then try again.")
    lay = project_layout(data_dir)
    os.makedirs(lay["internal"], exist_ok=True)
    running = running_team(data_dir)
    if running:
        return running  # never a second host on one project: both would run every op
    try:
        with open(os.path.join(data_dir, "host.json"), encoding="utf-8") as f:
            existing = json.load(f)
        mode, db = existing.get("mode", mode), existing["db"]
    except (OSError, ValueError, KeyError):
        existing = None
    db = db or db_name(os.path.basename(os.path.normpath(data_dir)))
    uri = f"http://127.0.0.1:{port}" if mode == "local" else MAINCLOUD_URI
    spacetime_pid = None
    if mode == "local" and not space_up(uri):
        spacetime_pid = start_spacetime(data_dir, port)
    token = host_token(uri, data_dir, mode)
    publish_module(uri, db, token)
    env = {"CADSWARM_ROOT": ROOT, "CADSWARM_HOST_TOKEN": token, "PYTHONIOENCODING": "utf-8"}
    if not existing:
        r = subprocess.run([host, "--data", data_dir, "init", "--mode", mode, "--db", db, "--python", python],
                           capture_output=True, text=True, env=clean_env(env), creationflags=NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError("Setting up the team server failed:\n" + (r.stderr or r.stdout)[-800:])
    try:
        os.remove(os.path.join(lay["internal"], "host-status.json"))  # a stale one must not look ready
    except OSError:
        pass
    host_pid = _background([host, "--data", data_dir, "run"], os.path.join(lay["internal"], "host.log"), env)
    return {"host_pid": host_pid, "spacetime_pid": spacetime_pid, "db": db, "uri": uri, "mode": mode}


def running_team(data_dir):
    """The info for a cadswarm-host already running this project (from its status file), else None."""
    try:
        with open(os.path.join(project_layout(data_dir)["internal"], "host-status.json"), encoding="utf-8") as f:
            st = json.load(f)
        with open(os.path.join(data_dir, "host.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    if not (st.get("ready") and is_our_process(st.get("pid"), ("cadswarm-host",))):
        return None
    return {"host_pid": st["pid"], "spacetime_pid": None, "db": cfg.get("db"), "uri": cfg.get("uri"),
            "mode": cfg.get("mode", "local")}


def team_ready(data_dir, pid=None):
    """True once cadswarm-host wrote host-status.json (ready, its pid alive)."""
    try:
        with open(os.path.join(project_layout(data_dir)["internal"], "host-status.json"), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError, TypeError):
        return False
    return bool(st.get("ready")) and pid_alive(st.get("pid")) and (pid is None or int(st.get("pid")) == int(pid))


def wait_ready(data_dir, timeout=120, pids=None):
    pids = pids or {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        if team_ready(data_dir, pids.get("host_pid")):
            return True
        if pids.get("host_pid") and not pid_alive(pids["host_pid"]):
            raise RuntimeError("The team server stopped while starting." + log_tail(data_dir, name="host.log"))
        time.sleep(0.7)
    raise RuntimeError("The team server is taking too long to start." + log_tail(data_dir, name="host.log"))


def log_tail(data_dir, lines=12, name=None):
    try:
        lay = project_layout(data_dir)
        with open(os.path.join(lay["internal"], name) if name else lay["log"], encoding="utf-8", errors="replace") as f:
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
            cmd = ["ps", "-ww", "-p", str(int(pid)), "-o", "command="]  # -ww: never cut at the terminal width
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""


def is_our_process(pid, names=("cadswarm-host", "spacetime")):
    """True only if `pid` runs one of our programs. A saved PID can be reused by an unrelated program after a
    reboot, and we must never kill that."""
    cmd = _command_line(pid) if pid_alive(pid) else ""
    return any(n in cmd for n in names)


def stop_team(info):
    """Stop the team server (and the SpacetimeDB this computer started for it). Never a blanket kill."""
    stopped = False
    for key in ("host_pid", "spacetime_pid"):
        pid = (info or {}).get(key)
        if not pid or not is_our_process(pid):
            continue
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        else:
            os.kill(int(pid), signal.SIGTERM)
        stopped = True
    return stopped


def pid_on_port(port):
    """PID of the SpacetimeDB listening on `port`, or None."""
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
