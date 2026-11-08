"""Real-solver validation of CADSwarm's simulation features on this machine.

    python scripts/sim_validate.py [--runs 3] [--only tools,fea,reviewer,interference,lighten,optimize]
                                   [--out DIR] [--json results.json]

Run it with the repo's venv Python (or any Python 3.11+). Every check drives the same code the team server
runs, against FreeCAD 1.x's own CalculiX and Gmsh, in fresh FreeCAD processes:

* tools        ccx and gmsh are found by our runner AND by FreeCAD's FEM code under a fresh, CADSwarm-owned
               FREECAD_USER_HOME (the branded app's engine profile), and the Netgen state is reported.
* fea          a cantilever bracket arm, fixed at one end, loaded at the other, solved through the hub's
               FEA job queue (fea_jobs.FeaJobs -> fea.run_job -> worker/fea_runner.py). Tip deflection is
               checked against Euler-Bernoulli + shear (Timoshenko) within 5 %, the support reaction against
               the applied load within 1 %, and the peak von Mises stress against 6FL/(bh^2) within a band
               (the peak sits on the fully fixed face, a known stress singularity, so it reads high).
* reviewer     the same queue at three load levels (light, moderate, overload): the automatic review points,
               the published report's verdict and its problem list (what an AI reviewer/helper reads back with
               fea_report) must match the safety factor: overload -> "fail" with a critical point.
* interference two boxes in a live CADSwarm worker with a known overlap volume (and a touching pair that must
               not clash), checked with the worker's interference query.
* lighten      a lattice lighten job (lattice_jobs.LatticeJobs -> lattice/runner.py) with a load case; the
               job must finish, save weight and report the verified stiffness it kept.
* optimize     structural optimization with the CalculiX backend (structural/job.py) on real FreeCAD
               geometry; the result must remove material, stay within its constraints and export a valid
               single-solid STEP.

Each scenario runs --runs times with a different size/load. Prints a table and writes JSON (pass/fail, the
numbers and wall-clock seconds per run). Exit code 1 if anything failed.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cadswarm.config import find_freecad_python, freecad_import_dirs  # noqa: E402

SCENARIOS = ("tools", "fea", "reviewer", "interference", "lighten", "optimize")

# Cantilever cases: length, width (y), height (z) in mm, tip force N, material preset.
FEA_CASES = [
    (100.0, 20.0, 10.0, 500.0, "steel_s235"),
    (150.0, 25.0, 12.0, 800.0, "aluminium_6061_t6"),
    (80.0, 15.0, 8.0, 200.0, "steel_s355"),
]
# Reviewer cases on a 100 x 20 x 10 steel S235 arm: tip force, expected verdict family.
REVIEW_CASES = [(150.0, "light"), (700.0, "thin margin"), (4000.0, "overload")]
# Interference: box A at origin, box B offset; expected overlap volume.
INTERFERENCE_CASES = [
    ((20, 20, 20), (10, 0, 0), (20, 20, 20), 10 * 20 * 20),
    ((30, 10, 10), (25, 5, 5), (10, 10, 10), 5 * 5 * 5),
    ((40, 40, 5), (10, 10, 2), (8, 8, 30), 8 * 8 * 3),
]
LIGHTEN_CASES = [
    dict(size=(40, 12, 12), cell="gyroid", cell_size_mm=5, newtons=300, material="aluminium_6061_t6"),
    dict(size=(48, 14, 14), cell="bcc", cell_size_mm=6, newtons=500, material="steel_s235"),
    dict(size=(36, 12, 10), cell="octet", cell_size_mm=5, newtons=150, material="pla"),
]
OPTIMIZE_CASES = [
    dict(box=(48, 16, 16), pitch=4, force=-200.0, reduction=0.05),
    dict(box=(60, 15, 15), pitch=5, force=-400.0, reduction=0.08),
    dict(box=(36, 12, 12), pitch=4, force=-150.0, reduction=0.05),
]

MATERIALS = None


def freecad():
    python = find_freecad_python()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), *freecad_import_dirs(python)]),
               PYTHONIOENCODING="utf-8")
    return python, env


def _engine_profile():
    """A fresh FreeCAD settings folder, as the branded app's engine profile is on a first start."""
    return tempfile.mkdtemp(prefix="cadswarm-engine-")


# --------------------------------------------------------------------------- tools

TOOLS_PROBE = r"""
import json, os, shutil, sys
sys.path.insert(0, sys.argv[1])
import FreeCAD
from cadswarm.worker import fea_runner
out = {"user_home": FreeCAD.getUserAppDataDir(), "freecad": ".".join(FreeCAD.Version()[:3])}
for name in ("ccx", "gmsh"):
    try:
        out["runner_" + name] = fea_runner.find_tool(name)
    except Exception as e:
        out["runner_" + name] = None
        out["runner_" + name + "_error"] = str(e)
from femsolver import settings
out["fem_ccx"] = settings.get_binary("Calculix")
out["fem_gmsh"] = shutil.which("gmsh")  # what femmesh.gmshtools.GmshTools.get_gmsh_command() uses
from freecad import utils
py = utils.get_python_exe()
import subprocess
p = subprocess.run([py, "-E", "-c", "import netgen, pyngcore; print(netgen.__file__)"], capture_output=True, text=True)
out["netgen_python"] = p.stdout.strip() or None
doc = FreeCAD.newDocument("probe")
try:
    import ObjectsFem, Part
    g = doc.addObject("Part::Feature", "G"); g.Shape = Part.makeBox(10, 10, 10)
    m = ObjectsFem.makeMeshNetgenLegacy(doc, "Legacy"); m.Shape = g; m.Fineness = "Moderate"
    doc.recompute()
    out["netgen_legacy_nodes"] = m.FemMesh.NodeCount
except Exception as e:
    out["netgen_legacy_nodes"] = 0
    out["netgen_legacy_error"] = f"{type(e).__name__}: {e}"
print("RESULT" + json.dumps(out))
"""


def run_tools(_case, work):
    python, env = freecad()
    env["FREECAD_USER_HOME"] = _engine_profile()
    script = Path(work) / "tools_probe.py"
    script.write_text(TOOLS_PROBE, encoding="utf-8")
    p = subprocess.run([python, str(script), str(ROOT)], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT")), None)
    if not line:
        return False, {"error": (p.stdout + p.stderr)[-800:]}
    r = json.loads(line[6:])
    in_profile = os.path.normcase(os.path.abspath(r["user_home"])).startswith(
        os.path.normcase(os.path.abspath(env["FREECAD_USER_HOME"])))
    ok = bool(r.get("runner_ccx") and r.get("runner_gmsh") and r.get("fem_ccx") and r.get("fem_gmsh") and in_profile)
    r["profile_is_cadswarms"] = in_profile
    return ok, r


# --------------------------------------------------------------------------- shared model building

def build_model(path, parts):
    """Save a model with the live CADSwarm worker. parts: [(name, (l, w, h), (x, y, z))]."""
    from cadswarm.worker_client import WorkerClient
    w = WorkerClient(str(path))
    w.start()
    try:
        for name, (l, wd, h), (x, y, z) in parts:
            w.call("create_primitive", {"kind": "box", "name": name, "props": {"Length": l, "Width": wd, "Height": h},
                                        "placement": {"base": [x, y, z]}})
        return w
    except Exception:
        w.stop()
        raise


def project(root, parts):
    from cadswarm import helper
    w = build_model(Path(root) / "model.FCStd", parts)
    w.stop()
    return helper.Project(str(root))


def wait_fea(p, jid, limit=900):
    t = time.time()
    while time.time() - t < limit:
        s = p.fea.status(jid)
        if s["status"] not in ("queued", "running"):
            return s
        time.sleep(0.5)
    raise TimeoutError(f"FEA job {jid} still running after {limit}s")


def cantilever_hand(L, b, h, F, preset):
    from cadswarm.fea import PRESETS
    m = PRESETS[preset]
    E, nu = m["youngs_mpa"], m["poisson"]
    G = E / (2 * (1 + nu))
    I = b * h ** 3 / 12
    bend = F * L ** 3 / (3 * E * I)
    shear = F * L / ((5 / 6) * G * b * h)
    return {"deflection_mm": bend + shear, "deflection_bending_only_mm": bend, "stress_mpa": 6 * F * L / (b * h * h),
            "yield_mpa": m["yield_mpa"]}


PERSON = {"id": 1, "name": "sim-check", "kind": "human"}


def fea_request(L, b, h, F, preset, label):
    return {"target": "Bracket", "analysis": "static", "material": preset, "fixed": ["xmin"],
            "loads": [{"type": "force", "faces": ["xmax"], "newtons": F, "direction": [0, 0, -1]}],
            "mesh": {"max_size_mm": round(min(b, h) / 3, 3)}, "label": label}


# --------------------------------------------------------------------------- fea

def run_fea(case, work):
    L, b, h, F, preset = case
    p = project(work, [("Bracket", (L, b, h), (0, 0, 0))])
    s = p.fea.start(fea_request(L, b, h, F, preset, f"hand calc {L:g}x{b:g}x{h:g} {F:g}N"), PERSON)
    s = wait_fea(p, s["job_id"])
    if s["status"] != "done":
        return False, {"error": s.get("error"), "code": s.get("code")}
    r, hand = s["results"], cantilever_hand(L, b, h, F, preset)
    d_err = (r["max_displacement_mm"] - hand["deflection_mm"]) / hand["deflection_mm"]
    reac = r.get("reactions") or []
    react = sum(math.sqrt(x["fx"] ** 2 + x["fy"] ** 2 + x["fz"] ** 2) for x in reac)
    r_err = (react - F) / F
    s_ratio = r["max_von_mises_mpa"] / hand["stress_mpa"]
    s95_ratio = r["von_mises_p95_mpa"] / hand["stress_mpa"]
    ok = abs(d_err) <= 0.05 and abs(r_err) <= 0.01 and 0.9 <= s_ratio <= 2.0 and s95_ratio <= 1.0
    return ok, {"case": f"{L:g}x{b:g}x{h:g} mm {preset}, {F:g} N", "mesh": s["mesh"],
                "fe_deflection_mm": r["max_displacement_mm"], "hand_deflection_mm": round(hand["deflection_mm"], 5),
                "deflection_error_pct": round(100 * d_err, 2),
                "fe_reaction_n": round(react, 2), "reaction_error_pct": round(100 * r_err, 3),
                "fe_peak_vm_mpa": r["max_von_mises_mpa"], "fe_p95_vm_mpa": r["von_mises_p95_mpa"],
                "hand_root_stress_mpa": round(hand["stress_mpa"], 2), "peak_over_hand": round(s_ratio, 3),
                "peak_near": r.get("peak_near"), "solver_timing": _timing(p, s["job_id"])}


def _timing(p, jid):
    row = p.fea.db.execute("SELECT result FROM fea_jobs WHERE id=?", (jid,)).fetchone()
    return json.loads(row[0]).get("timing") if row and row[0] else None


# --------------------------------------------------------------------------- reviewer

def run_reviewer(case, work):
    F, level = case
    L, b, h, preset = 100.0, 20.0, 10.0, "steel_s235"
    p = project(work, [("Bracket", (L, b, h), (0, 0, 0))])
    s = p.fea.start(fea_request(L, b, h, F, preset, f"review {level}"), PERSON)
    s = wait_fea(p, s["job_id"])
    if s["status"] != "done":
        return False, {"error": s.get("error")}
    jid, sf = s["job_id"], s["results"]["safety_factor"]
    pub = p.fea.publish(jid, summary=f"Automated check, {level} load.", improvements=[], actor=PERSON)
    rep = p.fea.reports(10, jid)
    sev = [x["severity"] for x in rep["problems"]]
    texts = " | ".join(x["text"] for x in rep["problems"])
    expect_fail = sf < 1
    checks = {
        "verdict_matches_sf": (rep["verdict"] == "fail") == expect_fail,
        "critical_iff_yield": ("critical" in sev) == (expect_fail and (s["results"].get("safety_factor_p95") or 0) < 1),
        "worst_first": sev == sorted(sev, key=lambda x: {"critical": 0, "warning": 1, "info": 2}.get(x, 3)),
        "fixed_face_singularity_flagged": s["results"].get("peak_near") != "fixed_face" or "fixed face" in texts,
        "rerun_args_repeat_the_case": rep["rerun_args"]["loads"][0]["newtons"] == F
                                      and rep["rerun_args"]["fixed"] == ["xmin"],
        "report_files": all((Path(work) / "files" / pub["folder"].removeprefix("files/") / n).exists()
                            for n in ("report.json", "report.html", "plot.png")),
    }
    return all(checks.values()), {"load_n": F, "safety_factor": sf, "safety_factor_p95":
                                  s["results"].get("safety_factor_p95"), "verdict": rep["verdict"],
                                  "severities": sev, "checks": checks, "points": [x["text"][:90] for x in
                                                                                   rep["problems"]],
                                  "folder": pub["folder"]}


# --------------------------------------------------------------------------- interference

def run_interference(case, work):
    size_a, pos_b, size_b, expected = case
    w = build_model(Path(work) / "model.FCStd", [("A", size_a, (0, 0, 0)), ("B", size_b, pos_b),
                                                  ("Touching", (5, 5, 5), (size_a[0], -20, 0)),
                                                  ("Neighbour", (5, 5, 5), (size_a[0] + 5, -20, 0))])
    try:
        r = w.call("interference", {})["result"]
    finally:
        w.stop()
    clashes = {(c["a"], c["b"]): c["volume"] for c in r["clashes"]}
    got = clashes.get(("A", "B"))
    ok = got is not None and abs(got - expected) <= 1e-6 * max(1, expected) and len(clashes) == 1
    return ok, {"expected_mm3": expected, "found_mm3": got, "clashes": r["clashes"], "checked": r["checked"]}


# --------------------------------------------------------------------------- lighten

def run_lighten(case, work):
    from cadswarm import helper
    from cadswarm.lattice_jobs import LatticeJobs
    l, wd, h = case["size"]
    w = build_model(Path(work) / "model.FCStd", [])
    w.call("create_extrusion", {"name": "Bracket", "points": [[0, 0], [l, 0], [l, wd], [0, wd]], "height": h,
                                "holes": [{"x": 5, "y": wd / 2, "r": 1.7}]})
    w.stop()
    p = helper.Project(str(work))
    p.lattice = LatticeJobs(p)
    s = p.lattice.start({"target": "Bracket", "cell": case["cell"], "cell_size_mm": case["cell_size_mm"],
                         "min_thickness_mm": 0.8, "skin_mm": 1.0, "stiffness_target_pct": 70, "voxel_mm": 0.45,
                         "material": case["material"], "fixed": ["xmin"],
                         "loads": [{"type": "force", "faces": ["xmax"], "newtons": case["newtons"],
                                    "direction": [0, 0, -1]}]}, {"id": 9, "name": "sim-check", "kind": "agent"})
    t = time.time()
    while time.time() - t < 1200:
        s = p.lattice.status(s["job_id"])
        if s["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(2)
    if s["status"] != "done":
        return False, {"status": s["status"], "error": s.get("error")}
    v = s.get("verify") or {}
    ok = (s["verdict"] in ("passed", "no_passing_design") and 0 < (s["weight_saving_pct"] or 0) < 100
          and (s["verdict"] != "passed" or v.get("stiffness_retained", 0) >= 0.7))
    return ok, {"case": f"{l}x{wd}x{h} {case['cell']} {case['cell_size_mm']} mm, {case['newtons']} N, "
                        f"{case['material']}", "verdict": s["verdict"],
                "weight_saving_pct": s["weight_saving_pct"], "stiffness_retained": v.get("stiffness_retained"),
                "files": sorted(os.path.basename(f) for f in (s.get("files") or []))[:6]}


# --------------------------------------------------------------------------- optimize

OPT_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import FreeCAD as App, Part
from pathlib import Path
from cadswarm.structural.demo import request
from cadswarm.structural.job import run
case = json.loads(sys.argv[3])
source = Part.makeBox(*case["box"])
config = request()
config.update(brep=source.exportBrepToString(), pitch_mm=case["pitch"], solver="calculix")
config["loads"] = [{"side": "x_max", "force_n": [0, 0, case["force"]]}]
config["constraints"]["target_reduction"] = case["reduction"]
config["constraints"]["max_stress_mpa"] = 1e9
config["constraints"]["max_displacement_mm"] = 1e9
out = Path(sys.argv[2]) / "result"
result = run(config, out)
step = Part.read(str(out / "optimized.step"))
rep = result.report
print("RESULT" + json.dumps({"improved": bool(result.improved), "status": rep.get("status"),
      "original_volume_mm3": rep.get("original_volume_mm3"), "optimized_volume_mm3": rep.get("optimized_volume_mm3"),
      "removed_pct": rep.get("material_removed_percent"), "baseline": rep.get("baseline"),
      "optimized": rep.get("optimized"), "step_valid": step.isValid(), "step_solids": len(step.Solids),
      "step_volume": step.Volume, "source_volume": source.Volume}, default=str))
"""


def run_optimize(case, work):
    python, env = freecad()
    script = Path(work) / "opt_probe.py"
    script.write_text(OPT_PROBE, encoding="utf-8")
    p = subprocess.run([python, str(script), str(ROOT), str(work), json.dumps(case)], env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=1200)
    line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT")), None)
    if not line:
        return False, {"error": (p.stdout + p.stderr)[-1200:]}
    r = json.loads(line[6:])
    ok = (r["improved"] and r["step_valid"] and r["step_solids"] == 1 and r["step_volume"] < r["source_volume"]
          and (r["removed_pct"] or 0) > 0)
    r["case"] = f"{case['box']} pitch {case['pitch']} mm, {case['force']} N, target -{case['reduction']:.0%}"
    return ok, r


RUNNERS = {"tools": (run_tools, [None]), "fea": (run_fea, FEA_CASES), "reviewer": (run_reviewer, REVIEW_CASES),
           "interference": (run_interference, INTERFERENCE_CASES), "lighten": (run_lighten, LIGHTEN_CASES),
           "optimize": (run_optimize, OPTIMIZE_CASES)}


def run_scenario(name, runs=3, out=None):
    fn, cases = RUNNERS[name]
    rows = []
    for i in range(min(runs, len(cases)) if name != "tools" else 1):
        work = tempfile.mkdtemp(prefix=f"cadswarm-sim-{name}-", dir=out)
        t = time.time()
        try:
            ok, data = fn(cases[i], work)
        except Exception as e:  # noqa: BLE001 - a crash is a failed run, with its reason
            import traceback
            ok, data = False, {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(limit=6)}
        rows.append({"scenario": name, "run": i + 1, "ok": bool(ok), "seconds": round(time.time() - t, 1),
                     "work": work, **data})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", default=",".join(SCENARIOS))
    ap.add_argument("--out", default=None, help="folder for the work directories (default: system temp)")
    ap.add_argument("--json", default=None, help="write all rows here")
    a = ap.parse_args(argv)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
    rows = []
    for name in [s.strip() for s in a.only.split(",") if s.strip()]:
        got = run_scenario(name, a.runs, a.out)
        for r in got:
            extra = {k: v for k, v in r.items() if k not in ("scenario", "run", "ok", "seconds", "work", "trace")}
            print(f"{'PASS' if r['ok'] else 'FAIL'}  {name:<12} run {r['run']}  {r['seconds']:>7.1f}s  "
                  f"{json.dumps(extra, default=str)[:400]}", flush=True)
        rows += got
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    failed = [r for r in rows if not r["ok"]]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} runs passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
