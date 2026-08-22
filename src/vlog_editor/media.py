from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
DJI_STAMP_RE = re.compile(r"DJI_(\d{8})(\d{6})")
# Composite-day bands for merged multi-day edits (local clock).
DAYPART_MORNING = 0
DAYPART_MIDDAY = 1
DAYPART_EVENING = 2
DAYPART_NIGHT = 3


def local_clock(capture_time: str | None, filename: str = "") -> tuple[int, int] | None:
    """Local hour/minute. DJI filenames are local; ISO capture_time is often UTC."""
    match = DJI_STAMP_RE.search(str(filename or ""))
    if match:
        stamp = match.group(2)
        return int(stamp[0:2]), int(stamp[2:4])
    text = str(capture_time or "").strip()
    if not text:
        return None
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.hour, parsed.minute


def daypart_rank(hour: int) -> int:
    if 5 <= hour < 11:
        return DAYPART_MORNING
    if 11 <= hour < 17:
        return DAYPART_MIDDAY
    if 17 <= hour < 21:
        return DAYPART_EVENING
    return DAYPART_NIGHT


def item_daypart(capture_time: str | None, filename: str = "") -> int:
    clock = local_clock(capture_time, filename)
    if clock is None:
        return DAYPART_MIDDAY
    return daypart_rank(clock[0])


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode:
        detail = (result.stderr or result.stdout)[-2000:]
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{detail}")
    return result


def list_videos(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )


def probe_video(path: Path) -> dict[str, Any]:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    format_info = payload.get("format", {}) or {}
    duration = float(format_info.get("duration") or video.get("duration") or 0)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    rotation = 0
    for side_data in video.get("side_data_list", []):
        if "rotation" in side_data:
            rotation = int(side_data["rotation"])
            break
    if abs(rotation) in (90, 270):
        width, height = height, width
    tags = format_info.get("tags") or {}
    capture_time = (
        tags.get("creation_time")
        or tags.get("com.apple.quicktime.creationdate")
        or tags.get("creation_date")
    )
    return {
        "filename": path.name,
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "orientation": "portrait" if height > width else "landscape",
        "fps": _parse_rate(str(video.get("avg_frame_rate") or "0/1")),
        "video_codec": video.get("codec_name"),
        "has_audio": audio is not None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "capture_time": str(capture_time) if capture_time else None,
    }


def _parse_rate(value: str) -> float:
    try:
        numerator, denominator = value.split("/", 1)
        return round(float(numerator) / float(denominator), 3) if float(denominator) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def sample_times(duration: float) -> list[float]:
    if duration <= 0:
        return [0.0]
    margin = min(0.5, duration * 0.05)
    if duration <= 20:
        values = [margin, duration / 2, max(margin, duration - margin)]
    elif duration <= 60:
        values = [margin + (duration - 2 * margin) * idx / 4 for idx in range(5)]
    else:
        # Keep long clips representative without creating an unbounded Ollama
        # request. Too many images can exceed the vision model's context.
        count = min(12, max(5, math.ceil(duration / 15)))
        values = [
            margin + (duration - 2 * margin) * idx / (count - 1)
            for idx in range(count)
        ]
    return sorted({round(max(0.0, min(duration, value)), 3) for value in values})


def extract_frames(path: Path, frame_dir: Path, duration: float, width: int = 1280) -> list[Path]:
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for index, timestamp in enumerate(sample_times(duration), start=1):
        output = frame_dir / f"frame_{index:03d}_{timestamp:.3f}.jpg"
        if not output.is_file():
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={width}:-2",
                    "-q:v",
                    "3",
                    str(output),
                ]
            )
        frames.append(output)
    return frames


def detect_volume(path: Path) -> dict[str, float | None]:
    result = run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "NUL"],
        check=False,
    )
    text = f"{result.stdout}\n{result.stderr}"

    def value(name: str) -> float | None:
        match = re.search(rf"{name}:\s*(-?(?:inf|\d+(?:\.\d+)?))\s*dB", text, re.IGNORECASE)
        if not match or match.group(1).lower() == "-inf":
            return None
        return float(match.group(1))

    return {"mean_db": value("mean_volume"), "max_db": value("max_volume")}


def thumbnail(path: Path, output: Path, timestamp: float) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.is_file():
        run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=480:-2",
                "-q:v",
                "4",
                str(output),
            ]
        )
    return output
