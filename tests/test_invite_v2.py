import base64
import json

import pytest

from cadswarm import ticket
from cadswarm.invite import InviteError, make_invite, read_invite

T = {"v": 1, "uri": "https://maincloud.spacetimedb.com", "db": "cadswarm-team-1a2b", "token": "ab" * 32,
     "kind": "human", "role": "member", "host_addr": ""}


def test_v2_code_round_trips_through_chat_noise():
    code = make_invite(T, "Phone stand")
    assert code.startswith("cadswarm-2:")
    got = read_invite(f"“{code[:30]}\n{code[30:]}” thanks!")
    assert got["token"] == T["token"] and got["db"] == T["db"] and got["project"] == "Phone stand"


def test_v2_code_returns_the_whole_ticket():
    assert read_invite(make_invite(T)) == {**T, "project": ""}


def test_base32_tickets_and_phone_links_are_accepted():
    b32 = ticket.encode(T)
    assert read_invite(b32)["token"] == T["token"]
    assert read_invite(f"https://cadswarm.app/#t={b32}")["uri"] == T["uri"]
    assert read_invite(f"join me: {b32[:40]}\n{b32[40:]} thanks")["db"] == T["db"]


def test_old_codes_get_a_clear_message():
    with pytest.raises(InviteError, match="older CadSwarm"):
        read_invite("cadswarm-1:eyJoIjoiaHR0cDovLzE5Mi4xNjguMS4yMDo4NzY1In0")


def test_a_code_without_a_database_is_damaged():
    bad = "cadswarm-2:" + base64.urlsafe_b64encode(json.dumps({"uri": "http://h", "token": "x"}).encode()).decode()
    with pytest.raises(InviteError, match="damaged"):
        read_invite(bad)


@pytest.mark.parametrize("text, fragment", [
    ("", "Paste the invite code"),
    ("http://192.168.1.20:8765", "doesn't look like a CadSwarm invite"),
    ("cadswarm-2:eyJ1cmkiOiJodHR0", "cut off"),
])
def test_errors_are_plain_language(text, fragment):
    with pytest.raises(InviteError) as e:
        read_invite(text)
    assert fragment in str(e.value)
