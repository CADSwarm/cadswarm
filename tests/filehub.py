"""The old Hub's project-file surface, for the file and storage policy tests.

The FastAPI hub is gone: on the host these calls are ops that `cadswarm/helper.py` answers with
`ProjectFiles` and `Storage`. This shim keeps the tests' old call shapes
(`hub.save_file(pid, path, ...)`) and raises `HubError` (the files module's FileError) on refusal.
"""
import os
import shutil
import sqlite3
import time

from cadswarm.files import FileError, ProjectFiles, safe_label, unique_path

HubError = FileError


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FileHub:
    def __init__(self, layout, clock=time.time, db_path=None):
        self.layout = layout
        self.clock = clock
        db = sqlite3.connect(db_path or layout["db"], check_same_thread=False, isolation_level=None)
        db.row_factory = sqlite3.Row
        self.files = ProjectFiles(layout, db, clock)
        self.people = {}

    def join(self, _token, name, kind="agent", **_):
        pid = len(self.people) + 1
        self.people[pid] = {"id": pid, "name": name, "kind": kind}
        return {"participant_id": pid}

    def name_of(self, pid):
        return self.people[pid]["name"]

    def _files(self):
        return self.files

    def _file_call(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def _people_only(self, pid, what):
        if self.people[pid]["kind"] != "human":
            raise HubError(403, "humans_only", f"Only a person can {what}.")

    # reads
    def list_files(self, prefix=""):
        return self.files.listing(prefix)

    def read_file(self, path):
        return self.files.read(path)

    def raw_file(self, path):
        return self.files.raw(path)

    def file_history(self, path):
        return self.files.history(path)

    def storage_status(self):
        return self.files.storage.status()

    # writes
    def save_file(self, pid, path, text=None, base64_data=None, if_exists="error", expected_version=None):
        return self.files.save(path, self.name_of(pid), text, base64_data, if_exists, expected_version)

    def make_folder(self, pid, path):
        return self.files.make_folder(path)

    def move_file(self, pid, src, dst):
        return self.files.move(src, dst, self.name_of(pid))

    def delete_file(self, pid, path):
        return self.files.delete(path, self.name_of(pid))

    def restore_file(self, pid, path, version, expected_version=None):
        return self.files.restore(path, version, self.name_of(pid), expected_version)

    def snapshot(self, pid, label=""):
        model = self.layout["model"]
        self.files.storage.check_add(os.path.getsize(model))
        stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(self.clock()))
        dest = unique_path(self.layout["snapshots"], f"{stamp} {safe_label(label, 'snapshot')}.FCStd")
        shutil.copy2(model, dest)
        rel = os.path.relpath(dest, self.layout["root"]).replace("\\", "/")
        return {"file": rel, "size": os.path.getsize(dest)}

    def prune_storage(self, pid, options):
        self._people_only(pid, "free up space")
        return self.files.storage.prune(options or {})

    def set_storage_settings(self, pid, changes):
        self._people_only(pid, "change the storage settings")
        self.files.storage.update(changes)
        return self.files.storage.status()
