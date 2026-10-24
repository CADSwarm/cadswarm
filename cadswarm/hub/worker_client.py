"""Supervises the FreeCAD worker subprocess and speaks its JSON-lines protocol.

Calls are serialized by a lock: the worker handles one request at a time, which
is exactly the ordering guarantee the shared document needs. If the worker
dies (an agent script segfaulting OCC, say), the next call restarts it from the
FCStd it saved after its last committed op — so a crash loses at most the op
that crashed it, never committed work.
"""
import json
import os
import subprocess
import threading

from ..config import find_freecad_python, worker_script


class WorkerError(Exception):
    def __init__(self, code, message, trace=None):
        super().__init__(message)
        self.code = code
        self.trace = trace


class WorkerClient:
    def __init__(self, doc_path, python=None, timeout=120):
        self.doc_path = os.path.abspath(doc_path)
        self.python = python or find_freecad_python()
        self.timeout = timeout
        self.lock = threading.Lock()
        self.proc = None
        self.next_id = 0

    def start(self):
        env = dict(os.environ, CADSWARM_FREECAD_BIN=os.path.dirname(self.python),
                   PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            [self.python, worker_script(), self.doc_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
            env=env, text=True, encoding="utf-8", bufsize=1)
        hello = self._read_line()
        if not hello.get("ready"):
            raise WorkerError("worker_start", f"FreeCAD worker did not start: {hello}")

    def _read_line(self):
        result = {}

        def read():
            line = self.proc.stdout.readline()
            result["line"] = line

        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(self.timeout)
        if t.is_alive():
            # A runaway agent script: kill the worker; the restart reloads the last save.
            self.proc.kill()
            raise WorkerError("timeout", f"FreeCAD worker exceeded {self.timeout}s; the op was rolled back")
        if not result.get("line"):
            raise WorkerError("worker_died", "FreeCAD worker exited; the op was rolled back")
        return json.loads(result["line"])

    def call(self, op, args=None, allowed=None):
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self.start()
            self.next_id += 1
            req = {"id": self.next_id, "op": op, "args": args or {}, "allowed": allowed}
            try:
                self.proc.stdin.write(json.dumps(req) + "\n")
                self.proc.stdin.flush()
                resp = self._read_line()
            except (OSError, ValueError) as e:
                self.proc = None
                raise WorkerError("worker_died", f"FreeCAD worker connection failed: {e}")
            except WorkerError:
                self.proc = None
                raise
        if not resp.get("ok"):
            raise WorkerError(resp.get("code", "op_error"), resp.get("error", "unknown error"), resp.get("trace"))
        return resp

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
