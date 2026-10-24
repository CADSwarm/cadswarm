"""Runs ONE lighten job in a throwaway FreeCAD process: `python runner.py <job.json>`.

Like the FEA runner, it works on its own copy of the saved model, so a long or crashing job can never stall
or change the shared document. It resolves the part and the load case (the same face selectors and loads as
run_fea, via fea_runner), tessellates them, and hands triangles to the NumPy pipeline.

job.json: {doc_path, target, recipe{...}, fea{analysis: "static", material, fixed, loads} or null, out_dir,
           name}. Progress goes to progress.jsonl next to the job; a file named `cancel` there stops it.
Writes result.json: {"ok": true, "report": {...}} or {"ok": false, "code", "error"}.
"""
import json
import os
import sys
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
# Before FreeCAD: importing FreeCAD puts its Mod folders on the path, where an installed CadSwarm add-on
# (possibly older) would shadow this checkout's package.
import cadswarm  # noqa: E402,F401
FREECAD_BIN = os.environ.get("CADSWARM_FREECAD_BIN")
for _d in ([FREECAD_BIN, os.path.join(FREECAD_BIN, os.pardir, "lib")] if FREECAD_BIN else []):
    if os.path.isdir(_d) and os.path.abspath(_d) not in sys.path:
        sys.path.append(os.path.abspath(_d))


class JobError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def tessellate(shape, tol):
    import numpy as np
    pts, tris = shape.tessellate(tol)
    return np.array([[p.x, p.y, p.z] for p in pts], float), np.array(tris, int).reshape(-1, 3)


def face_points(shape, names, step, tol):
    """Dense points on the named faces, plus their total area."""
    import numpy as np
    from cadswarm.lattice.loadcase import sample_triangles
    chunks, area = [], 0.0
    for n in names:
        face = shape.Faces[int(n[4:]) - 1]
        v, t = tessellate(face, tol)
        chunks.append(sample_triangles(v, t, step))
        area += face.Area
    return (np.concatenate(chunks) if chunks else np.zeros((0, 3))), area


def load_case(shape, fea, step, tol):
    """FEA-style supports and loads (normalized by cadswarm.fea.normalize_job) as sampled points."""
    from cadswarm.worker.fea_runner import select_faces
    fixed, _ = face_points(shape, select_faces(shape, fea.get("fixed") or []), step, tol)
    if not len(fixed):
        raise JobError("bad_args", "The load case holds no face still: give fixed faces (e.g. ['xmin']).")
    loads = []
    for load in fea.get("loads") or []:
        if load["type"] == "gravity":
            loads.append(dict(load))
            continue
        pts, area = face_points(shape, select_faces(shape, load.get("faces") or []), step, tol)
        loads.append(dict(load, points=pts, area=area))
    if not loads:
        raise JobError("bad_args", "The load case has no load: add a force, pressure or gravity.")
    return {"fixed": fixed, "loads": loads}


def run(job, here):
    import FreeCAD  # noqa: F401  (Part needs the FreeCAD module loaded)
    from cadswarm.lattice import build, pipeline
    from cadswarm.worker.fea_runner import FeaError, analysis_shape, load_target
    progress_path = os.path.join(here, "progress.jsonl")
    cancel_path = os.path.join(here, "cancel")

    def progress(phase, text, fraction):
        with open(progress_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"phase": phase, "text": text, "fraction": round(fraction, 3),
                                "at": time.time()}) + "\n")

    try:
        recipe = build.normalize(job.get("recipe") or {})
    except build.RecipeError as e:
        raise JobError(e.code, str(e))
    progress("prepare", "Reading the part", 0.01)
    try:
        shape, warnings = analysis_shape(load_target({"doc_path": job["doc_path"], "target": [job["target"]]}))
    except FeaError as e:
        raise JobError(e.code, str(e))
    if not shape.Solids or not shape.isClosed():
        raise JobError("source_not_closed", f"{job['target']} is not a closed solid, so it has no inside to "
                                            "fill with a lattice.")
    if len(shape.Solids) > 1:
        raise JobError("source_has_multiple_solids", "Lighten one solid at a time: this part has several.")
    tol = max(0.01, min(recipe.voxel_mm / 4, shape.BoundBox.DiagonalLength / 2000))
    vertices, triangles = tessellate(shape, tol)
    fea = job.get("fea")
    case = None
    material = {"name": "unspecified", "youngs_mpa": 70000.0, "poisson": 0.33, "yield_mpa": None,
                "density_kg_m3": 2700.0}
    if fea:
        material = dict(fea["material"])
        try:
            case = load_case(shape, fea, step=max(recipe.voxel_mm, 0.2) / 2, tol=tol)
        except FeaError as e:
            raise JobError(e.code, str(e))
    elif job.get("material"):
        material = dict(job["material"])
    report = pipeline.run(vertices, triangles, case, material, recipe, job["out_dir"],
                          name=job.get("name") or job["target"], progress=progress,
                          cancel=lambda: os.path.exists(cancel_path))
    report["source"] = {"name": job["target"], "volume_mm3": round(shape.Volume, 3),
                        "bbox": [round(v, 3) for v in (shape.BoundBox.XMin, shape.BoundBox.YMin, shape.BoundBox.ZMin,
                                                       shape.BoundBox.XMax, shape.BoundBox.YMax, shape.BoundBox.ZMax)]}
    report["warnings"] = [{"code": "source", "severity": "info", "text": w} for w in warnings] + report["warnings"]
    with open(os.path.join(job["out_dir"], "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=float)
    return report


def main():
    job_path = sys.argv[1]
    here = os.path.dirname(os.path.abspath(job_path))
    try:
        with open(job_path, encoding="utf-8") as f:
            job = json.load(f)
        out = {"ok": True, "report": run(job, here)}
    except JobError as e:
        out = {"ok": False, "code": e.code, "error": str(e)}
    except Exception as e:  # numerical or OCC failures: report them, never hang
        from cadswarm.lattice.pipeline import Cancelled
        from cadswarm.lattice.vfem import SolveError
        if isinstance(e, Cancelled) or (isinstance(e, SolveError) and e.code == "cancelled"):
            out = {"ok": False, "code": "cancelled", "error": "Cancelled."}
        elif isinstance(e, SolveError):
            out = {"ok": False, "code": e.code, "error": str(e)}
        elif isinstance(e, MemoryError):
            out = {"ok": False, "code": "out_of_memory", "error": "The job ran out of memory: use a larger voxel."}
        else:
            out = {"ok": False, "code": "lattice_error", "error": f"{type(e).__name__}: {e}",
                   "trace": traceback.format_exc(limit=5)}
    with open(os.path.join(here, "result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, default=float)


if __name__ == "__main__":
    main()
