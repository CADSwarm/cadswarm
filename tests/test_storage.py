"""Storage policy: disk guard, project budget, per-type file limits, history retention, 'free up space'."""
import base64
import os
import shutil
from collections import namedtuple

import pytest

from cadswarm.client import HubRequestError
from cadswarm.paths import project_layout
from cadswarm.space_hub import SpaceHub
from filehub import Clock, FileHub, HubError

GB, MB = 1024 ** 3, 1024 ** 2
Usage = namedtuple("Usage", "total used free")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("CADSWARM_PROJECT_LIMIT_GB", raising=False)
    lay = project_layout(tmp_path / "Phone stand")
    for k in ("internal", "snapshots", "exports"):  # a real project has these from the start
        os.makedirs(lay[k])
    clock = Clock()
    with open(lay["model"], "wb") as f:
        f.write(b"m" * 1000)
    hub = FileHub(lay, clock)
    a = hub.join("secret", "alice-claude")["participant_id"]
    b = hub.join("secret", "bob-human", kind="human")["participant_id"]
    return hub, lay, clock, a, b


def set_limit_bytes(hub, n):
    """A tiny project limit for tests (settings are in GB, so go through a float)."""
    hub.files.storage.update({"project_limit_gb": n / GB})


def fake_disk(monkeypatch, free, total=100 * GB):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(total, total - free, free))


# ------------------------------------------------------------------ disk guard

def test_disk_guard_refuses_writes_that_would_eat_the_reserve(env, monkeypatch):
    hub, lay, _, a, _ = env
    fake_disk(monkeypatch, free=2 * GB + 100)  # reserve is 2 GB (10% of 100 GB is bigger, so 2 GB wins)
    hub.save_file(a, "ok.md", text="x" * 50)
    with pytest.raises(HubError) as e:
        hub.save_file(a, "no.md", text="x" * 200)
    assert e.value.status == 507 and e.value.code == "disk_full"
    assert "free up" in str(e.value).lower() and "ask a person" in str(e.value).lower()
    assert not os.path.exists(os.path.join(lay["files"], "no.md"))


def test_disk_reserve_is_ten_percent_on_a_small_volume_and_configurable(env, monkeypatch):
    hub, _, _, a, _ = env
    fake_disk(monkeypatch, free=5 * GB, total=30 * GB)  # 10% of 30 GB is 3 GB, bigger than 2 GB: 2 GB wins
    assert hub.files.storage.reserve_bytes() == 2 * GB
    fake_disk(monkeypatch, free=5 * GB, total=10 * GB)  # 10% of 10 GB is 1 GB, smaller than 2 GB: 1 GB wins
    assert hub.files.storage.reserve_bytes() == 1 * GB
    hub.files.storage.update({"disk_reserve_gb": 4.9999})
    hub.save_file(a, "fits.md", text="x" * 1000)  # 5 GB free, 1 KB still leaves the reserve intact
    hub.files.storage.update({"disk_reserve_gb": 5})
    with pytest.raises(HubError) as e:
        hub.save_file(a, "x.md", text="x")
    assert e.value.code == "disk_full"


def test_disk_guard_covers_snapshots_exports_restore_and_move(env, monkeypatch):
    hub, lay, _, a, _ = env
    hub.save_file(a, "a.md", text="x" * 500)
    hub.save_file(a, "a.md", text="y" * 500, if_exists="replace", expected_version=1)
    fake_disk(monkeypatch, free=2 * GB + 10)
    with pytest.raises(HubError) as e:
        hub.snapshot(a, "big")  # the model is 1000 bytes
    assert e.value.code == "disk_full" and os.listdir(lay["snapshots"]) == []
    with pytest.raises(HubError) as e:
        hub.restore_file(a, "a.md", 1)
    assert e.value.code == "disk_full"
    with pytest.raises(HubError) as e:
        hub.move_file(a, "a.md", "b.md")
    assert e.value.code == "disk_full" and hub.read_file("a.md")["text"] == "y" * 500


# ---------------------------------------------------------------- project budget

def test_budget_counts_files_old_versions_snapshots_and_exports(env):
    hub, lay, _, a, _ = env
    set_limit_bytes(hub, 3000)
    hub.save_file(a, "n.md", text="x" * 1000)
    hub.save_file(a, "n.md", text="y" * 1000, if_exists="replace", expected_version=1)  # 1000 live + 1000 old
    with open(os.path.join(lay["exports"], "m.step"), "wb") as f:
        f.write(b"e" * 500)
    u = hub.storage_status()["usage"]
    assert (u["files"], u["old_versions"], u["snapshots"], u["exports"]) == (1000, 1000, 0, 500)
    assert u["total"] == 2500
    with pytest.raises(HubError) as e:  # model is 1000 bytes: 2500 + 1000 > 3000
        hub.snapshot(a)
    assert e.value.status == 413 and e.value.code == "quota"
    msg = str(e.value)
    assert "older versions" in msg and "exports" in msg and "ask a person" in msg.lower()
    with pytest.raises(HubError) as e:
        hub.save_file(a, "other.md", text="z" * 600)
    assert e.value.code == "quota"
    hub.save_file(a, "other.md", text="z" * 400)  # 2900: fits


def test_snapshots_count_after_they_are_taken(env):
    hub, lay, _, a, _ = env
    hub.snapshot(a, "one")
    hub.snapshot(a, "two")
    assert hub.storage_status()["usage"]["snapshots"] == 2000


def test_limit_is_configurable_and_zero_means_unlimited(env):
    hub, _, _, a, _ = env
    assert hub.storage_status()["limit_bytes"] == 20 * GB  # the default
    set_limit_bytes(hub, 100)
    with pytest.raises(HubError) as e:
        hub.save_file(a, "n.md", text="x" * 101)
    assert e.value.code == "quota"
    hub.files.storage.update({"project_limit_gb": 0})
    hub.save_file(a, "n.md", text="x" * 101)
    assert hub.storage_status()["limit_bytes"] == 0


def test_env_overrides_the_saved_limit(env, monkeypatch):
    hub, _, _, a, _ = env
    set_limit_bytes(hub, 100)
    monkeypatch.setenv("CADSWARM_PROJECT_LIMIT_GB", "0")
    hub.save_file(a, "n.md", text="x" * 500)
    st = hub.storage_status()
    assert st["limit_bytes"] == 0 and st["limit_source"] == "env"
    monkeypatch.setenv("CADSWARM_PROJECT_LIMIT_GB", "banana")  # junk is ignored, not a crash
    assert hub.storage_status()["limit_source"] == "setting"


def test_settings_persist_in_the_team_folder_and_are_validated(env):
    hub, lay, _, _, _ = env
    hub.files.storage.update({"project_limit_gb": 7, "history_keep": 5, "history_hours": 2})
    again = FileHub(lay, db_path=lay["db"] + "2")
    s = again.storage_status()["settings"]
    assert (s["project_limit_gb"], s["history_keep"], s["history_hours"]) == (7, 5, 2)
    for bad in ({"project_limit_gb": -1}, {"history_keep": 0}, {"history_keep": "x"}, {"history_hours": -3},
                {"project_limit_gb": True}, {"nonsense": 1}):
        with pytest.raises(HubError) as e:
            hub.set_storage_settings(env[4], bad)
        assert e.value.status == 400 and e.value.code == "bad_args"


# ------------------------------------------------------------- per-type limits

def test_binary_cad_files_may_be_100mb_but_text_stays_5mb(env):
    hub, _, _, a, _ = env
    with pytest.raises(HubError) as e:
        hub.save_file(a, "huge.md", text="x" * (5 * MB + 1))
    assert e.value.code == "too_big" and "5 MB" in str(e.value)
    big = base64.b64encode(b"s" * (6 * MB)).decode()
    assert hub.save_file(a, "assembly.step", base64_data=big)["size"] == 6 * MB  # fine for a CAD file
    with pytest.raises(HubError) as e:
        hub.save_file(a, "toobig.stl", base64_data=base64.b64encode(b"s" * (100 * MB + 1)).decode())
    assert e.value.code == "too_big" and "100 MB" in str(e.value)
    with pytest.raises(HubError) as e:  # a text format can't dodge its limit by arriving as base64
        hub.save_file(a, "sneaky.json", base64_data=base64.b64encode(b"s" * (5 * MB + 1)).decode())
    assert e.value.code == "too_big"


def test_big_binaries_are_not_inlined_into_an_agents_reply(env):
    hub, _, _, a, _ = env
    hub.save_file(a, "assembly.step", base64_data=base64.b64encode(b"s" * (6 * MB)).decode())
    with pytest.raises(HubError) as e:
        hub.read_file("assembly.step")
    assert e.value.code == "too_big_to_read"
    assert len(hub.raw_file("assembly.step")[1]) == 6 * MB  # the download path still serves it


# ------------------------------------------------------------ history retention

def save_versions(hub, a, clock, n, step, path="p.md"):
    hub.save_file(a, path, text="v1")
    for v in range(2, n + 1):
        clock.t += step
        hub.save_file(a, path, text=f"v{v}", if_exists="replace", expected_version=v - 1)


def old_versions(hub, path="p.md"):
    return [h["version"] for h in hub.file_history(path)["older_versions"]]


def test_the_21st_old_version_prunes_the_oldest(env):
    hub, lay, clock, a, _ = env
    save_versions(hub, a, clock, 21, step=3 * 3600)  # 3 hours apart: only the last 24 h (8 versions) are protected
    assert old_versions(hub) == list(range(1, 21))  # v21 is live; 20 old ones kept
    clock.t += 3 * 3600
    hub.save_file(a, "p.md", text="v22", if_exists="replace", expected_version=21)
    assert old_versions(hub) == list(range(2, 22))  # v1 is gone, bytes included
    stored = os.listdir(os.path.join(lay["history"]))
    assert len(stored) == 20


def test_everything_from_the_last_day_is_kept_even_beyond_20(env):
    hub, _, clock, a, _ = env
    save_versions(hub, a, clock, 30, step=60)  # 30 saves within half an hour
    assert old_versions(hub) == list(range(1, 30))
    clock.t += 25 * 3600
    hub.save_file(a, "p.md", text="v31", if_exists="replace", expected_version=30)
    assert old_versions(hub) == [30 - 20 + 1 + i for i in range(20)]  # only the newest 20 now that the day passed


def test_retention_is_configurable(env):
    hub, _, clock, a, _ = env
    hub.files.storage.update({"history_keep": 3, "history_hours": 0})
    save_versions(hub, a, clock, 10, step=100)
    assert old_versions(hub) == [7, 8, 9]  # v10 is the live one


def test_a_deleted_file_always_keeps_its_latest_version(env):
    hub, _, clock, a, _ = env
    hub.files.storage.update({"history_keep": 1, "history_hours": 0})
    save_versions(hub, a, clock, 3, step=100)
    clock.t += 100
    hub.delete_file(a, "p.md")
    assert old_versions(hub) == [3]  # deleting also prunes, but the last version survives so it can be undone
    hub.restore_file(a, "p.md", 3)
    assert hub.read_file("p.md")["text"] == "v3"


def test_deleting_does_not_store_the_bytes_twice(env):
    hub, lay, _, a, _ = env
    hub.save_file(a, "p.md", text="x" * 300)
    hub.delete_file(a, "p.md")
    assert hub.storage_status()["usage"]["old_versions"] == 300
    on_disk = sum(os.path.getsize(os.path.join(d, n)) for base in (lay["history"], lay["trash"])
                  for d, _, names in os.walk(base) for n in names)
    assert on_disk == 300
    hub.restore_file(a, "p.md", 1)
    assert hub.read_file("p.md")["text"] == "x" * 300


# ------------------------------------------------------- storage report + prune

def test_storage_status_breakdown_and_free_disk(env, monkeypatch):
    hub, lay, clock, a, _ = env
    fake_disk(monkeypatch, free=50 * GB)
    hub.save_file(a, "n.md", text="x" * 100)
    hub.save_file(a, "n.md", text="y" * 120, if_exists="replace", expected_version=1)
    hub.snapshot(a, "s")
    st = hub.storage_status()
    assert st["usage"] == {"files": 120, "old_versions": 100, "snapshots": 1000, "exports": 0, "total": 1220,
                           "model": 1000}
    assert st["free_disk_bytes"] == 50 * GB and st["limit_bytes"] == 20 * GB and st["reserve_bytes"] == 2 * GB
    assert st["settings"]["history_keep"] == 20 and st["settings"]["history_hours"] == 24
    assert st["old_version_files"] == 1


def seed_for_prune(hub, lay, clock, a):
    hub.files.storage.update({"history_keep": 50})
    save_versions(hub, a, clock, 6, step=100)  # 5 old versions of p.md
    day = 24 * 3600
    for name, age_days in (("old1.FCStd", 40), ("old2.FCStd", 30), ("mid.FCStd", 20), ("new.FCStd", 1)):
        p = os.path.join(lay["snapshots"], name)
        with open(p, "wb") as f:
            f.write(b"s" * 100)
        os.utime(p, (clock.t - age_days * day,) * 2)
    for name, age_days in (("e-old.step", 60), ("e-new.step", 2)):
        p = os.path.join(lay["exports"], name)
        with open(p, "wb") as f:
            f.write(b"e" * 10)
        os.utime(p, (clock.t - age_days * day,) * 2)


def test_prune_trims_versions_and_old_snapshots_and_exports(env):
    hub, lay, clock, a, human = env
    seed_for_prune(hub, lay, clock, a)
    r = hub.prune_storage(human, {"keep_versions": 2, "snapshots_older_than_days": 14, "keep_newest_snapshots": 1,
                                  "exports_older_than_days": 30})
    assert old_versions(hub) == [4, 5]
    # snapshots older than 14 days = old1, old2, mid; newest 1 overall is 'new', so all three go
    assert sorted(os.listdir(lay["snapshots"])) == ["new.FCStd"]
    assert os.listdir(lay["exports"]) == ["e-new.step"]
    assert r["freed"] == {"old_versions": 3 * 2, "snapshots": 300, "exports": 10, "total": 6 + 300 + 10}
    assert r["files_removed"] == {"old_versions": 3, "snapshots": 3, "exports": 1}


def test_prune_keeps_the_newest_snapshots_even_when_old(env):
    hub, lay, clock, a, human = env
    seed_for_prune(hub, lay, clock, a)
    hub.prune_storage(human, {"snapshots_older_than_days": 14, "keep_newest_snapshots": 3})
    assert sorted(os.listdir(lay["snapshots"])) == ["mid.FCStd", "new.FCStd", "old2.FCStd"]


def test_prune_with_no_options_deletes_nothing_and_bad_options_are_400(env):
    hub, lay, clock, a, human = env
    seed_for_prune(hub, lay, clock, a)
    assert hub.prune_storage(human, {})["freed"]["total"] == 0
    assert len(os.listdir(lay["snapshots"])) == 4 and len(old_versions(hub)) == 5
    for bad in ({"keep_versions": 0}, {"keep_versions": -1}, {"snapshots_older_than_days": "x"},
                {"exports_older_than_days": -2}, {"keep_newest_snapshots": -1}, {"what": 1}):
        with pytest.raises(HubError) as e:
            hub.prune_storage(human, bad)
        assert e.value.status == 400


def test_only_people_may_prune_or_change_settings(env):
    hub, lay, clock, agent, human = env
    for call in (lambda: hub.prune_storage(agent, {"keep_versions": 1}),
                 lambda: hub.set_storage_settings(agent, {"project_limit_gb": 0})):
        with pytest.raises(HubError) as e:
            call()
        assert e.value.status == 403 and e.value.code == "humans_only"
    hub.set_storage_settings(human, {"project_limit_gb": 5})
    assert hub.storage_status()["limit_bytes"] == 5 * GB


def test_freeing_space_unsticks_a_full_project(env):
    hub, lay, clock, a, human = env
    hub.files.storage.update({"history_keep": 50})
    save_versions(hub, a, clock, 4, step=100)
    set_limit_bytes(hub, hub.storage_status()["usage"]["total"] + 1)
    with pytest.raises(HubError) as e:
        hub.save_file(a, "p.md", text="v5", if_exists="replace", expected_version=4)
    assert e.value.code == "quota"
    hub.prune_storage(human, {"keep_versions": 1})
    hub.save_file(a, "p.md", text="v5", if_exists="replace", expected_version=4)


# ------------------------------------------------- limits change on the hosting computer only

def test_the_hosting_computer_changes_limits_through_spacehub(tmp_path):
    host = SpaceHub({"uri": "http://x", "db": "d", "host_dir": str(tmp_path / "team")}, {}, space=object())
    s = host.post("/project/storage/settings", {"project_limit_gb": 3})
    assert s["limit_bytes"] == 3 * GB
    with pytest.raises(HubRequestError) as e:
        host.post("/project/storage/settings", {"project_limit_gb": -1})
    assert e.value.status == 400 and e.value.code == "bad_args"
    away = SpaceHub({"uri": "http://x", "db": "d"}, {}, space=object())
    with pytest.raises(HubRequestError) as e:
        away.post("/project/storage/settings", {"project_limit_gb": 0})
    assert e.value.status == 403 and e.value.code == "host_only"
