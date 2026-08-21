from __future__ import annotations

import pytest

from vlog_editor.planner import (
    build_balanced_fallback_plan,
    cluster_contiguous_scenes,
    constrain_selection_lengths,
    is_playful_source,
    resolve_target_duration,
    select_excerpt,
    select_required_settings,
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
    # 1800s of unique, solidly-scored (0.8) footage is well above the pacing
    # cap (720s single-day) worth of "keep this" content, so the target fills
    # the cap rather than being rationed down to a fixed slice of the total.
    assert target == 720
    assert mode == "auto"


def test_auto_target_keeps_most_of_a_short_but_strong_shoot() -> None:
    # A user shoots only 10 minutes, but all of it is clearly worth keeping
    # (children on camera, playful, high quality/story value). The target
    # should track that ~10 minutes, not an arbitrary ~16-28% slice of it.
    clips = [
        _clip(
            f"clip{i}.mov",
            120,
            transcript=f"anak main seru ketawa beat {i}",
            quality=0.9,
            story_value=0.9,
        )
        for i in range(5)
    ]
    for clip in clips:
        clip["visual"]["subjects"] = ["children"]
    target, mode = resolve_target_duration("auto", {"clips": clips})
    assert mode == "auto"
    assert target >= 540  # kept at least 90% of the 600s shoot


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
    plan = build_balanced_fallback_plan(
        analysis, 120, title="Trip", story_arc="chronological"
    )
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


def test_balanced_fallback_all_days_includes_secondary_capture_day() -> None:
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
                40,
                setting="mall",
                capture_time="2026-08-08T03:00:00.000000Z",
                transcript="anak senang di mall malam ini",
            ),
        ]
    }
    plan = build_balanced_fallback_plan(
        analysis,
        150,
        title="Week",
        story_arc="chronological",
        planning_scope="all_days",
    )
    fixed, errors = validate_and_fix_plan(plan, analysis, target_duration=150)
    assert errors == []
    assert fixed["planning_day"] == "all_days"
    assert fixed["planning_scope"] == "all_days"
    selected = [
        clip["file"]
        for section in fixed["structure"]
        for clip in section["clips"]
    ]
    assert "later-mall.mov" in selected
    assert selected == sorted(
        selected,
        key=lambda name: next(
            item["metadata"]["capture_time"]
            for item in analysis["clips"]
            if item["metadata"]["filename"] == name
        ),
    )


def test_kids_energy_arc_orders_peak_before_quiet() -> None:
    play = _clip(
        "playground.mov",
        80,
        transcript="anak bermain di playground ketawa seru",
        setting="outdoor",
        capture_time="2026-08-01T04:00:00.000000Z",
    )
    play["visual"]["summary"] = "kids playing on playground equipment"
    play["visual"]["subjects"] = ["children", "playground"]
    animals = _clip(
        "animals.mov",
        70,
        transcript="lihat kelinci lucu adek",
        setting="outdoor",
        capture_time="2026-08-01T05:00:00.000000Z",
    )
    animals["visual"]["summary"] = "children watch rabbits at a mini zoo"
    animals["visual"]["subjects"] = ["children", "rabbits"]
    car = _clip(
        "car-wait.mov",
        40,
        transcript="macet ya",
        setting="vehicle",
        capture_time="2026-08-01T01:00:00.000000Z",
    )
    car["visual"]["summary"] = "adult drives through traffic alone"
    car["visual"]["subjects"] = ["man", "car"]
    meal = _clip(
        "adult-meal.mov",
        50,
        transcript="nasi goreng enak",
        setting="restaurant",
        capture_time="2026-08-01T02:00:00.000000Z",
    )
    meal["visual"]["summary"] = "woman eats nasi goreng while looking at her phone"
    meal["visual"]["subjects"] = ["woman", "plate of food", "phone"]
    bye = _clip(
        "bye.mov",
        30,
        transcript="dadah ya makasih see you",
        setting="outdoor",
        capture_time="2026-08-01T08:00:00.000000Z",
    )
    bye["visual"]["summary"] = "child smiles and waves goodbye"
    bye["visual"]["subjects"] = ["child"]
    analysis = {"clips": [car, meal, play, animals, bye]}
    plan = build_balanced_fallback_plan(
        analysis, 180, title="Energy day", story_arc="kids_energy"
    )
    fixed, errors = validate_and_fix_plan(plan, analysis, target_duration=180)
    errors = [error for error in errors if "too far from target" not in error]
    assert errors == []
    assert fixed["story_arc"] == "kids_energy"
    names = [section["section"] for section in fixed["structure"]]
    assert any(name.startswith("Kids peak") for name in names)
    assert any(name.startswith("Quiet / adult") for name in names)
    assert names[-1] == "CTA close"
    # Peak kids files should appear before quiet/adult files in the edit.
    files = [clip["file"] for section in fixed["structure"] for clip in section["clips"]]
    play_i = min(i for i, name in enumerate(files) if name in {"playground.mov", "animals.mov"})
    quiet_files = [name for name in files if name in {"car-wait.mov", "adult-meal.mov"}]
    assert quiet_files, files
    quiet_i = min(i for i, name in enumerate(files) if name in {"car-wait.mov", "adult-meal.mov"})
    assert play_i < quiet_i
    assert files[-1] == "bye.mov"


def test_scene_energy_arc_keeps_scene_time_order_and_ranks_within_scene() -> None:
    weak_outdoor = _clip(
        "outdoor-weak.mov",
        40,
        transcript="jalan-jalan dulu",
        setting="outdoor",
        capture_time="2026-08-01T03:00:00.000000Z",
    )
    weak_outdoor["visual"]["summary"] = "adult walks through a resort path"
    weak_outdoor["visual"]["subjects"] = ["man", "path"]
    strong_outdoor = _clip(
        "outdoor-strong.mov",
        70,
        transcript="anak bermain di playground ketawa",
        setting="outdoor",
        capture_time="2026-08-01T03:30:00.000000Z",
    )
    strong_outdoor["visual"]["summary"] = "kids playing on playground equipment"
    strong_outdoor["visual"]["subjects"] = ["children", "playground"]
    mid_outdoor = _clip(
        "outdoor-mid.mov",
        50,
        transcript="adek lihat kelinci",
        setting="outdoor",
        capture_time="2026-08-01T03:45:00.000000Z",
    )
    mid_outdoor["visual"]["summary"] = "children watch rabbits at a mini zoo"
    mid_outdoor["visual"]["subjects"] = ["children", "rabbits"]
    mall = _clip(
        "mall-treat.mov",
        60,
        transcript="makan donat seru banget",
        setting="mall",
        capture_time="2026-08-01T07:00:00.000000Z",
    )
    mall["visual"]["summary"] = "kids eat donuts at a mall food court"
    mall["visual"]["subjects"] = ["children", "donuts"]
    car = _clip(
        "car-wait.mov",
        40,
        transcript="macet ya",
        setting="vehicle",
        capture_time="2026-08-01T01:00:00.000000Z",
    )
    car["visual"]["summary"] = "adult drives through traffic alone"
    car["visual"]["subjects"] = ["man", "car"]
    night_car = _clip(
        "night-car.mov",
        40,
        transcript="pulang malam",
        setting="vehicle",
        capture_time="2026-08-01T07:40:00.000000Z",
    )
    night_car["visual"]["summary"] = "family rides home in the car at night"
    night_car["visual"]["subjects"] = ["family", "car"]
    bye = _clip(
        "bye.mov",
        30,
        transcript="dadah ya makasih see you",
        setting="mall",
        capture_time="2026-08-01T08:00:00.000000Z",
    )
    bye["visual"]["summary"] = "child smiles and waves goodbye"
    bye["visual"]["subjects"] = ["child"]
    analysis = {
        "clips": [
            car,
            weak_outdoor,
            strong_outdoor,
            mid_outdoor,
            mall,
            night_car,
            bye,
        ]
    }
    plan = build_balanced_fallback_plan(analysis, 200, title="Scene day")
    fixed, errors = validate_and_fix_plan(plan, analysis, target_duration=200)
    errors = [error for error in errors if "too far from target" not in error]
    assert errors == []
    assert fixed["story_arc"] == "scene_energy"
    names = [section["section"] for section in fixed["structure"]]
    assert names[-1] == "CTA close"
    files = [clip["file"] for section in fixed["structure"] for clip in section["clips"]]
    # Contiguous scenes: morning outdoor stay before mall; night car is not merged into morning car.
    if "outdoor-strong.mov" in files and "mall-treat.mov" in files:
        assert files.index("outdoor-strong.mov") < files.index("mall-treat.mov")
    if "car-wait.mov" in files and "night-car.mov" in files:
        assert files.index("car-wait.mov") < files.index("night-car.mov")
        # Morning car and night car must live in different contiguous scene sections.
        car_section_indexes = [
            index
            for index, section in enumerate(fixed["structure"])
            for clip in section["clips"]
            if clip["file"] == "car-wait.mov"
        ]
        night_section_indexes = [
            index
            for index, section in enumerate(fixed["structure"])
            for clip in section["clips"]
            if clip["file"] == "night-car.mov"
        ]
        assert car_section_indexes and night_section_indexes
        assert car_section_indexes[0] != night_section_indexes[0]
    outdoor_section = next(
        (
            section
            for section in fixed["structure"]
            if str(section["section"]).lower().startswith("outdoor")
        ),
        None,
    )
    if outdoor_section is not None:
        outdoor_files = [clip["file"] for clip in outdoor_section["clips"]]
        assert outdoor_files[0] == "outdoor-strong.mov"
        assert "outdoor-weak.mov" not in outdoor_files


def test_cluster_contiguous_scenes_splits_on_time_of_day() -> None:
    clips = [
        {
            "file": "a.mov",
            "_capture_time": "2026-08-01T01:00:00.000000Z",
            "start": 0,
            "end": 10,
        },
        {
            "file": "b.mov",
            "_capture_time": "2026-08-01T01:10:00.000000Z",
            "start": 0,
            "end": 10,
        },
        {
            "file": "c.mov",
            "_capture_time": "2026-08-01T07:00:00.000000Z",
            "start": 0,
            "end": 10,
        },
    ]
    clusters = cluster_contiguous_scenes(clips)
    assert len(clusters) == 2
    assert [item["file"] for item in clusters[0]] == ["a.mov", "b.mov"]
    assert [item["file"] for item in clusters[1]] == ["c.mov"]
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
        story_arc="chronological",
    )
    assert plan["structure"][0]["section"].startswith("Home") or plan[
        "structure"
    ][0]["section"] == "Playful open"
    assert plan["structure"][-1]["section"] in {"CTA close", "Outdoor â€” Best moments"}


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


def test_excerpt_prefers_children_speech_window_over_adult_talk() -> None:
    source = {
        "metadata": {"duration": 120},
        "audio": {
            "segments": [
                {
                    "start": 5,
                    "end": 20,
                    "text": "parking is crowded today we should find another entrance soon",
                },
                {
                    "start": 70,
                    "end": 85,
                    "text": "adek mbak merah main ayunan yuk ketawa seru banget",
                },
            ],
            "words": [],
            "text": "adek mbak merah main ayunan",
        },
        "visual": {
            "summary": "A family with children at a playground",
            "subjects": ["children", "playground"],
            "recommended_ranges": [{"start": 0, "end": 120}],
        },
    }
    start, end = select_excerpt(source, 30)
    assert start >= 65
    assert end <= 90


def test_excerpt_prefers_kids_audience_activity_over_flat_talk() -> None:
    """Secondary priority: kid-interesting activity categories, not place hardcodes."""
    source = {
        "metadata": {"duration": 120},
        "audio": {
            "segments": [
                {
                    "start": 8,
                    "end": 22,
                    "text": "the parking ticket machine is confusing and the line is long",
                },
                {
                    "start": 75,
                    "end": 90,
                    "text": "lihat ya ada hewan lucu di kolam yuk kita explore",
                },
            ],
            "words": [],
            "text": "hewan lucu explore",
        },
        "visual": {
            "summary": "Kids look at animals near the water while exploring outdoors",
            "subjects": ["children", "animals", "water"],
            "recommended_ranges": [{"start": 0, "end": 120}],
        },
    }
    start, end = select_excerpt(source, 30)
    assert start >= 70
    assert end <= 95


def test_kids_audience_categories_are_activity_types_not_place_hardcodes() -> None:
    from vlog_editor.audio_plan import kids_audience_categories, kids_audience_interest

    text = "children watch animals near the water and then climb the playground"
    cats = kids_audience_categories(text)
    assert "animals" in cats
    assert "water_play" in cats or "play_structure" in cats
    assert kids_audience_interest(text) >= 0.5
    # No dedicated fish-pond category â€” fish maps to animals.
    assert "fish_pond" not in cats
    assert "animals" in kids_audience_categories("kids point at fish in the pond")


def test_adult_meal_focus_is_demoted_vs_playground() -> None:
    from vlog_editor.planner import (
        _clip_score,
        allows_extra_play_beats,
        is_adult_meal_focus,
        source_has_children,
        source_kids_audience_categories,
    )

    meal = _clip(
        "meal.mov",
        400,
        transcript="Mbak Merah bersama adek, Mami makan dulu, tadi udah renang",
        quality=0.8,
        story_value=0.8,
    )
    meal["visual"]["summary"] = (
        "A woman eats nasi goreng at an outdoor restaurant while interacting with her phone"
    )
    meal["visual"]["subjects"] = ["woman", "plate of food", "glass of drink", "phone"]

    playground = _clip(
        "play.mov",
        120,
        transcript="adek main ayunan",
        quality=0.6,
        story_value=0.6,
    )
    playground["visual"]["summary"] = "Children play on a colorful playground structure"
    playground["visual"]["subjects"] = ["children", "playground"]

    assert is_adult_meal_focus(meal)
    assert not source_has_children(meal)
    assert source_kids_audience_categories(meal) == []
    assert not allows_extra_play_beats(meal)
    assert source_has_children(playground)
    assert "play_structure" in source_kids_audience_categories(playground)
    assert allows_extra_play_beats(playground)
    assert _clip_score(playground) > _clip_score(meal)


def test_balanced_plan_avoids_long_adult_meal_stretch() -> None:
    clips = [
        _clip(
            "meal.mov",
            400,
            transcript="Mbak Merah adek Mami makan nasi goreng tadi renang",
            quality=0.85,
            story_value=0.85,
            setting="outdoor",
            capture_time="2026-08-09T02:20:00.000000Z",
        ),
        _clip(
            "playground-a.mov",
            120,
            transcript="adek main slide",
            setting="outdoor",
            capture_time="2026-08-09T03:30:00.000000Z",
        ),
        _clip(
            "playground-b.mov",
            120,
            transcript="anak bermain di playground",
            setting="outdoor",
            capture_time="2026-08-09T03:40:00.000000Z",
        ),
        _clip(
            "vehicle.mov",
            40,
            setting="vehicle",
            capture_time="2026-08-09T01:00:00.000000Z",
        ),
        _clip(
            "street.mov",
            40,
            setting="street",
            capture_time="2026-08-09T01:10:00.000000Z",
        ),
        _clip(
            "store.mov",
            40,
            setting="store",
            capture_time="2026-08-09T04:00:00.000000Z",
        ),
    ]
    clips[0]["visual"]["summary"] = "A woman eats nasi goreng while using her phone"
    clips[0]["visual"]["subjects"] = ["woman", "plate of food", "phone"]
    clips[1]["visual"]["summary"] = "Children play on playground slides"
    clips[1]["visual"]["subjects"] = ["children", "playground"]
    clips[2]["visual"]["summary"] = "Kids climb playground tunnels"
    clips[2]["visual"]["subjects"] = ["children", "playground"]

    plan = build_balanced_fallback_plan(
        {"clips": clips}, 240, title="No adult meal pad", story_arc="kids_energy"
    )
    meal_beats = [
        clip
        for section in plan["structure"]
        for clip in section["clips"]
        if clip["file"] == "meal.mov"
    ]
    play_beats = [
        clip
        for section in plan["structure"]
        for clip in section["clips"]
        if "playground" in clip["file"]
    ]
    assert len(meal_beats) <= 1
    assert len(play_beats) >= 1


def test_clip_score_prefers_children_over_scenic_adult_clip() -> None:
    from vlog_editor.planner import _clip_score, source_has_children

    kids = _clip(
        "kids.mov",
        40,
        transcript="adek bermain di playground",
        quality=0.55,
        story_value=0.55,
    )
    kids["visual"]["summary"] = "children playing on the playground"
    kids["visual"]["subjects"] = ["children", "playground"]
    scenic = _clip(
        "scenic.mov",
        40,
        transcript="nice road and trees ahead",
        quality=0.8,
        story_value=0.8,
    )
    scenic["visual"]["summary"] = "empty tree-lined road in soft light"
    scenic["visual"]["subjects"] = ["trees", "road"]
    assert source_has_children(kids)
    assert not source_has_children(scenic)
    assert _clip_score(kids) > _clip_score(scenic)


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


def test_required_settings_span_earliest_and_latest() -> None:
    settings = ["vehicle", "street", "outdoor", "restaurant", "attraction", "mall"]
    first_seen = {name: index for index, name in enumerate(settings)}
    pool = [
        _clip(f"{name}.mov", 120 if name in {"outdoor", "mall"} else 40, setting=name)
        for name in settings
    ]
    required = select_required_settings(
        settings,
        first_seen=first_seen,
        pool=pool,
        max_settings=4,
    )
    assert required[0] == "vehicle"
    assert "mall" in required
    assert len(required) == 4


def test_balanced_plan_keeps_late_day_mall_coverage() -> None:
    clips = []
    # Long daytime outdoor play that previously ate the whole budget.
    for index in range(6):
        clips.append(
            _clip(
                f"day-outdoor-{index}.mov",
                180,
                transcript="anak bermain dan ketawa seru banget di outdoor",
                setting="outdoor",
                capture_time=f"2026-08-09T0{index + 1}:00:00.000000Z",
            )
        )
        clips[-1]["visual"]["summary"] = "kids playing and fooling around outdoors"
    clips.extend(
        [
            _clip(
                "vehicle.mov",
                40,
                setting="vehicle",
                capture_time="2026-08-09T00:30:00.000000Z",
            ),
            _clip(
                "street.mov",
                40,
                setting="street",
                capture_time="2026-08-09T00:45:00.000000Z",
            ),
            _clip(
                "restaurant.mov",
                40,
                setting="restaurant",
                capture_time="2026-08-09T08:00:00.000000Z",
            ),
            _clip(
                "night-mall-arrive.mov",
                80,
                transcript="kita sampai di mall malam ini yuk belanja",
                setting="mall",
                capture_time="2026-08-09T19:30:00.000000Z",
            ),
            _clip(
                "night-mall-donut.mov",
                70,
                transcript="makan donat di food court seru banget",
                setting="mall",
                capture_time="2026-08-09T19:50:00.000000Z",
            ),
            _clip(
                "night-mall-leave.mov",
                50,
                transcript="pulang dari mall makasih dadah",
                setting="mall",
                capture_time="2026-08-09T20:10:00.000000Z",
            ),
        ]
    )
    clips[-3]["visual"]["summary"] = "family arrives at a shopping mall at night"
    clips[-2]["visual"]["summary"] = "kids eat donuts at a mall food court"
    clips[-1]["visual"]["summary"] = "family leaves the mall and waves goodbye"
    analysis = {"clips": clips}
    plan = build_balanced_fallback_plan(
        analysis, 360, title="Day with night mall", story_arc="chronological"
    )
    mall_files = {
        clip["file"]
        for section in plan["structure"]
        for clip in section["clips"]
        if "mall" in clip["file"]
    }
    assert len(mall_files) >= 2
    assert "night-mall-arrive.mov" in mall_files or "night-mall-donut.mov" in mall_files
    fixed, errors = validate_and_fix_plan(plan, analysis, target_duration=360)
    # Sparse fixtures can undershoot duration; still require a coherent mall-aware plan.
    errors = [error for error in errors if "too far from target" not in error]
    assert errors == []
    selected_settings = {
        infer_setting(
            next(
                item
                for item in analysis["clips"]
                if item["metadata"]["filename"] == clip["file"]
            )
        )
        for section in fixed["structure"]
        for clip in section["clips"]
    }
    assert "mall" in selected_settings


def test_leaves_mall_driving_home_stays_mall() -> None:
    clip = {
        "visual": {
            "setting": "outdoor|mall|vehicle",
            "summary": (
                "A family leaves a mall after dining, transitions to driving home "
                "at night, and shares their experience with the camera."
            ),
        }
    }
    assert infer_setting(clip) == "mall"


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
    plan = build_balanced_fallback_plan(
        analysis, 150, title="Play day", story_arc="chronological"
    )
    fixed, errors = validate_and_fix_plan(plan, analysis, target_duration=150)
    assert errors == []
    assert fixed["structure"][0]["section"] == "Playful open"
    assert fixed["structure"][-1]["section"] == "CTA close"
    assert "Playful cold-open" in fixed["structure"][0]["clips"][0]["note"]
    assert "CTA close" in fixed["structure"][-1]["clips"][0]["note"]


def test_balanced_plan_prefers_greeting_open_over_later_play() -> None:
    greeting = _clip(
        "car-hello.mov",
        30,
        transcript="Hai Assalamualaikum kita pagi ini mau kemana",
        setting="vehicle",
        capture_time="2026-08-01T01:00:00.000000Z",
    )
    greeting["visual"]["summary"] = "family of four in a car discussing the day"
    greeting["visual"]["subjects"] = ["family", "car", "children"]
    greeting["audio"]["segments"] = [
        {
            "start": 0.3,
            "end": 12.0,
            "text": "Hai Assalamualaikum kita pagi ini mau kemana",
        },
        {
            "start": 12.0,
            "end": 24.0,
            "text": "kita mau renang ya di deket rumah aja",
        },
    ]
    playground = _clip(
        "later-play.mov",
        40,
        transcript="anak bermain di playground dan ketawa",
        setting="outdoor",
        capture_time="2026-08-01T03:00:00.000000Z",
    )
    playground["visual"]["summary"] = "kids playing on playground equipment"
    playground["visual"]["subjects"] = ["children", "playground"]
    analysis = {
        "clips": [
            greeting,
            playground,
            _clip(
                "store.mov",
                40,
                setting="store",
                capture_time="2026-08-01T05:00:00.000000Z",
            ),
            _clip(
                "mall.mov",
                40,
                setting="mall",
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
    analysis["clips"][-1]["visual"]["summary"] = "child smiles and waves goodbye"
    plan = build_balanced_fallback_plan(
        analysis, 150, title="Family day", story_arc="chronological"
    )
    fixed, errors = validate_and_fix_plan(plan, analysis, target_duration=150)
    assert errors == []
    assert fixed["structure"][0]["section"] == "Greeting open"
    open_clip = fixed["structure"][0]["clips"][0]
    assert open_clip["file"] == "car-hello.mov"
    assert "Greeting / start-of-day open" in open_clip["note"]
    assert "Assalamualaikum" in open_clip["subtitle"] or "pagi ini" in open_clip["subtitle"]
    assert fixed["structure"][-1]["section"] == "CTA close"


def test_balanced_plan_inserts_arrival_and_skips_speechless_vehicle() -> None:
    from vlog_editor.planner import (
        build_balanced_fallback_plan,
        is_arrival_source,
        is_speechless_transit_pad,
    )

    greeting = _clip(
        "car-hello.mov",
        30,
        transcript="Hai Assalamualaikum kita pagi ini mau kemana",
        setting="vehicle",
        capture_time="2026-08-01T01:00:00.000000Z",
    )
    greeting["visual"]["summary"] = "family of four in a car discussing the day"
    greeting["visual"]["subjects"] = ["family", "car", "children"]
    greeting["audio"]["segments"] = [
        {
            "start": 0.3,
            "end": 12.0,
            "text": "Hai Assalamualaikum kita pagi ini mau kemana",
        }
    ]

    silent_car = _clip(
        "silent-car.mov",
        8,
        transcript="",
        setting="vehicle",
        capture_time="2026-08-01T01:10:00.000000Z",
    )
    silent_car["visual"]["summary"] = "low angle face partially visible inside a car"
    silent_car["visual"]["subjects"] = ["person", "car"]
    silent_car["audio"]["segments"] = []
    silent_car["audio"]["text"] = ""

    arrival = _clip(
        "arrived.mov",
        60,
        transcript="Hai Oke kita sekarang udah sampai di leker ada kolam renangnya",
        setting="attraction",
        capture_time="2026-08-01T02:00:00.000000Z",
    )
    arrival["visual"]["summary"] = "family arrives at outdoor resort with pools"
    arrival["visual"]["subjects"] = ["people", "cars", "pools"]
    arrival["audio"]["segments"] = [
        {
            "start": 0.0,
            "end": 8.0,
            "text": "Hai Oke kita sekarang udah sampai di leker",
        },
        {
            "start": 8.0,
            "end": 20.0,
            "text": "leker itu dia restoran ada kolam renangnya gitu",
        },
    ]
    arrival["audio"]["text"] = (
        "Hai Oke kita sekarang udah sampai di leker leker itu dia restoran ada kolam renangnya"
    )

    playground = _clip(
        "play.mov",
        90,
        transcript="anak bermain di playground dan ketawa seru",
        setting="outdoor",
        capture_time="2026-08-01T03:00:00.000000Z",
    )
    playground["visual"]["summary"] = "kids playing on playground equipment"
    playground["visual"]["subjects"] = ["children", "playground"]

    bye = _clip(
        "bye.mov",
        30,
        transcript="dadah ya makasih see you",
        setting="outdoor",
        capture_time="2026-08-01T08:00:00.000000Z",
    )
    bye["visual"]["summary"] = "child smiles and waves goodbye"
    bye["visual"]["subjects"] = ["child"]

    assert is_arrival_source(arrival)
    assert is_speechless_transit_pad(silent_car)
    assert not is_speechless_transit_pad(greeting)

    analysis = {"clips": [greeting, silent_car, arrival, playground, bye]}
    plan = build_balanced_fallback_plan(
        analysis, 200, title="Family day", story_arc="scene_energy"
    )
    fixed, errors = validate_and_fix_plan(plan, analysis, target_duration=200)
    errors = [error for error in errors if "too far from target" not in error]
    assert errors == []
    sections = [section["section"] for section in fixed["structure"]]
    assert sections[0] == "Greeting open"
    assert "Arrival" in sections
    arrival_idx = sections.index("Arrival")
    assert arrival_idx == 1
    arrival_clip = fixed["structure"][arrival_idx]["clips"][0]
    assert arrival_clip["file"] == "arrived.mov"
    assert "sampai" in arrival_clip["subtitle"].lower()
    assert "Destination arrival" in arrival_clip["note"]
    planned_files = {
        clip["file"]
        for section in fixed["structure"]
        for clip in section["clips"]
    }
    assert "silent-car.mov" not in planned_files
