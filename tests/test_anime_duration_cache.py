import threading

from api import anime


def test_duration_cache_coalesces_concurrent_writers(monkeypatch):
    anime._dur_cache = {"episode": (1.0, 42.0)}
    anime._dur_cache_dirty = True
    calls = []
    first_started = threading.Event()
    release_first = threading.Event()

    def fake_write(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(2)

    monkeypatch.setattr(anime, "write_json_atomic", fake_write)

    first = threading.Thread(target=anime._save_dur_cache)
    first.start()
    assert first_started.wait(2)

    second = threading.Thread(target=anime._save_dur_cache)
    second.start()
    release_first.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(calls) == 1


def test_duration_cache_schedules_one_writer_for_a_burst(monkeypatch, tmp_path):
    anime._dur_cache = {}
    anime._dur_cache_dirty = False
    monkeypatch.setattr(anime, "_DUR_CACHE_PATH", tmp_path / "durations.json")
    monkeypatch.setattr(anime.subprocess, "check_output", lambda *a, **k: b"42.0\n")

    (tmp_path / "one.mkv").write_bytes(b"one")
    (tmp_path / "two.mkv").write_bytes(b"two")
    started = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            started.append((args, kwargs))

        def start(self):
            return None

    monkeypatch.setattr(anime.threading, "Thread", FakeThread)

    try:
        assert anime._video_duration(str(tmp_path / "one.mkv")) == 42.0
        assert anime._video_duration(str(tmp_path / "two.mkv")) == 42.0
        assert len(started) == 1
    finally:
        anime._dur_save_active = False
