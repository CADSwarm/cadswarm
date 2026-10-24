"""Runs ONE finite-element analysis in a throwaway FreeCAD process: `python fea_runner.py <job.json>`.

Standalone on purpose (stdlib plus FreeCAD, like script_runner.py): the hub starts it in a fresh temp folder
with the target's BREP copied out of the live model, so a slow or crashing solve can never stall, change or
corrupt the shared document. The analysis lives in an unsaved document that dies with the process.

Mesh with Gmsh, solve with CalculiX (both ship with FreeCAD). The FEM helpers' Qt-process paths (`run()`)
are avoided: each step is called directly and the two binaries run under our own timeouts.

Writes `result.json` next to the job (stdout is noisy with FreeCAD output) and `plot.png` when matplotlib is
there. Units in and out: mm, N, MPa, Hz, °C.

`face_table` and `select_faces` are also used by the live worker's `faces` query, so they import nothing
beyond Part.
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback

FREECAD_BIN = os.environ.get("CADSWARM_FREECAD_BIN")
# FreeCAD's module sits beside python on Windows (bin/) and in lib/ in the macOS and Linux bundles.
for _d in ([FREECAD_BIN, os.path.join(FREECAD_BIN, os.pardir, "lib")] if FREECAD_BIN else []):
    if os.path.isdir(_d) and os.path.abspath(_d) not in sys.path:
        sys.path.append(os.path.abspath(_d))

MAX_ELEMENTS = 300_000   # estimated tets; a finer mesh is coarsened and the report says so
TOOL_TIMEOUT = int(os.environ.get("CADSWARM_FEA_TOOL_TIMEOUT", "600"))
AXES = {"x": 0, "y": 1, "z": 2}


class FeaError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Faces: how agents point at "where it's bolted" and "where the load goes"
# --------------------------------------------------------------------------

def _r(v, n=3):
    return round(float(v), n) + 0.0  # + 0.0 turns -0.0 into 0.0


def face_table(shape):
    out = []
    for i, f in enumerate(shape.Faces, 1):
        row = {"face": f"Face{i}", "type": type(f.Surface).__name__, "area": _r(f.Area, 2),
               "center": [_r(c) for c in f.CenterOfMass],
               "bbox": [_r(v) for v in (f.BoundBox.XMin, f.BoundBox.YMin, f.BoundBox.ZMin,
                                        f.BoundBox.XMax, f.BoundBox.YMax, f.BoundBox.ZMax)]}
        if row["type"] == "Plane":
            u0, u1, v0, v1 = f.ParameterRange
            n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
            row["normal"] = [_r(c, 4) for c in n]
        elif hasattr(f.Surface, "Radius"):
            row["radius"] = _r(f.Surface.Radius)
        out.append(row)
    return out


def _tol(shape):
    return max(0.01, 1e-4 * shape.BoundBox.DiagonalLength)


def select_faces(shape, selectors):
    """Face names for selectors: "Face3", "xmin".."zmax", "z=10", "cyl", "cyl r=4", "largest"."""
    table, bb, tol = face_table(shape), shape.BoundBox, _tol(shape)
    lo = [bb.XMin, bb.YMin, bb.ZMin]
    hi = [bb.XMax, bb.YMax, bb.ZMax]
    picked = []
    for sel in selectors or []:
        s = str(sel).strip().lower().replace(" ", "")
        hits = []
        if re.fullmatch(r"face\d+", s):
            if int(s[4:]) <= len(table):
                hits = [f"Face{int(s[4:])}"]
        elif re.fullmatch(r"[xyz](min|max)", s):
            ax, end = AXES[s[0]], s[1:]
            want = lo[ax] if end == "min" else hi[ax]
            hits = [f["face"] for f in table if f.get("normal") and abs(abs(f["normal"][ax]) - 1) < 1e-3
                    and abs(f["center"][ax] - want) <= tol]
        elif re.fullmatch(r"[xyz]=-?[\d.]+", s):
            ax, want = AXES[s[0]], float(s[2:])
            hits = [f["face"] for f in table if f.get("normal") and abs(abs(f["normal"][ax]) - 1) < 1e-3
                    and abs(f["center"][ax] - want) <= tol]
        elif s.startswith("cyl"):
            m = re.fullmatch(r"cyl(?:r=([\d.]+))?", s)
            if m:
                hits = [f["face"] for f in table if f["type"] == "Cylinder"
                        and (m.group(1) is None or abs(f["radius"] - float(m.group(1))) <= max(tol, 0.01))]
        elif s == "largest":
            hits = [max(table, key=lambda f: f["area"])["face"]] if table else []
        if not hits:
            raise FeaError("bad_args", f"Face selector {sel!r} matched no face. Use list_faces to see them; "
                           "selectors: Face3, xmin..zmax, z=10, cyl, cyl r=4, largest.")
        picked += [h for h in hits if h not in picked]
    return picked


def analysis_shape(shape):
    """The one solid that gets meshed: several bodies are fused (bonded), so face names match list_faces."""
    import Part
    solids, warnings = shape.Solids, []
    if not solids:
        raise FeaError("bad_args", "The target has no solid to analyse (only faces or edges).")
    if len(solids) > 1:
        solids = solids[0].multiFuse(solids[1:]).removeSplitter().Solids
        warnings.append("Several bodies were fused and treated as one bonded part.")
    out = solids[0] if len(solids) == 1 else Part.makeCompound(solids)
    if not out.isValid():
        out.fix(1e-7, 1e-7, 1e-7)
    return out, warnings


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

def find_tool(name):
    """Path of FreeCAD's bundled ccx/gmsh. Reads FEM preferences but never writes them."""
    import FreeCAD
    pref = {"ccx": ("Ccx", "ccxBinaryPath"), "gmsh": ("Gmsh", "gmshBinaryPath")}[name]
    configured = FreeCAD.ParamGet(f"User parameter:BaseApp/Preferences/Mod/Fem/{pref[0]}").GetString(pref[1], "")
    dirs = [os.path.dirname(sys.executable), FREECAD_BIN or "", os.path.join(FreeCAD.getHomePath(), "bin"),
            "/snap/freecad/current/usr/bin"]
    exe = name + (".exe" if os.name == "nt" else "")
    for cand in [configured and shutil.which(configured), shutil.which(name)] + \
            [os.path.join(d, exe) for d in dirs if d]:
        if cand and os.path.isfile(cand):
            os.environ["PATH"] = os.path.dirname(cand) + os.pathsep + os.environ.get("PATH", "")
            return cand
    raise FeaError("missing_tool", f"FreeCAD's FEM {'solver (CalculiX ccx)' if name == 'ccx' else 'mesher (Gmsh)'} "
                   "wasn't found. It comes with the standard FreeCAD download; reinstall FreeCAD 1.x or set its path "
                   "in FreeCAD's FEM preferences.")


def mesher_command():
    """Argv prefix for Gmsh: FreeCAD's own, else the copy the hub installed (CADSWARM_GMSH_CMD, a JSON list)."""
    try:
        return [find_tool("gmsh")]
    except FeaError:
        pass
    cmd = json.loads(os.environ.get("CADSWARM_GMSH_CMD") or "null")
    if cmd and all(os.path.exists(c) for c in cmd):
        return cmd
    raise FeaError("missing_mesher", "No Gmsh mesher was found (FreeCAD usually includes it).")


def _run(cmd, cwd, what):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=TOOL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise FeaError("timeout", f"{what} took longer than {TOOL_TIMEOUT}s; try a coarser mesh.")
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# Building the analysis
# --------------------------------------------------------------------------

def _refs(geom, faces):
    return [(geom, f) for f in faces]


def build(doc, geom, job):
    import FreeCAD
    import ObjectsFem
    analysis = ObjectsFem.makeAnalysis(doc, "Analysis")
    solver = ObjectsFem.makeSolverCalculiXCcxTools(doc, "Solver")
    kind = job["analysis"]
    solver.AnalysisType = {"static": "static", "modal": "frequency", "thermal": "thermomech"}[kind]
    if kind == "modal":
        solver.EigenmodesCount = int(job.get("modes") or 6)
        solver.EigenmodeLowLimit = 0.0
        solver.EigenmodeHighLimit = 1e6
    if kind == "thermal":
        solver.ThermoMechType = "pure heat transfer"
        solver.ThermoMechSteadyState = True
    analysis.addObject(solver)

    m = job["material"]
    mat = ObjectsFem.makeMaterialSolid(doc, "Material")
    card = dict(mat.Material)
    card.update({"Name": m["name"], "YoungsModulus": f"{m['youngs_mpa']} MPa", "PoissonRatio": str(m["poisson"]),
                 "Density": f"{m['density_kg_m3']} kg/m^3",
                 "ThermalConductivity": f"{m['conductivity_w_mk']} W/m/K",
                 "SpecificHeat": f"{m['specific_heat_j_kgk']} J/kg/K",
                 "ThermalExpansionCoefficient": f"{m.get('expansion_um_mk', 12)} um/m/K"})
    mat.Material = card
    analysis.addObject(mat)

    used = {}
    if job["fixed_faces"]:
        fixed = ObjectsFem.makeConstraintFixed(doc, "Fixed")
        fixed.References = _refs(geom, job["fixed_faces"])
        analysis.addObject(fixed)
        used["fixed"] = fixed
    for i, load in enumerate(job.get("loads_resolved") or []):
        if load["type"] == "force":
            c = ObjectsFem.makeConstraintForce(doc, f"Force{i}")
            c.References = _refs(geom, load["faces"])
            c.Force = f"{abs(load['newtons'])} N"
            d = load.get("direction")
            if d == "normal" or d is None:
                c.Reversed = True  # FreeCAD's default normal points out of the solid; push into it
            else:
                v = FreeCAD.Vector(*d)
                if v.Length == 0:
                    raise FeaError("bad_args", "A force direction can't be [0, 0, 0].")
                v.normalize()
                if load["newtons"] < 0:
                    v = v * -1
                line = doc.addObject("Part::Feature", f"ForceDir{i}")
                import Part
                line.Shape = Part.makeLine(FreeCAD.Vector(0, 0, 0), v)
                c.Direction = (line, ["Edge1"])
                c.Reversed = False
        elif load["type"] == "pressure":
            c = ObjectsFem.makeConstraintPressure(doc, f"Pressure{i}")
            c.References = _refs(geom, load["faces"])
            c.Pressure = f"{load['mpa']} MPa"
            c.Reversed = load["mpa"] < 0
        elif load["type"] == "gravity":
            c = ObjectsFem.makeConstraintSelfWeight(doc, f"Gravity{i}")
            c.GravityAcceleration = f"{load.get('g', 9.81) * 1000} mm/s^2"
            c.GravityDirection = FreeCAD.Vector(*(load.get("direction") or [0, 0, -1]))
        else:
            continue
        analysis.addObject(c)

    th = job.get("thermal_resolved") or {}
    if kind == "thermal":
        init = ObjectsFem.makeConstraintInitialTemperature(doc, "InitialTemp")
        init.initialTemperature = f"{th.get('initial_c', 20) + 273.15} K"
        analysis.addObject(init)
        for i, t in enumerate(th.get("temperatures") or []):
            c = ObjectsFem.makeConstraintTemperature(doc, f"Temp{i}")
            c.References = _refs(geom, t["faces"])
            c.ConstraintType = "Temperature"
            c.Temperature = f"{t['celsius'] + 273.15} K"
            analysis.addObject(c)
        for i, h in enumerate(th.get("convection") or []):
            c = ObjectsFem.makeConstraintHeatflux(doc, f"Convection{i}")
            c.References = _refs(geom, h["faces"])
            c.ConstraintType = "Convection"
            c.FilmCoef = f"{h['h_w_m2k']} W/m^2/K"
            c.AmbientTemp = f"{h.get('ambient_c', 20) + 273.15} K"
            analysis.addObject(c)
        for i, h in enumerate(th.get("heat_flux") or []):
            c = ObjectsFem.makeConstraintHeatflux(doc, f"Flux{i}")
            c.References = _refs(geom, h["faces"])
            c.ConstraintType = "DFlux"
            c.DFlux = f"{h['w_m2']} W/m^2"
            analysis.addObject(c)
    doc.recompute()
    return analysis, solver


def mesh(doc, analysis, geom, size, second_order, tmp):
    import ObjectsFem
    from femmesh.gmshtools import GmshTools
    m = ObjectsFem.makeMeshGmsh(doc, "Mesh")
    m.Shape = geom
    m.CharacteristicLengthMax = f"{size} mm"
    m.CharacteristicLengthMin = f"{size / 10} mm"
    m.ElementOrder = "2nd" if second_order else "1st"
    analysis.addObject(m)
    doc.recompute()
    gm = GmshTools(m)
    gm.load_properties()
    gm.update_mesh_data()
    gm.get_tmp_file_paths(tmp, True)
    cmd = mesher_command()
    gm.gmsh_bin = cmd[-1]  # only quoted in the .geo file's comments
    gm.write_gmsh_input_files()
    code, _out, err = _run(cmd + ["-", gm.temp_file_geo], tmp, "Meshing")
    if code or not os.path.exists(gm.temp_file_mesh):
        raise FeaError("mesh_failed", "Gmsh couldn't mesh the part. Very thin walls or tiny features can cause "
                       "this; try a smaller mesh size. " + err.strip()[-400:])
    gm.read_and_set_new_mesh()
    doc.recompute()
    return m


def solve(analysis, solver, tmp):
    from femtools.ccxtools import FemToolsCcx
    fea = FemToolsCcx(analysis, solver)
    fea.update_objects()
    fea.setup_working_dir(tmp, True)
    problems = fea.check_prerequisites()
    if problems:
        raise FeaError("setup_error", "The analysis isn't complete: " + problems.strip())
    fea.write_inp_file()
    if not fea.inp_file_name:
        raise FeaError("setup_error", "FreeCAD couldn't write the CalculiX input file.")
    ccx = find_tool("ccx")
    base = os.path.splitext(os.path.basename(fea.inp_file_name))[0]
    os.environ.setdefault("OMP_NUM_THREADS", str(min(os.cpu_count() or 1, 4)))
    code, out, err = _run([ccx, "-i", base], os.path.dirname(fea.inp_file_name), "The solver")
    if code or not os.path.exists(os.path.join(os.path.dirname(fea.inp_file_name), base + ".frd")):
        hint = re.findall(r"\*ERROR.*", out + err)
        raise FeaError("solve_failed", "CalculiX stopped with an error: " + (" ".join(hint[:3]) or err.strip()[-400:]
                       or f"exit code {code}") + " Often this means the part isn't held still (add fixed faces).")
    fea.load_results()
    return fea, os.path.join(os.path.dirname(fea.inp_file_name), base + ".dat")


def parse_reactions(dat_path):
    """Total support reactions CalculiX prints for each fixed set ('*NODE PRINT ... TOTALS=ONLY')."""
    out = []
    try:
        lines = open(dat_path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return out
    for i, line in enumerate(lines):
        m = re.search(r"total force \(fx,fy,fz\) for set (\S+)", line)
        if m:
            for nxt in lines[i + 1:i + 4]:
                nums = re.findall(r"[-+]?\d*\.?\d+(?:[EeDd][-+]?\d+)?", nxt)
                if len(nums) >= 3:
                    fx, fy, fz = (float(n.replace("D", "E").replace("d", "e")) for n in nums[:3])
                    out.append({"set": m.group(1), "fx": _r(fx, 2), "fy": _r(fy, 2), "fz": _r(fz, 2)})
                    break
    return out[-len({r["set"] for r in out}):] if out else out  # last increment only


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

def _p95(values):
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(0.95 * (len(s) - 1)))]


def _where(shape, fixed_faces, point, size):
    import Part
    v = Part.Vertex(point)
    for name in fixed_faces:
        if shape.getElement(name).distToShape(v)[0] <= 1.5 * size:
            return "fixed_face"
    if min((e.distToShape(v)[0] for e in shape.Edges), default=1e9) <= 0.5 * size:
        return "edge"
    return "interior"


def extract(job, analysis, geom, size, dat_path):
    shape = geom.Shape
    results = [o for o in analysis.Group if o.isDerivedFrom("Fem::FemResultObject")]
    mesh_obj = next(o for o in analysis.Group if o.isDerivedFrom("Fem::FemMeshObject") and o.Name.startswith("Mesh"))
    fm = mesh_obj.FemMesh
    out = {"mesh": {"nodes": fm.NodeCount, "elements": fm.VolumeCount, "size_mm": _r(size, 3),
                    "order": 2 if job["mesh"]["second_order"] else 1}}
    if not results:
        raise FeaError("no_results", "CalculiX finished but produced no results.")
    res = {}
    kind = job["analysis"]
    if kind == "static":
        r = results[0]
        nodes = r.Mesh.FemMesh.Nodes
        vm, disp, ids = list(r.vonMises), list(r.DisplacementLengths), list(r.NodeNumbers)
        i_vm = max(range(len(vm)), key=vm.__getitem__)
        i_d = max(range(len(disp)), key=disp.__getitem__)
        at_vm, at_d = nodes[ids[i_vm]], nodes[ids[i_d]]
        yld = job["material"]["yield_mpa"]
        dvec = r.DisplacementVectors[i_d]
        res.update({"max_von_mises_mpa": _r(vm[i_vm], 3), "von_mises_p95_mpa": _r(_p95(vm), 3),
                    "max_von_mises_at": [_r(c) for c in at_vm],
                    "peak_near": _where(shape, job["fixed_faces"], at_vm, size),
                    "max_displacement_mm": _r(disp[i_d], 5), "max_displacement_at": [_r(c) for c in at_d],
                    "max_displacement_vector": [_r(c, 5) for c in dvec],
                    "yield_mpa": yld, "safety_factor": _r(yld / vm[i_vm], 3) if vm[i_vm] > 0 else None,
                    "safety_factor_p95": _r(yld / _p95(vm), 3) if _p95(vm) > 0 else None,
                    "reactions": parse_reactions(dat_path)})
        out["_plot"] = ("Von Mises stress (MPa)", [nodes[n] for n in ids], vm)
    elif kind == "modal":
        modes = sorted(((o.Eigenmode, o.EigenmodeFrequency) for o in results if o.Eigenmode > 0))
        res["modes"] = [{"mode": int(m), "hz": _r(f, 3)} for m, f in modes]
        res["rigid_modes"] = sum(1 for _, f in modes if f < 1.0)
        first = min((o for o in results if o.Eigenmode > 0), key=lambda o: o.Eigenmode)
        nodes = first.Mesh.FemMesh.Nodes
        out["_plot"] = (f"Mode 1 shape ({modes[0][1]:.1f} Hz), relative displacement",
                        [nodes[n] for n in first.NodeNumbers], list(first.DisplacementLengths))
    else:
        r = results[0]
        temps = [t - 273.15 for t in r.Temperature]
        nodes = r.Mesh.FemMesh.Nodes
        res["temperature_c"] = {"min": _r(min(temps), 2), "max": _r(max(temps), 2)}
        out["_plot"] = ("Temperature (°C)", [nodes[n] for n in r.NodeNumbers], temps)
    out["results"] = res
    return out


def plot(spec, modes, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    title, pts, vals = spec
    step = max(1, len(pts) // 20000)
    pts, vals = pts[::step], vals[::step]
    fig = plt.figure(figsize=(9, 5.5) if modes else (7, 5.5), dpi=110)
    ax = fig.add_subplot(1, 2 if modes else 1, 1, projection="3d")
    sc = ax.scatter([p.x for p in pts], [p.y for p in pts], [p.z for p in pts], c=vals, cmap="viridis", s=4)
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_zlabel("z (mm)")
    try:
        ax.set_box_aspect([max(1e-6, max(p.x for p in pts) - min(p.x for p in pts)),
                           max(1e-6, max(p.y for p in pts) - min(p.y for p in pts)),
                           max(1e-6, max(p.z for p in pts) - min(p.z for p in pts))])
    except Exception:
        pass
    ax.set_title(title, fontsize=10)
    fig.colorbar(sc, ax=ax, shrink=0.7)
    if modes:
        bx = fig.add_subplot(1, 2, 2)
        bx.bar([str(m["mode"]) for m in modes], [m["hz"] for m in modes], color="#3e9be9")
        bx.set_xlabel("mode")
        bx.set_ylabel("frequency (Hz)")
        bx.set_title("Natural frequencies", fontsize=10)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def run_once(job, shape, size, tmp):
    import FreeCAD
    doc = FreeCAD.newDocument("CadSwarmFEA")
    try:
        geom = doc.addObject("Part::Feature", "Geom")
        geom.Shape = shape
        analysis, solver = build(doc, geom, job)
        t0 = time.time()
        mesh(doc, analysis, geom, size, job["mesh"]["second_order"], tmp)
        t1 = time.time()
        fea, dat = solve(analysis, solver, tmp)
        t2 = time.time()
        out = extract(job, analysis, geom, size, dat)
        out["timing"] = {"mesh_seconds": _r(t1 - t0, 2), "solve_seconds": _r(t2 - t1, 2)}
        return out
    finally:
        FreeCAD.closeDocument(doc.Name)


def run(job, tmp):
    import FreeCAD
    import Part
    shape = Part.Shape()
    shape.importBrep(job["brep_path"])
    shape, warnings = analysis_shape(shape)
    # Faces are resolved on the final solid, so indices match what the mesh groups use.
    job["fixed_faces"] = select_faces(shape, job.get("fixed") or [])
    job["loads_resolved"] = [dict(l, faces=select_faces(shape, l.get("faces") or [])) if l["type"] != "gravity"
                             else dict(l) for l in job.get("loads") or []]
    th = dict(job.get("thermal") or {})
    for key in ("temperatures", "convection", "heat_flux"):
        th[key] = [dict(t, faces=select_faces(shape, t.get("faces") or [])) for t in th.get(key) or []]
    job["thermal_resolved"] = th

    size = float(job["mesh"]["max_size_mm"] or shape.BoundBox.DiagonalLength / 40)
    est = shape.Volume / (0.118 * size ** 3)
    if est > MAX_ELEMENTS:
        new = (shape.Volume / (0.118 * MAX_ELEMENTS)) ** (1 / 3)
        warnings.append(f"Mesh size {size:.3g} mm would need about {est:,.0f} elements; used {new:.3g} mm instead.")
        size = new

    out = run_once(job, shape, size, os.path.join(tmp, "run1"))
    conv = None
    if job.get("convergence_check"):
        fine = run_once(job, shape, size * 0.6, os.path.join(tmp, "run2"))
        a, b = out["results"], fine["results"]
        key = {"static": "max_displacement_mm", "modal": None, "thermal": None}[job["analysis"]]
        if key:
            conv = {"coarse_size_mm": _r(size, 3), "fine_size_mm": _r(size * 0.6, 3),
                    "coarse": a[key], "fine": b[key], "quantity": key,
                    "coarse_max_vm": a["max_von_mises_mpa"], "fine_max_vm": b["max_von_mises_mpa"],
                    "change_pct": _r(abs(b[key] - a[key]) / abs(b[key]) * 100, 2) if b[key] else 0.0,
                    "vm_change_pct": _r(abs(b["max_von_mises_mpa"] - a["max_von_mises_mpa"])
                                        / b["max_von_mises_mpa"] * 100, 2) if b["max_von_mises_mpa"] else 0.0}
        elif job["analysis"] == "modal" and a.get("modes") and b.get("modes"):
            fa, fb = a["modes"][0]["hz"], b["modes"][0]["hz"]
            conv = {"quantity": "mode_1_hz", "coarse": fa, "fine": fb, "coarse_size_mm": _r(size, 3),
                    "fine_size_mm": _r(size * 0.6, 3), "change_pct": _r(abs(fb - fa) / fb * 100, 2) if fb else 0.0}
        elif job["analysis"] == "thermal":
            ta, tb = a["temperature_c"]["max"], b["temperature_c"]["max"]
            conv = {"quantity": "max_temperature_c", "coarse": ta, "fine": tb, "coarse_size_mm": _r(size, 3),
                    "fine_size_mm": _r(size * 0.6, 3),
                    "change_pct": _r(abs(tb - ta) / abs(tb) * 100, 2) if tb else 0.0}
        out, fine_plot = fine, fine.pop("_plot", None)  # report the finer, more accurate run
        out["_plot"] = fine_plot
    spec = out.pop("_plot", None)
    has_plot = bool(spec) and plot(spec, out["results"].get("modes"), os.path.join(tmp, "plot.png"))
    if spec and not has_plot:
        warnings.append("No result picture: matplotlib isn't available in this FreeCAD.")
    bb = shape.BoundBox
    out["results"]["convergence"] = conv
    out.update({"ok": True, "warnings": warnings, "plot": "plot.png" if has_plot else None,
                "geometry": {"bbox": [_r(v) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)],
                             "volume_mm3": _r(shape.Volume, 2), "faces": len(shape.Faces),
                             "min_edge_mm": _r(min((e.Length for e in shape.Edges), default=0), 3),
                             "mass_kg": _r(shape.Volume * 1e-9 * job["material"]["density_kg_m3"], 4)},
                "resolved": {"fixed": job["fixed_faces"],
                             "loads": [{k: v for k, v in l.items()} for l in job["loads_resolved"]],
                             "thermal": th},
                "freecad": ".".join(FreeCAD.Version()[:3])})
    return out


def main():
    job_path = sys.argv[1]
    tmp = os.path.dirname(os.path.abspath(job_path))
    try:
        with open(job_path, encoding="utf-8") as f:
            job = json.load(f)
        out = run(job, tmp)
    except FeaError as e:
        out = {"ok": False, "code": e.code, "error": str(e)}
    except Exception as e:  # FreeCAD/OCC can raise anything; report it, never hang
        out = {"ok": False, "code": "fea_error", "error": f"{type(e).__name__}: {e}",
               "trace": traceback.format_exc(limit=4)}
    with open(os.path.join(tmp, "result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, default=str)


if __name__ == "__main__":
    main()
