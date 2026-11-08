import os
import time

from cadswarm.app import instance


def test_second_instance_keeps_the_live_channel_and_files_are_forwarded(tmp_path):
    first = instance.Server(str(tmp_path))
    second = instance.Server(str(tmp_path))
    assert first.start()
    try:
        assert not second.start()
        path = str(tmp_path / "sample.FCStd")
        assert instance.forward(str(tmp_path), [path])
        deadline = time.monotonic() + 3
        batches = []
        while time.monotonic() < deadline and not batches:
            batches = first.drain()
            if not batches:
                time.sleep(0.01)
        assert batches == [[os.path.abspath(path)]]
    finally:
        second.close()
        first.close()
