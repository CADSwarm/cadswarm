"""SpaceHub's components-library and team-settings routes (mixed into SpaceHub in space_hub.py).

Browsing, searching and recommending run here, over the `v_component` rows plus the built-in standard
families, so they answer instantly even while the host is busy. Adding, importing, exporting and placing
parts are host ops (library_* in the helper, insert_* in FreeCAD). Settings are reducers: people call
set_setting / resolve_suggestion; anyone (mainly AI helpers) calls suggest_setting.

Stdlib only.
"""
import base64
import json
import os
import re

from . import settings as team_settings
from . import ticket
from .client import HubRequestError
from .library import recommend as lib_recommend
from .library import search as lib_search
from .library import standard, suggest

MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # base64 inside one reducer call; bigger parts go in through project files
SETTING_EVENT_KINDS = ("setting_changed", "setting_suggested", "library")


def _bad(msg, code="bad_args", status=400):
    return HubRequestError(status, {"error": msg, "code": code})


def _js(text, default):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def component_meta(row):
    """A v_component row as the meta dict library/ modules use."""
    return {"id": row["id"], "name": row["name"], "category": row["category"], "tags": row.get("tags") or [],
            "description": row.get("description", ""), "source": row["source"], "source_url": row.get("source_url", ""),
            "license": row.get("license", ""), "author": row.get("author", ""), "format": row.get("format", ""),
            "size": row.get("size", 0), "version": row.get("version", 1), "bbox": row.get("bbox") or [],
            "volume": row.get("volume", 0.0), "features": _js(row.get("features_json"), {}),
            "std": _js(row.get("std_json"), None), "suggest": _js(row.get("suggest_json"), []),
            "uses": row.get("uses", 0), "updated_ms": row.get("updated_ms", 0)}


def part_name(label, taken):
    """A FreeCAD object name for a placed part: 'M3x12 socket head cap screw' -> 'M3x12SocketHead1'."""
    words = re.findall(r"[A-Za-z0-9]+", str(label))[:3] or ["Part"]
    base = "".join(w[:1].upper() + w[1:] for w in words)[:24]
    if base[0].isdigit():
        base = "P" + base
    n = 1
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"


class LibraryRoutes:
    """Mixed into SpaceHub: needs self._rows, self._f, self.run_op, self.me()."""

    def library_routes(self):
        return [
            ("GET", r"/library", self.library_list), ("POST", r"/library/search", self.library_search),
            ("POST", r"/library/recommend", self.library_recommend), ("POST", r"/library/hole", self.library_hole),
            ("POST", r"/library/upload", self.library_upload), ("POST", r"/library/import-url", self.library_import_url),
            ("POST", r"/library/import-pack", self.library_import_pack),
            ("POST", r"/library/export-pack", self.library_export_pack),
            ("POST", r"/library/insert", self.library_insert),
            ("GET", r"/library/(?P<cid>c_[a-z0-9_]+)", self.library_get),
            ("GET", r"/library/(?P<cid>c_[a-z0-9_]+)/preview", self.library_preview),
            ("POST", r"/library/(?P<cid>c_[a-z0-9_]+)/update", self.library_update),
            ("POST", r"/library/(?P<cid>c_[a-z0-9_]+)/delete", self.library_delete),
            ("GET", r"/settings", self.settings_get), ("POST", r"/settings", self.settings_set),
            ("POST", r"/settings/reset", self.settings_reset), ("POST", r"/settings/suggest", self.settings_suggest),
            ("POST", r"/settings/suggestions/(?P<sid>[^/]+)", self.settings_resolve),
        ]

    # ------------------------------------------------------------------ reading

    def team_components(self):
        return [component_meta(r) for r in self._rows("v_component")]

    def all_components(self):
        return self.team_components() + standard.virtual_components()

    def setting_values(self):
        return team_settings.effective(self._rows("v_setting"))

    def library_list(self, category=None, source=None):
        comps = self.all_components()
        if category:
            comps = [c for c in comps if c["category"] == category]
        if source:
            comps = [c for c in comps if c["source"] == source]
        comps.sort(key=lambda c: (c["source"] == "builtin", -int(c.get("uses") or 0), c["name"].lower()))
        return {"components": comps, "categories": sorted({c["category"] for c in comps})}

    def library_search(self, body):
        limit = max(1, min(int(body.get("limit") or 10), 30))
        hits = lib_search.search(self.all_components(), body.get("query") or "", body.get("category"),
                                 body.get("constraints") or {}, limit)
        out = []
        for score, c, sizes in hits:
            item = {k: c.get(k) for k in ("id", "name", "category", "source", "license", "tags", "uses", "description")}
            item["score"] = score
            if c.get("bbox"):
                b = c["bbox"]
                item["size_mm"] = [round(b[i + 3] - b[i], 2) for i in range(3)]
            if c.get("std"):
                item.update({"family": c["std"]["family"], "sizes": sizes or c["std"]["sizes"],
                             "lengths": c["std"].get("lengths", [])})
            out.append(item)
        return {"results": out, "note": "" if out else
                "Nothing matched. Try fewer words, recommend_components with what the part must do, or "
                "import_component_from_url with a STEP file you found."}

    def library_recommend(self, body):
        return lib_recommend.recommend(self.all_components(), body.get("need") or "", body.get("constraints") or {},
                                       self.setting_values(), max(1, min(int(body.get("limit") or 5), 10)))

    def library_hole(self, body):
        try:
            return standard.hole_for(body.get("family"), body.get("size"), self.setting_values(), body.get("purpose"))
        except (KeyError, ValueError) as e:
            raise _bad(str(e))

    def library_get(self, cid):
        values = self.setting_values()
        if cid.startswith(standard.STD_PREFIX):
            fam_name = next((f for f in standard.families() if standard.STD_PREFIX + f.lower() == cid), None)
            if fam_name is None:
                raise _bad(f"No standard part {cid}", "not_found", 404)
            fam = standard.family(fam_name)
            holes = {}
            for size in fam["sizes"]:
                try:
                    holes[size] = standard.hole_for(fam_name, size, values)
                except (KeyError, ValueError):
                    pass
            return {"id": cid, "family": fam_name, "name": fam["name"], "standard": fam["standard"],
                    "category": fam["category"], "params": fam["params"], "sizes": fam["sizes"],
                    "lengths": fam.get("lengths", []), "holes_for_team_settings": holes, "source": "builtin"}
        rows = self._rows("v_component", f"id = '{cid}'")
        if not rows:
            raise _bad(f"There is no part {cid} in the library.", "not_found", 404)
        meta = component_meta(rows[0])
        meta["suggest_now"] = suggest.suggestions(meta, values)
        return meta

    def library_preview(self, cid):
        """The part's preview mesh (binary STL bytes), or 404 while FreeCAD hasn't measured it yet."""
        rows = self._rows("v_component_preview", f"id = '{cid}'")
        if not rows or not rows[0].get("mesh"):
            raise _bad("No preview yet", "not_found", 404)
        b = rows[0]["mesh"]
        return bytes.fromhex(b) if isinstance(b, str) else bytes(b)

    # ------------------------------------------------------------------ writing

    def _file_args(self, body):
        """{base64, filename} from {local_path} (a file on this computer) or {base64, filename} or {path} in files/."""
        if body.get("local_path"):
            path = os.path.expanduser(str(body["local_path"]))
            if not os.path.isfile(path):
                raise _bad(f"No file at {path}", "not_found", 404)
            if os.path.getsize(path) > MAX_UPLOAD_BYTES:
                raise _bad(f"That file is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Save it into the project "
                           "files first (files/library-inbox/) and add it from there.", "too_big", 413)
            with open(path, "rb") as f:
                return {"base64": base64.b64encode(f.read()).decode(), "filename": os.path.basename(path)}
        if body.get("base64"):
            return {"base64": body["base64"], "filename": body.get("filename") or body.get("name")}
        if body.get("path"):
            return {"path": body["path"]}
        raise _bad("Give local_path (a file on this computer), path (in the project files) or base64 + filename.")

    def library_upload(self, body):
        args = self._file_args(body)
        args.update({k: body[k] for k in ("name", "category", "tags", "description", "license", "source_url")
                     if body.get(k) is not None})
        return self.run_op("library_add", args)

    def library_import_url(self, body):
        if not body.get("url"):
            raise _bad("url is required")
        return self.run_op("library_import_url", {k: body[k] for k in ("url", "name", "category", "tags",
                                                                        "description", "license") if body.get(k)})

    def library_import_pack(self, body):
        return self.run_op("library_import_pack", self._file_args(body))

    def library_export_pack(self, body):
        return self.run_op("library_export_pack", {k: body[k] for k in ("ids", "name", "include_settings")
                                                    if body.get(k) is not None})

    def library_update(self, body, cid):
        return self.run_op("library_update", {"id": cid, "fields": body.get("fields") or body})

    def library_delete(self, body, cid):
        return self.run_op("library_delete", {"id": cid})

    def library_insert(self, body):
        """Place a library part. Team parts: {id}. Standard parts: {id: c_std_<family> | family, size, length}."""
        cid = body.get("id") or ""
        taken = {o["name"] for o in self._rows("v_object")}
        placement = body.get("placement")
        family = body.get("family")
        if not family and cid.startswith(standard.STD_PREFIX):
            family = next((f for f in standard.families() if standard.STD_PREFIX + f.lower() == cid), None)
        if family:
            try:
                resolved = standard.resolve(family, body.get("size"), body.get("length"))
            except (KeyError, ValueError) as e:
                raise _bad(str(e))
            name = body.get("name") or part_name(resolved["label"], taken)
            args = {"family": family, "size": resolved["size"], "name": name, "label": body.get("label") or resolved["label"]}
            if resolved.get("length"):
                args["length"] = resolved["length"]
            if placement:
                args["placement"] = placement
            r = self.run_op("insert_standard_part", args)
            r["hole"] = standard.hole_for(family, resolved["size"], self.setting_values())
            return r
        if not cid:
            raise _bad("id is required (from search_components), or family + size for a standard part")
        rows = self._rows("v_component", f"id = '{cid}'") if re.fullmatch(r"c_[a-z0-9_]+", cid) else []
        if not rows:
            raise _bad(f"There is no part {cid} in the library.", "not_found", 404)
        name = body.get("name") or part_name(rows[0]["name"], taken)
        args = {"component": cid, "name": name, "label": body.get("label") or rows[0]["name"]}
        if placement:
            args["placement"] = placement
        return self.run_op("insert_component", args)

    # ----------------------------------------------------------------- settings

    def settings_get(self):
        rows = {r["setting"]: r for r in self._rows("v_setting")}
        names = self._names()
        out = []
        for s in team_settings.specs():
            r = rows.get(s["key"])
            value = team_settings.value_of(r) if r else s["default"]
            out.append({**{k: v for k, v in s.items()}, "value": value, "is_default": r is None,
                        "changed_by": names.get(r["set_by"], "?") if r else None})
        comps = {c["id"]: c["name"] for c in self.team_components()}
        pending = []
        for g in sorted(self._rows("v_suggestion"), key=lambda g: g["created_ms"], reverse=True):
            if g["status"] != "pending":
                continue
            spec = team_settings.spec(g["setting"]) or {}
            value = g["text"] if spec.get("kind") == "choice" else g["num"]
            pending.append({"id": g["id"], "setting": g["setting"], "label": spec.get("label", g["setting"]),
                            "value": value, "unit": spec.get("unit", ""), "reason": g["reason"],
                            "component": g["component"], "component_name": comps.get(g["component"], g["component"]),
                            "by": names.get(g["by"], "?"),
                            "current": next((x["value"] for x in out if x["key"] == g["setting"]), None)})
        return {"settings": out, "suggestions": pending}

    def settings_set(self, body):
        key = body.get("setting") or body.get("key")
        try:
            num, text = team_settings.check(key, body.get("value"))
        except ValueError as e:
            raise _bad(str(e))
        self._f("set_setting", key, num, text)
        return {"setting": key, "value": text or num}

    def settings_reset(self, body):
        key = body.get("setting") or body.get("key")
        if team_settings.spec(key) is None:
            raise _bad(f"Unknown setting {key!r}")
        self._f("reset_setting", key)
        return {"setting": key, "value": team_settings.spec(key)["default"], "reset": True}

    def settings_suggest(self, body):
        key = body.get("setting") or body.get("key")
        try:
            num, text = team_settings.check(key, body.get("value"))
        except ValueError as e:
            raise _bad(str(e))
        reason = (body.get("reason") or "").strip()
        if not reason:
            raise _bad("reason is required: the person accepting it reads this")
        sid = ticket.new_id("g_")
        self._f("suggest_setting", sid, key, num, text, reason, body.get("component") or "")
        return {"suggested": key, "value": text or num, "id": sid,
                "note": "A person accepts or dismisses it in Settings; until then the current value stays."}

    def settings_resolve(self, body, sid):
        accept = bool(body.get("accept"))
        self._f("resolve_suggestion", sid, accept)
        return {"suggestion": sid, "accepted": accept}
