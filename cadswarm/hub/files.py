"""The project's shared files: where AI helpers save notes, specs, scripts and pictures.

Everything lives under `<project>/files/`, and the rules are enforced here, not left to
the agents:

* Sandbox. Paths are relative, forward-slashed and checked component by component: no
  `..`, no absolute or drive paths, no hidden or Windows-reserved names, no characters
  Windows can't store, bounded depth and length; the resolved path must stay inside the
  folder even through symlinks or junctions.
* No silent overwrites. Saving onto an existing file is refused unless the caller asks for
  `if_exists="new_name"` (a free name like `notes-2.md`) or `if_exists="replace"` with the
  `expected_version` they last read — the same optimistic check model objects use.
* Nothing is lost. Before a file is replaced, moved away or deleted, its current bytes are
  copied into `.cadswarm-team/history/`, including edits someone made outside CadSwarm
  (detected by hash), and any old version can be restored as a new version.
* Atomic. Content is written to a temp file in the same folder, flushed, then renamed
  over the target, so a crash never leaves half a file.

Callers serialize access (the Hub's lock); this class does no locking of its own.
"""
import base64
import hashlib
import os
import re
import shutil
import time

TEXT_EXT = {".md", ".txt", ".json", ".csv", ".py", ".fcmacro", ".svg", ".yaml", ".yml", ".toml", ".html", ".xml"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".step", ".stp", ".stl", ".fcstd", ".dxf", ".3mf"}
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_DEPTH = 8
MAX_PATH_CHARS = 180
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f\\]')

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY, version INTEGER NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
  author TEXT, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS file_history (
  path TEXT NOT NULL, version INTEGER NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
  author TEXT, saved_at REAL NOT NULL, stored_as TEXT NOT NULL, reason TEXT NOT NULL);
"""


class FileError(Exception):
    def __init__(self, status, code, message, **data):
        super().__init__(message)
        self.status, self.code, self.data = status, code, data


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class ProjectFiles:
    def __init__(self, layout, db, clock=time.time):
        self.root = os.path.realpath(layout["files"])
        self.history_dir = layout["history"]
        self.trash_dir = layout["trash"]
        self.db, self.clock = db, clock
        os.makedirs(self.root, exist_ok=True)
        self.db.executescript(SCHEMA)

    # --------------------------------------------------------------- paths

    def clean(self, path, allow_folder=False):
        """Validate a caller's path; returns the canonical 'a/b/c.md' form or raises FileError."""
        if not isinstance(path, str) or not path.strip():
            raise FileError(400, "bad_path", "Give a file path like 'parts/base/notes.md'.")
        p = path.strip().replace("\\", "/")
        if p.startswith("files/"):
            p = p[len("files/"):]  # tolerate agents that include the folder name
        if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or p.startswith("~"):
            raise FileError(400, "bad_path", "Use a path inside the project, like 'notes/plan.md', not an absolute one.")
        parts = [c for c in p.split("/") if c not in ("", ".")]
        if not parts:
            raise FileError(400, "bad_path", "That path is empty.")
        if len(parts) > MAX_DEPTH or len(p) > MAX_PATH_CHARS:
            raise FileError(400, "bad_path", f"Keep paths under {MAX_DEPTH} folders deep and {MAX_PATH_CHARS} characters.")
        for c in parts:
            if c == "..":
                raise FileError(400, "bad_path", "Paths can't use '..' to leave the project's files folder.")
            if c.startswith("."):
                raise FileError(400, "bad_path", f"Hidden names like {c!r} aren't allowed.")
            if BAD_CHARS.search(c) or c.endswith((" ", ".")):
                raise FileError(400, "bad_path", f"{c!r} has characters Windows can't store in a file name.")
            if c.split(".")[0].lower() in RESERVED:
                raise FileError(400, "bad_path", f"{c!r} is a reserved name on Windows.")
        clean = "/".join(parts)
        if not allow_folder:
            ext = os.path.splitext(parts[-1])[1].lower()
            if ext not in TEXT_EXT | BINARY_EXT:
                raise FileError(400, "bad_type", f"Can't save {ext or 'files without an extension'} here. Allowed: "
                                + ", ".join(sorted(TEXT_EXT | BINARY_EXT)))
        full = os.path.realpath(os.path.join(self.root, *parts))
        if os.path.commonpath([full, self.root]) != self.root:
            raise FileError(400, "bad_path", "That path points outside the project's files folder.")
        return clean

    def full(self, clean):
        return os.path.join(self.root, *clean.split("/"))

    # ----------------------------------------------------------- reading

    def record(self, clean):
        row = self.db.execute("SELECT * FROM files WHERE path=?", (clean,)).fetchone()
        return dict(row) if row else None

    def disk_bytes(self, clean):
        try:
            with open(self.full(clean), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def reconcile(self, clean):
        """Notice files added or edited outside CadSwarm, so they're versioned like any other."""
        data, rec = self.disk_bytes(clean), self.record(clean)
        if data is None:
            if rec:
                self.db.execute("DELETE FROM files WHERE path=?", (clean,))
            return None
        digest = sha256(data)
        if rec is None:
            self.db.execute("INSERT INTO files VALUES (?,?,?,?,?,?)",
                            (clean, 1, digest, len(data), "outside CadSwarm", self.clock()))
        elif rec["sha256"] != digest:
            self.db.execute("UPDATE files SET version=version+1, sha256=?, size=?, author=?, updated_at=? WHERE path=?",
                            (digest, len(data), "outside CadSwarm", self.clock(), clean))
        return self.record(clean)

    def read(self, path):
        clean = self.clean(path)
        rec = self.reconcile(clean)
        if rec is None:
            raise FileError(404, "not_found", f"There's no file {clean!r}. Use list_files to see what exists.")
        data = self.disk_bytes(clean)
        text_ext = os.path.splitext(clean)[1].lower() in TEXT_EXT
        out = {"path": clean, "version": rec["version"], "size": rec["size"], "author": rec["author"],
               "updated_at": rec["updated_at"]}
        if text_ext:
            out["text"] = data.decode("utf-8", errors="replace")
        else:
            out["base64"] = base64.b64encode(data).decode()
        return out

    def raw(self, path):
        clean = self.clean(path)
        if self.reconcile(clean) is None:
            raise FileError(404, "not_found", f"There's no file {clean!r}.")
        return clean, self.disk_bytes(clean)

    def listing(self, prefix=""):
        base = self.root
        if prefix:
            base = self.full(self.clean(prefix, allow_folder=True))
        out, folders = [], []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            rel_dir = os.path.relpath(dirpath, self.root).replace("\\", "/")
            if rel_dir != ".":
                folders.append(rel_dir)
            for name in sorted(filenames):
                if name.startswith(".") or name.endswith(".cadswarm-tmp"):
                    continue
                rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                try:
                    rec = self.reconcile(self.clean(rel))
                except FileError:
                    continue  # a file someone dropped in by hand with a name we wouldn't allow
                if rec:
                    out.append({"path": rel, "version": rec["version"], "size": rec["size"],
                                "author": rec["author"], "updated_at": rec["updated_at"]})
        return {"files": out, "folders": folders}

    def history(self, path):
        clean = self.clean(path)
        rows = self.db.execute("SELECT version, size, author, saved_at, reason FROM file_history WHERE path=? "
                               "ORDER BY saved_at", (clean,)).fetchall()
        current = self.reconcile(clean)
        return {"path": clean, "current": current, "older_versions": [dict(r) for r in rows]}

    # ----------------------------------------------------------- writing

    def _keep(self, clean, rec, data, reason):
        """Copy the current bytes into history before they are replaced, moved or deleted."""
        stored = os.path.join(self.history_dir, *clean.split("/")[:-1],
                              f"{clean.split('/')[-1]}@v{rec['version']}-{int(self.clock() * 1000)}")
        os.makedirs(os.path.dirname(stored), exist_ok=True)
        with open(stored, "wb") as f:
            f.write(data)
        self.db.execute("INSERT INTO file_history VALUES (?,?,?,?,?,?,?,?)",
                        (clean, rec["version"], rec["sha256"], rec["size"], rec["author"], self.clock(), stored, reason))

    def _atomic_write(self, clean, data):
        target = self.full(clean)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = f"{target}.{os.getpid()}.cadswarm-tmp"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)

    def _total_bytes(self):
        return self.db.execute("SELECT COALESCE(SUM(size), 0) AS s FROM files").fetchone()["s"]

    def free_name(self, clean):
        stem, ext = os.path.splitext(clean)
        n = 2
        while os.path.exists(self.full(f"{stem}-{n}{ext}")):
            n += 1
        return f"{stem}-{n}{ext}"

    def save(self, path, author, text=None, base64_data=None, if_exists="error", expected_version=None):
        clean = self.clean(path)
        if (text is None) == (base64_data is None):
            raise FileError(400, "bad_args", "Pass exactly one of text or base64.")
        ext = os.path.splitext(clean)[1].lower()
        if text is not None:
            if ext not in TEXT_EXT:
                raise FileError(400, "bad_type", f"{ext} files are binary; send them as base64.")
            data = text.encode("utf-8")
        else:
            try:
                data = base64.b64decode(base64_data, validate=True)
            except Exception:
                raise FileError(400, "bad_args", "base64 content couldn't be decoded.")
        if len(data) > MAX_FILE_BYTES:
            raise FileError(413, "too_big", f"Files can be at most {MAX_FILE_BYTES // (1024 * 1024)} MB.")
        if if_exists not in ("error", "new_name", "replace"):
            raise FileError(400, "bad_args", "if_exists must be error, new_name or replace.")
        current = self.reconcile(clean)
        replaced = None
        if current is not None:
            if if_exists == "error":
                raise FileError(409, "file_exists",
                                f"{clean!r} already exists (version {current['version']}, by {current['author']}). "
                                "Nothing was overwritten. Save under another name, use if_exists='new_name', or "
                                "read it and save with if_exists='replace' and expected_version.",
                                path=clean, currentVersion=current["version"], author=current["author"])
            if if_exists == "new_name":
                clean, current = self.free_name(clean), None
            else:
                if expected_version is None or int(expected_version) != current["version"]:
                    raise FileError(409, "stale_version",
                                    f"{clean!r} is at version {current['version']} (last saved by {current['author']}). "
                                    "Read it again and pass that expected_version to replace it.",
                                    path=clean, currentVersion=current["version"], author=current["author"])
                self._keep(clean, current, self.disk_bytes(clean), "replaced")
                replaced = current
        elif expected_version not in (None, 0):
            raise FileError(409, "stale_version", f"{clean!r} doesn't exist any more; it may have been moved or deleted.",
                            path=clean, currentVersion=0)
        if self._total_bytes() - (replaced["size"] if replaced else 0) + len(data) > MAX_TOTAL_BYTES:
            raise FileError(413, "quota", "The project's files folder is full (500 MB).")
        self._atomic_write(clean, data)
        version = (replaced["version"] + 1) if replaced else 1
        self.db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)",
                        (clean, version, sha256(data), len(data), author, self.clock()))
        return {"path": clean, "version": version, "size": len(data), "replaced": bool(replaced),
                "previous_author": replaced["author"] if replaced else None,
                "renamed_from": path.replace("\\", "/") if if_exists == "new_name" and clean != self.clean(path) else None}

    def make_folder(self, path):
        clean = self.clean(path, allow_folder=True)
        os.makedirs(self.full(clean), exist_ok=True)
        return {"folder": clean}

    def move(self, src, dst, author):
        s, d = self.clean(src), self.clean(dst)
        rec = self.reconcile(s)
        if rec is None:
            raise FileError(404, "not_found", f"There's no file {s!r}.")
        if self.reconcile(d) is not None:
            raise FileError(409, "file_exists", f"{d!r} already exists; nothing was moved or overwritten.", path=d)
        data = self.disk_bytes(s)
        self._keep(s, rec, data, f"moved to {d}")
        os.makedirs(os.path.dirname(self.full(d)), exist_ok=True)
        os.replace(self.full(s), self.full(d))
        self.db.execute("DELETE FROM files WHERE path=?", (s,))
        self.db.execute("INSERT INTO files VALUES (?,?,?,?,?,?)", (d, 1, rec["sha256"], rec["size"], author, self.clock()))
        return {"from": s, "to": d}

    def delete(self, path, author):
        """Soft delete: the file moves to the project's trash and its bytes stay in history."""
        clean = self.clean(path)
        rec = self.reconcile(clean)
        if rec is None:
            raise FileError(404, "not_found", f"There's no file {clean!r}.")
        self._keep(clean, rec, self.disk_bytes(clean), f"deleted by {author}")
        trash = os.path.join(self.trash_dir, f"{int(self.clock())}-{clean.replace('/', '__')}")
        os.makedirs(self.trash_dir, exist_ok=True)
        shutil.move(self.full(clean), trash)
        self.db.execute("DELETE FROM files WHERE path=?", (clean,))
        return {"deleted": clean, "recoverable": True}

    def restore(self, path, version, author):
        """Bring back an older version as a NEW version (the current one is kept in history too)."""
        clean = self.clean(path)
        row = self.db.execute("SELECT * FROM file_history WHERE path=? AND version=? ORDER BY saved_at DESC LIMIT 1",
                              (clean, int(version))).fetchone()
        if not row:
            raise FileError(404, "not_found", f"No saved version {version} of {clean!r}; see file_history.")
        with open(row["stored_as"], "rb") as f:
            data = f.read()
        current = self.reconcile(clean)
        if current is not None:
            self._keep(clean, current, self.disk_bytes(clean), f"replaced by restore of v{version}")
        self._atomic_write(clean, data)
        new_version = (current["version"] + 1) if current else int(version)
        self.db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)",
                        (clean, new_version, sha256(data), len(data), author, self.clock()))
        return {"path": clean, "version": new_version, "restored_from": int(version)}


def unique_path(folder, filename):
    """A path in `folder` that doesn't exist yet: name.ext, name-2.ext, name-3.ext…"""
    os.makedirs(folder, exist_ok=True)
    stem, ext = os.path.splitext(filename)
    candidate, n = os.path.join(folder, filename), 2
    while os.path.exists(candidate):
        candidate, n = os.path.join(folder, f"{stem}-{n}{ext}"), n + 1
    return candidate


def safe_label(label, default):
    """Turn a free-text label into something usable in a file name."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", label or "").strip(" .")[:60]
    return text or default
