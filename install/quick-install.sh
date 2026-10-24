#!/usr/bin/env bash
# One-line CadSwarm install for macOS and Linux:
#   curl -fsSL https://raw.githubusercontent.com/SachchitBalamurugan/cadswarm/main/install/quick-install.sh | bash
# Downloads CadSwarm, then runs install/install.sh (which asks its questions on the terminal).
# Options after `bash -s --` are passed on, e.g. `| bash -s -- --yes`. CADSWARM_REF picks a branch or tag;
# CADSWARM_TARBALL_URL downloads from somewhere else entirely (a mirror, or a file:// path when offline).
set -eu
REPO="SachchitBalamurugan/cadswarm"
REF="${CADSWARM_REF:-main}"
command -v curl >/dev/null || { echo "Please install curl first." >&2; exit 1; }
command -v tar >/dev/null || { echo "Please install tar first." >&2; exit 1; }
TMP="$(mktemp -d "${TMPDIR:-/tmp}/cadswarm-install.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
echo "Downloading CadSwarm ($REF)…"
curl -fsSL "${CADSWARM_TARBALL_URL:-https://codeload.github.com/$REPO/tar.gz/$REF}" | tar -xzf - -C "$TMP"
SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
[ -f "$SRC/install/install.sh" ] || { echo "The download didn't contain the installer." >&2; exit 1; }
bash "$SRC/install/install.sh" "$@"
