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
from vlog_editor.media import probe_video
from vlog_editor.project import Episode, read_json, write_json
from vlog_editor.validation import infer_setting, validate_and_fix_plan, validate_audio_plan
from vlog_editor.vision import OllamaClient, parse_json_response

MIN_SELECTION_SEC = 10.0
DEFAULT_MAX_SELECTION_SEC = 36.0
PLAY_MAX_SELECTION_SEC = 55.0
MAX_PLAY_BEATS_PER_CLIP = 3
OPEN_CTA_BEAT_SEC = 22.0
MIN_OPEN_CTA_SEC = 12.0
# Hold part of the middle budget for the last portion of the capture day so
# evening settings (e.g. night mall) are not starved by long daytime play beats.
LATE_DAY_FRACTION = 0.30
LATE_DAY_RESERVE_RATIO = 0.22

CTA_RE = re.compile(
    r"\b("
    r"thanks|thank you|makasih|terima kasih|bye|dadah|daah|"
    r"wave|waving|senyum|smile|smiling|see you|sampai jumpa|"
    r"bye bye|dadah ya|kutip|subscribe|like"
    r")\b",
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
    """Multi-beats only for real kids-activity footage, never adult meal pads."""
    if is_adult_meal_focus(source):
        return False
    if float(source.get("metadata", {}).get("duration", 0) or 0) < 90:
        return False
    if not is_playful_source(source):
        return False
    # Need at least one on-camera kids-audience activity hook.
    return source_kids_audience_interest(source) >= 0.34


def is_cta_source(source: dict[str, Any]) -> bool:
    text = _source_text(source)
    if CTA_RE.search(text):
        return True
    story = float(source.get("visual", {}).get("story_value", 0) or 0)
    return bool(CUTE_RE.search(text) and story >= 0.65)


def _max_selection_for_source(source: dict[str, Any]) -> float:
    return PLAY_MAX_SELECTION_SEC if is_playful_source(source) else DEFAULT_MAX_SELECTION_SEC


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

    ranges = source.get("visual", {}).get("recommended_ranges", [])
    clip_has_children = source_has_children(source)
    # Prefer kid / kids-audience recommended ranges before generic focused ranges.
    ordered_ranges = sorted(
        (item for item in ranges if isinstance(item, dict)),
        key=lambda item: (
            0 if text_has_children(str(item.get("reason", ""))) else 1,
            0 if kids_audience_interest(str(item.get("reason", ""))) > 0 else 1,
            -kids_audience_interest(str(item.get("reason", ""))),
            float(item.get("start", 0) or 0),
        ),
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
                [
                    segment
                    for segment in source.get("audio", {}).get("segments", []) or []
                    if isinstance(segment, dict)
                ],
                start=start,
                max_duration=max_duration,
                source_duration=source_duration,
            )
            if not overlaps_avoided(start, end):
                return round(start, 3), round(min(end, start + max_duration), 3)

    segments = [
        segment
        for segment in source.get("audio", {}).get("segments", [])
        if isinstance(segment, dict)
    ]
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
) -> dict[str, Any] | None:
    metadata = source.get("metadata", {})
    source_duration = max(0.0, float(metadata.get("duration", 0)))
    min_needed = MIN_SELECTION_SEC if min_duration is None else float(min_duration)
    # Skip leftover budget crumbs that produce weird mid-sentence stubs.
    if max_duration < min_needed and source_duration >= min_needed:
        return None
    hard_cap = _max_selection_for_source(source)
    requested = min(hard_cap, source_duration, max(0.0, max_duration))
    if requested < 0.35:
        return None
    start, end = select_excerpt(source, requested, avoid=avoid)
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
    return {
        "file": str(metadata.get("filename", "")),
        "start": round(start, 3),
        "end": round(end, 3),
        "note": note,
        "subtitle": subtitle,
        "_capture_time": _capture_time(source),
        "_setting": infer_setting(source),
        "_score": _clip_score(source),
        "_playful": is_playful_source(source),
        "_kids_hooks": hooks,
    }


def _pick_playful_open_source(pool: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pool:
        return None
    early_count = max(1, int(len(pool) * 0.25 + 0.999))
    early = pool[:early_count]
    playful = [clip for clip in early if is_playful_source(clip)]
    return playful[0] if playful else None


def _cta_close_candidates(
    pool: list[dict[str, Any]],
    *,
    not_before_capture_time: str,
) -> list[dict[str, Any]]:
    """Late usable close candidates, best-first (explicit CTA, then cute, then latest)."""
    if not pool:
        return []
    usable = [
        clip
        for clip in pool
        if float(clip.get("metadata", {}).get("duration", 0) or 0) >= MIN_OPEN_CTA_SEC
        and (
            not not_before_capture_time
            or (_capture_time(clip) or "9999") >= not_before_capture_time
        )
    ]
    if not usable:
        return []
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
) -> dict[str, Any]:
    unique = unique_clips(analysis)
    pool, primary_day = select_primary_day_clips(unique)
    if not pool:
        raise ValueError("Cannot build a fallback plan without analyzed clips")

    pool.sort(
        key=lambda clip: (
            _capture_time(clip) or "9999",
            str(clip.get("metadata", {}).get("filename", "")),
        )
    )

    settings_present = sorted(
        {infer_setting(clip) for clip in pool if infer_setting(clip) != "other"}
    )
    if not settings_present:
        settings_present = ["other"]

    ranks = {
        setting.strip().lower(): index
        for index, setting in enumerate(setting_order or [])
    }
    # Prefer settings that appear earlier in capture order, then optional ranks.
    first_seen: dict[str, int] = {}
    for index, clip in enumerate(pool):
        setting = infer_setting(clip)
        first_seen.setdefault(setting, index)
    settings_present.sort(
        key=lambda setting: (
            ranks.get(setting, len(ranks)),
            first_seen.get(setting, 10_000),
        )
    )
    required_settings = select_required_settings(
        settings_present,
        first_seen=first_seen,
        pool=pool,
        setting_order=setting_order,
        max_settings=4,
    )

    desired_total = target_duration * 0.95
    # Validator caps a setting at 40% of *plan* duration — leave headroom vs undershoot.
    setting_budget = desired_total * 0.34
    selected: list[dict[str, Any]] = []
    selected_files: set[str] = set()
    selected_ranges: dict[str, list[tuple[float, float]]] = defaultdict(list)
    setting_duration: dict[str, float] = defaultdict(float)
    total = 0.0
    # Leave room for validator speech/segment expansion so multi-beats do not overlap.
    AVOID_PAD_SEC = 6.5
    open_selection: dict[str, Any] | None = None
    cta_selection: dict[str, Any] | None = None

    def _commit(selection: dict[str, Any]) -> None:
        nonlocal total
        filename = str(selection["file"])
        setting = str(selection.get("_setting", "other"))
        selected.append(selection)
        selected_files.add(filename)
        span = (float(selection["start"]), float(selection["end"]))
        selected_ranges[filename].append(
            (max(0.0, span[0] - AVOID_PAD_SEC), span[1] + AVOID_PAD_SEC)
        )
        duration = span[1] - span[0]
        setting_duration[setting] += duration
        total += duration

    # Reserve playful cold-open first; CTA is chosen after the middle so it cannot
    # starve the body by locking an early upper capture bound.
    open_source = _pick_playful_open_source(pool)
    if open_source is not None:
        candidate = _selection_from_source(
            open_source,
            max_duration=OPEN_CTA_BEAT_SEC,
            min_duration=MIN_OPEN_CTA_SEC,
        )
        if candidate is not None:
            candidate["_role"] = "open"
            note = str(candidate.get("note", "")).strip()
            candidate["note"] = f"{note} Playful cold-open.".strip()
            open_selection = candidate
            _commit(candidate)

    open_time = str(open_selection.get("_capture_time") or "") if open_selection else ""
    # Hold budget for the eventual CTA close.
    cta_reserve = OPEN_CTA_BEAT_SEC
    middle_cap = max(0.0, desired_total - cta_reserve)
    late_index = max(0, int(len(pool) * (1.0 - LATE_DAY_FRACTION)))
    late_cutoff_time = ""
    if pool and late_index < len(pool):
        late_cutoff_time = _capture_time(pool[late_index]) or ""
    late_reserve = middle_cap * LATE_DAY_RESERVE_RATIO if late_cutoff_time else 0.0
    early_middle_cap = max(0.0, middle_cap - late_reserve)

    def _in_middle_window(source: dict[str, Any]) -> bool:
        capture = _capture_time(source) or "9999"
        if open_time and capture < open_time:
            return False
        return True

    def _source_middle_cap(source: dict[str, Any]) -> float:
        capture = _capture_time(source) or ""
        if late_cutoff_time and capture >= late_cutoff_time:
            return middle_cap
        return early_middle_cap

    def _try_add(
        source: dict[str, Any],
        *,
        allow_extra_beats: bool,
        respect_late_reserve: bool = True,
    ) -> None:
        nonlocal total
        if not _in_middle_window(source):
            return
        filename = str(source.get("metadata", {}).get("filename", ""))
        setting = infer_setting(source)
        if setting_duration[setting] >= setting_budget - 1e-6:
            return
        source_cap = (
            _source_middle_cap(source) if respect_late_reserve else middle_cap
        )
        remaining_setting = setting_budget - setting_duration[setting]
        remaining_total = source_cap - total
        budget = min(remaining_setting, remaining_total)
        if budget < MIN_SELECTION_SEC and float(source.get("metadata", {}).get("duration", 0)) >= MIN_SELECTION_SEC:
            return
        source_duration = float(source.get("metadata", {}).get("duration", 0))
        beats = 1
        if (
            allow_extra_beats
            and allows_extra_play_beats(source)
        ):
            beats = min(
                MAX_PLAY_BEATS_PER_CLIP,
                max(1, int(min(budget, source_duration) // 40)),
            )
        for _ in range(beats):
            source_cap = (
                _source_middle_cap(source) if respect_late_reserve else middle_cap
            )
            if total >= source_cap or setting_duration[setting] >= setting_budget - 1e-6:
                break
            remaining_setting = setting_budget - setting_duration[setting]
            remaining_total = source_cap - total
            budget = min(remaining_setting, remaining_total)
            selection = _selection_from_source(
                source,
                max_duration=budget,
                avoid=selected_ranges.get(filename, []),
            )
            if selection is None:
                break
            selection["_role"] = "middle"
            _commit(selection)

    # Reserve coverage for each required setting.
    # Use the full middle cap here so diversity is not blocked by the late-day hold.
    # Late-day settings (night mall, etc.) get several distinct files up front;
    # daytime settings stay at one reserved beat so play-heavy mornings cannot
    # monopolize the reserve pass.
    late_settings = {
        infer_setting(clip)
        for clip in pool[late_index:]
        if infer_setting(clip) != "other"
    }
    for setting in required_settings:
        candidates = [
            clip
            for clip in pool
            if infer_setting(clip) == setting
            and str(clip.get("metadata", {}).get("filename", "")) not in selected_files
            and _in_middle_window(clip)
        ]
        if not candidates:
            continue
        candidates.sort(key=_clip_score, reverse=True)
        reserve_limit = 3 if setting in late_settings else 1
        reserved = 0
        for source in candidates:
            if reserved >= reserve_limit:
                break
            filename = str(source.get("metadata", {}).get("filename", ""))
            before = filename in selected_files
            _try_add(
                source,
                allow_extra_beats=False,
                respect_late_reserve=False,
            )
            if not before and filename in selected_files:
                reserved += 1

    # Fill remaining budget in capture order so story time never jumps backward.
    for source in pool:
        if total >= middle_cap:
            break
        filename = str(source.get("metadata", {}).get("filename", ""))
        # Long playful sources may already have one beat; still allow more beats.
        if filename in selected_files and not (
            allows_extra_play_beats(source)
            and len(selected_ranges.get(filename, [])) < MAX_PLAY_BEATS_PER_CLIP
        ):
            continue
        if is_adult_meal_focus(source) and _clip_score(source) < 0.45:
            continue
        if source_has_children(source):
            if _clip_score(source) < 0.28:
                continue
        elif _clip_score(source) < 0.35:
            continue
        _try_add(source, allow_extra_beats=True)

    # CTA close after the body — must be last in capture order.
    last_body_time = ""
    for item in selected:
        capture = str(item.get("_capture_time") or "")
        if capture >= last_body_time:
            last_body_time = capture
    for cta_source in _cta_close_candidates(
        pool,
        not_before_capture_time=last_body_time or open_time,
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
        cta_time = str(candidate.get("_capture_time") or "")
        if last_body_time and cta_time < last_body_time:
            continue
        candidate["_role"] = "cta"
        note = str(candidate.get("note", "")).strip()
        candidate["note"] = f"{note} CTA close b-roll.".strip()
        cta_selection = candidate
        _commit(candidate)
        break

    # If the day already ended in the body, promote the chronologically last middle beat.
    if cta_selection is None:
        middle_sorted = [
            item for item in selected if item.get("_role") == "middle"
        ]
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

    selected.sort(
        key=lambda clip: (
            str(clip.get("_capture_time") or "9999"),
            float(clip.get("start", 0)),
            str(clip.get("file", "")),
        )
    )

    # Keep setting share under the validator's 40% of *plan* duration (not target).
    # Prefer dropping middle beats; never drop reserved open/CTA bookends.
    def _plan_total() -> float:
        return sum(float(item["end"]) - float(item["start"]) for item in selected)

    def _setting_share(setting: str) -> float:
        total_dur = _plan_total()
        if total_dur <= 0:
            return 0.0
        used = sum(
            float(item["end"]) - float(item["start"])
            for item in selected
            if str(item.get("_setting")) == setting
        )
        return used / total_dur

    changed = True
    while changed and selected:
        changed = False
        totals = _plan_total()
        if totals <= 0:
            break
        overweight = [
            setting
            for setting, _ in setting_duration.items()
            if _setting_share(setting) > 0.40 + 1e-6
        ]
        if not overweight:
            break
        # Drop the shortest middle clip from the most overweight setting.
        setting = max(overweight, key=_setting_share)
        middle = [
            item
            for item in selected
            if item.get("_role") == "middle" and str(item.get("_setting")) == setting
        ]
        if not middle:
            break
        victim = min(middle, key=lambda item: float(item["end"]) - float(item["start"]))
        selected.remove(victim)
        duration = float(victim["end"]) - float(victim["start"])
        setting_duration[setting] = max(0.0, setting_duration[setting] - duration)
        total = max(0.0, total - duration)
        changed = True

    # If CTA ended up not last after chrono sort (same-time edge), keep role tags for sections.
    structure: list[dict[str, Any]] = []
    open_clips: list[dict[str, Any]] = []
    cta_clips: list[dict[str, Any]] = []
    middle_items: list[dict[str, Any]] = []
    for item in selected:
        role = str(item.pop("_role", "middle") or "middle")
        item.pop("_capture_time", None)
        item.pop("_score", None)
        item.pop("_playful", None)
        hooks = [str(h) for h in (item.pop("_kids_hooks", []) or []) if str(h).strip()]
        setting = str(item.pop("_setting", "other"))
        item["_section_setting"] = setting
        item["_section_hooks"] = hooks
        if role == "open":
            open_clips.append(item)
        elif role == "cta":
            cta_clips.append(item)
        else:
            middle_items.append(item)

    def _storyboard_section(setting: str, clips: list[dict[str, Any]]) -> dict[str, Any]:
        hook_counts: dict[str, int] = defaultdict(int)
        for clip in clips:
            for hook in clip.pop("_section_hooks", []) or []:
                hook_counts[str(hook)] += 1
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

    if open_clips:
        for item in open_clips:
            item.pop("_section_setting", None)
            item.pop("_section_hooks", None)
        structure.append(
            {
                "section": "Playful open",
                "description": (
                    "Kids-audience cold-open: early fun / fooling-around with children when possible."
                ),
                "clips": open_clips,
            }
        )

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

    day_note = (
        f" Primary capture day: {primary_day}."
        if primary_day not in {"", "unknown"}
        else ""
    )
    return {
        "title": title,
        "structure": structure,
        "bgm_suggestion": "",
        "editing_notes": (
            "Kids-audience chronological storyboard. Priority 1: children on camera/speaking. "
            "Priority 2: kid-interesting activity categories (play structures, animals/creatures, "
            "water play, treats, discovery/wonder, rides) — never place hardcodes. "
            "Keep setting diversity within one capture day (including late-day evening beats), "
            "preserve complete play narration, and bookend with playful open plus a fuller CTA/"
            "goodbye close when footage allows."
            f"{day_note}"
        ),
        "planning_day": primary_day,
    }


def resolve_target_duration(
    configured: Any,
    analysis: dict[str, Any],
) -> tuple[float, str]:
    is_auto = configured is None or (
        isinstance(configured, str) and configured.strip().lower() == "auto"
    )
    if not is_auto:
        try:
            target = float(configured)
        except (TypeError, ValueError) as exc:
            raise ValueError("target_duration_sec must be a positive number or 'auto'") from exc
        if target <= 0:
            raise ValueError("target_duration_sec must be a positive number or 'auto'")
        return target, "explicit"

    unique = unique_clips(analysis)
    pool, _ = select_primary_day_clips(unique)
    if not pool:
        raise ValueError("Cannot determine an automatic duration without analyzed clips")

    total_duration = sum(
        max(0.0, float(clip.get("metadata", {}).get("duration", 0))) for clip in pool
    )
    scores = [_clip_score(clip) for clip in pool]
    average_score = sum(scores) / len(scores) if scores else 0.5
    playful_duration = sum(
        max(0.0, float(clip.get("metadata", {}).get("duration", 0)))
        for clip in pool
        if is_playful_source(clip)
    )
    play_ratio = playful_duration / total_duration if total_duration else 0.0

    # Ordinary travel B-roll ~16%; play-heavy kids days keep more fooling-around
    # narration so scenes feel complete (up to ~28% of primary-day footage).
    retain = 0.16 + 0.12 * min(1.0, play_ratio * 1.6)
    target = total_duration * retain * (0.75 + 0.25 * average_score)
    target = min(target, total_duration * 0.8, 720.0)
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
    return f"""
Act as a decisive travel-vlog editor. Build a coherent edit from the clip analysis below.
Title/theme: {episode.config['title']}
Style: {episode.config['style']}
Target duration: {target:.0f} seconds

Rules:
- Keep the final edit in capture_time order. Never jump backward in capture time.
- Prefer one primary capture day when multiple days are present.
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
  (often 20-55s). Do not strip playful speech just to hit pacing. Long play takes may
  contribute 2-3 distinct non-overlapping beats instead of one tiny slice.
- Open with a short playful cold-open (about 8-14s) from early real footage when available.
- Close with a CTA-style goodbye beat (smile / thanks / bye / wave / night pulang,
  about 12-24s) from late real footage. Do not invent clips. Keep capture_time order
  (open earliest, CTA latest).
- Never start or end a spoken selection mid-sentence when a nearby ASR segment boundary fits.
- Cover every major distinct location or activity that has usable footage, with at least
  four settings when available on the chosen day.
- No single setting or activity may consume more than 40% of the target duration unless
  the source material genuinely contains no other usable setting.
- Prefer 8-20 focused selections. Skip crumb cuts under ~10s when the source has a fuller beat.
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
    if analysis is not None:
        plan = _attach_audio(episode, plan, analysis)
    plan["target_duration_sec"] = target_duration
    plan["target_duration_mode"] = target_mode
    write_json(episode.plan_path, plan)
    if episode.approval_path.exists():
        episode.approval_path.unlink()
    cue_count = len(plan.get("audio_cues") or [])
    print(f"Wrote {episode.plan_path} ({plan['duration_sec']:.1f}s, {cue_count} SFX cues)")
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
    target_duration, target_mode = resolve_target_duration(
        episode.config.get("target_duration_sec"),
        analysis,
    )
    fallback = build_balanced_fallback_plan(
        analysis,
        target_duration,
        title=str(episode.config["title"]),
        setting_order=list(episode.config.get("setting_order", [])),
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
    compact = _compact_analysis(analysis)
    target_duration, target_mode = resolve_target_duration(
        episode.config.get("target_duration_sec"),
        analysis,
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
        fixed, errors = validate_and_fix_plan(
            plan,
            analysis,
            target_duration=target_duration,
        )
        if errors:
            repair_prompt = f"""
Repair this edit plan. Return only the complete corrected JSON plan.
The required target duration is {target_duration:.1f} seconds. The sum of every
selected clip's (end - start) must be between {target_duration * 0.65:.1f} and
{target_duration * 1.35:.1f} seconds. Shorten pure visual B-roll if needed, but
preserve complete useful speech and fooling-around / play exchanges — do not
delete funny kids narration to hit the budget.
Keep selections in capture_time order and stay on one primary capture day when
multiple days are present. Prefer original files over duplicate Copy files.
Cover the major distinct locations and activities from that day; use at least
four settings when available, and do not let one setting consume more than 40%
of the target duration.
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
