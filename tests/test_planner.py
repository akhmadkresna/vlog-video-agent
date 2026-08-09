from __future__ import annotations

import pytest

from vlog_editor.planner import (
    build_balanced_fallback_plan,
    constrain_selection_lengths,
    is_playful_source,
    resolve_target_duration,
    select_excerpt,
    unique_clips,
)
from vlog_editor.validation import infer_setting, validate_and_fix_plan


def _clip(
    filename: str,
    duration: float,
    *,
    transcript: str = "",
    quality: float = 0.8,
    story_value: float = 0.8,
    setting: str | None = None,
    capture_time: str | None = None,
) -> dict:
    visual = {
        "summary": filename,
        "quality": quality,
        "story_value": story_value,
        "recommended_ranges": [{"start": 0, "end": min(30, duration)}],
    }
    if setting:
        visual["setting"] = setting
    metadata: dict = {"filename": filename, "duration": duration}
    if capture_time:
        metadata["capture_time"] = capture_time
    return {
        "metadata": metadata,
        "audio": {"text": transcript or filename, "segments": []},
        "visual": visual,
    }


def test_explicit_target_is_preserved() -> None:
    target, mode = resolve_target_duration(240, {"clips": []})
    assert target == 240
    assert mode == "explicit"


def test_auto_target_scales_with_unique_usable_footage() -> None:
    clips = [
        _clip("a.mov", 900, transcript="same spoken content"),
        _clip("a - Copy.mov", 900, transcript="same spoken content"),
        _clip("b.mov", 900, transcript="different spoken content"),
    ]
    target, mode = resolve_target_duration("auto", {"clips": clips})
    assert target == 270
    assert mode == "auto"


def test_auto_target_remains_feasible_for_short_footage() -> None:
    target, _ = resolve_target_duration("auto", {"clips": [_clip("short.mov", 20)]})
    assert target == 15


def test_invalid_target_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive number or 'auto'"):
        resolve_target_duration("ideal", {"clips": []})


def test_long_selections_are_capped_to_target_pacing() -> None:
    plan = {
        "structure": [
            {
                "section": "Journey",
                "clips": [
                    {"file": f"{index}.mov", "start": 5, "end": 105, "note": "", "subtitle": ""}
                    for index in range(10)
                ],
            }
        ]
    }
    constrained = constrain_selection_lengths(plan, 300)
    clips = constrained["structure"][0]["clips"]
    # Non-play caps at DEFAULT_MAX_SELECTION_SEC (36).
    assert all(clip["end"] == 41 for clip in clips)
    assert all("Focused excerpt." in clip["note"] for clip in clips)
    assert plan["structure"][0]["clips"][0]["end"] == 105


def test_unique_clips_prefer_original_over_copy() -> None:
    analysis = {
        "clips": [
            _clip("scene - Copy.mov", 20, transcript="same"),
            _clip("scene.mov", 20, transcript="same"),
        ]
    }
    unique = unique_clips(analysis)
    assert len(unique) == 1
    assert unique[0]["metadata"]["filename"] == "scene.mov"


def test_balanced_fallback_follows_capture_chronology_and_primary_day() -> None:
    analysis = {
        "clips": [
            _clip(
                "home.mov",
                30,
                setting="home",
                capture_time="2026-08-01T00:30:00.000000Z",
            ),
            _clip(
                "vehicle.mov",
                30,
                setting="vehicle",
                capture_time="2026-08-01T05:00:00.000000Z",
            ),
            _clip(
                "store.mov",
                30,
                setting="store",
                capture_time="2026-08-01T06:00:00.000000Z",
            ),
            _clip(
                "outdoor.mov",
                30,
                setting="outdoor",
                capture_time="2026-08-01T07:00:00.000000Z",
            ),
            _clip(
                "later-mall.mov",
                30,
                setting="mall",
                capture_time="2026-08-08T03:00:00.000000Z",
            ),
        ]
    }
    plan = build_balanced_fallback_plan(analysis, 120, title="Trip")
    fixed, errors = validate_and_fix_plan(plan, analysis, target_duration=120)
    assert errors == []
    assert fixed["planning_day"] == "2026-08-01"
    selected = [
        clip["file"]
        for section in fixed["structure"]
        for clip in section["clips"]
    ]
    assert "later-mall.mov" not in selected
    assert selected == sorted(
        selected,
        key=lambda name: next(
            item["metadata"]["capture_time"]
            for item in analysis["clips"]
            if item["metadata"]["filename"] == name
        ),
    )
    assert fixed["structure"][0]["section"].startswith("Home")


def test_setting_order_cannot_override_capture_chronology() -> None:
    analysis = {
        "clips": [
            _clip(
                "home.mov",
                30,
                setting="home",
                capture_time="2026-08-01T00:30:00.000000Z",
            ),
            _clip(
                "vehicle.mov",
                30,
                setting="vehicle",
                capture_time="2026-08-01T05:00:00.000000Z",
            ),
            _clip(
                "store.mov",
                30,
                setting="store",
                capture_time="2026-08-01T06:00:00.000000Z",
            ),
            _clip(
                "outdoor.mov",
                30,
                setting="outdoor",
                capture_time="2026-08-01T07:00:00.000000Z",
            ),
        ]
    }
    plan = build_balanced_fallback_plan(
        analysis,
        120,
        title="Trip",
        setting_order=["outdoor", "store", "vehicle", "home"],
    )
    assert plan["structure"][0]["section"].startswith("Home") or plan[
        "structure"
    ][0]["section"] == "Playful open"
    assert plan["structure"][-1]["section"] in {"CTA close", "Outdoor — Best moments"}


def test_excerpt_ignores_full_range_and_selects_interesting_speech_window() -> None:
    source = {
        "metadata": {"duration": 120},
        "audio": {
            "segments": [
                {"start": 0, "end": 4, "text": "hello"},
                {
                    "start": 62,
                    "end": 72,
                    "text": "unexpected colorful market discovery with the whole family",
                },
            ],
            "words": [],
        },
        "visual": {"recommended_ranges": [{"start": 0, "end": 120}]},
    }
    start, end = select_excerpt(source, 30)
    assert start > 60
    # Prefer a complete ASR exchange over padding to the full max window.
    assert 9.0 <= end - start <= 12.0
    assert end <= 73.0


def test_silent_long_clip_uses_centered_visual_excerpt() -> None:
    source = {
        "metadata": {"duration": 100},
        "audio": {"segments": [], "words": []},
        "visual": {"recommended_ranges": [{"start": 0, "end": 100}]},
    }
    assert select_excerpt(source, 20) == (40, 60)


def test_playful_source_boosts_kids_fooling_around() -> None:
    clip = _clip(
        "play.mov",
        120,
        transcript="anak bermain dan ketawa seru banget",
        quality=0.4,
        story_value=0.3,
    )
    clip["visual"]["summary"] = "kids playing and fooling around at home"
    assert is_playful_source(clip)
    from vlog_editor.planner import _clip_score

    assert _clip_score(clip) >= 0.62


def test_long_play_clip_can_contribute_multiple_beats() -> None:
    segments = [
        {"start": 10, "end": 40, "text": "mainan kapal lucu yuk kita main"},
        {"start": 120, "end": 160, "text": "ketawa seru bermain lagi di lantai"},
        {"start": 240, "end": 280, "text": "masih mainan dan heboh banget"},
    ]
    analysis = {
        "clips": [
            {
                "metadata": {
                    "filename": "long-play.mov",
                    "duration": 400,
                    "capture_time": "2026-08-01T01:00:00.000000Z",
                },
                "audio": {
                    "text": "mainan ketawa seru bermain",
                    "segments": segments,
                    "words": [],
                },
                "visual": {
                    "setting": "home",
                    "summary": "kids playing and fooling around",
                    "quality": 0.6,
                    "story_value": 0.4,
                    "recommended_ranges": [{"start": 0, "end": 400}],
                },
            },
            _clip(
                "vehicle.mov",
                40,
                setting="vehicle",
                capture_time="2026-08-01T05:00:00.000000Z",
            ),
            _clip(
                "store.mov",
                40,
                setting="store",
                capture_time="2026-08-01T06:00:00.000000Z",
            ),
            _clip(
                "outdoor.mov",
                40,
                setting="outdoor",
                capture_time="2026-08-01T07:00:00.000000Z",
            ),
        ]
    }
    plan = build_balanced_fallback_plan(analysis, 240, title="Play day")
    play_clips = [
        clip
        for section in plan["structure"]
        for clip in section["clips"]
        if clip["file"] == "long-play.mov"
    ]
    assert len(play_clips) >= 2
    assert all(clip["end"] - clip["start"] >= 10 for clip in play_clips)


def test_toy_store_cars_are_not_vehicle_setting() -> None:
    clip = {
        "visual": {
            "setting": "vehicle",
            "summary": "Hot Wheels cars lined up in a toy store aisle",
            "subjects": ["shopping cart", "toys"],
        }
    }
    assert infer_setting(clip) == "store"


def test_balanced_plan_bookends_playful_open_and_cta_close() -> None:
    analysis = {
        "clips": [
            _clip(
                "early-play.mov",
                40,
                transcript="anak bermain dan ketawa seru",
                setting="home",
                capture_time="2026-08-01T01:00:00.000000Z",
            ),
            _clip(
                "vehicle.mov",
                40,
                setting="vehicle",
                capture_time="2026-08-01T05:00:00.000000Z",
            ),
            _clip(
                "store.mov",
                40,
                setting="store",
                capture_time="2026-08-01T06:00:00.000000Z",
            ),
            _clip(
                "outdoor.mov",
                40,
                setting="outdoor",
                capture_time="2026-08-01T07:00:00.000000Z",
            ),
            _clip(
                "bye.mov",
                30,
                transcript="dadah ya makasih see you",
                setting="outdoor",
                capture_time="2026-08-01T08:00:00.000000Z",
            ),
        ]
    }
    analysis["clips"][0]["visual"]["summary"] = "kids playing and fooling around"
    analysis["clips"][-1]["visual"]["summary"] = "child smiles and waves goodbye"
    plan = build_balanced_fallback_plan(analysis, 150, title="Play day")
    fixed, errors = validate_and_fix_plan(plan, analysis, target_duration=150)
    assert errors == []
    assert fixed["structure"][0]["section"] == "Playful open"
    assert fixed["structure"][-1]["section"] == "CTA close"
    assert "Playful cold-open" in fixed["structure"][0]["clips"][0]["note"]
    assert "CTA close" in fixed["structure"][-1]["clips"][0]["note"]
