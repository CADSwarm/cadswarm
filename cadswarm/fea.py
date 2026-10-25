"""FEA jobs on the hub side: inputs with honest placeholders, the solver subprocess, review rules, the report.

Stdlib only. The solve itself runs in `worker/fea_runner.py`, a separate one-shot FreeCAD process, so a
minutes-long CalculiX run never holds the live worker (which serializes every teammate's ops). Placeholder
values are filled in HERE, not by the agent, so a report can't forget to say which numbers were assumed.
"""
import base64
import html
import json
import os
import shutil
import subprocess
import tempfile
import time

from .config import worker_script

FEA_TIMEOUT = int(os.environ.get("CADSWARM_FEA_TIMEOUT", "900"))
PUBLISH_GRACE = 300     # a finished, unpublished job is auto-published after this many seconds
ANALYSES = {"static": "Linear static (stress and deflection)", "modal": "Modal (natural frequencies)",
            "thermal": "Steady-state thermal"}
SCHEMA_ID = "cadswarm.fea/1"

# Typical room-temperature values. E MPa, density kg/m3, yield MPa, conductivity W/m/K, specific heat J/kg/K.
PRESETS = {
    "steel_s235": dict(name="Structural steel S235", youngs_mpa=210000, poisson=0.30, density_kg_m3=7850,
                       yield_mpa=235, conductivity_w_mk=50, specific_heat_j_kgk=460, max_service_c=400),
    "steel_s355": dict(name="Structural steel S355", youngs_mpa=210000, poisson=0.30, density_kg_m3=7850,
                       yield_mpa=355, conductivity_w_mk=50, specific_heat_j_kgk=460, max_service_c=400),
    "stainless_304": dict(name="Stainless steel 304", youngs_mpa=193000, poisson=0.29, density_kg_m3=8000,
                          yield_mpa=215, conductivity_w_mk=16, specific_heat_j_kgk=500, max_service_c=800),
    "aluminium_6061_t6": dict(name="Aluminium 6061-T6", youngs_mpa=68900, poisson=0.33, density_kg_m3=2700,
                              yield_mpa=276, conductivity_w_mk=167, specific_heat_j_kgk=896, max_service_c=150),
    "titanium_ti6al4v": dict(name="Titanium Ti-6Al-4V", youngs_mpa=113800, poisson=0.34, density_kg_m3=4430,
                             yield_mpa=880, conductivity_w_mk=6.7, specific_heat_j_kgk=526, max_service_c=350),
    "brass": dict(name="Brass C360", youngs_mpa=97000, poisson=0.31, density_kg_m3=8500, yield_mpa=124,
                  conductivity_w_mk=115, specific_heat_j_kgk=380, max_service_c=200),
    "pla": dict(name="PLA (3D printed)", youngs_mpa=3500, poisson=0.36, density_kg_m3=1240, yield_mpa=50,
                conductivity_w_mk=0.13, specific_heat_j_kgk=1800, max_service_c=50, plastic=True),
    "petg": dict(name="PETG (3D printed)", youngs_mpa=2100, poisson=0.38, density_kg_m3=1270, yield_mpa=45,
                 conductivity_w_mk=0.2, specific_heat_j_kgk=1200, max_service_c=70, plastic=True),
    "abs": dict(name="ABS (3D printed)", youngs_mpa=2200, poisson=0.35, density_kg_m3=1040, yield_mpa=40,
                conductivity_w_mk=0.17, specific_heat_j_kgk=1400, max_service_c=85, plastic=True),
    "nylon_pa12": dict(name="Nylon PA12", youngs_mpa=1700, poisson=0.39, density_kg_m3=1010, yield_mpa=48,
                       conductivity_w_mk=0.25, specific_heat_j_kgk=1700, max_service_c=90, plastic=True),
}
MATERIAL_KEYS = ("youngs_mpa", "poisson", "density_kg_m3", "yield_mpa", "conductivity_w_mk", "specific_heat_j_kgk")
DEFAULT_MATERIAL = "steel_s235"


class FeaInputError(ValueError):
    pass


def _num(v, what):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise FeaInputError(f"{what} must be a number, got {v!r}")


def _faces(v, what):
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not v or not all(isinstance(s, str) for s in v):
        raise FeaInputError(f"{what} must be a list of face selectors like ['Face3'] or ['zmin']")
    return v


def normalize_job(args):
    """Validate an agent's run_fea arguments; fill gaps with placeholders and record each one."""
    a = dict(args or {})
    kind = str(a.get("analysis") or "static").lower()
    kind = {"stress": "static", "linear_static": "static", "frequency": "modal", "heat": "thermal"}.get(kind, kind)
    if kind not in ANALYSES:
        raise FeaInputError(f"analysis must be one of {sorted(ANALYSES)}")
    targets = a.get("target")
    targets = [targets] if isinstance(targets, str) else targets
    if not targets or not all(isinstance(t, str) and t for t in targets):
        raise FeaInputError("target must name the part to analyse, e.g. 'Bracket' (see model_status)")
    placeholders = []
    for p in a.get("placeholders") or []:  # what the agent itself says it assumed
        placeholders.append({"field": "agent", "text": str(p)[:300]})

    m = a.get("material")
    if not m:
        mat = dict(PRESETS[DEFAULT_MATERIAL], preset=DEFAULT_MATERIAL)
        placeholders.append({"field": "material", "text": f"No material given: assumed {mat['name']}."})
    elif isinstance(m, str) or (isinstance(m, dict) and m.get("preset") and len(m) == 1):
        key = (m if isinstance(m, str) else m["preset"]).lower()
        if key not in PRESETS:
            raise FeaInputError(f"Unknown material preset {key!r}. Presets: {', '.join(PRESETS)}; or give "
                                f"{', '.join(MATERIAL_KEYS)}")
        mat = dict(PRESETS[key], preset=key)
    elif isinstance(m, dict):
        base_key = (m.get("preset") or DEFAULT_MATERIAL).lower()
        if base_key not in PRESETS:
            raise FeaInputError(f"Unknown material preset {base_key!r}")
        mat = dict(PRESETS[base_key], preset=None, name=str(m.get("name") or "Custom material"))
        missing = []
        for k in MATERIAL_KEYS:
            if m.get(k) is not None:
                mat[k] = _num(m[k], f"material.{k}")
            else:
                missing.append(k)
        for k in ("max_service_c", "expansion_um_mk"):
            if m.get(k) is not None:
                mat[k] = _num(m[k], f"material.{k}")
        if missing and "youngs_mpa" in missing:
            raise FeaInputError("A custom material needs at least youngs_mpa (or use a preset).")
        needed = {"static": ["yield_mpa", "density_kg_m3"], "modal": ["density_kg_m3"],
                  "thermal": ["conductivity_w_mk"]}[kind]
        assumed = [k for k in missing if k in needed + ["poisson"]]
        if assumed:
            placeholders.append({"field": "material", "text": f"{', '.join(assumed)} not given: used "
                                 f"{PRESETS[base_key]['name']} values."})
    else:
        raise FeaInputError("material must be a preset name or an object")
    if mat["youngs_mpa"] <= 0 or not 0 <= mat["poisson"] < 0.5:
        raise FeaInputError("youngs_mpa must be positive and poisson between 0 and 0.5")

    fixed = a.get("fixed")
    if kind in ("static", "modal") and not fixed:
        fixed = ["zmin"]
        placeholders.append({"field": "supports", "text": "No fixed faces given: assumed the bottom face (zmin) "
                             "is held completely still. Check this matches how the part is mounted.",
                             "severity": "critical"})
    fixed = _faces(fixed, "fixed") if fixed else []

    loads = []
    for i, l in enumerate(a.get("loads") or []):
        if not isinstance(l, dict) or l.get("type") not in ("force", "pressure", "gravity"):
            raise FeaInputError(f"loads[{i}] needs type force, pressure or gravity")
        out = {"type": l["type"]}
        if l["type"] != "gravity":
            out["faces"] = _faces(l.get("faces"), f"loads[{i}].faces")
        if l["type"] == "force":
            out["newtons"] = _num(l.get("newtons"), f"loads[{i}].newtons")
            d = l.get("direction", "normal")
            if d != "normal":
                if not (isinstance(d, list) and len(d) == 3):
                    raise FeaInputError(f"loads[{i}].direction must be [x, y, z] or 'normal'")
                d = [_num(c, "direction") for c in d]
            out["direction"] = d
        elif l["type"] == "pressure":
            out["mpa"] = _num(l.get("mpa"), f"loads[{i}].mpa")
        else:
            out["g"] = _num(l.get("g", 9.81), f"loads[{i}].g")
            out["direction"] = l.get("direction") or [0, 0, -1]
        loads.append(out)
    if kind == "static" and not loads:
        loads = [{"type": "force", "faces": ["zmax"], "newtons": 1000.0, "direction": [0, 0, -1]}]
        placeholders.append({"field": "loads", "text": "No load given: assumed 1000 N (about 100 kg) pushing "
                             "straight down on the top face (zmax)."})

    thermal = None
    if kind == "thermal":
        t = dict(a.get("thermal") or {})
        thermal = {"initial_c": _num(t.get("initial_c", 20), "thermal.initial_c")}
        for key, fields in (("temperatures", ("celsius",)), ("convection", ("h_w_m2k",)), ("heat_flux", ("w_m2",))):
            rows = []
            for i, r in enumerate(t.get(key) or []):
                row = {"faces": _faces(r.get("faces"), f"thermal.{key}[{i}].faces")}
                for f in fields:
                    row[f] = _num(r.get(f), f"thermal.{key}[{i}].{f}")
                if key == "convection":
                    row["ambient_c"] = _num(r.get("ambient_c", 20), "ambient_c")
                rows.append(row)
            thermal[key] = rows
        if not thermal["temperatures"] and not thermal["heat_flux"]:
            thermal["temperatures"] = [{"faces": ["zmin"], "celsius": 80.0}]
            placeholders.append({"field": "thermal", "text": "No heat source given: assumed the bottom face (zmin) "
                                 "is held at 80 °C."})
        if not thermal["convection"] and len(thermal["temperatures"]) < 2:  # heat needs somewhere to go
            thermal["convection"] = [{"faces": ["zmax"], "h_w_m2k": 10.0, "ambient_c": 20.0}]
            placeholders.append({"field": "thermal", "text": "No cooling given: assumed still air (10 W/m²K, "
                                 "20 °C) on the top face (zmax)."})

    mesh = dict(a.get("mesh") or {})
    size = mesh.get("max_size_mm")
    mesh_out = {"max_size_mm": _num(size, "mesh.max_size_mm") if size else None,
                "second_order": bool(mesh.get("second_order", True))}
    if mesh_out["max_size_mm"] is not None and mesh_out["max_size_mm"] <= 0:
        raise FeaInputError("mesh.max_size_mm must be positive")
    excitation = a.get("excitation_hz")
    return {"analysis": kind, "target": targets, "material": mat, "fixed": fixed, "loads": loads,
            "thermal": thermal, "modes": max(1, min(int(a.get("modes") or 6), 20)),
            "excitation_hz": _num(excitation, "excitation_hz") if excitation else None,
            "mesh": mesh_out, "convergence_check": bool(a.get("convergence_check")),
            "assumptions": str(a.get("assumptions") or "")[:2000], "label": str(a.get("label") or "")[:60],
            "placeholders": placeholders}


# --------------------------------------------------------------------------
# Running the solver process
# --------------------------------------------------------------------------

def runner_script():
    return os.path.join(os.path.dirname(worker_script()), "fea_runner.py")


def run_job(python, job, model_path, timeout=FEA_TIMEOUT, progress=None):
    """Solve `job["target"]` from a saved model with FreeCAD's python; installs Gmsh first if neither FreeCAD
    nor CADSwarm has one. Returns (result dict, plot PNG bytes or None). `progress(text)` hears about a
    mesher install.
    """
    from . import mesher
    gmsh_cmd = mesher.command()
    result, png = _run_job(python, job, model_path, timeout, gmsh_cmd)
    if result.get("code") == "missing_mesher" and not gmsh_cmd:
        try:
            gmsh_cmd = mesher.install(progress=progress)
        except mesher.MesherError as e:
            return {"ok": False, "code": "missing_mesher", "error": str(e)}, None
        result, png = _run_job(python, job, model_path, timeout, gmsh_cmd)
    return result, png


def _run_job(python, job, model_path, timeout, gmsh_cmd):
    tmp = tempfile.mkdtemp(prefix="cadswarm-fea-")
    try:
        doc_path = os.path.join(tmp, "model.FCStd")
        shutil.copy2(model_path, doc_path)  # the solver opens its own copy; the original is never opened
        with open(os.path.join(tmp, "job.json"), "w", encoding="utf-8") as f:
            json.dump(dict(job, doc_path=doc_path), f)
        env = dict(os.environ, CADSWARM_FREECAD_BIN=os.path.dirname(python), PYTHONIOENCODING="utf-8")
        if gmsh_cmd:
            env["CADSWARM_GMSH_CMD"] = json.dumps(gmsh_cmd)
        proc = subprocess.Popen([python, runner_script(), os.path.join(tmp, "job.json")], cwd=tmp, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)
            return {"ok": False, "code": "timeout", "error": f"The analysis took longer than {timeout // 60} minutes "
                    "and was stopped. Try a coarser mesh (larger mesh.max_size_mm)."}, None
        try:
            with open(os.path.join(tmp, "result.json"), encoding="utf-8") as f:
                result = json.load(f)
        except (OSError, ValueError):
            return {"ok": False, "code": "fea_crashed", "error": f"The FreeCAD analysis process exited "
                    f"(code {proc.returncode}) without a result."}, None
        png = None
        if result.get("plot"):
            try:
                with open(os.path.join(tmp, result["plot"]), "rb") as f:
                    png = f.read()
            except OSError:
                result["plot"] = None
        return result, png
    finally:
        if not os.environ.get("CADSWARM_FEA_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# Review rules: points of improvement that don't depend on the agent remembering them
# --------------------------------------------------------------------------

def _pt(severity, text):
    return {"source": "rules", "severity": severity, "text": text}


def rules(job, result):
    out = []
    r, mat, kind = result.get("results") or {}, job["material"], job["analysis"]
    geo, mesh = result.get("geometry") or {}, result.get("mesh") or {}
    ph = job.get("placeholders") or []
    if ph:
        crit = any(p.get("severity") == "critical" for p in ph)
        out.append(_pt("critical" if crit else "warning",
                       f"{len(ph)} input(s) are placeholders ({', '.join(sorted({p['field'] for p in ph}))}). "
                       "The numbers are only as good as these guesses: give the real material, supports and loads "
                       "and run it again."))
    if kind == "static":
        sf, sf95 = r.get("safety_factor"), r.get("safety_factor_p95")
        if sf is not None:
            if sf < 1:
                if sf95 and sf95 >= 1 and r.get("peak_near") in ("fixed_face", "edge"):
                    out.append(_pt("warning", f"The peak stress exceeds yield (safety factor {sf:.2f}) but only in "
                                   f"a small spot at a {'support' if r['peak_near'] == 'fixed_face' else 'sharp edge'}; "
                                   f"95% of the part has a safety factor of at least {sf95:.2f}. Check whether that "
                                   "spot is real (see below) before redesigning."))
                else:
                    out.append(_pt("critical", f"The part is predicted to yield (safety factor {sf:.2f} < 1). "
                                   "Make it thicker where stress is highest, add ribs or fillets, choose a stronger "
                                   "material, or reduce the load."))
            elif sf < 1.5:
                out.append(_pt("warning", f"Safety factor {sf:.2f} is thin. Most designs aim for at least 1.5–2 "
                               "(more for impacts, fatigue or people's safety)."))
            elif sf > 3:
                lighten = ("Lighten part (lighten_part, or the panel's Tools tab > Lighten part...) can fill its "
                           "inside with a lattice that is thicker where this analysis shows stress, then check it "
                           "keeps its stiffness, reusing this load case.")
                if sf > 10:
                    out.append(_pt("info", f"Safety factor {sf:.1f} is very high: the part is probably heavier than "
                                   f"it needs to be. Thinner walls or pockets could save material. {lighten}"))
                else:
                    out.append(_pt("info", f"Safety factor {sf:.1f}: the part has strength to spare. {lighten}"))
        if r.get("peak_near") == "fixed_face":
            out.append(_pt("warning", "The highest stress sits right at a fixed face. Fully fixed faces are "
                           "infinitely stiff, which creates artificial stress peaks that grow as the mesh gets finer. "
                           "Judge the part by the 95th-percentile stress, or model the bolts/contact more realistically."))
        elif r.get("peak_near") == "edge":
            out.append(_pt("warning", "The highest stress is at a sharp edge or corner. Sharp inside corners "
                           "concentrate stress; adding a fillet there usually lowers it a lot."))
        size = max((geo.get("bbox") or [0] * 6)[i + 3] - (geo.get("bbox") or [0] * 6)[i] for i in range(3)) or 0
        d = r.get("max_displacement_mm") or 0
        if size and d > 0.01 * size:
            out.append(_pt("warning", f"Maximum deflection {d:.3g} mm is over 1% of the part size ({size:.0f} mm). "
                           "A linear analysis gets less accurate for large movements, and the part may feel flimsy; "
                           "consider stiffening it."))
        reac = r.get("reactions") or []
        applied = sum(abs(l.get("newtons", 0)) for l in job["loads"] if l["type"] == "force")
        only_forces = all(l["type"] == "force" for l in job["loads"])
        if reac and applied and only_forces:
            total = sum((x["fx"] ** 2 + x["fy"] ** 2 + x["fz"] ** 2) ** 0.5 for x in reac)
            if abs(total - applied) > 0.05 * applied:
                out.append(_pt("warning", f"The supports carry {total:.0f} N but {applied:.0f} N was applied "
                               "(more than 5% apart). Loads may be on faces that touch the supports, or the mesh is "
                               "too coarse there."))
        if len(job["loads"]) == 1:
            out.append(_pt("info", "Only one load case was checked. Real parts often see several (sideways "
                           "knocks, being dropped, assembly forces); consider running those too."))
    if kind == "modal":
        modes = r.get("modes") or []
        if r.get("rigid_modes"):
            out.append(_pt("critical", f"{r['rigid_modes']} mode(s) are near 0 Hz: the part isn't fully held and "
                           "can move freely. Add or correct the fixed faces."))
        elastic = [m for m in modes if m["hz"] >= 1]
        if elastic and elastic[0]["hz"] < 20:
            out.append(_pt("warning", f"The first natural frequency is low ({elastic[0]['hz']:.1f} Hz); it is easy "
                           "to excite by handling, footsteps or motors. Stiffen or lighten the part."))
        exc = job.get("excitation_hz")
        near = [m for m in elastic if exc and abs(m["hz"] - exc) <= 0.2 * exc]
        if near:
            out.append(_pt("critical", f"Mode {near[0]['mode']} ({near[0]['hz']:.1f} Hz) is within 20% of the "
                           f"{exc:g} Hz excitation: risk of resonance. Move it away by changing stiffness or mass."))
        if not exc:
            out.append(_pt("info", "No excitation frequency was given (motor speed, fan, vehicle vibration), so "
                           "resonance risk couldn't be checked."))
    if kind == "thermal":
        tmax = (r.get("temperature_c") or {}).get("max")
        limit = mat.get("max_service_c")
        if tmax is not None and limit and tmax > limit:
            out.append(_pt("critical", f"The part reaches {tmax:.0f} °C, above the usual limit for "
                           f"{mat['name']} (about {limit:.0f} °C). Choose a material rated for it or add cooling."))
        out.append(_pt("info", "This is a heat-flow analysis only; thermal expansion stresses aren't included."))
    if not job["mesh"]["second_order"]:
        out.append(_pt("warning", "First-order elements are too stiff in bending; use second order (the default) "
                       "for trustworthy stress and deflection."))
    el, edge = mesh.get("elements") or 0, geo.get("min_edge_mm") or 0
    if el and el < 3000:
        out.append(_pt("warning", f"The mesh is coarse ({el} elements). Results can be off; refine the mesh "
                       "(smaller mesh.max_size_mm) or run with convergence_check."))
    elif edge and mesh.get("size_mm") and mesh["size_mm"] > 3 * edge and edge > 0.05:
        out.append(_pt("info", f"The mesh size ({mesh['size_mm']:.3g} mm) is much larger than the smallest feature "
                       f"({edge:.3g} mm); small details may be poorly resolved."))
    conv = r.get("convergence")
    if not conv:
        out.append(_pt("info", "No mesh convergence check was run. Running again with convergence_check=true "
                       "shows whether the answer changes with a finer mesh."))
    else:
        pct = max(conv.get("change_pct") or 0, 0)
        if pct > 10:
            out.append(_pt("warning", f"Refining the mesh changed the result by {pct:.0f}%: the answer hasn't "
                           "settled. Refine further before trusting the numbers."))
        if conv.get("vm_change_pct", 0) > 10 and kind == "static":
            out.append(_pt("info", f"Peak stress changed {conv['vm_change_pct']:.0f}% with the finer mesh, typical of "
                           "a stress singularity; deflection is the more reliable number here."))
    if mat.get("plastic"):
        out.append(_pt("info", "3D-printed plastic is weaker between layers (often 30–60% of the listed strength) "
                       "and creeps under constant load. Orient the print so layers don't split along the load."))
    if len(job["target"]) > 1 or any("fused" in w for w in result.get("warnings") or []):
        out.append(_pt("warning", "Several bodies were analysed as one glued piece. Bolted or sliding joints behave "
                       "differently; analyse parts separately with realistic supports if the joints matter."))
    return out


def verdict(improvements):
    sev = {i["severity"] for i in improvements}
    return "fail" if "critical" in sev else "check" if "warning" in sev else "ok"


def headline(report):
    r, kind = report["results"], report["analysis"]
    if kind == "static":
        sf = r.get("safety_factor")
        return (f"max stress {r.get('max_von_mises_mpa', 0):.3g} MPa, deflection {r.get('max_displacement_mm', 0):.3g} mm"
                + (f", safety factor {sf:.2f}" if sf is not None else ""))
    if kind == "modal":
        el = [m for m in r.get("modes") or [] if m["hz"] >= 1]
        return f"first natural frequency {el[0]['hz']:.1f} Hz" if el else "no elastic modes found"
    t = r.get("temperature_c") or {}
    return f"temperature {t.get('min', 0):.1f}–{t.get('max', 0):.1f} °C"


def build_report(jid, job, result, by, requested_at, finished_at, revs, seq, summary, agent_points, auto, now):
    improvements = rules(job, result) + [{"source": "agent", "severity": p.get("severity", "info"),
                                          "text": p["text"]} for p in agent_points]
    title = job["label"] or f"{ANALYSES[job['analysis']].split(' (')[0]} of {', '.join(job['target'])}"
    report = {
        "schema": SCHEMA_ID, "id": jid, "title": title, "label": job["label"], "analysis": job["analysis"],
        "analysis_name": ANALYSES[job["analysis"]], "by": by, "requested_at": requested_at,
        "finished_at": finished_at, "published_at": now, "auto_published": auto,
        "model": {"seq": seq, "target": job["target"], "revs": revs, **(result.get("geometry") or {})},
        "inputs": {"material": job["material"], "fixed": job["fixed"], "loads": job["loads"],
                   "thermal": job["thermal"], "modes": job["modes"] if job["analysis"] == "modal" else None,
                   "excitation_hz": job["excitation_hz"], "mesh": job["mesh"],
                   "convergence_check": job["convergence_check"], "assumptions": job["assumptions"],
                   "placeholders": job["placeholders"], "resolved_faces": result.get("resolved")},
        "mesh": result.get("mesh") or {}, "results": result.get("results") or {},
        "solver": {**(result.get("timing") or {}), "freecad": result.get("freecad"), "mesher": "Gmsh",
                   "solver": "CalculiX", "warnings": result.get("warnings") or []},
        "improvements": improvements, "verdict": verdict(improvements),
        "summary": summary or ("The AI helper didn't add a summary; the points below come from automatic checks."
                               if auto else ""),
        "files": {},
    }
    report["headline"] = headline(report)
    return report


DIRECTIONS = {"down (-z)": [0, 0, -1], "up (+z)": [0, 0, 1], "+x": [1, 0, 0], "-x": [-1, 0, 0],
              "+y": [0, 1, 0], "-y": [0, -1, 0], "into the face (normal)": "normal"}


def panel_request(target, analysis, material, fixed, newtons=None, direction="down (-z)", load_faces="zmax",
                  modes=6):
    """run_fea's body for a person who clicked Run a simulation: the part, how it is held and loaded, as typed.
    Face fields take the same selectors as list_faces ("zmin", "cyl r=2.5", "Face3"), comma separated."""
    if not target:
        raise FeaInputError("Pick the part to simulate")
    if analysis not in ("static", "modal"):
        raise FeaInputError("From the panel you can run a static or a modal analysis; ask a helper for heat")
    if material not in PRESETS:
        raise FeaInputError(f"Unknown material {material!r}")
    held = [s.strip() for s in str(fixed or "").split(",") if s.strip()]
    if not held:
        raise FeaInputError("Say which faces are held still, e.g. zmin for the bottom face")
    body = {"target": target, "analysis": analysis, "material": material, "fixed": held,
            "label": f"{target} ({'load test' if analysis == 'static' else 'vibration'}, from the panel)"}
    if analysis == "static":
        faces = [s.strip() for s in str(load_faces or "").split(",") if s.strip()]
        n = _num(newtons, "The force")
        if n <= 0 or not faces:
            raise FeaInputError("Give the force in newtons (more than 0) and the faces it pushes on")
        if direction not in DIRECTIONS:
            raise FeaInputError(f"Unknown direction {direction!r}")
        body["loads"] = [{"type": "force", "faces": faces, "newtons": n, "direction": DIRECTIONS[direction]}]
    else:
        body["modes"] = int(modes)
    return body


PANEL_WAIT, PANEL_POLLS = 45, 40  # each poll waits on the host like the MCP tools; ~30 min in all for big meshes


def run_and_publish(client, body, person, wait=PANEL_WAIT, polls=PANEL_POLLS):
    """Run a panel_request through the hub, wait for the solver and publish it as `person`'s report.
    Returns the publish reply (its folder pops up for the team) or raises FeaInputError saying what went wrong."""
    out = client.post("/fea/run", {**body, "wait": wait})
    for _ in range(polls):
        if out.get("status") not in ("queued", "running"):
            break
        out = client.get(f"/fea/jobs/{out['job_id']}", wait=wait)
    if out.get("status") == "failed":
        raise FeaInputError(f"The simulation failed: {out.get('error') or 'see the team activity'}")
    if out.get("status") != "done":
        raise FeaInputError("The simulation is still running; its report is published automatically when it ends")
    loads = body.get("loads") or []
    summary = (f"Run from the panel by {person}: {ANALYSES[body['analysis']].split(' (')[0].lower()} analysis of "
               f"{body['target']} in {PRESETS[body['material']]['name']}, held at {', '.join(body['fixed'])}"
               + (f", {loads[0]['newtons']:g} N on {', '.join(loads[0]['faces'])}." if loads else "."))
    return client.post(f"/fea/jobs/{out['job_id']}/publish", {"summary": summary, "improvements": []})


SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def digest(report, folder=""):
    """A published report as an AI helper needs it to fix the design: the problems worst first, the numbers
    behind them, and the run_fea arguments that repeat the same analysis after the change."""
    inp, model = report.get("inputs") or {}, report.get("model") or {}
    material = inp.get("material") or {}
    problems = sorted((p for p in report.get("improvements") or []),
                      key=lambda p: SEVERITY_ORDER.get(p.get("severity"), 3))
    rerun = {"target": model.get("target") or [], "analysis": report.get("analysis"),
             "material": material.get("preset") or material or None, "fixed": inp.get("fixed"),
             "loads": inp.get("loads"), "thermal": inp.get("thermal"), "mesh": inp.get("mesh"),
             "label": report.get("label") or None}
    if report.get("analysis") == "modal":
        rerun.update(modes=inp.get("modes"), excitation_hz=inp.get("excitation_hz"))
    return {
        "id": report.get("id"), "folder": folder, "title": report.get("title"), "by": report.get("by"),
        "verdict": report.get("verdict"), "headline": report.get("headline"), "summary": report.get("summary"),
        "target": model.get("target") or [], "model_revs": model.get("revs") or {},
        "results": report.get("results") or {},
        "problems": [{"severity": p.get("severity"), "text": p.get("text"), "source": p.get("source")}
                     for p in problems],
        "placeholders": [p.get("text") for p in inp.get("placeholders") or []],
        "rerun_args": {k: v for k, v in rerun.items() if v not in (None, [], {})},
        "how_to_fix": ("Fix the critical and warning problems in parts you hold (lock them first; ask the owner "
                       "otherwise). Placeholder inputs are guesses: ask a person for the real ones. Then call "
                       "run_fea with rerun_args (face numbers like Face7 change when geometry changes: check "
                       "them with list_faces) and publish the new report so the team can compare."),
    }


# --------------------------------------------------------------------------
# HTML (QTextBrowser renders a subset of HTML 4 / CSS 2, so keep it to tables and inline styles)
# --------------------------------------------------------------------------

VERDICT_STYLE = {"ok": ("#1a7f37", "Looks OK"), "check": ("#9a6700", "Check the warnings"),
                 "fail": ("#cf222e", "Needs changes")}
SEV_STYLE = {"critical": ("#cf222e", "Critical"), "warning": ("#9a6700", "Warning"), "info": ("#57606a", "Note")}


def _e(v):
    return html.escape(str(v), quote=True)


def _rows(pairs):
    return "".join(f"<tr><td style='color:#57606a;padding:2px 12px 2px 0'>{_e(k)}</td><td>{v}</td></tr>"
                   for k, v in pairs if v not in (None, ""))


def _faces_text(sel, resolved=None):
    text = ", ".join(sel)
    if resolved and resolved != sel:
        text += f" → {', '.join(resolved)}"
    return _e(text)


def render_html(report, plot_src="plot.png"):
    r, inp, kind = report["results"], report["inputs"], report["analysis"]
    color, word = VERDICT_STYLE[report["verdict"]]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(report["published_at"]))
    res_faces = inp.get("resolved_faces") or {}
    parts = [f"<html><head><meta charset='utf-8'><title>{_e(report['title'])}</title></head>"
             "<body style='font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:10pt;color:#1f2328'>",
             f"<h2 style='margin-bottom:2px'>{_e(report['title'])}</h2>",
             f"<p style='color:#57606a;margin-top:0'>{_e(report['analysis_name'])} · by {_e(report['by'])} · {_e(when)}"
             f"{' · published automatically' if report['auto_published'] else ''}</p>",
             f"<p><span style='background:{color};color:white;padding:3px 8px;font-weight:bold'>&nbsp;{word}&nbsp;</span>"
             f"&nbsp; {_e(report['headline'])}</p>"]
    if inp["placeholders"]:
        items = "".join(f"<li>{_e(p['text'])}</li>" for p in inp["placeholders"])
        parts.append("<table width='100%' cellpadding='6' style='background:#fff8c5;border:1px solid #d4a72c'><tr><td>"
                     f"<b>Uses placeholder values</b>. These were assumed, not given:<ul style='margin:4px 0'>{items}"
                     "</ul></td></tr></table>")
    if report["summary"]:
        parts.append(f"<h3>Summary</h3><p>{_e(report['summary']).replace(chr(10), '<br>')}</p>")

    parts.append("<h3>Results</h3><table cellspacing='0'>")
    if kind == "static":
        sf, sf95 = r.get("safety_factor"), r.get("safety_factor_p95")
        parts.append(_rows([
            ("Max von Mises stress", f"<b>{r.get('max_von_mises_mpa', 0):.4g} MPa</b> at "
                                     f"{_e(r.get('max_von_mises_at'))} ({_e(str(r.get('peak_near', '')).replace('_', ' '))})"),
            ("95th-percentile stress", f"{r.get('von_mises_p95_mpa', 0):.4g} MPa"),
            ("Yield strength", f"{r.get('yield_mpa', 0):.4g} MPa"),
            ("Safety factor (peak / 95th pct)", f"<b>{sf:.3g}</b> / {sf95:.3g}" if sf and sf95 else _e(sf)),
            ("Max displacement", f"<b>{r.get('max_displacement_mm', 0):.4g} mm</b> at {_e(r.get('max_displacement_at'))}"),
            ("Support reactions", "<br>".join(f"{_e(x['set'])}: Fx {x['fx']:.4g}, Fy {x['fy']:.4g}, Fz {x['fz']:.4g} N"
                                              for x in r.get("reactions") or []))]))
    elif kind == "modal":
        parts.append(_rows([(f"Mode {m['mode']}", f"<b>{m['hz']:.4g} Hz</b>") for m in r.get("modes") or []]
                           + [("Excitation", f"{inp['excitation_hz']:g} Hz" if inp.get("excitation_hz") else None)]))
    else:
        t = r.get("temperature_c") or {}
        parts.append(_rows([("Minimum temperature", f"{t.get('min', 0):.4g} °C"),
                            ("Maximum temperature", f"<b>{t.get('max', 0):.4g} °C</b>")]))
    conv = r.get("convergence")
    if conv:
        parts.append(_rows([("Mesh convergence", f"{_e(conv['quantity'])}: {conv['coarse']:.4g} → {conv['fine']:.4g} "
                                                 f"({conv['change_pct']:.3g}% change, mesh {conv['coarse_size_mm']:.3g} → "
                                                 f"{conv['fine_size_mm']:.3g} mm)")]))
    parts.append("</table>")
    if report["files"].get("plot") or plot_src.startswith("data:"):
        parts.append(f"<p><img src='{_e(plot_src)}' width='640'></p>")

    if report["improvements"]:
        parts.append("<h3>Points of improvement</h3><table cellspacing='0' cellpadding='3'>")
        order = {"critical": 0, "warning": 1, "info": 2}
        for i in sorted(report["improvements"], key=lambda i: order.get(i["severity"], 3)):
            c, w = SEV_STYLE.get(i["severity"], SEV_STYLE["info"])
            src = "AI helper" if i["source"] == "agent" else "automatic check"
            parts.append(f"<tr><td valign='top' style='color:{c};font-weight:bold;padding-right:8px'>{w}</td>"
                         f"<td>{_e(i['text'])} <span style='color:#8b949e'>({src})</span></td></tr>")
        parts.append("</table>")

    m = inp["material"]
    parts.append("<h3>Inputs</h3><table cellspacing='0'>")
    load_lines = []
    for i, l in enumerate(inp["loads"] or []):
        rf = ((res_faces.get("loads") or [{}] * (i + 1))[i] or {}).get("faces") if res_faces else None
        if l["type"] == "force":
            d = "normal to the face" if l["direction"] == "normal" else f"direction {l['direction']}"
            load_lines.append(f"Force {l['newtons']:g} N on {_faces_text(l['faces'], rf)}, {_e(d)}")
        elif l["type"] == "pressure":
            load_lines.append(f"Pressure {l['mpa']:g} MPa on {_faces_text(l['faces'], rf)}")
        else:
            load_lines.append(f"Gravity {l['g']:g} m/s² towards {_e(l['direction'])}")
    th = inp.get("thermal") or {}
    th_lines = [f"Starting temperature {th['initial_c']:g} °C"] if th else []
    th_lines += [f"{t['celsius']:g} °C on {_faces_text(t['faces'])}" for t in th.get("temperatures") or []]
    th_lines += [f"Convection {c['h_w_m2k']:g} W/m²K to {c['ambient_c']:g} °C on {_faces_text(c['faces'])}"
                 for c in th.get("convection") or []]
    th_lines += [f"Heat flux {h['w_m2']:g} W/m² on {_faces_text(h['faces'])}" for h in th.get("heat_flux") or []]
    model = report["model"]
    parts.append(_rows([
        ("Part(s)", _e(", ".join(model["target"])) + f" <span style='color:#8b949e'>(model rev {model.get('seq')})</span>"),
        ("Size", f"{_e(model.get('bbox'))} mm, volume {model.get('volume_mm3', 0):.4g} mm³, mass {model.get('mass_kg', 0):.4g} kg"
         if model.get("bbox") else None),
        ("Material", f"{_e(m['name'])}: E {m['youngs_mpa']:g} MPa, ν {m['poisson']:g}, ρ {m['density_kg_m3']:g} kg/m³, "
                     f"yield {m['yield_mpa']:g} MPa" + (f", k {m['conductivity_w_mk']:g} W/mK" if kind == "thermal" else "")),
        ("Fixed faces", _faces_text(inp["fixed"], res_faces.get("fixed")) if inp["fixed"] else None),
        ("Loads", "<br>".join(load_lines) or None),
        ("Thermal", "<br>".join(th_lines) or None),
        ("Modes requested", inp.get("modes")),
        ("Assumptions", _e(inp["assumptions"]) if inp["assumptions"] else None)]))
    parts.append("</table>")

    mesh, s = report["mesh"], report["solver"]
    parts.append("<h3>Mesh and solver</h3><table cellspacing='0'>")
    parts.append(_rows([
        ("Mesh", f"{mesh.get('nodes', 0):,} nodes, {mesh.get('elements', 0):,} tetrahedra, size {mesh.get('size_mm', 0):.3g} mm, "
                 f"{'second' if mesh.get('order') == 2 else 'first'}-order"),
        ("Software", f"FreeCAD {_e(s.get('freecad'))}, {s['mesher']} + {s['solver']}"),
        ("Time", f"mesh {s.get('mesh_seconds', 0):.3g} s, solve {s.get('solve_seconds', 0):.3g} s"),
        ("Warnings", "<br>".join(_e(w) for w in s["warnings"]) or None)]))
    parts.append("</table>")
    parts.append("<p style='color:#8b949e;font-size:8pt'>Linear-elastic FEA with simplified supports and loads. It is "
                 "a design aid, not a certification; check critical parts with an engineer and physical testing. "
                 f"Report {_e(report['id'])}, schema {SCHEMA_ID}.</p></body></html>")
    return "".join(parts)


def standalone_html(report, png):
    """The report as one file to keep or email: the picture is embedded instead of linked."""
    src = "data:image/png;base64," + base64.b64encode(png).decode() if png else "plot.png"
    return render_html(report, src)
