"""HTTP surface of the hub. Thin: every route authenticates, then calls Hub.

Auth headers on every call except /join and /health:
  X-Participant: <participant_id>   Authorization: Bearer <token>   X-Fence: <fence>
"""
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse

from .core import Hub, HubError


def create_app(hub: Hub, data_dir: str, sweep_seconds: float = 5.0):
    stop = threading.Event()

    def sweeper():
        while not stop.wait(sweep_seconds):
            try:
                hub.sweep()
            except Exception as e:  # never let the sweeper die silently
                print(f"[hub] sweep failed: {e}")

    @asynccontextmanager
    async def lifespan(_app):
        threading.Thread(target=sweeper, daemon=True).start()
        yield
        stop.set()
        hub.worker.stop()

    app = FastAPI(title="CadSwarm hub", lifespan=lifespan)
    exports = os.path.join(data_dir, "exports")
    os.makedirs(exports, exist_ok=True)

    @app.exception_handler(HubError)
    def hub_error(_request: Request, exc: HubError):
        return JSONResponse(status_code=exc.status, content=exc.body())

    def me(x_participant: str = Header(...), authorization: str = Header(...),
           x_fence: str | None = Header(None)):
        token = authorization.removeprefix("Bearer ").strip()
        return hub.authenticate(x_participant, token, x_fence)

    @app.get("/health")
    def health():
        return {"ok": True, "seq": hub.seq(), "freecad": hub.query("ping")["freecad"]}

    @app.post("/join")
    def join(body: dict = Body(...)):
        return hub.join(body.get("workspace_token"), body.get("name") or "anonymous", body.get("kind", "agent"),
                        body.get("client"), body.get("role", "member"), body.get("resume"))

    @app.post("/heartbeat")
    def heartbeat(body: dict = Body(default={}), p=Depends(me)):
        return hub.heartbeat(p["id"], body.get("doing"))

    @app.post("/leave")
    def leave(p=Depends(me)):
        return hub.leave(p["id"])

    @app.get("/model")
    def model(p=Depends(me)):
        return hub.model()

    @app.get("/objects/{name}")
    def get_object(name: str, p=Depends(me)):
        info = hub.query("get_object", {"name": name})
        info["locked_by"] = (hub.holder_info(name) or {}).get("name")
        return info

    @app.post("/brep")
    def brep(body: dict = Body(default={}), p=Depends(me)):
        return {"objects": hub.brep(body.get("names"))}

    @app.post("/dependents")
    def dependents(body: dict = Body(...), p=Depends(me)):
        return hub.query("dependents", {"names": body.get("names", [])})

    @app.post("/ops")
    def ops(body: dict = Body(...), p=Depends(me)):
        return hub.op(p["id"], body.get("op"), body.get("args"), body.get("expected_revs"))

    @app.post("/locks")
    def lock(body: dict = Body(...), p=Depends(me)):
        return hub.acquire(p["id"], body.get("names", []))

    @app.post("/locks/release")
    def release(body: dict = Body(...), p=Depends(me)):
        return hub.release(p["id"], body.get("names", []), bool(body.get("force")))

    @app.post("/interference")
    def interference(body: dict = Body(default={}), p=Depends(me)):
        return hub.query("interference", {"names": body.get("names"), "min_volume": body.get("min_volume", 1e-3)})

    @app.post("/export")
    def export(body: dict = Body(default={}), p=Depends(me)):
        fmt = body.get("format", "step").lower()
        if fmt not in ("step", "stl"):
            raise HubError(400, "bad_args", "format must be step or stl")
        fname = f"model-{hub.seq()}-{int(time.time())}.{fmt}"
        out = hub.query("export", {"path": os.path.join(exports, fname), "names": body.get("names")})
        return {"file": fname, "url": f"/files/{fname}", "objects": out["objects"]}

    @app.get("/files/{fname}")
    def files(fname: str, p=Depends(me)):
        path = os.path.join(exports, os.path.basename(fname))
        if not os.path.exists(path):
            raise HubError(404, "not_found", "No such export")
        return FileResponse(path, filename=fname)

    @app.get("/snapshot.FCStd")
    def snapshot(p=Depends(me)):
        return FileResponse(hub.worker.doc_path, filename="model.FCStd")

    @app.get("/participants")
    def participants(p=Depends(me)):
        return {"participants": hub.participants()}

    @app.post("/takeover")
    def takeover(body: dict = Body(...), p=Depends(me)):
        return hub.take_over(p["id"], body.get("target"), bool(body.get("force")))

    @app.get("/tasks")
    def tasks(p=Depends(me)):
        return {"tasks": hub.tasks()}

    @app.post("/tasks")
    def create_task(body: dict = Body(...), p=Depends(me)):
        return hub.create_task(p["id"], body.get("title", ""), body.get("detail", ""), body.get("objects"),
                               body.get("assignee"))

    @app.post("/tasks/{tid}/claim")
    def claim(tid: str, body: dict = Body(default={}), p=Depends(me)):
        return hub.claim_task(p["id"], tid, body.get("expected_rev"))

    @app.post("/tasks/{tid}/result")
    def result(tid: str, body: dict = Body(...), p=Depends(me)):
        return hub.report_task(p["id"], tid, body.get("status", "done"), body.get("summary", ""), body.get("evidence"))

    @app.post("/messages")
    def send(body: dict = Body(...), p=Depends(me)):
        return hub.send(p["id"], body.get("to", "all"), body.get("body", ""), body.get("kind", "message"),
                        body.get("task_id"))

    @app.get("/messages")
    def chat(limit: int = 50, p=Depends(me)):
        return {"messages": hub.chat(limit)}

    @app.get("/inbox")
    def inbox(p=Depends(me)):
        return {"messages": hub.inbox(p["id"])}

    @app.post("/inbox/ack")
    def ack(body: dict = Body(...), p=Depends(me)):
        return hub.ack(p["id"], body.get("ids", []))

    @app.get("/events")
    def events(since: int = 0, wait: float = 0, p=Depends(me)):
        return hub.events(since, wait)

    @app.get("/context")
    def context(p=Depends(me)):
        return {"context": hub.context(p["id"])}

    return app
