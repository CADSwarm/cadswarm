from pathlib import Path

ROOT = Path(r"C:\Users\sachc\Projects\cadswarm-fc")


def replace(rel, old, new):
    path = ROOT / rel
    raw = path.read_bytes()
    newline = b"\r\n" if raw.count(b"\r\n") > 100 else b"\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    old = old.replace("\r\n", "\n")
    new = new.replace("\r\n", "\n")
    if old not in text:
        raise SystemExit(f"Expected text not found in {rel}: {old[:120]!r}")
    path.write_bytes(text.replace(old, new, 1).replace("\n", newline.decode("ascii")).encode("utf-8"))


replace(
    "install/install.sh",
    'ICON_SVG="$DEST/cadswarm/gui/cadswarm.svg"\n',
    'ICON_SVG="$DEST/cadswarm/gui/cadswarm.svg"\nif [ "${#PY[@]}" -gt 0 ]; then write_setup_wrapper; fi\n',
)

old_install = '''else
    # Start from an empty staging folder: a failed earlier run must not leave old files in the copy.
    rm -rf "$DEST.new" && mkdir -p "$DEST.new" || die "Couldn't create $DEST"
    (cd "$HERE" && tar -cf - --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache \\
        --exclude=cadswarm-data --exclude=exports --exclude=.cadswarm --exclude='*.egg-info' --exclude='*.pyc' \\
        --exclude=node_modules --exclude=target .) | (cd "$DEST.new" && tar -xf -) || die "Copying CADSwarm into FreeCAD failed."
    WASM="$HERE/module/target/wasm32-unknown-unknown/release/cadswarm_module.wasm"
    # A local build wins; otherwise use the module the installer put beside the code, so a fresh
    # workbench never falls back to a stale download in the app folder.
    [ -f "$WASM" ] || WASM="$HERE/module/cadswarm_module.wasm"
    [ -f "$WASM" ] && cp "$WASM" "$DEST.new/module/cadswarm_module.wasm"  # the team server's database module
    rm -rf "$DEST" && mv "$DEST.new" "$DEST" || die "Couldn't replace the old CADSwarm in $DEST"
    ok "Installed CADSwarm into FreeCAD ($DEST)"
fi
'''
new_install = '''else
    # Never erase an unknown add-on or a previous install until a complete staged replacement is ready.
    is_cadswarm_install() {
        local path="$1" real
        if [ -L "$path" ]; then real="$(readlink -f "$path" 2>/dev/null || true)"; path="${real:-$path}"; fi
        [ -f "$path/InitGui.py" ] && [ -f "$path/cadswarm/gui/panel.py" ]
    }
    if { [ -e "$DEST" ] || [ -L "$DEST" ]; } && ! is_cadswarm_install "$DEST"; then
        die "$DEST already exists and isn't a CADSwarm install; it was left untouched."
    fi
    mkdir -p "$(dirname "$DEST")" || die "Couldn't create the FreeCAD add-on folder."
    STAGE="$(mktemp -d "${DEST}.stage.XXXXXX")" || die "Couldn't create a staging folder for CADSwarm."
    if ! (cd "$HERE" && tar -cf - --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache \\
        --exclude=cadswarm-data --exclude=exports --exclude=.cadswarm --exclude='*.egg-info' --exclude='*.pyc' \\
        --exclude=node_modules --exclude=target .) | (cd "$STAGE" && tar -xf -); then
        rm -rf "$STAGE"
        die "Copying CADSwarm into FreeCAD failed."
    fi
    WASM="$HERE/module/target/wasm32-unknown-unknown/release/cadswarm_module.wasm"
    [ -f "$WASM" ] || WASM="$HERE/module/cadswarm_module.wasm"
    [ -f "$WASM" ] && cp "$WASM" "$STAGE/module/cadswarm_module.wasm"
    BACKUP=""
    if [ -e "$DEST" ] || [ -L "$DEST" ]; then
        BACKUP="${DEST}.old.$$"
        if [ -e "$BACKUP" ] || [ -L "$BACKUP" ] || ! mv "$DEST" "$BACKUP"; then
            rm -rf "$STAGE"
            die "Couldn't safely stage the previous CADSwarm install."
        fi
    fi
    if mv "$STAGE" "$DEST"; then
        [ -z "$BACKUP" ] || rm -rf "$BACKUP"
    else
        [ -z "$BACKUP" ] || mv "$BACKUP" "$DEST"
        rm -rf "$STAGE"
        die "Couldn't replace the old CADSwarm in $DEST; the previous install was restored."
    fi
    ok "Installed CADSwarm into FreeCAD ($DEST)"
fi
'''
replace("install/install.sh", old_install, new_install)

replace(
    "install/install.sh",
    '''    <key>LSApplicationCategoryType</key><string>public.app-category.graphics-design</string>
    <key>CADSwarmInstallDir</key><string>$(xml_escape "$DEST")</string>
''',
    '''    <key>LSApplicationCategoryType</key><string>public.app-category.graphics-design</string>
    <key>CFBundleDocumentTypes</key>
    <array><dict>
        <key>CFBundleTypeName</key><string>CADSwarm design</string>
        <key>CFBundleTypeExtensions</key><array><string>FCStd</string><string>fcstd</string></array>
        <key>CFBundleTypeRole</key><string>Editor</string>
        <key>LSHandlerRank</key><string>Alternate</string>
    </dict></array>
    <key>CADSwarmInstallDir</key><string>$(xml_escape "$DEST")</string>
''',
)

replace(
    "install/install.sh",
    '''        } > "$APPS/cadswarm.desktop"
        chmod +x "$APPS/cadswarm.desktop"
        command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1
''',
    '''        } > "$APPS/cadswarm.desktop"
        chmod +x "$APPS/cadswarm.desktop"
        MIME_DIR="$DATA_HOME/mime/packages"
        mkdir -p "$MIME_DIR"
        cat > "$MIME_DIR/cadswarm-fcstd.xml" <<'MIME'
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-extension-fcstd">
    <comment>CADSwarm design</comment>
    <glob pattern="*.FCStd"/>
    <glob pattern="*.fcstd"/>
  </mime-type>
</mime-info>
MIME
        command -v update-mime-database >/dev/null && update-mime-database "$DATA_HOME/mime" >/dev/null 2>&1
        command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1
''',
)
