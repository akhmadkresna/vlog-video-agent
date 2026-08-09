from __future__ import annotations

from pathlib import Path

from vlog_editor.cache import JsonCache, cache_key
from vlog_editor.media import sample_times


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
