"""Supervises the FreeCAD worker subprocess and speaks its JSON-lines protocol.

Calls are serialized by a lock: the worker handles one request at a time, which
is exactly the ordering guarantee the shared document needs. If the worker
dies (an agent script segfaulting OCC, say), the next call restarts it from the
FCStd it saved after its last committed op — so a crash loses at most the op
that crashed it, never committed work.
"""
import json
import logging
import os
import subprocess
import threading
import time

from ..config import find_freecad_python, worker_script

log = logging.getLogger("cadswarm.worker")


class WorkerError(Exception):
    def __init__(self, code, message, trace=None):
        super().__init__(message)
        self.code = code
        self.trace = trace


class WorkerClient:
    def __init__(self, doc_path, python=None, timeout=120, script=None):
        self.doc_path = os.path.abspath(doc_path)
        self.python = python or find_freecad_python()
        self.script = script or worker_script()
        self.timeout = timeout
        self.lock = threading.Lock()
        self.proc = None
        self.next_id = 0

    def _kill(self):
        """Drop the worker for good: a process we merely forget would keep the document open."""
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            pass
        for pipe in (proc.stdin, proc.stdout):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass

    def start(self):
        env = dict(os.environ, CADSWARM_FREECAD_BIN=os.path.dirname(self.python),
                   PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            [self.python, self.script, self.doc_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
            env=env, text=True, encoding="utf-8", bufsize=1)
        try:
            hello = self._read_message(None)
            if not hello.get("ready"):
                raise WorkerError("worker_start", f"FreeCAD worker did not start: {hello}")
        except BaseException:
            self._kill()
            raise

    def _read_line(self, deadline):
        result = {}

        def read():
            result["line"] = self.proc.stdout.readline()

        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(max(deadline - time.monotonic(), 0))
        if t.is_alive():
            # A runaway agent script: kill the worker; the restart reloads the last save.
            raise WorkerError("timeout", f"FreeCAD worker exceeded {self.timeout}s; the op was rolled back")
        if not result.get("line"):
            raise WorkerError("worker_died", "FreeCAD worker exited; the op was rolled back")
        return result["line"]

    def _read_message(self, want_id):
        """Next protocol message for `want_id` (None: the ready hello).

        Stray lines (C-level FreeCAD/OCC output that reached the pipe) and replies to some
        other request are logged and skipped rather than trusted or crashed on.
        """
        deadline = time.monotonic() + self.timeout
        while True:
            line = self._read_line(deadline)
            try:
                msg = json.loads(line)
            except ValueError:
                msg = None
            if not isinstance(msg, dict) or ("ready" not in msg if want_id is None else msg.get("id") != want_id):
                log.warning("ignoring unexpected worker output: %.200r", line)
                continue
            return msg

    def call(self, op, args=None, allowed=None):
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._kill()
                self.start()
            self.next_id += 1
            req = {"id": self.next_id, "op": op, "args": args or {}, "allowed": allowed}
            try:
                self.proc.stdin.write(json.dumps(req) + "\n")
                self.proc.stdin.flush()
                resp = self._read_message(self.next_id)
            except (OSError, ValueError) as e:
                self._kill()
                raise WorkerError("worker_died", f"FreeCAD worker connection failed: {e}")
            except BaseException:
                self._kill()
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
