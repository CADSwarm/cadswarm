"""The components library on the host: storing parts, standard parts and fits, search, recommendations,
suggested settings, URL import rules and packs. No FreeCAD needed (test_worker_library.py covers that)."""
import base64
import io
import json

import pytest

from cadswarm import settings as team_settings
from cadswarm.helper import Project, handle
from cadswarm.library import fetch, pack, search, standard, suggest
from cadswarm.library.recommend import recommend

PERSON = {"id": 3, "name": "carol", "kind": "human"}
AGENT = {"id": 1, "name": "alice-claude", "kind": "agent"}


@pytest.fixture
def project(tmp_path):
    return Project(str(tmp_path))


def op(project, op_name, **args):
    r = handle(project, {"id": 1, "op": op_name, "args": args})
    if not r["ok"]:
        raise AssertionError(f"{op_name}: {r['code']}: {r['error']}")
    return r["result"]


def add(project, data=b"ISO-10303-21; plate", filename="Motor plate.step", **kw):
    return op(project, "library_add", base64=base64.b64encode(data).decode(), filename=filename, _actor=PERSON, **kw)


# ------------------------------------------------------------------ settings spec

def test_every_default_passes_its_own_check_and_values_overlay_defaults():
    for s in team_settings.specs():
        team_settings.check(s["key"], s["default"])
    vals = team_settings.effective([{"setting": "tol_clearance", "num": 0.35, "text": ""},
                                    {"setting": "hole_clearance_class", "num": 0, "text": "close"},
                                    {"setting": "gone_setting", "num": 1, "text": ""}])
    assert vals["tol_clearance"] == 0.35 and vals["hole_clearance_class"] == "close"
    assert vals["min_wall"] == 1.2 and "gone_setting" not in vals
    with pytest.raises(ValueError):
        team_settings.check("tol_clearance", 9)
    with pytest.raises(ValueError):
        team_settings.check("hole_clearance_class", "snug")


def test_fea_presets_cover_every_material_choice():
    from cadswarm.fea import PRESETS
    assert set(team_settings.spec("default_material")["choices"]) <= set(PRESETS)


# --------------------------------------------------------------------- the store

def test_adding_a_part_mirrors_a_row_and_asks_freecad_to_measure_it(project):
    r = add(project, category="motion", tags="NEMA17, motor ,motor")
    row = r["_components"][0]
    assert row["id"].startswith("c_motor_plate_") and row["category"] == "motion"
    assert row["tags"] == ["motor", "nema17"] and row["source"] == "upload" and row["author"] == "carol"
    assert r["_analyze"][0]["id"] == row["id"] and r["_analyze"][0]["path"].endswith("part.step")


def test_the_same_file_is_not_stored_twice(project):
    first = add(project)
    again = add(project, filename="copy.step")
    assert again["duplicates"] == ["Motor plate"] and "_components" not in again
    assert len(op(project, "library_search")["results"]) == 1 + len(standard.families())
    assert first["_components"][0]["id"] == again["components"][0]["id"]


def test_only_part_files_are_accepted(project):
    r = handle(project, {"id": 1, "op": "library_add",
                         "args": {"base64": base64.b64encode(b"x").decode(), "filename": "notes.exe", "_actor": PERSON}})
    assert not r["ok"] and r["code"] == "bad_type"


def test_an_upload_in_the_inbox_moves_into_the_library(project):
    project.files.save("library-inbox/bracket.stl", "carol", base64_data=base64.b64encode(b"solid x").decode())
    r = op(project, "library_add", path="library-inbox/bracket.stl", _actor=PERSON)
    assert r["_components"][0]["format"] == "stl"
    assert [f for f in project.files.listing("library-inbox").get("files", []) if f.get("path", "").endswith(".stl")] == []


def test_finalize_records_measurements_and_the_settings_they_imply(project):
    cid = add(project)["_components"][0]["id"]
    r = op(project, "library_finalize", items=[{"id": cid, "analysis": {
        "bbox": [0, 0, 0, 12, 8, 3], "volume": 200.0,
        "features": {"holes": [{"d": 3.2, "count": 2}, {"d": 1.0, "count": 1}]}}}])
    row = r["_components"][0]
    assert row["bbox"] == [0, 0, 0, 12, 8, 3] and row["volume"] == 200.0
    got = {s["setting"]: s["value"] for s in row["suggest"]}
    assert got["hole_clearance_class"] == "close"   # 3.2 mm is ISO 273 close for M3
    assert got["mesh_deviation"] < 0.2               # 1 mm holes need a finer mesh
    assert got["edit_decimals"] == 3                 # a 12 mm part needs finer editing


def test_people_delete_parts_and_agents_cannot(project):
    cid = add(project)["_components"][0]["id"]
    r = handle(project, {"id": 1, "op": "library_delete", "args": {"id": cid, "_actor": AGENT}})
    assert r["code"] == "humans_only"
    assert op(project, "library_delete", id=cid, _actor=PERSON)["_component_deletes"] == [cid]
    assert op(project, "library_search", query="motor")["results"] == []


def test_ids_that_could_escape_the_library_are_refused(project):
    r = handle(project, {"id": 1, "op": "library_get", "args": {"id": "../../model"}})
    assert not r["ok"] and r["code"] == "bad_args"


# ----------------------------------------------------------------- standard parts

def test_standard_parts_resolve_sizes_and_reject_ones_not_sold():
    r = standard.resolve("iso4762", "m3", 12)
    assert r["size"] == "M3" and r["length"] == 12 and r["label"] == "M3x12 socket head cap screw"
    with pytest.raises(ValueError, match="nearest is 12"):
        standard.resolve("iso4762", "M3", 13)
    with pytest.raises(ValueError, match="needs a length"):
        standard.resolve("iso4762", "M3")
    with pytest.raises(ValueError):
        standard.resolve("ball_bearing", "609")


def test_holes_follow_the_team_fits():
    vals = team_settings.defaults()
    assert standard.hole_for("iso4762", "M3", vals)["diameter"] == 3.4
    assert standard.hole_for("iso4762", "M3", {**vals, "hole_clearance_class": "close"})["diameter"] == 3.2
    assert standard.hole_for("iso4762", "M3", vals, "thread")["diameter"] == 4.0  # heat-set insert hole
    assert standard.hole_for("iso4762", "M3", {**vals, "thread_mode": "tapped"}, "thread")["diameter"] == 2.5
    bearing = standard.hole_for("ball_bearing", "608", {**vals, "tol_press_fit": -0.02})
    assert bearing["diameter"] == 21.98 and bearing["fit"] == "tol_press_fit"


def test_a_screw_hole_purpose_word_never_silently_becomes_a_seat():
    vals = team_settings.defaults()
    # "clearance" used to fall through to "seat" and give an M3 screw a 3.0 mm hole.
    assert standard.hole_for("iso4762", "M3", vals, "clearance")["diameter"] == 3.4
    assert standard.hole_for("iso4762", "M3", vals, "Threaded")["diameter"] == 4.0  # follows thread_mode
    with pytest.raises(ValueError, match="through, thread or seat"):
        standard.hole_for("iso4762", "M3", vals, "loose")


# ----------------------------------------------------------- search and recommend

def test_search_matches_words_tags_and_dimension_constraints():
    comps = standard.virtual_components()
    names = lambda hits: [c["name"] for _, c, _ in hits]
    assert names(search.search(comps, "bearing"))[0] == "Deep groove ball bearing"
    hits = search.search(comps, "", constraints={"bore_d": 8})
    assert [c["std"]["family"] for _, c, _ in hits] == ["ball_bearing", "iso8734"]
    assert hits[0][2] == ["688", "608"]
    assert "Hex nut" in names(search.search(comps, "nut", constraints={"thread": "M5"}))
    assert search.search(comps, "zzz") == []


def test_team_parts_must_fit_a_box_in_any_orientation():
    part = {"id": "c_x", "name": "Bracket", "category": "structural", "bbox": [0, 0, 0, 50, 10, 20], "source": "upload"}
    assert search.search([part], "", constraints={"max_dims": [20, 50, 10]})
    assert not search.search([part], "", constraints={"max_dims": [20, 40, 10]})


def test_recommend_picks_a_size_a_hole_and_how_to_place_it():
    vals = team_settings.defaults()
    r = recommend(standard.virtual_components(), "an 8 mm axle that spins", values=vals)
    top = r["recommendations"][0]
    assert top["family"] == "ball_bearing" and top["size"] == "608"
    assert top["insert_with"] == {"tool": "add_standard_part", "family": "ball_bearing", "size": "608"}
    assert top["hole"]["diameter"] == 22 - 0.05
    assert any(s["setting"] == "tol_press_fit" for s in top["suggest"])
    screws = recommend(standard.virtual_components(), "screw the lid down with M2.5", {"grip": 6}, vals)
    first = screws["recommendations"][0]
    assert first["size"] == "M2.5" and first["length"] == 10


def test_recommend_says_what_to_do_when_nothing_fits():
    r = recommend([], "a flux capacitor")
    assert r["recommendations"] == [] and "import_component_from_url" in r["note"]


def test_suggestions_skip_values_the_team_already_has():
    vals = {**team_settings.defaults(), "thread_mode": "heat_set_insert"}
    got = suggest.suggestions({"std": {"family": "iso4762", "size": "M3"}}, vals)
    assert got == []  # printed parts + screws -> inserts, which the team already uses
    metal = suggest.suggestions({"std": {"family": "iso4762", "size": "M3"}}, {**vals, "default_material": "aluminium_6061_t6"})
    assert metal == [{"setting": "thread_mode", "value": "tapped", "reason": metal[0]["reason"]}]


# ---------------------------------------------------------------- URL import

class FakeResponse(io.BytesIO):
    def __init__(self, data, url, headers=None):
        super().__init__(data)
        self.url, self.headers = url, headers or {}

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(data, final=None, headers=None):
    def open_(req, timeout=0):
        return FakeResponse(data, final or req.full_url, headers)
    return open_


def test_fetch_takes_https_part_files_and_rewrites_github_blob_links():
    seen = {}

    def open_(req, timeout=0):
        seen["url"] = req.full_url
        return FakeResponse(b"ISO-10303-21;", req.full_url)
    name, data, final = fetch.fetch("https://github.com/acme/parts/blob/main/m3%20nut.step", opener=open_,
                                    allow_private=True)
    assert seen["url"] == "https://raw.githubusercontent.com/acme/parts/main/m3%20nut.step"
    assert name == "m3 nut.step" and data == b"ISO-10303-21;"


@pytest.mark.parametrize("url,code", [("http://example.com/a.step", "bad_args"),
                                      ("https://example.com/page.html", "bad_type"),
                                      ("https://127.0.0.1/a.step", "not_allowed")])
def test_fetch_refuses_plain_http_web_pages_and_local_addresses(url, code):
    with pytest.raises(fetch.FetchError) as e:
        fetch.fetch(url, opener=opener_for(b"x"))
    assert e.value.code == code


def test_fetch_stops_at_the_size_limit_and_refuses_a_redirect_off_https():
    with pytest.raises(fetch.FetchError) as e:
        fetch.fetch("https://x.test/a.step", opener=opener_for(b"x" * 2000), allow_private=True, max_bytes=1000)
    assert e.value.code == "too_big"
    with pytest.raises(fetch.FetchError) as e:
        fetch.fetch("https://x.test/a.step", opener=opener_for(b"x", final="http://x.test/a.step"), allow_private=True)
    assert e.value.code == "not_allowed"


def test_import_url_through_the_helper_records_where_it_came_from(project):
    project.url_opener = opener_for(b"ISO-10303-21; pulley", headers={"Content-Disposition": 'attachment; filename="GT2 pulley.step"'})
    project.allow_private = True
    r = op(project, "library_import_url", url="https://vendor.test/dl?id=7", category="motion", _actor=AGENT)
    row = r["_components"][0]
    assert row["name"] == "GT2 pulley" and row["source"] == "url" and row["source_url"] == "https://vendor.test/dl?id=7"
    assert "license" in r["note"]


# -------------------------------------------------------------------- packs

def test_a_pack_round_trips_parts_and_carries_settings_as_suggestions_only(project, tmp_path):
    cid = add(project, category="bearing")["_components"][0]["id"]
    op(project, "library_finalize", items=[{"id": cid, "analysis": {"bbox": [0, 0, 0, 22, 22, 7], "volume": 1.0}}])
    out = op(project, "library_export_pack", name="Bearings", _actor=PERSON,
             _settings=[{"setting": "tol_press_fit", "num": 0.0, "text": ""}])
    assert out["parts"] == 1 and out["file"].startswith("library-packs/") and out["file"].endswith("Bearings.zip")
    data = project.files.raw(out["file"])[1]
    other = Project(str(tmp_path / "other"))
    r = op(other, "library_import_pack", base64=base64.b64encode(data).decode(), filename="b.zip", _actor=PERSON)
    assert [c["source"] for c in r["_components"]] == ["pack"]
    assert r["_components"][0]["bbox"] == [0, 0, 0, 22, 22, 7] and r["_analyze"] == []
    assert r["pack_settings"] == {"tol_press_fit": 0.0} and "suggest_setting" in r["note"]
    again = op(other, "library_import_pack", base64=base64.b64encode(data).decode(), filename="b.zip", _actor=PERSON)
    assert again["_components"] == [] and again["duplicates"] == ["Motor plate"]


def test_packs_that_are_not_ours_or_reach_outside_are_refused():
    with pytest.raises(pack.PackError):
        pack.read_pack(b"not a zip")
    buf = io.BytesIO()
    import zipfile
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": pack.FORMAT, "components": [{"file": "../evil.step"}]}))
    with pytest.raises(pack.PackError, match="Bad file path"):
        pack.read_pack(buf.getvalue())
