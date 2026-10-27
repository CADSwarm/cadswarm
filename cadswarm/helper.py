"""Host-side helper for ops that are not FreeCAD geometry: project files and storage, the project's
local board knowledge and design memory (lookup_board, board_spec, recall_designs, index_design), the team's
learned design style (observe_op, observe_brief, learn_style, style_context, teach_style, resolve_style;
see style_memory.py), and FEA jobs
(fea_run, fea_status, fea_publish, fea_reports; see fea_jobs.py), lighten-without-losing-stiffness lattice jobs
(lattice_*; see lattice_jobs.py), and the components library
(library_*, standard_parts, suggest_settings; see library/ops.py). The
Rust host drives it like the FreeCAD worker:

    python cadswarm/helper.py <project_dir>

First line out is {"ready": true}; then one request per line {"id", "op", "args"} and one reply per
line {"id", "ok": true, "result"} or {"id", "ok": false, "code", "error", ...details}. A bad request
never kills it. The host adds args["_actor"] = {"id", "name", "kind"} for authorship and people-only
actions. Op args follow the contract (plan 00): list_files {folder}, move_file {from_path, to_path}.
Knowledge lives in the project's own internal/knowledge.db, so it needs no account, keys or internet.

Stdlib only (with everything it imports): the host may run it with FreeCAD's Python.
"""
import base64
import json
import os
import shutil
import sqlite3
import sys
import time

if __package__ in (None, ""):  # run as a script by the host: make `cadswarm` importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cadswarm.files import ProjectFiles, safe_label, unique_path  # noqa: E402
from cadswarm.knowledge import Knowledge, KnowledgeError, design_doc  # noqa: E402
from cadswarm.paths import project_layout  # noqa: E402
from cadswarm.fea_jobs import FeaJobError  # noqa: E402
from cadswarm.lattice_jobs import LatticeJobError  # noqa: E402
from cadswarm.storage import FileError  # noqa: E402
from cadswarm.library.ops import HANDLERS as LIBRARY_HANDLERS, LibraryError  # noqa: E402
from cadswarm.style_memory import StyleError, StyleMemory  # noqa: E402


class HelperError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Project:
    def __init__(self, root, clock=time.time):
        self.layout = project_layout(root)
        os.makedirs(self.layout["internal"], exist_ok=True)
        for k in ("files", "exports", "snapshots"):
            os.makedirs(self.layout[k], exist_ok=True)
        self.db = sqlite3.connect(self.layout["db"], check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.files = ProjectFiles(self.layout, self.db, clock)
        self.clock = clock
        self._knowledge = None
        self._fea = None
        self._lattice = None
        self._style = None
        self._host_space = None
        self._host_space_checked = False

    @property
    def host_space(self):
        """The host's own team identity, from `<data_dir>/host.json`, or None.

        A helper started by the host runs inside the host's data folder; a checkout or a test does
        not. When there is no host.json, recall still answers and simply leaves no activity row."""
        if not self._host_space_checked:
            self._host_space_checked = True
            self._host_space = _host_hub(self.layout["root"])
        return self._host_space

    @host_space.setter
    def host_space(self, value):
        self._host_space = value
        self._host_space_checked = True

    @property
    def style(self):
        """What the team's way of working has taught CADSwarm (style_memory.py)."""
        if self._style is None:
            self._style = StyleMemory(self.db, self.clock)
        return self._style

    @property
    def fea(self):
        """FEA jobs, created on first use (tests set a fake runner)."""
        if self._fea is None:
            from cadswarm.fea_jobs import FeaJobs
            self._fea = FeaJobs(self, self.clock)
        return self._fea

    @property
    def lattice(self):
        """Lighten jobs (stress-graded lattices), created on first use (tests set a fake runner)."""
        if self._lattice is None:
            from cadswarm.lattice_jobs import LatticeJobs
            self._lattice = LatticeJobs(self, self.clock)
        return self._lattice

    @lattice.setter
    def lattice(self, value):
        self._lattice = value

    @property
    def knowledge(self):
        """The project's own design memory, opened on first use (tests set their own)."""
        if self._knowledge is None:
            self._knowledge = Knowledge.for_project(self.layout, self.clock)
        return self._knowledge

    @knowledge.setter
    def knowledge(self, value):
        self._knowledge = value


def _actor(a):
    return a.get("_actor") or {}


def _author(a):
    return _actor(a).get("name") or "someone"


def _people_only(a, what, where="the CADSwarm panel's Tools tab > Storage and free up space"):
    if _actor(a).get("kind") != "human":
        raise HelperError("humans_only", f"Only a person can {what}. Ask someone on the team to do it "
                                         f"(in FreeCAD: {where}).")


def _first(a, *keys):
    return next((a[k] for k in keys if a.get(k) is not None), None)


def _need(a, key):
    if not a.get(key):
        raise HelperError("bad_args", f"{key} is required")
    return a[key]


def list_files(p, a):
    return p.files.listing(_first(a, "folder", "prefix") or "")


def read_file(p, a):
    if a.get("raw"):
        clean, data = p.files.raw(a.get("path"))
        return {"path": clean, "base64": base64.b64encode(data).decode()}
    return p.files.read(a.get("path"))


def snapshot(p, a):
    model = p.layout["model"]
    if not os.path.exists(model):
        raise HelperError("not_found", "There is no model to snapshot yet.")
    p.files.storage.check_add(os.path.getsize(model))
    stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(p.clock()))
    dest = unique_path(p.layout["snapshots"], f"{stamp} {safe_label(a.get('label', ''), 'snapshot')}.FCStd")
    shutil.copy2(model, dest)  # the host runs one op at a time, and the worker saved before acknowledging
    rel = os.path.relpath(dest, p.layout["root"]).replace("\\", "/")
    return {"file": rel, "size": os.path.getsize(dest)}


def prune_storage(p, a):
    _people_only(a, "free up space")
    return p.files.storage.prune({k: v for k, v in a.items() if k != "_actor"})


def lookup_board(p, a):
    question = a.get("question") or _need(a, "board")  # a board alone asks about that board
    filters = {"board_id": a["board"]} if a.get("board") else None
    hits = p.knowledge.search("cadswarm-boards", question, 5, filters)
    return {"answers": [{"board_id": h["source"].get("board_id"), "title": h["source"].get("title"),
                         "source_url": h["source"].get("source_url"), "score": h["score"],
                         "text": " … ".join(h["chunks"][:2])[:1500]} for h in hits]}


def board_spec(p, a):
    board = _need(a, "board")
    try:
        return p.knowledge.board_spec(board)
    except KnowledgeError as e:  # the library has no entry (or is unreadable)
        raise HelperError("spec_unverified", str(e))


def _host_hub(root):
    """Connect as the host itself with the token it wrote to `host.json`. Best effort: None on any miss."""
    try:
        with open(os.path.join(root, "host.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict) or not (cfg.get("uri") and cfg.get("db") and cfg.get("token")):
        return None
    try:
        from cadswarm.space_hub import SpaceHub
        hub = SpaceHub(cfg, identity={"token": cfg["token"]}, timeout=5)  # the recall must not wait on a dead hub
        me = hub.me()
        hub.identity.update({"participant_id": me.get("id"), "fence": me.get("fence") or 0,
                             "name": me.get("name") or "host"})
        return hub
    except Exception:  # no hub, no member, a stale token: memory still answers the op
        return None


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _log_recall(p, query, out, actor=None):
    """The host's own recall creates no op, so the activity feed is the only place the team sees it.

    One `recall` row per returned design: its rank, its request, its own problem count and the
    lessons that came with the same call. A sum across designs would let three unrelated hits look
    like Act 1's three problems. A recall made by a helper (it carries `_actor`) is that helper's
    business, not the host's planning, so it leaves no row."""
    if actor:
        return
    hub = p.host_space
    if hub is None:
        return
    designs = [d for d in out.get("designs") or [] if isinstance(d, dict)]
    lessons = len(out.get("lessons") or [])
    query_text = str(query)[:60].replace('"', "'")
    for rank, design in enumerate(designs, 1):
        request = str(design.get("request") or "").strip().replace('"', "'")
        if not request:
            continue
        problems = len(design.get("problems") or [])
        summary = (f'recall "{query_text}" · #{rank} "{request[:80]}" · '
                   f'{_plural(problems, "problem")} · {_plural(lessons, "lesson")}')
        hub.log_activity("recall", summary, [])


def recall_designs(p, a):
    query = a.get("query") or _need(a, "board")
    board = a.get("board")
    designs = p.knowledge.search("cadswarm-designs", query, 3, {"board_id": board} if board else None)
    lessons = p.knowledge.lessons(board, 5) if board else []
    # Simulations the team published: what broke, bent or was over-built before.
    sims = [d["source"] for d in p.knowledge.search("cadswarm-fea", query, 3)]
    out = {"designs": [d["source"] for d in designs], "lessons": [l.get("lesson") for l in lessons],
           "simulations": [{k: s.get(k) for k in ("title", "target", "verdict", "headline", "problems", "material")}
                           for s in sims]}
    try:
        _log_recall(p, query, out, a.get("_actor"))
    except Exception:  # activity is evidence; it is never a reason for memory to fail
        pass
    return out


def index_design(p, a):
    brief = _need(a, "brief")
    p.knowledge.index_design(design_doc(brief, a.get("plan") or {}, a.get("reviews") or [], a.get("params") or {}))
    return {"indexed": brief["id"]}


def _learn(p):
    """Re-distil the rules now; profile is the team-wide style block the host gives every agent turn."""
    out = p.style.distil()
    out["profile"] = p.style.context("", "", 6)["text"]
    return out


def observe_op(p, a):
    out = p.style.observe_op(a)
    if p.style.due():
        learned = _learn(p)
        out.update(distilled=learned["rules"], profile=learned["profile"])
    return out


def observe_brief(p, a):
    out = p.style.observe_brief(a)
    learned = _learn(p)  # a finished brief is the natural moment to re-learn
    out.update(distilled=learned["rules"], profile=learned["profile"])
    return out


def style_context(p, a):
    return p.style.context(a.get("query") or a.get("topic") or "", a.get("board") or "", int(a.get("limit") or 8))


def teach_style(p, a):
    # A person's rule is the team's decision; an agent's is only a proposal until a person confirms it.
    proposed = _actor(a).get("kind") != "human"
    rule = p.style.teach(_need(a, "statement"), _author(a), a.get("scope") or "team", proposed)
    return {"rule": rule, "profile": p.style.context("", "", 6)["text"]}


def resolve_style(p, a):
    _people_only(a, "confirm or dismiss a design-style rule", "the CADSwarm panel > Learned style")
    decision = _need(a, "decision")
    if a.get("rule_ids"):
        rules = p.style.resolve_many(a["rule_ids"], decision, _author(a))
        return {"rules": rules, "profile": p.style.context("", "", 6)["text"]}
    rule = p.style.resolve(_need(a, "rule_id"), decision, _author(a))
    return {"rule": rule, "rules": [rule], "profile": p.style.context("", "", 6)["text"]}


def style_inbox(p, a):
    """What waits for a person, grouped into one decision per subject (people answer it in the panel)."""
    return p.style.inbox()


def list_style(p, a):
    statuses = tuple(s for s in (a.get("statuses") or ["confirmed", "active", "candidate"]))
    return {"rules": p.style.rules(statuses)}


HANDLERS = {
    "list_files": list_files,
    "read_file": read_file,
    "file_history": lambda p, a: p.files.history(a.get("path")),
    "save_file": lambda p, a: p.files.save(a.get("path"), _author(a), a.get("text"), a.get("base64"),
                                           a.get("if_exists") or "error", a.get("expected_version")),
    "make_folder": lambda p, a: p.files.make_folder(a.get("path")),
    "move_file": lambda p, a: p.files.move(_first(a, "from_path", "from"), _first(a, "to_path", "to"), _author(a)),
    "delete_file": lambda p, a: p.files.delete(a.get("path"), _author(a)),
    "restore_file": lambda p, a: p.files.restore(a.get("path"), a.get("version"), _author(a),
                                                 a.get("expected_version")),
    "snapshot": snapshot,
    "storage_status": lambda p, a: p.files.storage.status(),
    "prune_storage": prune_storage,
    "lookup_board": lookup_board,
    "board_spec": board_spec,
    "recall_designs": recall_designs,
    "index_design": index_design,
    "observe_op": observe_op,
    "observe_brief": observe_brief,
    "learn_style": lambda p, a: _learn(p),
    "style_context": style_context,
    "style_inbox": style_inbox,
    "list_style": list_style,
    "teach_style": teach_style,
    "resolve_style": resolve_style,
    # FEA: solves run in the background on a copy of the model; results carry `_events` for the team.
    # Lighten without losing stiffness: stress-graded lattices, verified (lattice_jobs.py).
    "lattice_capabilities": lambda p, a: p.lattice.capabilities(),
    "lattice_cells": lambda p, a: p.lattice.cells(a),
    "lattice_estimate": lambda p, a: p.lattice.estimate(a),
    "lattice_start": lambda p, a: p.lattice.with_events(p.lattice.start(a, _actor(a))),
    "lattice_status": lambda p, a: p.lattice.with_events(p.lattice.status(_need(a, "job_id"))),
    "lattice_list": lambda p, a: p.lattice.with_events(p.lattice.list(int(a.get("limit") or 20))),
    "lattice_cancel": lambda p, a: p.lattice.with_events(p.lattice.cancel(_need(a, "job_id"), _actor(a))),
    "lattice_result": lambda p, a: p.lattice.result_file(_need(a, "job_id")),
    "fea_run": lambda p, a: p.fea.with_events(p.fea.start(a, _actor(a))),
    "fea_status": lambda p, a: p.fea.with_events(p.fea.status(a.get("job_id"))),
    "fea_publish": lambda p, a: p.fea.with_events(p.fea.publish(a.get("job_id"), a.get("summary") or "",
                                                                a.get("improvements"), _actor(a))),
    "fea_reports": lambda p, a: p.fea.with_events(p.fea.reports(int(a.get("limit") or 100), a.get("report"))),
    **LIBRARY_HANDLERS,
}


def handle(project, req):
    rid, op, args = req.get("id"), req.get("op"), req.get("args")
    fn = HANDLERS.get(op)
    if fn is None:
        return {"id": rid, "ok": False, "code": "unknown_op", "error": f"The helper has no op {op!r}"}
    if not isinstance(args, dict):
        return {"id": rid, "ok": False, "code": "bad_args", "error": "args must be a JSON object"}
    try:
        return {"id": rid, "ok": True, "result": fn(project, args)}
    except FileError as e:
        return {**e.data, "id": rid, "ok": False, "code": e.code, "error": str(e)}
    except (HelperError, LibraryError, StyleError) as e:
        return {"id": rid, "ok": False, "code": e.code, "error": str(e)}
    except LatticeJobError as e:
        return {**e.data, "id": rid, "ok": False, "code": e.code, "error": str(e)}
    except FeaJobError as e:
        return {**e.data, "id": rid, "ok": False, "code": e.code, "error": str(e)}
    except KnowledgeError as e:
        return {"id": rid, "ok": False, "code": e.code, "error": str(e)}
    except Exception as e:  # report, never die: the host keeps one helper for the whole session
        return {"id": rid, "ok": False, "code": "op_error", "error": f"{type(e).__name__}: {e}"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    project = Project(argv[0] if argv else os.getcwd())
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError("not an object")
        except ValueError:
            print(json.dumps({"id": None, "ok": False, "code": "bad_request", "error": "not a JSON request"}),
                  flush=True)
            continue
        print(json.dumps(handle(project, req)), flush=True)


if __name__ == "__main__":
    main()
