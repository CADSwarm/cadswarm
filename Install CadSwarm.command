#!/bin/bash
# macOS launcher retained for clarity while the standalone release is Windows-first.
bash "$(dirname "$0")/install/install.sh" "$@"
status=$?
if [ $status -ne 0 ]; then echo; echo "CadSwarm setup did not finish. Please read the message above."; fi
echo; read -r -p "Press Enter to close this window. " _
exit $status
