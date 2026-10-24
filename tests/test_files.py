"""Shared project files: sandboxing, no silent overwrites, nothing ever lost."""
import base64
import os
import sqlite3

import pytest

from cadswarm.hub.core import Hub, HubError
from cadswarm.hub.files import unique_path
from cadswarm.paths import project_layout, resolve_project_folder
from test_core import Clock, FakeWorker


@pytest.fixture
def env(tmp_path):
    lay = project_layout(tmp_path / "Phone stand")
    os.makedirs(lay["internal"])
    clock = Clock()
    hub = Hub(FakeWorker(), lay["db"], "secret", clock=clock, layout=lay)
    a = hub.join("secret", "alice-claude")["participant_id"]
    b = hub.join("secret", "bob-codex")["participant_id"]
    return hub, lay, clock, a, b


@pytest.mark.parametrize("bad", [
    "../outside.md", "parts/../../x.md", "/etc/passwd.txt", "C:/Windows/x.txt", "C:\\Windows\\x.txt",
    "~/x.md", ".git/config.txt", "parts/.hidden.md", "CON.txt", "parts/aux.md", "a<b>.md", "trailing. /x.md",
    "noext", "evil.exe", "script.bat", "a/" * 9 + "deep.md", "",
])
def test_paths_cannot_escape_or_be_unsafe(env, bad):
    hub, lay, _, a, _ = env
    with pytest.raises(HubError) as e:
        hub.save_file(a, bad, text="x")
    assert e.value.status in (400, 409) and e.value.code in ("bad_path", "bad_type")
    assert not os.path.exists(os.path.join(lay["root"], "outside.md"))


def test_symlink_or_junction_escape_is_refused(env, tmp_path):
    hub, lay, _, a, _ = env
    outside = tmp_path / "secret-place"
    outside.mkdir()
    link = os.path.join(lay["files"], "sneaky")
    try:
        if os.name == "nt":  # junctions need no admin rights, so they are the realistic escape on Windows
            import _winapi
            _winapi.CreateJunction(str(outside), link)
        else:
            os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("links not permitted for this user")
    with pytest.raises(HubError) as e:
        hub.save_file(a, "sneaky/x.md", text="pwned")
    assert e.value.code == "bad_path" and not (outside / "x.md").exists()


def test_subfolders_are_created_and_listed(env):
    hub, lay, _, a, _ = env
    r = hub.save_file(a, "parts\\base/notes.md", text="# Base\n120 x 80 mm")
    assert r == {"path": "parts/base/notes.md", "version": 1, "size": 18, "replaced": False,
                 "previous_author": None, "renamed_from": None}
    hub.make_folder(a, "parts/lid")
    listing = hub.list_files()
    assert [f["path"] for f in listing["files"]] == ["parts/base/notes.md"]
    assert {"parts", "parts/base", "parts/lid"} <= set(listing["folders"])
    assert open(os.path.join(lay["files"], "parts", "base", "notes.md"), encoding="utf-8").read().startswith("# Base")


def test_existing_files_are_never_overwritten_by_default(env):
    hub, lay, _, a, b = env
    hub.save_file(a, "notes.md", text="alice's notes")
    with pytest.raises(HubError) as e:
        hub.save_file(b, "notes.md", text="bob's notes")
    assert e.value.status == 409 and e.value.code == "file_exists" and e.value.data["author"] == "alice-claude"
    assert hub.read_file("notes.md")["text"] == "alice's notes"
    r = hub.save_file(b, "notes.md", text="bob's notes", if_exists="new_name")
    assert r["path"] == "notes-2.md" and r["renamed_from"] == "notes.md"
    assert hub.read_file("notes.md")["text"] == "alice's notes"


def test_replace_needs_the_current_version_and_keeps_history(env):
    hub, _, clock, a, b = env
    hub.save_file(a, "plan.md", text="v1")
    with pytest.raises(HubError) as e:
        hub.save_file(b, "plan.md", text="v2", if_exists="replace")  # no expected_version
    assert e.value.code == "stale_version"
    clock.t += 1
    r = hub.save_file(b, "plan.md", text="v2", if_exists="replace", expected_version=1)
    assert r["version"] == 2 and r["previous_author"] == "alice-claude"
    with pytest.raises(HubError):  # alice still thinks it's v1: refused, bob's work survives
        hub.save_file(a, "plan.md", text="alice v2", if_exists="replace", expected_version=1)
    assert hub.read_file("plan.md")["text"] == "v2"
    assert any("replaced your file plan.md" in m["body"] for m in hub.inbox(a))
    hist = hub.file_history("plan.md")
    assert [h["version"] for h in hist["older_versions"]] == [1]
    clock.t += 1
    hub.restore_file(a, "plan.md", 1)
    assert hub.read_file("plan.md") | {} and hub.read_file("plan.md")["text"] == "v1"
    assert hub.read_file("plan.md")["version"] == 3
    assert [h["version"] for h in hub.file_history("plan.md")["older_versions"]] == [1, 2]


def test_edits_made_outside_cadswarm_are_detected_and_preserved(env):
    hub, lay, clock, a, _ = env
    hub.save_file(a, "bom.csv", text="part,qty\nbase,1\n")
    with open(os.path.join(lay["files"], "bom.csv"), "w", encoding="utf-8") as f:
        f.write("part,qty\nbase,1\nscrew,4\n")  # a human edited it in Excel
    with pytest.raises(HubError) as e:
        hub.save_file(a, "bom.csv", text="part,qty\n", if_exists="replace", expected_version=1)
    assert e.value.code == "stale_version" and e.value.data["author"] == "outside CadSwarm"
    clock.t += 1
    hub.save_file(a, "bom.csv", text="part,qty\nbase,2\n", if_exists="replace", expected_version=2)
    hist = hub.file_history("bom.csv")["older_versions"]
    assert hist[-1]["author"] == "outside CadSwarm"  # the human's edit is in history, not lost


def test_move_and_delete_are_reversible(env):
    hub, lay, clock, a, _ = env
    hub.save_file(a, "a.md", text="A")
    hub.save_file(a, "b.md", text="B")
    with pytest.raises(HubError) as e:
        hub.move_file(a, "a.md", "b.md")
    assert e.value.code == "file_exists" and hub.read_file("b.md")["text"] == "B"
    hub.move_file(a, "a.md", "parts/a.md")
    assert hub.read_file("parts/a.md")["text"] == "A"
    clock.t += 1
    r = hub.delete_file(a, "b.md")
    assert r["recoverable"] and not os.path.exists(os.path.join(lay["files"], "b.md"))
    assert os.listdir(lay["trash"])
    hub.restore_file(a, "b.md", 1)
    assert hub.read_file("b.md")["text"] == "B"


def test_binary_files_and_limits(env):
    hub, _, _, a, _ = env
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
    hub.save_file(a, "renders/front.png", base64_data=png)
    assert base64.b64decode(hub.read_file("renders/front.png")["base64"]).startswith(b"\x89PNG")
    with pytest.raises(HubError) as e:
        hub.save_file(a, "notes.png", text="text into a png")
    assert e.value.code == "bad_type"
    with pytest.raises(HubError) as e:
        hub.save_file(a, "huge.md", text="x" * (5 * 1024 * 1024 + 1))
    assert e.value.code == "too_big"


def test_writes_are_atomic_no_temp_left_behind(env):
    hub, lay, _, a, _ = env
    hub.save_file(a, "x.md", text="hello")
    leftovers = [n for _, _, names in os.walk(lay["files"]) for n in names if "tmp" in n]
    assert leftovers == []


def test_snapshots_and_unique_names_never_collide(env, tmp_path):
    hub, lay, _, a, _ = env
    hub.worker.doc_path = str(tmp_path / "model.FCStd")
    with open(hub.worker.doc_path, "w") as f:
        f.write("model")
    first, second = hub.snapshot(a, "before lid"), hub.snapshot(a, "before lid")
    assert first["file"] != second["file"] and first["file"].startswith("snapshots/")
    assert len(os.listdir(lay["snapshots"])) == 2
    d = tmp_path / "out"
    p1 = unique_path(str(d), "model.step")
    open(p1, "w").close()
    assert unique_path(str(d), "model.step").endswith("model-2.step")


def test_file_saves_show_in_team_context(env):
    hub, _, _, a, b = env
    hub.save_file(a, "parts/base/notes.md", text="hi")
    assert "saved files/parts/base/notes.md (v1)" in hub.context(b)


def test_resolve_project_folder_never_mixes_with_other_files(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert resolve_project_folder(str(empty), "Phone stand") == (str(empty), "new")
    busy = tmp_path / "Downloads"
    busy.mkdir()
    (busy / "taxes.pdf").write_text("x")
    folder, how = resolve_project_folder(str(busy), "Phone stand")
    assert how == "inside" and folder == str(busy / "Phone stand")
    lay = project_layout(folder)
    os.makedirs(lay["internal"])
    sqlite3.connect(lay["db"]).close()
    assert resolve_project_folder(str(busy), "Phone stand") == (folder, "existing")
    assert resolve_project_folder(folder, "anything") == (folder, "existing")


# ---- hardening: case-insensitive names, quota, error mapping, reserved names, restore guard, trash

def disk_names(lay):
    return sorted(os.listdir(lay["files"]))


def test_other_casing_cannot_bypass_the_version_check(env):
    hub, lay, clock, a, b = env
    hub.save_file(a, "notes.md", text="v1")
    for v in (1, 2):
        hub.save_file(a, "notes.md", text=f"v{v + 1}", if_exists="replace", expected_version=v)
    with pytest.raises(HubError) as e:  # bob read v1 long ago and uses another casing
        hub.save_file(b, "NOTES.md", text="clobber", if_exists="replace", expected_version=1)
    assert e.value.code == "stale_version" and e.value.data["currentVersion"] == 3
    with pytest.raises(HubError) as e:
        hub.save_file(b, "Notes.md", text="clobber")
    assert e.value.code == "file_exists"
    assert hub.read_file("NOTES.MD")["text"] == "v3" and disk_names(lay) == ["notes.md"]
    hub.save_file(b, "NOTES.md", text="ok", if_exists="replace", expected_version=3)  # right version: fine, one row
    assert [(f["path"], f["version"]) for f in hub.list_files()["files"]] == [("notes.md", 4)]
    assert disk_names(lay) == ["notes.md"]


def test_casing_is_folded_for_folders_move_history_and_delete(env):
    hub, lay, _, a, _ = env
    hub.save_file(a, "Parts/Base.md", text="x")
    hub.save_file(a, "parts/base.md", text="y", if_exists="new_name")
    assert [f["path"] for f in hub.list_files()["files"]] == ["Parts/Base-2.md", "Parts/Base.md"]
    with pytest.raises(HubError) as e:
        hub.move_file(a, "parts/BASE.md", "PARTS/base-2.MD")
    assert e.value.code == "file_exists"
    assert hub.file_history("PARTS/BASE.MD")["current"]["version"] == 1
    hub.delete_file(a, "parts/base.MD")
    assert [f["path"] for f in hub.list_files()["files"]] == ["Parts/Base-2.md"]


def test_short_8dot3_names_are_refused(env):
    hub, _, _, a, _ = env
    hub.save_file(a, "draft~2.md", text="x")  # a tilde name that isn't an alias of anything is fine
    hub.save_file(a, "longfilename.md", text="long")
    if os.name != "nt":
        pytest.skip("8.3 aliases are an NTFS thing")
    import ctypes
    long_path = hub.files.full("longfilename.md")
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(long_path, buf, len(buf))
    short = os.path.basename(buf.value)
    if not short or short.lower() == "longfilename.md":
        pytest.skip("8.3 name generation is off on this volume")
    with pytest.raises(HubError) as e:
        hub.save_file(a, short, text="x", if_exists="replace", expected_version=1)
    assert e.value.code == "bad_path"


def test_history_and_trash_count_toward_the_quota(env):
    hub, _, _, a, _ = env
    hub.files.storage.update({"project_limit_gb": 100 / 1024 ** 3})
    hub.save_file(a, "big.md", text="x" * 40)
    hub.save_file(a, "big.md", text="y" * 40, if_exists="replace", expected_version=1)  # 40 live + 40 history
    with pytest.raises(HubError) as e:  # live size alone (40+40) would fit; history makes it 120
        hub.save_file(a, "big.md", text="z" * 40, if_exists="replace", expected_version=2)
    assert e.value.code == "quota" and hub.read_file("big.md")["text"] == "y" * 40
    assert [h["version"] for h in hub.file_history("big.md")["older_versions"]] == [1]  # no orphan history row
    with pytest.raises(HubError) as e:  # restore can't sneak past the quota either
        hub.restore_file(a, "big.md", 1)
    assert e.value.code == "quota"


def test_odd_filesystem_states_are_clean_errors(env):
    hub, lay, _, a, _ = env
    hub.make_folder(a, "x.md")
    for call in (lambda: hub.read_file("x.md"), lambda: hub.save_file(a, "x.md", text="t"),
                 lambda: hub.delete_file(a, "x.md")):
        with pytest.raises(HubError) as e:
            call()
        assert e.value.status == 409 and e.value.code == "is_folder"
    hub.save_file(a, "f.md", text="file")
    hub.save_file(a, "g.md", text="g")
    for call in (lambda: hub.save_file(a, "f.md/b.md", text="t"), lambda: hub.move_file(a, "g.md", "f.md/g.md"),
                 lambda: hub.make_folder(a, "f.md")):
        with pytest.raises(HubError) as e:
            call()
        assert e.value.status == 409 and e.value.code == "not_a_folder"
    assert hub.read_file("g.md")["text"] == "g" and hub.file_history("g.md")["older_versions"] == []  # no orphan row
    for call in (lambda: hub.save_file(a, "bad\ud800.md", text="t"), lambda: hub.save_file(a, "ok.md", text="\ud800")):
        with pytest.raises(HubError) as e:
            call()
        assert e.value.status == 400


@pytest.mark.parametrize("bad", ["abc", True, -1, 1.5, [1]])
def test_expected_version_must_be_a_real_number(env, bad):
    hub, _, _, a, _ = env
    hub.save_file(a, "v.md", text="1")
    with pytest.raises(HubError) as e:
        hub.save_file(a, "v.md", text="2", if_exists="replace", expected_version=bad)
    assert e.value.status == 400 and e.value.code == "bad_args"


@pytest.mark.parametrize("bad", ["CON .txt", "aux .md", "conin$.txt", "CONOUT$.md", "COM\u00b9.txt", "lpt\u00b3.md",
                                 "Nul..txt", "parts/prn .md"])
def test_more_reserved_names_are_refused(env, bad):
    hub, _, _, a, _ = env
    with pytest.raises(HubError) as e:
        hub.save_file(a, bad, text="x")
    assert e.value.code == "bad_path"


def test_restore_can_require_the_version_you_last_saw(env):
    hub, _, clock, a, b = env
    hub.save_file(a, "plan.md", text="one")
    hub.save_file(a, "plan.md", text="two", if_exists="replace", expected_version=1)
    files = hub._files()
    with pytest.raises(HubError):
        hub._file_call(files.restore, "plan.md", 1, "bob-codex", expected_version=1)  # it is at v2 now
    assert hub.read_file("plan.md")["text"] == "two"
    r = hub._file_call(files.restore, "plan.md", 1, "bob-codex", expected_version=2)
    assert r["version"] == 3 and hub.read_file("plan.md")["text"] == "one"


def test_trash_and_history_names_never_collide(env):
    hub, lay, _, a, _ = env
    hub.save_file(a, "a/b.md", text="slash")
    hub.save_file(a, "a__b.md", text="underscores")
    hub.delete_file(a, "a/b.md")
    hub.delete_file(a, "a__b.md")  # same clock tick, same flattened trash name
    hub.restore_file(a, "a/b.md", 1)
    hub.delete_file(a, "a/b.md")  # same path, same second, same version number again
    trashed = sorted(open(os.path.join(lay["trash"], n)).read() for n in os.listdir(lay["trash"]))
    assert trashed == ["slash", "slash", "underscores"]
    stored = [h for h in hub._files().db.execute("SELECT stored_as FROM file_history WHERE path='a/b.md'")]
    assert len({r["stored_as"] for r in stored}) == len(stored) == 2


def test_only_one_leading_files_prefix_is_stripped(env):
    hub, lay, _, a, _ = env
    assert hub.save_file(a, "files/x.md", text="1")["path"] == "x.md"
    assert hub.save_file(a, "files/files/x.md", text="2")["path"] == "files/x.md"
    assert os.path.exists(os.path.join(lay["files"], "files", "x.md"))
