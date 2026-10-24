"""The chat box's @mentions: which helpers get a task, @team / @all, and the text the task keeps."""
from cadswarm.mentions import mention_prefix, parse_mentions, strip_mentions

HELPERS = ["lid-builder", "Bob-Codex"]


def test_mentions_pick_helpers_case_insensitively_in_order_without_repeats():
    helpers, special, unknown = parse_mentions("@bob-codex and @lid-builder, then @BOB-codex again", HELPERS)
    assert helpers == ["Bob-Codex", "lid-builder"] and special == set() and unknown == []


def test_team_all_and_unknown_names():
    assert parse_mentions("@team a phone stand", HELPERS)[1] == {"team"}
    assert parse_mentions("@all pause", HELPERS)[1] == {"all"}
    assert parse_mentions("@carol can you look?", HELPERS)[2] == ["carol"]


def test_an_email_address_is_not_a_mention():
    assert parse_mentions("send it to dan@example.com", HELPERS) == ([], set(), [])


def test_the_task_keeps_the_words_without_the_names():
    assert strip_mentions("@lid-builder make the lid 20 mm tall") == "make the lid 20 mm tall"
    assert strip_mentions("@a, @b: widen the slot") == "widen the slot"


def test_completion_starts_right_after_an_at_sign():
    assert mention_prefix("@") == ""
    assert mention_prefix("hey @li") == "li"
    assert mention_prefix("dan@exa") is None
    assert mention_prefix("@lid-builder make") is None
