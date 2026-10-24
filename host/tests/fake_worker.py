"""Stand-in for cadswarm/worker/freecad_worker.py: same JSON-lines protocol, no FreeCAD."""
import json
import sys
import time

print(json.dumps({"ready": True, "doc": sys.argv[1] if len(sys.argv) > 1 else ""}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    op, args = req["op"], req.get("args") or {}
    if op == "sleep":
        time.sleep(args.get("seconds", 5))
    if op == "die":
        sys.exit(3)
    if op == "fail":
        resp = {"ok": False, "code": "lock_violation", "error": "would change ['Plate']"}
    else:
        resp = {"ok": True, "result": {"echo": op, "allowed": req.get("allowed")},
                "created": [args["name"]] if "name" in args else [], "modified": [], "deleted": [],
                "recomputed": [], "broken": []}
    resp["id"] = req["id"]
    print(json.dumps(resp), flush=True)
