"""One CADSwarm window per profile: a second launch hands its files to the running app and exits.

Double-clicking a .FCStd file ("Open with CADSwarm") or running ``cadswarm.app file.FCStd`` while CADSwarm is
already open should open the file in that window, not start a second copy of the whole application (which
would also fight over the same engine profile). The first instance listens on a per-profile local channel (a
named pipe on Windows, a Unix socket elsewhere); later launches connect, send the file paths and exit.

The channel is authenticated with a random key stored in the profile, so another user (or a web page) can't
make CADSwarm open files. Stdlib only, no Qt: the hand-off happens before FreeCAD is imported, which keeps a
second launch instant. The running app drains ``Server.queue`` from a Qt timer on its main thread.
"""
import hashlib
import multiprocessing
import os
import queue
import sys
import tempfile
import threading
from multiprocessing.connection import Client, Listener

KEY_FILE = "instance.key"


def _is_windows():
    return sys.platform.startswith("win")


def address(home):
    """(address, family) of the channel for an engine profile folder."""
    digest = hashlib.sha1(os.path.normcase(os.path.abspath(home)).encode("utf-8")).hexdigest()[:16]
    if _is_windows():
        return r"\\.\pipe\CADSwarm-" + digest, "AF_PIPE"
    # Unix socket paths are limited to ~104 bytes, so not inside the (possibly long) profile path.
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return os.path.join(tempfile.gettempdir(), f"cadswarm-{uid}-{digest}.sock"), "AF_UNIX"


def authkey(home, create=False):
    path = os.path.join(home, KEY_FILE)
    try:
        with open(path, "rb") as f:
            key = f.read()
        if len(key) >= 16:
            return key
    except OSError:
        pass
    if not create:
        return None
    os.makedirs(home, exist_ok=True)
    key = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def forward(home, files):
    """Hand ``files`` to a CADSwarm already running on this profile. True if one took them."""
    key = authkey(home)
    addr, family = address(home)
    if key is None or (family == "AF_UNIX" and not os.path.exists(addr)):
        return False
    try:
        conn = Client(addr, family, authkey=key)
    except (OSError, EOFError, multiprocessing.AuthenticationError):
        return False  # nobody listening (or a stale socket): this launch becomes the app
    try:
        if _is_windows():
            try:  # let the running window come to the front (this process owns the user's click)
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
            except Exception:  # noqa: BLE001
                pass
        conn.send({"open": [os.path.abspath(f) for f in files]})
        return conn.poll(10) and conn.recv() == "ok"
    except (OSError, EOFError):
        return False
    finally:
        conn.close()


class Server:
    """Accepts hand-offs from later launches. ``queue`` receives one list of paths per launch ([] = just raise)."""

    def __init__(self, home):
        self.home = home
        self.queue = queue.Queue()
        self._listener = None

    def start(self):
        addr, family = address(self.home)
        key = authkey(self.home, create=True)
        if family == "AF_UNIX" and os.path.exists(addr):
            try:  # forward() failed to reach it, so it's left over from a crashed run
                os.unlink(addr)
            except OSError:
                return False
        try:
            self._listener = Listener(addr, family, authkey=key)
        except OSError:
            return False  # another instance owns the channel; run without hand-offs
        threading.Thread(target=self._serve, name="cadswarm-instance", daemon=True).start()
        return True

    def _serve(self):
        while self._listener is not None:
            try:
                conn = self._listener.accept()
            except (OSError, EOFError, multiprocessing.AuthenticationError):
                if self._listener is None:
                    return
                continue
            try:
                if conn.poll(10):
                    msg = conn.recv()
                    files = msg.get("open", []) if isinstance(msg, dict) else []
                    self.queue.put([f for f in files if isinstance(f, str)])
                    conn.send("ok")
            except (OSError, EOFError):
                pass
            finally:
                conn.close()

    def drain(self):
        """Every hand-off received since the last call, as one list per launch."""
        out = []
        while True:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                return out

    def close(self):
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
