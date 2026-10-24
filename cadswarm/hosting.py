"""Hosting a team from a normal person's computer: no terminal, no Python install.

The FreeCAD setup guide uses these to (1) download the agent packages once into a private
folder with FreeCAD's own pip, (2) download the team server, database program and module once
into the app folder, (3) create the team's SpacetimeDB database and start `cadswarm-host` as a
quiet background process that survives closing FreeCAD, and (4) check on it. Stdlib only.
"""
import hashlib
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

from .paths import DEPENDENCIES, LAUNCHER, ROOT, app_dir, deps_dir, project_layout

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOCAL_URI = "http://127.0.0.1:3000"
LOCAL_PORT = 3000
MAINCLOUD_URI = "https://maincloud.spacetimedb.com"
# Only ever stopped or reported on when the command line names one of these (never a blanket kill).
HOST_PROCESS_NAMES = ("cadswarm-host", "spacetime")

# Download-on-first-use. `CADSWARM_RELEASE_BASE` points at a folder or server during development.
RELEASE_BASE = "https://github.com/SachchitBalamurugan/cadswarm/releases/latest/download"
RELEASE_ASSETS = {
    "windows-x86_64": "cadswarm-host-windows-x86_64.exe",
    "linux-x86_64": "cadswarm-host-linux-x86_64",
    "linux-aarch64": "cadswarm-host-linux-aarch64",
    "macos-x86_64": "cadswarm-host-macos-x86_64",
    "macos-aarch64": "cadswarm-host-macos-aarch64",
}
MODULE_ASSET = "cadswarm_module.wasm"
SUMS_ASSET = "SHA256SUMS.txt"

# Pinned with the module and host (overview Global Constraints). Hash-verified from the release.
SPACETIME_VERSION = "2.13.0"
SPACETIME_BASE = f"https://github.com/clockworklabs/SpacetimeDB/releases/download/v{SPACETIME_VERSION}"
SPACETIME_ASSETS = {
    "windows-x86_64": ("spacetime-x86_64-pc-windows-msvc.zip",
                       "94e26e3e8782e88e2cb676920a7df2faa482724f83253c399cb9e59a5c04b4d3"),
    "linux-x86_64": ("spacetime-x86_64-unknown-linux-gnu.tar.gz",
                     "034fe43ebb42e8d291c6ba18b6b3a6ac9a3bb4aa548d37743340362cb021165e"),
    "linux-aarch64": ("spacetime-aarch64-unknown-linux-gnu.tar.gz",
                      "47e4ad61f3d5830cf75a0b5b2432f5d9c115ab4e37505f757d7555f3ad1fe8f0"),
    "macos-x86_64": ("spacetime-x86_64-apple-darwin.tar.gz",
                     "12268695592b1cd6a7384cc677dad2815a9504ed135a660df75abf7e81c9e6ab"),
    "macos-aarch64": ("spacetime-aarch64-apple-darwin.tar.gz",
                      "54bb70971b88821563a1839c0d908edb94ece169e0f3bedc8ffdf8e18083c316"),
}


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

def platform_key():
    """The OS/CPU name used in release assets, e.g. "windows-x86_64"."""
    if sys.platform.startswith("win"):
        return "windows-x86_64"  # SpacetimeDB ships x86_64; ARM Windows runs it under emulation
    machine = (platform.machine() or "").lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(machine)
    if not arch:
        raise RuntimeError(f"CadSwarm doesn't have a build for this computer ({machine or 'unknown CPU'}).")
    return ("macos-" if sys.platform == "darwin" else "linux-") + arch


def tools_dir():
    """Where downloaded programs live: the private app folder, never a project or FreeCAD itself."""
    return os.path.join(app_dir(), "bin")


def release_base():
    return (os.environ.get("CADSWARM_RELEASE_BASE") or RELEASE_BASE).rstrip("/")


def find_tool(name, env):
    """Absolute path of `name`, or None: the `env` variable, the app folder, PATH, then the usual
    install folders."""
    explicit = (os.environ.get(env) or "").strip() if env else ""
    if explicit and os.path.isfile(explicit):
        return explicit
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", "")
    dirs = [tools_dir(), os.path.join(local, "SpacetimeDB"), os.path.join(local, "SpacetimeDB", "bin", "current"),
            os.path.join(home, ".cargo", "bin"), os.path.join(ROOT, "host", "target", "release"),
            "/usr/local/bin", os.path.join(home, ".local", "bin")]
    exts = ("", ".exe") if os.name == "nt" else ("",)
    for d in filter(None, dirs):
        for ext in exts:
            cand = os.path.join(d, name + ext)
            if os.path.isfile(cand):
                return cand
    return shutil.which(name)


def host_bin():
    return find_tool("cadswarm-host", "CADSWARM_HOST_BIN")


def spacetime_bin():
    return find_tool("spacetime", "SPACETIME_BIN")


def module_wasm():
    """The module to publish: the environment's, the downloaded one, else this checkout's build."""
    explicit = os.environ.get("CADSWARM_MODULE_WASM")
    if explicit and os.path.isfile(explicit):
        return explicit
    built = os.path.join(ROOT, "module", "target", "wasm32-unknown-unknown", "release", "cadswarm_module.wasm")
    if os.path.isfile(built):
        return built
    downloaded = os.path.join(app_dir(), "module", MODULE_ASSET)
    return downloaded if os.path.isfile(downloaded) else None


def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _release_sums():
    """The checksums published with the release's CadSwarm assets."""
    url = f"{release_base()}/{SUMS_ASSET}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            text = r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError("Couldn't check the CadSwarm downloads. Check your internet connection and try "
                           f"again.\n\nDetails:\n{e}")
    sums = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def _download(url, dest, expect=None, on_line=None):
    """Download `url` to `dest` atomically, verifying sha256 when `expect` is given."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    digest, got, mark = hashlib.sha256(), 0, 0
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                if on_line and got - mark >= 8 * 1024 * 1024:
                    mark = got
                    on_line(f"Downloaded {got // (1024 * 1024)} MB…")
    except (urllib.error.URLError, OSError) as e:
        _unlink(part)
        raise RuntimeError(f"Couldn't download {os.path.basename(dest)}. Check your internet connection and "
                           f"try again.\n\nDetails:\n{e}")
    if expect and digest.hexdigest() != expect.lower():
        _unlink(part)
        raise RuntimeError(f"{os.path.basename(dest)} downloaded but was damaged, so it was thrown away. "
                           "Try again; if it keeps happening, download CadSwarm again.")
    os.replace(part, dest)
    if os.name != "nt":
        os.chmod(dest, 0o755)
    return dest


def _install_spacetime(archive):
    """Put the CLI and its standalone server from the official archive into the app folder."""
    wanted = {"spacetimedb-cli": "spacetime", "spacetimedb-standalone": "spacetimedb-standalone"}
    suffix = ".exe" if os.name == "nt" else ""
    os.makedirs(tools_dir(), exist_ok=True)
    found = {}

    def take(name, read):
        base = os.path.basename(name)
        stem = base[:-4] if base.lower().endswith(".exe") else base
        if stem not in wanted:
            return
        out = os.path.join(tools_dir(), wanted[stem] + suffix)
        with read() as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        found[stem] = out

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                take(member, lambda m=member: z.open(m))
    else:
        with tarfile.open(archive, "r:*") as t:
            for member in t.getmembers():
                if member.isfile():
                    take(member.name, lambda m=member: t.extractfile(m))
    if "spacetimedb-cli" not in found:
        raise RuntimeError("The downloaded database program is missing its main file. Try again.")
    if os.name != "nt":
        for path in found.values():
            os.chmod(path, 0o755)


def ensure_tools(on_line=None):
    """Download the team server, SpacetimeDB CLI and module once, into the private app folder.

    Does nothing when all three are already available (a developer checkout, a previous run, or
    the programs on PATH). Raises RuntimeError in plain words when a download cannot be finished.
    """
    log = on_line or (lambda _line: None)
    key = platform_key()
    sums = None
    if not host_bin():
        asset = RELEASE_ASSETS.get(key)
        if not asset:
            raise RuntimeError(f"CadSwarm doesn't have a team server for this computer ({key}) yet.")
        sums = _release_sums()
        if asset not in sums:
            raise RuntimeError(f"The CadSwarm release is missing the checksum for {asset}. Try again later.")
        log("Downloading the team server")
        _download(f"{release_base()}/{asset}", os.path.join(tools_dir(), "cadswarm-host" +
                                                           (".exe" if os.name == "nt" else "")),
                  sums.get(asset), on_line=log)
    if not spacetime_bin():
        entry = SPACETIME_ASSETS.get(key)
        if not entry:
            raise RuntimeError(f"CadSwarm doesn't have a database program for this computer ({key}) yet.")
        name, sha = entry
        log("Downloading the database program (about 50 MB, one time)")
        with tempfile.TemporaryDirectory(prefix="cadswarm-") as tmp:
            archive = os.path.join(tmp, name)
            _download(f"{SPACETIME_BASE}/{name}", archive, sha, on_line=log)
            _install_spacetime(archive)
    if not module_wasm():
        sums = sums or _release_sums()
        if MODULE_ASSET not in sums:
            raise RuntimeError(f"The CadSwarm release is missing the checksum for {MODULE_ASSET}. Try again later.")
        log("Downloading the team's database module")
        _download(f"{release_base()}/{MODULE_ASSET}", os.path.join(app_dir(), "module", MODULE_ASSET),
                  sums.get(MODULE_ASSET), on_line=log)
    return {"host": host_bin(), "spacetime": spacetime_bin(), "wasm": module_wasm()}



def host_env():
    """What `cadswarm-host` and `spacetime` run with: this checkout on PYTHONPATH (inside FreeCAD the
    package lives in the add-on folder, not site-packages) and the CLI's folder on PATH."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    # A FreeCAD AppImage exports PYTHONHOME for its own Python; passed on, it breaks every other Python the host
    # starts (agent CLIs' tools, a system Python). FreeCAD's bundled Python finds its home without it.
    env.pop("PYTHONHOME", None)
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
    log = on_log or (lambda _line: None)
    ensure_tools(log)  # one-time downloads into the app folder; no-ops when already available
    host = host_bin()
    if not host:
        raise RuntimeError("CadSwarm needs its team server program (cadswarm-host), and it isn't available "
                           "on this computer. Check your internet connection and click “Try again”.")
    lay = project_layout(data_dir)
    for key in ("files", "exports", "snapshots", "internal"):
        os.makedirs(lay[key], exist_ok=True)
    write_project_readme(lay["root"])
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
            cmd = ["ps", "-ww", "-p", str(int(pid)), "-o", "command="]  # -ww: never cut at the terminal width
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
