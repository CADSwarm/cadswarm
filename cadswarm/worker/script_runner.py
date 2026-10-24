"""Runs ONE agent part script, in a throwaway child process the worker has confined (see sandbox.py).

Standalone on purpose: stdlib plus FreeCAD, no imports from the rest of CadSwarm, so the worker can copy
this single file into the sandbox's read-only folder and the child needs access to nothing else.

Stdin carries one JSON request: {code, params, doc_path, freecad_bin, guards}.
Stdout carries JSON lines: {"started": true} once FreeCAD is loaded, then ONE reply
  {ok, brep, result, error, code}   (`brep` is the script's `shape`, or null).
The document at `doc_path` is a snapshot of the live model; whatever the script does to it is discarded
with the sandbox. The first layer here, the AST check and audit hook, gives agents readable errors; it is not
the security boundary.
"""
import ast
import json
import math
import os
import sys
import traceback

# Modules a part script may import: geometry and maths only. No os, shutil, subprocess,
# pathlib, socket, ctypes… — scripts build shapes; saving files goes through save_file,
# which never overwrites. This is a guard against accidents and naive misuse, not a
# security boundary (Python cannot be fully sandboxed in-process); the real boundary is the OS
# sandbox this script runs inside (see sandbox.py).
SCRIPT_MODULES = {"math", "cmath", "random", "itertools", "functools", "collections", "statistics",
                  "fractions", "decimal", "json", "re", "string", "dataclasses", "typing", "copy", "numbers",
                  "FreeCAD", "App", "Part", "Sketcher", "PartDesign", "Mesh", "MeshPart", "numpy", "BOPTools"}
# FreeCAD/OCC/numpy objects can write files themselves (shape.exportStep(path), doc.saveAs(path),
# arr.tofile(path)…), and allowed modules re-export dangerous ones (random._os, re.enum.sys…). So the
# guard walks the script's AST: it bans these names as *attributes* (comments and strings are inert),
# private/dunder attribute access, and the reflection builtins that would build a banned name at runtime.
SCRIPT_BANNED_ATTRS = {
    # writing files
    "save", "saveAs", "saveCopy", "write", "writelines", "writeInventor", "write_text", "write_bytes",
    "tofile", "savetxt", "savez", "savez_compressed", "memmap", "open_memmap", "DataSource", "dump",
    # escaping to the interpreter, the OS or other modules an allowed module happens to import
    "sys", "os", "modules", "builtins", "importlib", "import_module", "inspect", "subprocess", "shutil",
    "pathlib", "io", "ctypes", "ctypeslib", "socket", "f2py", "distutils", "system", "popen",
    "closeDocument", "setActiveDocument",
}
SCRIPT_DUNDER_OK = {"__init__", "__name__"}  # everything else dunder is how sandboxes get escaped
SCRIPT_BANNED_NAMES = {"getattr", "setattr", "delattr", "locals", "__import__", "__builtins__"}


def script_violation(code):
    """First thing in `code` the guard refuses, as 'line N: why'; None if it looks fine.

    Not a security boundary (see above). SyntaxErrors are left for compile() to report.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Attribute):
            attr = node.attr
            if attr in SCRIPT_BANNED_ATTRS or (attr.startswith("export") and not attr.endswith("ToString")):
                return f"line {line}: '.{attr}' (writes files or reaches outside the part)"
            if attr.startswith("_") and attr not in SCRIPT_DUNDER_OK and not (
                    isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")):
                return f"line {line}: private/dunder attribute '.{attr}'"
        elif isinstance(node, ast.Name):
            if node.id in SCRIPT_BANNED_NAMES or (node.id.startswith("__") and node.id not in SCRIPT_DUNDER_OK):
                return f"line {line}: '{node.id}'"
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in SCRIPT_BANNED_ATTRS or alias.name.startswith(("_", "export")):
                    return f"line {line}: importing '{alias.name}'"
    return None


SCRIPT_BLOCKED_BUILTINS = {"open", "exec", "eval", "compile", "input", "breakpoint", "help", "exit", "quit",
                           "globals", "vars", "memoryview", "__loader__", "__spec__"}


SCRIPT_RUNNING = []  # non-empty while agent code is executing
SCRIPT_AUDIT_BLOCKED = ("os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.truncate", "os.chmod",
                        "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile", "shutil.",
                        "subprocess.", "ctypes.", "socket.connect", "socket.bind")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _script_audit(event, args):
    """Backstop for what the AST check can't see: Python-level file/process access while a script runs."""
    if not SCRIPT_RUNNING:
        return
    if event == "open":
        mode, flags = (args[1], args[2]) if len(args) > 2 else (None, 0)
        if (isinstance(mode, str) and set(mode) & set("wax+")) or (flags or 0) & _WRITE_FLAGS:
            raise PermissionError("Part scripts can't write files")
    elif event.startswith(SCRIPT_AUDIT_BLOCKED):
        raise PermissionError(f"Part scripts can't use {event}")


def _script_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".")[0]
    if level or root not in SCRIPT_MODULES:
        raise ImportError(f"Part scripts can't import {name!r}. Allowed: {', '.join(sorted(SCRIPT_MODULES))}. "
                          "To save a file, use the save_file tool instead.")
    return __import__(name, globals, locals, fromlist, level)


def script_builtins():
    import builtins
    safe = {k: v for k, v in vars(builtins).items() if k not in SCRIPT_BLOCKED_BUILTINS}
    safe["__import__"] = _script_import
    return safe


def _reply(out, **fields):
    out.write(json.dumps({"ok": False, "brep": None, "result": None, "error": None, "code": None, **fields},
                         default=str) + "\n")
    out.flush()


def _agent_trace(code, exc):
    """The traceback trimmed to the agent's own lines: 'line N: source' for each frame in its code."""
    lines = code.splitlines()
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "<part_script>"]
    where = "".join(f"\n  line {f.lineno}: {lines[f.lineno - 1].strip() if 0 < f.lineno <= len(lines) else ''}"
                    for f in frames[-3:])
    detail = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, SyntaxError):
        return f"SyntaxError: {exc.msg} (line {exc.lineno})"
    return detail + where


def main():
    req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    # FreeCAD and OCC print to stdout from C++, past sys.stdout. Keep the reply on a private duplicate of
    # fd 1 and point fd 1 itself (and Python's stdout) at stderr.
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    for d in ([req["freecad_bin"], os.path.join(req["freecad_bin"], os.pardir, "lib")] if req.get("freecad_bin") else []):
        if os.path.isdir(d) and os.path.abspath(d) not in sys.path:  # bin/ on Windows, lib/ on macOS and Linux
            sys.path.append(os.path.abspath(d))
    import FreeCAD
    import Part
    out.write(json.dumps({"started": True}) + "\n")
    out.flush()

    code = req.get("code", "")
    guards = req.get("guards", True)
    if guards:
        bad = script_violation(code)
        if bad:
            return _reply(out, code="not_allowed", error=f"Part scripts can't do that ({bad}). Scripts build "
                          "shapes; use save_file for notes and export_model for 3D files; both never overwrite.")
    try:
        compiled = compile(code, "<part_script>", "exec")
        doc = FreeCAD.openDocument(req["doc_path"])  # the snapshot copy
        ns = {"App": FreeCAD, "FreeCAD": FreeCAD, "Part": Part, "doc": doc, "Vector": FreeCAD.Vector,
              "Placement": FreeCAD.Placement, "Rotation": FreeCAD.Rotation, "math": math,
              "params": req.get("params") or {}}
        if guards:
            ns["__builtins__"] = script_builtins()
            sys.addaudithook(_script_audit)
            SCRIPT_RUNNING.append(True)
        exec(compiled, ns)
        if guards:
            SCRIPT_RUNNING.pop()
    except MemoryError:
        return _reply(out, code="sandbox_limit", error="Script ran out of memory")
    except BaseException as e:  # agent scripts can raise anything
        if guards and SCRIPT_RUNNING:
            SCRIPT_RUNNING.pop()
        return _reply(out, code="script_error", error=_agent_trace(code, e))

    shape, fields = ns.get("shape"), {}
    try:
        if shape is not None:
            if not isinstance(shape, Part.Shape) or shape.isNull():
                return _reply(out, code="bad_args", error="`shape` must be a non-null Part.Shape")
            fields["brep"] = shape.exportBrepToString()
        if "result" in ns:
            fields["result"] = json.loads(json.dumps(ns["result"], default=str))
    except MemoryError:
        return _reply(out, code="sandbox_limit", error="Script ran out of memory")
    except Exception as e:
        return _reply(out, code="script_error", error=f"Could not return the result: {type(e).__name__}: {e}")
    _reply(out, ok=True, **fields)


if __name__ == "__main__":
    main()
