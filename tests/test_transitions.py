from __future__ import annotations

import copy
from pathlib import Path

import pytest

from vlog_editor.project import DEFAULT_CONFIG, Episode
from vlog_editor.render import build_render_command
from vlog_editor.transitions import (
    bundled_font_path,
    format_time_skip_label,
    plan_time_skip_cards,
    shift_time,
)


def _episode(tmp_path: Path) -> Episode:
    for name in ("footage", "work", "output"):
        (tmp_path / name).mkdir()
    return Episode(tmp_path, copy.deepcopy(DEFAULT_CONFIG))


def _plan_with_clips(durations: list[float]) -> dict:
    clips = []
    for index, duration in enumerate(durations):
        clips.append(
            {
                "file": f"clip{index}.mp4",
                "start": 0.0,
                "end": duration,
                "note": "",
                "subtitle": "",
            }
        )
    return {
        "title": "Test",
        "duration_sec": sum(durations),
        "structure": [{"section": "Body", "clips": clips}],
    }


def test_bundled_font_exists() -> None:
    assert bundled_font_path().is_file()


def test_format_time_skip_label() -> None:
    assert format_time_skip_label(60) == "1 minute later..."
    assert format_time_skip_label(300) == "5 minutes later..."
    assert format_time_skip_label(295) == "5 minutes later..."


def test_plan_time_skip_cards_snaps_to_clip_boundaries() -> None:
    # Three 200s clips: 5-min mark (300s) falls mid-second-clip, so the card
    # should land after clip index 1 (cumulative 400s), not mid-clip.
    plan = _plan_with_clips([200.0, 200.0, 200.0])
    cards = plan_time_skip_cards(plan, interval_sec=300.0, card_duration=2.0)
    assert len(cards) == 1
    assert cards[0]["after_index"] == 1
    assert cards[0]["content_time"] == 400.0
    assert cards[0]["label"] == "5 minutes later..."


def test_plan_time_skip_cards_skips_card_near_the_end() -> None:
    # Total is only slightly over one interval — a card here would land right
    # before the video ends, so it should be skipped.
    plan = _plan_with_clips([301.0])
    cards = plan_time_skip_cards(plan, interval_sec=300.0, card_duration=2.0)
    assert cards == []


def test_plan_time_skip_cards_multiple_intervals() -> None:
    # Cards only snap to clip boundaries, so use several clips (not one giant
    # one) to give the 5/10/15-minute marks somewhere to land.
    plan = _plan_with_clips([200.0] * 6)  # 1200s total
    cards = plan_time_skip_cards(plan, interval_sec=300.0, card_duration=2.0)
    labels = [card["label"] for card in cards]
    assert labels == ["5 minutes later...", "10 minutes later...", "15 minutes later..."]


def test_plan_time_skip_cards_disabled_via_zero_interval() -> None:
    plan = _plan_with_clips([1000.0])
    assert plan_time_skip_cards(plan, interval_sec=0.0, card_duration=2.0) == []


def test_shift_time_accumulates_prior_cards() -> None:
    cards = [
        {"content_time": 100.0, "duration": 2.0},
        {"content_time": 250.0, "duration": 3.0},
    ]
    assert shift_time(50.0, cards) == 50.0
    assert shift_time(100.0, cards) == 102.0
    assert shift_time(200.0, cards) == 202.0
    assert shift_time(300.0, cards) == 305.0


def test_render_graph_splices_time_skip_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(tmp_path)
    (episode.footage / "clip0.mp4").write_bytes(b"fake")
    (episode.footage / "clip1.mp4").write_bytes(b"fake")
    monkeypatch.setattr("vlog_editor.render.probe_video", lambda _: {"has_audio": True})
    monkeypatch.setattr("vlog_editor.render._encoder", lambda: ("libx264", ["-crf", "18"]))
    plan = _plan_with_clips([3.0, 3.0])
    cards = [{"after_index": 0, "content_time": 3.0, "duration": 2.0, "label": "5 minutes later..."}]
    command, filters, output_duration = build_render_command(
        episode, plan, episode.output / "final.mp4", time_skip_cards=cards
    )
    assert output_duration == 8.0  # 3 + 2 (card) + 3
    assert "concat=n=3:v=1:a=1" in filters
    assert "drawtext=fontfile=" in filters
    assert "5 minutes later" in filters
    assert "-t" in command
    assert "8.000" in command
