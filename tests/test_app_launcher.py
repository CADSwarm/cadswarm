"""cadswarm.app's launch robustness: log file, missing-FreeCAD dialog, single-instance hand-off."""
import os
import sys

import pytest

from cadswarm.app import __main__ as app_main
from cadswarm.app import engine, instance


def test_log_goes_to_the_engine_profile_or_the_app_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("CADSWARM_LOG", raising=False)
    monkeypatch.setenv("CADSWARM_ENGINE_HOME", str(tmp_path / "eng"))
    assert app_main.log_path() == os.path.join(str(tmp_path / "eng"), "logs", "cadswarm-app.log")
    monkeypatch.delenv("CADSWARM_ENGINE_HOME")
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert app_main.log_path() == os.path.join(str(tmp_path / "roaming"), "CadSwarm", "logs", "cadswarm-app.log")


def test_setup_log_captures_output_and_rotates(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "cadswarm-app.log"
    path.parent.mkdir()
    path.write_text("x" * (app_main.LOG_LIMIT + 1))
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    import faulthandler
    log = app_main.setup_log(str(path))
    try:
        print("hello from the app")
        sys.stderr.write("a warning\n")
    finally:
        faulthandler.disable()
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
        log.close()
    text = path.read_text(encoding="utf-8")
    assert "=== CADSwarm started" in text and "hello from the app" in text and "a warning" in text
    assert (tmp_path / "logs" / "cadswarm-app.log.1").stat().st_size > app_main.LOG_LIMIT


def test_missing_freecad_shows_a_dialog_and_exits_2(tmp_path, monkeypatch):
    monkeypatch.setenv("CADSWARM_ENGINE_HOME", str(tmp_path / "eng"))
    monkeypatch.setattr(app_main, "find_root", lambda: None)
    shown = []
    monkeypatch.setattr(app_main, "show_error", lambda title, text: shown.append((title, text)))
    args = app_main.parse_args(["--new-instance"])
    assert app_main.run(args, "C:/logs/app.log") == 2
    (title, text), = shown
    assert "FreeCAD" in title and "freecad.org" in text and "C:/logs/app.log" in text


def test_a_startup_crash_is_reported_not_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("CADSWARM_LOG", str(tmp_path / "app.log"))
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)

    def boom(args, log_file):
        raise RuntimeError("engine exploded")
    monkeypatch.setattr(app_main, "run", boom)
    shown = []
    monkeypatch.setattr(app_main, "show_error", lambda title, text: shown.append(text))
    import faulthandler
    try:
        assert app_main.main([]) == 1
    finally:
        faulthandler.disable()
        sys.stdout.log.close()
    assert "engine exploded" in shown[0] and str(tmp_path / "app.log") in shown[0]
    assert "RuntimeError: engine exploded" in (tmp_path / "app.log").read_text(encoding="utf-8")


def test_show_error_can_be_silenced(monkeypatch, capsys):
    monkeypatch.setenv("CADSWARM_NO_DIALOG", "1")
    app_main.show_error("Title", "Body")
    assert "Title: Body" in capsys.readouterr().err


def test_osascript_quoting():
    assert app_main._osascript_quote('say "hi" \\ bye') == '"say \\"hi\\" \\\\ bye"'


def test_handoff_to_the_running_app(tmp_path):
    home = str(tmp_path / "profile")
    assert instance.forward(home, ["a.FCStd"]) is False  # nobody running yet
    server = instance.Server(home)
    assert server.start()
    try:
        assert instance.Server(home).start() is False  # the channel belongs to the first instance
        assert instance.forward(home, ["a.FCStd", str(tmp_path / "b.step")]) is True
        assert instance.forward(home, []) is True  # a plain relaunch just raises the window
        import time
        deadline = time.time() + 5
        got = []
        while len(got) < 2 and time.time() < deadline:
            got += server.drain()
            time.sleep(0.02)
        assert got == [[os.path.abspath("a.FCStd"), str(tmp_path / "b.step")], []]
    finally:
        server.close()


def test_handoff_needs_the_profile_key(tmp_path):
    home = str(tmp_path / "profile")
    server = instance.Server(home)
    assert server.start()
    try:
        with open(os.path.join(home, instance.KEY_FILE), "wb") as f:
            f.write(os.urandom(32))  # a client with the wrong key
        assert instance.forward(home, ["evil.FCStd"]) is False
        assert server.drain() == []
    finally:
        server.close()


def test_profiles_have_separate_channels(tmp_path):
    assert instance.address(str(tmp_path / "a"))[0] != instance.address(str(tmp_path / "b"))[0]


@pytest.mark.skipif(sys.platform == "win32", reason="Linux layout")
def test_finds_the_appimage_the_linux_installer_unpacks(tmp_path, monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    for v in ("1.0.2", "1.1.4"):
        (tmp_path / "CadSwarm" / f"FreeCAD-{v}" / "squashfs-root" / "usr" / "lib").mkdir(parents=True)
    roots = engine.candidate_roots(env={}, platform="linux")
    assert roots[0].endswith(os.path.join("FreeCAD-1.1.4", "squashfs-root", "usr"))
