from pathlib import Path

ROOT = Path(r"C:\Users\sachc\Projects\cadswarm-fc")


def replace(rel, old, new):
    path = ROOT / rel
    raw = path.read_bytes()
    newline = b"\r\n" if raw.count(b"\r\n") > 100 else b"\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    old = old.replace("\r\n", "\n")
    new = new.replace("\r\n", "\n")
    if old not in text:
        raise SystemExit(f"Expected text not found in {rel}: {old[:100]!r}")
    text = text.replace(old, new, 1)
    path.write_bytes(text.replace("\n", newline.decode("ascii")).encode("utf-8"))


replace(
    "cadswarm/app/instance.py",
    "import sys\nimport tempfile\n",
    "import sys\nimport socket\nimport tempfile\n",
)
replace(
    "cadswarm/app/instance.py",
    '''        if family == "AF_UNIX" and os.path.exists(addr):
            try:  # forward() failed to reach it, so it's left over from a crashed run
                os.unlink(addr)
            except OSError:
                return False
        try:
            self._listener = Listener(addr, family, authkey=key)
        except OSError:
            return False  # another instance owns the channel; run without hand-offs
''',
    '''        if family == "AF_UNIX" and os.path.exists(addr):
            # A failed authenticated handoff can still mean a live listener. Probe its socket
            # before unlinking so a temporary connection issue cannot split one profile in two.
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(0.25)
                probe.connect(addr)
            except ConnectionRefusedError:  # stale path left by a crashed process
                try:
                    os.unlink(addr)
                except OSError:
                    return False
            except FileNotFoundError:  # another process already removed the stale path
                pass
            except OSError:
                return False
            else:  # a live listener owns the channel
                return False
            finally:
                probe.close()
        try:
            self._listener = Listener(addr, family, authkey=key)
        except OSError:
            return False  # another instance owns the channel; run without hand-offs
''',
)
replace(
    "tests/test_pro_ui.py",
    '''    assert pro_style.unpacked(params["BackgroundColor2"]) == "#c5cfda"   # top
    assert pro_style.unpacked(params["BackgroundColor3"]) == "#f3f5f7"   # bottom
    assert pro_style.unpacked(params["SelectionColor"]) == "#1f7ae0"
    assert pro_style.unpacked(params["HighlightColor"]) == "#f2a93b"
''',
    '''    light = pro_style.tokens("light")
    assert pro_style.unpacked(params["BackgroundColor2"]) == light["VIEW_TOP"]
    assert pro_style.unpacked(params["BackgroundColor3"]) == light["VIEW_BOTTOM"]
    assert pro_style.unpacked(params["SelectionColor"]) == light["SELECT"]
    assert pro_style.unpacked(params["HighlightColor"]) == light["PRESELECT"]
''',
)
replace(
    "tests/test_pro_ui.py",
    'assert view.GetUnsigned("BackgroundColor2") == pro_style.packed("#c5cfda")',
    'assert view.GetUnsigned("BackgroundColor2") == pro_style.packed(pro_style.tokens("light")["VIEW_TOP"])',
)

instance_tests = '''import os
import time

from cadswarm.app import instance


def test_second_instance_keeps_the_live_channel_and_files_are_forwarded(tmp_path):
    first = instance.Server(str(tmp_path))
    second = instance.Server(str(tmp_path))
    assert first.start()
    try:
        assert not second.start()
        path = str(tmp_path / "sample.FCStd")
        assert instance.forward(str(tmp_path), [path])
        deadline = time.monotonic() + 3
        batches = []
        while time.monotonic() < deadline and not batches:
            batches = first.drain()
            if not batches:
                time.sleep(0.01)
        assert batches == [[os.path.abspath(path)]]
    finally:
        second.close()
        first.close()
'''
new_test = ROOT / "tests/test_app_instance.py"
if new_test.exists():
    raise SystemExit("tests/test_app_instance.py already exists; refusing to overwrite it")
new_test.write_text(instance_tests, encoding="utf-8", newline="\n")

replace(
    "tests/e2e/team_e2e.py",
    "[--scenario team|build|remote|all]",
    "[--scenario team|build|all]",
)
replace(
    "tests/e2e/team_e2e.py",
    "  * remote: drives the web app's remote view and remote control (tests/e2e/web_remote.mjs) against the team.\n",
    "The web/phone remote view is not exercised by this runner yet; do not treat a team/build pass as evidence\n"
    "for remote control.\n",
)
replace(
    "tests/e2e/team_e2e.py",
    '"--listen-addr", f"0.0.0.0:{PORT}"',
    '"--listen-addr", f"127.0.0.1:{PORT}"',
)
replace(
    "tests/e2e/team_e2e.py",
    '''        for p in self.procs:
            p.terminate()
''',
    '''        for p in self.procs:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)
''',
)
