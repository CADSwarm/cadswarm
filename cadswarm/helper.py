"""Host-side helper for ops that are not FreeCAD geometry: project files and storage (and the
Elastic lookups added by plan 05). The Rust host drives it like the FreeCAD worker:

    python cadswarm/helper.py <project_dir>

First line out is {"ready": true}; then one request per line {"id", "op", "args"} and one reply per
line {"id", "ok": true, "result"} or {"id", "ok": false, "code", "error", ...details}. A bad request
never kills it. The host adds args["_actor"] = {"id", "name", "kind"} for authorship and people-only
actions. Op args follow the contract (plan 00): list_files {folder}, move_file {from_path, to_path}.

Stdlib only (with everything it imports): the host may run it with FreeCAD's Python.
"""
import base64
import json
import os
import shutil
import sqlite3
import sys
import time

if __package__ in (None, ""):  # run as a script by the host: make `cadswarm` importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cadswarm.files import ProjectFiles, safe_label, unique_path  # noqa: E402
from cadswarm.paths import project_layout  # noqa: E402
from cadswarm.storage import FileError  # noqa: E402


class HelperError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Project:
    def __init__(self, root, clock=time.time):
        self.layout = project_layout(root)
        os.makedirs(self.layout["internal"], exist_ok=True)
        for k in ("files", "exports", "snapshots"):
            os.makedirs(self.layout[k], exist_ok=True)
        self.db = sqlite3.connect(self.layout["db"], check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.files = ProjectFiles(self.layout, self.db, clock)
        self.clock = clock


def _actor(a):
    return a.get("_actor") or {}


def _author(a):
    return _actor(a).get("name") or "someone"


def _people_only(a, what):
    if _actor(a).get("kind") != "human":
        raise HelperError("humans_only", f"Only a person can {what}. Ask someone on the team to do it "
                                         "(in FreeCAD: Team tools > Storage and free up space).")


def _first(a, *keys):
    return next((a[k] for k in keys if a.get(k) is not None), None)


def list_files(p, a):
    return p.files.listing(_first(a, "folder", "prefix") or "")


def read_file(p, a):
    if a.get("raw"):
        clean, data = p.files.raw(a.get("path"))
        return {"path": clean, "base64": base64.b64encode(data).decode()}
    return p.files.read(a.get("path"))


def snapshot(p, a):
    model = p.layout["model"]
    if not os.path.exists(model):
        raise HelperError("not_found", "There is no model to snapshot yet.")
    p.files.storage.check_add(os.path.getsize(model))
    stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(p.clock()))
    dest = unique_path(p.layout["snapshots"], f"{stamp} {safe_label(a.get('label', ''), 'snapshot')}.FCStd")
    shutil.copy2(model, dest)  # the host runs one op at a time, and the worker saved before acknowledging
    rel = os.path.relpath(dest, p.layout["root"]).replace("\\", "/")
    return {"file": rel, "size": os.path.getsize(dest)}


def prune_storage(p, a):
    _people_only(a, "free up space")
    return p.files.storage.prune({k: v for k, v in a.items() if k != "_actor"})


HANDLERS = {
    "list_files": list_files,
    "read_file": read_file,
    "file_history": lambda p, a: p.files.history(a.get("path")),
    "save_file": lambda p, a: p.files.save(a.get("path"), _author(a), a.get("text"), a.get("base64"),
                                           a.get("if_exists") or "error", a.get("expected_version")),
    "make_folder": lambda p, a: p.files.make_folder(a.get("path")),
    "move_file": lambda p, a: p.files.move(_first(a, "from_path", "from"), _first(a, "to_path", "to"), _author(a)),
    "delete_file": lambda p, a: p.files.delete(a.get("path"), _author(a)),
    "restore_file": lambda p, a: p.files.restore(a.get("path"), a.get("version"), _author(a),
                                                 a.get("expected_version")),
    "snapshot": snapshot,
    "storage_status": lambda p, a: p.files.storage.status(),
    "prune_storage": prune_storage,
}


def handle(project, req):
    rid, op, args = req.get("id"), req.get("op"), req.get("args")
    fn = HANDLERS.get(op)
    if fn is None:
        return {"id": rid, "ok": False, "code": "unknown_op", "error": f"The helper has no op {op!r}"}
    if not isinstance(args, dict):
        return {"id": rid, "ok": False, "code": "bad_args", "error": "args must be a JSON object"}
    try:
        return {"id": rid, "ok": True, "result": fn(project, args)}
    except FileError as e:
        return {**e.data, "id": rid, "ok": False, "code": e.code, "error": str(e)}
    except HelperError as e:
        return {"id": rid, "ok": False, "code": e.code, "error": str(e)}
    except Exception as e:  # report, never die: the host keeps one helper for the whole session
        return {"id": rid, "ok": False, "code": "op_error", "error": f"{type(e).__name__}: {e}"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    project = Project(argv[0] if argv else os.getcwd())
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError("not an object")
        except ValueError:
            print(json.dumps({"id": None, "ok": False, "code": "bad_request", "error": "not a JSON request"}),
                  flush=True)
            continue
        print(json.dumps(handle(project, req)), flush=True)


if __name__ == "__main__":
    main()
