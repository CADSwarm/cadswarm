"""Worker layer: script guard, FreeCAD discovery, worker supervision, save/rollback, exec timeout."""
import importlib.util
import os
import sys
import textwrap
import threading
import time
import types
from pathlib import Path
from unittest import mock

import pytest

from cadswarm import config
from cadswarm.worker_client import WorkerClient, WorkerError

WORKER_PY = Path(config.__file__).parent / "worker" / "freecad_worker.py"
RUNNER_PY = WORKER_PY.parent / "script_runner.py"
try:
    config.find_freecad_python()
    HAVE_FREECAD = True
except RuntimeError:
    HAVE_FREECAD = False


# --------------------------------------------------------------------------
# Script guard (pure AST check; lives in the standalone script runner, which imports FreeCAD only in main())
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def worker_mod():
    spec = importlib.util.spec_from_file_location("script_runner_under_test", RUNNER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BYPASSES = [
    "import random\nrandom._os.makedirs('x')",
    "import typing\ntyping.sys.modules['os']",
    "import re\nre.enum.sys",
    "import dataclasses\ndataclasses.sys",
    "import statistics\nstatistics.sys",
    "from random import _os",
    "from typing import sys",
    "__import__.__globals__['x']",
    "().__class__.__base__.__subclasses__()",
    "f = doc.saveAs\nf('x.FCStd')",
    "getattr(doc, 'save' + 'As')('x')",
    "setattr(doc, 'x', 1)",
    "import numpy\nnumpy.savetxt('x.txt', [1])",
    "import numpy\nnumpy.array([1]).tofile('x')",
    "import numpy\nnumpy.memmap('x', mode='w+', shape=(1,))",
    "shape.exportStep('x.step')",
    "shape.exportStl('x.stl')",
    "shape.exportBrep('x.brep')",
    "shape.exportIges('x.igs')",
    "Mesh.export([], 'x.stl')",
    "doc.saveCopy('x')",
    "doc.save()",
    "m.write('x.stl')",
    "print(__builtins__)",
    "x = doc.__dict__",
    "FreeCAD.closeDocument(doc.Name)",
    "import functools\nfunctools.sys",
]

LEGIT = [
    "# not .write( or doc.saveAs( here\nshape = Part.makeBox(1, 2, 3)",
    "note = 'call doc.saveAs(p) or shape.exportStep(p) later'\nresult = note",
    '"""docstring mentioning buf.write(x)"""\nx = 1',
    "import math\nshape = Part.makeCylinder(10, 5)\nresult = {'pi': round(math.pi, 2)}",
    "class Peg:\n    def __init__(self, r):\n        self._r = r\n    def area(self):\n        return self._r ** 2\n"
    "result = Peg(2).area()",
    "brep = Part.makeBox(1, 1, 1).exportBrepToString()",
    "import numpy as np\nresult = float(np.linspace(0, 1, 5).sum())",
    "shape = Part.makeBox(*[p for p in (1, 2, 3)])",
]


@pytest.mark.parametrize("code", BYPASSES)
def test_guard_refuses(worker_mod, code):
    assert worker_mod.script_violation(code), code


@pytest.mark.parametrize("code", LEGIT)
def test_guard_allows(worker_mod, code):
    assert worker_mod.script_violation(code) is None, worker_mod.script_violation(code)


def test_guard_leaves_syntax_errors_to_compile(worker_mod):
    assert worker_mod.script_violation("def (") is None


def test_guard_reports_line(worker_mod):
    assert worker_mod.script_violation("x = 1\ny = 2\ndoc.saveAs('p')").startswith("line 3")


# --------------------------------------------------------------------------
# FreeCAD discovery
# --------------------------------------------------------------------------

def _install(root, name, exe="python.exe", sub=("bin",)):
    bin_dir = Path(root, name, *sub)
    bin_dir.mkdir(parents=True)
    (bin_dir / exe).write_text("")
    os.chmod(bin_dir / exe, 0o755)  # POSIX only accepts an executable Python
    return str((bin_dir / exe))


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Windows layouts unless a test says otherwise: ProgramFiles and LOCALAPPDATA only count there."""
    monkeypatch.setattr(config, "_platform", lambda: "win32")
    monkeypatch.delenv("CADSWARM_FREECAD_PYTHON", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nolocal"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nopf"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "nopf86"))
    monkeypatch.setattr(config, "_registry_installs", lambda: [])
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    return tmp_path


def test_version_key_is_numeric():
    assert config._version_key("FreeCAD 1.10") > config._version_key("FreeCAD 1.9")
    assert config._version_key("FreeCAD_1.1.4-Windows-x86_64-py311") == (1, 1, 4)
    assert config._version_key("FreeCAD") == ()


def test_newest_version_wins_numerically(clean_env, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(clean_env))
    _install(clean_env / "Programs", "FreeCAD 1.9")
    new = _install(clean_env / "Programs", "FreeCAD 1.10")
    _install(clean_env / "Programs", "FreeCAD 0.21")
    assert config.find_freecad_python() == os.path.normpath(new)


def test_program_files_x86_is_searched(clean_env, monkeypatch):
    monkeypatch.setenv("ProgramFiles(x86)", str(clean_env / "x86"))
    py = _install(clean_env / "x86", "FreeCAD 1.0")
    assert config.find_freecad_python() == os.path.normpath(py)


def test_freecadcmd_on_path(clean_env, monkeypatch):
    py = _install(clean_env / "elsewhere", "FreeCAD 1.2")
    cmd = Path(py).with_name("FreeCADCmd.exe")
    cmd.write_text("")
    monkeypatch.setattr(config.shutil, "which", lambda name: str(cmd) if name == "freecadcmd" else None)
    assert config.find_freecad_python() == os.path.normpath(py)


def test_registry_install_location(clean_env, monkeypatch):
    py = _install(clean_env / "weird place", "FC")
    monkeypatch.setattr(config, "_registry_installs",
                        lambda: [(str(clean_env / "weird place" / "FC"), "1.12.0"), (str(clean_env / "gone"), "9.9")])
    assert config.find_freecad_python() == os.path.normpath(py)


def test_registry_version_beats_folder_ranking(clean_env, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(clean_env))
    _install(clean_env / "Programs", "FreeCAD 1.0")
    reg = _install(clean_env / "other", "FC")
    monkeypatch.setattr(config, "_registry_installs", lambda: [(str(clean_env / "other" / "FC"), "1.1.4")])
    assert config.find_freecad_python() == os.path.normpath(reg)


def test_macos_app_bundles_in_home_applications(clean_env, monkeypatch):
    monkeypatch.setattr(config, "_platform", lambda: "darwin")
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(clean_env))
    _install(clean_env / "Applications", "FreeCAD 1.0.app", "python", ("Contents", "Resources", "bin"))
    new = _install(clean_env / "Applications", "FreeCAD 1.1.app", "python", ("Contents", "Resources", "bin"))
    assert config.find_freecad_python() == os.path.normpath(new)


def test_linux_extracted_appimage_or_tarball(clean_env, monkeypatch):
    monkeypatch.setattr(config, "_platform", lambda: "linux")
    monkeypatch.setattr(config, "LINUX_FREECAD_LIBS", ())
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(clean_env))
    py = _install(clean_env / "Applications", "FreeCAD-1.1", "python3")
    assert config.find_freecad_python() == os.path.normpath(py)


def test_explicit_env_wins_and_missing_raises(clean_env, monkeypatch):
    with pytest.raises(RuntimeError, match="FreeCAD not found"):
        config.find_freecad_python()
    monkeypatch.setenv("CADSWARM_FREECAD_PYTHON", "/my/python")
    assert config.find_freecad_python() == "/my/python"


def test_real_registry_lookup_does_not_crash():
    assert isinstance(config._registry_installs(), list)


# --------------------------------------------------------------------------
# WorkerClient against a fake worker (deterministic failure modes)
# --------------------------------------------------------------------------

FAKE = textwrap.dedent('''
    import json, os, sys, time
    mode = os.environ["FAKE_MODE"]
    def say(o): print(json.dumps(o), flush=True)
    if mode == "junk_start":
        print("OCC banner {not json", flush=True)
    if mode == "notready":
        say({"hello": 1}); time.sleep(60)
    if mode == "badready":
        print("garbage only", flush=True); time.sleep(60)
    say({"ready": True})
    for line in sys.stdin:
        req = json.loads(line)
        if mode == "junk":
            print("stray {not json", flush=True)
            say({"id": req["id"] - 1, "ok": True, "result": "stale"})
            print("[1, 2]", flush=True)
            say({"id": req["id"], "ok": True, "result": "fine"})
        elif mode == "die":
            print("junk then death", flush=True); sys.exit(0)
        elif mode == "hang":
            time.sleep(60)
        elif mode == "error":
            say({"id": req["id"], "ok": False, "code": "save_failed", "error": "nope"})
        else:
            say({"id": req["id"], "ok": True, "result": "fine"})
''')


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE)
    made = []

    def make(mode, timeout=5):
        monkeypatch.setenv("FAKE_MODE", mode)
        c = WorkerClient(str(tmp_path / "m.FCStd"), python=sys.executable, script=str(script), timeout=timeout)
        made.append(c)
        return c
    yield make
    for c in made:
        c._kill()


def test_client_skips_stray_lines_and_stale_ids(fake, caplog):
    c = fake("junk")
    assert c.call("ping")["result"] == "fine"
    assert c.call("ping")["result"] == "fine"
    assert "ignoring unexpected worker output" in caplog.text


def test_client_survives_junk_before_ready(fake):
    c = fake("junk_start")
    assert c.call("ping")["result"] == "fine"


@pytest.mark.parametrize("mode", ["notready", "badready"])
def test_client_start_failure_kills_process(fake, mode):
    c = fake(mode, timeout=1.5)
    with pytest.raises(WorkerError):
        c.start()
    assert c.proc is None


def test_client_death_after_junk_reaps_and_restarts(fake):
    c = fake("die")
    c.start()
    proc = c.proc
    with pytest.raises(WorkerError) as e:
        c.call("ping")
    assert e.value.code == "worker_died" and c.proc is None and proc.poll() is not None


def test_client_timeout_kills_worker(fake):
    c = fake("hang", timeout=1.5)
    c.start()
    proc = c.proc
    with pytest.raises(WorkerError) as e:
        c.call("ping")
    assert e.value.code == "timeout" and c.proc is None and proc.poll() is not None


def test_client_passes_worker_error_codes_through(fake):
    c = fake("error")
    with pytest.raises(WorkerError) as e:
        c.call("set_param")
    assert e.value.code == "save_failed" and c.proc is not None and c.proc.poll() is None


# --------------------------------------------------------------------------
# Real FreeCAD
# --------------------------------------------------------------------------

@pytest.fixture
def fc(tmp_path):
    if not HAVE_FREECAD:
        pytest.skip("FreeCAD not installed")
    c = WorkerClient(str(tmp_path / "model.FCStd"))
    c.start()
    yield c, tmp_path / "model.FCStd"
    c.stop()
    c._kill()


def names(c):
    return {o["name"] for o in c.call("status")["result"]["objects"]}


def hold_open(path):
    """Open `path` without FILE_SHARE_DELETE, as an AV scanner or sync client might (Windows only)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(path), 0x80000000, 1 | 2, None, 3, 0x80, None)  # GENERIC_READ, share R|W, OPEN_EXISTING
    assert h not in (None, wintypes.HANDLE(-1).value), ctypes.get_last_error()
    return lambda: k32.CloseHandle(h)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share modes")
def test_failed_save_rolls_the_op_back(fc):
    c, model = fc
    release = hold_open(model)
    try:
        t0 = time.time()
        with pytest.raises(WorkerError) as e:
            c.call("create_primitive", {"kind": "box", "name": "Plate", "props": {"Length": 10}})
        assert e.value.code == "save_failed" and "rolled back" in str(e.value)
        assert time.time() - t0 > 1  # it retried before giving up
        assert "Plate" not in names(c)  # memory matches the disk and the hub's view
    finally:
        release()
    c.call("create_primitive", {"kind": "box", "name": "Plate", "props": {"Length": 10}})  # no 'exists'
    assert "Plate" in names(c)
    assert not os.path.exists(str(model) + ".tmp.FCStd")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share modes")
def test_brief_hold_is_waited_out(fc):
    c, model = fc
    release = hold_open(model)
    threading.Timer(0.6, release).start()
    resp = c.call("create_primitive", {"kind": "box", "name": "Plate", "props": {"Length": 10}})
    assert resp["created"] == ["Plate"]


def test_script_guard_end_to_end(fc, tmp_path):
    c, _ = fc
    target = tmp_path / "leak.txt"
    for code in ("import random\nrandom._os.makedirs(r'%s')" % (tmp_path / "dir"),
                 "f = doc.saveAs\nf(r'%s')" % (tmp_path / "x.FCStd")):
        with pytest.raises(WorkerError) as e:
            c.call("run_part_script", {"name": "Evil", "code": code})
        assert e.value.code == "not_allowed"
    # Past the AST check, the audit hook still refuses Python-level writes.
    with pytest.raises(WorkerError):
        c.call("run_part_script", {"code": "import json\njson.codecs.open(r'%s', 'w')" % target})
    assert not target.exists() and not (tmp_path / "dir").exists() and not (tmp_path / "x.FCStd").exists()
    r = c.call("run_part_script", {"name": "Gear", "code": "# not .write(\nshape = Part.makeCylinder(10, 5)"})
    assert r["created"] == ["Gear"]


def test_c_level_stdout_cannot_reach_protocol(tmp_path, caplog):
    if not HAVE_FREECAD:
        pytest.skip("FreeCAD not installed")
    wrapper = tmp_path / "wrap.py"
    wrapper.write_text(textwrap.dedent(f'''
        import os, sys
        sys.path.insert(0, r"{WORKER_PY.parent}")
        import freecad_worker as w
        orig = w.handle
        def noisy(req):
            os.write(1, b"OCC says hello\\n")  # what C++ printf does: straight to fd 1
            return orig(req)
        w.handle = noisy
        w.main()
    '''))
    c = WorkerClient(str(tmp_path / "model.FCStd"), script=str(wrapper))
    try:
        assert c.call("ping")["result"]["pong"]
        assert "unexpected worker output" not in caplog.text
    finally:
        c.stop()
        c._kill()
