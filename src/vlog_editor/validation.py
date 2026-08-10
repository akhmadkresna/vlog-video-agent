from __future__ import annotations

import copy
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

# Order matters: place/activity cues before generic transport words so
# "Hot Wheels cars in a toy store aisle" stays store, not vehicle.
# More specific places before generic home/outdoor/vehicle so
# "leaves a mall … driving home" stays mall, not home.
SETTING_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("store", ("store", "shop", "aisle", "market", "counter", "toy store")),
    ("mall", ("mall", "shopping center", "shopping area")),
    ("restaurant", ("restaurant", "cafe", "food court", "stall", "warung")),
    ("transit", ("airport", "station", "terminal", "platform")),
    ("nature", ("beach", "mountain", "forest", "lake", "river", "waterfall", "park")),
    ("attraction", ("museum", "temple", "monument", "attraction", "landmark")),
    ("street", ("street", "road", "sidewalk", "parking")),
    (
        "home",
        (
            "home",
            "house",
            "room",
            "office",
            "kitchen",
            "bedroom",
            "living room",
            "play with toys",
            "on the floor",
            "playing on the floor",
        ),
    ),
    ("outdoor", ("outdoor", "outside", "garden", "courtyard")),
    # Word-boundary match only — "car" must not hit "cars" / "cart" / "cartoon".
    ("vehicle", ("car", "bus", "train", "plane", "vehicle", "backseat", "dashboard")),
)
VALID_SETTINGS = {
    "vehicle",
    "home",
    "street",
    "outdoor",
    "mall",
    "store",
    "restaurant",
    "hotel",
    "transit",
    "nature",
    "attraction",
    "other",
}


def _keyword_in_text(normalized: str, keyword: str) -> bool:
    """Match whole words/phrases so 'car' does not match 'cars' or 'cart'."""
    pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
    return re.search(pattern, normalized) is not None


_DECLARED_SETTING_PRIORITY: tuple[str, ...] = (
    "mall",
    "store",
    "restaurant",
    "attraction",
    "hotel",
    "transit",
    "nature",
    "street",
    "vehicle",
    "home",
    "outdoor",
    "other",
)


def _declared_settings(raw: str) -> list[str]:
    parts = [part.strip().lower() for part in re.split(r"[|,/]+", raw) if part.strip()]
    return [part for part in parts if part in VALID_SETTINGS]


def infer_setting(clip: dict[str, Any]) -> str:
    visual = clip.get("visual", {})
    text = " ".join(
        [
            str(visual.get("summary", "")),
            " ".join(str(item) for item in visual.get("subjects", [])),
        ]
    ).lower()
    normalized = re.sub(r"[^a-z0-9 ]+", " ", text)
    declared_options = _declared_settings(str(visual.get("setting", "")))
    if declared_options:
        priority = {name: index for index, name in enumerate(_DECLARED_SETTING_PRIORITY)}
        declared = min(declared_options, key=lambda name: priority.get(name, len(priority)))
        # Soft correction: toy-store / aisle play is not a vehicle scene even if
        # the vision model said "vehicle" because of "cars" / "cart".
        storeish = any(
            _keyword_in_text(normalized, keyword)
            for keyword in ("store", "aisle", "shop", "toy store", "market")
        )
        if declared == "vehicle" and storeish:
            return "store"
        return declared

    for setting, keywords in SETTING_KEYWORDS:
        if any(_keyword_in_text(normalized, keyword) for keyword in keywords):
            return setting
    return "other"


def _speech_bounds(
    start: float,
    end: float,
    words: list[dict[str, Any]],
    *,
    pad_start: float = 0.08,
    pad_end: float = 0.12,
    segments: list[dict[str, Any]] | None = None,
    max_expand: float = 4.0,
) -> tuple[float, float]:
    original_start = start
    original_end = end
    overlapping = [
        word
        for word in words
        if float(word.get("end", 0)) > original_start and float(word.get("start", 0)) < original_end
    ]
    if overlapping:
        first, last = overlapping[0], overlapping[-1]
        if float(first["start"]) < original_start < float(first["end"]):
            start = max(0.0, float(first["start"]) - pad_start)
        if float(last["start"]) < original_end < float(last["end"]):
            end = float(last["end"]) + pad_end

    # Prefer complete ASR segments so fooling-around narration is not bisected.
    # Only snap using the original cut points — never chain-expand through neighbors.
    if segments:
        for segment in segments:
            try:
                seg_start = float(segment.get("start", 0))
                seg_end = float(segment.get("end", 0))
            except (TypeError, ValueError):
                continue
            if seg_start < original_start < seg_end and (original_start - seg_start) <= max_expand:
                start = max(0.0, min(start, seg_start - pad_start))
            if seg_start < original_end < seg_end and (seg_end - original_end) <= max_expand:
                end = max(end, seg_end + pad_end)
    return start, end


def _capture_day(clip: dict[str, Any]) -> str:
    capture_time = str(clip.get("metadata", {}).get("capture_time") or "")
    return capture_time[:10] if len(capture_time) >= 10 else ""


def validate_and_fix_plan(
    plan: dict[str, Any],
    analysis: dict[str, Any],
    *,
    target_duration: float,
    tolerance: float = 0.35,
) -> tuple[dict[str, Any], list[str]]:
    fixed = copy.deepcopy(plan)
    errors: list[str] = []
    clips_by_name = {
        str(item["metadata"]["filename"]): item for item in analysis.get("clips", [])
    }
    structure = fixed.get("structure")
    if not isinstance(structure, list) or not structure:
        return fixed, ["Plan must have a non-empty structure array"]

    total = 0.0
    selected_by_file: dict[str, list[tuple[float, float]]] = {}
    ordered_sources: list[dict[str, Any]] = []
    previous_capture_time: str | None = None
    for section_index, section in enumerate(structure):
        if not isinstance(section, dict):
            errors.append(f"Section {section_index + 1} is not an object")
            continue
        section.setdefault("section", f"Section {section_index + 1}")
        section.setdefault("description", "")
        clips = section.get("clips")
        if not isinstance(clips, list) or not clips:
            errors.append(f"Section {section_index + 1} has no clips")
            continue
        for clip_index, clip in enumerate(clips):
            context = f"section {section_index + 1}, clip {clip_index + 1}"
            if not isinstance(clip, dict):
                errors.append(f"{context}: must be an object")
                continue
            filename = str(clip.get("file", ""))
            source = clips_by_name.get(filename)
            if source is None:
                errors.append(f"{context}: unknown file {filename!r}")
                continue
            duration = float(source["metadata"]["duration"])
            try:
                start = max(0.0, float(clip.get("start", 0)))
                end = min(duration, float(clip.get("end", duration)))
            except (TypeError, ValueError):
                errors.append(f"{context}: start/end must be numeric")
                continue
            words = source.get("audio", {}).get("words", [])
            segments = source.get("audio", {}).get("segments", []) or []
            start, end = _speech_bounds(
                start,
                end,
                words,
                segments=[item for item in segments if isinstance(item, dict)],
            )
            start = max(0.0, min(start, duration))
            end = max(0.0, min(end, duration))
            if end - start < 0.35:
                errors.append(f"{context}: selected range is shorter than 0.35 seconds")
                continue
            for previous_start, previous_end in selected_by_file.get(filename, []):
                overlap = min(end, previous_end) - max(start, previous_start)
                if overlap > 0.05:
                    errors.append(
                        f"{context}: range overlaps another use of {filename} by {overlap:.2f}s"
                    )
            capture_time = str(source.get("metadata", {}).get("capture_time") or "")
            story_arc = str(fixed.get("story_arc") or "chronological").strip().lower()
            enforce_chrono = story_arc not in {"kids_energy", "energy", "kids"}
            if (
                enforce_chrono
                and capture_time
                and previous_capture_time
                and capture_time < previous_capture_time
            ):
                errors.append(
                    f"{context}: capture time {capture_time} is earlier than previous "
                    f"selection {previous_capture_time}; keep the plan chronological"
                )
            if capture_time:
                previous_capture_time = capture_time
            selected_by_file.setdefault(filename, []).append((start, end))
            ordered_sources.append(source)
            clip["start"] = round(start, 3)
            clip["end"] = round(end, 3)
            clip.setdefault("note", "")
            clip.setdefault("subtitle", "")
            total += end - start

    fixed["duration_sec"] = round(total, 3)
    fixed.setdefault("title", "Untitled Vlog")
    fixed.setdefault("bgm_suggestion", "")
    fixed.setdefault("editing_notes", "")

    # Diversity is judged against the primary capture day represented in the plan,
    # so multi-day folders do not force impossible cross-day montage.
    day_counts: dict[str, int] = defaultdict(int)
    for source in ordered_sources:
        day = _capture_day(source)
        if day:
            day_counts[day] += 1
    primary_day = max(day_counts, key=day_counts.get) if day_counts else ""
    if primary_day:
        fixed["planning_day"] = primary_day
        diversity_pool = [
            clip
            for clip in clips_by_name.values()
            if _capture_day(clip) in {"", primary_day}
        ]
    else:
        diversity_pool = list(clips_by_name.values())

    available_settings = {
        infer_setting(clip) for clip in diversity_pool if infer_setting(clip) != "other"
    }
    selected_duration_by_setting: dict[str, float] = defaultdict(float)
    body_duration_by_setting: dict[str, float] = defaultdict(float)
    bookend_sections = {"greeting open", "playful open", "cta close"}
    for section in structure:
        if not isinstance(section, dict):
            continue
        section_name = str(section.get("section", "")).strip().lower()
        is_bookend = section_name in bookend_sections
        for clip in section.get("clips", []):
            if not isinstance(clip, dict):
                continue
            source = clips_by_name.get(str(clip.get("file", "")))
            if source is None:
                continue
            try:
                selected_duration = float(clip["end"]) - float(clip["start"])
            except (KeyError, TypeError, ValueError):
                continue
            selected_duration = max(0.0, selected_duration)
            setting = infer_setting(source)
            selected_duration_by_setting[setting] += selected_duration
            if not is_bookend:
                body_duration_by_setting[setting] += selected_duration

    required_settings = min(4, len(available_settings))
    selected_available_settings = available_settings.intersection(selected_duration_by_setting)
    if len(selected_available_settings) < required_settings:
        errors.append(
            "Plan covers "
            f"{len(selected_available_settings)} of {len(available_settings)} available settings; "
            f"at least {required_settings} are required"
        )
    # Open/CTA count toward the denominator but not the numerator, so reserved
    # bookends do not force kids-peak settings over the 40% cap by themselves.
    if len(available_settings) >= 3 and total > 0 and body_duration_by_setting:
        dominant_setting, dominant_duration = max(
            body_duration_by_setting.items(),
            key=lambda item: item[1],
            default=("other", 0.0),
        )
        if dominant_duration > total * 0.4:
            errors.append(
                f"Setting {dominant_setting!r} consumes "
                f"{dominant_duration / total:.0%} of the plan; maximum is 40%"
            )

    if target_duration > 0 and abs(total - target_duration) > target_duration * tolerance:
        errors.append(
            f"Planned duration {total:.1f}s is too far from target {target_duration:.1f}s"
        )
    return fixed, errors


def validate_render_sources(plan: dict[str, Any], footage: Path) -> list[str]:
    errors: list[str] = []
    for section in plan.get("structure", []):
        for clip in section.get("clips", []):
            path = footage / str(clip.get("file", ""))
            if not path.is_file():
                errors.append(f"Missing source file: {path}")
            if float(clip.get("end", 0)) <= float(clip.get("start", 0)):
                errors.append(f"Invalid range in {clip.get('file')}")
    return errors


def validate_audio_plan(
    plan: dict[str, Any],
    episode_root: Path,
    *,
    min_gap: float = 6.0,
) -> list[str]:
    from vlog_editor.audio_pack import ALLOWED_LICENSES, CUE_TYPES

    errors: list[str] = []
    duration = float(plan.get("duration_sec", 0) or 0)
    cues = plan.get("audio_cues", [])
    if cues is None:
        cues = []
    if not isinstance(cues, list):
        return ["audio_cues must be a list"]

    previous_at: float | None = None
    for index, cue in enumerate(cues, start=1):
        context = f"audio cue {index}"
        if not isinstance(cue, dict):
            errors.append(f"{context}: must be an object")
            continue
        cue_type = str(cue.get("type", ""))
        if cue_type not in CUE_TYPES:
            errors.append(f"{context}: unknown type {cue_type!r}")
        try:
            at_sec = float(cue.get("at_sec", -1))
        except (TypeError, ValueError):
            errors.append(f"{context}: at_sec must be numeric")
            continue
        if at_sec < 0 or (duration > 0 and at_sec > duration):
            errors.append(f"{context}: at_sec {at_sec:.3f} outside edit duration {duration:.3f}s")
        if previous_at is not None and at_sec - previous_at < min_gap - 1e-6:
            errors.append(
                f"{context}: spacing {at_sec - previous_at:.2f}s is below minimum gap {min_gap:.1f}s"
            )
        previous_at = at_sec
        license_name = str(cue.get("license", "")).strip().lower()
        if license_name not in ALLOWED_LICENSES:
            errors.append(f"{context}: license must be one of {sorted(ALLOWED_LICENSES)}")
        relative = str(cue.get("file", ""))
        path = episode_root / relative if relative else None
        if not relative or path is None or not path.is_file():
            errors.append(f"{context}: missing file {relative!r}")

    bgm = plan.get("bgm")
    if bgm is not None:
        if not isinstance(bgm, dict):
            errors.append("bgm must be an object")
        else:
            segments = bgm.get("segments")
            has_segments = isinstance(segments, list) and bool(segments)
            relative = str(bgm.get("file", ""))
            path = episode_root / relative if relative else None
            if not has_segments and (not relative or path is None or not path.is_file()):
                errors.append(f"bgm file missing: {relative!r}")
            license_name = str(bgm.get("license", "")).strip().lower()
            if license_name and license_name not in ALLOWED_LICENSES:
                errors.append(f"bgm license must be one of {sorted(ALLOWED_LICENSES)}")
            if segments is not None:
                if not isinstance(segments, list):
                    errors.append("bgm.segments must be a list")
                else:
                    for index, segment in enumerate(segments, start=1):
                        context = f"bgm segment {index}"
                        if not isinstance(segment, dict):
                            errors.append(f"{context}: must be an object")
                            continue
                        try:
                            start = float(segment.get("start_sec", -1))
                            end = float(segment.get("end_sec", -1))
                        except (TypeError, ValueError):
                            errors.append(f"{context}: start_sec/end_sec must be numeric")
                            continue
                        if end <= start:
                            errors.append(f"{context}: end_sec must be greater than start_sec")
                        if start < 0 or (duration > 0 and end > duration + 1e-3):
                            errors.append(
                                f"{context}: window {start:.2f}-{end:.2f}s outside edit duration"
                            )
                        seg_file = str(segment.get("file") or relative)
                        seg_path = episode_root / seg_file if seg_file else None
                        if not seg_file or seg_path is None or not seg_path.is_file():
                            errors.append(f"{context}: missing file {seg_file!r}")
                        seg_license = str(segment.get("license") or license_name).strip().lower()
                        if seg_license not in ALLOWED_LICENSES:
                            errors.append(
                                f"{context}: license must be one of {sorted(ALLOWED_LICENSES)}"
                            )
    return errors
