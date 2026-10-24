"""The helper answers the hub's old file and storage calls in the worker's reply format."""
import base64
import json
import subprocess
import sys

from cadswarm import helper
from cadswarm.config import helper_script

HUMAN = {"id": 3, "name": "carol", "kind": "human"}
AGENT = {"id": 4, "name": "alice-claude", "kind": "agent"}


def ask(project, op, actor=AGENT, **args):
    return helper.handle(project, {"id": "1", "op": op, "args": {**args, "_actor": actor}})


def test_save_read_list_and_history(tmp_path):
    p = helper.Project(str(tmp_path))
    r = ask(p, "save_file", path="notes/plan.md", text="v1")
    assert r["ok"] and r["result"]["path"] == "notes/plan.md" and r["result"]["version"] == 1
    assert ask(p, "read_file", path="NOTES/plan.md")["result"]["text"] == "v1"  # case-insensitive keys
    r = ask(p, "save_file", path="notes/plan.md", text="v2", if_exists="replace", expected_version=1)
    assert r["result"]["version"] == 2
    hist = ask(p, "file_history", path="notes/plan.md")["result"]
    assert hist["current"]["version"] == 2 and [v["version"] for v in hist["older_versions"]] == [1]
    assert hist["current"]["author"] == "alice-claude"
    listing = ask(p, "list_files")["result"]
    assert [f["path"] for f in listing["files"]] == ["notes/plan.md"]
    assert ask(p, "list_files", folder="notes")["result"]["files"][0]["path"] == "notes/plan.md"


def test_contract_args_for_move_and_folders(tmp_path):
    p = helper.Project(str(tmp_path))
    ask(p, "save_file", path="a.md", text="A")
    assert ask(p, "make_folder", path="parts/lid")["ok"]
    r = ask(p, "move_file", from_path="a.md", to_path="parts/lid/a.md")
    assert r["ok"], r
    assert ask(p, "read_file", path="parts/lid/a.md")["result"]["text"] == "A"
    assert ask(p, "delete_file", path="parts/lid/a.md")["ok"]
    assert ask(p, "restore_file", path="parts/lid/a.md", version=1)["result"]["version"] >= 1


def test_raw_read_returns_base64(tmp_path):
    p = helper.Project(str(tmp_path))
    ask(p, "save_file", path="a.png", base64=base64.b64encode(b"\x00\x01").decode())
    r = ask(p, "read_file", path="a.png", raw=True)["result"]
    assert base64.b64decode(r["base64"]) == b"\x00\x01" and r["path"] == "a.png"


def test_errors_keep_their_codes_and_details_and_never_raise(tmp_path):
    p = helper.Project(str(tmp_path))
    r = ask(p, "read_file", path="../escape.txt")
    assert r["ok"] is False and r["code"] and r["error"]
    assert ask(p, "nope")["code"] == "unknown_op"
    ask(p, "save_file", path="n.md", text="1")
    r = ask(p, "save_file", path="n.md", text="2", if_exists="replace", expected_version=7)
    assert r["code"] == "stale_version" and r["currentVersion"] == 1
    assert helper.handle(p, {"id": "x", "op": "save_file", "args": None})["ok"] is False


def test_only_people_can_free_up_space(tmp_path):
    p = helper.Project(str(tmp_path))
    assert ask(p, "prune_storage", actor=AGENT)["code"] == "humans_only"
    assert ask(p, "prune_storage", actor=HUMAN)["ok"] is True
    assert "limit" in json.dumps(ask(p, "storage_status")["result"])


def test_snapshot_copies_the_model(tmp_path):
    p = helper.Project(str(tmp_path))
    assert ask(p, "snapshot", label="x")["code"] == "not_found"
    (tmp_path / "model.FCStd").write_bytes(b"PK fake model")
    r = ask(p, "snapshot", label="before lid")["result"]
    assert r["file"].startswith("snapshots/") and r["file"].endswith(".FCStd")
    assert (tmp_path / r["file"]).read_bytes() == b"PK fake model"


def test_runs_as_a_script_with_the_worker_protocol(tmp_path):
    proc = subprocess.Popen([sys.executable, "-I", helper_script(), str(tmp_path)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, encoding="utf-8")
    assert json.loads(proc.stdout.readline()) == {"ready": True}
    proc.stdin.write("not json\n")
    proc.stdin.write(json.dumps({"id": 7, "op": "list_files", "args": {}}) + "\n")
    proc.stdin.flush()
    bad = json.loads(proc.stdout.readline())
    reply = json.loads(proc.stdout.readline())
    proc.stdin.close()
    proc.wait(timeout=10)
    assert bad["ok"] is False and bad["code"] == "bad_request"
    assert reply["id"] == 7 and reply["ok"] is True and reply["result"]["files"] == []
