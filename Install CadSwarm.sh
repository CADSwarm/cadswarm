#!/bin/bash
# Linux: run me to install CadSwarm into FreeCAD:  bash "Install CadSwarm.sh"
# (or right-click > "Run as a Program" in your file manager).
if [ ! -t 0 ] && [ -z "${CADSWARM_NO_TERMINAL:-}" ]; then
    # Started from a file manager without a terminal: reopen in one so the questions can be answered.
    for term in x-terminal-emulator gnome-terminal konsole xfce4-terminal xterm; do
        if command -v "$term" >/dev/null; then
            [ "$term" = gnome-terminal ] && exec "$term" -- env CADSWARM_NO_TERMINAL=1 bash "$0" "$@"
            exec "$term" -e env CADSWARM_NO_TERMINAL=1 bash "$0" "$@"
        fi
    done
fi
bash "$(dirname "$(readlink -f "$0")")/install/install.sh" "$@"
status=$?
if [ $status -ne 0 ]; then echo; echo "CadSwarm setup did not finish. Please read the message above."; fi
[ -t 0 ] && { echo; read -r -p "Press Enter to close this window. " _; }
exit $status
