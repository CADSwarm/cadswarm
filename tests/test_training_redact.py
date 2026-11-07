"""Nothing machine-specific or private reaches a dataset file."""
from cadswarm.training import redact


def test_windows_backslash_paths_are_replaced():
    assert redact.text(r"model at C:\Users\waves\Documents\CadSwarm\Lexus IS350") == "model at <path>"


def test_windows_forward_slash_paths_are_replaced():
    assert redact.text("open C:/Users/waves/x.FCStd") == "open <path>"


def test_posix_home_paths_are_replaced():
    assert redact.text("read /home/waves/My Documents/x.FCStd") == "read <path>"


def test_emails_are_replaced():
    assert redact.text("ask waves@example.com about it") == "ask <email> about it"


def test_hex_tokens_are_replaced():
    assert redact.text("invite " + "a" * 64) == "invite <token>"


def test_uppercase_hex_tokens_are_replaced():
    assert redact.text("token " + "A" * 64) == "token <token>"


def test_urls_are_not_mistaken_for_drive_paths():
    assert redact.text("see https://example.com/a/b now") == "see https://example.com/a/b now"


def test_plain_text_is_untouched():
    assert redact.text("wall thickness 2.4 mm") == "wall thickness 2.4 mm"
