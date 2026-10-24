"""Optional structural analysis; importing CADSwarm never imports numerical dependencies."""


def optimize_part(part, material, loads, supports, constraints, **options):
    """Optimize a Grid, or a FreeCAD shape/object with an explicit pitch_mm.

    Returns an OptimizationResult. Never modifies a document or publishes a model.
    Units throughout: mm, N, MPa and kg/mm³.
    """
    from .engine import optimize
    from .geometry import Grid

    if not isinstance(part, Grid):
        from .freecad import discretize
        part = discretize(getattr(part, "Shape", part), options.pop("pitch_mm"))
    return optimize(part, material, loads, supports, constraints, **options)
