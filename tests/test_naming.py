"""The shared model's tab is named after the person's project, not a generic product name."""
from cadswarm.naming import DEFAULT_MIRROR_LABEL, mirror_label


def test_the_shared_model_is_named_after_the_project():
    assert mirror_label("Racing Drone") == "Racing Drone (live, read-only)"


def test_an_unnamed_project_keeps_the_generic_label():
    assert mirror_label("") == DEFAULT_MIRROR_LABEL
    assert mirror_label(None) == DEFAULT_MIRROR_LABEL
    assert mirror_label("   ") == DEFAULT_MIRROR_LABEL


def test_extra_spaces_do_not_leak_into_the_tab():
    assert mirror_label("  Phone   stand ") == "Phone stand (live, read-only)"
