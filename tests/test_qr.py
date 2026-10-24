"""The QR the Remote tab paints: rows come from the host program, and a broken one is never shown."""
from cadswarm import qr


def test_phone_url_puts_the_ticket_in_the_fragment():
    from cadswarm import ticket

    assert ticket.phone_url("https://cadswarm.design/app", "ABC") == "https://cadswarm.design/app/#t=ABC"
    assert ticket.phone_url("https://cadswarm.design/app/", "ABC") == "https://cadswarm.design/app/#t=ABC"
    assert ticket.phone_url("http://localhost:5173", "ABC") == "http://localhost:5173/#t=ABC"


def test_parse_accepts_a_square_grid_and_rejects_anything_else():
    grid = "000\n010\n000"
    assert qr.parse(grid + "\n") == ["000", "010", "000"]
    assert qr.parse("000\n010\n00") == []          # ragged: not a code
    assert qr.parse("000\n0x0\n000") == []         # not modules
    assert qr.parse("error: nope") == []
    assert qr.parse("") == []


def test_rows_is_quiet_when_the_host_program_cannot_draw(monkeypatch):
    """No program, or one too old for `qr`: the panel still works, it just shows the link."""
    monkeypatch.setattr(qr.hosting, "host_bin", lambda: None)
    assert qr.rows("https://cadswarm.design/app/#t=ABC") == []
    assert qr.rows("") == []
