"""The helper's components-library ops (the host routes library_* kinds here; see helper.py).

Writes return `_components` / `_component_deletes` so complete_op mirrors them into the `component`
table, and `_analyze` [{id, path}] for parts FreeCAD should measure. The host runs the worker's
analyze_component for each, then calls library_finalize with the results (bbox, volume, holes) so
meta.json and the table carry them, plus the settings each part implies.

Every op gets `_settings` (the team's setting rows) from the host and `_actor` (who asked).
"""
import base64
import os
import re
import time

from cadswarm import settings as team_settings
from cadswarm.library import pack as lib_pack
from cadswarm.library import recommend as lib_recommend
from cadswarm.library import search as lib_search
from cadswarm.library import standard, suggest
from cadswarm.library.fetch import FetchError, fetch
from cadswarm.library.store import CATEGORIES, ComponentStore, row
from cadswarm.storage import FileError


class LibraryError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def store_of(p):
    if getattr(p, "_library", None) is None:
        p._library = ComponentStore(p.layout["root"], p.files.storage, p.clock, trash=os.path.join(p.layout["trash"], "library"))
    return p._library


def _values(a):
    return team_settings.effective(a.get("_settings") or [])


def _author(a):
    return (a.get("_actor") or {}).get("name") or "someone"


def _wrap(fn):
    def run(p, a):
        try:
            return fn(p, a)
        except FetchError as e:
            raise LibraryError(e.code, str(e))
        except lib_pack.PackError as e:
            raise LibraryError(e.code, str(e))
        except ValueError as e:
            raise LibraryError("bad_args", str(e))
    return run


def _analyze_list(store, metas):
    return [{"id": m["id"], "path": store.part_path(m["id"]), "format": m["format"]} for m in metas]


def _summary(c, sizes=None):
    out = {k: c.get(k) for k in ("id", "name", "category", "source", "license", "tags", "uses") if c.get(k) is not None}
    if c.get("bbox"):
        b = c["bbox"]
        out["size_mm"] = [round(b[i + 3] - b[i], 2) for i in range(3)]
    if c.get("std"):
        out["family"] = c["std"]["family"]
        out["sizes"] = sizes if sizes else c["std"]["sizes"]
        if c["std"].get("lengths"):
            out["lengths"] = c["std"]["lengths"]
    if c.get("description"):
        out["description"] = c["description"][:200]
    return out


def all_components(p):
    """Team parts on disk plus the built-in standard families."""
    return store_of(p).all() + standard.virtual_components()


def _bytes_from(p, a):
    """(filename, bytes) from {path} in files/ or {base64, filename}."""
    if a.get("path"):
        clean, data = p.files.raw(a["path"])
        return os.path.basename(clean), data, clean
    if a.get("base64"):
        name = a.get("filename") or a.get("name")
        if not name:
            raise LibraryError("bad_args", "filename is required with base64 (it says what kind of file it is)")
        try:
            return name, base64.b64decode(a["base64"], validate=True), None
        except Exception:
            raise LibraryError("bad_args", "base64 content couldn't be decoded")
    raise LibraryError("bad_args", "Give the uploaded file's path in the project files, or base64 + filename.")


def _category(a):
    cat = (a.get("category") or "other").strip().lower()
    if cat not in CATEGORIES:
        raise LibraryError("bad_args", f"category must be one of {', '.join(CATEGORIES)}")
    return cat


def _added_result(store, metas, duplicates=()):
    return {"components": [_summary(m) for m in metas], "duplicates": list(duplicates),
            "_components": [row(m) for m in metas], "_analyze": _analyze_list(store, metas)}


def library_add(p, a):
    store = store_of(p)
    filename, data, from_files = _bytes_from(p, a)
    if filename.lower().endswith(".zip"):
        return library_import_pack(p, {**a, "_data": data})
    meta, dup = store.add(data, filename, _author(a), name=a.get("name"), category=_category(a), tags=a.get("tags"),
                          description=a.get("description", ""), source="upload",
                          source_url=a.get("source_url", ""), license_text=a.get("license", ""))
    if dup:
        return {"components": [_summary(meta)], "duplicates": [meta["name"]],
                "note": f"That file is already in the library as {meta['name']} ({meta['id']})."}
    if from_files and a.get("remove_upload", str(from_files).startswith("library-inbox/")):
        try:
            p.files.delete(from_files, _author(a))  # the inbox copy; the library keeps the part
        except FileError:
            pass
    return _added_result(store, [meta])


def library_import_url(p, a):
    url = a.get("url")
    if not url:
        raise LibraryError("bad_args", "url is required")
    name, data, final = fetch(url, opener=getattr(p, "url_opener", None), allow_private=getattr(p, "allow_private", False))
    if name.lower().endswith(".zip"):
        return library_import_pack(p, {**a, "_data": data})
    store = store_of(p)
    meta, dup = store.add(data, name, _author(a), name=a.get("name") or os.path.splitext(name)[0],
                          category=_category(a), tags=a.get("tags"), description=a.get("description", ""),
                          source="url", source_url=final, license_text=a.get("license", ""))
    if dup:
        return {"components": [_summary(meta)], "duplicates": [meta["name"]],
                "note": f"That file is already in the library as {meta['name']} ({meta['id']})."}
    out = _added_result(store, [meta])
    if not a.get("license"):
        out["note"] = ("Saved without a license note. Check the source's license before sharing a design that "
                       "uses it, and add it with library_update.")
    return out


def library_import_pack(p, a):
    data = a.get("_data")
    if data is None:
        _, data, _ = _bytes_from(p, a)
    store = store_of(p)
    added, skipped, pack_settings = lib_pack.import_pack(store, data, _author(a))
    out = _added_result(store, added, skipped)
    out["_analyze"] = [x for x, m in zip(out["_analyze"], added) if not m.get("bbox")]
    if pack_settings:
        out["pack_settings"] = pack_settings
        out["note"] = ("The pack also carries team settings. They were not applied; use suggest_setting for any "
                       "you think this project should use, and a person will accept them.")
    return out


def library_finalize(p, a):
    """Host-internal: merge the worker's analyses, compute suggestions, return rows for the table."""
    store = store_of(p)
    values = _values(a)
    metas = []
    for item in a.get("items") or []:
        try:
            meta = store.get(item["id"])
        except FileError:
            continue
        analysis = item.get("analysis") or {}
        merged = {**meta, **{k: analysis[k] for k in ("bbox", "volume", "features") if k in analysis}}
        metas.append(store.finalize(item["id"], analysis, suggest.suggestions(merged, values)))
    return {"components": [_summary(m) for m in metas], "_components": [row(m) for m in metas],
            "suggest": {m["id"]: m.get("suggest", []) for m in metas if m.get("suggest")}}


def library_update(p, a):
    cid = a.get("id")
    fields = {k: v for k, v in (a.get("fields") or {}).items()}
    if not fields:
        raise LibraryError("bad_args", "fields is required, e.g. {\"tags\": [\"m3\"], \"license\": \"CC-BY-4.0\"}")
    meta = store_of(p).update(cid, fields, _author(a))
    return {"component": _summary(meta), "_components": [row(meta)]}


def library_delete(p, a):
    if (a.get("_actor") or {}).get("kind") != "human":
        raise LibraryError("humans_only", "Only a person can remove parts from the team library. Ask a teammate.")
    meta = store_of(p).delete(a.get("id"), _author(a))
    return {"removed": meta["name"], "_component_deletes": [meta["id"]]}


def library_search(p, a):
    limit = max(1, min(int(a.get("limit") or 10), 30))
    hits = lib_search.search(all_components(p), a.get("query") or "", a.get("category"), a.get("constraints") or {}, limit)
    return {"results": [{**_summary(c, sizes), "score": s} for s, c, sizes in hits],
            "note": "" if hits else "Nothing matched. Try fewer words, or recommend_components with what it must do."}


def library_recommend(p, a):
    limit = max(1, min(int(a.get("limit") or 5), 10))
    return lib_recommend.recommend(all_components(p), a.get("need") or "", a.get("constraints") or {}, _values(a), limit)


def library_get(p, a):
    cid = a.get("id") or ""
    values = _values(a)
    if cid.startswith(standard.STD_PREFIX):
        fam_name = next((f for f in standard.families() if standard.STD_PREFIX + f.lower() == cid), None)
        if fam_name is None:
            raise LibraryError("not_found", f"No standard part {cid}")
        fam = standard.family(fam_name)
        holes = {}
        for size in fam["sizes"]:
            try:
                holes[size] = standard.hole_for(fam_name, size, values)
            except (KeyError, ValueError):
                pass
        return {"id": cid, "family": fam_name, **{k: fam[k] for k in ("name", "standard", "category", "params", "sizes")},
                "lengths": fam.get("lengths", []), "holes_for_team_settings": holes,
                "insert_with": "add_standard_part(family, size, length?, placement?)"}
    meta = store_of(p).get(cid)
    return {**{k: v for k, v in meta.items() if k != "hash"}, "insert_with": "insert_component(id, placement?, name?)"}


def standard_parts(p, a):
    out = {}
    for name, fam in standard.families().items():
        out[name] = {"name": fam["name"], "standard": fam["standard"], "category": fam["category"],
                     "sizes": list(fam["sizes"]), "lengths": fam.get("lengths", []), "params": fam["params"]}
    return {"families": out}


def suggest_settings(p, a):
    values = _values(a)
    if a.get("family"):
        meta = {"std": {"family": a["family"], "size": standard.size_key(standard.family(a["family"]), a.get("size", ""))},
                "name": a["family"]}
    else:
        meta = store_of(p).get(a.get("id"))
    return {"suggestions": suggest.suggestions(meta, values), "current": values,
            "how": "Pass any you agree with to suggest_setting with your reason; a person accepts them."}


def library_export_pack(p, a):
    store = store_of(p)
    settings_rows = a.get("_settings") or []
    pack_settings = {r["setting"]: team_settings.value_of(r) for r in settings_rows} if a.get("include_settings", True) else {}
    data, count = lib_pack.export_pack(store, a.get("ids"), pack_settings, a.get("name") or "library")
    if count == 0:
        raise LibraryError("not_found", "The library has no parts of the team's own to export yet.")
    label = re.sub(r"[^A-Za-z0-9 _-]+", "", a.get("name") or "library").strip() or "library"
    stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(p.clock()))
    saved = p.files.save(f"library-packs/{stamp} {label}.zip", _author(a), base64_data=base64.b64encode(data).decode(),
                         if_exists="new_name")
    return {"file": saved.get("path"), "parts": count, "size": len(data),
            "note": "Saved in the project files under library-packs/. Download it from Project files, then use "
                    "Import pack in another project."}


HANDLERS = {name: _wrap(fn) for name, fn in {
    "library_add": library_add,
    "library_import_url": library_import_url,
    "library_import_pack": library_import_pack,
    "library_finalize": library_finalize,
    "library_update": library_update,
    "library_delete": library_delete,
    "library_search": library_search,
    "library_recommend": library_recommend,
    "library_get": library_get,
    "library_export_pack": library_export_pack,
    "standard_parts": standard_parts,
    "suggest_settings": suggest_settings,
}.items()}
