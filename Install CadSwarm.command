#!/bin/sh
# Double-click me (macOS) or run me from a terminal (Linux) to install CadSwarm into FreeCAD.
cd "$(dirname "$0")" || exit 1
sh install/install.sh "$@"
status=$?
if [ "$status" -ne 0 ]; then
    echo
    echo "CadSwarm setup did not finish. Please read the message above."
fi
exit "$status"
