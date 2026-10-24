"""Optional real CalculiX C3D8 backend. Never substitutes another solver silently."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import numpy as np

from .fem import assemble, recover
from .model import SolverError


def find_ccx():
    explicit = os.environ.get("CADSWARM_CCX")
    if explicit:
        if not Path(explicit).is_file():
            raise SolverError("CADSWARM_CCX does not point to a file")
        return explicit
    hit = shutil.which("ccx")
    if hit:
        return hit
    for name in ("ccx.exe", "ccx"):
        bundled = Path(sys.executable).parent / name
        if bundled.is_file():
            return str(bundled)
    try:
        from ..config import find_freecad_python
        directory = Path(find_freecad_python()).parent
        for candidate in (directory / "ccx.exe", directory / "ccx"):
            if candidate.is_file():
                return str(candidate)
    except RuntimeError:
        pass
    raise SolverError("CalculiX unavailable. Install ccx and set CADSWARM_CCX to its executable, or explicitly select scipy.")


def input_deck(grid, material, system):
    _, nodes, elements, _, _, force, fixed, *_ = system
    lines = ["*HEADING", "CADSwarm linear elastic exact grid; mm N MPa", "*NODE,NSET=NALL"]
    for i, xyz in enumerate(nodes * grid.pitch_mm + grid.origin, 1):
        lines.append(f"{i}," + ",".join(f"{v:.16g}" for v in xyz))
    lines.append("*ELEMENT,TYPE=C3D8,ELSET=EALL")
    for i, elem in enumerate(elements + 1, 1):
        lines.append(f"{i}," + ",".join(map(str, elem)))
    lines += ["*MATERIAL,NAME=MAT", "*ELASTIC", f"{material.young_mpa:.16g},{material.poisson:.16g}",
              "*SOLID SECTION,ELSET=EALL,MATERIAL=MAT", "*BOUNDARY"]
    for node in np.unique(fixed // 3) + 1:
        lines.append(f"{node},1,3,0")
    lines += ["*STEP", "*STATIC", "*CLOAD"]
    for dof in np.flatnonzero(force):
        lines.append(f"{dof // 3 + 1},{dof % 3 + 1},{force[dof]:.16g}")
    lines += ["*NODE PRINT,NSET=NALL", "U", "*EL PRINT,ELSET=EALL", "S", "*END STEP"]
    return "\n".join(lines) + "\n"


def parse_dat(text, node_count, element_count):
    mode = None
    displacement, stress = {}, {}
    for line in text.splitlines():
        lower = line.lower()
        if "displacements (" in lower:
            mode = "u"
            continue
        if "stresses (" in lower:
            mode = "s"
            continue
        fields = line.split()
        if not fields or not fields[0].isdigit():
            continue
        try:
            if mode == "u" and len(fields) == 4:
                node = int(fields[0])
                if node in displacement:
                    raise SolverError("Duplicate displacement row")
                displacement[node] = [float(v.replace("D", "E")) for v in fields[1:]]
            elif mode == "s" and len(fields) == 8:
                key = (int(fields[0]), int(fields[1]))
                if key in stress:
                    raise SolverError("Duplicate stress row")
                stress[key] = [float(v.replace("D", "E")) for v in fields[2:]]
        except ValueError as exc:
            raise SolverError(f"Invalid CalculiX results: {exc}") from exc
    if set(displacement) != set(range(1, node_count + 1)):
        raise SolverError("CalculiX did not return every node's displacement")
    expected = {(e, g) for e in range(1, element_count + 1) for g in range(1, 9)}
    if set(stress) != expected:
        raise SolverError("CalculiX did not return all eight integration-point stresses per element")
    u = np.array([displacement[i] for i in range(1, node_count + 1)]).ravel()
    s = np.array([[stress[e, g] for g in range(1, 9)] for e in range(1, element_count + 1)])
    if not np.isfinite(u).all() or not np.isfinite(s).all():
        raise SolverError("Nonfinite CalculiX output")
    return u, s


class CalculixSolver:
    name = "CalculiX C3D8 (2x2x2 integration)"

    def __init__(self, executable=None, timeout=90, evidence_dir=None):
        self.executable = executable or find_ccx()
        self.timeout = timeout
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None
        self.calls = 0

    def solve(self, grid, material, loads, supports):
        system = assemble(grid, material, loads, supports)
        self.calls += 1
        with tempfile.TemporaryDirectory(prefix="cadswarm-ccx-") as tmp:
            directory = Path(tmp)
            (directory / "analysis.inp").write_text(input_deck(grid, material, system), encoding="ascii")
            try:
                proc = subprocess.run([self.executable, "-i", "analysis"], cwd=tmp, capture_output=True,
                                      text=True, errors="replace", timeout=self.timeout,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                      env=dict(os.environ, OMP_NUM_THREADS="1"))
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SolverError(f"CalculiX could not complete: {exc}") from exc
            log = proc.stdout + "\n" + proc.stderr
            (directory / "solver.log").write_text(log, encoding="utf-8")
            if self.evidence_dir:
                dest = self.evidence_dir / f"solve-{self.calls:03d}"
                dest.mkdir(parents=True, exist_ok=False)
                for path in directory.iterdir():
                    if path.suffix in (".inp", ".dat", ".sta", ".log"):
                        shutil.copy2(path, dest / path.name)
            if proc.returncode or re.search(r"\*ERROR|singular|not converg", log, re.I) or "Job finished" not in log:
                raise SolverError(f"CalculiX failed (exit {proc.returncode}): {log[-1500:]}")
            path = directory / "analysis.dat"
            if not path.is_file():
                raise SolverError("CalculiX finished without results")
            u, s = parse_dat(path.read_text(), len(system[1]), len(system[0]))
        result = recover(grid, system, u, self.name, printed_digits=7)
        # Use actual solver stresses for constraints; recovery also independently checks equilibrium.
        xx, yy, zz, xy, xz, yz = np.moveaxis(s, -1, 0)
        vm = np.sqrt(((xx-yy)**2 + (yy-zz)**2 + (zz-xx)**2)/2 + 3*(xy*xy+xz*xz+yz*yz))
        if not np.allclose(result.element_stress[tuple(system[0].T)], vm.max(axis=1), rtol=1e-3, atol=1e-6):
            raise SolverError("CalculiX stress disagrees with independent displacement-based recovery")
        result.element_stress[tuple(system[0].T)] = vm.max(axis=1)
        result.max_stress_mpa = float(vm.max())
        return result
