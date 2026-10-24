"""FEA: honest placeholders, review rules, the report, and the hub's job/publish flow (solver faked)."""
import json
import os

import pytest

from cadswarm.hub import fea
from cadswarm.hub.core import Hub, HubError
from cadswarm.paths import project_layout
from cadswarm.prompt_hook import fea_hint
from test_core import Clock, FakeWorker

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


# --------------------------------------------------------------- hub flow

class FeaWorker(FakeWorker):
    python = "freecad-python"

    def call(self, op, args=None, allowed=None):
        if op == "fea_shape":
            return {"result": {"brep": "BREP"}}
        if op == "faces":
            return {"result": {"faces": [{"face": "Face1"}], "selected": {"xmin": ["Face1"]}}}
        return super().call(op, args, allowed)


@pytest.fixture
def env(tmp_path, monkeypatch):
    lay = project_layout(tmp_path / "Bracket project")
    os.makedirs(lay["internal"])
    clock = Clock()
    hub = Hub(FeaWorker(), lay["db"], "secret", clock=clock, layout=lay)
    a = hub.join("secret", "alice-claude")["participant_id"]
    b = hub.join("secret", "bob-codex")["participant_id"]
    calls = []

    def fake_run(python, job, brep, timeout=None, progress=None):
        calls.append((python, job, brep))
        if job["label"] == "explode":
            return {"ok": False, "code": "solve_failed", "error": "CalculiX stopped"}, None
        return json.loads(json.dumps(STATIC_RESULT)), b"\x89PNG fake"
    monkeypatch.setattr(fea, "run_job", fake_run)
    return hub, lay, clock, a, b, calls


def start(hub, pid, **kw):
    return hub.fea_start(pid, {"target": "Beam", "fixed": ["xmin"], "material": "steel_s235",
                               "loads": [{"type": "force", "faces": ["xmax"], "newtons": 1000}], **kw}, wait=5)


def test_run_then_publish_writes_report_files_and_tells_everyone(env):
    hub, lay, _, a, b, calls = env
    r = start(hub, a, label="shelf load")
    assert r["status"] == "done" and r["results"]["max_von_mises_mpa"] == 298.4
    assert calls[0][0] == "freecad-python" and calls[0][2] == "BREP"
    assert r["automatic_points"] and "publish_fea_report" in r["next_step"]
    assert "publish_fea_report now" in hub.context(a)
    seq = hub.seq()
    p = hub.fea_publish(a, r["job_id"], "It bends but holds.", ["Add a fillet at the root",
                                                                {"text": "Model the bolts", "severity": "warning"}])
    folder = p["folder"].removeprefix("files/")
    for name in ("report.json", "report.html", "plot.png"):
        assert os.path.exists(os.path.join(lay["files"], *folder.split("/"), name))
    assert hub.list_files("fea")["files"][0]["version"] == 1
    rep = json.loads(hub.read_file(f"{folder}/report.json")["text"])
    assert rep["summary"] == "It bends but holds." and rep["by"] == "alice-claude" and not rep["auto_published"]
    assert sum(i["source"] == "agent" for i in rep["improvements"]) == 2
    ev = [e for e in hub.events(seq)["events"] if e["kind"] == "fea_report"]
    assert ev and ev[0]["folder"] == folder and ev[0]["verdict"] == rep["verdict"]
    assert "published FEA report" in hub.context(b)
    assert hub.fea_reports()["reports"][0]["folder"] == folder
    assert hub.fea_job(r["job_id"])["report_folder"] == p["folder"]


def test_publish_rules(env):
    hub, _, _, a, b, _ = env
    r = start(hub, a, label="same")
    with pytest.raises(HubError) as e:
        hub.fea_publish(b, r["job_id"], "mine now")
    assert e.value.code == "not_owner"
    first = hub.fea_publish(a, r["job_id"], "ok")
    with pytest.raises(HubError) as e:
        hub.fea_publish(a, r["job_id"], "again")
    assert e.value.code == "already_published"
    second = hub.fea_publish(a, start(hub, a, label="same")["job_id"], "ok")
    assert first["folder"] != second["folder"]  # same label and minute: a new folder, nothing overwritten


def test_unpublished_result_is_auto_published_by_the_sweep(env):
    hub, _, clock, a, _, _ = env
    r = start(hub, a)
    hub.sweep()
    assert hub.fea_job(r["job_id"])["status"] == "done"  # still within the grace period
    clock.t += fea.PUBLISH_GRACE + 1
    hub.heartbeat(a)
    hub.sweep()
    reps = hub.fea_reports()["reports"]
    assert len(reps) == 1 and reps[0]["id"] == r["job_id"]
    rep = json.loads(hub.read_file(f"{reps[0]['folder']}/report.json")["text"])
    assert rep["auto_published"] and "didn't add a summary" in rep["summary"]


def test_failed_run_is_reported_without_a_popup(env):
    hub, _, _, a, _, _ = env
    seq = hub.seq()
    r = start(hub, a, label="explode")
    assert r["status"] == "failed" and "CalculiX" in r["error"]
    kinds = [e["kind"] for e in hub.events(seq)["events"]]
    assert "fea_failed" in kinds and "fea_report" not in kinds
    assert any("failed" in m["body"] for m in hub.inbox(a))
    with pytest.raises(HubError):
        hub.fea_publish(a, r["job_id"], "x")


def test_bad_job_is_refused_before_queueing(env):
    hub, _, _, a, _, calls = env
    with pytest.raises(HubError) as e:
        hub.fea_start(a, {"target": "Beam", "analysis": "cfd"})
    assert e.value.status == 400 and not calls
    assert hub.fea_faces("Beam", ["xmin"])["selected"] == {"xmin": ["Face1"]}


def test_hub_restart_fails_interrupted_jobs(env, tmp_path):
    hub, lay, _, a, _, _ = env
    hub.x("INSERT INTO fea_jobs (id, pid, status, job, created_at) VALUES ('fea_x', ?, 'running', '{}', 0)", a)
    hub2 = Hub(FeaWorker(), lay["db"], "secret", clock=Clock(), layout=lay)
    assert hub2.one("SELECT status FROM fea_jobs WHERE id='fea_x'")["status"] == "failed"


# ------------------------------------------------------- mesher install

def fake_wheel(tmp_path, version=None):
    """A stand-in Gmsh wheel: a gmsh.py whose 'initialize' prints the version, plus a 'library'."""
    import io
    import zipfile
    from cadswarm.hub import mesher
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
    from cadswarm.hub import mesher
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
    from cadswarm.hub import mesher
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
