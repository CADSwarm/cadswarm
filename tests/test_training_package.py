"""Training code must not reach a user's FreeCAD, and must not drag torch into the app."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def test_the_windows_installer_excludes_training():
    assert "cadswarm\\training" in read(os.path.join("install", "install.ps1"))


def test_the_posix_installer_excludes_training():
    assert "cadswarm/training" in read(os.path.join("install", "install.sh"))


def test_importing_the_package_loads_no_heavy_dependency():
    code = ("import sys, cadswarm.training, cadswarm.training.eval_replay;"
            "print(sorted(m for m in ('FreeCAD', 'torch', 'transformers', 'peft') if m in sys.modules))")
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert got.stdout.strip() == "[]", got.stderr


def test_the_cli_has_the_three_subcommands():
    from cadswarm.training import cli
    assert {"export", "build", "eval"} <= set(cli.SUBCOMMANDS)
