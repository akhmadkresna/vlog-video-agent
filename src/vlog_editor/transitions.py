from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_TRANSITIONS: dict[str, Any] = {
    "enabled": True,
    "interval_sec": 300.0,
    "card_duration": 2.0,
    "bg_color": "0x1F6FEB",
    "text_color": "white",
}


def transitions_config(episode_config: dict[str, Any]) -> dict[str, Any]:
    raw = episode_config.get("transitions")
    merged = dict(DEFAULT_TRANSITIONS)
    if isinstance(raw, dict):
        merged.update(raw)
    return merged


def bundled_font_path() -> Path:
    return Path(__file__).resolve().parent / "assets" / "fonts" / "Bangers-Regular.ttf"


def format_time_skip_label(elapsed_sec: float) -> str:
    minutes = max(1, round(elapsed_sec / 60.0))
    unit = "minute" if minutes == 1 else "minutes"
    return f"{minutes} {unit} later..."


def plan_time_skip_cards(
    plan: dict[str, Any],
    *,
    interval_sec: float,
    card_duration: float,
) -> list[dict[str, Any]]:
    """Original-design "X minutes later" cards, snapped to clip boundaries.

    Returns entries keyed by after_index (0-based position in the flattened
    plan.structure[].clips[] list) so the render pipeline can splice a card
    in right after that clip, plus content_time (the pre-card edit-timeline
    position, used by shift_time to keep audio/captions in sync).
    """
    if interval_sec <= 0 or card_duration <= 0:
        return []
    clips = [
        clip
        for section in plan.get("structure", []) or []
        for clip in section.get("clips", []) or []
        if isinstance(clip, dict)
    ]
    durations = [max(0.0, float(clip["end"]) - float(clip["start"])) for clip in clips]
    total = sum(durations)

    cards: list[dict[str, Any]] = []
    cumulative = 0.0
    next_mark = interval_sec
    for index, duration in enumerate(durations):
        cumulative += duration
        # Skip a card that would land in the last stretch of the edit — a
        # "later" card right before the video ends reads as a mistake, not a joke.
        while cumulative >= next_mark and (total - cumulative) > card_duration * 2:
            cards.append(
                {
                    "after_index": index,
                    "content_time": cumulative,
                    "duration": card_duration,
                    "label": format_time_skip_label(next_mark),
                }
            )
            next_mark += interval_sec
    return cards


def shift_time(seconds: float, cards: list[dict[str, Any]]) -> float:
    """Map a content-timeline second to its position once cards are spliced in."""
    value = float(seconds)
    return value + sum(
        float(card["duration"]) for card in cards if float(card["content_time"]) <= value + 1e-6
    )
