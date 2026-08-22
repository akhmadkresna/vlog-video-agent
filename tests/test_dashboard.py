from __future__ import annotations

import copy
from pathlib import Path

from vlog_editor.dashboard import _thumbnail_path, format_clock, generate_dashboard
from vlog_editor.project import DEFAULT_CONFIG, Episode, write_json


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


def test_dashboard_shows_time_skip_card_at_interval(tmp_path: Path, monkeypatch) -> None:
    for name in ("footage", "work", "output"):
        (tmp_path / name).mkdir()
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        (tmp_path / "footage" / name).write_bytes(b"fake")
    monkeypatch.setattr("vlog_editor.dashboard.thumbnail", lambda *a, **k: None)
    config = copy.deepcopy(DEFAULT_CONFIG)
    # Small interval so a 3-clip, 9s-total plan actually crosses a mark
    # without landing in the skipped last-stretch window.
    config["transitions"] = {"enabled": True, "interval_sec": 5.0, "card_duration": 1.0}
    episode = Episode(tmp_path, config)
    write_json(
        episode.analysis_path,
        {
            "clips": [
                {"metadata": {"filename": name, "duration": 3}, "audio": {"words": []}, "visual": {}}
                for name in ("a.mp4", "b.mp4", "c.mp4")
            ]
        },
    )
    plan = {
        "title": "Test",
        "duration_sec": 9.0,
        "structure": [
            {
                "section": "Body",
                "description": "",
                "clips": [
                    {"file": "a.mp4", "start": 0.0, "end": 3.0, "note": "", "subtitle": ""},
                    {"file": "b.mp4", "start": 0.0, "end": 3.0, "note": "", "subtitle": ""},
                    {"file": "c.mp4", "start": 0.0, "end": 3.0, "note": "", "subtitle": ""},
                ],
            }
        ],
    }
    write_json(episode.plan_path, plan)
    output = generate_dashboard(episode, open_browser=False)
    html_text = output.read_text(encoding="utf-8")
    assert 'class="time-skip"' in html_text
    assert "later" in html_text
    # The card lands after b.mp4 (cumulative 6s >= the 5s mark), before c.mp4.
    b_pos = html_text.index("b.mp4")
    skip_pos = html_text.index('class="time-skip"')
    c_pos = html_text.index("c.mp4")
    assert b_pos < skip_pos < c_pos


def test_dashboard_shows_sfx_chip_on_matching_clip(tmp_path: Path, monkeypatch) -> None:
    for name in ("footage", "work", "output"):
        (tmp_path / name).mkdir()
    (tmp_path / "footage" / "a.mp4").write_bytes(b"fake")
    (tmp_path / "footage" / "b.mp4").write_bytes(b"fake")
    monkeypatch.setattr("vlog_editor.dashboard.thumbnail", lambda *a, **k: None)
    config = copy.deepcopy(DEFAULT_CONFIG)
    episode = Episode(tmp_path, config)
    write_json(
        episode.analysis_path,
        {
            "clips": [
                {"metadata": {"filename": "a.mp4", "duration": 10}, "audio": {"words": []}, "visual": {}},
                {"metadata": {"filename": "b.mp4", "duration": 10}, "audio": {"words": []}, "visual": {}},
            ]
        },
    )
    plan = {
        "title": "Test",
        "duration_sec": 10.0,
        "structure": [
            {
                "section": "Body",
                "description": "",
                "clips": [
                    {"file": "a.mp4", "start": 0.0, "end": 5.0, "note": "", "subtitle": ""},
                    {"file": "b.mp4", "start": 0.0, "end": 5.0, "note": "", "subtitle": ""},
                ],
            }
        ],
        "audio_cues": [
            {"at_sec": 2.0, "type": "boing", "reason": "mid-clip energy beat"},
            {"at_sec": 7.0, "type": "sparkle", "reason": "kids-interest hype moment"},
        ],
    }
    write_json(episode.plan_path, plan)
    output = generate_dashboard(episode, open_browser=False)
    html_text = output.read_text(encoding="utf-8")
    assert html_text.count('class="sfx-chip"') == 2
    assert "boing" in html_text
    assert "sparkle" in html_text
    # The 2.0s cue belongs to a.mp4 (0-5s edit span); the 7.0s cue to b.mp4
    # (5-10s). The header also lists cue types in its own summary, so only
    # look within <main> (the clip cards) for placement.
    main_start = html_text.index("<main>")
    a_card_start = html_text.index("a.mp4", main_start)
    b_card_start = html_text.index("b.mp4", main_start)
    boing_pos = html_text.index("boing", main_start)
    sparkle_pos = html_text.index("sparkle", main_start)
    assert a_card_start < boing_pos < b_card_start
    assert b_card_start < sparkle_pos
