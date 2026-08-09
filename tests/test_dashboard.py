from __future__ import annotations

from pathlib import Path

from vlog_editor.dashboard import _thumbnail_path, format_clock


def test_thumbnail_cache_key_tracks_source_and_selected_range(tmp_path: Path) -> None:
    dashboard = tmp_path / "dashboard"
    source = tmp_path / "clip.mov"
    source.write_bytes(b"first version")

    original = _thumbnail_path(dashboard, source, start=10, end=20)
    assert original == _thumbnail_path(dashboard, source, start=10, end=20)
    assert original != _thumbnail_path(dashboard, source, start=11, end=20)

    source.write_bytes(b"changed source content")
    changed = _thumbnail_path(dashboard, source, start=10, end=20)
    assert changed != original


def test_different_sources_never_share_positional_thumbnail(tmp_path: Path) -> None:
    dashboard = tmp_path / "dashboard"
    first = tmp_path / "first.mov"
    second = tmp_path / "second.mov"
    first.write_bytes(b"same bytes")
    second.write_bytes(b"same bytes")

    first_thumb = _thumbnail_path(dashboard, first, start=0, end=5)
    second_thumb = _thumbnail_path(dashboard, second, start=0, end=5)
    assert first_thumb != second_thumb


def test_format_clock_uses_edit_timeline_units() -> None:
    assert format_clock(0) == "0:00"
    assert format_clock(65) == "1:05"
    assert format_clock(3661) == "1:01:01"
