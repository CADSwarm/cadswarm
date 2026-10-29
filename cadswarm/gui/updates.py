"""The update window: "A new release is here" and the one click that installs it.

`cadswarm/update.py` finds the release and moves the files. This module owns the small window
and the safety sequence around it: save open documents, leave the team (releasing locks), stop
this computer's AI helpers and team server, install, then restart FreeCAD back into CADSwarm.

Like the rest of the panel, everything that touches FreeCAD or Qt runs on the GUI thread.
Downloading, waiting for runners and stopping the server happen on one worker thread that only
posts stages and progress into a queue the window drains on a timer.
"""
import html
import os
import queue
import re
import shutil
import threading
import time

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

from .. import hosting, update
from ..naming import is_mirror_doc
from ..paths import ROOT, app_dir, freecad_python
from . import theme

CHECK_AFTER_MS = 2 * 60 * 1000   # let FreeCAD finish opening before the first check
LATER_SECONDS = 24 * 3600        # "Later" hides this release for a day
OPENER = os.path.join(ROOT, "cadswarm", "gui", "open_cadswarm.py")

_dialog = None
_timer = None
_checking = False
# What a check thread found, run on the GUI thread by _pump. A QTimer.singleShot made on a plain Python
# thread never fires (that thread has no Qt event loop), so the thread only queues the next step.
_results = queue.Queue()
_pump = None


# ------------------------------------------------------------------------------- schedule

def on_start():
    """Called once from FreeCAD's startup: finish an earlier update's cleanup and start checking."""
    # A checkout's parent folder is someone's projects folder, not FreeCAD's Mod: leave it alone.
    if update.updates_allowed():
        try:
            update.sweep()
        except OSError:
            pass
    start_checking()


def start_checking():
    """Ask GitHub shortly after startup, then every hour while FreeCAD stays open."""
    global _timer
    if _timer is not None:
        return
    _timer = QtCore.QTimer()
    _timer.setInterval(update.CHECK_SECONDS * 1000)
    _timer.timeout.connect(lambda: check(manual=False))
    _timer.start()
    QtCore.QTimer.singleShot(CHECK_AFTER_MS, lambda: check(manual=False))


def check_for_updates():
    """The Tools entry: check now and say what happened, even when there is nothing new."""
    check(manual=True)


def check(manual=False):
    global _checking
    if _checking:
        return
    if not update.updates_allowed():
        if manual:
            if update.is_git_checkout():
                _hint("This copy is a development checkout, so CADSwarm won't replace its files. "
                      "Update it with git instead; installed copies update themselves.")
            else:
                _hint("Update checks are turned off on this computer (CADSWARM_UPDATE=0).")
        return
    if _dialog is not None and _dialog.busy:
        return
    if manual and _dialog is not None and _dialog.isVisible():
        _dialog.raise_()
        _dialog.activateWindow()
        return
    if manual:
        from .panel import say
        say("Checking for a new CADSwarm release…")
    _checking = True
    _start_pump()
    threading.Thread(target=_look, args=(manual,), daemon=True).start()


def _start_pump():
    """The GUI-thread timer that runs what a check thread queued (called from the GUI thread)."""
    global _pump
    if _pump is None:
        _pump = QtCore.QTimer()
        _pump.setInterval(200)
        _pump.timeout.connect(_drain)
    if not _pump.isActive():
        _pump.start()


def _drain():
    while True:
        try:
            step = _results.get_nowait()
        except queue.Empty:
            break
        try:
            step()
        except Exception as e:  # noqa: BLE001 - a check must never break FreeCAD
            _console_warning(f"couldn't show the update check: {e}")
    if not _checking and _results.empty() and _pump is not None:
        _pump.stop()


def _look(manual):
    global _checking
    try:
        release = update.check_latest(force=manual)
    except update.UpdateError as e:
        if manual:
            _results.put(lambda: _hint(str(e)))
        else:
            _console_warning(f"couldn't check for a new release: {e}")
        _checking = False
        return
    except Exception as e:  # noqa: BLE001 - a check must never break FreeCAD
        _console_warning(f"couldn't check for a new release: {e}")
        _checking = False
        return
    _results.put(lambda: _found(manual, release))
    _checking = False


def _found(manual, release):
    if not update.newer_than_installed(release):
        if manual:
            _hint(f"You're on the newest version (v{update.installed_version()}).")
        return
    if not manual and _postponed(release):
        return
    # Don't open over the setup guide (or another dialog someone is reading): ask again shortly.
    if not manual and QtGui.QApplication.activeModalWidget() is not None:
        QtCore.QTimer.singleShot(10 * 60 * 1000, lambda: check(manual=False))
        return
    _show(release)


def _postponed(release):
    from .panel import prefs
    p = prefs()
    if p.GetString("UpdateLaterTag") != release.get("tag"):
        return False
    try:
        until = float(p.GetString("UpdateLaterAt") or 0) + LATER_SECONDS
    except ValueError:
        return False
    return time.time() < until


def _postpone(release):
    from .panel import prefs
    p = prefs()
    p.SetString("UpdateLaterTag", release.get("tag") or "")
    p.SetString("UpdateLaterAt", str(time.time()))


def _forget_postponed():
    from .panel import prefs
    p = prefs()
    p.SetString("UpdateLaterTag", "")
    p.SetString("UpdateLaterAt", "")


def _show(release):
    global _dialog
    if _dialog is None:  # one window for the life of this FreeCAD, shown again when a release lands
        _dialog = UpdateDialog(release)
    elif _dialog.busy or _dialog.after_stop:
        return  # mid-update (or stopped by one): a late check must not reset that window
    else:
        _dialog.release = release
        _dialog.render_release()
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()


def _hint(text):
    QtGui.QMessageBox.information(Gui.getMainWindow(), "CADSwarm", text)


def _console_warning(text):
    App.Console.PrintWarning(f"[CADSwarm] {text}\n")


# --------------------------------------------------------------------------------- saving

def _recovery_folder():
    folder = os.path.join(app_dir(), "update-recovery")
    os.makedirs(folder, exist_ok=True)
    return folder


def _free_name(folder, base):
    dest, n = os.path.join(folder, f"{base}.FCStd"), 2
    while os.path.exists(dest):
        dest, n = os.path.join(folder, f"{base} ({n}).FCStd"), n + 1
    return dest


def _inside(path, folder):
    """True when `path` is inside `folder`: the update replaces `folder`, so the file must move."""
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(folder)]) == os.path.abspath(folder)
    except ValueError:  # different drives on Windows
        return False


def save_documents():
    """Save every open document and return the files to reopen after the restart.

    The live read-only team model is skipped (the team server owns it). Every other document
    with a file is written (FreeCAD can't tell us whether it is behind), and a document that was
    never saved is kept in the app folder's recovery folder instead of prompting during a
    restart, so nothing the person made is lost and nothing blocks the quit. A document saved
    inside the workbench folder (a demo, say) is copied out before that folder is replaced.
    """
    reopen, recovered, failed = [], [], []
    for name in list(App.listDocuments()):
        try:
            doc = App.getDocument(name)
        except Exception:
            continue
        if doc is None or is_mirror_doc(doc.Name):
            continue
        try:
            if doc.FileName:
                # Always write it: FreeCAD's isTouched() says "needs recompute", not "changed since the
                # last save", so it cannot tell us whether the file on disk is behind.
                doc.save()
                path = doc.FileName
                if _inside(path, ROOT):
                    base = re.sub(r"[^A-Za-z0-9 _.-]+", " ", doc.Label or doc.Name or "document").strip() or "document"
                    dest = _free_name(_recovery_folder(), base)
                    shutil.copy2(path, dest)
                    path = dest
                    recovered.append(dest)
                reopen.append(path)
            elif doc.Objects:  # never saved: keep the work rather than prompt during a restart
                base = re.sub(r"[^A-Za-z0-9 _.-]+", " ", doc.Label or doc.Name or "document").strip() or "document"
                dest = _free_name(_recovery_folder(), base)
                doc.saveAs(dest)
                reopen.append(dest)
                recovered.append(dest)
        except Exception as e:  # noqa: BLE001 - report it, and don't close that document
            failed.append(f"{doc.Label or name}: {e}")
    if failed:
        # Nothing has been stopped yet: leaving is better than closing a document whose work wasn't saved.
        raise update.UpdateError(
            "CADSwarm couldn't save " + "; ".join(failed) +
            ". Save or close that document yourself, then update again. Nothing was changed.")
    if recovered:
        from .panel import say
        say(f"Kept your work in {os.path.dirname(recovered[0])}")
    return reopen


def close_documents():
    """Close them without asking: they are saved, and the restart reopens them."""
    for name in list(App.listDocuments()):
        try:
            App.closeDocument(name)
        except Exception:
            pass


# ---------------------------------------------------------------------------------- stopping

def stop_helpers(folders, timeout=10):
    """Ask each runner to stop (it ends its agent run and warm server), then wait for it."""
    from ..runner import runner_alive, stop_runner
    for folder in folders:
        try:
            stop_runner(folder)
        except OSError:
            continue
    deadline = time.time() + timeout
    while time.time() < deadline and any(runner_alive(folder) for folder in folders):
        time.sleep(0.2)


def stop_hosting(pids, timeout=10):
    hosting.stop_team(pids)
    deadline = time.time() + timeout
    while time.time() < deadline and any(hosting.pid_alive(pid) for pid in pids.values() if pid):
        time.sleep(0.2)


def _others_on_team():
    """People (and helpers) other than me who are on the team right now."""
    from .panel import SESSION
    session = SESSION
    if not session.connected or not session.ident:
        return []
    me = session.ident.get("name")
    return [p for p in session.model.get("participants", [])
            if p.get("name") != me and p.get("status") == "active"]


def _hosting_here():
    from .panel import host_dir_for, prefs
    return bool(host_dir_for(prefs().GetString("Db")))


# ---------------------------------------------------------------------------------- window

class UpdateDialog(QtGui.QDialog):
    """A small window over FreeCAD: the release, one button that installs it, and progress."""

    def __init__(self, release):
        super().__init__(Gui.getMainWindow())
        self.release = release
        self.busy = False
        self.after_stop = False
        self.installed_ok = False
        self.reopen_paths = []
        self.events = queue.Queue()
        self.setWindowFlag(QtCore.Qt.Tool, True)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
        self.setWindowTitle("CADSwarm update")
        theme.apply(self)
        self.setMinimumWidth(430)
        self.setMaximumWidth(560)

        lay = QtGui.QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(9)

        self.headline = QtGui.QLabel("A new release is here")
        self.headline.setObjectName("title")
        lay.addWidget(self.headline)

        self.lede = QtGui.QLabel()
        self.lede.setObjectName("lead")
        self.lede.setWordWrap(True)
        lay.addWidget(self.lede)

        self.notes = QtGui.QTextBrowser()
        self.notes.setOpenLinks(False)
        self.notes.setMinimumHeight(96)
        self.notes.setMaximumHeight(150)
        lay.addWidget(self.notes)

        self.plan = QtGui.QLabel()
        self.plan.setObjectName("hint")
        self.plan.setWordWrap(True)
        lay.addWidget(self.plan)

        self.progress = QtGui.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.hide()
        lay.addWidget(self.progress)

        self.status = QtGui.QLabel("")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        self.status.hide()
        lay.addWidget(self.status)

        row = QtGui.QHBoxLayout()
        self.whats_new = QtGui.QPushButton("What's new")
        self.whats_new.setObjectName("quiet")
        self.whats_new.clicked.connect(self.open_notes)
        self.later = QtGui.QPushButton("Later")
        self.later.setToolTip("Don't ask about this release again for a day")
        self.later.clicked.connect(self.postpone)
        self.now = QtGui.QPushButton("Update now")
        self.now.setObjectName("primary")
        self.now.setDefault(True)
        self.now.clicked.connect(self.update_now)
        row.addWidget(self.whats_new)
        row.addStretch(1)
        row.addWidget(self.later)
        row.addWidget(self.now)
        lay.addLayout(row)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.drain)
        self.render_release()

    def render_release(self):
        self.after_stop = False
        self.installed_ok = False
        self.reopen_paths = []
        try:  # a previous failure may have turned the button into Close
            self.now.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.now.clicked.connect(self.update_now)
        version = self.release.get("version") or ""
        self.lede.setText(f"CADSwarm <b>v{html.escape(version)}</b> is ready to install.")
        notes = (self.release.get("notes") or "").strip()
        self.notes.setPlainText(_short_notes(notes))
        self.notes.setVisible(bool(notes))
        self.whats_new.setVisible(bool(self.release.get("url")))
        stops = "your AI helpers and the team server stop" if _hosting_here() else "your AI helpers stop"
        self.plan.setText(
            f"Your work is saved first. Then {stops}, the new version installs, and FreeCAD "
            "reopens on it.")
        self.status.hide()
        self.progress.hide()
        self.set_busy(False)

    # ------------------------------------------------------------------ buttons

    def open_notes(self):
        url = (self.release or {}).get("url")
        if url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def postpone(self):
        if self.busy:
            return
        _postpone(self.release)
        self.close()

    def set_busy(self, on):
        self.busy = on
        for button in (self.now, self.later, self.whats_new):
            button.setEnabled(not on)
        self.now.setText("Updating…" if on else "Update now")
        if on:
            self.progress.show()
            self.status.show()

    def update_now(self):
        if self.busy:
            return
        if not self.ok_to_stop():
            return
        _forget_postponed()
        self.status.setObjectName("hint")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.set_busy(True)
        self.progress.setRange(0, 0)  # unknown size until the download starts
        self.say(f"Downloading CADSwarm v{self.release.get('version')}…")
        QtGui.QApplication.processEvents()  # paint "Updating…" before the network work starts
        self.timer.start()
        threading.Thread(target=self.fetch, daemon=True).start()

    def ok_to_stop(self):
        """One question, and only when someone else would notice the team going quiet."""
        if not _hosting_here():
            return True
        from .panel import SESSION, host_dir_for, prefs
        others = _others_on_team()
        if others:
            names = [p.get("name") for p in others if p.get("name")]
            who = ", ".join(names[:4]) + (" and others" if len(names) > 4 else "")
            text = (f"{who} {'is' if len(names) == 1 else 'are'} on the team now.\n\n"
                    "Updating stops this computer's team server for a moment. Everyone reconnects by "
                    "themselves once the new version starts.\n\nUpdate now?")
        elif not SESSION.connected and hosting.team_ready(host_dir_for(prefs().GetString("Db"))):
            # Hosting while this window isn't in the team: someone may still be connected out there.
            text = ("Your team server is running on this computer.\n\nUpdating stops it for a moment; "
                    "anyone connected reconnects once the new version starts.\n\nUpdate now?")
        else:
            return True
        answer = QtGui.QMessageBox.question(self, "CADSwarm update", text,
                                            QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
                                            QtGui.QMessageBox.No)
        return answer == QtGui.QMessageBox.Yes

    # ------------------------------------------------------------------ worker

    def fetch(self):
        try:
            parent = os.path.dirname(os.path.abspath(ROOT))
            staged = update.fetch_update(self.release, parent, on_progress=self.progressed)
            self.events.put(("staged", staged))
        except update.UpdateError as e:
            self.events.put(("error", str(e)))
        except Exception as e:  # noqa: BLE001 - say it in the window instead of a traceback
            self.events.put(("error", f"The update couldn't be prepared: {e}"))

    def progressed(self, done, total):
        self.events.put(("progress", (done, total)))

    def drain(self):
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "progress":
                done, total = payload
                if total:
                    self.progress.setRange(0, 100)
                    self.progress.setValue(min(100, int(done * 100 / total)))
                    self.say(f"Downloading CADSwarm v{self.release.get('version')}… "
                             f"{done // 1024 // 1024} of {max(1, total // 1024 // 1024)} MB")
                else:
                    self.progress.setRange(0, 0)
            elif kind == "status":
                self.say(payload)
            elif kind == "staged":
                try:
                    self.staged(payload)
                except Exception as e:  # noqa: BLE001 - a stuck "Updating…" is worse than a clear error
                    self.discard(payload)
                    self.failed(f"The update couldn't start: {e}")
            elif kind == "installed":
                self.installed_ok = True
                self.installed()
            elif kind == "error":
                self.failed(payload)

    def say(self, text):
        self.status.setText(text)

    # ------------------------------------------------------------------ the sequence

    def staged(self, staged):
        """Downloaded and checked: save, leave the team, then stop everything on a worker."""
        from .panel import SESSION, host_dir_for, local_helper_folders, prefs
        self.say("Saving your work…")
        QtGui.QApplication.processEvents()
        try:
            self.reopen_paths = save_documents()
        except Exception as e:  # noqa: BLE001
            self.discard(staged)
            self.failed(f"CADSwarm couldn't save your work, so nothing was changed: {e}")
            return
        self.say("Leaving the team…")
        try:
            SESSION.disconnect()
        except Exception:
            pass
        p = prefs()
        pids = {"host_pid": p.GetString("HostPid"), "spacetime_pid": p.GetString("SpacetimePid")}
        folders = local_helper_folders()
        hosting_here = bool(host_dir_for(p.GetString("Db")))
        self.after_stop = True
        # The next start finds or starts this team's server itself; stale PIDs would only confuse it.
        # PIDs of another team this computer hosts are left alone (that server isn't being stopped).
        if hosting_here:
            for key in ("HostPid", "SpacetimePid"):
                p.SetString(key, "")
        self.say("Stopping your AI helpers…")
        threading.Thread(target=self.stop_and_install,
                         args=(staged, folders, pids, hosting_here), daemon=True).start()

    def stop_and_install(self, staged, folders, pids, hosting_here):
        try:
            stop_helpers(folders)
            self.events.put(("status", "Stopping the team server…"))
            if hosting_here:
                stop_hosting(pids)
            self.events.put(("status", f"Installing CADSwarm v{self.release.get('version')}…"))
            update.install(staged)
            self.events.put(("installed", None))
        except update.UpdateError as e:
            self.discard(staged)
            self.events.put(("error", str(e)))
        except Exception as e:  # noqa: BLE001
            self.discard(staged)
            self.events.put(("error", f"The update couldn't be installed: {e}"))

    def installed(self):
        self.timer.stop()
        self.say("Restarting FreeCAD…")
        try:
            close_documents()
            update.remember_reopen(self.reopen_paths)
            update.spawn_restart(update.freecad_executable(), OPENER, freecad_python(), os.getpid())
        except Exception as e:  # noqa: BLE001
            self.progress.hide()
            self.status.setText(f"The new version is installed, but FreeCAD couldn't restart by "
                                f"itself ({e}). Close and open CADSwarm to use it.")
            self.after_stop = False
            self.set_busy(False)
            self.now.setText("Close")
            self.now.clicked.disconnect()
            self.now.clicked.connect(self.close)
            return
        self.progress.setRange(0, 0)
        self.status.setText("FreeCAD is reopening on the new version. Your work comes back with it.")
        QtCore.QTimer.singleShot(500, self.quit_freecad)

    def quit_freecad(self):
        try:
            Gui.getMainWindow().close()
            app = QtGui.QApplication.instance()
            if app is not None:
                app.quit()
        except Exception:
            pass
        # Everything is saved and closed; don't leave the old window (and the waiter) hanging.
        QtCore.QTimer.singleShot(12_000, lambda: os._exit(0))

    def discard(self, staged):
        if staged:
            shutil.rmtree(os.path.dirname(os.path.abspath(staged)), ignore_errors=True)

    def failed(self, message):
        self.timer.stop()
        self.progress.hide()
        self.status.setText(message + (" Your work is saved." if self.after_stop else ""))
        self.status.setObjectName("error")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.set_busy(False)
        self.now.setText("Try again" if not self.after_stop else "Close")
        if self.after_stop:
            try:
                self.now.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.now.clicked.connect(self.close)
            self.plan.setText("Your team server is stopped. Close this window, then reconnect from "
                              "the panel, or update again after reconnecting.")
        self.busy = False

    def closeEvent(self, event):
        if self.busy:
            event.ignore()  # downloading or installing: closing would leave a half-done update
            return
        if not self.installed_ok:
            # Closing the window means "not now": don't ask again in an hour about the same release.
            _postpone(self.release)
        super().closeEvent(event)

    def reject(self):
        if self.busy:
            return
        super().reject()


def _short_notes(text, limit=900):
    """Release notes as plain text, short enough for a small window."""
    text = re.sub(r"^\s*#+\s*", "", text or "", flags=re.MULTILINE)
    text = re.sub(r"\r\n?", "\n", text).strip()
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[:cut if cut > 200 else limit].rstrip() + "…"
