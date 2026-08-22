from __future__ import annotations

from pathlib import Path

from vlog_editor.cache import JsonCache, cache_key
from vlog_editor.media import daypart_rank, local_clock, sample_times


def test_dji_filename_uses_local_clock_not_utc_capture_time() -> None:
    hour, minute = local_clock(
        "2026-08-10T12:53:28.000000Z",
        "DJI_20260810195327_0005_D.MP4",
    )
    assert (hour, minute) == (19, 53)
    assert daypart_rank(7) == 0
    assert daypart_rank(12) == 1
    assert daypart_rank(19) == 2
    assert daypart_rank(22) == 3


def test_sample_times_follow_clip_duration_policy() -> None:
    assert len(sample_times(10)) == 3
    assert len(sample_times(30)) == 5
    assert len(sample_times(75)) == 5
    assert len(sample_times(800)) == 12
    assert all(0 <= value <= 10 for value in sample_times(10))
    assert sample_times(800)[-1] > 790


def test_cache_key_changes_with_file_content_and_settings(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"first")
    first = cache_key(media, "vision", {"model": "a"})
    assert first == cache_key(media, "vision", {"model": "a"})
    assert first != cache_key(media, "vision", {"model": "b"})
    media.write_bytes(b"second")
    assert first != cache_key(media, "vision", {"model": "a"})


def test_json_cache_round_trip(tmp_path: Path) -> None:
    cache = JsonCache(tmp_path)
    assert cache.get("asr", "missing") is None
    cache.put("asr", "key", {"text": "hello"})
    assert cache.get("asr", "key") == {"text": "hello"}
