"""Background throttling: a minimised or hidden helper runner must keep its pace."""
import os
import sys

from cadswarm.power import keep_running_in_background


def test_the_opt_out_is_safe_to_call_and_idempotent():
    first = keep_running_in_background()
    assert keep_running_in_background() is first
    if os.name != "nt":
        assert first is False, "no Windows API elsewhere"
    if sys.platform == "win32":
        assert first is True, "Windows 8+ accepts the power-throttling opt-out"
