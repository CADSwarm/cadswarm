"""Hosting a team from a normal person's computer: no terminal, no Python install.

The FreeCAD setup wizard uses these to (1) download the server's packages once
into a private folder with FreeCAD's own pip, (2) start the team server as a
quiet background process that survives closing FreeCAD, and (3) check on it.
Stdlib only.
"""
import json
import os
import signal
import socket
import subprocess
import time
import urllib.request

from .paths import DEPENDENCIES, LAUNCHER, deps_dir, project_layout

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


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))  # UDP connect sends nothing; it just picks the outbound interface
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def free_port(start=8765, tries=50):
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Couldn't find a free network port on this computer.")


def health(url, timeout=2.0):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as r:
            return json.loads(r.read()).get("ok") is True
    except Exception:
        return False


def start_hub(python, data_dir, token, port):
    """Start the team server in the background (no console window). Returns its PID."""
    lay = project_layout(data_dir)
    os.makedirs(lay["internal"], exist_ok=True)
    log = open(lay["log"], "a", encoding="utf-8")
    env = dict(os.environ, CADSWARM_FREECAD_PYTHON=python, PYTHONIOENCODING="utf-8")
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # keep running after FreeCAD closes
    proc = subprocess.Popen([python, LAUNCHER, "hub", "--data", data_dir, "--token", token,
                             "--port", str(port), "--host", "0.0.0.0"],
                            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, **kwargs)
    return proc.pid


def wait_healthy(url, timeout=90, pid=None, data_dir=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if health(url):
            return True
        if pid is not None and not pid_alive(pid):
            raise RuntimeError("The team server stopped while starting." + log_tail(data_dir))
        time.sleep(0.7)
    raise RuntimeError("The team server is taking too long to start." + log_tail(data_dir))


def log_tail(data_dir, lines=12):
    try:
        with open(project_layout(data_dir)["log"], encoding="utf-8", errors="replace") as f:
            return "\n\nDetails:\n" + "".join(f.readlines()[-lines:])
    except (OSError, TypeError):
        return ""


def pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"], capture_output=True, text=True,
                             creationflags=NO_WINDOW).stdout
        return str(pid) in out
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def stop_hub(pid):
    """Stop the team server. Its FreeCAD worker exits by itself when the server's pipe closes."""
    if not pid_alive(pid):
        return False
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    else:
        os.kill(int(pid), signal.SIGTERM)
    return True
