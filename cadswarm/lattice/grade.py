"""Stress field to lattice density: thicker where the part works hard, thinner where it does not.

The solid part's stress, smoothed over about one cell (a lattice cannot follow detail finer than its cell),
sets the shape of the density field; one global scale `s` sets its level, and the optimizer chooses `s`.
Two floors apply everywhere: the thinnest printable wall, and the density at which the cell's calibrated
strength still carries the local stress with the requested safety factor.
"""
import numpy as np
from scipy import ndimage

from . import calibration


def resample(values, src, dst, order=1):
    """Values on grid `src` sampled at the voxel centres of grid `dst` (both VoxelGrids)."""
    axes = [((dst.centres(a) - src.origin[a]) / src.pitch - .5) for a in range(3)]
    coords = np.meshgrid(*axes, indexing="ij", sparse=False)
    return ndimage.map_coordinates(np.asarray(values, float), coords, order=order, mode="nearest")


def smooth_stress(stress, part, sigma_voxels):
    """Fill empty voxels with the nearest stress, then blur; returned on the same grid (MPa)."""
    s = np.asarray(stress, float)
    valid = np.isfinite(s)
    if not valid.any():
        return np.zeros_like(s)
    idx = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    filled = s[tuple(idx)]
    out = ndimage.gaussian_filter(filled, sigma=max(sigma_voxels, 0.0), mode="nearest") if sigma_voxels > 0 \
        else filled
    out[~part] = 0.0
    return out


def bounds(cell, cell_size, t_min, t_max):
    """(rho_lo, rho_hi) from the printable wall range."""
    return (float(calibration.density(cell, t_min / cell_size)),
            float(calibration.density(cell, t_max / cell_size)))


def density(stress, lattice, cell, cell_size, t_min, t_max, scale, yield_mpa=None, safety=None,
            exponent=None):
    """Relative density on the grid for the voxels in `lattice` (0 elsewhere).

    stress: smoothed solid-part von Mises (MPa). scale: the optimizer's global level.
    """
    rho_lo, rho_hi = bounds(cell, cell_size, t_min, t_max)
    s = np.where(lattice, stress, 0.0)
    ref = np.percentile(s[lattice], 95) if lattice.any() else 0.0
    q = np.where(lattice, s / ref, 0.0) if ref > 0 else np.where(lattice, 1.0, 0.0)
    n = exponent or calibration.table()["cells"][cell]["strength"]["n"]
    rho = scale * np.power(np.maximum(q, 0.0), 1.0 / n)
    if yield_mpa and safety:
        need = calibration.density_for_strength(cell, np.where(lattice, s * safety / yield_mpa, 0.0))
        rho = np.maximum(rho, need)
    rho = np.clip(rho, rho_lo, rho_hi)
    return np.where(lattice, rho, 0.0)


def thickness(rho, cell, cell_size):
    """Wall or strut thickness (mm) for a density field."""
    return calibration.thickness_ratio(cell, rho) * cell_size
