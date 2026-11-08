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


import pytest  # noqa: E402


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_theme_text_is_readable_on_every_background(mode):
    over = branded.theme_overrides(mode)
    for fg, bg in branded.TEXT_PAIRS:
        ratio = branded.contrast(over[fg], over[bg])
        assert ratio >= 4.5, (mode, fg, bg, ratio)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_active_tab_never_follows_the_3d_view_colour(mode):
    over = branded.theme_overrides(mode)
    assert not over["ActiveTabBackgroundColor"].startswith("@")
    assert not over["3DViewBackgroundRefColor"].startswith("@")
    assert over["ButtonTopBackgroundColor"] == over["ButtonBottomBackgroundColor"]  # flat, no gradient


def test_blend_and_contrast():
    assert branded.blend("#000000", "#FFFFFF", 0) == "#000000"
    assert branded.blend("#000000", "#FFFFFF", 1) == "#FFFFFF"
    assert branded.blend("#000000", "#FFFFFF", 0.5) == "#808080"
    assert round(branded.contrast("#000000", "#FFFFFF"), 1) == 21.0
    assert branded.contrast("#111111", "#111111") == 1.0


def test_square_corners_keeps_radio_dots_round():
    qss = ("QPushButton { border-radius: 3px; color: red; }\n"
           "QRadioButton::indicator { border-radius: 6.55px; }\n"
           "QTabBar::tab:top { border-top-left-radius: 3px; border-top-right-radius: 7px; }")
    out = branded.square_corners(qss)
    assert "QPushButton { border-radius: 0px; color: red; }" in out
    assert "border-radius: 6.55px" in out
    assert "border-top-left-radius: 0px" in out and "border-top-right-radius: 0px" in out


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_write_stylesheet_and_brand_params_use_it(tmp_path, mode):
    root = tmp_path / "fc"
    sheets = root / "data" / "Gui" / "Stylesheets"
    (sheets / "parameters").mkdir(parents=True)
    (sheets / "FreeCAD.qss").write_text("QToolTip { border-radius: 5px; }\n", encoding="utf-8")
    theme = branded.write_theme(str(root), str(tmp_path / "home"), mode)
    qss = branded.stylesheet_path(str(tmp_path / "home"), mode)
    text = open(qss, encoding="utf-8").read()
    assert "border-radius: 0px" in text and branded.colours(mode)["signal"] in text
    as_dict = {(g, k): v for g, _, k, v in branded.brand_params(theme, mode)}
    assert as_dict[("BaseApp/Preferences/MainWindow", "StyleSheet")] == qss


def test_brand_params_fall_back_to_freecad_qss(tmp_path):
    as_dict = {(g, k): v for g, _, k, v in branded.brand_params(str(tmp_path / "Themes" / "x.yaml"))}
    assert as_dict[("BaseApp/Preferences/MainWindow", "StyleSheet")] == "FreeCAD.qss"


def test_ico_from_pngs_directory():
    import struct
    from cadswarm.app import app_icon
    png = b"\x89PNG\r\n\x1a\nfake"
    data = app_icon.ico_from_pngs({256: png + b"big", 16: png})
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, 2)
    first = struct.unpack("<BBBBHHII", data[6:22])
    second = struct.unpack("<BBBBHHII", data[22:38])
    assert first[0] == 16 and second[0] == 0  # 0 means 256
    assert data[first[7]:first[7] + first[6]] == png
    assert data[second[7]:second[7] + second[6]] == png + b"big"
    with pytest.raises(ValueError):
        app_icon.ico_from_pngs({16: b"GIF89a"})


def test_built_icon_files_exist():
    from cadswarm.app import app_icon
    assert os.path.exists(app_icon.ICO) and os.path.exists(app_icon.PNG)
    assert [s for s, _ in app_icon.icon_files()] == list(app_icon.PNG_SIZES)
    with open(app_icon.ICO, "rb") as f:
        assert f.read(4) == b"\x00\x00\x01\x00"
    assert branded.BRAND_CONFIG["WindowIcon"] == branded.LOGO and os.path.exists(branded.LOGO)


def test_about_text_names_cadswarm_and_credits_the_engine():
    text = branded.about_html("0.1.0", "1.1.4")
    assert text.startswith("<h2") and "CADSwarm" in text and "0.1.0" in text
    assert "FreeCAD 1.1.4" in text and "LGPL" in text and "unmodified" in text
    assert branded.engine_version(["1", "1", "4", "20260928", "git"]) == "1.1.4"
    assert branded.engine_version(None) == ""
    assert branded.cadswarm_version()  # read from pyproject.toml


def test_cadswarm_version_reads_pyproject(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\nname = "x"\nversion = "2.3.4"\n', encoding="utf-8")
    assert branded.cadswarm_version(str(p)) == "2.3.4"
    assert branded.cadswarm_version(str(tmp_path / "missing.toml")) == ""
