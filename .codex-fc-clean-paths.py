from pathlib import Path

p = Path(r"C:\Users\sachc\Projects\cadswarm-fc\cadswarm\gui\team_view.py")
raw = p.read_bytes()
newline = b"\r\n" if raw.count(b"\r\n") > 100 else b"\n"
text = raw.decode("utf-8").replace("\r\n", "\n")
text = text.replace('folder = folder or (f"$HOME\\CadSwarm\\{name}" if windows else f"$HOME/CadSwarm/{name}")',
                    'folder = folder or f"$HOME/CadSwarm/{name}"')
text = text.replace('setup_exe = ".\\cadswarm.cmd" if windows else "./cadswarm"',
                    'setup_exe = r".\\cadswarm.cmd" if windows else "./cadswarm"')
if 'folder = folder or (f"$HOME\\CadSwarm\\{name}"' in text or 'setup_exe = ".\\cadswarm.cmd"' in text:
    raise SystemExit("The Windows command strings still contain invalid Python escapes")
p.write_bytes(text.replace("\n", newline.decode("ascii")).encode("utf-8"))
