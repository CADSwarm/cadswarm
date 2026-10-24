"""Library packs: the team's parts (and optionally its settings) in one .zip, to reuse in another project.

    manifest.json          {"format": "cadswarm.library-pack/1", "components": [meta...], "settings": {...}}
    parts/<id>/part.<ext>  each part's file

Importing skips parts already in the library (same file hash) and never imports settings by itself:
they come back as suggestions a person accepts.
"""
import io
import json
import os
import zipfile

FORMAT = "cadswarm.library-pack/1"
MAX_PARTS = 500
MAX_UNPACKED = 500 * 1024 * 1024


class PackError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def export_pack(store, ids=None, settings=None, name="library"):
    """Bytes of a pack with the given part ids (all when None)."""
    metas = store.all() if not ids else [store.get(i) for i in ids]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = {"format": FORMAT, "name": name, "components": [], "settings": settings or {}}
        for m in metas:
            path = store.part_path(m["id"])
            arc = f"parts/{m['id']}/{os.path.basename(path)}"
            z.write(path, arc)
            manifest["components"].append({**m, "file": arc})
        z.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    return buf.getvalue(), len(metas)


def read_pack(data):
    """(manifest, {arcname: bytes}) after checking the pack is one of ours and not a zip bomb."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackError("bad_pack", "That isn't a zip file.")
    with z:
        try:
            manifest = json.loads(z.read("manifest.json"))
        except KeyError:
            raise PackError("bad_pack", "That zip has no manifest.json, so it isn't a CADSwarm library pack.")
        except ValueError:
            raise PackError("bad_pack", "The pack's manifest.json is not valid JSON.")
        if manifest.get("format") != FORMAT:
            raise PackError("bad_pack", f"Unknown pack format {manifest.get('format')!r}.")
        comps = manifest.get("components") or []
        if len(comps) > MAX_PARTS:
            raise PackError("too_big", f"Packs hold at most {MAX_PARTS} parts.")
        total = sum(i.file_size for i in z.infolist())
        if total > MAX_UNPACKED:
            raise PackError("too_big", "That pack unpacks to more than 500 MB.")
        files = {}
        for c in comps:
            arc = str(c.get("file", ""))
            if not arc.startswith("parts/") or ".." in arc.split("/"):
                raise PackError("bad_pack", f"Bad file path in the pack: {arc!r}")
            try:
                files[arc] = z.read(arc)
            except KeyError:
                raise PackError("bad_pack", f"The pack lists {arc} but doesn't contain it.")
    return manifest, files


def import_pack(store, data, author):
    """Add the pack's parts to the store. Returns (added metas, skipped names, pack settings)."""
    manifest, files = read_pack(data)
    added, skipped = [], []
    for c in manifest.get("components") or []:
        blob = files[c["file"]]
        meta, dup = store.add(
            blob, os.path.basename(c["file"]), author, name=c.get("name"), category=c.get("category", "other"),
            tags=c.get("tags"), description=c.get("description", ""), source="pack",
            source_url=c.get("source_url", ""), license_text=c.get("license", ""),
            extra={k: c[k] for k in ("bbox", "volume", "features", "suggest") if k in c})
        (skipped if dup else added).append(meta if not dup else meta.get("name"))
    return added, skipped, manifest.get("settings") or {}
