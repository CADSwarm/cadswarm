"""FEA: honest placeholders, review rules, the report, and the hub's job/publish flow (solver faked)."""
import json
import os

import pytest

from cadswarm import fea, fea_jobs, helper
from cadswarm.client import HubRequestError
from cadswarm.prompt_hook import fea_hint
from cadswarm.space_hub import SpaceHub

STATIC_RESULT = {
    "ok": True, "warnings": [], "plot": "plot.png", "freecad": "1.1.1",
    "mesh": {"nodes": 7880, "elements": 4424, "size_mm": 3.0, "order": 2},
    "geometry": {"bbox": [0, 0, 0, 100, 20, 10], "volume_mm3": 20000.0, "faces": 6, "min_edge_mm": 10.0,
                 "mass_kg": 0.157},
    "timing": {"mesh_seconds": 0.6, "solve_seconds": 1.4},
    "resolved": {"fixed": ["Face1"], "loads": [{"type": "force", "faces": ["Face2"], "newtons": 1000,
                                                "direction": [0, 0, -1]}], "thermal": {}},
    "results": {"max_von_mises_mpa": 298.4, "von_mises_p95_mpa": 250.1, "max_von_mises_at": [0, 0, 10],
                "peak_near": "fixed_face", "max_displacement_mm": 0.949, "max_displacement_at": [100, 10, 0],
                "yield_mpa": 235, "safety_factor": 0.788, "safety_factor_p95": 0.94,
                "reactions": [{"set": "FIXED", "fx": 0.0, "fy": 0.0, "fz": 1000.0}], "convergence": None},
}


def job(**kw):
    base = {"target": "Beam", "material": "steel_s235", "fixed": ["xmin"],
            "loads": [{"type": "force", "faces": ["xmax"], "newtons": 1000, "direction": [0, 0, -1]}]}
    return fea.normalize_job({**base, **kw})


def texts(points):
    return " ".join(p["text"] for p in points)


# ------------------------------------------------------------------ inputs

def test_complete_inputs_have_no_placeholders():
    j = job()
    assert j["analysis"] == "static" and j["target"] == ["Beam"] and j["placeholders"] == []
    assert j["material"]["youngs_mpa"] == 210000 and j["mesh"] == {"max_size_mm": None, "second_order": True}


def test_missing_inputs_become_flagged_placeholders():
    j = fea.normalize_job({"target": "Beam"})
    fields = {p["field"] for p in j["placeholders"]}
    assert fields == {"material", "supports", "loads"}
    assert j["material"]["preset"] == "steel_s235" and j["fixed"] == ["zmin"]
    assert j["loads"][0]["newtons"] == 1000 and j["loads"][0]["faces"] == ["zmax"]
    assert any(p.get("severity") == "critical" for p in j["placeholders"])


def test_partial_custom_material_fills_and_flags_the_gaps():
    j = job(material={"name": "Mystery alloy", "youngs_mpa": 70000})
    assert j["material"]["name"] == "Mystery alloy" and j["material"]["youngs_mpa"] == 70000
    assert any("yield_mpa" in p["text"] for p in j["placeholders"])


def test_thermal_and_modal_defaults():
    t = fea.normalize_job({"target": "Fin", "analysis": "thermal", "material": "aluminium_6061_t6"})
    assert t["thermal"]["temperatures"][0]["celsius"] == 80 and t["thermal"]["convection"]
    assert t["fixed"] == []  # a heat analysis needs no supports
    m = fea.normalize_job({"target": "Arm", "analysis": "frequency", "modes": 50, "fixed": "zmin"})
    assert m["analysis"] == "modal" and m["modes"] == 20 and m["fixed"] == ["zmin"]


@pytest.mark.parametrize("bad", [
    {"target": ""}, {"target": "A", "analysis": "cfd"}, {"target": "A", "material": "unobtainium"},
    {"target": "A", "material": {"poisson": 0.3}}, {"target": "A", "loads": [{"type": "torque"}]},
    {"target": "A", "loads": [{"type": "force", "faces": ["xmax"], "newtons": "lots"}]},
    {"target": "A", "loads": [{"type": "force", "faces": [], "newtons": 1}]},
    {"target": "A", "mesh": {"max_size_mm": -1}},
])
def test_bad_inputs_are_refused_with_a_reason(bad):
    with pytest.raises(fea.FeaInputError):
        fea.normalize_job(bad)


# ------------------------------------------------------------------- rules

def test_rules_for_a_yielding_beam():
    pts = fea.rules(job(), STATIC_RESULT)
    t = texts(pts)
    assert "fixed face" in t and "Only one load case" in t and "convergence" in t
    assert fea.verdict(pts) in ("check", "fail")


def test_rules_safety_factor_bands():
    def sf_points(sf, p95=None, near="interior"):
        r = json.loads(json.dumps(STATIC_RESULT))
        r["results"].update(safety_factor=sf, safety_factor_p95=p95 or sf, peak_near=near)
        return fea.rules(job(), r)
    assert any(p["severity"] == "critical" and "yield" in p["text"] for p in sf_points(0.5))
    assert any("thin" in p["text"] for p in sf_points(1.2))
    assert any("heavier" in p["text"] for p in sf_points(20))
    assert not any("safety factor" in p["text"].lower() for p in sf_points(3))
    # A peak above yield only at a support is a warning about the singularity, not a verdict of failure.
    local = sf_points(0.8, 1.6, "fixed_face")
    assert not any(p["severity"] == "critical" for p in local) and "small spot" in texts(local)


def test_rules_modal_and_thermal():
    m = fea.normalize_job({"target": "Arm", "analysis": "modal", "fixed": ["zmin"], "material": "pla",
                           "excitation_hz": 50})
    r = {"results": {"modes": [{"mode": 1, "hz": 0.01}, {"mode": 2, "hz": 12}, {"mode": 3, "hz": 55}],
                     "rigid_modes": 1}, "mesh": {"elements": 9000}}
    t = texts(fea.rules(m, r))
    assert "isn't fully held" in t and "low (12.0 Hz)" in t and "resonance" in t and "3D-printed" in t
    th = fea.normalize_job({"target": "Fin", "analysis": "thermal", "material": "pla"})
    t = texts(fea.rules(th, {"results": {"temperature_c": {"min": 20, "max": 75}}, "mesh": {"elements": 9000}}))
    assert "above the usual limit" in t


def test_reaction_mismatch_is_flagged():
    r = json.loads(json.dumps(STATIC_RESULT))
    r["results"]["reactions"][0]["fz"] = 800
    assert "more than 5% apart" in texts(fea.rules(job(), r))


# ------------------------------------------------------------------ report

def test_report_html_escapes_agent_text_and_flags_placeholders():
    j = fea.normalize_job({"target": "Beam", "label": "<b>beam</b>"})
    rep = fea.build_report("fea_1", j, STATIC_RESULT, "alice", 1, 2, {"Beam": 3}, 9,
                           "<script>alert(1)</script>", [{"text": "<img src=x onerror=1>", "severity": "warning"}],
                           False, 3)
    page = fea.render_html(rep)
    assert "<script>" not in page and "&lt;script&gt;" in page and "<img src=x" not in page
    assert "Uses placeholder values" in page and "&lt;b&gt;beam&lt;/b&gt;" in page
    assert rep["schema"] == fea.SCHEMA_ID and rep["verdict"] == "fail"
    assert {i["source"] for i in rep["improvements"]} == {"rules", "agent"}
    for key in ("model", "inputs", "mesh", "results", "solver", "improvements", "summary", "headline"):
        assert key in rep
    assert "safety factor 0.79" in rep["headline"]
    assert "data:image/png;base64," in fea.standalone_html(rep, b"\x89PNG")


def test_fea_hint_detects_simulation_requests():
    for yes in ("Will this bracket hold 10 kg?", "check the stress in the base", "natural frequency of the arm",
                "how hot will it get", "run an FEA"):
        assert fea_hint(yes)
    for no in ("make the lid 2 mm thicker", "list my tasks", ""):
        assert not fea_hint(no)
    assert not fea_hint(None)


# ----------------------------------------------------- jobs on the host helper

AGENT = {"id": 4, "name": "alice-claude", "kind": "agent"}
OTHER = {"id": 5, "name": "bob-codex", "kind": "agent"}
HUMAN = {"id": 3, "name": "carol", "kind": "human"}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "model.FCStd").write_bytes(b"PK fake model")
    clock = Clock()
    p = helper.Project(str(tmp_path), clock)
    calls = []

    def fake_run(python, job, model_path, progress=None):
        calls.append((python, job, open(model_path, "rb").read()))
        if job["label"] == "explode":
            return {"ok": False, "code": "solve_failed", "error": "CalculiX stopped"}, None
        if job["label"] == "slow-mesher":
            progress("Installing the Gmsh mesher…")
        return json.loads(json.dumps(STATIC_RESULT)), b"\x89PNG fake"
    monkeypatch.setattr(fea_jobs, "freecad_python", lambda: "freecad-python")
    p._fea = fea_jobs.FeaJobs(p, clock, runner=fake_run)
    return p, tmp_path, clock, calls


def ask(p, op, actor=AGENT, **args):
    return helper.handle(p, {"id": "1", "op": op, "args": {**args, "_actor": actor}})


def finished(p, jid):
    import time
    for _ in range(200):
        r = ask(p, "fea_status", job_id=jid)["result"]
        if r["status"] not in ("queued", "running"):
            return r
        time.sleep(0.01)
    raise AssertionError("job never finished")


def kinds(result):
    return [e["kind"] for e in result.get("_events", [])]


def start(p, actor=AGENT, **kw):
    r = ask(p, "fea_run", actor, target="Beam", fixed=["xmin"], material="steel_s235",
            loads=[{"type": "force", "faces": ["xmax"], "newtons": 1000}], revs={"Beam": 3}, seq=9, **kw)
    assert r["ok"], r
    return r["result"]


def test_run_then_publish_writes_report_files_and_announces_it(env):
    p, root, _, calls = env
    r = start(p, label="shelf load")
    assert kinds(r) == ["fea_started"] and r["_events"][0]["data"]["target"] == ["Beam"]
    done = finished(p, r["job_id"])
    assert done["status"] == "done" and done["results"]["max_von_mises_mpa"] == 298.4
    assert calls[0][0] == "freecad-python" and calls[0][2] == b"PK fake model"  # solved on a copy of the model
    assert done["automatic_points"] and "publish_fea_report" in done["next_step"]
    pub = ask(p, "fea_publish", job_id=r["job_id"], summary="It bends but holds.",
              improvements=["Add a fillet at the root", {"text": "Model the bolts", "severity": "warning"}])
    assert pub["ok"], pub
    ev = pub["result"]["_events"]
    assert [e["kind"] for e in ev] == ["fea_report"] and ev[0]["data"]["verdict"] in ("check", "fail")
    folder = pub["result"]["folder"].removeprefix("files/")
    for name in ("report.json", "report.html", "plot.png"):
        assert (root / "files" / folder / name).exists()
    rep = json.loads(ask(p, "read_file", path=f"{folder}/report.json")["result"]["text"])
    assert rep["summary"] == "It bends but holds." and rep["by"] == "alice-claude" and not rep["auto_published"]
    assert rep["model"]["revs"] == {"Beam": 3} and rep["model"]["seq"] == 9
    assert sum(i["source"] == "agent" for i in rep["improvements"]) == 2
    assert ask(p, "fea_reports")["result"]["reports"][0]["folder"] == folder
    assert finished(p, r["job_id"])["report_folder"] == pub["result"]["folder"]
    assert not list((root / ".cadswarm-team" / "fea").glob("*.FCStd"))  # the job's model copy is cleaned up


def test_publish_rules(env):
    p, _, _, _ = env
    jid = start(p, label="same")["job_id"]
    finished(p, jid)
    assert ask(p, "fea_publish", OTHER, job_id=jid, summary="mine now")["code"] == "not_owner"
    first = ask(p, "fea_publish", HUMAN, job_id=jid, summary="a person may publish")["result"]
    assert ask(p, "fea_publish", job_id=jid, summary="again")["code"] == "already_published"
    jid2 = start(p, label="same")["job_id"]
    finished(p, jid2)
    second = ask(p, "fea_publish", job_id=jid2, summary="ok")["result"]
    assert first["folder"] != second["folder"]  # same label and minute: a new folder, nothing overwritten


def test_forgotten_result_is_auto_published_on_the_next_fea_op(env):
    p, _, clock, _ = env
    jid = start(p)["job_id"]
    assert finished(p, jid)["status"] == "done"
    clock.t += fea.PUBLISH_GRACE + 1
    r = ask(p, "fea_status", job_id=jid)["result"]
    assert "fea_report" in kinds(r) and r["_events"][-1]["data"]["auto"] is True
    rep = ask(p, "fea_reports")["result"]["reports"][0]
    text = json.loads(ask(p, "read_file", path=f"{rep['folder']}/report.json")["result"]["text"])
    assert text["auto_published"] and "didn't add a summary" in text["summary"]


def test_failed_run_is_announced_without_a_report(env):
    p, _, _, _ = env
    jid = start(p, label="explode")["job_id"]
    r = finished(p, jid)
    assert r["status"] == "failed" and "CalculiX" in r["error"] and "fea_failed" in kinds(r)
    assert ask(p, "fea_publish", job_id=jid, summary="x")["code"] == "not_ready"


def test_mesher_install_progress_is_announced(env):
    p, _, _, _ = env
    jid = start(p, label="slow-mesher")["job_id"]
    seen = []
    import time
    for _ in range(200):
        r = ask(p, "fea_status", job_id=jid)["result"]
        seen += kinds(r)
        if r["status"] == "done":
            break
        time.sleep(0.01)
    assert "fea_progress" in seen


def test_bad_jobs_are_refused_before_anything_runs(env, tmp_path):
    p, root, _, calls = env
    assert ask(p, "fea_run", target="Beam", analysis="cfd")["code"] == "bad_args"
    (root / "model.FCStd").unlink()
    assert ask(p, "fea_run", target="Beam")["code"] == "not_found"
    assert ask(p, "fea_status", job_id="fea_nope")["code"] == "not_found" and not calls


def test_host_restart_fails_interrupted_jobs(env):
    p, root, clock, _ = env
    p.db.execute("INSERT INTO fea_jobs (id, owner, owner_name, status, job, created_at) "
                 "VALUES ('fea_x', 4, 'alice-claude', 'running', '{}', 0)")
    fea_jobs.FeaJobs(p, clock)
    assert p.db.execute("SELECT status FROM fea_jobs WHERE id='fea_x'").fetchone()["status"] == "failed"


# ------------------------------------------------------------- SpaceHub routes

def test_space_hub_fea_routes_check_targets_and_poll_from_the_client(monkeypatch):
    hub = SpaceHub({"uri": "http://x", "db": "d"}, {"participant_id": 1, "token": "t", "fence": 2}, space=object())
    monkeypatch.setattr(hub, "_rows", lambda view, where="": [{"name": "Beam", "rev": 4}] if view == "v_object" else [])
    monkeypatch.setattr(hub, "latest_seq", lambda: 17)
    monkeypatch.setattr("cadswarm.space_hub.time.sleep", lambda s: None)
    sent, states = [], iter(["running", "done"])

    def run_op(kind, args, expected_revs=None):
        sent.append((kind, args))
        if kind == "fea_run":
            return {"job_id": "fea_1", "status": "queued", "_events": [{"kind": "fea_started", "data": {}}]}
        return {"job_id": "fea_1", "status": next(states)} if kind == "fea_status" else {"ok": True}
    monkeypatch.setattr(hub, "run_op", run_op)
    with pytest.raises(HubRequestError) as e:
        hub.post("/fea/run", {"target": "Ghost"})
    assert e.value.code == "bad_args" and not sent
    r = hub.post("/fea/run", {"target": "Beam", "wait": 30})
    assert r == {"job_id": "fea_1", "status": "done"}  # events are for the module, not the agent
    assert sent[0] == ("fea_run", {"target": "Beam", "revs": {"Beam": 4}, "seq": 17})
    assert [k for k, _ in sent] == ["fea_run", "fea_status", "fea_status"]
    hub.post("/fea/jobs/fea_1/publish", {"summary": "s", "improvements": ["i"]})
    assert sent[-1] == ("fea_publish", {"job_id": "fea_1", "summary": "s", "improvements": ["i"]})
    hub.post("/fea/faces", {"names": "Beam", "select": ["xmin"]})
    assert sent[-1] == ("faces", {"names": ["Beam"], "select": ["xmin"]})


# ------------------------------------------------------- mesher install

def fake_wheel(tmp_path, version=None):
    """A stand-in Gmsh wheel: a gmsh.py whose 'initialize' prints the version, plus a 'library'."""
    import io
    import zipfile
    from cadswarm import mesher
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("gmsh.py", f"def initialize(argv, run=False):\n    print('{version or mesher.GMSH_VERSION}')\n"
                              "def finalize():\n    pass\n")
        z.writestr(f"gmsh-{mesher.GMSH_VERSION}.data/data/lib/libgmsh.so", b"\x7fELF")
        z.writestr(f"gmsh-{mesher.GMSH_VERSION}.data/data/lib/../../../../evil.txt", b"zip slip")
        z.writestr("gmsh-x.data/scripts/gmsh", b"#!/usr/bin/env python")
    return buf.getvalue()


@pytest.fixture
def gmsh_env(tmp_path, monkeypatch):
    import hashlib
    import io
    import platform
    from cadswarm import mesher
    monkeypatch.setenv("APPDATA", str(tmp_path / "app"))
    monkeypatch.delenv("CADSWARM_NO_AUTO_INSTALL", raising=False)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    data = fake_wheel(tmp_path)
    url = mesher.WHEELS[("Linux", "x86_64")][0]
    monkeypatch.setitem(mesher.WHEELS, ("Linux", "x86_64"), (url, hashlib.sha256(data).hexdigest(), len(data)))
    fetched = []

    def opener(u, timeout=None):
        fetched.append(u)
        return io.BytesIO(data)
    return mesher, opener, fetched, tmp_path


def test_mesher_installs_once_into_cadswarms_own_folder(gmsh_env):
    mesher, opener, fetched, tmp_path = gmsh_env
    notes = []
    cmd = mesher.install(progress=notes.append, opener=opener)
    d = mesher.install_dir()
    assert cmd[1] == os.path.join(d, "gmsh_launcher.py") and d.startswith(str(tmp_path / "app"))
    assert set(os.listdir(d)) - {"__pycache__"} == {"gmsh.py", "gmsh_launcher.py", "installed.json", "lib"}
    assert os.listdir(os.path.join(d, "lib")) == ["libgmsh.so"]  # only libraries, flattened: no zip slip
    assert not list(tmp_path.rglob("evil.txt")) and "Installing" in notes[0]
    assert mesher.install(opener=opener) == cmd and len(fetched) == 1 and mesher.command() == cmd


def test_mesher_refuses_a_tampered_download(gmsh_env, monkeypatch):
    mesher, opener, _, _ = gmsh_env
    url, _sha, size = mesher.WHEELS[("Linux", "x86_64")]
    monkeypatch.setitem(mesher.WHEELS, ("Linux", "x86_64"), (url, "0" * 64, size))
    with pytest.raises(mesher.MesherError, match="checksum"):
        mesher.install(opener=opener)
    assert mesher.command() is None and not os.path.exists(mesher.install_dir())


def test_mesher_install_explains_when_it_cannot(gmsh_env, monkeypatch):
    import platform
    mesher, opener, fetched, _ = gmsh_env
    monkeypatch.setenv("CADSWARM_NO_AUTO_INSTALL", "1")
    with pytest.raises(mesher.MesherError, match="turned off"):
        mesher.install(opener=opener)
    monkeypatch.delenv("CADSWARM_NO_AUTO_INSTALL")
    monkeypatch.setattr(platform, "machine", lambda: "riscv64")
    with pytest.raises(mesher.MesherError, match="gmsh.info"):
        mesher.install(opener=opener)

    def offline(u, timeout=None):
        raise OSError("network unreachable")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    with pytest.raises(mesher.MesherError, match="internet"):
        mesher.install(opener=offline)
    assert not fetched


def test_run_job_installs_a_missing_mesher_and_retries(monkeypatch):
    from cadswarm import mesher
    runs, notes = [], []

    def fake_run(python, job, brep, timeout, gmsh_cmd):
        runs.append(gmsh_cmd)
        if not gmsh_cmd:
            return {"ok": False, "code": "missing_mesher", "error": "no gmsh"}, None
        return {"ok": True}, None
    monkeypatch.setattr(fea, "_run_job", fake_run)
    monkeypatch.setattr(mesher, "command", lambda python=None: None)
    monkeypatch.setattr(mesher, "install", lambda progress=None: progress("Installing…") or ["py", "launcher"])
    assert fea.run_job("py", {}, "", progress=notes.append)[0]["ok"]
    assert runs == [None, ["py", "launcher"]] and notes == ["Installing…"]

    def broken(progress=None):
        raise mesher.MesherError("no internet")
    monkeypatch.setattr(mesher, "install", broken)
    r, _ = fea.run_job("py", {}, "")
    assert r["code"] == "missing_mesher" and r["error"] == "no internet"
