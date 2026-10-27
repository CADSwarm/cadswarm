"""FEA jobs on the host: the helper's side of run_fea / fea_status / publish_fea_report.

The Rust host runs ops one at a time, so a minutes-long solve can't be an op. `fea_run` only queues a job
and returns; a background thread solves it (one at a time) in a separate FreeCAD process on a COPY of the
saved model, and `fea_status` reports on it. Nothing here touches the live document or its locks.

The helper can't write team events itself, so FEA op results carry `_events`
([{"kind": "fea_report", "data": {...}}]); the module's complete_op emits those (FEA kinds only) for every
member to see: that's what pops the report up in each teammate's FreeCAD. Finished jobs nobody published
are published automatically (rule-based points only) the next time any FEA op runs after the grace period;
the owner's MCP server polls its jobs, so that happens even if the AI helper forgets.

Stdlib only.
"""
import base64
import json
import os
import shutil
import threading
import time
import uuid

from . import fea
from .files import safe_label

SCHEMA = """
CREATE TABLE IF NOT EXISTS fea_jobs (
  id TEXT PRIMARY KEY, owner INTEGER, owner_name TEXT, status TEXT NOT NULL, job TEXT NOT NULL,
  result TEXT, plot BLOB, revs TEXT, seq INTEGER, folder TEXT, report TEXT, note TEXT,
  created_at REAL, finished_at REAL, published_at REAL);
"""
EVENT_KINDS = ("fea_started", "fea_progress", "fea_report", "fea_failed")


class FeaJobError(Exception):
    def __init__(self, code, message, **data):
        super().__init__(message)
        self.code, self.data = code, data


def freecad_python():
    """FreeCAD's python for the solver process (the helper itself may run in another Python)."""
    from .config import find_freecad_python
    try:
        return find_freecad_python()
    except RuntimeError as e:
        raise FeaJobError("no_freecad", f"The team server can't find FreeCAD's Python to run the analysis: {e}")


class FeaJobs:
    def __init__(self, project, clock=time.time, runner=None):
        self.p, self.clock = project, clock
        self.runner = runner or fea.run_job  # tests swap in a fake solver
        self.db = project.db
        self.lock = threading.RLock()
        self.slot = threading.Semaphore(1)  # one solve at a time; CalculiX already uses every core
        self.outbox = []
        self.db.executescript(SCHEMA)
        self.db.execute("UPDATE fea_jobs SET status='failed', result=? WHERE status IN ('queued','running')",
                        (json.dumps({"ok": False, "code": "host_restarted",
                                     "error": "The team server restarted during the analysis."}),))

    # ---------------------------------------------------------------- helpers

    def _row(self, jid):
        row = self.db.execute("SELECT * FROM fea_jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise FeaJobError("not_found", f"No FEA job {jid}")
        return row

    def _event(self, kind, data):
        self.outbox.append({"kind": kind, "data": data})

    def with_events(self, result):
        """Attach (and clear) the events waiting to be announced; complete_op emits them."""
        with self.lock:
            self._auto_publish_overdue()
            if self.outbox:
                result = dict(result, _events=self.outbox[:10])
                del self.outbox[:10]
        return result

    # ------------------------------------------------------------------- ops

    def start(self, args, actor):
        try:
            job = fea.normalize_job(args)
        except fea.FeaInputError as e:
            raise FeaJobError("bad_args", str(e))
        model = self.p.layout["model"]
        if not os.path.exists(model):
            raise FeaJobError("not_found", "There is no saved model to analyse yet.")
        python = freecad_python()
        jid = "fea_" + uuid.uuid4().hex[:8]
        with self.lock:
            ahead = self.db.execute("SELECT COUNT(*) AS n FROM fea_jobs WHERE status IN ('queued','running')"
                                    ).fetchone()["n"]
            self.db.execute("INSERT INTO fea_jobs (id, owner, owner_name, status, job, revs, seq, created_at) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (jid, actor.get("id"), actor.get("name") or "someone", "queued", json.dumps(job),
                             json.dumps(args.get("revs") or {}), int(args.get("seq") or 0), self.clock()))
            self._event("fea_started", {"name": actor.get("name"), "job_id": jid, "target": job["target"],
                                        "analysis": job["analysis"]})
        # Copy the model now: the job analyses the part as it was when asked, whatever happens next.
        snapshot = os.path.join(self.p.layout["internal"], "fea", f"{jid}.FCStd")
        os.makedirs(os.path.dirname(snapshot), exist_ok=True)
        shutil.copy2(model, snapshot)
        threading.Thread(target=self._solve, args=(jid, job, python, snapshot), daemon=True).start()
        out = self.status(jid)
        if ahead:
            out["queue_position"] = ahead
        return out

    def _solve(self, jid, job, python, snapshot):
        def progress(text):  # e.g. a one-time mesher install
            with self.lock:
                self.db.execute("UPDATE fea_jobs SET note=? WHERE id=?", (text, jid))
                row = self._row(jid)
                self._event("fea_progress", {"name": row["owner_name"], "job_id": jid, "text": text})
        with self.slot:
            with self.lock:
                self.db.execute("UPDATE fea_jobs SET status='running' WHERE id=?", (jid,))
            try:
                result, png = self.runner(python, job, snapshot, progress=progress)
            except Exception as e:  # never leave a job running forever
                result, png = {"ok": False, "code": "fea_error", "error": f"{type(e).__name__}: {e}"}, None
            finally:
                try:
                    os.remove(snapshot)
                except OSError:
                    pass
        with self.lock:
            ok = bool(result.get("ok"))
            self.db.execute("UPDATE fea_jobs SET status=?, result=?, plot=?, finished_at=?, note=NULL WHERE id=?",
                            ("done" if ok else "failed", json.dumps(result), png, self.clock(), jid))
            if not ok:
                row = self._row(jid)
                self._event("fea_failed", {"name": row["owner_name"], "job_id": jid, "target": job["target"],
                                           "error": str(result.get("error", ""))[:300]})

    def status(self, jid):
        with self.lock:
            row = self._row(jid)
        job = json.loads(row["job"])
        out = {"job_id": jid, "status": row["status"], "analysis": job["analysis"], "target": job["target"],
               "placeholders": job["placeholders"], "by": row["owner_name"]}
        if row["status"] in ("queued", "running"):
            out["next_step"] = f"call fea_status('{jid}') to keep waiting"
            if row["note"]:
                out["note"] = row["note"]
        elif row["status"] == "failed":
            r = json.loads(row["result"] or "{}")
            out.update(error=r.get("error"), code=r.get("code"))
        else:
            r = json.loads(row["result"])
            out.update(results=r.get("results"), mesh=r.get("mesh"), warnings=r.get("warnings"),
                       geometry=r.get("geometry"), resolved_faces=r.get("resolved"),
                       automatic_points=[i["text"] for i in fea.rules(job, r)],
                       headline=fea.headline({"analysis": job["analysis"], "results": r.get("results") or {}}))
            if row["status"] == "done":
                out["next_step"] = (f"publish_fea_report('{jid}', summary, improvements): explain the result in "
                                    "plain words and add 2-5 points of improvement of your own")
            else:
                out["report_folder"] = f"files/{row['folder']}"
        return out

    def publish(self, jid, summary="", improvements=None, actor=None, auto=False):
        actor = actor or {}
        with self.lock:
            row = self._row(jid)
            if row["status"] == "published":
                raise FeaJobError("already_published", f"{jid} is already published in files/{row['folder']}",
                                  folder=row["folder"])
            if row["status"] != "done":
                raise FeaJobError("not_ready", f"{jid} is {row['status']}; only a finished analysis can be published")
            if not auto and actor.get("id") != row["owner"] and actor.get("kind") != "human":
                raise FeaJobError("not_owner", f"{jid} belongs to {row['owner_name']}")
            points = []
            for p in improvements or []:
                p = {"text": p} if isinstance(p, str) else dict(p or {})
                if str(p.get("text") or "").strip():
                    sev = p.get("severity") if p.get("severity") in ("critical", "warning", "info") else "info"
                    points.append({"text": str(p["text"])[:1000], "severity": sev})
            job, result = json.loads(row["job"]), json.loads(row["result"])
            report = fea.build_report(jid, job, result, row["owner_name"], row["created_at"], row["finished_at"],
                                      json.loads(row["revs"] or "{}"), row["seq"], str(summary or "")[:4000],
                                      points[:10], auto, self.clock())
            files = self.p.files
            stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(self.clock()))
            base = f"fea/{stamp} {safe_label(job['label'] or '-'.join(job['target']), 'analysis')}"
            folder, n = base, 2
            while os.path.exists(files.full(files.clean(folder, allow_folder=True))):
                folder, n = f"{base} {n}", n + 1
            paths = {"json": f"{folder}/report.json", "html": f"{folder}/report.html"}
            if row["plot"]:
                paths["plot"] = f"{folder}/plot.png"
            report["files"] = paths
            author = row["owner_name"]
            if row["plot"]:
                files.save(paths["plot"], author, base64_data=base64.b64encode(row["plot"]).decode())
            files.save(paths["json"], author, text=json.dumps(report, indent=2))
            files.save(paths["html"], author, text=fea.render_html(report))
            self.db.execute("UPDATE fea_jobs SET status='published', folder=?, report=?, published_at=? WHERE id=?",
                            (folder, json.dumps(report), self.clock(), jid))
            self._event("fea_report", {"name": author, "report_id": jid, "folder": folder, "title": report["title"],
                                       "analysis": job["analysis"], "verdict": report["verdict"],
                                       "headline": report["headline"], "placeholders": len(job["placeholders"]),
                                       "auto": auto})
            threading.Thread(target=self.remember, args=(report,), daemon=True).start()
            return {"published": True, "folder": f"files/{folder}", "files": paths, "verdict": report["verdict"],
                    "headline": report["headline"], "improvements": len(report["improvements"])}

    def _auto_publish_overdue(self):
        rows = self.db.execute("SELECT id FROM fea_jobs WHERE status='done' AND finished_at < ?",
                               (self.clock() - fea.PUBLISH_GRACE,)).fetchall()
        for r in rows:
            try:
                self.publish(r["id"], auto=True)
            except Exception as e:  # a report that can't be written must not break every FEA op
                self.db.execute("UPDATE fea_jobs SET status='failed', result=? WHERE id=?",
                                (json.dumps({"ok": False, "code": "publish_failed", "error": str(e)}), r["id"]))

    def remember(self, report):
        """Keep a published report in the project's design memory, so later designs and helpers can
        recall what failed and why. Best effort: a read-only folder simply skips it."""
        try:
            from .knowledge import fea_doc
            project = os.path.basename(os.path.dirname(os.path.normpath(self.p.files.full(""))))
            self.p.knowledge.index_fea(fea_doc(report, project))
            return True
        except Exception:
            return False

    def reports(self, limit=100, ref=None):
        """Every published report, newest first; with `ref` (a report id or its folder), that one report as a
        digest an AI helper can act on (fea.digest)."""
        if ref:
            ref = str(ref).removeprefix("files/").rstrip("/")
            row = self.db.execute("SELECT report, folder FROM fea_jobs WHERE status='published' AND (id=? OR folder=?)",
                                  (ref, ref)).fetchone()
            if not row:
                raise FeaJobError("not_found", f"No published simulation report {ref}; fea_reports lists them")
            return fea.digest(json.loads(row["report"]), f"files/{row['folder']}")
        rows = self.db.execute("SELECT id, report, folder, published_at FROM fea_jobs WHERE status='published' "
                               "ORDER BY published_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            rep = json.loads(r["report"])
            out.append({"id": r["id"], "by": rep["by"], "folder": r["folder"], "title": rep["title"],
                        "analysis": rep["analysis"], "verdict": rep["verdict"], "headline": rep["headline"],
                        "published_at": r["published_at"], "placeholders": len(rep["inputs"]["placeholders"])})
        return {"reports": out}
