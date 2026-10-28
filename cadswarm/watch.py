"""Watch a live model overnight and teach the team a rule when a new class of defect appears.

This is the app looking over the build while nobody is in front of it: every few minutes it audits
every part, renders the model, writes a timeline and a morning summary, and (only for real product
rules, never geometry) teaches a confirmed team rule once per class of defect. It never creates tasks,
messages helpers or touches the model: the reviewer and the helpers own the work.

    python -m cadswarm.watch --project <folder> [--every 10] [--cycles 0]

Everything lands in <project>/.cadswarm-team/watch.log and watch-summary.md.
"""
import argparse
import json
import os
import sys
import threading
import time

from cadswarm.power import keep_running_in_background


def _person(project):
    """This computer's human sign-in for the team, kept outside the project folder."""
    host = json.load(open(os.path.join(project, "host.json"), encoding="utf-8"))
    name = "".join(c if c.isalnum() or c in "_.-" else "_" for c in host["db"]) + ".json"
    path = os.path.join(os.environ.get("APPDATA", ""), "CADSwarm", "gui", name)
    with open(path, encoding="utf-8") as f:
        return host, json.load(f)


def _rules(hub):
    try:
        return [r["statement"] for r in hub.get("/style")["rules"]]
    except Exception:
        return []


def _teach(hub, statement, known, log):
    if any(statement.lower() in existing.lower() for existing in known):
        return False
    try:
        hub.post("/style/teach", {"statement": statement, "scope": "team"})
        known.append(statement)
        log(f"taught a rule: {statement}")
        return True
    except Exception as e:
        log(f"could not teach a rule: {e}")
        return False


def audit(hub, log):
    """One look at the model: findings, rules worth teaching, and a render every so often."""
    model = hub.get("/model")
    params = model.get("params") or {}
    objects = model.get("objects") or []
    stats = hub.post("/surface", {"names": []}).get("objects", [])
    by_name = {s["name"]: s for s in stats}

    findings = []
    for o in objects:
        box = o.get("bbox") or []
        if len(box) != 6:
            continue
        x0, y0, z0, x1, y1, z1 = (float(v) for v in box)
        car_l = float(params.get("car_l") or 0)
        half = max(float(params.get("car_w") or 0), float(params.get("mirror_span") or 0)) / 2.0
        car_h = float(params.get("car_h") or 0)
        if car_l and (x0 < -20 or x1 > car_l + 20 or abs(y0) > half + 20 or abs(y1) > half + 20
                      or z0 < -20 or z1 > car_h + 20):
            findings.append(("outside", o["name"],
                             f"{o['name']} sits outside the car envelope (bbox {[round(v) for v in box]})"))
    for s in stats:
        if s.get("valid") is False:
            findings.append(("invalid", s["name"], f"{s['name']} is not a valid solid"))
        if s["faces"] and s["planar"] / s["faces"] > 0.6 and s["name"] in (
                "BodyShell", "Roof", "Pillars", "TrunkLid", "FrontBumper"):
            findings.append(("slab", s["name"],
                             f"{s['name']} is {s['planar']}/{s['faces']} flat faces ({s['planar']/s['faces']:.0%})"))
        if s["name"] == "Pillars" and 0 < s.get("volume_mm3", 0) < 100_000:
            findings.append(("collapsed", s["name"], f"Pillars collapsed to {s['volume_mm3']:.0f} mm3"))

    known = _rules(hub)
    classes = {kind for kind, _, _ in findings}
    if "invalid" in classes:
        _teach(hub, "A part with open edges or an invalid solid is not finished, whatever a helper reported.",
               known, log)
    if "outside" in classes:
        _teach(hub, "Every part must sit inside the car envelope (X 0..car_l, |Y| <= car_w/2, Z 0..car_h); "
                    "assemblies sit at their Params.", known, log)
    if "slab" in classes or "collapsed" in classes:
        _teach(hub, "The body shell and greenhouse keep continuous lofted surfaces with one unbroken shoulder "
                    "line; no flat-sided slab and no separate box roof or pillars.", known, log)
    return findings, objects, stats


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m cadswarm.watch")
    ap.add_argument("--project", required=True)
    ap.add_argument("--every", type=float, default=10, help="minutes between looks")
    ap.add_argument("--cycles", type=int, default=0, help="0 = until stopped")
    ap.add_argument("--render-every", type=int, default=3)
    ns = ap.parse_args(argv)

    from cadswarm.space_hub import SpaceHub
    project = os.path.abspath(ns.project)
    internal = os.path.join(project, ".cadswarm-team")
    os.makedirs(internal, exist_ok=True)
    log_path = os.path.join(internal, "watch.log")
    summary_path = os.path.join(internal, "watch-summary.md")

    def log(text):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")

    keep_running_in_background()

    def awake():
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        except Exception:
            pass

    def awake_loop():
        while True:
            awake()
            time.sleep(30)

    if os.name == "nt":
        threading.Thread(target=awake_loop, daemon=True).start()

    host, person = _person(project)
    hub = SpaceHub({"uri": host["uri"], "db": host["db"]}, identity=person)
    log(f"watch started: every {ns.every:g} min, render every {ns.render_every} cycle(s)")
    cycle, last_render = 0, None
    while ns.cycles <= 0 or cycle < ns.cycles:
        cycle += 1
        try:
            findings, objects, stats = audit(hub, log)
            if ns.render_every and cycle % ns.render_every == 1:
                rendered = hub.post("/render", {"views": ["left", "three_quarter"], "size": 700})
                last_render = rendered.get("files") or last_render
                log(f"rendered {len(last_render or [])} view(s): {last_render}")
            log(f"cycle {cycle}: {len(objects)} parts, {len(findings)} finding(s)"
                + ("; " + "; ".join(text for _, _, text in findings[:6]) if findings else ""))
            lines = [f"# Watch summary — {time.strftime('%Y-%m-%d %H:%M')}", "",
                     f"{len(objects)} parts, {len(stats)} with geometry, {len(findings)} finding(s).", ""]
            lines += [f"- {text}" for _, _, text in findings] or ["- nothing out of order"]
            if last_render:
                lines += ["", "Latest renders:", *[f"- `{p}`" for p in last_render]]
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as e:
            log(f"cycle {cycle} failed: {type(e).__name__}: {e}")
        if ns.cycles > 0 and cycle >= ns.cycles:
            break
        time.sleep(max(30.0, ns.every * 60.0))
    log("watch stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
