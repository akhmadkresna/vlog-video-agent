from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from vlog_editor.audio_pack import (
    MEME_ROLES,
    ensure_audio_pack_layout,
    load_audio_pack,
    meme_files_for_role,
    resolve_pack_file,
)
from vlog_editor.project import Episode
from vlog_editor.validation import infer_setting

DENSITY_MIN_GAP = {
    "light": 10.0,
    "medium": 6.0,
    "heavy": 3.5,
}

LAUGH_RE = re.compile(
    r"\b(ketawa|lucu|haha|hehe|hihi|laugh|funny|gemes|gemas)\b",
    re.IGNORECASE,
)
FAIL_RE = re.compile(
    r"\b(jatuh|gagal|kotor|aduh|oops|oh no|salah|rusak)\b",
    re.IGNORECASE,
)
FART_RE = re.compile(
    r"\b(kentut|fart|pup|mencret|bau)\b",
    re.IGNORECASE,
)
CUTE_RE = re.compile(
    r"\b(baby|bayi|adek|adik|anak|child|smile|senyum|main)\b",
    re.IGNORECASE,
)
CHILDREN_RE = re.compile(
    r"\b("
    r"child|children|kid|kids|boy|girl|baby|bayi|anak|adek|adik|"
    r"toddler|son|daughter|mbak\s*merah|adek\s*arka|arka"
    r")\b",
    re.IGNORECASE,
)
PLAY_RE = re.compile(
    r"\b("
    r"main|bermain|mainan|play|playing|fool|fooling|chase|kejar|"
    r"lari|lompat|guling|seru|asik|heboh|ribut|joget|dance|"
    r"fun|game|toy|tiktok|playground|ayunan|loncat"
    r")\b",
    re.IGNORECASE,
)
# Activity-type cues for a kids audience (categories, not place hardcodes).
# Example: animals covers fish/rabbits/birds — we never special-case "fish pond".
KIDS_AUDIENCE_CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "play_structure",
        re.compile(
            r"\b("
            r"playground|ayunan|slide|perosotan|jungkatan|climbing|climb|"
            r"tunnel|ayun|ball\s*pit|trampoline|ayunan"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "animals",
        re.compile(
            r"\b("
            r"animal|animals|hewan|fish|ikan|rabbit|kelinci|bird|burung|"
            r"zoo|mini\s*zoo|kucing|cat|dog|anjing|duck|bebek"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "water_play",
        re.compile(
            r"\b("
            r"pool|renang|swim|swimming|splash|water\s*play|kolam|"
            r"fountain|pancuran|beach|pantai"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "treats",
        re.compile(
            r"\b("
            r"donut|donat|ice\s*cream|es\s*krim|candy|permen|snack|"
            r"cookies|kue|dessert|sweet|manis|gelato"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "discovery",
        re.compile(
            r"\b("
            r"look|lihat|wow|surprise|kaget|curious|penasaran|point|"
            r"tunjuk|discover|explore|jelajah|amazing|keren|lihat\s*ya"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ride_fun",
        re.compile(
            r"\b("
            r"carousel|komedi\s*putar|ride|wahana|train\s*ride|"
            r"scooter|sepeda|stroller\s*ride"
            r")\b",
            re.IGNORECASE,
        ),
    ),
)


def text_has_children(text: str) -> bool:
    return bool(CHILDREN_RE.search(text or ""))


def kids_audience_categories(text: str) -> list[str]:
    blob = text or ""
    return [name for name, pattern in KIDS_AUDIENCE_CATEGORIES if pattern.search(blob)]


def kids_audience_interest(text: str) -> float:
    """0..1 score for how interesting the beat is to a kids audience."""
    categories = kids_audience_categories(text)
    if not categories:
        return 0.0
    # Diminishing returns for stacking categories in one window.
    return min(1.0, 0.34 * len(categories) + (0.12 if PLAY_RE.search(text or "") else 0.0))


MAX_MEME_PER_TOTAL = 14.0

INTRO_SEC = 8.0
OUTRO_SEC = 8.0
MAX_BGM_COVERAGE = 0.45
MAX_PLAY_BED_SEC = 18.0
MAX_FUN_BED_SEC = 12.0
MERGE_GAP_SEC = 2.0
LOW_SPEECH_WORDS_PER_SEC = 0.85


def _timeline_clips(plan: dict[str, Any]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for section in plan.get("structure", []):
        if not isinstance(section, dict):
            continue
        for clip in section.get("clips", []):
            if not isinstance(clip, dict):
                continue
            start = float(clip["start"])
            end = float(clip["end"])
            duration = end - start
            timeline.append(
                {
                    "file": str(clip["file"]),
                    "source_start": start,
                    "source_end": end,
                    "edit_start": cursor,
                    "edit_end": cursor + duration,
                    "duration": duration,
                    "note": str(clip.get("note", "")),
                    "subtitle": str(clip.get("subtitle", "")),
                    "section": str(section.get("section", "")),
                    "setting_change": False,
                }
            )
            cursor += duration
    return timeline


def _annotate_settings(
    timeline: list[dict[str, Any]],
    analysis: dict[str, Any],
) -> None:
    by_name = {
        str(item["metadata"]["filename"]): item for item in analysis.get("clips", [])
    }
    previous = None
    for item in timeline:
        source = by_name.get(item["file"], {})
        setting = infer_setting(source) if source else "other"
        item["setting"] = setting
        item["setting_change"] = previous is not None and setting != previous
        item["story_value"] = float(source.get("visual", {}).get("story_value", 0.5) or 0.5)
        item["quality"] = float(source.get("visual", {}).get("quality", 0.5) or 0.5)
        item["summary"] = str(source.get("visual", {}).get("summary", "") or "")
        item["words"] = source.get("audio", {}).get("words", []) or []
        previous = setting


def _speech_density(words: list[dict[str, Any]], source_start: float, source_end: float) -> int:
    return sum(
        1
        for word in words
        if isinstance(word, dict)
        and float(word.get("end", 0)) > source_start
        and float(word.get("start", 0)) < source_end
    )


def _dense_speech(
    item: dict[str, Any],
    edit_at: float,
    *,
    window: float = 0.4,
) -> bool:
    offset = edit_at - float(item["edit_start"])
    source_center = float(item["source_start"]) + offset
    count = _speech_density(
        item["words"],
        source_center - window,
        source_center + window,
    )
    return count >= 3


def _pick_file(candidates: list[dict[str, Any]], *, salt: int) -> dict[str, Any] | None:
    if not candidates:
        return None
    return candidates[salt % len(candidates)]


def _can_place(cues: list[dict[str, Any]], at_sec: float, min_gap: float) -> bool:
    return all(abs(at_sec - float(cue["at_sec"])) >= min_gap for cue in cues)


def _clip_text(item: dict[str, Any]) -> str:
    return f"{item.get('subtitle', '')} {item.get('note', '')} {item.get('summary', '')}"


def _speech_rate(item: dict[str, Any]) -> float:
    duration = max(0.1, float(item["duration"]))
    count = _speech_density(
        item.get("words", []) or [],
        float(item["source_start"]),
        float(item["source_end"]),
    )
    return count / duration


def _is_play_moment(item: dict[str, Any]) -> bool:
    return bool(PLAY_RE.search(_clip_text(item)))


def _is_fun_moment(item: dict[str, Any]) -> bool:
    text = _clip_text(item)
    if LAUGH_RE.search(text):
        return True
    return bool(CUTE_RE.search(text) and float(item.get("story_value", 0)) >= 0.75)


def _clip_window(
    item: dict[str, Any],
    *,
    max_sec: float,
    prefer: str = "start",
) -> tuple[float, float]:
    start = float(item["edit_start"])
    end = float(item["edit_end"])
    duration = end - start
    if duration <= max_sec:
        return start, end
    if prefer == "center":
        pad = (duration - max_sec) / 2.0
        return start + pad, end - pad
    return start, start + max_sec


def _primary_reason(reason: str) -> str:
    text = str(reason or "")
    for key in (
        "intro",
        "outro",
        "playing / fooling around",
        "fun moment",
        "light b-roll",
        "setting transition",
    ):
        if key in text:
            return key
    return text


def _merge_bgm_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not segments:
        return []
    ordered = sorted(segments, key=lambda item: float(item["start_sec"]))
    merged: list[dict[str, Any]] = [dict(ordered[0])]
    for item in ordered[1:]:
        previous = merged[-1]
        gap_ok = float(item["start_sec"]) <= float(previous["end_sec"]) + MERGE_GAP_SEC
        same_reason = _primary_reason(str(previous.get("reason", ""))) == _primary_reason(
            str(item.get("reason", ""))
        )
        overlapping = float(item["start_sec"]) <= float(previous["end_sec"]) + 1e-6
        if overlapping or (gap_ok and same_reason):
            previous["end_sec"] = max(float(previous["end_sec"]), float(item["end_sec"]))
            previous["volume"] = max(float(previous["volume"]), float(item["volume"]))
            if same_reason:
                previous["reason"] = _primary_reason(str(previous.get("reason", "")))
            else:
                reasons = {
                    _primary_reason(str(previous.get("reason", ""))),
                    _primary_reason(str(item.get("reason", ""))),
                }
                previous["reason"] = " + ".join(sorted(reason for reason in reasons if reason))
        else:
            merged.append(dict(item))
    return merged


def _trim_bgm_coverage(
    segments: list[dict[str, Any]],
    total: float,
    *,
    max_coverage: float = MAX_BGM_COVERAGE,
) -> list[dict[str, Any]]:
    if total <= 0 or not segments:
        return segments
    budget = total * max_coverage
    priority = {
        "playing / fooling around": 0,
        "fun moment": 1,
        "intro": 2,
        "outro": 3,
        "light b-roll": 4,
        "setting transition": 5,
    }

    def rank(segment: dict[str, Any]) -> tuple[int, float]:
        reason = _primary_reason(str(segment.get("reason", "")))
        return priority.get(reason, 9), -float(segment["end_sec"] - segment["start_sec"])

    kept: list[dict[str, Any]] = []
    used = 0.0
    # Keep a little room for open/close beds when present.
    reserved = 0.0
    reasons_present = {_primary_reason(str(seg.get("reason", ""))) for seg in segments}
    if "intro" in reasons_present:
        reserved += min(INTRO_SEC, budget * 0.15)
    if "outro" in reasons_present:
        reserved += min(OUTRO_SEC, budget * 0.15)

    for segment in sorted(segments, key=rank):
        start = float(segment["start_sec"])
        end = float(segment["end_sec"])
        duration = end - start
        if duration <= 0:
            continue
        reason = _primary_reason(str(segment.get("reason", "")))
        soft_budget = budget if reason in {"intro", "outro"} else max(2.0, budget - reserved)
        remaining = soft_budget - used
        if reason in {"intro", "outro"}:
            remaining = budget - used
        if remaining < 2.0 and kept:
            continue
        if duration > remaining:
            segment = dict(segment)
            segment["end_sec"] = start + max(2.0, remaining)
            duration = float(segment["end_sec"]) - start
        kept.append(segment)
        used += duration
        if reason in {"intro", "outro"}:
            reserved = max(0.0, reserved - duration)
        if used >= budget - 1e-6:
            break
    return _merge_bgm_segments(kept)


def _plan_bgm_segments(
    timeline: list[dict[str, Any]],
    total: float,
    *,
    base_volume: float,
) -> list[dict[str, Any]]:
    if total <= 0 or not timeline:
        return []

    segments: list[dict[str, Any]] = []
    for item in timeline:
        start = float(item["edit_start"])
        end = float(item["edit_end"])
        if end - start < 2.5:
            continue
        if _is_play_moment(item):
            bed_start, bed_end = _clip_window(
                item, max_sec=MAX_PLAY_BED_SEC, prefer="start"
            )
            segments.append(
                {
                    "start_sec": bed_start,
                    "end_sec": bed_end,
                    "volume": min(0.16, base_volume + 0.03),
                    "reason": "playing / fooling around",
                }
            )
            continue
        if _is_fun_moment(item):
            bed_start, bed_end = _clip_window(
                item, max_sec=MAX_FUN_BED_SEC, prefer="center"
            )
            segments.append(
                {
                    "start_sec": bed_start,
                    "end_sec": bed_end,
                    "volume": min(0.15, base_volume + 0.02),
                    "reason": "fun moment",
                }
            )
            continue
        if item.get("setting_change") and _speech_rate(item) <= LOW_SPEECH_WORDS_PER_SEC:
            pad = min(4.0, (end - start) * 0.5)
            segments.append(
                {
                    "start_sec": start,
                    "end_sec": min(end, start + pad),
                    "volume": base_volume,
                    "reason": "setting transition",
                }
            )
        elif _speech_rate(item) <= LOW_SPEECH_WORDS_PER_SEC and float(
            item.get("story_value", 0)
        ) >= 0.55:
            segments.append(
                {
                    "start_sec": start,
                    "end_sec": end,
                    "volume": base_volume,
                    "reason": "light b-roll",
                }
            )

    # Intro/outro last so play beds win when they already cover the open/close.
    has_open_bed = any(float(seg["start_sec"]) <= 0.5 for seg in segments)
    if not has_open_bed:
        segments.append(
            {
                "start_sec": 0.0,
                "end_sec": min(INTRO_SEC, total),
                "volume": min(0.20, base_volume + 0.06),
                "reason": "intro",
            }
        )
    has_close_bed = any(float(seg["end_sec"]) >= total - 0.5 for seg in segments)
    if total > OUTRO_SEC + 2 and not has_close_bed:
        segments.append(
            {
                "start_sec": max(0.0, total - OUTRO_SEC),
                "end_sec": total,
                "volume": min(0.18, base_volume + 0.04),
                "reason": "outro",
            }
        )

    return _trim_bgm_coverage(_merge_bgm_segments(segments), total)


def _normalize_bgm_relative(relative: str) -> str:
    text = str(relative).replace("\\", "/")
    if text.startswith("audio/"):
        return text
    return f"audio/{text.lstrip('/')}"


def _bgm_pool(
    episode: Episode,
    pack: dict[str, Any],
    bgm_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolve usable BGM candidates. Pinned bgm.file uses only that track."""
    configured = bgm_config.get("file")
    volume = float(bgm_config.get("volume", 0.12))
    if configured:
        relative = str(configured)
        candidate = Path(relative)
        path = candidate if candidate.is_absolute() else episode.root / candidate
        if not path.is_file():
            return []
        license_name = "original"
        attribution = None
        for entry in pack["bgm_candidates"]:
            entry_path = resolve_pack_file(episode.root, entry["file"])
            if entry_path.resolve() == path.resolve():
                license_name = entry["license"]
                attribution = entry.get("attribution")
                relative = _normalize_bgm_relative(str(entry["file"]))
                break
        else:
            try:
                relative = str(path.relative_to(episode.root)).replace("\\", "/")
            except ValueError:
                relative = str(path)
        return [
            {
                "file": relative.replace("\\", "/"),
                "volume": volume,
                "license": license_name,
                "attribution": attribution,
            }
        ]

    pool: list[dict[str, Any]] = []
    for entry in pack["bgm_candidates"]:
        relative = _normalize_bgm_relative(str(entry["file"]))
        if not resolve_pack_file(episode.root, relative).is_file():
            continue
        pool.append(
            {
                "file": relative,
                "volume": float(entry.get("gain", volume)),
                "license": entry["license"],
                "attribution": entry.get("attribution"),
            }
        )
    return pool


def _assign_bgm_tracks(
    segments: list[dict[str, Any]],
    pool: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Round-robin BGM across beds; avoid immediate repeats when 2+ tracks exist."""
    if not segments or not pool:
        return segments
    assigned: list[dict[str, Any]] = []
    last_index = -1
    for offset, segment in enumerate(segments):
        if len(pool) == 1:
            index = 0
        else:
            index = offset % len(pool)
            if index == last_index:
                index = (index + 1) % len(pool)
        last_index = index
        chosen = pool[index]
        item = dict(segment)
        item["file"] = chosen["file"]
        item["license"] = chosen["license"]
        item["attribution"] = chosen.get("attribution")
        assigned.append(item)
    return assigned


def _resolve_bgm_source(
    episode: Episode,
    pack: dict[str, Any],
    bgm_config: dict[str, Any],
) -> dict[str, Any] | None:
    pool = _bgm_pool(episode, pack, bgm_config)
    if not pool:
        return None
    primary = pool[0]
    return {
        "file": primary["file"],
        "volume": float(bgm_config.get("volume", primary.get("volume", 0.12))),
        "license": primary["license"],
        "attribution": primary.get("attribution"),
        "candidates": pool,
    }


def plan_audio_cues(
    episode: Episode,
    plan: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    audio_config = dict(episode.config.get("audio", {}))
    if not audio_config.get("enabled", True):
        return plan

    ensure_audio_pack_layout(episode.root)
    pack_rel = str(audio_config.get("pack", "audio/pack.yaml"))
    pack = load_audio_pack(episode.root, pack_rel)
    if pack["errors"]:
        raise ValueError("Audio pack is invalid:\n- " + "\n- ".join(pack["errors"]))

    timeline = _timeline_clips(plan)
    if not timeline:
        return plan
    _annotate_settings(timeline, analysis)

    density = str(audio_config.get("sfx_density", "medium")).lower()
    min_gap = DENSITY_MIN_GAP.get(density, DENSITY_MIN_GAP["medium"])
    total = float(plan.get("duration_sec") or timeline[-1]["edit_end"])
    max_cues = max(1, int(total / 8))
    max_meme = max(2, int(total / MAX_MEME_PER_TOTAL))

    cues: list[dict[str, Any]] = []
    salt = 0
    meme_count = 0

    def add_cue(
        cue_types: str | list[str],
        at_sec: float,
        reason: str,
        item: dict[str, Any],
    ) -> bool:
        """Try cue types in order (classic folder or meme role). Returns True if placed."""
        nonlocal salt, meme_count
        if len(cues) >= max_cues:
            return False
        at_sec = max(0.0, min(total - 0.05, at_sec))
        if not _can_place(cues, at_sec, min_gap):
            return False
        if _dense_speech(item, at_sec):
            return False

        candidates = [cue_types] if isinstance(cue_types, str) else list(cue_types)
        for cue_type in candidates:
            is_meme = cue_type in MEME_ROLES
            if is_meme and meme_count >= max_meme:
                continue
            if is_meme:
                block = pack["sfx"].get("meme", {})
                files = meme_files_for_role(pack, cue_type)
            else:
                block = pack["sfx"].get(cue_type, {})
                files = list(block.get("files", []) or [])
            chosen = _pick_file(files, salt=salt)
            salt += 1
            if chosen is None:
                continue
            relative = str(chosen["file"])
            if not relative.startswith("audio/"):
                relative = f"audio/{relative.lstrip('/')}"
            path = resolve_pack_file(episode.root, relative)
            if not path.is_file():
                continue
            cue: dict[str, Any] = {
                "at_sec": round(at_sec, 3),
                "type": cue_type,
                "file": relative.replace("\\", "/"),
                "gain": float(chosen.get("gain", block.get("gain", 0.5))),
                "license": chosen["license"],
                "attribution": chosen.get("attribution"),
                "reason": reason,
            }
            if is_meme:
                cue["role"] = cue_type
                meme_count += 1
            cues.append(cue)
            return True
        return False

    for item in timeline:
        text = _clip_text(item)
        # Transitions: light click by default; vine-boom only on strong story beats.
        if item["setting_change"]:
            if float(item.get("story_value", 0)) >= 0.8:
                add_cue(
                    ["boom", "whoosh", "click"],
                    item["edit_start"] + 0.08,
                    "setting transition punch",
                    item,
                )
            else:
                add_cue(
                    ["click", "whoosh", "boom"],
                    item["edit_start"] + 0.08,
                    "setting transition",
                    item,
                )
        # Reaction hierarchy — mutually exclusive body, transition may still fire.
        if FART_RE.search(text):
            add_cue(
                ["fart", "bruh", "fail"],
                item["edit_start"] + min(0.6, item["duration"] * 0.2),
                "silly toilet-joke beat",
                item,
            )
        elif FAIL_RE.search(text):
            add_cue(
                ["bruh", "fail"],
                item["edit_start"] + min(0.8, item["duration"] * 0.25),
                "soft mishap moment",
                item,
            )
        elif LAUGH_RE.search(text) or (
            item["story_value"] >= 0.75 and CUTE_RE.search(text)
        ):
            add_cue(
                ["goofy_laugh", "sparkle"],
                item["edit_start"] + min(1.2, item["duration"] * 0.35),
                "cute or laugh moment",
                item,
            )
        elif item["duration"] <= 8 and item["story_value"] >= 0.7:
            add_cue(
                ["click", "pop", "boing"],
                item["edit_start"] + min(0.5, item["duration"] * 0.2),
                "short punchy clip",
                item,
            )
        elif item["duration"] > 20 and item["quality"] >= 0.7:
            add_cue(
                ["boing", "boom"],
                item["edit_start"] + item["duration"] * 0.55,
                "mid-clip energy beat",
                item,
            )

    cues.sort(key=lambda cue: float(cue["at_sec"]))

    bgm_config = dict(episode.config.get("bgm", {}))
    bgm_payload = _resolve_bgm_source(episode, pack, bgm_config)
    configured = bgm_config.get("file")
    if bgm_payload:
        mode = str(bgm_config.get("mode", "beds")).lower()
        bgm_payload["mode"] = mode if mode in {"beds", "full"} else "beds"
        pool = list(bgm_payload.pop("candidates", []) or [])
        if bgm_payload["mode"] == "beds":
            segments = _plan_bgm_segments(
                timeline,
                total,
                base_volume=float(bgm_payload["volume"]),
            )
            assigned = _assign_bgm_tracks(segments, pool)
            bgm_payload["segments"] = [
                {
                    "start_sec": round(float(segment["start_sec"]), 3),
                    "end_sec": round(float(segment["end_sec"]), 3),
                    "volume": round(float(segment["volume"]), 3),
                    "reason": str(segment["reason"]),
                    "file": str(segment["file"]),
                    "license": str(segment["license"]),
                    "attribution": segment.get("attribution"),
                }
                for segment in assigned
            ]
            if assigned:
                bgm_payload["file"] = assigned[0]["file"]
                bgm_payload["license"] = assigned[0]["license"]
                bgm_payload["attribution"] = assigned[0].get("attribution")
        else:
            bgm_payload.pop("segments", None)
        plan["bgm"] = bgm_payload
    elif "bgm" in plan and not configured:
        plan.pop("bgm", None)

    plan["audio_cues"] = cues
    attribution_items: list[dict[str, Any]] = list(cues)
    if bgm_payload:
        attribution_items.append(bgm_payload)
        attribution_items.extend(bgm_payload.get("segments") or [])
    attributions = sorted(
        {
            str(item["attribution"]).strip()
            for item in attribution_items
            if item and item.get("attribution")
        }
    )
    if attributions:
        notes = str(plan.get("editing_notes", "")).rstrip()
        credit = "Audio attribution: " + "; ".join(attributions)
        plan["editing_notes"] = f"{notes}\n{credit}".strip()
    return plan
