"""FreeCAD adapter: exact grid classification, B-Rep reconstruction, no document mutation."""
import math
import numpy as np

from .geometry import Grid, MAX_CELLS
from .model import StructuralError, positive


def discretize(shape, pitch_mm):
    """Accept only exact unions of grid cells, rejecting approximation of CAD boundaries.

    OCC intersection volume classifies each cell. Comparing occupied volume with CAD
    volume also prevents small missed holes/features from passing silently.
    """
    import FreeCAD as App
    import Part
    positive(pitch_mm, "pitch_mm")
    if shape.isNull() or not shape.isValid() or len(shape.Solids) != 1:
        raise StructuralError("Select one valid solid")
    bb = shape.BoundBox
    lengths = [bb.XLength, bb.YLength, bb.ZLength]
    dims = [round(v / pitch_mm) for v in lengths]
    if any(n < 1 or not math.isclose(n * pitch_mm, v, abs_tol=1e-7, rel_tol=1e-9) for n, v in zip(dims, lengths)):
        raise StructuralError("Bounding dimensions must be exact multiples of pitch_mm")
    if math.prod(dims) > MAX_CELLS:
        raise StructuralError(f"Grid exceeds {MAX_CELLS} cells; increase pitch_mm")
    origin = (bb.XMin, bb.YMin, bb.ZMin)
    mask = np.zeros(dims, dtype=bool)
    cell_volume = pitch_mm ** 3
    tolerance = max(1e-9, cell_volume * 1e-8)
    for cell in np.ndindex(*dims):
        base = App.Vector(*[origin[i] + cell[i] * pitch_mm for i in range(3)])
        cube = Part.makeBox(pitch_mm, pitch_mm, pitch_mm, base)
        volume = shape.common(cube).Volume
        if abs(volume - cell_volume) <= tolerance:
            mask[cell] = True
        elif volume > tolerance:
            raise StructuralError("Unsupported curved, oblique or off-grid boundary. No approximate geometry was analyzed.")
    grid = Grid(mask, pitch_mm, origin)
    if not math.isclose(grid.volume_mm3, shape.Volume, rel_tol=1e-8, abs_tol=1e-7):
        raise StructuralError("Grid/CAD volume mismatch; refine pitch or use a conforming FEM mesher")
    return grid


def preserve_boxes(grid, boxes):
    """Retain every occupied cell intersecting explicit world-coordinate [min,max] boxes."""
    keep = np.zeros_like(grid.mask)
    for box in boxes:
        limits = np.asarray(box, dtype=float)
        if limits.shape != (2, 3) or not np.isfinite(limits).all() or np.any(limits[1] <= limits[0]):
            raise StructuralError("Each preserve box needs finite [[xmin,ymin,zmin],[xmax,ymax,zmax]] with positive dimensions")
        selected = np.zeros_like(keep)
        for cell in np.argwhere(grid.mask):
            low = np.asarray(grid.origin) + cell * grid.pitch_mm
            high = low + grid.pitch_mm
            if np.all(high > limits[0]) and np.all(low < limits[1]):
                selected[tuple(cell)] = True
        if not selected.any():
            raise StructuralError("A preserve box does not intersect the solid")
        keep |= selected
    return keep


def reconstruct(source_shape, original, optimized):
    import FreeCAD as App
    import Part
    if original.mask.shape != optimized.mask.shape or original.origin != optimized.origin or original.pitch_mm != optimized.pitch_mm:
        raise StructuralError("Candidate grid does not match source grid")
    if np.any(optimized.mask & ~original.mask):
        raise StructuralError("Optimization must not add material")
    removed = original.mask & ~optimized.mask
    # Merge removed cells into x-runs to reduce the number of OCC Boolean tools.
    boxes = []
    for y in range(removed.shape[1]):
        for z in range(removed.shape[2]):
            x = 0
            while x < removed.shape[0]:
                if not removed[x, y, z]:
                    x += 1
                    continue
                start = x
                while x < removed.shape[0] and removed[x, y, z]:
                    x += 1
                p = original.pitch_mm
                base = App.Vector(*[original.origin[i] + c * p for i, c in enumerate((start, y, z))])
                boxes.append(Part.makeBox((x-start)*p, p, p, base))
    result = source_shape.copy()
    if boxes:
        result = result.cut(Part.makeCompound(boxes)).removeSplitter()
    if result.isNull() or not result.isValid() or len(result.Solids) != 1:
        raise StructuralError("Reconstructed candidate is not a valid single CAD solid")
    if not math.isclose(result.Volume, optimized.volume_mm3, rel_tol=1e-8, abs_tol=1e-6):
        raise StructuralError("Reconstructed CAD volume differs from analyzed volume")
    return result
