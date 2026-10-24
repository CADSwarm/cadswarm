"""The part-script sandbox, proven against real FreeCAD: scripts run in a confined child process, and the
OS boundary (not the in-script AST guard) is what stops file writes, file reads, network, processes and
runaway memory/time. Escape tests switch the AST guard off to show the boundary holds on its own."""
import json
import tempfile
import os
import socket
import sys
import threading
from pathlib import Path

import pytest

from cadswarm import config
from cadswarm.hub.worker_client import WorkerClient, WorkerError

try:
    config.find_freecad_python()
except RuntimeError:
    pytest.skip("FreeCAD not installed", allow_module_level=True)

WIN = sys.platform == "win32"
needs_windows = pytest.mark.skipif(not WIN, reason="AppContainer / low-integrity are Windows features")


@pytest.fixture
def worker(tmp_path, monkeypatch):
    """Factory: a started worker with env overrides (read by the worker at start)."""
    made = []

    def make(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        c = WorkerClient(str(tmp_path / f"model{len(made)}.FCStd"))
        c.start()
        made.append(c)
        return c
    yield make
    for c in made:
        c.stop()
        c._kill()


def run(c, code, **args):
    return c.call("run_part_script", {"code": code, **args})


def names(c):
    return {o["name"] for o in c.call("status")["result"]["objects"]}


# --------------------------------------------------------------------------
# It still does its job
# --------------------------------------------------------------------------

def test_box_and_boolean_script_lands_in_the_live_doc(worker):
    c = worker()
    r = run(c, "box = Part.makeBox(10, 20, 30)\nhole = Part.makeCylinder(2, 30, Vector(5, 5, 0))\n"
               "shape = box.cut(hole)", name="Block", label="Drilled block")
    assert r["created"] == ["Block"]
    vol = 10 * 20 * 30 - 3.141592653589793 * 4 * 30
    assert r["result"]["volume"] == pytest.approx(vol, rel=1e-6)
    live = c.call("get_object", {"name": "Block"})["result"]
    assert live["label"] == "Drilled block"
    assert c.call("brep", {"names": ["Block"]})["result"]["objects"]["Block"]["brep"]


def test_result_round_trips_and_params_and_namespace(worker):
    c = worker()
    c.call("create_primitive", {"kind": "box", "name": "Plate", "props": {"Length": 7, "Width": 3, "Height": 2}})
    c.call("set_param", {"alias": "pitch", "value": "40 mm"})
    r = run(c, "import math\nplate = doc.getObject('Plate')\n"
               "result = {'pi': round(math.pi, 3), 'pitch': params['pitch'], 'vol': plate.Shape.Volume,\n"
               "          'ns': [Part.__name__, App.__name__, Vector(1, 2, 3).z, Placement().Base.x,\n"
               "                 Rotation().Angle], 'list': [1, 'two', None, 3.5]}")
    got = r["result"]["result"]
    assert got["pi"] == 3.142 and got["pitch"] == 40 and got["vol"] == 42
    assert got["ns"][:2] == ["Part", "FreeCAD"] and got["ns"][2] == 3 and got["list"] == [1, "two", None, 3.5]
    assert "created" in r and r["created"] == []  # a result-only script creates nothing


def test_script_errors_come_back_with_the_agents_own_lines(worker):
    c = worker()
    with pytest.raises(WorkerError) as e:
        run(c, "x = 1\ny = x / 0\n")
    assert e.value.code == "script_error" and "ZeroDivisionError" in str(e.value) and "line 2: y = x / 0" in str(e.value)
    assert "script_runner" not in str(e.value)  # no sandbox internals leaked
    with pytest.raises(WorkerError) as e:
        run(c, "def (")
    assert e.value.code == "script_error" and "SyntaxError" in str(e.value)
    with pytest.raises(WorkerError) as e:
        run(c, "shape = 42")
    assert e.value.code == "bad_args"


def test_script_changes_to_doc_are_discarded(worker):
    c = worker()
    c.call("create_primitive", {"kind": "box", "name": "Plate", "props": {"Length": 10, "Width": 10, "Height": 5}})
    before = c.call("brep", {"names": ["Plate"]})["result"]["objects"]["Plate"]["brep"]
    r = run(c, "p = doc.getObject('Plate')\np.Height = 50\ndoc.addObject('Part::Box', 'Sneaky')\n"
               "doc.recompute()\nresult = {'inside': p.Shape.Volume, 'n': len(doc.Objects)}")
    assert r["result"]["result"]["inside"] == pytest.approx(5000)  # the script saw its own edit...
    assert r["modified"] == [] and r["created"] == [] and "Sneaky" not in names(c)  # ...the live doc did not
    assert c.call("brep", {"names": ["Plate"]})["result"]["objects"]["Plate"]["brep"] == before
    c.stop()
    c._kill()  # and nothing was saved either
    assert "Sneaky" not in names(c)


def test_replacing_a_shape_recomputes_its_dependents(worker):
    c = worker()
    run(c, "shape = Part.makeBox(10, 10, 10)", name="Base")
    c.call("create_primitive", {"kind": "box", "name": "Tool", "props": {"Length": 4, "Width": 4, "Height": 4},
                                "placement": {"base": [3, 3, 3]}})
    c.call("boolean", {"kind": "cut", "base": "Base", "tool": "Tool", "name": "Cut"})
    c.call("create_primitive", {"kind": "box", "name": "Follower", "props": {"Width": 1, "Height": 1},
                                "expressions": {".Length": "Base.Shape.BoundBox.XLength"}})
    r = run(c, "shape = Part.makeBox(20, 10, 10)", name="Base")
    assert r["modified"] == ["Base"] and r["created"] == []
    assert set(r["recomputed"]) == {"Cut", "Follower"}  # one via a link, one via an expression
    assert c.call("get_object", {"name": "Cut"})["result"]["bbox"][3] == pytest.approx(20)
    assert c.call("get_object", {"name": "Follower"})["result"]["bbox"][3] == pytest.approx(20)


def test_locks_still_apply_to_the_shape_a_script_replaces(worker):
    c = worker()
    run(c, "shape = Part.makeBox(1, 1, 1)", name="Mine")
    with pytest.raises(WorkerError) as e:
        c.call("run_part_script", {"code": "shape = Part.makeBox(2, 2, 2)", "name": "Mine"}, allowed=["Other"])
    assert e.value.code == "lock_violation"
    assert c.call("get_object", {"name": "Mine"})["result"]["bbox"][3] == pytest.approx(1)


def test_ast_guard_is_still_the_first_layer(worker, tmp_path):
    c = worker()
    with pytest.raises(WorkerError) as e:
        run(c, "import os")
    assert e.value.code == "not_allowed" or "import" in str(e.value)
    with pytest.raises(WorkerError) as e:
        run(c, "f = doc.saveAs\nf(r'%s')" % (tmp_path / "x.FCStd"))
    assert e.value.code == "not_allowed" and not (tmp_path / "x.FCStd").exists()


def test_ping_reports_the_isolation_level_and_limits(worker):
    c = worker()
    info = c.call("ping")["result"]["script_sandbox"]
    assert info["level"] in {"appcontainer", "lowintegrity", "process-limits"}
    assert info["timeout_s"] <= 60 and info["memory_mb"] >= 256
    if WIN:
        assert info["level"] == "appcontainer", info  # verified on this machine; see README for fallbacks


def test_run_folders_are_cleaned_up(worker):
    import tempfile
    tmp = Path(tempfile.gettempdir())
    before = set(tmp.glob("cadswarm-run-*"))
    c = worker()
    run(c, "shape = Part.makeBox(1, 1, 1)", name="Tidy")
    with pytest.raises(WorkerError):
        run(c, "raise ValueError('x')")
    assert set(tmp.glob("cadswarm-run-*")) == before


# --------------------------------------------------------------------------
# The boundary itself (AST guard off: everything Python lets the script try, it gets to try)
# --------------------------------------------------------------------------

def probe_code(target_dir, read_file, read_dir, home, port):
    """One script that tries every escape and records what happened, per attempt."""
    return f'''
import sys, random, socket, subprocess, os, ctypes, tempfile
out = {{}}
def attempt(name, fn):
    try:
        out[name] = "ESCAPED: " + repr(fn())[:80]
    except BaseException as e:
        out[name] = "blocked: " + type(e).__name__
T = {str(target_dir)!r}
H = {str(home)!r}
RT = {tempfile.gettempdir()!r}
def write(path):
    def go():
        with open(path, "w") as f:
            f.write("pwned")
        return path
    return go
attempt("write-project", write(os.path.join(T, "project.txt")))
attempt("write-documents", write(os.path.join(H, "Documents", "cadswarm-escape.txt")))
attempt("write-realtemp", write(os.path.join(RT, "cadswarm-escape-temp.txt")))
attempt("write-profile", write(os.path.join(H, "cadswarm-escape.txt")))
attempt("write-via-random-os", lambda: random._os.makedirs(os.path.join(T, "made-by-random")))
def chain_write():
    for c in ().__class__.__base__.__subclasses__():
        if c.__name__ == "_wrap_close":  # os's popen wrapper: its __init__ globals ARE the os module's
            g = c.__init__.__globals__
            fd = g["open"](os.path.join(T, "chain.txt"), os.O_WRONLY | os.O_CREAT)
            return "wrote fd %d" % fd
    return "chain not found"
def chain_system():
    for c in ().__class__.__base__.__subclasses__():
        if c.__name__ == "_wrap_close":
            rc = c.__init__.__globals__["system"]("echo hi > " + os.path.join(T, "chain-sys.txt"))
            if rc != 0:
                raise OSError("rc %d" % rc)
            return "ran"
    return "chain not found"
attempt("write-via-subclasses-chain", chain_write)
attempt("spawn-via-subclasses-chain", chain_system)
def ctypes_write():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = ctypes.c_void_p
    h = k.CreateFileW(os.path.join(T, "ctypes.txt"), 0x40000000, 0, None, 2, 0x80, None)
    if h in (None, ctypes.c_void_p(-1).value, 0xFFFFFFFF, 2**64 - 1):
        raise OSError(ctypes.get_last_error())
    return h
if sys.platform == "win32":
    attempt("write-via-ctypes", ctypes_write)
attempt("write-via-freecad", lambda: __import__("FreeCAD").newDocument("x").saveAs(os.path.join(T, "fc.FCStd")))
attempt("write-via-shape", lambda: Part.makeBox(1, 1, 1).exportStep(os.path.join(T, "s.step")))
attempt("read-profile-file", lambda: open({str(read_file)!r}).read())
attempt("list-profile-dir", lambda: os.listdir({str(read_dir)!r}))
def net(host, p):
    def go():
        s = socket.socket(); s.settimeout(4); s.connect((host, p)); s.close(); return "connected"
    return go
attempt("net-loopback-hub", net("127.0.0.1", {port}))
attempt("net-external", net("1.1.1.1", 53))
def spawn(argv):
    def go():
        p = subprocess.run(argv, timeout=10)
        if p.returncode != 0:
            raise OSError("rc %d" % p.returncode)
        return "ran"
    return go
attempt("spawn-subprocess", spawn([sys.executable, "-c", "open(r'%s', 'w').write('x')" % os.path.join(T, "child.txt")]))
attempt("spawn-cmd", spawn(["cmd", "/c", "echo hi > " + os.path.join(T, "cmd.txt")] if sys.platform == "win32" else ["sh", "-c", "echo hi > " + os.path.join(T, "cmd.txt")]))
def system():
    rc = os.system("echo hi > " + os.path.join(T, "system.txt"))
    if rc != 0:
        raise OSError("rc %d" % rc)
    return "ran"
attempt("spawn-os-system", system)
result = out
'''


@pytest.fixture
def listener():
    """A local TCP server standing in for the hub's port; records whether anything ever connected."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(0.2)
    hits, stop = [], threading.Event()

    def accept():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                hits.append(1)
                conn.close()
            except (socket.timeout, OSError):
                pass
    t = threading.Thread(target=accept, daemon=True)
    t.start()
    yield srv.getsockname()[1], hits
    stop.set()
    t.join(2)
    srv.close()


def escape_run(worker, tmp_path, listener, level):
    port, hits = listener
    project = tmp_path / "project"
    project.mkdir()
    secret = tmp_path / "secret.txt"  # tmp_path lives under the user's profile
    secret.write_text("top secret")
    c = worker(CADSWARM_TEST_NO_AST_GUARD="1", **({"CADSWARM_SANDBOX_LEVEL": level} if level else {}))
    out = run(c, probe_code(project, secret, Path.home() / "Documents", Path.home(), port))["result"]["result"]
    return c, out, project, hits


@needs_windows
def test_appcontainer_blocks_every_escape(worker, tmp_path, listener):
    c, out, project, hits = escape_run(worker, tmp_path, listener, "appcontainer")
    assert c.call("ping")["result"]["script_sandbox"]["level"] == "appcontainer"
    escaped = {k: v for k, v in out.items() if v.startswith("ESCAPED")}
    assert not escaped, escaped
    assert not hits, "the script reached a loopback port"
    assert list(project.iterdir()) == [] and not (tmp_path / "project.txt").exists()
    assert not (Path.home() / "Documents" / "cadswarm-escape.txt").exists()
    assert not (Path.home() / "cadswarm-escape.txt").exists()
    assert not (Path(tempfile.gettempdir()) / "cadswarm-escape-temp.txt").exists()
    for name in ("project.txt", "chain.txt", "chain-sys.txt", "ctypes.txt", "s.step", "fc.FCStd", "child.txt", "cmd.txt", "system.txt"):
        assert not (project / name).exists(), name
    assert len(out) >= 15  # every attempt actually ran and was recorded


@needs_windows
def test_lowintegrity_fallback_mechanism(tmp_path):
    """The fallback level, driven directly (a real FreeCAD can't be used: once the AppContainer level has
    granted its ACE on the install, the low-integrity token can no longer read it; see sandbox.py)."""
    from cadswarm.worker import sandbox_win as win
    outside = tmp_path / "outside"
    outside.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    win.mark_low_integrity(str(scratch))
    code = f'''
import os, subprocess
r = {{}}
def t(k, f):
    try:
        r[k] = repr(f())[:40]
    except BaseException as e:
        r[k] = 'blocked ' + type(e).__name__
t('write-outside', lambda: open(r"{outside / 'x.txt'}", 'w').write('x'))
t('write-scratch', lambda: open(r"{scratch / 'ok.txt'}", 'w').write('x'))
t('read-secret', lambda: open(r"{secret}").read())
t('spawn', lambda: subprocess.run(['cmd', '/c', r"echo hi > {outside / 'p.txt'}"], timeout=10).returncode)
print(repr(r))
'''
    base_python = os.path.join(sys.base_prefix, "python.exe")
    env = {"SystemRoot": os.environ["SystemRoot"], "TEMP": str(scratch), "TMP": str(scratch),
           "PATH": os.path.join(os.environ["SystemRoot"], "System32")}
    res = win.run_confined("lowintegrity", [base_python, "-I", "-c", code], env, str(scratch), b"", 30, 1 << 30)
    got = eval(res.stdout.decode().strip().splitlines()[-1])
    assert got["write-outside"].startswith("blocked") and not (outside / "x.txt").exists()
    assert got["write-scratch"] == "1" and (scratch / "ok.txt").exists()
    assert got["spawn"].startswith("blocked") and not (outside / "p.txt").exists()
    assert got["read-secret"] == "'top secret'"  # what this level does NOT stop; the README says so


def test_the_scripts_own_scratch_folder_is_writable_and_private(worker):
    c = worker(CADSWARM_TEST_NO_AST_GUARD="1")
    r = run(c, "import os, tempfile\np = os.path.join(tempfile.gettempdir(), 'a.txt')\n"
               "open(p, 'w').write('hello')\nresult = {'dir': tempfile.gettempdir(), 'read': open(p).read()}")
    got = r["result"]["result"]
    assert got["read"] == "hello"
    assert not os.path.exists(os.path.join(got["dir"], "a.txt"))  # gone with the run


@needs_windows
def test_children_cannot_start_grandchildren_via_the_job(worker, tmp_path):
    c = worker(CADSWARM_TEST_NO_AST_GUARD="1")
    r = run(c, "import subprocess, sys\ntry:\n    subprocess.run([sys.executable, '-c', 'print(1)'], timeout=10)\n"
               "    result = 'spawned'\nexcept BaseException as e:\n    result = type(e).__name__")
    assert r["result"]["result"] != "spawned"


@pytest.mark.skipif(not WIN, reason="job-object memory cap")
def test_memory_hog_is_stopped_and_worker_survives(worker):
    c = worker(CADSWARM_SCRIPT_MEMORY_MB=768)
    proc = c.proc
    with pytest.raises(WorkerError) as e:
        run(c, "blocks = []\nwhile True:\n    blocks.append(bytearray(64 * 1024 * 1024))")
    assert e.value.code == "sandbox_limit" and "memory" in str(e.value).lower()
    assert c.proc is proc and proc.poll() is None
    assert run(c, "shape = Part.makeBox(1, 2, 3)", name="Alive")["created"]


def test_runaway_loop_times_out_and_worker_stays_healthy(worker):
    c = worker(CADSWARM_SCRIPT_TIMEOUT=4)
    c.call("create_primitive", {"kind": "box", "name": "Kept", "props": {"Length": 5}})
    proc = c.proc
    with pytest.raises(WorkerError) as e:
        run(c, "doc.addObject('Part::Feature', 'Half')\nwhile True:\n    pass")
    assert e.value.code == "timeout" and "4s" in str(e.value)
    assert c.proc is proc and proc.poll() is None  # the same worker, never restarted
    assert c.call("ping")["result"]["pong"] and names(c) >= {"Kept"} and "Half" not in names(c)
    assert run(c, "shape = Part.makeBox(1, 1, 1)", name="After")["created"] == ["After"]


# --------------------------------------------------------------------------
# Operating modes
# --------------------------------------------------------------------------

def test_sandbox_off_switch_is_loud_and_unconfined(worker, caplog, capfd):
    c = worker(CADSWARM_SCRIPT_SANDBOX="off")
    info = c.call("ping")["result"]["script_sandbox"]
    assert info["level"] == "off" and "off" in info["detail"]
    assert run(c, "shape = Part.makeBox(1, 1, 1)", name="Plain")["created"] == ["Plain"]
    assert "UNCONFINED" in capfd.readouterr().err  # the worker's stderr is inherited


class StubWin:
    class SandboxError(Exception):
        pass

    def grant(self, *a):
        pass

    mark_low_integrity = grant


def stubbed_run(monkeypatch, tmp_path, levels, runner):
    from cadswarm.worker import sandbox as sb
    monkeypatch.setattr(sb, "prepare", lambda python: None)
    monkeypatch.setattr(sb, "_state", {"levels": levels, "detail": "test", "container_sid": "S-1"})
    monkeypatch.setattr(sb, "_win", lambda: StubWin)
    monkeypatch.setattr(sb, "WINDOWS", True)
    monkeypatch.setattr(sb, "_run_windows", runner)
    monkeypatch.setattr(sb, "_child_env", lambda scratch, python: {})
    return sb, lambda path: open(path, "w").write("x")


def test_no_level_available_is_a_clean_error(monkeypatch, tmp_path):
    sb, save = stubbed_run(monkeypatch, tmp_path, [], None)
    with pytest.raises(sb.SandboxError) as e:
        sb.run_script(sys.executable, save, {"code": ""})
    assert e.value.code == "sandbox_unavailable" and "CADSWARM_SCRIPT_SANDBOX=off" in str(e.value)


def test_a_level_that_cannot_start_falls_back_to_the_next(monkeypatch, tmp_path, caplog):
    calls = []

    def runner(level, argv, env, scratch, payload, timeout, memory_mb):
        calls.append(level)
        if level == "appcontainer":
            raise StubWin.SandboxError("CreateProcess failed")
        return sb.Outcome(0, b'{"started": true}\n{"ok": true, "brep": null, "result": 5}\n', b"", False)
    sb, save = stubbed_run(monkeypatch, tmp_path, ["appcontainer", "lowintegrity"], runner)
    assert sb.run_script(sys.executable, save, {"code": ""})["result"] == 5
    assert calls == ["appcontainer", "lowintegrity"] and "fallback level lowintegrity" in caplog.text
    calls.clear()
    sb2, save = stubbed_run(monkeypatch, tmp_path, ["appcontainer"], runner)
    with pytest.raises(sb2.SandboxError) as e:
        sb2.run_script(sys.executable, save, {"code": ""})
    assert e.value.code == "sandbox_unavailable" and "CreateProcess failed" in str(e.value)


def test_unit_verdict_mapping():
    from cadswarm.worker import sandbox as sb
    ok = json.dumps({"ok": True, "brep": None, "result": 1}).encode()
    reply = sb._verdict(sb.Outcome(0, b'{"started": true}\njunk\n' + ok + b"\n", b"", False), 5, 100, "x")
    assert reply["ok"] and reply["result"] == 1
    with pytest.raises(sb.SandboxError) as e:
        sb._verdict(sb.Outcome(1, b"", b"", True), 5, 100, "x")
    assert e.value.code == "timeout"
    with pytest.raises(sb.SandboxError) as e:
        sb._verdict(sb.Outcome(1, b"", b"boom", False, peak_memory=100 << 20), 5, 100, "x")
    assert e.value.code == "sandbox_limit"
    with pytest.raises(sb.SandboxError) as e:
        sb._verdict(sb.Outcome(3, b"", b"no freecad", False), 5, 100, "x")
    assert e.value.code == "sandbox_unavailable" and "no freecad" in str(e.value)
    with pytest.raises(sb.SandboxError) as e:
        sb._verdict(sb.Outcome(-11, b'{"started": true}\n', b"", False), 5, 100, "x")
    assert e.value.code == "script_error"


def test_unit_limits_come_from_env(monkeypatch):
    from cadswarm.worker import sandbox as sb
    monkeypatch.setenv("CADSWARM_SCRIPT_TIMEOUT", "7")
    monkeypatch.setenv("CADSWARM_SCRIPT_MEMORY_MB", "300")
    assert sb.limits() == (7.0, 300)
    monkeypatch.setenv("CADSWARM_SCRIPT_TIMEOUT", "junk")
    assert sb.limits()[0] == 60
