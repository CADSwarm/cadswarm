import os

from cadswarm.app import engine


def _fake_install(tmp_path, name="FreeCAD 1.1"):
    root = tmp_path / name
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "FreeCAD.pyd").write_text("")
    (root / "bin" / "FreeCADGui.pyd").write_text("")
    return root


def test_explicit_executable_path_resolves_to_install_root(tmp_path):
    root = _fake_install(tmp_path)
    exe = root / "bin" / "freecad.exe"
    exe.write_text("")
    assert engine.find_freecad({"CADSWARM_FREECAD": str(exe)}, "win32") == str(root)


def test_windows_search_prefers_newest_version(tmp_path):
    progs = tmp_path / "Programs"
    progs.mkdir()
    _fake_install(progs, "FreeCAD 1.0")
    newest = _fake_install(progs, "FreeCAD 1.1")
    found = engine.find_freecad({"LOCALAPPDATA": str(tmp_path)}, "win32")
    assert found == str(newest)


def test_folder_without_gui_library_is_not_an_engine(tmp_path):
    root = tmp_path / "FreeCAD 1.1"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "FreeCAD.pyd").write_text("")  # headless build only
    assert engine.find_freecad({"CADSWARM_FREECAD": str(root)}, "win32") is None


def test_params_silence_start_page_and_first_run():
    keys = {(g, k): v for g, _, k, v in engine.ENGINE_PARAMS}
    assert keys[("BaseApp/Preferences/Mod/Start", "ShowOnStartup")] is False
    assert keys[("BaseApp/Preferences/Mod/Start", "FirstStart2024")] is False


def test_apply_params_writes_typed_values():
    calls = []

    class Group:
        def __getattr__(self, name):
            return lambda k, v: calls.append((name, k, v))

    class FakeFreeCAD:
        @staticmethod
        def ParamGet(path):
            assert path.startswith("User parameter:BaseApp/")
            return Group()

    engine.apply_params(FakeFreeCAD, [("BaseApp/X", "Bool", "A", True), ("BaseApp/X", "Int", "B", 3)])
    assert calls == [("SetBool", "A", True), ("SetInt", "B", 3)]


def test_prepare_points_freecad_settings_at_our_app_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = _fake_install(tmp_path)
    monkeypatch.setattr(os, "add_dll_directory", lambda d: None, raising=False)
    monkeypatch.setattr("sys.path", list(__import__("sys").path))
    home = engine.prepare(str(root))
    assert os.environ["FREECAD_USER_HOME"] == home
    assert home.startswith(str(tmp_path / "appdata"))
    assert os.path.isdir(home)
