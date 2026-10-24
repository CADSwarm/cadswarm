"""Out-of-process sandbox for agent part scripts.

The worker never runs agent code itself. For each run_part_script it saves a snapshot copy of the live
document into a fresh per-run folder and starts FreeCAD's python on `script_runner.py` as a confined
child: the child opens the copy, runs the script, and writes back one JSON reply (the BREP of `shape`
and any `result`). Whatever the script does to its copy of the document dies with the folder.

Isolation levels, strongest first (the active one is reported in the worker's ping):
  appcontainer    Windows AppContainer with no capabilities: no network, no access to the user's files
                  except FreeCAD itself (read) and a per-run scratch folder (write). Plus a Job object.
  lowintegrity    Windows low-integrity token: can't WRITE outside the scratch folder, but can still read
                  the user's files and use the network. Plus a Job object. Fallback when AppContainers
                  are unavailable.
  process-limits  macOS/Linux: plain child process with rlimits (memory, CPU, no forking), new session,
                  working in the scratch folder. No filesystem or network isolation: much weaker.
  off             CADSWARM_SCRIPT_SANDBOX=off, debugging only: a plain child process, no confinement.

Every level has a wall-clock timeout; the child is killed, never the worker. Settings (env):
  CADSWARM_SCRIPT_TIMEOUT (seconds, 60)  CADSWARM_SCRIPT_MEMORY_MB (2048)  CADSWARM_SCRIPT_SANDBOX=off
  CADSWARM_SANDBOX_LEVEL (appcontainer|lowintegrity: restrict the levels tried; for tests)
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

log = logging.getLogger("cadswarm.sandbox")

RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "script_runner.py")
DEFAULT_TIMEOUT = 60
DEFAULT_MEMORY_MB = 2048
WINDOWS = sys.platform == "win32"


class SandboxError(Exception):
    """A run that produced no script verdict. `code` is an OpError code for the worker's reply."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _env_number(name, default):
    try:
        value = float(os.environ.get(name, ""))
        return value if value > 0 else default
    except ValueError:
        return default


def limits():
    return _env_number("CADSWARM_SCRIPT_TIMEOUT", DEFAULT_TIMEOUT), int(
        _env_number("CADSWARM_SCRIPT_MEMORY_MB", DEFAULT_MEMORY_MB))


def disabled():
    return os.environ.get("CADSWARM_SCRIPT_SANDBOX", "").lower() == "off"


# --------------------------------------------------------------------------
# Which level is active (decided once, in the background, when the worker starts)
# --------------------------------------------------------------------------

_state = {"levels": None, "detail": "", "container_sid": None}
_lock = threading.Lock()


def install_root(python):
    """FreeCAD's install folder: …/FreeCAD/bin/python.exe -> …/FreeCAD."""
    bin_dir = os.path.dirname(os.path.abspath(python))
    return os.path.dirname(bin_dir) if os.path.basename(bin_dir).lower() == "bin" else bin_dir


def _ensure_container_grants(win, sid, root):
    """Read+execute on FreeCAD for the container, granted once (a per-user install such as
    %LOCALAPPDATA%\Programs\FreeCAD doesn't allow AppContainers by default; Program Files does)."""
    if win.has_access(root, sid):
        return
    log.info("granting the script sandbox read access to %s (one time, can take a few seconds)", root)
    win.grant(root, sid, "RX")


def _win():
    try:
        from . import sandbox_win
    except ImportError:  # the worker runs as a plain script with its own folder on sys.path
        import sandbox_win
    return sandbox_win


def _probe(python):
    """Work out which levels can run here, strongest first."""
    if disabled():
        log.warning("CADSWARM_SCRIPT_SANDBOX=off: part scripts run in an UNCONFINED child process "
                    "(debugging only; do not use with untrusted teammates or agents)")
        return ["off"], "sandbox disabled by CADSWARM_SCRIPT_SANDBOX=off"
    if not WINDOWS:
        log.warning("script sandbox: process limits only (no filesystem/network isolation on this OS); "
                    "part scripts run as the current user, so use only with trusted teammates")
        return ["process-limits"], "rlimits only: no filesystem or network isolation on this OS"
    win = _win()
    levels, notes = [], []
    try:
        sid = win.container_sid_string()
        _ensure_container_grants(win, sid, install_root(python))
        _state["container_sid"] = sid
        levels.append("appcontainer")
    except Exception as e:  # old Windows, policy, no icacls…
        notes.append(f"AppContainer unavailable ({e})")
        log.warning("script sandbox: AppContainer unavailable (%s); falling back to low integrity", e)
    levels.append("lowintegrity")  # a failure to start is caught per run
    want = os.environ.get("CADSWARM_SANDBOX_LEVEL", "")
    if want in levels:
        levels = [want]
    if levels[0] == "lowintegrity":
        log.warning("script sandbox: low-integrity level: scripts cannot write outside their scratch folder "
                    "but CAN still read your files and use the network; use only with trusted teammates")
    return levels, "; ".join(notes)


def prepare(python):
    """Decide the isolation level (idempotent; the worker calls it in a thread at start-up)."""
    with _lock:
        if _state["levels"] is None:
            try:
                _state["levels"], _state["detail"] = _probe(python)
            except Exception as e:
                log.exception("script sandbox probe failed")
                _state["levels"], _state["detail"] = [], f"probe failed: {e}"
            log.info("script sandbox level: %s", (_state["levels"] or ["unavailable"])[0])


def info(python):
    """{level, detail, timeout_s, memory_mb} for the worker's ping; waits for the start-up probe."""
    prepare(python)
    timeout, mem = limits()
    levels = _state["levels"] or []
    return {"level": levels[0] if levels else "unavailable", "detail": _state["detail"],
            "timeout_s": timeout, "memory_mb": mem}


# --------------------------------------------------------------------------
# One confined run
# --------------------------------------------------------------------------

def _child_env(scratch, python):
    """A minimal environment for the child: nothing from ours (no tokens or API keys), temp and
    FreeCAD's user folders all pointing at the scratch folder."""
    home = os.path.join(scratch, "home")
    os.makedirs(home, exist_ok=True)
    env = {"TEMP": scratch, "TMP": scratch, "TMPDIR": scratch, "HOME": home,
           "FREECAD_USER_HOME": home, "FREECAD_USER_DATA": os.path.join(home, "data"),
           "FREECAD_USER_TEMP": scratch, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONUNBUFFERED": "1"}
    bin_dir = os.path.dirname(os.path.abspath(python))
    if WINDOWS:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        for k in ("SystemRoot", "SystemDrive", "windir", "ComSpec", "OS", "PROCESSOR_ARCHITECTURE",
                  "NUMBER_OF_PROCESSORS", "ProgramData", "ProgramFiles", "ALLUSERSPROFILE"):
            if os.environ.get(k):
                env[k] = os.environ[k]
        # An AppContainer launch fails (error 203) without LOCALAPPDATA, and Windows then rewrites
        # LOCALAPPDATA/TEMP/TMP to <LOCALAPPDATA>\Packages\<container>\AC[\Temp] (FreeCAD aborts if
        # that folder is missing), so point it at scratch and pre-create the rewritten path.
        os.makedirs(os.path.join(home, "Packages", "cadswarm.scriptsandbox", "AC", "Temp"), exist_ok=True)
        env.update(LOCALAPPDATA=home, APPDATA=home, USERPROFILE=home,
                   PATH=os.pathsep.join([bin_dir, os.path.join(root, "System32"), root]))
    else:
        env["PATH"] = os.pathsep.join([bin_dir, "/usr/bin", "/bin"])
        for k in ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH", "LANG", "LC_ALL"):
            if os.environ.get(k):
                env[k] = os.environ[k]
    return env


class Outcome:
    def __init__(self, exit_code, stdout, stderr, timed_out, peak_memory=0):
        self.exit_code, self.stdout, self.stderr = exit_code, stdout, stderr
        self.timed_out, self.peak_memory = timed_out, peak_memory


def _run_posix(argv, env, scratch, payload, timeout, memory_mb, confined):
    import signal

    def child_setup():
        os.setsid()  # own process group, so a kill takes everything the child started
        if not confined:
            return
        import resource
        for name, soft in (("RLIMIT_AS", memory_mb << 20), ("RLIMIT_CPU", int(timeout) + 5),
                           ("RLIMIT_NPROC", 0), ("RLIMIT_NOFILE", 256), ("RLIMIT_CORE", 0)):
            try:
                resource.setrlimit(getattr(resource, name), (soft, soft))
            except (ValueError, OSError, AttributeError):
                pass  # not every OS supports every limit (macOS ignores RLIMIT_AS, for one)

    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=scratch, env=env, preexec_fn=child_setup)
    timed_out = False
    try:
        out, err = proc.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        out, err = proc.communicate()
    return Outcome(proc.returncode, out, err, timed_out)


def _run_windows(level, argv, env, scratch, payload, timeout, memory_mb):
    r = _win().run_confined(level, argv, env, scratch, payload, timeout, memory_mb << 20,
                            _state["container_sid"])
    return Outcome(r.exit_code, r.stdout, r.stderr, r.timed_out, r.peak_memory)


def _parse(stdout):
    """(started, reply) from the runner's JSON lines; stray non-JSON lines are ignored."""
    started, reply = False, None
    for line in stdout.decode("utf-8", "replace").splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict):
            started = started or bool(msg.get("started"))
            if "ok" in msg:
                reply = msg
    return started, reply


def _tail(data, n=600):
    return data.decode("utf-8", "replace").strip()[-n:]


def _verdict(out, timeout, memory_mb, level):
    started, reply = _parse(out.stdout)
    if out.timed_out:
        raise SandboxError("timeout", f"Script exceeded the {timeout:g}s time limit and was stopped. "
                           "Simplify the geometry or split the work into smaller steps.")
    if reply is not None:
        if reply.get("code") == "sandbox_limit":
            reply["error"] = f"Script ran out of memory (limit {memory_mb} MB)"
        return reply
    if out.peak_memory and out.peak_memory >= 0.9 * (memory_mb << 20):
        raise SandboxError("sandbox_limit", f"Script ran out of memory (limit {memory_mb} MB)")
    detail = _tail(out.stderr)
    if not started:
        raise SandboxError("sandbox_unavailable", f"The script sandbox ({level}) could not start FreeCAD "
                           f"(exit code {out.exit_code}). {detail}")
    raise SandboxError("sandbox_limit" if out.exit_code in (-9, 137) else "script_error",
                       f"The script process was killed or crashed (exit code {out.exit_code}); nothing was "
                       f"changed. {detail}")


def _remove(path):
    for _ in range(5):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            time.sleep(0.2)
    shutil.rmtree(path, ignore_errors=True)


def run_script(python, save_copy, request):
    """Run `request["code"]` in a confined child against a snapshot of the document.

    `save_copy(path)` writes the snapshot. Returns the runner's reply dict ({ok, brep, result, error, code});
    raises SandboxError for timeouts, limits, crashes and an unavailable sandbox.
    """
    prepare(python)
    levels = _state["levels"]
    timeout, memory_mb = limits()
    if not levels:
        raise SandboxError("sandbox_unavailable", "Part scripts can't run: no isolation level is available on "
                           f"this machine ({_state['detail']}). Set CADSWARM_SCRIPT_SANDBOX=off to run them "
                           "unconfined (debugging only).")
    problems = []
    for level in levels:
        run_dir = tempfile.mkdtemp(prefix="cadswarm-run-")
        in_dir, scratch = os.path.join(run_dir, "in"), os.path.join(run_dir, "scratch")
        try:
            os.mkdir(in_dir)
            os.mkdir(scratch)
            # Folders are granted/labelled per level BEFORE the files go in, which then inherit it.
            if level == "appcontainer":
                _win().grant(in_dir, _state["container_sid"], "R")
                _win().grant(scratch, _state["container_sid"], "M")
            elif level == "lowintegrity":
                _win().mark_low_integrity(scratch)
            doc_copy = os.path.join(in_dir, "model.FCStd")
            save_copy(doc_copy)
            runner = os.path.join(in_dir, "script_runner.py")
            shutil.copyfile(RUNNER, runner)
            payload = json.dumps(dict(request, doc_path=doc_copy,
                                      freecad_bin=os.path.dirname(os.path.abspath(python)))).encode("utf-8")
            argv, env = [python, "-I", runner], _child_env(scratch, python)
            if level in ("appcontainer", "lowintegrity"):
                out = _run_windows(level, argv, env, scratch, payload, timeout, memory_mb)
            else:
                out = _run_posix(argv, env, scratch, payload, timeout, memory_mb, level != "off")
        except (OSError, _win_error()) as e:  # this level can't start here: try the next one down
            problems.append(f"{level}: {e}")
            log.warning("script sandbox level %s failed to start (%s)", level, e)
            continue
        finally:
            _remove(run_dir)
        if level != levels[0]:
            log.warning("part script ran at fallback level %s", level)
        return _verdict(out, timeout, memory_mb, level)
    raise SandboxError("sandbox_unavailable", "Part scripts can't run: the sandbox could not start ("
                       + "; ".join(problems) + "). Set CADSWARM_SCRIPT_SANDBOX=off to run them unconfined "
                       "(debugging only).")


def _win_error():
    return _win().SandboxError if WINDOWS else OSError
