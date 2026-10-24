from cadswarm import ticket


def test_ticket_round_trips_case_insensitively_and_matches_the_host_format():
    t = {"v": 1, "uri": "http://127.0.0.1:3000", "db": "cadswarm", "token": "ab" * 32,
         "kind": "agent", "role": "member", "host_addr": ""}
    s = ticket.encode(t)
    assert s == s.upper() and "=" not in s
    assert ticket.decode(s) == t
    assert ticket.decode(s.lower() + "\n") == t


def test_tokens_hashes_and_ids():
    tok = ticket.new_token()
    assert len(tok) == 64 and int(tok, 16) >= 0
    assert ticket.hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    oid = ticket.new_id("o_")
    assert oid.startswith("o_") and len(oid) == 2 + 12
    assert len(ticket.new_id("t_")) == 2 + 8
