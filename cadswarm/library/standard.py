"""Built-in standard parts (standard_parts.json) and the hole sizes that fit them.

A standard family is browsable like any library part (as a virtual component `c_std_<family>`); placing
one runs the worker's insert_standard_part op, which builds the solid from the same numbers.
"""
import json
import os

CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "standard_parts.json")
STD_PREFIX = "c_std_"
CLASS_INDEX = {"close": 0, "normal": 1, "loose": 2}
_CATALOG = None


def catalog():
    global _CATALOG
    if _CATALOG is None:
        with open(CATALOG_PATH, encoding="utf-8") as f:
            _CATALOG = json.load(f)
    return _CATALOG


def families():
    return catalog()["families"]


def family(name):
    fam = families().get(name)
    if fam is None:
        raise ValueError(f"No standard part family {name!r}. Known: {', '.join(families())}")
    return fam


def size_key(fam, size):
    """The catalog's spelling of `size` ('m3' -> 'M3', 608 -> '608')."""
    sizes = fam["sizes"]
    s = str(size).strip()
    for k in sizes:
        if k.lower() == s.lower():
            return k
    raise ValueError(f"{fam['name']} comes in {', '.join(sizes)}, not {size!r}")


def resolve(family_name, size, length=None):
    """{'family', 'size', 'length', 'dims', 'label'} for a placeable standard part, or ValueError."""
    fam = family(family_name)
    key = size_key(fam, size)
    dims = dict(fam["sizes"][key])
    out = {"family": family_name, "size": key, "dims": dims, "shape": fam["shape"]}
    if "length" in fam["params"]:
        lengths = fam.get("lengths", [])
        if length is None:
            raise ValueError(f"{fam['name']} needs a length ({', '.join(map(str, lengths))} mm)")
        length = float(length)
        if lengths and length not in [float(x) for x in lengths]:
            nearest = min(lengths, key=lambda x: abs(x - length))
            raise ValueError(f"{key} {fam['name'].lower()}s are sold in {', '.join(map(str, lengths))} mm; "
                             f"the nearest is {nearest} mm")
        out["length"] = length
    out["label"] = label(family_name, key, out.get("length"))
    return out


def label(family_name, size, length=None):
    fam = family(family_name)
    text = f"{size} {fam['name'].lower()}"
    if length:
        text = f"{size}x{length:g} {fam['name'].lower()}"
    return text


def thread_size(dims_or_size):
    """'M3' from a size key, if it is a metric thread."""
    s = str(dims_or_size)
    return s if s.upper().startswith("M") else None


def clearance_hole(size, cls="normal"):
    """ISO 273 through-hole diameter for a metric screw."""
    holes = catalog()["clearance_holes"]
    key = next((k for k in holes if k.lower() == str(size).lower()), None)
    if key is None or key.startswith("_"):
        raise ValueError(f"No clearance hole data for {size!r}")
    return holes[key][CLASS_INDEX.get(cls, 1)]


PURPOSE_WORDS = {"through": "through", "clearance": "through", "pass": "through", "pass_through": "through",
                 "thread": "thread", "threaded": "thread",
                 "seat": "seat", "press": "seat", "press_fit": "seat", "pocket": "seat"}


def hole_for(family_name, size, settings, purpose=None):
    """The hole a printed or machined part needs for this standard part, with how it was worked out.

    purpose: 'through' (screw passes through), 'thread' (screw holds in this part), 'seat' (bearing/insert/
    magnet/pin pressed or slid in). Defaults by family. Fits come from the team settings.
    """
    fam = family(family_name)
    key = size_key(fam, size)
    d = fam["sizes"][key]
    cat = catalog()
    shape = fam["shape"]
    if purpose is None:
        purpose = {"insert": "seat", "bearing": "seat", "disc": "seat", "pin": "seat"}.get(shape, "through")
    # An unknown word must not fall through to "seat": that gives a screw a hole its own size.
    purpose = PURPOSE_WORDS.get(str(purpose).strip().lower(), purpose)
    if purpose not in ("through", "thread", "seat"):
        raise ValueError(f"purpose must be through, thread or seat, not {purpose!r}")
    if purpose == "through":
        cls = settings.get("hole_clearance_class", "normal")
        return {"diameter": clearance_hole(key, cls), "why": f"ISO 273 {cls} clearance hole for {key}"}
    if purpose == "thread":
        mode = settings.get("thread_mode", "heat_set_insert")
        if mode == "heat_set_insert":
            ins = families()["heat_set_insert"]["sizes"].get(key)
            if ins:
                return {"diameter": ins["hole_d"], "depth": ins["hole_depth"],
                        "why": f"hole for an {key} heat-set insert (thread_mode=heat_set_insert)"}
        if mode == "tapped":
            return {"diameter": cat["tap_drill"][key], "why": f"tap drill for {key}"}
        if mode == "self_tapping":
            return {"diameter": cat["self_tapping_hole"][key], "why": f"pilot hole for an {key} screw in plastic"}
        return {"diameter": d.get("d", 0), "why": f"modelled {key} thread (nominal diameter)"}
    # seat: outer diameter plus the team's fit
    od = d.get("od") or d.get("d")
    if shape == "insert":
        return {"diameter": d["hole_d"], "depth": d["hole_depth"], "why": f"{key} insert hole from the catalog"}
    fit_key = "tol_press_fit" if shape in ("bearing", "disc") else "tol_slip_fit"
    fit = float(settings.get(fit_key, 0))
    return {"diameter": round(od + fit, 3), "fit": fit_key, "fit_mm": fit,
            "why": f"{od} mm {fam['name'].lower()} + {fit_key} {fit:+g} mm"}


def bbox_of(resolved):
    """Rough [xmin, ymin, zmin, xmax, ymax, zmax] of a placed standard part (origin under the head/at the base)."""
    d, shape, L = resolved["dims"], resolved["shape"], resolved.get("length", 0)
    if shape in ("socket_head", "button_head"):
        r = d["dk"] / 2
        return [-r, -r, -L, r, r, d["k"]]
    if shape == "hex_nut":
        r = d["s"] / 3 ** 0.5
        return [-r, -r, 0, r, r, d["m"]]
    if shape == "washer":
        r = d["d2"] / 2
        return [-r, -r, 0, r, r, d["h"]]
    if shape == "insert":
        r = d["od"] / 2
        return [-r, -r, 0, r, r, d["length"]]
    if shape == "bearing":
        r = d["od"] / 2
        return [-r, -r, 0, r, r, d["b"]]
    if shape == "pin":
        r = d["d"] / 2
        return [-r, -r, 0, r, r, L]
    if shape == "disc":
        r = d["d"] / 2
        return [-r, -r, 0, r, r, d["h"]]
    return []


def virtual_components():
    """One browsable library entry per standard family (not stored on disk)."""
    out = []
    for name, fam in families().items():
        out.append({
            "id": STD_PREFIX + name.lower(), "name": fam["name"], "category": fam["category"],
            "tags": fam.get("tags", []), "description": f"{fam['standard']}. Sizes: {', '.join(fam['sizes'])}"
            + (f"; lengths {', '.join(map(str, fam['lengths']))} mm" if fam.get("lengths") else ""),
            "source": "builtin", "source_url": "", "license": "CADSwarm built-in", "author": "CADSwarm",
            "format": "standard", "std": {"family": name, "sizes": list(fam["sizes"]),
                                          "lengths": fam.get("lengths", []), "params": fam["params"]},
        })
    return out
