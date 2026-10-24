"""boards/boards.json keeps the shape plan 00 defines (the host seeds Params from it)."""
import json
import os

from cadswarm.paths import ROOT

BOARDS = json.load(open(os.path.join(ROOT, "boards", "boards.json"), encoding="utf-8"))


def test_every_board_has_the_contract_fields():
    assert "esp32-s3-devkitc-1" in BOARDS
    for bid, b in BOARDS.items():
        assert bid == bid.lower() and " " not in bid
        assert isinstance(b["label"], str) and b["label"]
        assert len(b["pcb"]) == 3 and all(v > 0 for v in b["pcb"])
        assert all(len(h) == 2 for h in b["holes"])
        assert b["hole_d"] >= 0 and b["clear_top"] > 0 and b["clear_bottom"] >= 0
        assert (b["hole_d"] > 0) == bool(b["holes"])
        for p in b["ports"]:
            assert p["side"] in ("-x", "+x", "-y", "+y")
            assert len(p["center"]) == 2 and len(p["size"]) == 2
            assert p["name"].replace("_", "").isalnum() and p["name"] == p["name"].lower()
            edge = b["pcb"][1] if p["side"] in ("-x", "+x") else b["pcb"][0]
            assert 0 <= p["center"][0] <= edge, (bid, p["name"])
        assert isinstance(b["verified"], bool)
