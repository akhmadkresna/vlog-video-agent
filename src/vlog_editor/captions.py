from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DEFAULT_CAPTIONS: dict[str, Any] = {
    "enabled": True,
    "burn_in": False,
    "max_chars": 42,
    "max_lines": 2,
    "max_cue_sec": 4.5,
    "min_cue_sec": 0.7,
    "font_name": "Arial",
    "font_size": 64,
    "bold": True,
    "margin_v": 64,
    # Karaoke-style burned-in captions: words highlight in primary_color as they
    # are spoken, outlined in outline_color, sized for easy reading.
    "karaoke": True,
    "primary_color": "#FF2D78",
    "secondary_color": "#FFFFFF",
    "outline_color": "#FFFFFF",
    "outline_width": 4,
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


def _hex_to_ass_color(hex_color: str) -> str:
    """Convert '#RRGGBB' to ASS '&HAABBGGRR' (opaque)."""
    text = str(hex_color or "").strip().lstrip("#")
    if len(text) != 6:
        text = "FFFFFF"
    red, green, blue = text[0:2], text[2:4], text[4:6]
    return f"&H00{blue}{green}{red}".upper()


def _escape_ass_word(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return cleaned.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


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


def _wrap_words_into_lines(
    words: list[dict[str, Any]],
    *,
    max_chars: int,
    max_lines: int,
) -> list[list[dict[str, Any]]]:
    """Greedy word-boundary wrap into at most max_lines lines of max_chars each."""
    lines: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_len = 0
    for word in words:
        text = str(word["text"])
        added = len(text) + (1 if current else 0)
        if current and current_len + added > max_chars:
            lines.append(current)
            current = [word]
            current_len = len(text)
        else:
            current.append(word)
            current_len += added
    if current:
        lines.append(current)
    limit = max(1, int(max_lines))
    if len(lines) > limit:
        kept = lines[: limit - 1] if limit > 1 else []
        overflow = [word for line in lines[limit - 1 :] for word in line]
        kept.append(overflow)
        lines = kept
    return lines


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
        lines = _wrap_words_into_lines(current_words, max_chars=max_chars, max_lines=max_lines)
        timeline_words: list[dict[str, Any]] = []
        for line_index, line in enumerate(lines):
            for word_index, word in enumerate(line):
                timeline_words.append(
                    {
                        "text": str(word["text"]),
                        "start": timeline_offset + (float(word["start"]) - clip_start),
                        "end": timeline_offset + (float(word["end"]) - clip_start),
                        "line_break_after": word_index == len(line) - 1
                        and line_index < len(lines) - 1,
                    }
                )
        text = "\n".join(" ".join(str(word["text"]) for word in line) for line in lines)
        cues.append({"start": start, "end": end, "text": text, "words": timeline_words})
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
        words = [dict(word) for word in cue.get("words", []) or []]
        cleaned.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "words": words,
            }
        )
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


def _karaoke_ass_text(words: list[dict[str, Any]]) -> str:
    """Build ASS text with per-word {\\k<centiseconds>} highlight tags and \\N line breaks."""
    lines: list[str] = []
    tokens: list[str] = []
    prev_end: float | None = None
    for word in words:
        start = float(word.get("start", 0))
        end = float(word.get("end", start))
        if prev_end is not None:
            gap_cs = round((start - prev_end) * 100)
            if gap_cs > 2:
                tokens.append(f"{{\\k{gap_cs}}}")
        duration_cs = max(1, round((end - start) * 100))
        text = _escape_ass_word(str(word.get("text", "")))
        tokens.append(f"{{\\k{duration_cs}}}{text}")
        prev_end = end
        if word.get("line_break_after"):
            lines.append(" ".join(tokens))
            tokens = []
    if tokens:
        lines.append(" ".join(tokens))
    return r"\N".join(lines)


def write_ass(
    cues: list[dict[str, Any]],
    path: Path,
    *,
    width: int,
    height: int,
    font_name: str = "Arial",
    font_size: int = 64,
    margin_v: int = 64,
    bold: bool = True,
    karaoke: bool = True,
    primary_color: str = "#FF2D78",
    secondary_color: str = "#FFFFFF",
    outline_color: str = "#FFFFFF",
    outline_width: int = 4,
) -> Path:
    primary = _hex_to_ass_color(primary_color)
    secondary = _hex_to_ass_color(secondary_color)
    outline = _hex_to_ass_color(outline_color)
    bold_flag = -1 if bold else 0
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {int(width)}
PlayResY: {int(height)}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{int(font_size)},{primary},{secondary},{outline},&H80000000,{bold_flag},0,0,0,100,100,0,0,1,{int(outline_width)},1,2,80,80,{int(margin_v)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for cue in cues:
        words = cue.get("words") or []
        if karaoke and words:
            text = _karaoke_ass_text(words)
        else:
            text = _escape_ass_text(str(cue["text"]))
        events.append(
            "Dialogue: 0,"
            f"{_ass_time(float(cue['start']))},"
            f"{_ass_time(float(cue['end']))},"
            f"Default,,0,0,0,,{text}"
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
    *,
    time_skip_cards: list[dict[str, Any]] | None = None,
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
    if time_skip_cards:
        from vlog_editor.transitions import shift_time

        for word in [w for cue in cues for w in cue.get("words", [])]:
            word["start"] = shift_time(word["start"], time_skip_cards)
            word["end"] = shift_time(word["end"], time_skip_cards)
        for cue in cues:
            cue["start"] = shift_time(cue["start"], time_skip_cards)
            cue["end"] = shift_time(cue["end"], time_skip_cards)
    srt_path = episode.output / "captions.srt"
    write_srt(cues, srt_path)
    ass_path: Path | None = None
    burn_in = bool(config.get("burn_in", False))
    if burn_in:
        ass_path = episode.work / "captions.ass"
        write_ass(
            cues,
            ass_path,
            width=int(episode.config["output"]["width"]),
            height=int(episode.config["output"]["height"]),
            font_name=str(config.get("font_name", "Arial")),
            font_size=int(config.get("font_size", 64)),
            margin_v=int(config.get("margin_v", 64)),
            bold=bool(config.get("bold", True)),
            karaoke=bool(config.get("karaoke", True)),
            primary_color=str(config.get("primary_color", "#FF2D78")),
            secondary_color=str(config.get("secondary_color", "#FFFFFF")),
            outline_color=str(config.get("outline_color", "#FFFFFF")),
            outline_width=int(config.get("outline_width", 4)),
        )
    return {
        "enabled": True,
        "burn_in": burn_in,
        "cues": len(cues),
        "srt_path": str(srt_path),
        "ass_path": str(ass_path) if ass_path is not None else None,
    }
