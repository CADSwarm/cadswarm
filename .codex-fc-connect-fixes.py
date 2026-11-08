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
    "cadswarm/agent_setup.py",
    '''    if client not in CLIENTS:
        raise ValueError(f"client must be one of {sorted(CLIENTS)}")
''',
    '''    if client not in CLIENTS and client != "custom":
        raise ValueError(f"client must be one of {sorted(CLIENTS)} or 'custom'")
''',
)
replace(
    "cadswarm/agent_setup.py",
    '''    writers = {"claude": setup_claude, "codex": setup_codex, "opencode": setup_opencode}
    written = writers[client](project, python)
''',
    '''    writers = {"claude": setup_claude, "codex": setup_codex, "opencode": setup_opencode}
    # A generic MCP client has no standard config-file format. Redeem its invite and print
    # the server entry from the CLI so the user can add it to that client's settings.
    written = writers[client](project, python) if client in writers else []
''',
)

replace(
    "cadswarm/cli.py",
    "from .paths import ROOT\n",
    "from .paths import LAUNCHER, ROOT\n",
)
replace(
    "cadswarm/cli.py",
    '''    try:
        t = read_invite(a.invite)
        written = write_agent_config(project, a.client, t, a.name, sys.executable, a.fresh, ephemeral=a.ephemeral,
                                     model=a.model)
''',
    '''    try:
        t = read_invite(a.invite)
        from .hosting import deps_ready, install_deps
        if not deps_ready(sys.executable):
            print("Installing the CADSwarm agent runtime…", flush=True)
            install_deps(sys.executable)
        written = write_agent_config(project, a.client, t, a.name, sys.executable, a.fresh, ephemeral=a.ephemeral,
                                     model=a.model)
''',
)
replace(
    "cadswarm/cli.py",
    '''    if a.client == "claude":
        print("Next: open this folder and run `claude`; the cadswarm MCP server joins the team on start.")
    elif a.client == "opencode":
        print("Next: run `opencode` here; it loads opencode.json and joins the team on start.")
    else:
        print("Next: run `codex` here. Codex only loads .codex/config.toml for TRUSTED projects: accept the "
              "trust prompt, or add to ~/.codex/config.toml:\\n"
              f"  [projects.{json.dumps(project)}]\\n  trust_level = \\"trusted\\"")
        print("Then, in Codex, type /hooks and trust the CADSwarm hook. Codex skips new or changed hooks until "
              "you do, so without this your helper won't see its team's updates. Do it again after updating CADSwarm.")
''',
    '''    if a.client == "custom":
        print("Next: add this MCP server to your agent's settings, then start it in this project folder:")
        print(json.dumps({"mcpServers": {"cadswarm": {
            "command": sys.executable, "args": [LAUNCHER, "mcp"], "env": {"CADSWARM_PROJECT": project}}}},
            indent=2))
    elif a.client == "claude":
        print("Next: open this folder and run `claude`; the cadswarm MCP server joins the team on start.")
    elif a.client == "opencode":
        print("Next: run `opencode` here; it loads opencode.json and joins the team on start.")
    else:
        print("Next: run `codex` here. Codex only loads .codex/config.toml for TRUSTED projects: accept the "
              "trust prompt, or add to ~/.codex/config.toml:\\n"
              f"  [projects.{json.dumps(project)}]\\n  trust_level = \\"trusted\\"")
        print("Then, in Codex, type /hooks and trust the CADSwarm hook. Codex skips new or changed hooks until "
              "you do, so without this your helper won't see its team's updates. Do it again after updating CADSwarm.")
''',
)
replace(
    "cadswarm/cli.py",
    's.add_argument("--client", choices=["claude", "codex", "opencode"], required=True)',
    's.add_argument("--client", choices=["claude", "codex", "opencode", "custom"], required=True)',
)

remote_old = '''def remote_instructions(client, code, name, python="python", launcher="cadswarm_launch.py",
                        folder="~/cadswarm-helper"):
    """The exact steps to connect an agent on another computer: install, join with the invite, run it.

    Returns {"setup": the one command that joins (it writes the agent's MCP config), "run": how to start the agent,
    "mcp": the MCP server entry as JSON for agents configured by hand, "steps": the numbered steps as text}."""
    cli_client = client if client in ("claude", "codex", "opencode") else "opencode"
    setup = (f"cadswarm setup --client {cli_client} --invite {code} --name {name} --dir {folder}")
    run = {"claude": "claude", "codex": "codex", "opencode": "opencode"}.get(client, "<your agent's command>")
    mcp = json.dumps({"mcpServers": {"cadswarm": mcp_server_entry(python, launcher, folder)}}, indent=2)
    steps = [
        "Install CADSwarm on that computer (Install CadSwarm.cmd / .command / .sh from the download).",
        f"In a terminal there, run:\\n{setup}",
        f"Start the agent in that folder: cd {folder} && {run}",
    ]
    if client not in ("claude", "codex", "opencode"):
        steps.append("For any other MCP agent, add this server to its MCP settings (after step 2):\\n" + mcp)
    return {"setup": setup, "run": f"cd {folder} && {run}", "mcp": mcp,
            "steps": "\\n\\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))}
'''
remote_new = '''def remote_instructions(client, code, name, python="python", launcher="cadswarm_launch.py",
                        folder=None, platform="linux"):
    """Commands for joining another device. The installers place a small wrapper beside the app copy, so
    helpers do not need a separately installed `cadswarm` command. `platform` is windows, macos, or linux."""
    cli_client = client if client in ("claude", "codex", "opencode", "custom") else "opencode"
    windows = platform.lower() in ("windows", "win", "win32")
    folder = folder or (f"$HOME\\CadSwarm\\{name}" if windows else f"$HOME/CadSwarm/{name}")
    setup_exe = ".\\cadswarm.cmd" if windows else "./cadswarm"
    setup = (f'{setup_exe} setup --client {cli_client} --invite {code} --name {name} --dir "{folder}"')
    agent = {"claude": "claude", "codex": "codex", "opencode": "opencode"}.get(client, "<your agent command>")
    run = f'Set-Location "{folder}"; {agent}' if windows else f'cd "{folder}" && {agent}'
    mcp = json.dumps({"mcpServers": {"cadswarm": mcp_server_entry(python, launcher, folder)}}, indent=2)
    install_dir = "the CADSwarm install folder (the one containing cadswarm.cmd)" if windows else \\
        "the CADSwarm install folder (the one containing cadswarm)"
    steps = [
        "Install CADSwarm on that computer, then open PowerShell/a terminal in " + install_dir + ".",
        f"Run this one-time setup command:\\n{setup}",
    ]
    if client == "custom":
        steps.append("The setup command prints the exact cadswarm MCP server entry. Add it to your agent's MCP "
                     "settings, then start that agent in the project folder.")
    else:
        steps.append(f"Start the agent in that project folder:\\n{run}")
    return {"setup": setup, "run": run, "mcp": mcp,
            "steps": "\\n\\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))}
'''
replace("cadswarm/gui/team_view.py", remote_old, remote_new)

replace(
    "cadswarm/gui/agents_page.py",
    "import os\nimport time\n",
    "import os\nimport sys\nimport time\n",
)
replace(
    "cadswarm/gui/agents_page.py",
    '''        form.addWidget(self.remote_name, 1)
        lay.addLayout(form)
        self.make = QtGui.QPushButton("Make connection code")
''',
    '''        form.addWidget(self.remote_name, 1)
        lay.addLayout(form)
        target_row = QtGui.QHBoxLayout()
        target_row.addWidget(QtGui.QLabel("Other device"))
        self.remote_platform = QtGui.QComboBox()
        for key, label in (("windows", "Windows"), ("macos", "macOS"), ("linux", "Linux")):
            self.remote_platform.addItem(label, key)
        local_platform = "windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux"
        self.remote_platform.setCurrentIndex(self.remote_platform.findData(local_platform))
        target_row.addWidget(self.remote_platform, 1)
        lay.addLayout(target_row)
        self.make = QtGui.QPushButton("Make connection code")
''',
)
replace(
    "cadswarm/gui/agents_page.py",
    '''        mcp_note = QtGui.QLabel("Every CADSwarm agent talks to the team through the <b>cadswarm</b> MCP server "
                                "(stdio). This is the entry an MCP agent's settings need, for a folder that ran "
                                "<i>cadswarm setup</i>:")
''',
    '''        mcp_note = QtGui.QLabel("Every CADSwarm agent talks to the team through the <b>cadswarm</b> MCP server "
                                "(stdio). This config uses this computer's CADSwarm install paths. For another "
                                "device, the setup command prints its own MCP entry:")
''',
)
replace(
    "cadswarm/gui/agents_page.py",
    '''        self.instructions.update(team_view.remote_instructions(client, code, name, folder=f"~/cadswarm-{name}"))
''',
    '''        self.instructions.update(team_view.remote_instructions(
            client, code, name, platform=self.remote_platform.currentData()))
''',
)

replace(
    "tests/test_team_view.py",
    '''def test_remote_instructions_are_exact():
    r = tv.remote_instructions("claude", "cadswarm-2:abc", "dave-laptop", python="py", launcher="L.py",
                               folder="~/h")
    assert r["setup"] == "cadswarm setup --client claude --invite cadswarm-2:abc --name dave-laptop --dir ~/h"
    assert r["run"] == "cd ~/h && claude"
    assert json.loads(r["mcp"]) == {"mcpServers": {"cadswarm": {"command": "py", "args": ["L.py", "mcp"],
                                                                 "env": {"CADSWARM_PROJECT": "~/h"}}}}
    custom = tv.remote_instructions("custom", "c", "n")
    assert "--client opencode" in custom["setup"] and "mcpServers" in custom["steps"]
''',
    '''def test_remote_instructions_use_installed_setup_wrappers_for_each_platform():
    r = tv.remote_instructions("claude", "cadswarm-2:abc", "dave-laptop", python="py", launcher="L.py",
                               folder="$HOME\\\\h", platform="windows")
    assert r["setup"] == '.\\\\cadswarm.cmd setup --client claude --invite cadswarm-2:abc --name dave-laptop --dir "$HOME\\\\h"'
    assert r["run"] == 'Set-Location "$HOME\\\\h"; claude'
    assert json.loads(r["mcp"]) == {"mcpServers": {"cadswarm": {"command": "py", "args": ["L.py", "mcp"],
                                                                 "env": {"CADSWARM_PROJECT": "$HOME\\\\h"}}}}
    unix = tv.remote_instructions("codex", "code", "laptop", platform="linux")
    assert "./cadswarm setup --client codex" in unix["setup"]
    assert 'cd "$HOME/CadSwarm/laptop" && codex' == unix["run"]
    custom = tv.remote_instructions("custom", "c", "n", platform="macos")
    assert "--client custom" in custom["setup"] and "prints the exact cadswarm MCP server entry" in custom["steps"]
''',
)

replace(
    "tests/test_setup_space.py",
    '''def test_cli_setup_takes_any_invite_form(tmp_path, monkeypatch, capsys, code):
    calls = []
    monkeypatch.setattr(agent_setup, "redeem", fake_redeem(calls))
''',
    '''def test_cli_setup_takes_any_invite_form(tmp_path, monkeypatch, capsys, code):
    from cadswarm import hosting
    monkeypatch.setattr(hosting, "deps_ready", lambda _python: True)
    calls = []
    monkeypatch.setattr(agent_setup, "redeem", fake_redeem(calls))
''',
)
new_test = '''

def test_custom_mcp_setup_prints_server_entry_without_editing_another_clients_config(tmp_path, monkeypatch, capsys):
    from cadswarm import hosting
    monkeypatch.setattr(hosting, "deps_ready", lambda _python: True)
    calls = []
    monkeypatch.setattr(agent_setup, "redeem", fake_redeem(calls))
    cli.main(["setup", "--client", "custom", "--invite", ticket.encode(T), "--name", "rover-mcp",
              "--dir", str(tmp_path)])
    output = capsys.readouterr().out
    assert calls == [("ab" * 32, "rover-mcp", "custom")]
    assert "MCP server entry" in output and '"CADSWARM_PROJECT"' in output
    cfg = json.load(open(tmp_path / ".cadswarm" / "config.json", encoding="utf-8"))
    assert cfg["client"] == "custom" and cfg["name"] == "rover-mcp"
    assert not (tmp_path / ".mcp.json").exists()
'''
path = ROOT / "tests/test_setup_space.py"
raw = path.read_bytes()
newline = b"\r\n" if raw.count(b"\r\n") > 100 else b"\n"
text = raw.decode("utf-8").replace("\r\n", "\n")
marker = '\n\ndef test_cli_setup_explains_a_bad_invite(tmp_path):'
if marker not in text:
    raise SystemExit("setup test insertion point not found")
path.write_bytes(text.replace(marker, new_test + marker, 1).replace("\n", newline.decode("ascii")).encode("utf-8"))

replace(
    "install/install.ps1",
    '''# ---------------------------------------------------------------- 3. Start menu and desktop shortcuts
''',
    '''# The CLI shim runs setup through the same FreeCAD Python used by the desktop app.
$SetupWrapper = "@echo off`r`n`"$Python`" `"%~dp0cadswarm_launch.py`" %*`r`n"
[IO.File]::WriteAllText((Join-Path $InstallDir "cadswarm.cmd"), $SetupWrapper, [Text.Encoding]::ASCII)

# ---------------------------------------------------------------- 3. Start menu and desktop shortcuts
''',
)

replace(
    "install/install.sh",
    '''quote_args() { local out="" a; for a in "$@"; do out+="\\\"${a//\\\"/\\\\\\\"}\\\" "; done; printf '%s' "${out% }"; }
xml_escape() { local t="${1//&/&amp;}"; t="${t//</&lt;}"; printf '%s' "${t//>/&gt;}"; }
''',
    '''quote_args() { local out="" a; for a in "$@"; do out+="\\\"${a//\\\"/\\\\\\\"}\\\" "; done; printf '%s' "${out% }"; }
shell_quote() { local q=${1//\\'/\\'\\\\\\'\\'}; printf "'%s'" "$q"; }
write_setup_wrapper() {
    local arg
    {
        printf '#!/bin/sh\\nexec'
        for arg in "${PY[@]}"; do printf ' %s' "$(shell_quote "$arg")"; done
        printf ' %s "$@"\\n' "$(shell_quote "$DEST/cadswarm_launch.py")"
    } > "$DEST/cadswarm" || die "Couldn't write the CADSwarm setup command."
    chmod 700 "$DEST/cadswarm"
}
xml_escape() { local t="${1//&/&amp;}"; t="${t//</&lt;}"; printf '%s' "${t//>/&gt;}"; }
''',
)
replace(
    "install/install.sh",
    '''        [ -f "$ICON_SVG" ] && echo "Icon=$ICON_SVG" || echo "Icon=org.freecad.FreeCAD"
''',
    '''        [ -f "$ICON_SVG" ] && echo "Icon=$ICON_SVG" || echo "Icon=org.freecad.FreeCAD"
''',
)
replace(
    "install/install.sh",
    '''            <key>CFBundleCategoryType</key><string>public.app-category.graphics-design</string>
''',
    '''            <key>CFBundleCategoryType</key><string>public.app-category.graphics-design</string>
''',
)
