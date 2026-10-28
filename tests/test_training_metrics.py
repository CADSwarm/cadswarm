"""Geometry and completion metrics, and the cheap critic score the learned one must beat."""
from cadswarm.training import critic, metrics


def test_jaccard_handles_empty_sets():
    assert metrics.jaccard(set(), set()) == 1.0
    assert metrics.jaccard({"a"}, {"b"}) == 0.0
    assert metrics.jaccard({"a", "b"}, {"b", "c"}) == round(1 / 3, 4)


def test_bbox_iou_is_one_for_identical_boxes():
    assert metrics.bbox_iou([0, 0, 0, 10, 10, 10], [0, 0, 0, 10, 10, 10]) == 1.0
    assert metrics.bbox_iou([0, 0, 0, 10, 10, 10], [0, 0, 0, 5, 10, 10]) == 0.5


def test_volume_ratio_is_symmetric_log_error():
    assert metrics.volume_ratio(10, 10) == 1.0
    assert metrics.volume_ratio(10, 20) == metrics.volume_ratio(20, 10)
    assert metrics.volume_ratio(0, 10) == 0.0


def test_geometry_compares_names_boxes_and_volumes():
    reference = {"objects": {"A": {"bbox": [0, 0, 0, 1, 1, 1], "volume": 1.0}}}
    candidate = {"objects": {"A": {"bbox": [0, 0, 0, 1, 1, 2], "volume": 2.0}}}
    got = metrics.geometry(reference, candidate)
    assert got["names"] == 1.0 and got["bbox"] == 0.5 and got["volume"] < 1.0


def test_geometry_reports_none_when_no_box_or_volume_was_recorded():
    got = metrics.geometry({"objects": {"A": {"volume": 1.0}}},
                           {"objects": {"A": {"volume": 1.0}}})
    assert got["bbox"] is None and got["volume"] == 1.0
    empty = metrics.geometry({"objects": {}}, {"objects": {}})
    assert empty["bbox"] is None and empty["volume"] is None


def test_aggregate_averages_and_ignores_missing_values():
    got = metrics.aggregate([{"completion": 1.0, "geometry": {"bbox": 1.0}},
                             {"completion": 0.0, "geometry": {"bbox": 0.0}}])
    assert got["completion"] == 0.5 and got["bbox"] == 0.5


def test_the_heuristic_critic_rewards_a_printable_known_solid():
    solid = {"objects": {"A": {"known": True, "volume": 1.0}}}
    assert critic.heuristic(solid, printable=True) > critic.heuristic(solid, printable=False)
    assert critic.heuristic({"objects": {}}) == 0.0
