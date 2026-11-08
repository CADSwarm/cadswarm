import os

from cadswarm.app import branded


def test_rebrand_text_replaces_name_and_version():
    assert branded.rebrand_text("FreeCAD 1.1.4") == "CADSwarm"
    assert branded.rebrand_text("[*] Bracket - FreeCAD 1.1.4 (Revision 1234)") == "[*] Bracket - CADSwarm"
    assert branded.rebrand_text("About FreeCAD") == "About CADSwarm"
    assert branded.rebrand_text("FreeCADGui") == "FreeCADGui"  # identifiers are left alone
    assert branded.rebrand_text("") == ""
    assert branded.rebrand_text(None) is None


def test_rgba_uint():
    assert branded.rgba_uint("#FF5F00") == 0xFF5F00FF
    assert branded.rgba_uint("111111", 0) == 0x11111100


def test_theme_overrides_use_tokens():
    light = branded.theme_overrides("light")
    dark = branded.theme_overrides("dark")
    assert light["AccentColor"] == branded.colours("light")["signal"]
    assert dark["AccentColor"] == branded.colours("dark")["signal"]
    assert light["InputFieldBorderRadius"] == "0px"


def test_write_theme_overrides_existing_keys(tmp_path):
    root = tmp_path / "fc"
    params = root / "data" / "Gui" / "Stylesheets" / "parameters"
    params.mkdir(parents=True)
    (params / "FreeCAD Light.yaml").write_text('PrimaryColor: "#F0F0F0"\nOther: "x"\n', encoding="utf-8")
    path = branded.write_theme(str(root), str(tmp_path / "home"), "light")
    text = open(path, encoding="utf-8").read()
    assert 'Other: "x"' in text
    assert text.count("PrimaryColor:") == 1
    assert f'PrimaryColor: "{branded.colours("light")["chrome"]}"' in text
    assert 'AccentColor:' in text  # missing keys are appended


def test_brand_params_switch_off_start_page_and_point_at_theme(tmp_path):
    params = branded.brand_params(str(tmp_path / "t.yaml"))
    as_dict = {(g, k): v for g, _, k, v in params}
    assert as_dict[("BaseApp/Preferences/Mod/Start", "ShowOnStartup")] is False
    assert as_dict[("BaseApp/Preferences/MainWindow", "ThemeStyleParametersFile")] == str(tmp_path / "t.yaml")
    kinds = {t for _, t, _, _ in params}
    assert kinds <= {"Bool", "String", "Int", "Unsigned"}


def test_link_addon_keeps_real_copy(tmp_path):
    home = tmp_path / "home"
    dest = home / "Mod" / "CadSwarm"
    dest.mkdir(parents=True)
    (dest / "InitGui.py").write_text("# installed copy", encoding="utf-8")
    assert branded.link_addon(str(home), repo=str(tmp_path)) == str(dest)
    assert (dest / "InitGui.py").read_text(encoding="utf-8") == "# installed copy"


def test_link_addon_links_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "InitGui.py").write_text("# repo", encoding="utf-8")
    dest = branded.link_addon(str(tmp_path / "home"), repo=str(repo))
    assert os.path.exists(os.path.join(dest, "InitGui.py"))
