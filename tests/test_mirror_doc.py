"""A suffixed live-model document and the generic label are recognised as the shared model."""
from cadswarm.naming import DEFAULT_MIRROR_LABEL, MIRROR_DOC, is_mirror_doc, mirror_label


def test_the_live_model_is_the_plain_name_or_a_suffixed_one():
    assert is_mirror_doc(MIRROR_DOC)
    assert is_mirror_doc("CadSwarmLive1")
    assert is_mirror_doc("CadSwarmLive012")


def test_other_documents_are_not_the_live_model():
    for name in ("MyDrone", "CadSwarmPointersDemo", "CadSwarmLightenSource", "CadSwarmLiveDraft", "", None):
        assert not is_mirror_doc(name), name


def test_the_label_still_names_the_project():
    assert mirror_label("Racing Drone") == "Racing Drone (live, read-only)"
    assert mirror_label("") == DEFAULT_MIRROR_LABEL
