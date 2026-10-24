#!/bin/bash
# macOS: double-click me to install CadSwarm into FreeCAD. (If macOS says it can't be opened,
# right-click this file, choose Open, then Open again.)
bash "$(dirname "$0")/install/install.sh" "$@"
status=$?
if [ $status -ne 0 ]; then echo; echo "CadSwarm setup did not finish. Please read the message above."; fi
echo; read -r -p "Press Enter to close this window. " _
exit $status
