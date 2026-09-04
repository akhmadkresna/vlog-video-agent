from __future__ import annotations

import copy
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from vlog_editor.audio_plan import (
    CUTE_RE,
    LAUGH_RE,
    PLAY_RE,
    kids_audience_categories,
    kids_audience_interest,
    plan_audio_cues,
    text_has_children,
    text_looks_like_adult_meal,
)
from vlog_editor.kids_interest import (
    WOW_SCORE_THRESHOLD,
    best_wow_span,
    score_window,
    source_has_strong_wow,
    source_wow_summary,
)
from vlog_editor.gameplay import attach_gameplay_matches
from vlog_editor.media import item_daypart, probe_video
from vlog_editor.project import Episode, read_json, write_json
from vlog_editor.validation import infer_setting, validate_and_fix_plan, validate_audio_plan
from vlog_editor.vision import OllamaClient, parse_json_response

MIN_SELECTION_SEC = 10.0
DEFAULT_MAX_SELECTION_SEC = 36.0
PLAY_MAX_SELECTION_SEC = 90.0
OPEN_CTA_BEAT_SEC = 22.0
MIN_OPEN_CTA_SEC = 12.0

MIN_RECOMMENDED_DURATION_SEC = 7 * 60

CTA_RE = re.compile(
    r"\b("
    r"thanks|thank you|makasih|terima kasih|bye|dadah|daah|"
    r"wave|waving|senyum|smile|smiling|see you|sampai jumpa|"
    r"bye bye|dadah ya|kutip|subscribe|like"
    r")\b",
    re.IGNORECASE,
)

# Start-of-day / vlog greeting cues (ASR). Prefer these over mid-day playful cold-opens.
GREETING_RE = re.compile(
    r"("
    r"assalamu'?alaikum|assalamualaikum|waalaikumsalam|"
    r"\bselamat\s+pagi\b|\bpagi\s+ini\b|\bmau\s+kemana\b|"
    r"\bkita\s+mau\b|\bberangkat\b|\budah\s+sampai\b|\bsudah\s+sampai\b|"
    r"\boke\s+kita\s+sekarang\b|\blet'?s\s+go\b|"
    r"\bhai\b|\bhalo\b|\bhello\b|\bhi\b"
    r")",
    re.IGNORECASE,
)
STRONG_GREETING_RE = re.compile(
    r"("
    r"assalamu'?alaikum|assalamualaikum|"
    r"\bselamat\s+pagi\b|\bpagi\s+ini\b|\bmau\s+kemana\b|"
    r"\budah\s+sampai\b|\bsudah\s+sampai\b|\boke\s+kita\s+sekarang\b"
    r")",
    re.IGNORECASE,
)

# Destination arrival narration — its own story beat after open (not a silent car pad).
ARRIVAL_RE = re.compile(
    r"("
    r"\budah\s+sampai\b|\bsudah\s+sampai\b|\bwe\s+arrived\b|\barrived\s+at\b|"
    r"\bsampai\s+di\b|\bsampai\s+ini\b|\bkita\s+sekarang\s+udah\s+sampai\b|"
    r"\bkita\s+udah\s+sampai\b|\bok(?:e)?\s+kita\s+udah\s+sampai\b"
    r")",
    re.IGNORECASE,
)
DEPARTURE_OPEN_RE = re.compile(
    r"("
    r"assalamu'?alaikum|assalamualaikum|"
    r"\bselamat\s+pagi\b|\bpagi\s+ini\b|\bmau\s+kemana\b|"
    r"\bberangkat\b|\blet'?s\s+go\b"
    r")",
    re.IGNORECASE,
)


def _source_text(source: dict[str, Any]) -> str:
    visual = source.get("visual", {})
    audio = source.get("audio", {})
    subjects = " ".join(str(item) for item in visual.get("subjects", []) or [])
    return f"{audio.get('text', '')} {visual.get('summary', '')} {subjects}"


def _visual_text(source: dict[str, Any]) -> str:
    """On-camera evidence only — subjects + summary (ignore past-tense ASR chatter)."""
    visual = source.get("visual", {})
    subjects = " ".join(str(item) for item in visual.get("subjects", []) or [])
    return f"{visual.get('summary', '')} {subjects}"


def is_adult_meal_focus(source: dict[str, Any]) -> bool:
    """Adult eating/phone B-roll with no children visible in the vision summary/subjects."""
    return text_looks_like_adult_meal(_visual_text(source))


def is_playful_source(source: dict[str, Any]) -> bool:
    """Kids fooling-around / play / laugh — keep these scenes, don't starve them."""
    if is_adult_meal_focus(source):
        return False
    text = _source_text(source)
    visual = _visual_text(source)
    # Prefer visual play cues; ASR-only name drops on adult meals are not playful.
    if PLAY_RE.search(visual) or LAUGH_RE.search(text):
        return True
    if PLAY_RE.search(text) and text_has_children(visual):
        return True
    if CUTE_RE.search(visual) and float(source.get("visual", {}).get("story_value", 0) or 0) >= 0.55:
        return True
    return False


def source_has_children(source: dict[str, Any]) -> bool:
    """Children on camera (subjects/summary). ASR name-drops alone do not count."""
    return text_has_children(_visual_text(source))


def source_kids_audience_interest(source: dict[str, Any]) -> float:
    """Secondary priority from on-camera activity types (not ASR talking about earlier swim)."""
    if is_adult_meal_focus(source):
        return 0.0
    visual = _visual_text(source)
    score = kids_audience_interest(visual)
    # Generic on-camera kids play still counts even without a named play structure.
    if score <= 0 and source_has_children(source) and PLAY_RE.search(visual):
        return 0.34
    return score


def source_kids_audience_categories(source: dict[str, Any]) -> list[str]:
    if is_adult_meal_focus(source):
        return []
    return kids_audience_categories(_visual_text(source))


def allows_extra_play_beats(source: dict[str, Any]) -> bool:
    """Multi-beats for playful kids activity, strong unused wow payoffs, or any
    other long clip that's clearly worth keeping (children on camera, high
    score) — a good long take shouldn't be capped at one short beat just
    because it wasn't tagged "playful"."""
    if is_adult_meal_focus(source):
        return False
    if float(source.get("metadata", {}).get("duration", 0) or 0) < 90:
        return False
    if source_has_strong_wow(source):
        return True
    if is_playful_source(source):
        # Need at least one on-camera kids-audience activity hook.
        if source_kids_audience_interest(source) >= 0.34:
            return True
    return source_has_children(source) and _clip_score(source) >= 0.6


def is_cta_source(source: dict[str, Any]) -> bool:
    text = _source_text(source)
    if CTA_RE.search(text):
        return True
    story = float(source.get("visual", {}).get("story_value", 0) or 0)
    return bool(CUTE_RE.search(text) and story >= 0.65)


def _audio_text(source: dict[str, Any]) -> str:
    audio = source.get("audio", {}) or {}
    segments = audio.get("segments", []) or []
    spoken = " ".join(str(segment.get("text", "")) for segment in segments if isinstance(segment, dict))
    return f"{audio.get('text', '')} {spoken}".strip()


def is_greeting_source(source: dict[str, Any]) -> bool:
    """Spoken start-of-day greeting / departure narration (prefer ASR over visuals)."""
    return bool(GREETING_RE.search(_audio_text(source)))


def is_strong_greeting_source(source: dict[str, Any]) -> bool:
    """Explicit greeting / 'pagi ini mau kemana' / arrival open — not a casual mid-day 'hai'."""
    return bool(STRONG_GREETING_RE.search(_audio_text(source)))


def is_arrival_source(source: dict[str, Any]) -> bool:
    """Spoken destination arrival ('udah sampai…') — narrative beat, not transit pad."""
    return bool(ARRIVAL_RE.search(_audio_text(source)))


def is_departure_open_source(source: dict[str, Any]) -> bool:
    """Start-of-day departure / hello — preferred for the open bookend over arrival."""
    return bool(DEPARTURE_OPEN_RE.search(_audio_text(source)))


def is_speechless_transit_pad(source: dict[str, Any]) -> bool:
    """Vehicle/street crumbs with no usable speech — demote unless arrival narration."""
    setting = infer_setting(source)
    if setting not in {"vehicle", "street"}:
        return False
    if is_arrival_source(source):
        return False
    text = " ".join(_audio_text(source).split())
    # Truly empty / near-empty pads (e.g. silent car close-up). Short but real
    # lines like "macet ya" still count as spoken transit.
    return len(text) < 8


def _max_selection_for_source(source: dict[str, Any]) -> float:
    if is_playful_source(source) or source_has_strong_wow(source):
        return PLAY_MAX_SELECTION_SEC
    return DEFAULT_MAX_SELECTION_SEC


def _complete_exchange(
    segments: list[dict[str, Any]],
    *,
    start: float,
    max_duration: float,
    source_duration: float,
) -> tuple[float, float]:
    """Grow from a segment start through whole ASR segments (complete narration)."""
    start = max(0.0, start)
    cap = min(source_duration, start + max_duration)
    overlapping = sorted(
        (
            item
            for item in segments
            if float(item.get("end", 0)) > start and float(item.get("start", 0)) < cap
        ),
        key=lambda item: float(item.get("start", 0)),
    )
    if not overlapping:
        return start, min(cap, start + max_duration)
    end = min(source_duration, float(overlapping[0].get("end", start)) + 0.12)
    for item in overlapping[1:]:
        candidate = min(source_duration, float(item.get("end", end)) + 0.12)
        if candidate - start <= max_duration + 1e-6:
            end = candidate
        else:
            break
    # Prefer not ending mid-segment when a tiny pad finishes it.
    last = overlapping[-1]
    last_end = min(source_duration, float(last.get("end", end)) + 0.12)
    if end < last_end and (last_end - start) <= max_duration + 1.5:
        end = last_end
    return start, max(end, min(cap, start + 0.35))


def constrain_selection_lengths(plan: dict[str, Any], target_duration: float) -> dict[str, Any]:
    constrained = copy.deepcopy(plan)
    selections = [
        clip
        for section in constrained.get("structure", [])
        if isinstance(section, dict)
        for clip in section.get("clips", [])
        if isinstance(clip, dict)
    ]
    if not selections or target_duration <= 0:
        return constrained

    max_selection = min(
        PLAY_MAX_SELECTION_SEC,
        max(MIN_SELECTION_SEC, target_duration * 1.25 / len(selections)),
    )
    for clip in selections:
        try:
            start = float(clip.get("start", 0))
            end = float(clip.get("end", start))
        except (TypeError, ValueError):
            continue
        note = str(clip.get("note", ""))
        subtitle = str(clip.get("subtitle", ""))
        playful = bool(PLAY_RE.search(f"{note} {subtitle}") or LAUGH_RE.search(f"{note} {subtitle}"))
        cap = PLAY_MAX_SELECTION_SEC if playful else min(DEFAULT_MAX_SELECTION_SEC, max_selection)
        if end - start > cap:
            clip["end"] = round(start + cap, 3)
            clip["note"] = f"{note} Focused excerpt.".strip()
    return constrained


def select_excerpt(
    source: dict[str, Any],
    max_duration: float,
    *,
    avoid: list[tuple[float, float]] | None = None,
    prefer_greeting: bool = False,
    prefer_arrival: bool = False,
) -> tuple[float, float]:
    source_duration = max(0.0, float(source.get("metadata", {}).get("duration", 0)))
    max_duration = min(max(0.0, max_duration), source_duration)
    if max_duration < 0.35:
        return 0.0, max_duration
    avoided = avoid or []

    def overlaps_avoided(start: float, end: float) -> bool:
        for previous_start, previous_end in avoided:
            if min(end, previous_end) - max(start, previous_start) > 0.35:
                return True
        return False

    # The whole clip was requested and fits: use all of it rather than
    # narrowing to a single "best moment" sub-range — a caller asking for
    # the full duration wants the full clip, not a highlight of it.
    if max_duration >= source_duration - 1e-6 and not overlaps_avoided(0.0, source_duration):
        return 0.0, source_duration

    segments = [
        segment
        for segment in source.get("audio", {}).get("segments", [])
        if isinstance(segment, dict)
    ]

    def _lock_speech_pattern(
        pattern: re.Pattern[str],
        *,
        strength_pattern: re.Pattern[str] | None = None,
        min_span: float = MIN_OPEN_CTA_SEC,
    ) -> tuple[float, float] | None:
        hits: list[tuple[float, float, float]] = []
        for segment in segments:
            text = str(segment.get("text", ""))
            if not pattern.search(text):
                continue
            try:
                start = max(0.0, float(segment.get("start", 0)) - 0.08)
            except (TypeError, ValueError):
                continue
            start, end = _complete_exchange(
                segments,
                start=start,
                max_duration=max_duration,
                source_duration=source_duration,
            )
            if end - start < 0.35 or overlaps_avoided(start, end):
                continue
            strength = 2.0 if strength_pattern and strength_pattern.search(text) else 1.0
            earliness = 1.0 - (start / max(source_duration, 1.0))
            hits.append((strength + earliness, start, end))
        if not hits:
            return None
        _, start, end = max(hits, key=lambda item: item[0])
        if end - start + 1e-6 < min_span and source_duration + 1e-6 >= min_span:
            start, end = _complete_exchange(
                segments,
                start=start,
                max_duration=max(max_duration, min_span + 2.0),
                source_duration=source_duration,
            )
            if end - start + 1e-6 < min_span:
                end = min(source_duration, start + min(max_duration, min_span))
        return round(start, 3), round(min(end, start + max_duration), 3)

    # Arrival beats lock onto "udah sampai…" before generic wow / ranges.
    if prefer_arrival and segments:
        locked = _lock_speech_pattern(ARRIVAL_RE, strength_pattern=ARRIVAL_RE)
        if locked is not None:
            return locked

    # Greeting opens should lock onto the spoken hello near the start of the take.
    if prefer_greeting and segments:
        locked = _lock_speech_pattern(
            GREETING_RE,
            strength_pattern=STRONG_GREETING_RE,
        )
        if locked is not None:
            return locked

    # Prefer kids-interest payoff spans (animal wow, reactions) over walk-up setup.
    wow_pick = best_wow_span(
        source,
        max_duration,
        avoid=avoided,
        min_score=WOW_SCORE_THRESHOLD,
    )
    if wow_pick is not None:
        wow_start, wow_end, _moment = wow_pick
        wow_start, wow_end = _complete_exchange(
            segments,
            start=wow_start,
            max_duration=max_duration,
            source_duration=source_duration,
        )
        if wow_end - wow_start >= 0.35 and not overlaps_avoided(wow_start, wow_end):
            return round(wow_start, 3), round(min(wow_end, wow_start + max_duration), 3)

    ranges = source.get("visual", {}).get("recommended_ranges", [])
    clip_has_children = source_has_children(source)
    shot_type = str(source.get("visual", {}).get("shot_type", "") or "")

    def _range_wow_key(item: dict[str, Any]) -> tuple[Any, ...]:
        reason = str(item.get("reason", ""))
        scored = score_window("", visual_reason=reason, shot_type=shot_type)
        phase = str(scored.get("phase") or "payoff")
        phase_rank = 0 if phase == "payoff" else 1 if phase == "logistics" else 2
        return (
            phase_rank,
            -float(scored.get("score", 0) or 0),
            0 if text_has_children(reason) else 1,
            -kids_audience_interest(reason),
            float(item.get("start", 0) or 0),
        )

    # Prefer payoff / high-wow recommended ranges before establishing walk-ups.
    ordered_ranges = sorted(
        (item for item in ranges if isinstance(item, dict)),
        key=_range_wow_key,
    )
    for item in ordered_ranges:
        try:
            start = max(0.0, float(item.get("start", 0)))
            end = min(source_duration, float(item.get("end", source_duration)))
        except (TypeError, ValueError):
            continue
        span = end - start
        is_focused = span <= max_duration * 1.5 and (
            source_duration <= max_duration or span < source_duration * 0.8
        )
        if span >= 0.35 and is_focused and not overlaps_avoided(start, min(end, start + max_duration)):
            start, end = _complete_exchange(
                segments,
                start=start,
                max_duration=max_duration,
                source_duration=source_duration,
            )
            if not overlaps_avoided(start, end):
                return round(start, 3), round(min(end, start + max_duration), 3)

    words = [
        word for word in source.get("audio", {}).get("words", []) if isinstance(word, dict)
    ]
    candidates: list[tuple[float, float, float]] = []
    for segment in segments:
        try:
            start = max(0.0, float(segment.get("start", 0)) - 0.08)
        except (TypeError, ValueError):
            continue
        start, end = _complete_exchange(
            segments,
            start=start,
            max_duration=max_duration,
            source_duration=source_duration,
        )
        if end - start < 0.35 or overlaps_avoided(start, end):
            continue
        overlapping = [
            item
            for item in segments
            if float(item.get("end", 0)) > start and float(item.get("start", 0)) < end
        ]
        text = " ".join(str(item.get("text", "")) for item in overlapping).lower()
        tokens = re.findall(r"\w+", text, flags=re.UNICODE)
        probabilities = [
            float(word.get("probability", 0.5))
            for word in words
            if float(word.get("end", 0)) > start and float(word.get("start", 0)) < end
        ]
        confidence = sum(probabilities) / len(probabilities) if probabilities else 0.5
        lexical_interest = len(set(tokens)) + len(tokens) * 0.15
        # Prefer fuller exchanges over tiny mid-sentence windows.
        completeness = min(1.0, (end - start) / max(12.0, max_duration * 0.5))
        play_bonus = 1.5 if PLAY_RE.search(text) or LAUGH_RE.search(text) else 0.0
        children_bonus = 4.0 if text_has_children(text) else 0.0
        # Inside family footage, demote adult-only windows so kids beats win.
        if clip_has_children and not text_has_children(text):
            children_bonus -= 2.0
        audience_bonus = 2.2 * kids_audience_interest(text)
        wow = score_window(text, shot_type=shot_type)
        wow_bonus = 5.0 * float(wow.get("score", 0) or 0)
        if str(wow.get("phase") or "") == "setup":
            wow_bonus -= 3.0
        greeting_bonus = 0.0
        if prefer_greeting and GREETING_RE.search(text):
            greeting_bonus = 6.0 if STRONG_GREETING_RE.search(text) else 3.5
            greeting_bonus += (1.0 - (start / max(source_duration, 1.0))) * 2.0
        center = (start + end) / 2
        center_preference = 1 - abs(center - source_duration / 2) / max(source_duration, 1)
        score = (
            lexical_interest
            + confidence * 3
            + center_preference * 0.25
            + completeness * 2.5
            + play_bonus
            + children_bonus
            + audience_bonus
            + wow_bonus
            + greeting_bonus
        )
        candidates.append((score, start, end))
    if candidates:
        _, start, end = max(candidates, key=lambda item: item[0])
        return round(start, 3), round(end, 3)

    start = max(0.0, (source_duration - max_duration) / 2)
    end = start + max_duration
    if overlaps_avoided(start, end):
        # No safe silent window left in this source.
        return 0.0, 0.0
    return round(start, 3), round(end, 3)


def _is_copy_filename(filename: str) -> bool:
    stem = Path(filename).stem.lower()
    return bool(re.search(r"(?:^|[ _-])copy(?:[ _-]|$|\d)", stem))


def _clip_signature(clip: dict[str, Any]) -> tuple[Any, ...]:
    metadata = clip.get("metadata", {})
    audio = clip.get("audio", {})
    visual = clip.get("visual", {})
    duration = max(0.0, float(metadata.get("duration", 0)))
    transcript = " ".join(str(audio.get("text", "")).lower().split())
    return (
        round(duration, 2),
        transcript or str(visual.get("summary", "")).lower(),
    )


def _clip_score(clip: dict[str, Any]) -> float:
    visual = clip.get("visual", {})
    try:
        quality = float(visual.get("quality", 0.5))
        story_value = float(visual.get("story_value", 0.5))
    except (TypeError, ValueError):
        quality = story_value = 0.5
    score = (quality + story_value) / 2
    # Handheld play is often scored low by vision but is the emotional core for kids vlogs.
    if is_playful_source(clip):
        score = max(score, 0.62)
        score = min(1.0, score + 0.12)
    # Explicit child presence outranks scenic adult-only B-roll.
    if source_has_children(clip):
        score = max(score, 0.68)
        score = min(1.0, score + 0.15)
    # Secondary: kid-audience activity interest (play structures, animals, water,
    # treats, discovery — category cues, not place hardcodes).
    audience = source_kids_audience_interest(clip)
    if audience > 0:
        score = min(1.0, score + 0.10 * audience)
    # Adult meal / phone B-roll must not crowd out playground footage.
    if is_adult_meal_focus(clip):
        score = min(score, 0.38)
    return score


def kids_energy_score(source: dict[str, Any]) -> float:
    """0-1 score for kids-on-camera activity strength (used by kids_energy story arc)."""
    if is_adult_meal_focus(source):
        return 0.05
    score = 0.0
    if source_has_children(source):
        score += 0.42
    score += 0.55 * source_kids_audience_interest(source)
    if is_playful_source(source):
        score += 0.22
    setting = infer_setting(source)
    # Transit / wait B-roll stays low unless kids activity is clearly present.
    if setting in {"vehicle", "street"} and score < 0.55:
        score *= 0.4
    return round(min(1.0, score), 4)


def is_peak_kids_source(source: dict[str, Any]) -> bool:
    return kids_energy_score(source) >= 0.34


def _capture_epoch(capture_time: str) -> float | None:
    text = str(capture_time or "").strip()
    if not text:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


TIME_OF_DAY_BUCKETS = (
    (5, 11, "Morning"),
    (11, 17, "Afternoon"),
    (17, 21, "Evening"),
)


def time_of_day_bucket(capture_time: str) -> str:
    """Simple daypart label (Morning/Afternoon/Evening/Night) from a capture_time hour."""
    text = str(capture_time or "").strip()
    if len(text) < 13 or text[10] not in ("T", " "):
        return "Night"
    try:
        hour = int(text[11:13])
    except ValueError:
        return "Night"
    for start_hour, end_hour, name in TIME_OF_DAY_BUCKETS:
        if start_hour <= hour < end_hour:
            return name
    return "Night"


def cluster_contiguous_scenes(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group clips by day + time-of-day bucket (Morning/Afternoon/Evening/Night)."""
    ordered = sorted(
        items,
        key=lambda item: (
            str(item.get("_capture_time") or "9999"),
            float(item.get("start", 0) or 0),
            str(item.get("file", "")),
        ),
    )
    clusters: list[list[dict[str, Any]]] = []
    keys: list[tuple[str, str]] = []
    for item in ordered:
        capture_time = str(item.get("_capture_time") or "")
        key = (capture_time[:10], time_of_day_bucket(capture_time))
        if clusters and keys[-1] == key:
            clusters[-1].append(item)
        else:
            clusters.append([item])
            keys.append(key)
    return clusters


def _item_filename(item: dict[str, Any]) -> str:
    return str(item.get("file") or item.get("metadata", {}).get("filename", "") or "")


def _item_capture_stamp(item: dict[str, Any]) -> str:
    return str(
        item.get("_capture_time")
        or item.get("metadata", {}).get("capture_time")
        or ""
    )


def _item_daypart(item: dict[str, Any]) -> int:
    return item_daypart(_item_capture_stamp(item), _item_filename(item))


def order_scene_clusters(
    clusters: list[list[dict[str, Any]]],
    *,
    planning_scope: str = "primary_day",
) -> list[list[dict[str, Any]]]:
    """On merged days, tell one composite day: morning → midday → evening → night."""
    if normalize_planning_scope(planning_scope) != "all_days":
        return clusters

    def cluster_key(cluster: list[dict[str, Any]]) -> tuple[int, str, str]:
        daypart = min(_item_daypart(item) for item in cluster)
        first_time = min(_item_capture_stamp(item) or "9999" for item in cluster)
        first_file = min(_item_filename(item) for item in cluster)
        return daypart, first_time, first_file

    return sorted(clusters, key=cluster_key)


def rank_clips_within_scene(clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best→better by file energy; keep each file's beats in timeline order."""
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for clip in clips:
        by_file[str(clip.get("file", ""))].append(clip)

    ranked_files: list[tuple[float, bool, str, str, str, list[dict[str, Any]]]] = []
    for filename, group in by_file.items():
        energy = max(float(item.get("_energy", 0) or 0) for item in group)
        peak = any(bool(item.get("_peak")) for item in group)
        first_time = min(str(item.get("_capture_time") or "9999") for item in group)
        setting = str(group[0].get("_section_setting") or "other")
        group.sort(
            key=lambda item: (
                float(item.get("start", 0) or 0),
                float(item.get("end", 0) or 0),
            )
        )
        ranked_files.append((energy, peak, first_time, filename, setting, group))

    ranked_files.sort(key=lambda row: (-row[0], 0 if row[1] else 1, row[2], row[3]))
    # A daypart scene can mix settings (e.g. vehicle + outdoor in the same
    # morning); only drop weak takes against peaks from their own setting so
    # merging by time-of-day doesn't wipe out a whole setting's coverage.
    has_peak_by_setting: dict[str, bool] = defaultdict(bool)
    for _energy, peak, _first, _filename, setting, _group in ranked_files:
        if peak:
            has_peak_by_setting[setting] = True
    ordered: list[dict[str, Any]] = []
    for energy, peak, _first, _filename, setting, group in ranked_files:
        # If this setting already has strong kids beats, drop its weak pads.
        if has_peak_by_setting[setting] and (not peak) and energy < 0.34:
            continue
        ordered.extend(group)
    return ordered or clips


def _capture_time(clip: dict[str, Any]) -> str:
    return str(clip.get("metadata", {}).get("capture_time") or "")


def _capture_day(clip: dict[str, Any]) -> str:
    capture_time = _capture_time(clip)
    return capture_time[:10] if len(capture_time) >= 10 else ""


def unique_clips(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    selected: dict[tuple[Any, ...], dict[str, Any]] = {}
    for clip in analysis.get("clips", []):
        signature = _clip_signature(clip)
        current = selected.get(signature)
        filename = str(clip.get("metadata", {}).get("filename", ""))
        if current is None:
            selected[signature] = clip
            continue
        current_name = str(current.get("metadata", {}).get("filename", ""))
        prefer_new = _is_copy_filename(current_name) and not _is_copy_filename(filename)
        if prefer_new:
            selected[signature] = clip
    return list(selected.values())


def enrich_capture_times(analysis: dict[str, Any], footage: Path) -> bool:
    changed = False
    for clip in analysis.get("clips", []):
        metadata = clip.setdefault("metadata", {})
        if metadata.get("capture_time"):
            continue
        path = footage / str(metadata.get("filename", ""))
        if not path.is_file():
            continue
        probed = probe_video(path)
        if probed.get("capture_time"):
            metadata["capture_time"] = probed["capture_time"]
            changed = True
    return changed


def select_primary_day_clips(clips: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    undated: list[dict[str, Any]] = []
    for clip in clips:
        day = _capture_day(clip)
        if day:
            by_day[day].append(clip)
        else:
            undated.append(clip)
    if not by_day:
        return clips, "unknown"

    def day_weight(day_clips: list[dict[str, Any]]) -> float:
        return sum(float(clip.get("metadata", {}).get("duration", 0)) for clip in day_clips)

    primary_day = max(by_day, key=lambda day: day_weight(by_day[day]))
    return by_day[primary_day] + undated, primary_day


def normalize_planning_scope(raw: Any) -> str:
    text = str(raw or "primary_day").strip().lower().replace("-", "_")
    if text in {"all", "all_days", "multi", "multi_day", "multiday", "every_day"}:
        return "all_days"
    return "primary_day"


def select_planning_clips(
    clips: list[dict[str, Any]],
    *,
    planning_scope: Any = "primary_day",
) -> tuple[list[dict[str, Any]], str]:
    """Return the clip pool used for planning plus a planning_day label."""
    if normalize_planning_scope(planning_scope) == "all_days":
        return list(clips), "all_days"
    return select_primary_day_clips(clips)


def select_required_settings(
    settings_present: list[str],
    *,
    first_seen: dict[str, int],
    pool: list[dict[str, Any]],
    setting_order: list[str] | None = None,
    max_settings: int = 4,
) -> list[str]:
    """Pick up to four settings spanning the day (earliest + latest + strongest)."""
    if not settings_present:
        return []
    # Always reason in capture order; setting_order only breaks ties when filling.
    ordered = sorted(
        settings_present,
        key=lambda setting: first_seen.get(setting, 10_000),
    )
    if len(ordered) <= max_settings:
        return ordered

    ranks = {
        setting.strip().lower(): index
        for index, setting in enumerate(setting_order or [])
    }
    duration_by_setting: dict[str, float] = defaultdict(float)
    score_by_setting: dict[str, float] = defaultdict(float)
    for clip in pool:
        setting = infer_setting(clip)
        if setting not in ordered:
            continue
        duration_by_setting[setting] += max(
            0.0, float(clip.get("metadata", {}).get("duration", 0))
        )
        score_by_setting[setting] = max(score_by_setting[setting], _clip_score(clip))

    chosen: list[str] = []
    earliest = min(ordered, key=lambda setting: first_seen.get(setting, 10_000))
    latest = max(ordered, key=lambda setting: first_seen.get(setting, -1))
    for setting in (earliest, latest):
        if setting not in chosen:
            chosen.append(setting)

    def fill_key(setting: str) -> tuple[Any, ...]:
        return (
            ranks.get(setting, len(ranks)),
            -duration_by_setting.get(setting, 0.0),
            -score_by_setting.get(setting, 0.0),
            first_seen.get(setting, 10_000),
        )

    for setting in sorted(ordered, key=fill_key):
        if len(chosen) >= max_settings:
            break
        if setting not in chosen:
            chosen.append(setting)

    chosen.sort(key=lambda setting: first_seen.get(setting, 10_000))
    return chosen


def _selection_from_source(
    source: dict[str, Any],
    *,
    max_duration: float,
    avoid: list[tuple[float, float]] | None = None,
    min_duration: float | None = None,
    prefer_greeting: bool = False,
    prefer_arrival: bool = False,
    hard_cap_override: float | None = None,
) -> dict[str, Any] | None:
    metadata = source.get("metadata", {})
    source_duration = max(0.0, float(metadata.get("duration", 0)))
    min_needed = MIN_SELECTION_SEC if min_duration is None else float(min_duration)
    # Skip leftover budget crumbs that produce weird mid-sentence stubs.
    if max_duration < min_needed and source_duration >= min_needed:
        return None
    hard_cap = (
        _max_selection_for_source(source) if hard_cap_override is None else hard_cap_override
    )
    requested = min(hard_cap, source_duration, max(0.0, max_duration))
    if requested < 0.35:
        return None
    start, end = select_excerpt(
        source,
        requested,
        avoid=avoid,
        prefer_greeting=prefer_greeting,
        prefer_arrival=prefer_arrival,
    )
    duration = end - start
    if duration < 0.35:
        return None
    if duration + 1e-6 < min(min_needed, source_duration, requested) and source_duration >= min_needed:
        return None
    segments = source.get("audio", {}).get("segments", [])
    subtitle = " ".join(
        str(segment.get("text", "")).strip()
        for segment in segments
        if float(segment.get("end", 0)) > start and float(segment.get("start", 0)) < end
    ).strip()
    note = str(source.get("visual", {}).get("summary", ""))
    if is_playful_source(source):
        note = f"{note} Keep fooling-around / play beat.".strip()
    hooks = source_kids_audience_categories(source)
    if hooks:
        note = f"{note} Kids-audience hooks: {', '.join(hooks)}.".strip()
    wow = source_wow_summary(source, start=start, end=end)
    return {
        "file": str(metadata.get("filename", "")),
        "start": round(start, 3),
        "end": round(end, 3),
        "note": note,
        "subtitle": subtitle,
        "wow": wow,
        "_capture_time": _capture_time(source),
        "_setting": infer_setting(source),
        "_score": _clip_score(source),
        "_playful": is_playful_source(source),
        "_greeting": is_greeting_source(source),
        "_arrival": is_arrival_source(source),
        "_energy": kids_energy_score(source),
        "_peak": is_peak_kids_source(source) or is_arrival_source(source),
        "_kids_hooks": hooks,
    }


def _chapter_selection(
    source: dict[str, Any],
    start: float,
    end: float,
) -> dict[str, Any] | None:
    """A plain time-window beat from a long take — no speech anchor required.

    Used to chapterize a single long clip when speech-anchored selection has
    run out of transcript to work with. Enrichment mirrors
    ``_selection_from_source`` so downstream (validation, dashboard, gameplay
    matching, render) treats it identically.
    """
    metadata = source.get("metadata", {})
    source_duration = max(0.0, float(metadata.get("duration", 0)))
    start = max(0.0, float(start))
    end = min(source_duration, float(end))
    if end - start < MIN_SELECTION_SEC - 1e-6:
        return None
    segments = source.get("audio", {}).get("segments", [])
    subtitle = " ".join(
        str(segment.get("text", "")).strip()
        for segment in segments
        if float(segment.get("end", 0)) > start and float(segment.get("start", 0)) < end
    ).strip()
    note = str(source.get("visual", {}).get("summary", "")).strip()
    hooks = source_kids_audience_categories(source)
    if hooks:
        note = f"{note} Kids-audience hooks: {', '.join(hooks)}.".strip()
    note = f"{note} Chapter beat.".strip()
    return {
        "file": str(metadata.get("filename", "")),
        "start": round(start, 3),
        "end": round(end, 3),
        "note": note,
        "subtitle": subtitle,
        "wow": source_wow_summary(source, start=start, end=end),
        "_capture_time": _capture_time(source),
        "_setting": infer_setting(source),
        "_score": _clip_score(source),
        "_playful": is_playful_source(source),
        "_greeting": False,
        "_arrival": False,
        "_energy": kids_energy_score(source),
        "_peak": is_peak_kids_source(source),
        "_kids_hooks": hooks,
    }


def _pick_open_source(
    pool: list[dict[str, Any]],
    *,
    planning_scope: str = "primary_day",
) -> dict[str, Any] | None:
    """Prefer early departure greeting; keep arrival for its own beat when possible."""
    if not pool:
        return None
    usable = [
        clip
        for clip in pool
        if float(clip.get("metadata", {}).get("duration", 0) or 0) >= MIN_OPEN_CTA_SEC
    ]
    if not usable:
        return None
    if normalize_planning_scope(planning_scope) == "all_days":
        usable = sorted(
            usable,
            key=lambda clip: (
                _item_daypart(clip),
                _capture_time(clip) or "9999",
                str(clip.get("metadata", {}).get("filename", "")),
            ),
        )
        earliest_daypart = min(_item_daypart(clip) for clip in usable)
        usable = [clip for clip in usable if _item_daypart(clip) == earliest_daypart]
    # Search a wider early window for spoken greetings so car-hello beats are not missed.
    greet_count = max(1, int(len(usable) * 0.40 + 0.999))
    greet_window = usable[:greet_count]
    # Prefer departure/hello over "udah sampai" so arrival can be section 2.
    depart = [clip for clip in greet_window if is_departure_open_source(clip)]
    strong_depart = [clip for clip in depart if is_strong_greeting_source(clip)]
    if strong_depart:
        return strong_depart[0]
    if depart:
        return depart[0]
    strong = [clip for clip in greet_window if is_strong_greeting_source(clip)]
    if strong:
        return strong[0]
    greeting = [clip for clip in greet_window if is_greeting_source(clip)]
    if greeting:
        return greeting[0]
    # Playful cold-open stays in the first quartile so mid-morning play does not
    # become the open and lock out earlier transit / setting coverage.
    play_count = max(1, int(len(usable) * 0.25 + 0.999))
    playful = [clip for clip in usable[:play_count] if is_playful_source(clip)]
    if playful:
        return playful[0]
    return None


def _pick_arrival_source(
    pool: list[dict[str, Any]],
    *,
    not_before_capture_time: str = "",
    exclude_files: set[str] | None = None,
) -> dict[str, Any] | None:
    """First spoken destination arrival after the open bookend."""
    excluded = exclude_files or set()
    candidates: list[dict[str, Any]] = []
    for clip in pool:
        filename = str(clip.get("metadata", {}).get("filename", ""))
        if filename in excluded:
            continue
        if float(clip.get("metadata", {}).get("duration", 0) or 0) < MIN_OPEN_CTA_SEC:
            continue
        if not is_arrival_source(clip):
            continue
        capture = _capture_time(clip) or ""
        if not_before_capture_time and capture and capture < not_before_capture_time:
            continue
        candidates.append(clip)
    if not candidates:
        return None
    candidates.sort(
        key=lambda clip: (
            0 if is_strong_greeting_source(clip) else 1,
            _capture_time(clip) or "9999",
            str(clip.get("metadata", {}).get("filename", "")),
        )
    )
    return candidates[0]


def _cta_close_candidates(
    pool: list[dict[str, Any]],
    *,
    not_before_capture_time: str,
    not_before_daypart: int | None = None,
) -> list[dict[str, Any]]:
    """Late usable close candidates, best-first (explicit CTA, then cute, then latest)."""
    if not pool:
        return []
    usable = [
        clip
        for clip in pool
        if float(clip.get("metadata", {}).get("duration", 0) or 0) >= MIN_OPEN_CTA_SEC
        and (
            (
                not_before_daypart is not None
                and _item_daypart(clip) >= not_before_daypart
            )
            or (
                not_before_daypart is None
                and (
                    not not_before_capture_time
                    or (_capture_time(clip) or "9999") >= not_before_capture_time
                )
            )
        )
    ]
    if not usable:
        return []
    if not_before_daypart is not None:
        latest_part = max(_item_daypart(clip) for clip in usable)
        late = [clip for clip in usable if _item_daypart(clip) == latest_part]
    else:
        late_count = max(1, int(len(usable) * 0.30 + 0.999))
        late = usable[-late_count:]
    explicit = [clip for clip in late if CTA_RE.search(_source_text(clip))]
    cute = [
        clip
        for clip in late
        if is_cta_source(clip)
        or float(clip.get("visual", {}).get("story_value", 0) or 0) >= 0.7
    ]
    ordered: list[dict[str, Any]] = []
    for group in (list(reversed(explicit)), list(reversed(cute)), list(reversed(late))):
        for clip in group:
            if clip not in ordered:
                ordered.append(clip)
    return ordered


def build_balanced_fallback_plan(
    analysis: dict[str, Any],
    target_duration: float,
    *,
    title: str,
    setting_order: list[str] | None = None,
    story_arc: str = "scene_energy",
    planning_scope: Any = "primary_day",
) -> dict[str, Any]:
    unique = unique_clips(analysis)
    scope = normalize_planning_scope(planning_scope)
    pool, primary_day = select_planning_clips(unique, planning_scope=scope)
    if not pool:
        raise ValueError("Cannot build a fallback plan without analyzed clips")

    raw_arc = str(story_arc or "scene_energy").strip().lower()
    if raw_arc in {"chrono", "chronological", "capture"}:
        arc = "chronological"
    elif raw_arc in {"kids_energy", "energy", "kids"}:
        arc = "kids_energy"
    else:
        # Default: scenes in capture order; best→better energy inside each scene.
        arc = "scene_energy"
    pool.sort(
        key=lambda clip: (
            _capture_time(clip) or "9999",
            str(clip.get("metadata", {}).get("filename", "")),
        )
    )

    desired_total = target_duration * 0.95
    # No per-setting diversity cap and no required-settings reservation: one
    # setting may dominate the plan if that's genuinely where the
    # strongest/most abundant footage is (removed per explicit request — was
    # rationing good footage down for balance).
    selected: list[dict[str, Any]] = []
    selected_files: set[str] = set()
    selected_ranges: dict[str, list[tuple[float, float]]] = defaultdict(list)
    total = 0.0
    # Leave room for validator speech/segment expansion so multi-beats do not overlap.
    AVOID_PAD_SEC = 6.5
    open_selection: dict[str, Any] | None = None
    cta_selection: dict[str, Any] | None = None

    def _commit(selection: dict[str, Any]) -> None:
        nonlocal total
        filename = str(selection["file"])
        selected.append(selection)
        selected_files.add(filename)
        span = (float(selection["start"]), float(selection["end"]))
        selected_ranges[filename].append(
            (max(0.0, span[0] - AVOID_PAD_SEC), span[1] + AVOID_PAD_SEC)
        )
        total += span[1] - span[0]

    # Reserve greeting / start-of-day open first; CTA is chosen after the middle so it
    # cannot starve the body by locking an early upper capture bound.
    open_source = _pick_open_source(pool, planning_scope=scope)
    if open_source is not None:
        candidate = _selection_from_source(
            open_source,
            max_duration=OPEN_CTA_BEAT_SEC,
            min_duration=MIN_OPEN_CTA_SEC,
            prefer_greeting=True,
        )
        if candidate is not None:
            candidate["_role"] = "open"
            note = str(candidate.get("note", "")).strip()
            if candidate.get("_greeting") or is_strong_greeting_source(open_source):
                candidate["note"] = f"{note} Greeting / start-of-day open.".strip()
            else:
                candidate["note"] = f"{note} Playful cold-open.".strip()
            open_selection = candidate
            _commit(candidate)

    open_time = str(open_selection.get("_capture_time") or "") if open_selection else ""
    open_file = str(open_selection.get("file") or "") if open_selection else ""
    arrival_selection: dict[str, Any] | None = None
    # A global "Arrival" section works for one capture day, but in an all-days
    # episode it can pull a later day's arrival ahead of earlier-day scenes.
    # Let arrivals participate in their natural chronological scene instead.
    arrival_source = (
        None
        if scope == "all_days"
        else _pick_arrival_source(
            pool,
            not_before_capture_time=open_time,
            exclude_files={open_file} if open_file else set(),
        )
    )
    if arrival_source is not None:
        candidate = _selection_from_source(
            arrival_source,
            max_duration=max(OPEN_CTA_BEAT_SEC, 28.0),
            min_duration=MIN_OPEN_CTA_SEC,
            prefer_arrival=True,
        )
        if candidate is not None and str(candidate.get("subtitle") or "").strip():
            candidate["_role"] = "arrival"
            note = str(candidate.get("note", "")).strip()
            candidate["note"] = f"{note} Destination arrival narration.".strip()
            arrival_selection = candidate
            _commit(candidate)

    # Hold budget for the eventual CTA close.
    cta_reserve = OPEN_CTA_BEAT_SEC
    middle_cap = max(0.0, desired_total - cta_reserve)

    def _in_middle_window(source: dict[str, Any]) -> bool:
        capture = _capture_time(source) or "9999"
        if open_time and capture < open_time:
            return False
        return True

    # Middle content: no per-setting diversity cap, no required-settings
    # reservation, no peak/low-activity split, no repeated highlight beats.
    # If the remaining footage fits under the pacing cap, keep all of it —
    # one selection per clip, at full length. If it doesn't fit, keep the
    # highest-energy clips first and let the weakest miss the cut; a
    # speechless transit pad is excluded outright since it carries no energy
    # to judge either way.
    remaining_pool = [
        clip
        for clip in pool
        if str(clip.get("metadata", {}).get("filename", "")) not in selected_files
        and _in_middle_window(clip)
        and not is_speechless_transit_pad(clip)
    ]
    remaining_duration = sum(
        max(0.0, float(clip.get("metadata", {}).get("duration", 0))) for clip in remaining_pool
    )
    fill_order = (
        remaining_pool
        if remaining_duration <= middle_cap
        else sorted(remaining_pool, key=_clip_score, reverse=True)
    )
    for source in fill_order:
        if total >= middle_cap:
            break
        filename = str(source.get("metadata", {}).get("filename", ""))
        source_duration = float(source.get("metadata", {}).get("duration", 0))
        budget = min(source_duration, middle_cap - total)
        selection = _selection_from_source(
            source,
            max_duration=budget,
            avoid=selected_ranges.get(filename, []),
            hard_cap_override=source_duration,
            # MIN_SELECTION_SEC exists to avoid tiny leftover-budget stubs
            # from repeated beats on one clip; irrelevant here since each
            # clip gets exactly one selection, and rejecting a short but
            # genuine excerpt would just drop the clip outright.
            min_duration=0.35,
        )
        if selection is None:
            continue
        selection["_role"] = "middle"
        _commit(selection)

    # Chapterize a thin clip pool. The pass above takes one speech-anchored
    # excerpt per source, so a shoot that is a single long take (e.g. one
    # camera clip + one screen recording) only ever contributes ~one beat and
    # lands far under the pacing budget — and any stretch the transcript does
    # not cover (silent gameplay, wordless reactions) is invisible to
    # select_excerpt entirely. When the one-excerpt pass leaves the plan
    # short, walk the sources already in play forward in fixed chapters,
    # taking every stretch that does not overlap an existing pick, until the
    # budget is met. The time-skip cards land between the resulting chapters.
    # Guard rails: only stitch extra beats from one source when the shoot is
    # genuinely a thin pool (one long take, or a camera clip paired with a
    # screen recording) AND the one-excerpt pass has left the plan under the
    # duration validator's floor, where it would be rejected outright. A normal
    # multi-clip shoot keeps its single continuous excerpt per clip.
    CHAPTER_BEAT_SEC = PLAY_MAX_SELECTION_SEC
    CHAPTER_GAP_SEC = 1.0
    CHAPTER_MAX_DISTINCT_SOURCES = 2
    chapter_floor = target_duration * 0.65
    chapter_target = min(middle_cap, chapter_floor + CHAPTER_BEAT_SEC)
    chapter_sources = sorted(
        (
            source
            for source in pool
            if str(source.get("metadata", {}).get("filename", "")) in selected_files
            and _in_middle_window(source)
            and not is_speechless_transit_pad(source)
        ),
        key=lambda source: (
            _capture_time(source) or "9999",
            str(source.get("metadata", {}).get("filename", "")),
        ),
    )
    if (
        len(selected_files) <= CHAPTER_MAX_DISTINCT_SOURCES
        and total < chapter_floor
        and total + MIN_SELECTION_SEC <= chapter_target
    ):
        for source in chapter_sources:
            metadata = source.get("metadata", {})
            filename = str(metadata.get("filename", ""))
            source_duration = max(0.0, float(metadata.get("duration", 0)))
            cursor = 0.0
            while (
                total + MIN_SELECTION_SEC <= chapter_target
                and cursor + MIN_SELECTION_SEC <= source_duration
            ):
                beat_end = min(
                    source_duration, cursor + min(CHAPTER_BEAT_SEC, chapter_target - total)
                )
                blocker = next(
                    (
                        span
                        for span in sorted(selected_ranges.get(filename, []))
                        if min(beat_end, span[1]) - max(cursor, span[0]) > 0.35
                    ),
                    None,
                )
                if blocker is not None:
                    if blocker[0] - cursor >= MIN_SELECTION_SEC:
                        beat_end = blocker[0]
                    else:
                        cursor = blocker[1] + CHAPTER_GAP_SEC
                        continue
                selection = _chapter_selection(source, cursor, beat_end)
                if selection is not None:
                    selection["_role"] = "middle"
                    _commit(selection)
                cursor = beat_end + CHAPTER_GAP_SEC

    # CTA close after the body — last in the story, not necessarily last calendar day.
    last_body_time = ""
    last_body_daypart = 0
    for item in selected:
        capture = str(item.get("_capture_time") or "")
        if capture >= last_body_time:
            last_body_time = capture
        last_body_daypart = max(last_body_daypart, _item_daypart(item))
    cta_daypart = last_body_daypart if scope == "all_days" else None
    for cta_source in _cta_close_candidates(
        pool,
        not_before_capture_time=last_body_time or open_time,
        not_before_daypart=cta_daypart,
    ):
        filename = str(cta_source.get("metadata", {}).get("filename", ""))
        candidate = _selection_from_source(
            cta_source,
            max_duration=OPEN_CTA_BEAT_SEC,
            min_duration=MIN_OPEN_CTA_SEC,
            avoid=selected_ranges.get(filename, []),
        )
        if candidate is None:
            continue
        if scope == "all_days":
            if _item_daypart(candidate) < last_body_daypart:
                continue
        else:
            cta_time = str(candidate.get("_capture_time") or "")
            if last_body_time and cta_time < last_body_time:
                continue
        candidate["_role"] = "cta"
        note = str(candidate.get("note", "")).strip()
        candidate["note"] = f"{note} CTA close b-roll.".strip()
        cta_selection = candidate
        _commit(candidate)
        break

    # If the day already ended in the body, promote the last story beat.
    if cta_selection is None:
        middle_sorted = [
            item for item in selected if item.get("_role") == "middle"
        ]
        if scope == "all_days":
            middle_sorted.sort(
                key=lambda item: (
                    _item_daypart(item),
                    str(item.get("_capture_time") or "9999"),
                    float(item.get("start", 0)),
                    str(item.get("file", "")),
                )
            )
        else:
            middle_sorted.sort(
                key=lambda item: (
                    str(item.get("_capture_time") or "9999"),
                    float(item.get("start", 0)),
                    str(item.get("file", "")),
                )
            )
        if middle_sorted:
            item = middle_sorted[-1]
            item["_role"] = "cta"
            note = str(item.get("note", "")).strip()
            item["note"] = f"{note} CTA close b-roll.".strip()
            cta_selection = item

    if arc == "chronological":
        selected.sort(
            key=lambda clip: (
                str(clip.get("_capture_time") or "9999"),
                float(clip.get("start", 0)),
                str(clip.get("file", "")),
            )
        )
    elif scope == "all_days":
        selected.sort(
            key=lambda clip: (
                0
                if clip.get("_role") == "open"
                else 1
                if clip.get("_role") == "arrival"
                else 2
                if clip.get("_role") == "middle"
                else 3,
                _item_daypart(clip),
                str(clip.get("_capture_time") or "9999"),
                float(clip.get("start", 0)),
                str(clip.get("file", "")),
            )
        )
    else:
        # Keep open earliest / arrival next / CTA latest; middle reordered later.
        selected.sort(
            key=lambda clip: (
                0
                if clip.get("_role") == "open"
                else 1
                if clip.get("_role") == "arrival"
                else 2
                if clip.get("_role") == "middle"
                else 3,
                str(clip.get("_capture_time") or "9999"),
                float(clip.get("start", 0)),
                str(clip.get("file", "")),
            )
        )

    # If CTA ended up not last after chrono sort (same-time edge), keep role tags for sections.
    structure: list[dict[str, Any]] = []
    open_clips: list[dict[str, Any]] = []
    arrival_clips: list[dict[str, Any]] = []
    cta_clips: list[dict[str, Any]] = []
    middle_items: list[dict[str, Any]] = []
    for item in selected:
        role = str(item.pop("_role", "middle") or "middle")
        item.pop("_score", None)
        item.pop("_playful", None)
        is_greeting_open = bool(item.pop("_greeting", False))
        is_arrival = bool(item.pop("_arrival", False))
        hooks = [str(h) for h in (item.pop("_kids_hooks", []) or []) if str(h).strip()]
        setting = str(item.pop("_setting", "other"))
        item["_section_setting"] = setting
        item["_section_hooks"] = hooks
        if role == "open":
            item.pop("_capture_time", None)
            item.pop("_energy", None)
            item.pop("_peak", None)
            item["_greeting_open"] = is_greeting_open or (
                "Greeting / start-of-day open" in str(item.get("note", ""))
            )
            open_clips.append(item)
        elif role == "arrival":
            item.pop("_capture_time", None)
            item.pop("_energy", None)
            item.pop("_peak", None)
            item["_arrival_beat"] = is_arrival or (
                "Destination arrival" in str(item.get("note", ""))
            )
            arrival_clips.append(item)
        elif role == "cta":
            item.pop("_capture_time", None)
            item.pop("_energy", None)
            item.pop("_peak", None)
            cta_clips.append(item)
        else:
            middle_items.append(item)

    def _storyboard_section(setting: str, clips: list[dict[str, Any]]) -> dict[str, Any]:
        hook_counts: dict[str, int] = defaultdict(int)
        for clip in clips:
            for hook in clip.pop("_section_hooks", []) or []:
                hook_counts[str(hook)] += 1
            clip.pop("_section_setting", None)
            clip.pop("_capture_time", None)
            clip.pop("_energy", None)
            clip.pop("_peak", None)
        top_hooks = sorted(hook_counts, key=lambda name: (-hook_counts[name], name))[:2]
        label = setting.replace("_", " ").title()
        if top_hooks:
            hook_label = " + ".join(h.replace("_", " ") for h in top_hooks)
            section = f"{label} — Kids {hook_label}"
            description = (
                f"Kids-audience storyboard beat in {setting}: prioritize {hook_label} "
                f"with children on camera when available."
            )
        else:
            section = f"{label} — Best moments"
            description = (
                f"Chronological {setting} coverage; prefer children and kid-interesting activity."
            )
        return {"section": section, "description": description, "clips": clips}

    def _energy_band_section(
        title: str,
        description: str,
        clips: list[dict[str, Any]],
    ) -> dict[str, Any]:
        hook_counts: dict[str, int] = defaultdict(int)
        cleaned: list[dict[str, Any]] = []
        for clip in clips:
            for hook in clip.pop("_section_hooks", []) or []:
                hook_counts[str(hook)] += 1
            clip.pop("_section_setting", None)
            clip.pop("_capture_time", None)
            clip.pop("_energy", None)
            clip.pop("_peak", None)
            cleaned.append(clip)
        if hook_counts:
            top = sorted(hook_counts, key=lambda name: (-hook_counts[name], name))[:2]
            hook_label = " + ".join(h.replace("_", " ") for h in top)
            title = f"{title} — {hook_label}"
        return {"section": title, "description": description, "clips": cleaned}

    def _split_peak_bands(
        clips: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not clips:
            return [], []
        ranked = sorted(
            clips,
            key=lambda item: (
                -float(item.get("_energy", 0) or 0),
                str(item.get("_capture_time") or "9999"),
                float(item.get("start", 0)),
                str(item.get("file", "")),
            ),
        )
        total_dur = sum(float(item["end"]) - float(item["start"]) for item in ranked)
        if len(ranked) == 1 or total_dur <= 0:
            return ranked, []
        midpoint = total_dur / 2.0
        first: list[dict[str, Any]] = []
        second: list[dict[str, Any]] = []
        running = 0.0
        for item in ranked:
            dur = float(item["end"]) - float(item["start"])
            if not first or (running < midpoint and len(second) == 0 and running + dur <= midpoint * 1.15):
                first.append(item)
                running += dur
            else:
                second.append(item)
        if not second and len(first) > 1:
            second = [first.pop()]
        # Local capture flow inside each peak band.
        first.sort(
            key=lambda item: (
                str(item.get("_capture_time") or "9999"),
                float(item.get("start", 0)),
                str(item.get("file", "")),
            )
        )
        second.sort(
            key=lambda item: (
                str(item.get("_capture_time") or "9999"),
                float(item.get("start", 0)),
                str(item.get("file", "")),
            )
        )
        return first, second

    if open_clips:
        greeting_open = False
        for item in open_clips:
            item.pop("_section_setting", None)
            item.pop("_section_hooks", None)
            greeting_open = greeting_open or bool(item.pop("_greeting_open", False))
        structure.append(
            {
                "section": "Greeting open" if greeting_open else "Playful open",
                "description": (
                    "Start-of-day greeting / departure from early real footage."
                    if greeting_open
                    else "Kids-audience cold-open: early fun / fooling-around with children when possible."
                ),
                "clips": open_clips,
            }
        )

    if arrival_clips:
        for item in arrival_clips:
            item.pop("_section_setting", None)
            item.pop("_section_hooks", None)
            item.pop("_arrival_beat", None)
        structure.append(
            {
                "section": "Arrival",
                "description": (
                    "Spoken destination arrival ('udah sampai…') — place the day before play peaks."
                ),
                "clips": arrival_clips,
            }
        )

    if arc == "kids_energy":
        peak_items = [item for item in middle_items if item.get("_peak")]
        low_items = [item for item in middle_items if not item.get("_peak")]
        peak1, peak2 = _split_peak_bands(peak_items)
        low_items.sort(
            key=lambda item: (
                str(item.get("_capture_time") or "9999"),
                float(item.get("start", 0)),
                str(item.get("file", "")),
            )
        )
        if peak1:
            structure.append(
                _energy_band_section(
                    "Kids peak",
                    "Highest kids-on-camera activity from the day (play, animals, water, treats).",
                    peak1,
                )
            )
        if peak2:
            structure.append(
                _energy_band_section(
                    "Kids peak 2",
                    "Second wave of strong kids activity beats.",
                    peak2,
                )
            )
        if low_items:
            structure.append(
                _energy_band_section(
                    "Quiet / adult",
                    "Lower-energy wait, transit, and adult moments — kept shorter and before goodbye.",
                    low_items,
                )
            )
    elif arc == "scene_energy":
        # Simple time-of-day scenes (Morning/Afternoon/Evening/Night), not setting
        # buckets. Inside each scene, rank files best→better; keep each file's
        # beats in order. On merged multi-day pools, reorder the composite day
        # morning->midday->evening->night instead of by calendar date.
        for cluster in order_scene_clusters(
            cluster_contiguous_scenes(middle_items),
            planning_scope=scope,
        ):
            if not cluster:
                continue
            ranked = rank_clips_within_scene(cluster)
            daypart = time_of_day_bucket(str(ranked[0].get("_capture_time") or ""))
            structure.append(_storyboard_section(daypart, ranked))
    else:
        current_setting: str | None = None
        current_clips: list[dict[str, Any]] = []
        for item in middle_items:
            setting = str(item.pop("_section_setting", "other"))
            if setting != current_setting and current_clips:
                structure.append(_storyboard_section(current_setting, current_clips))
                current_clips = []
            current_setting = setting
            current_clips.append(item)
        if current_setting and current_clips:
            structure.append(_storyboard_section(current_setting, current_clips))

    if cta_clips:
        for item in cta_clips:
            item.pop("_section_setting", None)
            item.pop("_section_hooks", None)
        structure.append(
            {
                "section": "CTA close",
                "description": (
                    "Kids-audience close: smile / thanks / bye / night goodbye from late footage."
                ),
                "clips": cta_clips,
            }
        )

    if scope == "all_days":
        day_note = (
            " Planning scope: all capture days, assembled as one composite day "
            "(morning → midday → evening → night) so night footage never jumps "
            "ahead of later-day daylight."
        )
    elif primary_day not in {"", "unknown"}:
        day_note = f" Primary capture day: {primary_day}."
    else:
        day_note = ""
    if arc == "kids_energy":
        editing_notes = (
            "Kids-energy story arc: greeting/open → kids peak → kids peak 2 → "
            "quiet/adult filler → goodbye. Priority 1: children on camera. "
            "Priority 2: kid-interesting activity (play structures, animals, water play, "
            "treats, discovery, rides). Low-activity transit/adult beats are capped and "
            "placed before the CTA. Within each band, clips stay capture-time ordered."
            f"{day_note}"
        )
    elif arc == "scene_energy":
        editing_notes = (
            "Scene-energy story arc: greeting/open → contiguous day scenes in time order → "
            "goodbye. Scenes split when setting/time jumps (so morning car stays separate from "
            "night car). Inside each scene, files are ranked best→better by kids energy while "
            "each file's beats stay timeline-ordered. Weak transit pads are dropped when a "
            "scene already has strong kids beats."
            f"{day_note}"
        )
    else:
        diversity_note = (
            "across the selected capture days"
            if scope == "all_days"
            else "within one capture day (including late-day evening beats)"
        )
        editing_notes = (
            "Kids-audience chronological storyboard. Priority 1: children on camera/speaking. "
            "Priority 2: kid-interesting activity categories (play structures, animals/creatures, "
            "water play, treats, discovery/wonder, rides) — never place hardcodes. "
            f"Keep setting diversity {diversity_note}, "
            "preserve complete play narration, and bookend with a greeting/start-of-day open "
            "(or playful open if no greeting) plus a fuller CTA/goodbye close when footage allows."
            f"{day_note}"
        )
    return {
        "title": title,
        "structure": structure,
        "bgm_suggestion": "",
        "editing_notes": editing_notes,
        "planning_day": primary_day,
        "planning_scope": scope,
        "story_arc": arc,
    }


def resolve_target_duration(
    configured: Any,
    analysis: dict[str, Any],
    *,
    planning_scope: Any = "primary_day",
) -> tuple[float, str]:
    is_auto = configured is None or (
        isinstance(configured, str) and configured.strip().lower() == "auto"
    )
    scope = normalize_planning_scope(planning_scope)
    if not is_auto:
        try:
            target = float(configured)
        except (TypeError, ValueError) as exc:
            raise ValueError("target_duration_sec must be a positive number or 'auto'") from exc
        if target <= 0:
            raise ValueError("target_duration_sec must be a positive number or 'auto'")
        return target, "explicit"

    unique = unique_clips(analysis)
    pool, _ = select_planning_clips(unique, planning_scope=scope)
    if not pool:
        raise ValueError("Cannot determine an automatic duration without analyzed clips")

    total_duration = sum(
        max(0.0, float(clip.get("metadata", {}).get("duration", 0))) for clip in pool
    )

    # Target how much footage is actually worth keeping, not a fixed slice of
    # total raw footage — a shoot that's mostly strong content shouldn't lose
    # most of it just because that's most of what you shot. Each clip's
    # duration counts toward the budget in proportion to its _clip_score:
    # near-zero below WORTHY_LOW (weak/filler), full credit at/above
    # WORTHY_HIGH (clearly worth keeping), linear in between.
    WORTHY_LOW = 0.35
    WORTHY_HIGH = 0.85
    worthy_duration = 0.0
    for clip in pool:
        duration = max(0.0, float(clip.get("metadata", {}).get("duration", 0)))
        score = _clip_score(clip)
        if score <= WORTHY_LOW:
            weight = 0.0
        elif score >= WORTHY_HIGH:
            weight = 1.0
        else:
            weight = (score - WORTHY_LOW) / (WORTHY_HIGH - WORTHY_LOW)
        worthy_duration += duration * weight

    # Cap is a pacing ceiling (how long a final video should run), not a
    # discard mechanism — abundant good footage should fill it, not be
    # rationed down to a fraction of it.
    duration_cap = 2700.0 if scope == "all_days" else 1800.0
    target = min(worthy_duration, duration_cap)
    if total_duration >= 150:
        target = max(target, 120.0)
    else:
        target = max(target, min(30.0, total_duration * 0.8))
    target = max(1.0, round(target / 15.0) * 15.0)
    return target, "auto"


def _compact_analysis(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for clip in analysis.get("clips", []):
        metadata = clip["metadata"]
        audio = clip.get("audio", {})
        visual = clip.get("visual", {})
        compact.append(
            {
                "file": metadata["filename"],
                "duration": metadata["duration"],
                "capture_time": metadata.get("capture_time"),
                "orientation": metadata["orientation"],
                "summary": visual.get("summary", ""),
                "setting": infer_setting(clip),
                "shot_type": visual.get("shot_type", ""),
                "mood": visual.get("mood", ""),
                "quality": visual.get("quality", 0),
                "motion": visual.get("motion", 0),
                "story_value": visual.get("story_value", 0),
                "kids_audience_categories": source_kids_audience_categories(clip),
                "kids_audience_interest": round(source_kids_audience_interest(clip), 3),
                "has_children": source_has_children(clip),
                "issues": visual.get("issues", []),
                "recommended_ranges": visual.get("recommended_ranges", []),
                "transcript": str(audio.get("text", ""))[:400],
                "speech_segments": audio.get("segments", [])[:12],
            }
        )
    compact.sort(key=lambda item: (str(item.get("capture_time") or "9999"), str(item["file"])))
    return compact


def _planning_prompt(episode: Episode, compact: list[dict[str, Any]], target: float) -> str:
    scope = normalize_planning_scope(episode.config.get("planning_scope", "primary_day"))
    if scope == "all_days":
        day_rule = (
            "- Use footage from every capture day present in the analysis. Assemble "
            "one composite day: morning → midday → evening → night. Later calendar "
            "days may appear before earlier-day night footage. Do not cut night home "
            "immediately after a morning greeting when later daylight still exists."
        )
    else:
        day_rule = (
            "- Prefer one primary capture day when multiple days are present."
        )
    return f"""
Act as a decisive travel-vlog editor. Build a coherent edit from the clip analysis below.
Title/theme: {episode.config['title']}
Style: {episode.config['style']}
Target duration: {target:.0f} seconds
Planning scope: {scope}

Rules:
- Keep the final edit in capture_time order. Never jump backward in capture time.
{day_rule}
- Prefer original filenames over duplicate "Copy" files.
- Use a hook, development, highlight, and emotional close; 4-10 sections total.
- Prefer landscape, high quality, motion and story value. Skip poor/redundant footage.
- Storyboard for a kids audience:
  1) Primary: children visible on camera (subjects/summary), not ASR name-drops alone.
  2) Secondary: kid-interesting ACTIVITY CATEGORIES from what is on camera — play
     structures, animals/creatures, water play, treats/sweets, discovery/wonder, rides.
     Prefer these over flat adult talk, adult-only eating/phone B-roll, or empty scenery.
     Do NOT hardcode specific places; pick whatever kid-interesting activity analysis shows.
  3) Do not spend long multi-beat stretches on adult meal scenes when unused playground /
     play / animal footage still exists later in the day.
- Name sections like a kids storyboard when helpful (e.g. "Outdoor — Kids water play"),
  still keeping capture chronology.
- Pure visual shots usually last 2-6 seconds. Preserve complete useful speech.
- Kids fooling-around / play / laugh narration is high value — keep complete exchanges
  (often 20-55s, longer if the take stays engaging). Do not strip playful speech just to
  hit pacing.
- Open with a short greeting / start-of-day beat (about 12-22s) from early real footage
  when available (assalamualaikum / hai / pagi ini / mau kemana / arrival). Fall back to
  a playful cold-open only if no greeting exists.
- Close with a CTA-style goodbye beat (smile / thanks / bye / wave / night pulang,
  about 12-24s) from late real footage. Do not invent clips. Keep capture_time order
  (open earliest, CTA latest).
- Never start or end a spoken selection mid-sentence when a nearby ASR segment boundary fits.
- No diversity requirement: use whichever footage is actually good, even if that means
  one location or activity dominates the video. Judge selections purely on how
  interesting/engaging they are, not on covering a spread of settings.
- If the day's usable footage fits within the target duration, include essentially all of
  it rather than trimming for pacing. Only cut when there is genuinely more usable footage
  than the target duration allows, and then cut the least engaging material first.
- Skip crumb cuts under ~10s when the source has a fuller beat.
- Ranges must remain within source duration and may use a source more than once only for distinct ranges.
- Aim within 25% of target duration. Do not fabricate filenames or transcript.

Return only JSON:
{{
  "title": "title",
  "structure": [
    {{
      "section": "Section name — subtitle",
      "description": "purpose",
      "clips": [
        {{"file": "exact filename", "start": 0.0, "end": 4.0,
          "note": "visual purpose", "subtitle": "exact retained speech or empty"}}
      ]
    }}
  ],
  "bgm_suggestion": "genre, mood, instruments, tempo and bpm",
  "editing_notes": "short rationale"
}}

Clip analysis (already sorted by capture_time):
{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}
""".strip()


def _attach_audio(
    episode: Episode,
    plan: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    densified = plan_audio_cues(episode, plan, analysis)
    density = str(episode.config.get("audio", {}).get("sfx_density", "medium")).lower()
    min_gap = {"light": 10.0, "medium": 6.0, "heavy": 3.5}.get(density, 6.0)
    audio_errors = validate_audio_plan(densified, episode.root, min_gap=min_gap)
    if audio_errors:
        raise ValueError("Audio plan is invalid:\n- " + "\n- ".join(audio_errors))
    return densified


def _save_plan(
    episode: Episode,
    plan: dict[str, Any],
    *,
    target_duration: float,
    target_mode: str,
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = attach_gameplay_matches(episode, plan)
    if analysis is not None:
        plan = _attach_audio(episode, plan, analysis)
    plan["target_duration_sec"] = target_duration
    plan["target_duration_mode"] = target_mode
    write_json(episode.plan_path, plan)
    if episode.approval_path.exists():
        episode.approval_path.unlink()
    cue_count = len(plan.get("audio_cues") or [])
    print(f"Wrote {episode.plan_path} ({plan['duration_sec']:.1f}s, {cue_count} SFX cues)")
    duration_sec = float(plan.get("duration_sec") or 0)
    if duration_sec < MIN_RECOMMENDED_DURATION_SEC:
        minutes = duration_sec / 60.0
        print(
            f"Warning: plan is only {minutes:.1f} min, below the 7 min minimum. "
            "Loosen the cut for more runtime — set a higher target_duration_sec "
            "(e.g. 480 or more) in the episode config and re-run 've plan'."
        )
    return plan


def _prepare_analysis(episode: Episode) -> dict[str, Any]:
    analysis = read_json(episode.analysis_path)
    if enrich_capture_times(analysis, episode.footage):
        write_json(episode.analysis_path, analysis)
    return analysis


def create_balanced_plan(episode: Episode) -> dict[str, Any]:
    if not episode.analysis_path.is_file():
        raise FileNotFoundError("Missing clip analysis. Run `ve analyze` first.")
    analysis = _prepare_analysis(episode)
    planning_scope = episode.config.get("planning_scope", "primary_day")
    target_duration, target_mode = resolve_target_duration(
        episode.config.get("target_duration_sec"),
        analysis,
        planning_scope=planning_scope,
    )
    fallback = build_balanced_fallback_plan(
        analysis,
        target_duration,
        title=str(episode.config["title"]),
        setting_order=list(episode.config.get("setting_order", [])),
        story_arc=str(episode.config.get("story_arc", "scene_energy")),
        planning_scope=planning_scope,
    )
    fixed, errors = validate_and_fix_plan(
        fallback,
        analysis,
        target_duration=target_duration,
    )
    if errors:
        raise ValueError("Balanced planner produced an invalid plan:\n- " + "\n- ".join(errors))
    return _save_plan(
        episode,
        fixed,
        target_duration=target_duration,
        target_mode=target_mode,
        analysis=analysis,
    )


def create_plan(episode: Episode) -> dict[str, Any]:
    if not episode.analysis_path.is_file():
        raise FileNotFoundError("Missing clip analysis. Run `ve analyze` first.")
    analysis = _prepare_analysis(episode)
    planning_scope = episode.config.get("planning_scope", "primary_day")
    scope = normalize_planning_scope(planning_scope)
    pool, _ = select_planning_clips(unique_clips(analysis), planning_scope=scope)
    pool_names = {
        str(clip.get("metadata", {}).get("filename", ""))
        for clip in pool
        if clip.get("metadata", {}).get("filename")
    }
    compact = [
        item for item in _compact_analysis(analysis) if str(item.get("file", "")) in pool_names
    ]
    target_duration, target_mode = resolve_target_duration(
        episode.config.get("target_duration_sec"),
        analysis,
        planning_scope=planning_scope,
    )
    client = OllamaClient(dict(episode.config["vision"]))
    try:
        content, _ = client.chat(
            _planning_prompt(episode, compact, target_duration),
            timeout=900,
            max_tokens=4096,
            context_tokens=32768,
        )
        plan = constrain_selection_lengths(
            parse_json_response(content),
            target_duration,
        )
        plan["planning_scope"] = scope
        fixed, errors = validate_and_fix_plan(
            plan,
            analysis,
            target_duration=target_duration,
        )
        if errors:
            day_repair = (
                "Use every capture day. Keep one composite day (morning → night); "
                "later daylight may sit before earlier-day evening."
                if scope == "all_days"
                else (
                    "Keep selections in capture_time order and stay on one primary "
                    "capture day when multiple days are present."
                )
            )
            repair_prompt = f"""
Repair this edit plan. Return only the complete corrected JSON plan.
The required target duration is {target_duration:.1f} seconds. The sum of every
selected clip's (end - start) must be between {target_duration * 0.65:.1f} and
{target_duration * 1.35:.1f} seconds. Shorten pure visual B-roll if needed, but
preserve complete useful speech and fooling-around / play exchanges — do not
delete funny kids narration to hit the budget.
{day_repair} Prefer original files over duplicate Copy files.
No diversity requirement — one location or activity may dominate if that's where the
engaging footage is. Judge selections purely on interest, not on setting coverage.
Validation errors:
{json.dumps(errors, ensure_ascii=False)}

Valid clip analysis:
{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}

Broken plan:
{json.dumps(fixed, ensure_ascii=False, separators=(',', ':'))}
""".strip()
            repaired_content, _ = client.chat(
                repair_prompt,
                timeout=900,
                max_tokens=4096,
                context_tokens=32768,
            )
            repaired = constrain_selection_lengths(
                parse_json_response(repaired_content),
                target_duration,
            )
            repaired["planning_scope"] = scope
            fixed, errors = validate_and_fix_plan(
                repaired,
                analysis,
                target_duration=target_duration,
            )
        if errors:
            fallback = build_balanced_fallback_plan(
                analysis,
                target_duration,
                title=str(episode.config["title"]),
                setting_order=list(episode.config.get("setting_order", [])),
                story_arc=str(episode.config.get("story_arc", "scene_energy")),
                planning_scope=planning_scope,
            )
            fixed, errors = validate_and_fix_plan(
                fallback,
                analysis,
                target_duration=target_duration,
            )
        if errors:
            raise ValueError("Local planner produced an invalid plan:\n- " + "\n- ".join(errors))
        return _save_plan(
            episode,
            fixed,
            target_duration=target_duration,
            target_mode=target_mode,
            analysis=analysis,
        )
    finally:
        client.unload()
