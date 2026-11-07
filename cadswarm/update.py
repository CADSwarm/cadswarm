"""Finding, checking and installing a newer CADSwarm release.

A release publishes, beside the team server and module, a `cadswarm-workbench.zip`: this
workbench's own files stamped with the release version. Updating is therefore:

    check the newest release tag (GitHub API, cached for an hour)
    download that zip and check it against the release's SHA256SUMS
    unpack it beside the installed workbench and check it is the version it says
    swap the folders (the old one is set aside and deleted)

A running FreeCAD keeps the old code in memory, so the caller saves its work, stops the team
and helpers, swaps, and restarts FreeCAD. Nothing here touches project folders or the app
folder's manager downloads.

Stdlib only and no FreeCAD/Qt: the window and the stop sequence live in `gui/updates.py`.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

from .hosting import REPO
from .paths import ROOT, app_dir
from .version import __version__

API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_BASE = f"https://github.com/{REPO}/releases/download"
WORKBENCH_ASSET = "cadswarm-workbench.zip"
SUMS_ASSET = "SHA256SUMS.txt"
ADDON_DISABLED = "ADDON_DISABLED"  # FreeCAD skips a Mod folder holding this file
CACHE_SECONDS = 3600            # a fresh answer is reused for an hour (startup + periodic checks)
CHECK_SECONDS = 3600            # how often an open FreeCAD asks again
TIMEOUT = 30
CHUNK = 64 * 1024
USER_AGENT = f"CADSwarm/{__version__} (+https://cadswarm.design)"


class UpdateError(Exception):
    """What went wrong, in words a person can act on."""


# ----------------------------------------------------------------------------- versions

def parse_version(text):
    """(0, 1, 17) for "v0.1.17". Raises UpdateError for anything that isn't a version."""
    match = re.search(r"(\d+(?:\.\d+)*)", text or "")
    if not match:
        raise UpdateError(f"'{text}' doesn't look like a CADSwarm version.")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate, current):
    """True when `candidate` (a release tag) is a later version than `current`."""
    try:
        new, old = parse_version(candidate), parse_version(current)
    except UpdateError:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def installed_version():
    return __version__


def release_version(release):
    return (release or {}).get("version") or ""


def newer_than_installed(release):
    return is_newer(release_version(release), installed_version())


def is_git_checkout(root=ROOT):
    """A development checkout: its code belongs to git, not to the release updater."""
    return os.path.isdir(os.path.join(root, ".git"))


def updates_allowed(root=ROOT):
    """False when updating would fight a developer or was switched off.

    CADSWARM_UPDATE=1 forces checks (and allows a swap) in a checkout; =0 turns them off anywhere.
    """
    flag = (os.environ.get("CADSWARM_UPDATE") or "").strip().lower()
    if flag in ("0", "off", "no", "false"):
        return False
    if flag in ("1", "on", "yes", "true"):
        return True
    return not is_git_checkout(root)


# ------------------------------------------------------------------------------- checking

def _urlopen(url):
    """One place for every request, so tests can answer without a network."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def _cache_path():
    return os.path.join(app_dir(), "update.json")


def _read_cache():
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError, TypeError):
        return {}
    return cache if isinstance(cache, dict) else {}


def _write_cache(release):
    try:
        os.makedirs(app_dir(), exist_ok=True)
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "release": release}, f)
    except OSError:
        pass  # a check is not worth failing over a cache we can't write


def check_latest(force=False):
    """The newest published release as {tag, version, url, notes, published}, or raise UpdateError.

    Automatic checks reuse an answer younger than an hour; `force` (a person asked) always asks
    GitHub, so pressing the button after a release never says "nothing new" from a stale cache.
    """
    if not force:
        cache = _read_cache()
        release = cache.get("release")
        try:
            fresh = time.time() - float(cache.get("at") or 0) < CACHE_SECONDS
        except (TypeError, ValueError):
            fresh = False
        if isinstance(release, dict) and release.get("tag") and fresh:
            return release
    release = _fetch_latest()
    _write_cache(release)
    return release


def _fetch_latest():
    try:
        with _urlopen(API_LATEST) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("CADSwarm hasn't published a release yet.") from None
        raise UpdateError(f"Couldn't check for a new release (GitHub said {e.code}). "
                          "CADSwarm will try again later.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UpdateError("Couldn't check for a new release. Check your internet connection; "
                          "CADSwarm will try again later.") from None
    except (ValueError, UnicodeDecodeError):
        raise UpdateError("GitHub's answer didn't make sense; CADSwarm will try again later.") from None
    if not isinstance(body, dict):
        raise UpdateError("GitHub's answer didn't make sense; CADSwarm will try again later.")
    tag = (body.get("tag_name") or "").strip()
    if body.get("draft") or body.get("prerelease") or not tag:
        raise UpdateError("The newest CADSwarm release isn't ready to install yet.")
    try:
        version = ".".join(str(part) for part in parse_version(tag))
    except UpdateError:
        raise UpdateError(f"The newest release ({tag}) has a version CADSwarm doesn't understand.") from None
    return {"tag": tag, "version": version, "url": body.get("html_url") or "",
            "notes": (body.get("body") or "").strip(), "published": body.get("published_at") or ""}


# ----------------------------------------------------------------------- downloads & files

def asset_url(release, name):
    tag = (release or {}).get("tag") or ""
    return f"{DOWNLOAD_BASE}/{tag}/{name}"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_sums(release):
    """{asset name: sha256} from the release's SHA256SUMS.txt."""
    try:
        with _urlopen(asset_url(release, SUMS_ASSET)) as response:
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise UpdateError(f"This release doesn't publish its checksums (GitHub said {e.code}), "
                          "so CADSwarm didn't install it.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UpdateError("Couldn't download the release's checksums. Check your internet connection.") from None
    sums = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            sums[parts[-1].lstrip("*")] = parts[0].lower()
    return sums


def download_asset(release, name, dest, on_progress=None):
    """Stream one release asset to `dest`, calling on_progress(bytes_so_far, total_or_0)."""
    try:
        response = _urlopen(asset_url(release, name))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError(f"This release doesn't include {name}, so CADSwarm didn't install it.") from None
        raise UpdateError(f"Couldn't download the new version (GitHub said {e.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UpdateError("Couldn't download the new version. Check your internet connection and try again.") from None
    try:
        with response, open(dest, "wb") as f:
            try:
                total = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            done = 0
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    except (OSError, TimeoutError):
        raise UpdateError("The download stopped part-way. Check your internet connection and try again.") from None
    return dest


def read_version(root):
    """The version stamped into a workbench folder's cadswarm/version.py."""
    path = os.path.join(root, "cadswarm", "version.py")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        raise UpdateError("The download doesn't look like CADSwarm, so it wasn't installed.") from None
    match = re.search(r"__version__\s*=\s*[\"']([^\"']+)[\"']", text)
    if not match:
        raise UpdateError("The download doesn't say which version it is, so CADSwarm didn't install it.")
    return match.group(1).strip()


def _safe_members(zf):
    """Validate every entry before writing a byte (zip-slip, absolute paths, symlinks)."""
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        parts = [part for part in name.split("/") if part not in ("", ".")]
        if not parts:
            continue
        if name.startswith("/") or ":" in parts[0] or any(part == ".." for part in parts):
            raise UpdateError("The download contains an unsafe file path, so CADSwarm didn't install it.")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:  # a symlink could point anywhere
            raise UpdateError("The download contains a link, so CADSwarm didn't install it.")


def extract_zip(zip_path, dest):
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError:
        raise UpdateError("CADSwarm couldn't make room to unpack the download; nothing was changed.") from None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            _safe_members(zf)
            zf.extractall(dest)
    except zipfile.BadZipFile:
        raise UpdateError("The download was damaged, so CADSwarm didn't install it.") from None
    except OSError:
        raise UpdateError("CADSwarm couldn't unpack the download; the workbench is unchanged.") from None


def fetch_update(release, parent, on_progress=None):
    """Download and verify a release into a staging folder inside `parent`; return its path.

    Everything is checked before the caller stops anything: checksums, the zip's structure and
    the version it carries. Raises UpdateError and leaves no usable staging folder on failure.
    """
    try:
        work = tempfile.mkdtemp(prefix=".cadswarm-update-", dir=parent)
    except OSError as e:
        raise UpdateError(f"CADSwarm couldn't make room for the update beside its own files ({e}).") from None
    try:
        archive = os.path.join(work, WORKBENCH_ASSET)
        download_asset(release, WORKBENCH_ASSET, archive, on_progress=on_progress)
        sums = release_sums(release)
        expected = sums.get(WORKBENCH_ASSET)
        if not expected:
            raise UpdateError(f"This release doesn't publish the checksum for {WORKBENCH_ASSET}, "
                              "so CADSwarm didn't install it.")
        if sha256_file(archive) != expected:
            raise UpdateError("The download didn't match the release's checksum, so CADSwarm "
                              "didn't install it. Try again later.")
        app = os.path.join(work, "app")
        extract_zip(archive, app)
        try:
            os.remove(archive)
        except OSError:
            pass
        if not os.path.isfile(os.path.join(app, "InitGui.py")):
            raise UpdateError("The download doesn't look like CADSwarm, so it wasn't installed.")
        found = read_version(app)
        if parse_version(found) != parse_version(release_version(release)):
            raise UpdateError(f"The download is version {found}, not {release_version(release)}, "
                              "so CADSwarm didn't install it.")
        return app
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _rename(source, dest):
    """Its own name, so a test can make the second move fail without breaking pytest's own renames."""
    os.rename(source, dest)


def _copy_tree(source, dest):
    shutil.copytree(source, dest, symlinks=True)


def _set_disabled(folder, disabled):
    """Mark a set-aside workbench so FreeCAD doesn't load it, or clear the mark on one put back.

    FreeCAD loads every folder in Mod/ that has an InitGui.py, `CadSwarm.old-…` included, and skips
    one holding ADDON_DISABLED (the Addon Manager's own switch). Windows often keeps the old folder
    until a restart, and without the mark that restart would load the old CADSwarm beside the new."""
    marker = os.path.join(folder, ADDON_DISABLED)
    try:
        if disabled:
            with open(marker, "w", encoding="utf-8") as f:
                f.write("Set aside by a CADSwarm update; deleted on the next start.\n")
        elif os.path.exists(marker):
            os.remove(marker)
    except OSError:
        pass


def _restore(old, root):
    """Put a folder set aside back where it belongs: a move if possible, a copy if it is locked."""
    if os.path.exists(root):
        return True
    try:
        _rename(old, root)
    except OSError:
        try:
            _copy_tree(old, root)
        except OSError:
            return False
    _set_disabled(root, False)
    return True


def install(app, root=ROOT, log=None):
    """Swap the staged workbench in, keeping the old one until the new one is in place.

    Returns where the old folder was set aside. If the second move fails the old folder is put
    back (moved, or copied when the move is locked) before the error is raised, so a failed
    update never leaves no workbench; if even that fails, the error names the folder to restore.
    """
    log = log or (lambda _line: None)
    root = os.path.abspath(root)
    old = f"{root}.old-{int(time.time())}"
    n = 2
    while os.path.exists(old):
        old, n = f"{root}.old-{int(time.time())}-{n}", n + 1
    try:
        _rename(root, old)
    except OSError as e:
        raise UpdateError(f"CADSwarm couldn't set the old version aside, so nothing changed ({e}).") from None
    _set_disabled(old, True)
    log("old version set aside")
    try:
        try:
            _rename(app, root)
        except OSError as e:
            if not _restore(old, root):
                # Never pretend it's fine: say where the working copy is, so nothing is ever "gone".
                raise UpdateError(
                    f"CADSwarm couldn't put the new version in place, and couldn't move the old one "
                    f"back either ({e}). The working copy is in {old}; rename it back to {root} and "
                    f"delete the {ADDON_DISABLED} file in it to keep using CADSwarm.") from None
            raise UpdateError(f"CADSwarm couldn't put the new version in place, so the old one was "
                              f"kept ({e}).") from None
    finally:
        # The staging folder now only holds what the rename left behind.
        work = os.path.dirname(os.path.abspath(app))
        if os.path.basename(work).startswith(".cadswarm-update-"):
            shutil.rmtree(work, ignore_errors=True)
    log("new version in place")
    shutil.rmtree(old, ignore_errors=True)  # Windows may still hold it; sweep() finishes the job
    return old


def sweep(root=ROOT):
    """Remove folders an earlier update set aside (Windows often needs a restart to let go).

    If the workbench itself is missing and an earlier update left only its old folder, that folder
    is put back first: cleanup must never be what makes the add-on disappear."""
    root = os.path.abspath(root)
    parent, name = os.path.split(root)
    if not os.path.isdir(parent):
        return
    stale, staged = [], []
    for entry in os.scandir(parent):
        if not entry.is_dir():
            continue
        if entry.name.startswith(f"{name}.old-"):
            stale.append(entry.path)
        elif entry.name.startswith(".cadswarm-update-"):
            staged.append(entry.path)
    if not os.path.isdir(root) and stale:
        newest = max(stale, key=lambda p: os.path.getmtime(p))
        if _restore(newest, root):
            stale.remove(newest)
    for path in stale + staged:
        shutil.rmtree(path, ignore_errors=True)


# -------------------------------------------------------------------------------- restart

_WAITER = r"""
import os, sys, time, subprocess
pid, exe, script = int(sys.argv[1]), sys.argv[2], sys.argv[3]

def alive(process):
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, process)  # SYNCHRONIZE
        if not handle:
            return False
        # A handle stays valid after the process ends, so ask whether it has actually finished.
        finished = kernel32.WaitForSingleObject(handle, 0) == 0  # WAIT_OBJECT_0
        kernel32.CloseHandle(handle)
        return not finished
    try:
        os.kill(process, 0)
    except OSError:
        return False
    return True

for _ in range(900):
    if not alive(pid):
        break
    time.sleep(0.2)
kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True}
if os.name == "nt":
    kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
else:
    kwargs["start_new_session"] = True
subprocess.Popen([exe, script], **kwargs)
"""


def freecad_executable():
    """The FreeCAD program to start again: the one running now, or the one CADSWARM_FREECAD names."""
    named = (os.environ.get("CADSWARM_FREECAD") or "").strip()
    if named:
        if named.endswith(".app") or os.path.isdir(named):
            mac = os.path.join(named, "Contents", "MacOS", "FreeCAD")
            if os.path.isfile(mac):
                return mac
        elif os.path.isfile(named):
            return named
    exe = sys.executable
    if "freecad" in os.path.basename(exe).lower():
        return exe
    try:
        import FreeCAD  # only available inside FreeCAD; the fallbacks below still work outside
        home = FreeCAD.getHomePath()
    except Exception:
        home = ""
    if home:
        for rel in (("bin", "FreeCAD.exe"), ("bin", "FreeCAD"), ("bin", "freecad"),
                    ("MacOS", "FreeCAD"), ("FreeCAD",)):
            path = os.path.join(home, *rel)
            if os.path.isfile(path):
                return path
    return exe


def spawn_restart(freecad, opener, python, pid=None):
    """Start a detached helper that reopens FreeCAD once this process has exited."""
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "close_fds": True, "cwd": tempfile.gettempdir()}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen([python, "-c", _WAITER, str(pid or os.getpid()), freecad, opener],
                            **kwargs).pid


def reopen_path():
    """Where the paths of the documents to reopen after the restart are kept."""
    return os.path.join(app_dir(), "update-reopen.json")


def remember_reopen(paths):
    try:
        os.makedirs(app_dir(), exist_ok=True)
        with open(reopen_path(), "w", encoding="utf-8") as f:
            json.dump([p for p in paths if p], f)
    except OSError:
        pass


def take_reopen():
    """The documents to reopen, once: the new FreeCAD asks for them at startup."""
    path = reopen_path()
    try:
        with open(path, encoding="utf-8") as f:
            paths = json.load(f)
    except (OSError, ValueError, TypeError):
        return []
    try:
        os.remove(path)
    except OSError:
        pass
    return [p for p in paths if isinstance(p, str) and os.path.isfile(p)]
