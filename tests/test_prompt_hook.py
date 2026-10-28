"""The prompt hook nudges Claude and Codex towards real surfaces (OpenCode reads AGENTS.md)."""
from cadswarm.prompt_hook import aesthetic_hint


def test_a_shape_request_gets_the_surface_hint():
    hint = aesthetic_hint("make the Lexus IS350 F Sport body look right, it is all flat")
    assert "surface_quality" in hint and "loft" in hint


def test_a_bracket_gets_no_hint():
    assert aesthetic_hint("a bracket for the shelf") == ""
