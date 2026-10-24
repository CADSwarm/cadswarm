#!/bin/sh
# CadSwarm installer for macOS and Linux. Run "Install CadSwarm.command" (double-click on macOS),
# or `sh install/install.sh` from a terminal.
# Copies this folder into FreeCAD's add-on folder, optionally adds a launcher,
# then opens FreeCAD in CadSwarm, where a step-by-step guide takes over.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"  # the checkout root

NO_LAUNCH=0
for arg in "$@"; do
    case "$arg" in
        --no-launch) NO_LAUNCH=1 ;;  # install only (used for testing)
    esac
done

# Colours only when someone is watching.
if [ -t 1 ]; then
    GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m'); RED=$(printf '\033[31m')
    CYAN=$(printf '\033[36m'); GRAY=$(printf '\033[90m'); OFF=$(printf '\033[0m')
else
    GREEN=; YELLOW=; RED=; CYAN=; GRAY=; OFF=
fi

say() {
    case "${2:-}" in
        green)  _c=$GREEN  ;;
        yellow) _c=$YELLOW ;;
        red)    _c=$RED    ;;
        cyan)   _c=$CYAN   ;;
        gray)   _c=$GRAY   ;;
        *)      _c=        ;;
    esac
    printf '%s%s%s\n' "$_c" "$1" "$OFF"
}

finish() {  # pause so a double-clicked window doesn't vanish, then exit
    if [ "${CADSWARM_YES:-0}" != "1" ] && [ -t 0 ]; then
        printf '\n'
        printf 'Press Enter to close this window'
        read -r _ || true
    fi
    exit "$1"
}

confirm() {  # CADSWARM_YES=1 (or no terminal) answers yes
    if [ "${CADSWARM_YES:-0}" = "1" ] || [ ! -t 0 ]; then return 0; fi
    printf "  Add a 'CadSwarm' shortcut to your desktop? (Y/n) "
    _answer=''
    read -r _answer || true
    case "$_answer" in
        [nN]*) return 1 ;;
        *)     return 0 ;;
    esac
}

say ''
say '  CadSwarm setup' cyan
say "  Build 3D designs together with your team's AI helpers." gray
say ''

OS=$(uname -s)
PYTHON=''; FREECAD_EXE=''; FREECAD_APP=''

# 1. Find FreeCAD
find_freecad_linux() {
    # (a) tarballs (<FreeCAD>/bin) and extracted AppImages (<FreeCAD>/usr/bin) ship their own Python
    for _bin in /opt/FreeCAD*/bin /opt/freecad*/bin /opt/FreeCAD*/usr/bin \
                "$HOME"/Applications/FreeCAD*/bin "$HOME"/Applications/FreeCAD*/usr/bin \
                "$HOME"/.local/share/FreeCAD*/bin "$HOME"/.local/share/FreeCAD*/usr/bin; do
        [ -d "$_bin" ] || continue
        _py=''
        for _c in python python3; do [ -x "$_bin/$_c" ] && { _py="$_bin/$_c"; break; }; done
        [ -n "$_py" ] || continue
        _exe=''
        for _c in freecad FreeCAD; do [ -x "$_bin/$_c" ] && { _exe="$_bin/$_c"; break; }; done
        [ -n "$_exe" ] || continue
        PYTHON=$_py; FREECAD_EXE=$_exe
        return 0
    done
    # (b) distro package: runs on the system python3
    _fc=$(command -v freecad 2>/dev/null || true)
    [ -n "$_fc" ] || _fc=$(command -v FreeCAD 2>/dev/null || true)
    if [ -n "$_fc" ]; then
        for _lib in /usr/lib/freecad/lib /usr/lib/freecad-python3/lib \
                    /usr/lib64/freecad/lib /usr/local/lib/freecad/lib; do
            if [ -f "$_lib/FreeCAD.so" ]; then
                _py=$(command -v python3 2>/dev/null || true)
                if [ -n "$_py" ]; then PYTHON=$_py; FREECAD_EXE=$_fc; return 0; fi
            fi
        done
    fi
    return 1
}

if [ "$OS" = "Darwin" ]; then
    # Newest version wins; sort by the number in the bundle name, since as text "1.9" would beat "1.10".
    _lines=''
    for _dir in /Applications "$HOME/Applications"; do
        for _app in "$_dir"/FreeCAD*.app; do
            [ -d "$_app" ] || continue
            _ver=$(basename "$_app" .app | sed -n 's/.*[^0-9.]\([0-9][0-9.]*\).*/\1/p')
            [ -n "$_ver" ] || _ver=0  # FreeCAD.app with no number counts as lowest
            _lines="$_lines$_ver $_app
"
        done
    done
    if [ -n "$_lines" ]; then
        if sort -V </dev/null >/dev/null 2>&1; then
            _best=$(printf '%s' "$_lines" | sort -V -k1,1 | tail -n 1)
        else
            _best=$(printf '%s' "$_lines" | sort -k1,1 | tail -n 1)
        fi
        FREECAD_APP=${_best#* }
        PYTHON="$FREECAD_APP/Contents/Resources/bin/python"
        FREECAD_EXE="$FREECAD_APP/Contents/MacOS/FreeCAD"
    fi
else
    if ! find_freecad_linux; then
        # (c) FreeCAD may be here, but not in a shape whose Python we can run.
        _odd=''
        if command -v flatpak >/dev/null 2>&1 &&
           { flatpak info org.freecad.FreeCAD >/dev/null 2>&1 ||
             flatpak info org.freecadweb.FreeCAD >/dev/null 2>&1; }; then
            _odd=1
        fi
        if [ -z "$_odd" ] && command -v snap >/dev/null 2>&1 && snap list freecad >/dev/null 2>&1; then
            _odd=1
        fi
        if [ -z "$_odd" ]; then
            for _f in "$HOME"/Applications/*.AppImage "$HOME"/Downloads/*.AppImage; do
                [ -f "$_f" ] && { _odd=1; break; }
            done
        fi
        if [ -n "$_odd" ]; then
            say 'CadSwarm needs a FreeCAD whose own Python it can run.' yellow
            say "Please install FreeCAD from your distro's package manager (for example"
            say "'sudo apt install freecad' or 'sudo dnf install freecad'), or extract the"
            say "AppImage into ~/Applications/FreeCAD with '--appimage-extract', then run"
            say 'this installer again.'
            finish 1
        fi
        say "FreeCAD isn't installed on this computer yet." yellow
        say 'Opening the FreeCAD download page. Install it (the default options are fine),'
        say "then run 'Install CadSwarm' again."
        if command -v xdg-open >/dev/null 2>&1; then
            xdg-open 'https://www.freecad.org/downloads.php' >/dev/null 2>&1 || true
        fi
        finish 1
    fi
fi

if [ -z "$PYTHON" ]; then  # macOS with no FreeCAD*.app
    say "FreeCAD isn't installed on this computer yet." yellow
    say 'Opening the FreeCAD download page. Install it (the default options are fine),'
    say "then double-click 'Install CadSwarm' again."
    if command -v open >/dev/null 2>&1; then
        open 'https://www.freecad.org/downloads.php' >/dev/null 2>&1 || true
    fi
    finish 1
fi

if [ -n "$FREECAD_APP" ]; then
    say "  [ok] Found FreeCAD: $FREECAD_APP" green
else
    say "  [ok] Found FreeCAD: $FREECAD_EXE" green
fi

# 2. Where FreeCAD keeps add-ons for this user
USERDIR=$("$PYTHON" -c "import sys,os; b=os.path.dirname(sys.executable); sys.path += [b, os.path.join(os.path.dirname(b),'lib'), '/usr/lib/freecad/lib', '/usr/lib/freecad-python3/lib', '/usr/lib64/freecad/lib', '/usr/local/lib/freecad/lib']; import FreeCAD; print(FreeCAD.getUserAppDataDir())" 2>/dev/null | tail -n 1) || true
if [ -z "$USERDIR" ]; then
    say "Couldn't ask FreeCAD where its add-ons go." red
    finish 1
fi
DEST="${USERDIR%/}/Mod/CadSwarm"

if pgrep -x FreeCAD >/dev/null 2>&1 || pgrep -x freecad >/dev/null 2>&1; then
    say 'FreeCAD is open. Please close it (save your work first), then press Enter.' yellow
    if [ "${CADSWARM_YES:-0}" != "1" ] && [ -t 0 ]; then read -r _ || true; fi
fi

# 3. Copy CadSwarm into FreeCAD
# A clean copy each time (updates included). Only code lives here: projects are in Documents.
# If this script was started from the installed copy there is nothing to copy (and deleting $DEST
# would delete the files we are running from).
_same=0
if [ -d "$DEST" ]; then
    _a=$(cd "$HERE" && pwd -P) || true
    _b=$(cd "$DEST" && pwd -P) || true
    [ -n "$_a" ] && [ "$_a" = "$_b" ] && _same=1
fi

if [ "$_same" = "1" ]; then
    say '  [ok] CadSwarm is already installed in FreeCAD' green
else
    rm -rf "$DEST"
    mkdir -p "$DEST"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a \
            --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
            --exclude 'cadswarm-data' --exclude 'exports' --exclude '.cadswarm' \
            --exclude '*.egg-info' --exclude '*.pyc' --exclude 'node_modules' --exclude 'target' \
            "$HERE/" "$DEST/" \
            || { say "Copying CadSwarm into FreeCAD failed (rsync code $?)." red; finish 1; }
    else
        ( cd "$HERE" && tar --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
              --exclude='.pytest_cache' --exclude='cadswarm-data' --exclude='exports' \
              --exclude='.cadswarm' --exclude='*.egg-info' --exclude='*.pyc' \
              --exclude='node_modules' --exclude='target' -cf - . ) | ( cd "$DEST" && tar -xf - ) \
            || { say "Copying CadSwarm into FreeCAD failed (tar code $?)." red; finish 1; }
    fi
    chmod +x "$DEST/install/install.sh"
    [ -f "$DEST/Install CadSwarm.command" ] && chmod +x "$DEST/Install CadSwarm.command"
    say '  [ok] Installed CadSwarm into FreeCAD' green
fi

OPENER="$DEST/cadswarm/gui/open_cadswarm.py"

# 4. Optional launcher that opens FreeCAD straight into CadSwarm
if confirm; then
    if [ "$OS" = "Darwin" ]; then
        mkdir -p "$HOME/Desktop"
        cat > "$HOME/Desktop/CadSwarm.command" <<EOF
#!/bin/sh
open -a "$FREECAD_APP" --args "$OPENER"
EOF
        chmod +x "$HOME/Desktop/CadSwarm.command"
    else
        mkdir -p "$HOME/.local/share/applications"
        _lnk="$HOME/.local/share/applications/cadswarm.desktop"
        cat > "$_lnk" <<EOF
[Desktop Entry]
Type=Application
Name=CadSwarm
Comment=Build 3D designs with your team's AI helpers
Exec="$FREECAD_EXE" "$OPENER"
Icon=freecad
Terminal=false
Categories=Graphics;Engineering;
EOF
        chmod +x "$_lnk"
        if [ -d "$HOME/Desktop" ]; then
            cp "$_lnk" "$HOME/Desktop/cadswarm.desktop"
            chmod +x "$HOME/Desktop/cadswarm.desktop"
            gio set "$HOME/Desktop/cadswarm.desktop" metadata::trusted true 2>/dev/null || true
        fi
    fi
    say '  [ok] Added a CadSwarm shortcut to your desktop' green
fi

say ''
if [ "$NO_LAUNCH" = "1" ]; then
    say 'All set! Open FreeCAD and pick CadSwarm to see the guide.' cyan
else
    say 'All set! FreeCAD will open now and show you the CadSwarm guide.' cyan
fi
say '(The first time FreeCAD starts it may ask about language and theme; any choice is fine.)' gray
if [ "$NO_LAUNCH" = "0" ]; then
    if [ "$OS" = "Darwin" ]; then
        open -a "$FREECAD_APP" --args "$OPENER"
    else
        nohup "$FREECAD_EXE" "$OPENER" >/dev/null 2>&1 &
    fi
fi
exit 0
