"""Which team settings a library part implies, and why.

Deterministic rules over what we know about a part: its standard family (a 608 bearing, an M3 screw), the
features FreeCAD found in an uploaded file (holes and their diameters, overall size), and the team's
material. AI helpers add judgement on top with suggest_setting; nothing here changes a setting, a person
accepts each suggestion.
"""
from cadswarm import settings as team_settings
from cadswarm.library import standard

PLASTICS = {"pla", "petg", "abs", "nylon_pa12"}


def _printed(values):
    return values.get("default_material", "pla") in PLASTICS


def _s(setting, value, reason):
    return {"setting": setting, "value": value, "reason": reason}


def for_standard(family_name, size, values):
    fam = standard.family(family_name)
    shape, printed = fam["shape"], _printed(values)
    out = []
    if shape == "bearing":
        if printed:
            out.append(_s("tol_press_fit", 0.0, f"{size} bearings: printed holes come out slightly undersize, so a "
                                                  "nominal-size seat already grips; a negative fit risks cracking the part."))
        else:
            out.append(_s("tol_press_fit", -0.02, f"{size} bearing in a machined seat: about 0.02 mm interference "
                                                    "(close to an ISO K6/N6 housing) holds it without a retainer."))
        out.append(_s("tol_slip_fit", 0.1 if printed else 0.01,
                      f"the {size}'s shaft should slide into the inner ring by hand"))
    elif shape == "disc":
        out.append(_s("tol_press_fit", 0.05 if printed else -0.02,
                      f"{size} magnets press-fit into {'printed pockets with a little extra room (they are brittle)' if printed else 'machined pockets'}"))
    elif shape == "insert":
        out.append(_s("thread_mode", "heat_set_insert", f"{size} heat-set inserts are in use; screws into printed "
                                                        "parts should get insert-sized holes"))
    elif shape == "pin":
        out.append(_s("tol_slip_fit", 0.15 if printed else 0.02,
                      f"{size} mm dowel pins locate parts; a {'0.15 mm gap lets them slide into printed holes' if printed else '0.02 mm gap is a close location fit'}"))
    elif shape in ("socket_head", "button_head", "hex_nut", "washer"):
        out.append(_s("thread_mode", "heat_set_insert" if printed else "tapped",
                      f"{size} screws {'in printed parts last much longer in heat-set inserts' if printed else 'in metal parts go into tapped holes'}"))
    return out


def for_features(meta, values):
    out = []
    feats = meta.get("features") or {}
    holes = [h for h in feats.get("holes", []) if h.get("d")]
    # Holes that match an ISO 273 clearance size tell us which class the part was designed with.
    classes = {}
    for h in holes:
        for size, row in standard.catalog()["clearance_holes"].items():
            if size.startswith("_"):
                continue
            for cls, d in zip(("close", "normal", "loose"), row):
                if abs(h["d"] - d) <= 0.02:
                    classes[cls] = classes.get(cls, 0) + h.get("count", 1)
    if classes:
        cls = max(classes, key=classes.get)
        out.append(_s("hole_clearance_class", cls, f"{meta.get('name', 'this part')} has {sum(classes.values())} "
                                                   f"hole(s) sized as ISO 273 {cls} clearance holes"))
    smallest = min((h["d"] for h in holes), default=None)
    dev = float(values.get("mesh_deviation", 0.2))
    if smallest and smallest < dev * 15:
        new = max(0.005, round(smallest / 30, 3))
        out.append(_s("mesh_deviation", new, f"{meta.get('name', 'this part')} has holes as small as {smallest:g} mm; "
                                             f"a {dev:g} mm mesh would visibly facet them in STL exports"))
    bbox = meta.get("bbox") or []
    if len(bbox) == 6:
        span = max(bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2])
        if 0 < span < 15 and int(values.get("edit_decimals", 2)) < 3:
            out.append(_s("edit_decimals", 3, f"{meta.get('name', 'this part')} is only {span:.1f} mm across; "
                                              "editing it needs 0.001 mm steps"))
            out.append(_s("edit_step", 0.01, "finer arrow-key steps for a small part"))
    if meta.get("category") in ("board", "electronics") and float(values.get("tol_clearance", 0.2)) < 0.3:
        out.append(_s("tol_clearance", 0.3, "circuit boards vary by about ±0.2 mm; enclosures around them need "
                                            "at least 0.3 mm of room"))
    return out


def suggestions(meta, values):
    """Suggestions for a component (meta.json or a standard part {'std': {'family','size'}}), skipping any that
    match the team's current value. Each is {'setting', 'value', 'reason'}."""
    std = meta.get("std") or {}
    out = []
    if std.get("family") and std.get("size"):
        out += for_standard(std["family"], std["size"], values)
    out += for_features(meta, values)
    seen, keep = set(), []
    for s in out:
        if s["setting"] in seen:
            continue
        seen.add(s["setting"])
        try:
            team_settings.check(s["setting"], s["value"])
        except ValueError:
            continue
        if values.get(s["setting"]) == s["value"]:
            continue
        keep.append(s)
    return keep
