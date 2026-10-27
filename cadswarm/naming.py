"""The names people see for a project and its shared model.

The team's shared model opens as a separate FreeCAD document. Its tab carries the
project's name (e.g. "Racing Drone") instead of a generic product name, so people
recognize the model they are working on. Stdlib only: shared by the GUI and tested
without FreeCAD.
"""

DEFAULT_MIRROR_LABEL = "CADSwarm Live (read-only mirror)"
MIRROR_DOC = "CadSwarmLive"  # the live model document's internal FreeCAD name


def is_mirror_doc(name):
    """Whether a FreeCAD document's internal name belongs to the shared model.

    FreeCAD never lets a document be renamed, so a mirror that was opened while another one still
    existed keeps a suffixed name (CadSwarmLive1). Those are the same document to us: treating a
    suffixed mirror as a different document is what opens a second, identical "read-only mirror" tab.
    """
    suffix = name[len(MIRROR_DOC):] if name else ""
    return name == MIRROR_DOC or (bool(name) and name.startswith(MIRROR_DOC) and suffix.isdigit())


def mirror_label(project):
    """The FreeCAD tab label for the team's shared model: the project name when there is one."""
    project = " ".join((project or "").split())
    return f"{project} (live, read-only)" if project else DEFAULT_MIRROR_LABEL
