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


def test_rows_is_quiet_when_the_host_program_cannot_draw(monkeypatch, tmp_path):
    """No program, or one too old for `qr`: the panel still works, it just shows the link."""
    monkeypatch.setattr(qr.hosting, "host_bin", lambda: None)
    monkeypatch.setattr(qr.hosting, "ROOT", str(tmp_path))  # no checkout build to fall back to either
    qr._cache.clear()
    assert qr.rows("https://cadswarm.design/app/#t=ABC") == []
    assert qr.rows("") == []


def test_rows_falls_back_to_a_program_that_knows_qr(monkeypatch):
    """A running team locks the release binary on Windows, so a newer checkout build gets a turn."""
    monkeypatch.setattr(qr, "programs", lambda: iter(["C:/old/cadswarm-host.exe", "C:/new/cadswarm-host.exe"]))
    seen = []

    class Done:
        def __init__(self, code, out):
            self.returncode, self.stdout = code, out

    def fake_run(argv, **kwargs):
        seen.append(argv[0])
        if "old" in argv[0]:
            return Done(2, "error: unrecognized subcommand 'qr'")
        return Done(0, "000\n010\n000\n")

    monkeypatch.setattr(qr.subprocess, "run", fake_run)
    qr._cache.clear()
    assert qr.rows("https://cadswarm.design/app/#t=ABC") == ["000", "010", "000"]
    assert seen == ["C:/old/cadswarm-host.exe", "C:/new/cadswarm-host.exe"]
    assert qr.rows("https://cadswarm.design/app/#t=ABC") == ["000", "010", "000"]
    assert len(seen) == 2  # the second call was served from memory
