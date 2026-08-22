from __future__ import annotations

from vlog_editor.validation import infer_setting, validate_and_fix_plan


def _analysis() -> dict:
    return {
        "clips": [
            {
                "metadata": {"filename": "a.mp4", "duration": 10},
                "audio": {
                    "words": [
                        {"start": 1.0, "end": 1.8, "text": "hello"},
                        {"start": 1.9, "end": 2.5, "text": "world"},
                    ]
                },
            }
        ]
    }


def test_validator_snaps_cut_away_from_spoken_word() -> None:
    plan = {
        "title": "Test",
        "structure": [
            {
                "section": "Hook",
                "clips": [
                    {"file": "a.mp4", "start": 1.2, "end": 2.2, "note": "", "subtitle": ""}
                ],
            }
        ],
    }
    fixed, errors = validate_and_fix_plan(plan, _analysis(), target_duration=1.7)
    clip = fixed["structure"][0]["clips"][0]
    assert clip["start"] == 0.92
    assert clip["end"] == 2.62
    assert errors == []


def test_validator_rejects_unknown_source() -> None:
    plan = {
        "structure": [
            {"section": "Hook", "clips": [{"file": "missing.mp4", "start": 0, "end": 2}]}
        ]
    }
    _, errors = validate_and_fix_plan(plan, _analysis(), target_duration=2)
    assert any("unknown file" in error for error in errors)


def test_infer_setting_uses_existing_summary_for_legacy_analysis() -> None:
    clip = {"visual": {"summary": "A child explores a brightly lit toy store aisle"}}
    assert infer_setting(clip) == "store"


def test_infer_setting_does_not_treat_cars_or_cart_as_vehicle() -> None:
    clip = {
        "visual": {
            "summary": "toy cars and a shopping cart in the store aisle",
            "subjects": ["cart"],
        }
    }
    assert infer_setting(clip) == "store"


def test_validator_expands_to_complete_speech_segment() -> None:
    analysis = {
        "clips": [
            {
                "metadata": {"filename": "a.mp4", "duration": 20},
                "audio": {
                    "words": [
                        {"start": 5.0, "end": 5.4, "text": "main"},
                        {"start": 5.5, "end": 6.0, "text": "yuk"},
                    ],
                    "segments": [{"start": 4.5, "end": 8.0, "text": "main yuk seru"}],
                },
            }
        ]
    }
    plan = {
        "title": "Test",
        "structure": [
            {
                "section": "Hook",
                "clips": [
                    {"file": "a.mp4", "start": 5.2, "end": 6.5, "note": "", "subtitle": ""}
                ],
            }
        ],
    }
    fixed, errors = validate_and_fix_plan(plan, analysis, target_duration=4)
    clip = fixed["structure"][0]["clips"][0]
    assert clip["start"] <= 4.5
    assert clip["end"] >= 8.0
    assert errors == []


def test_validator_has_no_scene_diversity_requirement() -> None:
    # No minimum-settings-covered floor: a plan using only 2 of 4 available
    # settings is valid on its own terms (removed per explicit request).
    settings = ("vehicle", "home", "store", "outdoor")
    analysis = {
        "clips": [
            {
                "metadata": {"filename": f"{setting}.mp4", "duration": 10},
                "audio": {"words": []},
                "visual": {"setting": setting},
            }
            for setting in settings
        ]
    }
    plan = {
        "structure": [
            {
                "section": "Journey",
                "clips": [
                    {"file": "vehicle.mp4", "start": 0, "end": 8},
                    {"file": "home.mp4", "start": 0, "end": 2},
                ],
            }
        ]
    }
    _, errors = validate_and_fix_plan(plan, analysis, target_duration=10)
    assert not any("are required" in error for error in errors)
    # No per-setting dominance cap: one setting may fill most of the plan.
    assert not any("maximum is" in error for error in errors)


def test_reserved_arrival_beat_is_excluded_from_setting_share() -> None:
    settings = ("vehicle", "home", "store", "outdoor")
    analysis = {
        "clips": [
            {
                "metadata": {"filename": f"{setting}.mp4", "duration": 40},
                "audio": {"words": []},
                "visual": {"setting": setting},
            }
            for setting in settings
        ]
    }
    plan = {
        "structure": [
            {
                "section": "Arrival",
                "clips": [{"file": "vehicle.mp4", "start": 0, "end": 20}],
            },
            {
                "section": "Home — Best moments",
                "clips": [{"file": "home.mp4", "start": 0, "end": 12}],
            },
            {
                "section": "Store — Kids treats",
                "clips": [{"file": "store.mp4", "start": 0, "end": 12}],
            },
            {
                "section": "Outdoor — Best moments",
                "clips": [{"file": "outdoor.mp4", "start": 0, "end": 12}],
            },
        ]
    }
    _, errors = validate_and_fix_plan(plan, analysis, target_duration=56)
    assert not [error for error in errors if "maximum is 40%" in error]


def test_validator_accepts_balanced_settings() -> None:
    settings = ("vehicle", "home", "store", "outdoor")
    analysis = {
        "clips": [
            {
                "metadata": {"filename": f"{setting}.mp4", "duration": 10},
                "audio": {"words": []},
                "visual": {"setting": setting},
            }
            for setting in settings
        ]
    }
    plan = {
        "structure": [
            {
                "section": "Balanced",
                "clips": [
                    {"file": f"{setting}.mp4", "start": 0, "end": 2}
                    for setting in settings
                ],
            }
        ]
    }
    _, errors = validate_and_fix_plan(plan, analysis, target_duration=8)
    assert errors == []


def test_validator_rejects_capture_time_regressions() -> None:
    analysis = {
        "clips": [
            {
                "metadata": {
                    "filename": "later.mp4",
                    "duration": 10,
                    "capture_time": "2026-08-01T06:00:00.000000Z",
                },
                "audio": {"words": []},
                "visual": {"setting": "store"},
            },
            {
                "metadata": {
                    "filename": "earlier.mp4",
                    "duration": 10,
                    "capture_time": "2026-08-01T01:00:00.000000Z",
                },
                "audio": {"words": []},
                "visual": {"setting": "home"},
            },
        ]
    }
    plan = {
        "structure": [
            {
                "section": "Broken",
                "clips": [
                    {"file": "later.mp4", "start": 0, "end": 4},
                    {"file": "earlier.mp4", "start": 0, "end": 4},
                ],
            }
        ]
    }
    _, errors = validate_and_fix_plan(plan, analysis, target_duration=8)
    assert any("keep the plan chronological" in error for error in errors)


def test_validator_scopes_diversity_to_primary_capture_day() -> None:
    analysis = {
        "clips": [
            {
                "metadata": {
                    "filename": "home.mp4",
                    "duration": 10,
                    "capture_time": "2026-08-01T01:00:00.000000Z",
                },
                "audio": {"words": []},
                "visual": {"setting": "home"},
            },
            {
                "metadata": {
                    "filename": "vehicle.mp4",
                    "duration": 10,
                    "capture_time": "2026-08-01T05:00:00.000000Z",
                },
                "audio": {"words": []},
                "visual": {"setting": "vehicle"},
            },
            {
                "metadata": {
                    "filename": "store.mp4",
                    "duration": 10,
                    "capture_time": "2026-08-01T06:00:00.000000Z",
                },
                "audio": {"words": []},
                "visual": {"setting": "store"},
            },
            {
                "metadata": {
                    "filename": "outdoor.mp4",
                    "duration": 10,
                    "capture_time": "2026-08-01T07:00:00.000000Z",
                },
                "audio": {"words": []},
                "visual": {"setting": "outdoor"},
            },
            {
                "metadata": {
                    "filename": "mall.mp4",
                    "duration": 10,
                    "capture_time": "2026-08-08T03:00:00.000000Z",
                },
                "audio": {"words": []},
                "visual": {"setting": "mall"},
            },
        ]
    }
    plan = {
        "structure": [
            {
                "section": "Day one",
                "clips": [
                    {"file": "home.mp4", "start": 0, "end": 2},
                    {"file": "vehicle.mp4", "start": 0, "end": 2},
                    {"file": "store.mp4", "start": 0, "end": 2},
                    {"file": "outdoor.mp4", "start": 0, "end": 2},
                ],
            }
        ]
    }
    fixed, errors = validate_and_fix_plan(plan, analysis, target_duration=8)
    assert errors == []
    assert fixed["planning_day"] == "2026-08-01"
