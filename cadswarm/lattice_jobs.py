"""Lattice jobs on the host: the helper's side of lighten_part / lattice_status / lattice_cancel.

A lighten job takes minutes, so like FEA it can't be an op: `start` only queues it and returns. A background
thread runs it (one at a time) in a separate FreeCAD process (cadswarm/lattice/runner.py) on a COPY of the
saved model, and `status` reports on it. Nothing here touches the live document; adding the result to the
model is a separate, ordinary `insert_mesh` op that the hub submits (lattice_apply).

States follow the lattice contract: queued -> preparing -> generating -> packaging -> done | failed | cancelled.
Finished results are saved in the project files under lattice/<date> <name>/ (STL, 3MF, preview, report).

The load case is the FEA one: reuse a finished static FEA job (`fea_job_id`) or give `fixed` and `loads`
like run_fea. Without either, the lattice is uniform and unverified, and the report says so.

Stdlib only here; the numerical work happens in FreeCAD's Python.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid

from . import fea
from .files import safe_label

SCHEMA = """
CREATE TABLE IF NOT EXISTS lattice_jobs (
  id TEXT PRIMARY KEY, owner INTEGER, owner_name TEXT, status TEXT NOT NULL, target TEXT NOT NULL,
  name TEXT NOT NULL, request TEXT NOT NULL, fea TEXT, revs TEXT, progress TEXT, result TEXT, folder TEXT,
  cancel INTEGER DEFAULT 0, work TEXT, log TEXT, created_at REAL, started_at REAL, finished_at REAL);
"""
EVENT_KINDS = ("lattice_started", "lattice_progress", "lattice_report", "lattice_failed")
TIMEOUT = int(os.environ.get("CADSWARM_LATTICE_TIMEOUT", "2400"))
CANCEL_GRACE = 20
LOG_LINES = 400   # enough for a long verify; status sends the newest 120 of them
CELLS = ("gyroid", "diamond", "schwarzP", "iwp", "bcc", "fcc", "octet", "cubic")
RECIPE_KEYS = ("cell", "cell_size_mm", "min_thickness_mm", "max_thickness_mm", "skin_mm", "open_faces", "voxel_mm",
               "analysis_voxel_mm", "grading", "thickness_mm", "stiffness_target", "min_safety_factor",
               "auto_skin", "keep_radius_mm", "preserve")
TERMINAL = ("done", "failed", "cancelled", "expired")


def _log_entries(row, limit=120):
    """The rows' progress lines, oldest first. Bad JSON (an older build) is skipped, never fatal."""
    out = []
    for line in (row["log"] or "").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out[-limit:]


def _out_files(out_dir):
    """The result files that survived a failed publish, as 'name (N MB)' strings."""
    out = []
    for name in ("result.stl", "result.3mf", "preview.stl", "report.md", "report.json"):
        path = os.path.join(out_dir, name)
        if os.path.exists(path):
            out.append(f"{name} ({os.path.getsize(path) / 1e6:.0f} MB)")
    return out


class LatticeJobError(Exception):
    def __init__(self, code, message, **data):
        super().__init__(message)
        self.code, self.data = code, data


def runner_script():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "lattice", "runner.py")


def freecad_python():
    from .config import find_freecad_python
    try:
        return find_freecad_python()
    except RuntimeError as e:
        raise LatticeJobError("lattice_unavailable", f"The team server can't find FreeCAD's Python to run the "
                                                     f"lattice job: {e}")


def _recipe(args):
    r = {k: args[k] for k in RECIPE_KEYS if args.get(k) is not None}
    if "stiffness_target_pct" in args and args["stiffness_target_pct"] is not None:
        r["stiffness_target"] = float(args["stiffness_target_pct"]) / 100
    if r.get("cell", "gyroid") not in CELLS:
        raise LatticeJobError("bad_cell", f"cell must be one of {', '.join(CELLS)}")
    try:  # full validation when NumPy is here; the runner validates again either way
        from .lattice import build
        return build.normalize(r).as_dict()
    except ImportError:
        return r
    except ValueError as e:
        raise LatticeJobError(getattr(e, "code", "bad_args"), str(e))


def cell_library(args):
    """Every cell's wall for a target stiffness (E*/E0) or relative density, lightest first."""
    try:
        from .lattice import calibration
    except ImportError:
        raise LatticeJobError("lattice_unavailable", "NumPy is not installed in the team server's Python.")
    kind = args.get("target") or "stiffness"
    if kind not in ("stiffness", "density"):
        raise LatticeJobError("bad_args", "target must be stiffness or density")
    value = float(args.get("value") or (0.1 if kind == "stiffness" else 0.3))
    rows = calibration.library(float(args.get("cell_size_mm") or 8), kind, value,
                               float(args.get("min_wall_mm") or 0.8), args.get("youngs_mpa"))
    return {"target": kind, "value": value, "cells": rows,
            "note": "From CadSwarm's simulated cell map (E*/E0 = C*rho^n). A starting point: lighten_part "
                    "verifies the real part under its own load."}

def estimate(args):
    """Validate a request without starting it: recipe, warnings and grid sizes for the part's box.

    The hub passes the target's bounding box as `_bbox` [xmin, ymin, zmin, xmax, ymax, zmax] (mm)."""
    recipe = _recipe(args)
    bbox = args.get("_bbox")
    out = {"recipe": recipe, "warnings": [], "graded": bool(args.get("fea_job_id") or args.get("fixed")
                                                             or args.get("loads"))}
    if not bbox or len(bbox) != 6:
        return out
    size = [float(bbox[i + 3]) - float(bbox[i]) for i in range(3)]
    try:
        from .lattice import build
        r = build.normalize(recipe)
        out["warnings"] = build.warnings(r, size)
        vox = 1.0
        for s in size:
            vox *= s / r.voxel_mm
        out["build_voxels"] = int(vox)
        out["build_voxel_mm"] = round(max(r.voxel_mm, (size[0] * size[1] * size[2] / 24_000_000) ** (1 / 3)), 3)
        out["verify_voxel_mm"] = round(r.min_thickness_mm / 2.5, 3)
        # About a minute per 10 M build voxels plus the simulation; a rough guide for people.
        out["minutes_estimate"] = round(1 + vox / 10e6 + (2 if out["graded"] else 0), 1)
    except (ImportError, ValueError):
        pass
    return out


class LatticeJobs:
    def __init__(self, project, clock=time.time, runner=None):
        self.p, self.clock = project, clock
        self.runner = runner or self._run_process   # tests swap in a fake
        # Its own connection: the job thread must never share a sqlite connection with the helper's ops.
        self.db = sqlite3.connect(project.layout["db"], check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.slot = threading.Semaphore(1)
        self.outbox = []
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(lattice_jobs)")}
        if "work" not in cols:  # a table made by an earlier build
            self.db.execute("ALTER TABLE lattice_jobs ADD COLUMN work TEXT")
        if "log" not in cols:  # a table made before the dialog showed progress lines
            self.db.execute("ALTER TABLE lattice_jobs ADD COLUMN log TEXT")
        self.db.execute("UPDATE lattice_jobs SET status='failed', result=? WHERE status NOT IN "
                        "('done','failed','cancelled','expired')",
                        (json.dumps({"ok": False, "code": "host_restarted",
                                     "error": "The team server restarted during the lattice job."}),))

    # ---------------------------------------------------------------- helpers

    def _row(self, jid):
        with self.lock:
            row = self.db.execute("SELECT * FROM lattice_jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise LatticeJobError("not_found", f"No lattice job {jid}")
        return row

    def _event(self, kind, data):
        with self.lock:
            self.outbox.append({"kind": kind, "data": data})

    def with_events(self, result):
        self._finish_pending()
        with self.lock:
            if self.outbox:
                result = dict(result, _events=self.outbox[:10])
                del self.outbox[:10]
        return result

    # ------------------------------------------------------------------ reads

    def capabilities(self):
        try:
            python = freecad_python()
        except LatticeJobError as e:
            return {"available": False, "reason": str(e)}
        return {"available": os.path.exists(runner_script()), "engine": "cadswarm-voxel/1", "python": python,
                "cells": list(CELLS), "families": {"tpms": list(CELLS[:4]), "beam": list(CELLS[4:])},
                "grading": ["stress", "uniform"], "outputs": ["stl", "3mf", "mesh"],
                "limits": {"build_voxels": 24_000_000, "verify_elements": 450_000, "timeout_s": TIMEOUT}}

    def cells(self, args):
        return cell_library(args)

    def estimate(self, args):
        return estimate(args)

    def status(self, jid):
        self._finish_pending()
        with self.lock:
            row = self._row(jid)
        out = {"job_id": jid, "status": row["status"], "target": row["target"], "name": row["name"],
               "by": row["owner_name"], "recipe": json.loads(row["request"])}
        log = _log_entries(row)
        ended = row["finished_at"] or self.clock()
        out.update(log=log, started_at=row["started_at"], finished_at=row["finished_at"],
                   updated_at=log[-1].get("at") if log else None,
                   elapsed_s=round(ended - row["started_at"], 1) if row["started_at"] else None)
        prog = json.loads(row["progress"] or "{}")
        if row["status"] not in TERMINAL:
            out.update(phase=prog.get("phase"), progress=prog.get("fraction"), note=prog.get("text"),
                       next_step=f"call lattice_status('{jid}', wait=60) to keep waiting")
            return out
        res = json.loads(row["result"] or "{}")
        if row["status"] != "done":
            out.update(code=res.get("code"), error=res.get("error"), out_dir=res.get("out_dir"),
                       out_files=res.get("files"))
            return out
        rep = res.get("report") or {}
        out.update({
            "verdict": rep.get("status"), "weight_saving_pct": (rep.get("mass") or {}).get("weight_saving_pct"),
            "mass": rep.get("mass"), "thickness_mm": rep.get("thickness_mm"), "verify": rep.get("verify"),
            "mesh": rep.get("mesh"), "warnings": [w["text"] for w in rep.get("warnings") or []],
            "notes": rep.get("notes"), "placeholders": (json.loads(row["fea"] or "{}") or {}).get("placeholders"),
            "folder": f"files/{row['folder']}", "files": {k: f"files/{row['folder']}/{v}"
                                                          for k, v in (rep.get("files") or {}).items()},
            "next_step": (f"lattice_apply('{jid}') adds the lattice to the model as a mesh next to "
                          f"{row['target']}; tell your person the weight saving and the verified stiffness first"
                          if rep.get("status") == "passed" else
                          "explain why no design met the target (see notes) and what to change"),
        })
        return out

    def list(self, limit=20):
        self._finish_pending()
        with self.lock:
            rows = self.db.execute("SELECT id FROM lattice_jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return {"jobs": [self.status(r["id"]) for r in rows]}

    def result_file(self, jid):
        """(project file path of result.stl, source revs at start) for lattice_apply."""
        self._finish_pending()
        row = self._row(jid)
        if row["status"] != "done":
            raise LatticeJobError("not_ready", f"{jid} is {row['status']}; only a finished job can be applied")
        return {"file": f"{row['folder']}/result.stl", "target": row["target"], "name": row["name"],
                "revs": json.loads(row["revs"] or "{}"),
                "verdict": (json.loads(row["result"] or "{}").get("report") or {}).get("status")}

    # ----------------------------------------------------------------- writes

    def start(self, args, actor):
        actor = actor or {}
        target = args.get("target") or args.get("source")
        if not target or not isinstance(target, str):
            raise LatticeJobError("bad_args", "target must name the part to lighten, e.g. 'Bracket'")
        name = str(args.get("name") or f"{target}_lattice")
        recipe = _recipe(args)
        fea_job = self._load_case(args, target)
        model = self.p.layout["model"]
        if not os.path.exists(model):
            raise LatticeJobError("not_found", "There is no saved model to lighten yet.")
        python = freecad_python()
        jid = "lj_" + uuid.uuid4().hex[:8]
        with self.lock:
            ahead = self.db.execute("SELECT COUNT(*) AS n FROM lattice_jobs WHERE status NOT IN "
                                    "('done','failed','cancelled','expired')").fetchone()["n"]
            self.db.execute("INSERT INTO lattice_jobs (id, owner, owner_name, status, target, name, request, fea, "
                            "revs, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (jid, actor.get("id"), actor.get("name") or "someone", "queued", target, name,
                             json.dumps(recipe), json.dumps(fea_job), json.dumps(args.get("revs") or {}),
                             self.clock()))
            self._event("lattice_started", {"name": actor.get("name"), "job_id": jid, "target": target,
                                            "cell": recipe.get("cell", "gyroid"), "graded": bool(fea_job)})
        work = os.path.join(self.p.layout["internal"], "lattice", jid)
        os.makedirs(work, exist_ok=True)
        snapshot = os.path.join(work, "model.FCStd")
        shutil.copy2(model, snapshot)   # the part as it was when asked, whatever happens next
        threading.Thread(target=self._work, args=(jid, python, snapshot, work), daemon=True).start()
        out = self.status(jid)
        if ahead:
            out["queue_position"] = ahead
        return out

    def _load_case(self, args, target):
        if args.get("fea_job_id"):
            try:
                row = self.p.fea._row(args["fea_job_id"])
            except Exception:
                raise LatticeJobError("not_found", f"No FEA job {args['fea_job_id']}")
            job = json.loads(row["job"])
            if job.get("analysis") != "static":
                raise LatticeJobError("bad_args", "Lightening needs a static (stress) load case, not "
                                                  f"{job.get('analysis')}.")
            if target not in job.get("target", []):
                raise LatticeJobError("bad_args", f"FEA job {args['fea_job_id']} analysed "
                                                  f"{', '.join(job['target'])}, not {target}.")
            return job
        if args.get("fixed") or args.get("loads"):
            try:
                return fea.normalize_job({"analysis": "static", "target": target, "material": args.get("material"),
                                          "fixed": args.get("fixed"), "loads": args.get("loads")})
            except fea.FeaInputError as e:
                raise LatticeJobError("bad_args", str(e))
        return None

    def cancel(self, jid, actor=None):
        actor = actor or {}
        with self.lock:
            row = self._row(jid)
            if actor and actor.get("id") != row["owner"] and actor.get("kind") != "human":
                raise LatticeJobError("not_owner", f"{jid} belongs to {row['owner_name']}")
            if row["status"] in TERMINAL:
                return self.status(jid)
            if row["status"] == "queued":
                self.db.execute("UPDATE lattice_jobs SET status='cancelled', finished_at=?, result=? WHERE id=?",
                                (self.clock(), json.dumps({"ok": False, "code": "cancelled", "error": "Cancelled."}),
                                 jid))
            else:
                self.db.execute("UPDATE lattice_jobs SET cancel=1 WHERE id=?", (jid,))
        return self.status(jid)

    # ----------------------------------------------------------------- worker

    def _set(self, jid, **cols):
        with self.lock:
            sets = ", ".join(f"{k}=?" for k in cols)
            self.db.execute(f"UPDATE lattice_jobs SET {sets} WHERE id=?", (*cols.values(), jid))

    def _work(self, jid, python, snapshot, work):
        with self.slot:
            row = self._row(jid)
            if row["status"] != "queued":
                return
            self._set(jid, status="preparing", started_at=self.clock())
            job = {"doc_path": snapshot, "target": row["target"], "name": row["name"],
                   "recipe": json.loads(row["request"]), "fea": json.loads(row["fea"] or "null"),
                   "out_dir": os.path.join(work, "out")}
            last_phase = [None]

            def on_progress(p):
                status = "packaging" if p.get("phase") in ("mesh", "done") else "generating"
                entry = {"at": p.get("at") or self.clock(), "phase": p.get("phase"),
                         "text": p.get("text") or "", "fraction": p.get("fraction")}
                with self.lock:
                    prev = self.db.execute("SELECT log FROM lattice_jobs WHERE id=?", (jid,)).fetchone()
                    lines = (prev["log"] or "").splitlines() if prev else []
                    lines.append(json.dumps(entry))
                    self._set(jid, status=status, progress=json.dumps(p), log="\n".join(lines[-LOG_LINES:]))
                if p.get("phase") != last_phase[0]:
                    last_phase[0] = p.get("phase")
                    self._event("lattice_progress", {"name": row["owner_name"], "job_id": jid,
                                                     "phase": p.get("phase"), "text": p.get("text")})

            def cancelled():
                return bool(self._row(jid)["cancel"])

            try:
                result = self.runner(python, job, work, on_progress, cancelled)
            except Exception as e:  # never leave a job running forever
                result = {"ok": False, "code": "lattice_error", "error": f"{type(e).__name__}: {e}"}
            if result.get("ok"):
                # The files are published by the helper's own thread (_finish_pending), never from here.
                self._set(jid, status="packaging", result=json.dumps(result), work=work)
                return
            status = "cancelled" if result.get("code") == "cancelled" else "failed"
            self._set(jid, status=status, result=json.dumps(result), finished_at=self.clock())
            if status == "failed":
                with self.lock:
                    self._event("lattice_failed", {"name": row["owner_name"], "job_id": jid, "target": row["target"],
                                                   "error": str(result.get("error", ""))[:300]})
            self._cleanup(work)

    def _cleanup(self, work):
        if work and not os.environ.get("CADSWARM_LATTICE_KEEP"):
            shutil.rmtree(work, ignore_errors=True)

    def _finish_pending(self):
        """Publish finished jobs into the project files (on the caller's thread: the helper's)."""
        with self.lock:
            rows = self.db.execute("SELECT * FROM lattice_jobs WHERE status='packaging' AND work IS NOT NULL"
                                   ).fetchall()
            for row in rows:
                result = json.loads(row["result"])
                rep = result["report"]
                out_dir = os.path.join(row["work"], "out")
                try:
                    folder = self._publish(row["id"], row, out_dir, rep)
                except Exception as e:  # a full disk or quota must not leave the job hanging
                    # Minutes of compute are in out_dir: keep it (and say where) instead of deleting it.
                    kept = _out_files(out_dir)
                    self._set(row["id"], status="failed", folder=None, work=row["work"],
                              finished_at=self.clock(),
                              result=json.dumps({"ok": False, "code": "publish_failed",
                                                 "out_dir": out_dir, "files": kept,
                                                 "error": f"The lattice was made but could not be saved: {e} "
                                                          f"Nothing was deleted: "
                                                          + (f"the files are still in {out_dir} "
                                                             f"({', '.join(kept)})." if kept else
                                                             f"the job folder is {out_dir}.")}))
                    continue
                self._set(row["id"], status="done", folder=folder, work=None, finished_at=self.clock())
                self._event("lattice_report", {
                    "name": row["owner_name"], "job_id": row["id"], "target": row["target"], "folder": folder,
                    "verdict": rep.get("status"), "weight_saving_pct": rep["mass"]["weight_saving_pct"],
                    "stiffness_retained": (rep.get("verify") or {}).get("stiffness_retained")})
                self._cleanup(row["work"])

    def _publish(self, jid, row, out_dir, report):
        files = self.p.files
        stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(self.clock()))
        base = f"lattice/{stamp} {safe_label(row['name'], 'lattice')}"
        folder, n = base, 2
        while os.path.exists(files.full(files.clean(folder, allow_folder=True))):
            folder, n = f"{base} {n}", n + 1
        author = row["owner_name"]
        for name in ("result.stl", "result.3mf", "preview.stl"):
            path = os.path.join(out_dir, name)
            if os.path.exists(path):
                with open(path, "rb") as f:
                    # Raw bytes: a 300 MB STL would need over a gigabyte of RAM as base64.
                    files.save(f"{folder}/{name}", author, data=f.read())
        report = dict(report, job_id=jid)
        files.save(f"{folder}/report.json", author, text=json.dumps(report, indent=1, default=float))
        md = os.path.join(out_dir, "report.md")
        if os.path.exists(md):
            with open(md, encoding="utf-8") as f:
                files.save(f"{folder}/report.md", author, text=f.read())
        return folder

    @staticmethod
    def _run_process(python, job, work, on_progress, cancelled, timeout=TIMEOUT):
        job_path = os.path.join(work, "job.json")
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job, f)
        env = dict(os.environ, CADSWARM_FREECAD_BIN=os.path.dirname(python), PYTHONIOENCODING="utf-8")
        log = open(os.path.join(work, "runner.log"), "wb")
        proc = subprocess.Popen([python, runner_script(), job_path], cwd=work, env=env, stdout=log,
                                stderr=subprocess.STDOUT)
        progress_path = os.path.join(work, "progress.jsonl")
        start, seen, cancel_at = time.time(), 0, None
        try:
            while proc.poll() is None:
                time.sleep(1.0)
                if os.path.exists(progress_path):
                    with open(progress_path, encoding="utf-8") as f:
                        lines = f.read().splitlines()
                    if len(lines) > seen:
                        seen = len(lines)
                        try:
                            on_progress(json.loads(lines[-1]))
                        except ValueError:
                            pass
                if cancel_at is None and cancelled():
                    open(os.path.join(work, "cancel"), "w").close()
                    cancel_at = time.time()
                if cancel_at is not None and time.time() - cancel_at > CANCEL_GRACE:
                    proc.kill()
                    proc.wait(10)
                    return {"ok": False, "code": "cancelled", "error": "Cancelled."}
                if time.time() - start > timeout:
                    proc.kill()
                    proc.wait(10)
                    return {"ok": False, "code": "timeout", "error": f"The lattice job took longer than "
                            f"{timeout // 60} minutes and was stopped. Try a larger voxel or a smaller part."}
        finally:
            log.close()
        try:
            with open(os.path.join(work, "result.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            tail = ""
            try:
                with open(os.path.join(work, "runner.log"), "rb") as f:
                    tail = f.read()[-600:].decode("utf-8", "replace")
            except OSError:
                pass
            return {"ok": False, "code": "lattice_crashed", "error": f"The FreeCAD lattice process exited (code "
                    f"{proc.returncode}) without a result. {tail}".strip()}
