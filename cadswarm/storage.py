"""How much room a project may use, and how people get it back.

Two limits protect the host's computer, in this order:

* The disk guard: before anything that adds bytes (a save, restore, move, snapshot or export) the
  volume's free space is checked, and the write is refused if it would leave less than a reserve
  (2 GB, or 10% of the volume if that is smaller). This is the real safety: it holds even when the
  project budget is switched off.
* The project budget: live files + older versions + snapshots + exports, 20 GB by default. The host
  can change it (a team setting stored next to the hub's database; the env var
  CADSWARM_PROJECT_LIMIT_GB overrides it; 0 means no project limit).

Older versions are trimmed automatically (see `prune_path`). Snapshots and exports are things people
asked for, so they are never deleted automatically; `prune` removes them only when a person asks.
Callers serialize access (the Hub's lock); this class does no locking of its own.
"""
import json
import os
import shutil

GB = 1024 ** 3
DEFAULTS = {
    "project_limit_gb": 20,    # 0 = no project limit (the disk guard still applies)
    "disk_reserve_gb": None,   # None = automatic: 2 GB, or 10% of the volume if smaller
    "history_keep": 20,        # newest older versions kept per file ...
    "history_hours": 24,       # ... plus everything saved within this many hours
}
SETTINGS_FILE = "storage.json"
DAY = 24 * 3600


class FileError(Exception):
    def __init__(self, status, code, message, **data):
        super().__init__(message)
        self.status, self.code, self.data = status, code, data


def human(n):
    """Bytes in words a person uses: '320 MB', '1.5 GB'."""
    for unit, size in (("GB", GB), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            v = n / size
            return f"{v:.1f} {unit}" if v < 10 else f"{v:.0f} {unit}"
    return f"{n} bytes"


def _number(v, field, minimum=0):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < minimum:
        raise FileError(400, "bad_args", f"{field} must be a number that is at least {minimum}.")
    return v


def _tree_bytes(folder):
    total = 0
    for d, _, names in os.walk(folder):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(d, n))
            except OSError:
                pass
    return total


class Storage:
    def __init__(self, layout, db, clock):
        self.layout, self.db, self.clock = layout, db, clock
        self.path = os.path.join(layout["internal"], SETTINGS_FILE)

    # ------------------------------------------------------------ settings

    def settings(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = {}
        out = dict(DEFAULTS)
        if isinstance(saved, dict):
            out.update({k: v for k, v in saved.items() if k in DEFAULTS})
        return out

    def update(self, changes):
        if not isinstance(changes, dict) or set(changes) - set(DEFAULTS):
            raise FileError(400, "bad_args", "Unknown setting. Known: " + ", ".join(DEFAULTS))
        new = self.settings()
        for k, v in changes.items():
            if k == "disk_reserve_gb" and v is None:
                new[k] = None
            elif k in ("history_keep",):
                new[k] = int(_number(v, k, 1))
            else:
                new[k] = _number(v, k)
        os.makedirs(self.layout["internal"], exist_ok=True)
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(new, f, indent=1)
        os.replace(tmp, self.path)
        return new

    def limit(self):
        """(bytes, source): 0 bytes = no project limit. The env var wins over the saved setting."""
        raw = os.environ.get("CADSWARM_PROJECT_LIMIT_GB")
        if raw not in (None, ""):
            try:
                gb = float(raw)
                if gb >= 0:
                    return int(gb * GB), "env"
            except ValueError:
                pass  # junk in the environment is ignored, not a crash
        source = "setting" if "project_limit_gb" in self._saved() else "default"
        return int(self.settings()["project_limit_gb"] * GB), source

    def _saved(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
            return saved if isinstance(saved, dict) else {}
        except (OSError, ValueError):
            return {}

    # ------------------------------------------------------------ disk guard

    def _volume(self):
        p = self.layout["root"]
        while p and not os.path.exists(p):
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        return p

    def free_bytes(self):
        try:
            return shutil.disk_usage(self._volume()).free
        except OSError:
            return None  # can't tell: don't block work on a guess

    def reserve_bytes(self):
        gb = self.settings()["disk_reserve_gb"]
        if gb is not None:
            return int(gb * GB)
        try:
            return int(min(2 * GB, shutil.disk_usage(self._volume()).total * 0.10))
        except OSError:
            return 2 * GB

    # ---------------------------------------------------------------- usage

    def usage(self):
        db = self.db
        live = db.execute("SELECT COALESCE(SUM(size), 0) AS s FROM files").fetchone()["s"]
        old = db.execute("SELECT COALESCE(SUM(size), 0) AS s FROM file_history").fetchone()["s"]
        u = {"files": live, "old_versions": old, "snapshots": _tree_bytes(self.layout["snapshots"]),
             "exports": _tree_bytes(self.layout["exports"])}
        u["total"] = sum(u.values())
        try:
            u["model"] = os.path.getsize(self.layout["model"])  # shown for information; not part of the budget
        except OSError:
            u["model"] = 0
        return u

    def status(self):
        limit, source = self.limit()
        return {"usage": self.usage(), "limit_bytes": limit, "limit_source": source,
                "free_disk_bytes": self.free_bytes(), "reserve_bytes": self.reserve_bytes(),
                "settings": self.settings(),
                "old_version_files": db_count(self.db)}

    def check_add(self, extra, transient=0):
        """Refuse a write that adds `extra` bytes. `transient` is room needed only while it runs
        (e.g. the old copy beside the new one during a replace)."""
        free = self.free_bytes()
        if free is not None and free - extra - transient < self.reserve_bytes():
            raise FileError(507, "disk_full",
                            f"The computer hosting this project is nearly out of disk space ({human(max(free, 0))} free; "
                            f"CadSwarm keeps {human(self.reserve_bytes())} free so Windows keeps working). Nothing was saved. "
                            "Ask a person on the team to free up space: delete things from that drive, or in FreeCAD use "
                            "Team tools > Storage and free up space. Don't delete files to make room yourself.")
        limit, _ = self.limit()
        if limit:
            u = self.usage()
            if u["total"] + extra > limit:
                raise FileError(413, "quota",
                                f"This project is full: it uses {human(u['total'])} of its {human(limit)} limit "
                                f"(your files {human(u['files'])}, older versions {human(u['old_versions'])}, "
                                f"snapshots {human(u['snapshots'])}, exports {human(u['exports'])}) and this needs "
                                f"{human(extra)} more. Nothing was saved. Ask a person on the team to free up space "
                                "(Team tools > Storage and free up space in FreeCAD) or raise the limit. "
                                "Don't delete files to make room yourself.")

    # ------------------------------------------------------------- pruning

    def _drop(self, rows):
        """Forget these history rows and delete their bytes. Returns bytes freed."""
        freed = 0
        for r in rows:
            self.db.execute("DELETE FROM file_history WHERE rowid=?", (r["rowid"],))
            try:
                os.remove(r["stored_as"])
            except OSError:
                pass
            freed += r["size"]
        return freed

    def _versions(self, path):
        return self.db.execute("SELECT rowid, * FROM file_history WHERE path=? COLLATE NOCASE "
                               "ORDER BY saved_at DESC, rowid DESC", (path,)).fetchall()

    def prune_path(self, path):
        """Automatic retention, after each history write: keep the newest `history_keep` older versions
        and everything saved within `history_hours`. The newest version is always among those, so a file
        with no live copy (deleted or moved away) can still be undone."""
        s = self.settings()
        cutoff = self.clock() - s["history_hours"] * 3600
        rows = self._versions(path)
        doomed = [r for i, r in enumerate(rows)
                  if i >= s["history_keep"] and r["saved_at"] < cutoff]
        return self._drop(doomed)

    def prune(self, options):
        """A person's 'free up space' request. Returns what was freed per kind."""
        known = {"keep_versions", "snapshots_older_than_days", "keep_newest_snapshots", "exports_older_than_days"}
        if not isinstance(options, dict) or set(options) - known:
            raise FileError(400, "bad_args", "Unknown option. Known: " + ", ".join(sorted(known)))
        keep = options.get("keep_versions")
        if keep is not None:
            keep = int(_number(keep, "keep_versions", 1))
        snap_days, exp_days = options.get("snapshots_older_than_days"), options.get("exports_older_than_days")
        for field, v in (("snapshots_older_than_days", snap_days), ("exports_older_than_days", exp_days)):
            if v is not None:
                _number(v, field)
        keep_snaps = int(_number(options.get("keep_newest_snapshots", 3), "keep_newest_snapshots"))
        freed = {"old_versions": 0, "snapshots": 0, "exports": 0}
        removed = dict(freed)
        if keep is not None:
            paths = [r["path"] for r in self.db.execute("SELECT MIN(path) AS path FROM file_history GROUP BY lower(path)")]
            for p in paths:
                rows = self._versions(p)
                doomed = rows[keep:]  # keep >= 1, so a deleted file's latest version always survives
                removed["old_versions"] += len(doomed)
                freed["old_versions"] += self._drop(doomed)
        for kind, days, protect in (("snapshots", snap_days, keep_snaps), ("exports", exp_days, 0)):
            if days is None:
                continue
            files = []
            for d, _, names in os.walk(self.layout[kind]):
                for n in names:
                    full = os.path.join(d, n)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    files.append((st.st_mtime, n, full, st.st_size))
            files.sort(reverse=True)
            cutoff = self.clock() - days * DAY
            for mtime, _, full, size in files[protect:]:
                if mtime < cutoff:
                    try:
                        os.remove(full)
                    except OSError:
                        continue
                    freed[kind] += size
                    removed[kind] += 1
        freed["total"] = sum(freed.values())
        return {"freed": freed, "files_removed": removed}


def db_count(db):
    return db.execute("SELECT COUNT(DISTINCT lower(path)) AS n FROM file_history").fetchone()["n"]
