from __future__ import annotations

import copy
from pathlib import Path
from typing import Any


def _speech_bounds(
    start: float,
    end: float,
    words: list[dict[str, Any]],
    *,
    pad_start: float = 0.08,
    pad_end: float = 0.12,
) -> tuple[float, float]:
    overlapping = [
        word
        for word in words
        if float(word.get("end", 0)) > start and float(word.get("start", 0)) < end
    ]
    if not overlapping:
        return start, end
    first, last = overlapping[0], overlapping[-1]
    if float(first["start"]) < start < float(first["end"]):
        start = max(0.0, float(first["start"]) - pad_start)
    if float(last["start"]) < end < float(last["end"]):
        end = float(last["end"]) + pad_end
    return start, end


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
            start, end = _speech_bounds(start, end, words)
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
            selected_by_file.setdefault(filename, []).append((start, end))
            clip["start"] = round(start, 3)
            clip["end"] = round(end, 3)
            clip.setdefault("note", "")
            clip.setdefault("subtitle", "")
            total += end - start

    fixed["duration_sec"] = round(total, 3)
    fixed.setdefault("title", "Untitled Vlog")
    fixed.setdefault("bgm_suggestion", "")
    fixed.setdefault("editing_notes", "")
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
