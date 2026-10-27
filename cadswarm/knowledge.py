"""CADSwarm's own design memory: board knowledge, finished designs, simulations and lessons.

Everything lives in a SQLite file inside the project folder (`internal/knowledge.db`), so a team's
knowledge works offline, on the host alone, with no account or keys. Boards come from the local
library (`boards/boards.json`) plus anything `python -m cadswarm.ingest` fetched into it.

Stdlib only.
"""
import json
import os
import re
import sqlite3
import threading
import time

BOARDS = "cadswarm-boards"
DESIGNS = "cadswarm-designs"
FEA = "cadswarm-fea"

# Findings that are about the reviewer, not the design: a design must not learn these as lessons.
NOT_DESIGN_PROBLEMS = ("the reviewer did not report a verdict", "the reviewer exited without a verdict")

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  id TEXT PRIMARY KEY,
  index_name TEXT NOT NULL,
  text TEXT NOT NULL,
  source TEXT NOT NULL,
  at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS docs_by_index ON docs(index_name);
"""


class KnowledgeError(Exception):
    """A lookup that cannot be answered (an unknown board, an unreadable library)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _words(text):
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _passage(text, terms, size=1500):
    """The window around the first matching term, so a hit shows the answer instead of the datasheet header."""
    low = text.lower()
    hits = [i for i in (low.find(t) for t in terms) if i >= 0]
    if not hits:
        return text[:size]
    start = max(0, min(hits) - size // 3)
    return text[start:start + size]


class Knowledge:
    """Board docs, finished designs and simulation reports, searched and ranked in-process."""

    def __init__(self, path, root=None, clock=time.time):
        self.path = str(path)
        self.root = str(root or os.path.dirname(os.path.abspath(self.path)))
        self.clock = clock
        self.lock = threading.RLock()
        self._db = None
        self._library = None

    @classmethod
    def for_project(cls, layout, clock=time.time):
        """The store in a project's `internal` folder, beside the host's own files."""
        return cls(os.path.join(layout["internal"], "knowledge.db"), root=layout["root"], clock=clock)

    # ------------------------------------------------------------------ store

    def db(self):
        if self._db is None:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            db = sqlite3.connect(self.path, check_same_thread=False, timeout=10, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            self._db = db
            self._seed_library(db)
        return self._db

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None

    def _put(self, index_name, doc_id, text, source, at=None):
        with self.lock:
            self.db().execute(
                "INSERT INTO docs (id, index_name, text, source, at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET index_name=excluded.index_name, text=excluded.text, "
                "source=excluded.source, at=excluded.at",
                (str(doc_id), index_name, str(text), json.dumps(source, default=str),
                 float(at if at is not None else self.clock())))

    def index_design(self, doc):
        source = {k: v for k, v in doc.items() if k not in ("text", "content")}
        self._put(DESIGNS, doc["brief_id"], doc.get("text") or doc.get("content") or "", source,
                  doc.get("finished_at", 0) / 1000.0)

    def index_fea(self, doc):
        source = {k: v for k, v in doc.items() if k not in ("text", "content")}
        self._put(FEA, doc["report_id"], doc.get("text") or doc.get("content") or "", source,
                  doc.get("published_at", 0) / 1000.0)

    def index_board(self, doc):
        source = {k: v for k, v in doc.items() if k not in ("text", "content")}
        self._put(BOARDS, doc.get("doc_id") or doc.get("source_url") or doc.get("title"),
                  doc.get("text") or doc.get("content") or "", source)

    # -------------------------------------------------------------- searching

    def search(self, index, query, k=5, filters=None):
        """Docs matching `query`, best first. A filter keeps only docs whose field equals the value."""
        with self.lock:
            rows = self.db().execute(
                "SELECT * FROM docs WHERE index_name=? ORDER BY at DESC", (index,)).fetchall()
        terms = _words(query)
        phrase = " ".join(terms)
        scored = []
        for row in rows:
            source = json.loads(row["source"] or "{}")
            if any(str(source.get(field, "")) != str(value) for field, value in (filters or {}).items()):
                continue
            text = row["text"]
            low = text.lower()
            score = float(len(terms) == 0)
            for term in terms:
                if term in low:
                    score += 1.0 + 0.2 * low.count(term)
            if phrase and phrase in low:
                score += 3.0
            scored.append((score, row["at"], {"score": round(score, 3), "source": source,
                                              "chunks": [_passage(text, terms)]}))
        scored.sort(key=lambda h: (h[0], h[1]), reverse=True)
        return [hit for _score, _at, hit in scored[:max(0, k)]]

    def lessons(self, board_id, k=5):
        """Recurring problems for a board, most frequent first, derived from the designs we finished."""
        with self.lock:
            rows = self.db().execute(
                "SELECT source FROM docs WHERE index_name=?", (DESIGNS,)).fetchall()
        counts = {}
        for row in rows:
            source = json.loads(row["source"] or "{}")
            if board_id and source.get("board_id") != board_id:
                continue
            for problem in source.get("problems") or []:
                problem = str(problem).strip()
                if not problem or problem.lower() in NOT_DESIGN_PROBLEMS:
                    continue
                counts[problem] = counts.get(problem, 0) + 1
        out = []
        for problem, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:max(0, k)]:
            out.append({"board_id": board_id, "problem": problem, "count": count,
                        "lesson": f"Seen {count} time(s) on {board_id or 'this team'}: {problem}. "
                                  f"Design to avoid it."})
        return out

    # ------------------------------------------------------------ board specs

    def library(self):
        """The local board library: {board_id: spec}, plus its datasheet links by board id."""
        if self._library is None:
            path = self._library_path()
            spec, sources = {}, {}
            if path:
                try:
                    with open(path, encoding="utf-8") as f:
                        spec = json.load(f)
                except (OSError, ValueError) as e:
                    raise KnowledgeError("library_unreadable", f"The board library {path} is unreadable: {e}")
                if not isinstance(spec, dict):
                    raise KnowledgeError("library_unreadable", f"The board library {path} is not a map")
                sources = self._read_sources(path)
            self._library = (spec, sources)
        return self._library

    def _library_path(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for path in (os.environ.get("CADSWARM_BOARDS"),
                     os.path.join(self.root, "boards.json"),
                     os.path.join(self.root, "boards", "boards.json"),
                     os.path.join(here, "boards", "boards.json")):
            if path and os.path.isfile(path):
                return path
        return None

    @staticmethod
    def _read_sources(board_path):
        path = os.path.join(os.path.dirname(board_path), "sources.json")
        try:
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
        except (OSError, ValueError):
            return {}
        out = {}
        for s in rows if isinstance(rows, list) else []:
            if isinstance(s, dict) and s.get("board_id") and s.get("url"):
                out.setdefault(s["board_id"], []).append(s["url"])
        return out

    def board_spec(self, board_id):
        """The measured spec for a library board, with the datasheet links we have for it."""
        spec, sources = self.library()
        raw = spec.get(board_id)
        if not isinstance(raw, dict):
            known = ", ".join(sorted(spec)[:8]) or "none"
            raise KnowledgeError("unknown_board", f"No local spec for {board_id} (known boards: {known}). "
                                                 f"Add it to boards/boards.json, or run cadswarm.ingest.")
        out = dict(raw)
        links = sources.get(board_id) or ([out["source_url"]] if out.get("source_url") else [])
        if links:
            out["sources"] = {k: out["sources"][k] for k in ("pcb", "holes", "ports")
                              if isinstance(out.get("sources"), dict) and out["sources"].get(k)}
            for key in ("pcb", "holes", "ports"):
                out["sources"].setdefault(key, links[0])
        else:
            out.pop("sources", None)
        out.setdefault("label", board_id)
        out.setdefault("ports", [])
        return out

    def _seed_library(self, db):
        """Every library board is also a searchable board doc, so lookups answer with no ingest run."""
        try:
            spec, sources = self.library()
        except KnowledgeError:
            return
        for board_id, raw in spec.items():
            if not isinstance(raw, dict) or db.execute(
                    "SELECT 1 FROM docs WHERE id=?", (f"lib:{board_id}",)).fetchone():
                continue
            links = sources.get(board_id) or ([raw["source_url"]] if raw.get("source_url") else [])
            pcb = raw.get("pcb") or [0, 0, 0]
            holes = raw.get("holes") or []
            parts = [f"{raw.get('label') or board_id} ({board_id}).",
                     f"PCB {pcb[0]} x {pcb[1]} x {pcb[2]} mm.",
                     f"Mounting holes: {' '.join(f'({x}, {y})' for x, y in holes)}"
                     f"{' at ' + str(raw.get('hole_d')) + ' mm' if holes else ' none'}.",
                     f"Clearance above {raw.get('clear_top')} mm, below {raw.get('clear_bottom')} mm."]
            ports = raw.get("ports") or []
            if ports:
                parts.append("Ports: " + "; ".join(
                    f"{p.get('name')} on {p.get('side')} at {p.get('center')} sized {p.get('size')}"
                    for p in ports if isinstance(p, dict)) + ".")
            if links:
                parts.append("Datasheet: " + links[0])
            text = " ".join(parts)
            db.execute("INSERT OR IGNORE INTO docs (id, index_name, text, source, at) VALUES (?,?,?,?,?)",
                       (f"lib:{board_id}", BOARDS, text,
                        json.dumps({"board_id": board_id, "title": raw.get("label") or board_id,
                                    "source_url": links[0] if links else "", "source_type": "library"}),
                        0.0))


# --------------------------------------------------------------------- documents


def review_findings(reviews):
    """Every finding across the review rounds, minus the reviewer's own failures.

    A review that was retried (a crashed reviewer, then a rerun) leaves two verdicts for the same
    round; only the newest one counts, or every finding would be learned twice.
    """
    by_round = {}
    for rv in sorted(reviews or [], key=lambda r: (r.get("round") or 0, r.get("at_ms") or 0)):
        try:
            by_round[rv.get("round") or 0] = json.loads(rv.get("findings_json") or "[]")
        except (TypeError, ValueError):
            pass
    findings = [f for round_no in sorted(by_round) for f in by_round[round_no] if isinstance(f, dict)]
    return [f for f in findings if str(f.get("problem") or "").strip().lower() not in NOT_DESIGN_PROBLEMS]


def design_problems(reviews):
    """The problems a finished design teaches: what was wrong with the model, in the team's own words."""
    return [str(f["problem"]) for f in review_findings(reviews) if f.get("problem")]


def design_doc(brief, plan, reviews, params):
    findings = review_findings(reviews)
    problems = [str(f["problem"]) for f in findings if f.get("problem")]
    parts = [p.get("name", "") for p in (plan or {}).get("parts", [])]
    text = (f"Request: {brief['text']}\nBoard: {brief.get('board') or 'unspecified'}\nParts: {', '.join(parts)}\n"
            f"Problems found in review: {'; '.join(problems) or 'none'}\n"
            f"Final params: {', '.join(f'{k}={v}' for k, v in sorted(params.items()))}")
    return {"brief_id": brief["id"], "board_id": brief.get("board") or "", "request": brief["text"],
            "outcome": brief.get("status", ""), "findings": json.dumps(findings), "problems": problems,
            "params": params, "finished_at": int(time.time() * 1000), "text": text, "content": text}


def fea_doc(report, project=""):
    """A published simulation report as design memory: what was checked, how it did, and what to change."""
    problems = [p["text"] for p in report.get("improvements") or [] if p.get("severity") in ("critical", "warning")]
    res, material = report.get("results") or {}, (report.get("inputs") or {}).get("material") or {}
    target = (report.get("model") or {}).get("target") or []
    text = (f"Simulation: {report.get('title')} ({report.get('analysis_name') or report.get('analysis')})\n"
            f"Parts: {', '.join(target)}\nMaterial: {material.get('name') or 'unspecified'}\n"
            f"Verdict: {report.get('verdict')}. {report.get('headline') or ''}\n"
            f"Summary: {report.get('summary') or ''}\nProblems: {'; '.join(problems) or 'none'}")
    return {"report_id": report["id"], "project": project, "title": report.get("title") or "", "target": target,
            "analysis": report.get("analysis"),
            "verdict": report.get("verdict"), "headline": report.get("headline") or "",
            "material": material.get("name") or "", "safety_factor": res.get("safety_factor"),
            "max_stress_mpa": res.get("max_von_mises_mpa"), "max_displacement_mm": res.get("max_displacement_mm"),
            "problems": problems, "by": report.get("by") or "",
            "published_at": int((report.get("published_at") or time.time()) * 1000), "text": text, "content": text}


def validate_spec(raw):
    """A fetched or typed spec: plausible dimensions and a source URL for every measured number."""
    src = raw.get("sources") or {}
    pcb = [float(x) for x in raw.get("pcb", [])]
    if len(pcb) != 3 or not all(0 < x < 400 for x in pcb[:2]) or not 0 < pcb[2] < 10:
        raise ValueError(f"pcb dimensions look wrong: {raw.get('pcb')}")
    if not str(src.get("pcb", "")).startswith("http"):
        raise ValueError("pcb has no source_url")
    holes = [[float(a), float(b)] for a, b in raw.get("holes", [])]
    for x, y in holes:
        if not (0 <= x <= pcb[0] and 0 <= y <= pcb[1]):
            raise ValueError(f"hole ({x}, {y}) is outside the {pcb[0]} x {pcb[1]} board")
    if holes and not str(src.get("holes", "")).startswith("http"):
        raise ValueError("holes have no source_url")
    hole_d = float(raw.get("hole_d") or 0)
    if holes and not 0.5 <= hole_d <= 8:
        raise ValueError(f"hole_d looks wrong for mounting holes: {raw.get('hole_d')}")
    ports = []
    for p in raw.get("ports", []):
        if not isinstance(p, dict) or p.get("side") not in ("-x", "+x", "-y", "+y"):
            raise ValueError(f"port {p.get('name') if isinstance(p, dict) else p} has no valid side")
        try:
            ports.append({"name": re.sub(r"\W+", "_", str(p["name"]).lower()), "side": p["side"],
                          "center": [float(v) for v in p["center"]], "size": [float(v) for v in p["size"]]})
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"port {p.get('name')} needs a name, center and size ({e})")
    return {"label": str(raw.get("label", "")), "pcb": pcb, "holes": holes, "hole_d": hole_d,
            "clear_top": float(raw.get("clear_top", 0)) or 10.0, "clear_bottom": float(raw.get("clear_bottom", 0)) or 3.0,
            "ports": ports, "sources": src, "verified": False}
