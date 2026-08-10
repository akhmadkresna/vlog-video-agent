from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DEFAULT_CAPTIONS: dict[str, Any] = {
    "enabled": True,
    "burn_in": True,
    "max_chars": 42,
    "max_lines": 2,
    "max_cue_sec": 4.5,
    "min_cue_sec": 0.7,
    "font_name": "Arial",
    "font_size": 48,
    "margin_v": 64,
}


def captions_config(episode_config: dict[str, Any]) -> dict[str, Any]:
    raw = episode_config.get("captions")
    merged = dict(DEFAULT_CAPTIONS)
    if isinstance(raw, dict):
        merged.update(raw)
    return merged


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        whole += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{millis:03d}"


def _clean_caption_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    cleaned = cleaned.replace("-->", "->")
    return cleaned


def _escape_ass_text(text: str) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(text or "").replace("-->", "->").splitlines()
    ]
    cleaned = "\n".join(line for line in lines if line)
    return (
        cleaned.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


def _analysis_by_file(analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("metadata", {}).get("filename", "")): item
        for item in analysis.get("clips", [])
        if isinstance(item, dict) and item.get("metadata", {}).get("filename")
    }


def _words_for_selection(
    source: dict[str, Any],
    *,
    clip_start: float,
    clip_end: float,
) -> list[dict[str, Any]]:
    audio = source.get("audio", {}) if isinstance(source.get("audio"), dict) else {}
    words = [
        word
        for word in audio.get("words", []) or []
        if isinstance(word, dict)
        and word.get("start") is not None
        and word.get("end") is not None
        and _clean_caption_text(str(word.get("text", "")))
    ]
    selected: list[dict[str, Any]] = []
    for word in words:
        start = float(word["start"])
        end = float(word["end"])
        if end <= clip_start + 1e-3 or start >= clip_end - 1e-3:
            continue
        selected.append(
            {
                "start": max(clip_start, start),
                "end": min(clip_end, end),
                "text": _clean_caption_text(str(word.get("text", ""))),
            }
        )
    if selected:
        return selected

    # Fall back to segment text when word timestamps are missing.
    segments = [
        segment
        for segment in audio.get("segments", []) or []
        if isinstance(segment, dict)
    ]
    for segment in segments:
        try:
            start = float(segment.get("start", 0))
            end = float(segment.get("end", start))
        except (TypeError, ValueError):
            continue
        text = _clean_caption_text(str(segment.get("text", "")))
        if not text or end <= clip_start + 1e-3 or start >= clip_end - 1e-3:
            continue
        selected.append(
            {
                "start": max(clip_start, start),
                "end": min(clip_end, end),
                "text": text,
            }
        )
    return selected


def _pack_words_into_cues(
    words: list[dict[str, Any]],
    *,
    timeline_offset: float,
    clip_start: float,
    max_chars: int,
    max_lines: int,
    max_cue_sec: float,
    min_cue_sec: float,
) -> list[dict[str, Any]]:
    if not words:
        return []

    cues: list[dict[str, Any]] = []
    line_budget = max(1, int(max_lines)) * max(12, int(max_chars))
    current_words: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current_words
        if not current_words:
            return
        text = _clean_caption_text(" ".join(str(item["text"]) for item in current_words))
        if not text:
            current_words = []
            return
        start = timeline_offset + (float(current_words[0]["start"]) - clip_start)
        end = timeline_offset + (float(current_words[-1]["end"]) - clip_start)
        if end - start < min_cue_sec:
            end = start + min_cue_sec
        # Soft wrap into up to max_lines.
        pieces: list[str] = []
        remaining = text
        for _ in range(max(1, int(max_lines))):
            if len(remaining) <= max_chars:
                pieces.append(remaining)
                remaining = ""
                break
            cut = remaining.rfind(" ", 0, max_chars + 1)
            if cut < max(8, max_chars // 3):
                cut = max_chars
            pieces.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            pieces[-1] = _clean_caption_text(f"{pieces[-1]} {remaining}")
        cues.append({"start": start, "end": end, "text": "\n".join(pieces)})
        current_words = []

    for word in words:
        if not current_words:
            current_words = [word]
            continue
        tentative = _clean_caption_text(
            " ".join(str(item["text"]) for item in current_words + [word])
        )
        span = float(word["end"]) - float(current_words[0]["start"])
        boundary = bool(re.search(r"[.!?…]$", str(current_words[-1]["text"])))
        if len(tentative) > line_budget or span > max_cue_sec or boundary:
            flush()
            current_words = [word]
        else:
            current_words.append(word)
    flush()
    return cues


def build_caption_cues(
    plan: dict[str, Any],
    analysis: dict[str, Any],
    *,
    max_chars: int = 42,
    max_lines: int = 2,
    max_cue_sec: float = 4.5,
    min_cue_sec: float = 0.7,
) -> list[dict[str, Any]]:
    """Map ASR words onto the edit timeline for each selected clip range."""
    by_file = _analysis_by_file(analysis)
    cues: list[dict[str, Any]] = []
    offset = 0.0
    for section in plan.get("structure", []) or []:
        if not isinstance(section, dict):
            continue
        for clip in section.get("clips", []) or []:
            if not isinstance(clip, dict):
                continue
            try:
                clip_start = float(clip.get("start", 0))
                clip_end = float(clip.get("end", clip_start))
            except (TypeError, ValueError):
                continue
            duration = max(0.0, clip_end - clip_start)
            source = by_file.get(str(clip.get("file", "")))
            if source is not None and duration > 0:
                words = _words_for_selection(
                    source,
                    clip_start=clip_start,
                    clip_end=clip_end,
                )
                cues.extend(
                    _pack_words_into_cues(
                        words,
                        timeline_offset=offset,
                        clip_start=clip_start,
                        max_chars=max_chars,
                        max_lines=max_lines,
                        max_cue_sec=max_cue_sec,
                        min_cue_sec=min_cue_sec,
                    )
                )
            offset += duration

    # Keep chronological and collapse tiny overlaps.
    cues.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    cleaned: list[dict[str, Any]] = []
    for cue in cues:
        start = float(cue["start"])
        end = float(cue["end"])
        text = _clean_caption_text(str(cue.get("text", "")))
        if not text or end <= start:
            continue
        if cleaned and start < float(cleaned[-1]["end"]):
            start = float(cleaned[-1]["end"])
            if end - start < 0.35:
                continue
        cleaned.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return cleaned


def write_srt(cues: list[dict[str, Any]], path: Path) -> Path:
    lines: list[str] = []
    for index, cue in enumerate(cues, start=1):
        lines.append(str(index))
        lines.append(f"{_srt_time(float(cue['start']))} --> {_srt_time(float(cue['end']))}")
        lines.append(str(cue["text"]))
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_ass(
    cues: list[dict[str, Any]],
    path: Path,
    *,
    width: int,
    height: int,
    font_name: str = "Arial",
    font_size: int = 48,
    margin_v: int = 64,
) -> Path:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {int(width)}
PlayResY: {int(height)}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{int(font_size)},&H00FFFFFF,&H000000FF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,80,80,{int(margin_v)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for cue in cues:
        events.append(
            "Dialogue: 0,"
            f"{_ass_time(float(cue['start']))},"
            f"{_ass_time(float(cue['end']))},"
            f"Default,,0,0,0,,{_escape_ass_text(str(cue['text']))}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(events) + ("\n" if events else ""), encoding="utf-8")
    return path


def ffmpeg_subtitles_path(path: Path) -> str:
    """Escape an absolute Windows path for ffmpeg subtitles/ass filters."""
    text = path.resolve().as_posix()
    if len(text) >= 2 and text[1] == ":":
        text = f"{text[0]}\\:{text[2:]}"
    return text.replace("'", r"\'").replace(",", r"\,")


def prepare_caption_files(
    episode: Any,
    plan: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    """Write soft SRT (+ ASS for burn-in) from plan+analysis. Returns caption metadata."""
    config = captions_config(episode.config)
    if not bool(config.get("enabled", True)):
        return {"enabled": False, "cues": 0}

    cues = build_caption_cues(
        plan,
        analysis,
        max_chars=int(config.get("max_chars", 42)),
        max_lines=int(config.get("max_lines", 2)),
        max_cue_sec=float(config.get("max_cue_sec", 4.5)),
        min_cue_sec=float(config.get("min_cue_sec", 0.7)),
    )
    srt_path = episode.output / "captions.srt"
    ass_path = episode.work / "captions.ass"
    write_srt(cues, srt_path)
    write_ass(
        cues,
        ass_path,
        width=int(episode.config["output"]["width"]),
        height=int(episode.config["output"]["height"]),
        font_name=str(config.get("font_name", "Arial")),
        font_size=int(config.get("font_size", 48)),
        margin_v=int(config.get("margin_v", 64)),
    )
    return {
        "enabled": True,
        "burn_in": bool(config.get("burn_in", True)),
        "cues": len(cues),
        "srt_path": str(srt_path),
        "ass_path": str(ass_path),
    }
