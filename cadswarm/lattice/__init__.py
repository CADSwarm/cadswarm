"""Lighten without losing stiffness: stress-graded lattices with a verified stiffness target.

Pipeline (see runner.py): voxelize the part, solve its load case on the voxel grid, grade the lattice so it
is thicker where the stress is high, scale the density until the verified stiffness meets the target, then
mesh the result and write STL/3MF and a report.

NumPy/SciPy only. Imported by the analysis subprocess (FreeCAD's Python) and by tests, never by the panel.
All lengths are mm, forces N, stresses MPa.
"""
CELLS = ("gyroid", "diamond", "schwarzP", "iwp", "bcc", "fcc", "octet", "cubic")
TPMS = ("gyroid", "diamond", "schwarzP", "iwp")
BEAMS = ("bcc", "fcc", "octet", "cubic")
