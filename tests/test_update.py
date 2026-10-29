"""Auto-update pieces that don't need FreeCAD or a network: release facts, the download's checks,
and swapping the workbench folder without ever leaving none behind."""
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import zipfile

import pytest

from cadswarm import update


class Response:
    """What `_urlopen` hands back: bytes that can also be read in chunks."""

    def __init__(self, body, total=None):
        self.body = body
        self.headers = {"Content-Length": str(len(body) if total is None else total)}

    def read(self, size=-1):
        if size is None or size < 0:
            body, self.body = self.body, b""
            return body
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buffer.getvalue()


def workbench_zip(version="0.1.18"):
    return zipped({
        "InitGui.py": "# the workbench's GUI entry point\n",
        "cadswarm/version.py": f'__version__ = "{version}"\n',
        "cadswarm/gui/open_cadswarm.py": "# opens CADSwarm\n",
    })


def release(**extra):
    base = {"tag": "v0.1.18", "version": "0.1.18", "url": "https://github.com/x/y/releases/tag/v0.1.18",
            "notes": "## What's Changed\n* a new thing\n", "published": "2026-10-27T00:00:00Z"}
    base.update(extra)
    return base


@pytest.fixture(autouse=True)
def private_app_dir(tmp_path, monkeypatch):
    """Never read or write the real app folder's cache during tests."""
    monkeypatch.setattr(update, "app_dir", lambda: str(tmp_path / "app"))
    (tmp_path / "app").mkdir()


# ------------------------------------------------------------------------------- versions

@pytest.mark.parametrize("text, expected", [
    ("v0.1.17", (0, 1, 17)), ("0.1.9", (0, 1, 9)), ("V2", (2,)), ("release-3.4.5", (3, 4, 5)),
])
def test_versions_are_read_from_tags(text, expected):
    assert update.parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "latest", "v"])
def test_a_non_version_is_plain_words(text):
    with pytest.raises(update.UpdateError):
        update.parse_version(text)


@pytest.mark.parametrize("candidate, current, newer", [
    ("v0.1.18", "0.1.17", True),
    ("v0.1.9", "0.1.10", False),   # text order would say 9 > 10
    ("v0.1.17", "v0.1.17", False),
    ("v0.2.0", "0.1.99", True),
    ("v0.1.17.1", "0.1.17", True),
    ("garbage", "0.1.17", False),
])
def test_is_newer_compares_numbers_not_text(candidate, current, newer):
    assert update.is_newer(candidate, current) is newer


def test_newer_than_installed_uses_the_stamped_version():
    assert update.newer_than_installed(release()) is True
    assert update.newer_than_installed(release(tag="v0.1.17", version="0.1.17")) is False


def test_updating_is_skipped_in_a_development_checkout(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / ".git").mkdir(parents=True)
    (tmp_path / "installed").mkdir()
    monkeypatch.delenv("CADSWARM_UPDATE", raising=False)
    assert update.updates_allowed(str(root)) is False
    assert update.updates_allowed(str(tmp_path / "installed")) is True
    monkeypatch.setenv("CADSWARM_UPDATE", "1")
    assert update.updates_allowed(str(root)) is True
    monkeypatch.setenv("CADSWARM_UPDATE", "0")
    assert update.updates_allowed(str(tmp_path / "installed")) is False


# ------------------------------------------------------------------------------- checking

def test_the_newest_release_is_read_and_reused_from_the_cache(monkeypatch):
    calls = []

    def answer(url):
        calls.append(url)
        assert url == update.API_LATEST
        return Response(json.dumps({
            "tag_name": "v0.1.18", "html_url": "https://github.com/x/y/releases/tag/v0.1.18",
            "body": "## What's Changed\n* fixed a thing\n", "published_at": "2026-10-27T00:00:00Z",
            "draft": False, "prerelease": False}).encode())

    monkeypatch.setattr(update, "_urlopen", answer)
    found = update.check_latest(force=True)
    assert found == {"tag": "v0.1.18", "version": "0.1.18",
                     "url": "https://github.com/x/y/releases/tag/v0.1.18",
                     "notes": "## What's Changed\n* fixed a thing",
                     "published": "2026-10-27T00:00:00Z"}
    assert update.newer_than_installed(found) is True

    monkeypatch.setattr(update, "_urlopen", lambda url: pytest.fail("the cache should answer this"))
    assert update.check_latest() == found  # the periodic check reuses the hour-old answer
    assert len(calls) == 1


def test_forcing_a_check_ignores_the_cache(monkeypatch):
    asked = []

    def answer(url):
        asked.append(url)
        return Response(json.dumps({"tag_name": f"v0.1.{18 + len(asked)}", "html_url": "", "body": "",
                                    "published_at": ""}).encode())

    monkeypatch.setattr(update, "_urlopen", answer)
    first = update.check_latest(force=True)
    second = update.check_latest(force=True)
    assert len(asked) == 2 and first["version"] != second["version"]


def test_offline_says_so_in_plain_words(monkeypatch):
    def offline(url):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(update, "_urlopen", offline)
    with pytest.raises(update.UpdateError) as e:
        update.check_latest(force=True)
    assert "internet" in str(e.value)


def test_github_errors_are_plain_words(monkeypatch):
    def refused(url):
        raise urllib.error.HTTPError(url, 403, "rate limited", {}, None)

    monkeypatch.setattr(update, "_urlopen", refused)
    with pytest.raises(update.UpdateError) as e:
        update.check_latest(force=True)
    assert "403" in str(e.value)


def test_a_draft_or_prerelease_is_not_offered(monkeypatch):
    for flag in ("draft", "prerelease"):
        monkeypatch.setattr(update, "_urlopen", lambda url, flag=flag: Response(json.dumps({
            "tag_name": "v0.2.0", flag: True}).encode()))
        with pytest.raises(update.UpdateError):
            update.check_latest(force=True)


# ------------------------------------------------------------------------- download/verify

def fake_release_urls(monkeypatch, files, sums=None):
    """Answer the asset and checksums URLs; `files` maps asset name to bytes."""
    if sums is None:
        import hashlib
        sums = {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}
    text = "".join(f"{digest}  {name}\n" for name, digest in sums.items()).encode()

    def answer(url):
        if url.endswith(update.SUMS_ASSET):
            return Response(text)
        name = url.rsplit("/", 1)[-1]
        if name in files:
            return Response(files[name])
        raise urllib.error.HTTPError(url, 404, "missing", {}, None)

    monkeypatch.setattr(update, "_urlopen", answer)


def test_fetch_update_downloads_checks_unpacks_and_returns_the_staged_app(tmp_path, monkeypatch):
    body = workbench_zip()
    fake_release_urls(monkeypatch, {update.WORKBENCH_ASSET: body})
    seen = []

    staged = update.fetch_update(release(), str(tmp_path), on_progress=lambda done, total: seen.append((done, total)))

    assert os.path.isfile(os.path.join(staged, "InitGui.py"))
    assert update.read_version(staged) == "0.1.18"
    assert seen and seen[-1][0] == len(body)
    assert os.path.basename(os.path.dirname(staged)).startswith(".cadswarm-update-")


def test_a_bad_checksum_installs_nothing(tmp_path, monkeypatch):
    fake_release_urls(monkeypatch, {update.WORKBENCH_ASSET: workbench_zip()},
                      sums={update.WORKBENCH_ASSET: "0" * 64})
    with pytest.raises(update.UpdateError) as e:
        update.fetch_update(release(), str(tmp_path))
    assert "checksum" in str(e.value).lower()
    assert not [n for n in os.listdir(tmp_path) if n.startswith(".cadswarm-update-")]


def test_a_release_without_the_workbench_asset_is_refused(tmp_path, monkeypatch):
    def missing(url):
        raise urllib.error.HTTPError(url, 404, "missing", {}, None)

    monkeypatch.setattr(update, "_urlopen", missing)
    with pytest.raises(update.UpdateError) as e:
        update.fetch_update(release(), str(tmp_path))
    assert "doesn't include" in str(e.value)
    assert not [n for n in os.listdir(tmp_path) if n.startswith(".cadswarm-update-")]


def test_a_zip_that_escapes_the_folder_is_refused(tmp_path):
    archive = tmp_path / "evil.zip"
    archive.write_bytes(zipped({"../evil.txt": "boom", "InitGui.py": "ok"}))
    with pytest.raises(update.UpdateError) as e:
        update.extract_zip(str(archive), str(tmp_path / "out"))
    assert "unsafe" in str(e.value)
    assert not (tmp_path / "evil.txt").exists()


def test_a_download_for_another_version_is_refused(tmp_path, monkeypatch):
    fake_release_urls(monkeypatch, {update.WORKBENCH_ASSET: workbench_zip("0.1.16")})
    with pytest.raises(update.UpdateError) as e:
        update.fetch_update(release(), str(tmp_path))
    assert "0.1.16" in str(e.value) and "0.1.18" in str(e.value)


# --------------------------------------------------------------------------------- swapping

def workbench_folder(path, marker):
    (path / "cadswarm").mkdir(parents=True)
    (path / "InitGui.py").write_text(marker)
    (path / "cadswarm" / "version.py").write_text('__version__ = "0.1.17"\n')
    return path


def test_install_swaps_the_folders(tmp_path):
    root = workbench_folder(tmp_path / "CadSwarm", "old")
    staged = workbench_folder(tmp_path / ".cadswarm-update-abc" / "app", "new")

    update.install(str(staged), str(root))

    assert (root / "InitGui.py").read_text() == "new"
    assert not any(n.startswith("CadSwarm.old-") for n in os.listdir(tmp_path))


def test_a_failed_second_move_puts_the_old_version_back(tmp_path, monkeypatch):
    root = workbench_folder(tmp_path / "CadSwarm", "old")
    staged = workbench_folder(tmp_path / ".cadswarm-update-abc" / "app", "new")
    real = update._rename
    calls = {"n": 0}

    def rename(source, dest):
        calls["n"] += 1
        if calls["n"] == 2:  # root -> old worked; app -> root is locked
            raise OSError("the folder is in use")
        return real(source, dest)

    monkeypatch.setattr(update, "_rename", rename)
    with pytest.raises(update.UpdateError) as e:
        update.install(str(staged), str(root))
    assert "old one was kept" in str(e.value)
    assert (root / "InitGui.py").read_text() == "old"
    assert not [n for n in os.listdir(tmp_path) if n.startswith("CadSwarm.old-")]


def test_a_failed_move_and_failed_restore_never_loses_the_working_copy(tmp_path, monkeypatch):
    """The outage this guards against: nothing left at the workbench path, and the only copy deleted."""
    root = workbench_folder(tmp_path / "CadSwarm", "old")
    staged = workbench_folder(tmp_path / ".cadswarm-update-abc" / "app", "new")
    real = update._rename
    calls = {"n": 0}

    def rename(source, dest):
        calls["n"] += 1
        if calls["n"] >= 2:  # app -> root and old -> root both fail
            raise OSError("locked")
        return real(source, dest)

    def no_copy(source, dest):
        raise OSError("denied")

    monkeypatch.setattr(update, "_rename", rename)
    monkeypatch.setattr(update, "_copy_tree", no_copy)
    with pytest.raises(update.UpdateError) as e:
        update.install(str(staged), str(root))
    kept = [p for p in tmp_path.iterdir() if p.name.startswith("CadSwarm.old-")]
    assert len(kept) == 1 and (kept[0] / "InitGui.py").read_text() == "old"
    assert str(kept[0]) in str(e.value)  # the message says where the working copy is


def test_an_old_copy_windows_still_holds_is_never_loaded_by_freecad(tmp_path, monkeypatch):
    """FreeCAD loads every Mod folder with an InitGui.py; the set-aside copy must say it is disabled."""
    root = workbench_folder(tmp_path / "CadSwarm", "old")
    staged = workbench_folder(tmp_path / ".cadswarm-update-abc" / "app", "new")
    real_rmtree = update.shutil.rmtree
    monkeypatch.setattr(update.shutil, "rmtree", lambda path, ignore_errors=False: (
        None if os.path.basename(path).startswith("CadSwarm.old-") else real_rmtree(path, ignore_errors=True)))

    old = update.install(str(staged), str(root))

    assert (root / "InitGui.py").read_text() == "new"
    assert not (root / update.ADDON_DISABLED).exists()
    assert os.path.isfile(os.path.join(old, update.ADDON_DISABLED))


def test_a_restored_copy_is_enabled_again(tmp_path, monkeypatch):
    root = workbench_folder(tmp_path / "CadSwarm", "old")
    staged = workbench_folder(tmp_path / ".cadswarm-update-abc" / "app", "new")
    real = update._rename
    calls = {"n": 0}

    def rename(source, dest):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("the folder is in use")
        return real(source, dest)

    monkeypatch.setattr(update, "_rename", rename)
    with pytest.raises(update.UpdateError):
        update.install(str(staged), str(root))
    assert (root / "InitGui.py").read_text() == "old"
    assert not (root / update.ADDON_DISABLED).exists()

    leftover = workbench_folder(tmp_path / "Other" / "CadSwarm.old-1700000000", "older")
    (leftover / update.ADDON_DISABLED).write_text("x")
    update.sweep(str(tmp_path / "Other" / "CadSwarm"))
    assert (tmp_path / "Other" / "CadSwarm" / "InitGui.py").read_text() == "older"
    assert not (tmp_path / "Other" / "CadSwarm" / update.ADDON_DISABLED).exists()


def test_sweep_puts_the_workbench_back_if_only_the_old_copy_is_left(tmp_path):
    old = workbench_folder(tmp_path / "CadSwarm.old-1700000000", "old")

    update.sweep(str(tmp_path / "CadSwarm"))

    assert (tmp_path / "CadSwarm" / "InitGui.py").read_text() == "old"
    assert not old.exists()


def test_sweep_removes_what_earlier_updates_left(tmp_path):
    root = workbench_folder(tmp_path / "CadSwarm", "current")
    (tmp_path / "CadSwarm.old-1700000000").mkdir()
    (tmp_path / ".cadswarm-update-deadbeef").mkdir()
    (tmp_path / "unrelated").mkdir()

    update.sweep(str(root))

    left = set(os.listdir(tmp_path))
    assert "CadSwarm" in left and "unrelated" in left
    assert not any(n.startswith("CadSwarm.old-") or n.startswith(".cadswarm-update-") for n in left)


# ---------------------------------------------------------------------------------- restart

def test_the_restart_waits_for_this_copy_to_exit(tmp_path):
    """The new FreeCAD must start after the old one has let go of the workbench folder."""
    marker = tmp_path / "started.txt"
    script = tmp_path / "fake_freecad.py"
    script.write_text(f"open(r'{marker}', 'w').write('ok')\n")
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"])
    update.spawn_restart(sys.executable, str(script), sys.executable, old.pid)
    time.sleep(0.15)
    assert not marker.exists()  # still running: the waiter is waiting
    old.wait()
    deadline = time.time() + 15
    while time.time() < deadline and not marker.exists():
        time.sleep(0.05)
    assert marker.exists()


def test_reopen_documents_are_handed_over_once(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "app_dir", lambda: str(tmp_path))
    opened = tmp_path / "bracket.FCStd"
    opened.write_text("model")
    update.remember_reopen([str(opened), str(tmp_path / "gone.FCStd")])

    assert update.take_reopen() == [str(opened)]  # a file that vanished meanwhile isn't reopened
    assert update.take_reopen() == []              # the new FreeCAD asks once
