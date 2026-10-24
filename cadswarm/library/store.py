"""The library's files on the host: <project>/library/<id>/part.<ext> plus meta.json.

The `component` table in SpacetimeDB mirrors meta.json (library_* op results carry `_components`), so
every teammate sees the library live while the bytes stay on the hosting computer, like files/.
Same rules as files/: size limits, the disk guard and project budget, atomic writes, and deletes go to
the trash instead of vanishing.
"""
import hashlib
import json
import os
import re
import shutil
import time

from cadswarm.files import MAX_BINARY_BYTES
from cadswarm.storage import FileError

FORMATS = {".step": "step", ".stp": "step", ".fcstd": "fcstd", ".stl": "stl"}
CATEGORIES = ("fastener", "bearing", "insert", "board", "electronics", "enclosure", "motion", "structural",
              "hardware", "other")
SOURCES = ("upload", "url", "pack", "builtin")
EDITABLE = ("name", "category", "tags", "description", "license", "source_url")
ID_RE = re.compile(r"^c_[a-z0-9_]{1,46}$")
ROW_KEYS = ("id", "name", "category", "tags", "description", "source", "source_url", "license", "author",
            "format", "hash", "size", "version", "bbox", "volume", "features", "std", "suggest")


def slug(text, limit=24):
    s = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
    return (s or "part")[:limit].strip("_") or "part"


def format_of(filename):
    ext = os.path.splitext(str(filename))[1].lower()
    if ext not in FORMATS:
        raise FileError(400, "bad_type", f"Library parts must be STEP (.step/.stp), FreeCAD (.FCStd) or STL "
                                         f"(.stl) files, not {ext or 'a file without an extension'}.")
    return FORMATS[ext], (".FCStd" if FORMATS[ext] == "fcstd" else ext)


def clean_tags(tags):
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,;]", tags)]
    return sorted({str(t).strip().lower()[:40] for t in (tags or []) if str(t).strip()})[:30]


def row(meta):
    """The fields the `component` table holds (what `_components` carries to complete_op)."""
    return {k: meta.get(k) for k in ROW_KEYS if k in meta}


class ComponentStore:
    def __init__(self, root, storage, clock=time.time, trash=None):
        self.root = os.path.join(os.path.abspath(root), "library")
        self.trash = trash or os.path.join(os.path.abspath(root), ".cadswarm-team", "trash", "library")
        self.storage = storage
        self.clock = clock
        os.makedirs(self.root, exist_ok=True)

    # -- reading ---------------------------------------------------------------------------------
    def _dir(self, cid):
        if not isinstance(cid, str) or not ID_RE.match(cid):
            raise FileError(400, "bad_args", f"{cid!r} is not a library part id (they look like c_m3_bracket_1a2b3c).")
        return os.path.join(self.root, cid)

    def get(self, cid):
        path = os.path.join(self._dir(cid), "meta.json")
        if not os.path.exists(path):
            raise FileError(404, "not_found", f"There is no part {cid} in the library.")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def all(self):
        out = []
        for name in sorted(os.listdir(self.root)):
            if ID_RE.match(name) and os.path.exists(os.path.join(self.root, name, "meta.json")):
                try:
                    out.append(self.get(name))
                except (OSError, ValueError):
                    continue  # a half-written or hand-edited folder: skip it, never fail the listing
        return out

    def part_path(self, cid):
        meta = self.get(cid)
        ext = ".FCStd" if meta["format"] == "fcstd" else "." + meta["format"]
        path = os.path.join(self._dir(cid), "part" + ext)
        if not os.path.exists(path):
            raise FileError(404, "not_found", f"The file for library part {cid} is missing on the host.")
        return path

    def find_hash(self, digest):
        return next((m for m in self.all() if m.get("hash") == digest), None)

    # -- writing ---------------------------------------------------------------------------------
    def _write_meta(self, cid, meta):
        path = os.path.join(self._dir(cid), "meta.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, sort_keys=True)
        os.replace(tmp, path)

    def add(self, data, filename, author, name=None, category="other", tags=None, description="",
            source="upload", source_url="", license_text="", extra=None):
        """Store a part file; returns (meta, duplicate). A file already in the library is not stored twice."""
        fmt, ext = format_of(filename)
        if not data:
            raise FileError(400, "bad_args", "That file is empty.")
        if len(data) > MAX_BINARY_BYTES:
            raise FileError(413, "too_big", f"Library parts can be at most {MAX_BINARY_BYTES // (1024 * 1024)} MB.")
        digest = hashlib.sha256(data).hexdigest()
        dup = self.find_hash(digest)
        if dup is not None:
            return dup, True
        if category not in CATEGORIES:
            category = "other"
        if source not in SOURCES:
            source = "upload"
        name = (name or os.path.splitext(os.path.basename(str(filename)))[0]).strip()[:80] or "Part"
        self.storage.check_add(len(data))
        cid = f"c_{slug(name)}_{digest[:6]}"
        folder = self._dir(cid)
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, "part" + ext + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(folder, "part" + ext))
        meta = {
            "id": cid, "name": name, "category": category, "tags": clean_tags(tags),
            "description": str(description or "")[:2000], "source": source, "source_url": str(source_url or "")[:500],
            "license": str(license_text or "")[:300], "author": author, "format": fmt, "hash": digest,
            "size": len(data), "version": 1, "original_name": os.path.basename(str(filename))[:120],
            "created": self.clock(), "bbox": [], "volume": 0.0, "features": {}, "suggest": [],
        }
        meta.update(extra or {})
        self._write_meta(cid, meta)
        return meta, False

    def finalize(self, cid, analysis, suggest=None):
        """Merge the FreeCAD analysis (bbox, volume, features) and the settings it implies into meta.json."""
        meta = self.get(cid)
        for k in ("bbox", "volume", "features"):
            if k in (analysis or {}):
                meta[k] = analysis[k]
        if suggest is not None:
            meta["suggest"] = suggest
        if analysis and analysis.get("error"):
            meta["analysis_error"] = str(analysis["error"])[:300]
        self._write_meta(cid, meta)
        return meta

    def update(self, cid, fields, author):
        meta = self.get(cid)
        unknown = set(fields) - set(EDITABLE)
        if unknown:
            raise FileError(400, "bad_args", f"Can't change {', '.join(sorted(unknown))}; editable: {', '.join(EDITABLE)}")
        for k, v in fields.items():
            if k == "tags":
                meta["tags"] = clean_tags(v)
            elif k == "category":
                if v not in CATEGORIES:
                    raise FileError(400, "bad_args", f"category must be one of {', '.join(CATEGORIES)}")
                meta["category"] = v
            else:
                meta[k] = str(v or "")[:2000 if k == "description" else 300]
        meta["version"] = int(meta.get("version", 1)) + 1
        meta["updated_by"] = author
        self._write_meta(cid, meta)
        return meta

    def delete(self, cid, author):
        """Move the part to the trash (kept, like deleted project files) and return its last meta."""
        meta = self.get(cid)
        os.makedirs(self.trash, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        dest = os.path.join(self.trash, f"{cid}-{stamp}")
        n = 2
        while os.path.exists(dest):
            dest = os.path.join(self.trash, f"{cid}-{stamp}-{n}")
            n += 1
        shutil.move(self._dir(cid), dest)
        meta["deleted_by"] = author
        return meta
