"""Explicit engineering inputs. Material and boundary conditions are never inferred."""
from dataclasses import dataclass
import math


class StructuralError(ValueError):
    pass


class SolverError(StructuralError):
    pass


class Cancelled(StructuralError):
    pass


def positive(value, name):
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise StructuralError(f"{name} must be finite and positive")


SIDES = tuple(f"{axis}_{end}" for axis in "xyz" for end in ("min", "max"))


@dataclass(frozen=True)
class Material:
    name: str
    young_mpa: float
    poisson: float
    yield_mpa: float
    density_kg_mm3: float

    def __post_init__(self):
        if not self.name.strip():
            raise StructuralError("An explicit material name is required")
        for name in ("young_mpa", "yield_mpa", "density_kg_mm3"):
            positive(getattr(self, name), name)
        if not math.isfinite(self.poisson) or not -0.9 < self.poisson < 0.45:
            raise StructuralError("Poisson ratio must be between -0.9 and 0.45; nearly incompressible materials unsupported")


@dataclass(frozen=True)
class Load:
    side: str
    force_n: tuple[float, float, float]

    def __post_init__(self):
        if self.side not in SIDES:
            raise StructuralError("Load side must be an axis extreme face: " + ", ".join(SIDES))
        if len(self.force_n) != 3 or not all(math.isfinite(v) for v in self.force_n):
            raise StructuralError("Force must contain three finite components in N")
        if not any(self.force_n):
            raise StructuralError("A nonzero load is required")


@dataclass(frozen=True)
class Support:
    side: str

    def __post_init__(self):
        if self.side not in SIDES:
            raise StructuralError("Support side must be an axis extreme face")


@dataclass(frozen=True)
class Constraints:
    max_stress_mpa: float
    max_displacement_mm: float
    safety_factor: float
    target_reduction: float = 0.1
    max_stress_ratio: float = 1.25
    max_displacement_ratio: float = 1.15
    skin_cells: int = 1
    max_trials: int = 24

    def __post_init__(self):
        for name in ("max_stress_mpa", "max_displacement_mm", "safety_factor",
                     "max_stress_ratio", "max_displacement_ratio"):
            positive(getattr(self, name), name)
        if self.safety_factor < 1 or min(self.max_stress_ratio, self.max_displacement_ratio) < 1:
            raise StructuralError("Safety factor and relative limits must be at least 1")
        if not 0 < self.target_reduction < 1:
            raise StructuralError("Target reduction must be between 0 and 1")
        for name, high in (("skin_cells", 10), ("max_trials", 100)):
            v = getattr(self, name)
            if type(v) is not int or not 1 <= v <= high:
                raise StructuralError(f"{name} must be an integer from 1 to {high}")

    def stress_limit(self, material):
        return min(self.max_stress_mpa, material.yield_mpa / self.safety_factor)
