"""Reuse the existing guarded part-script operation for accepted output only."""
import re


def accepted_operation(source_name, source_revision, source_brep, candidate_brep, name, summary):
    """No networking. The caller invokes this only after a human accepts the preview.

    Compare both the revision and actual source shape in the serialized worker.
    New-name revision zero prevents replacing even an object owned by this user.
    """
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", name) or name == source_name:
        raise ValueError("Choose a new alphanumeric object name starting with a letter")
    if type(source_revision) is not int or source_revision < 1:
        raise ValueError("A current source revision is required")
    if summary.get("status") != "improved" or not summary.get("protected_cells_preserved"):
        raise ValueError("Only a passing improvement can be accepted")
    # Re-serialization of a BREP may change whitespace/metadata; compare OCC geometry
    # through mutual difference volumes in addition to the hub's exact revision check.
    code = f"""if doc.getObject({name!r}) is not None:
    raise ValueError('Output name already exists')
source = doc.getObject({source_name!r})
if source is None:
    raise ValueError('Source no longer exists')
expected = Part.Shape()
expected.importBrepFromString({source_brep!r})
if source.Shape.cut(expected).Volume > 1e-7 or expected.cut(source.Shape).Volume > 1e-7:
    raise ValueError('Source geometry changed; rerun optimization')
shape = Part.Shape()
shape.importBrepFromString({candidate_brep!r})
if not shape.isValid() or len(shape.Solids) != 1:
    raise ValueError('Candidate is not a valid solid')
result = {{'structural_report': {summary!r}}}
"""
    return {"op": "run_part_script", "args": {"name": name, "label": name, "code": code},
            "expected_revs": {source_name: source_revision, name: 0}}
