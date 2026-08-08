from __future__ import annotations

from vlog_editor.validation import validate_and_fix_plan


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
